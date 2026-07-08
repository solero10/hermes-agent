"""Supervisor primitives for OpenBrain workflow step cards.

Kanban remains the execution control plane. The dispatcher claims an OpenBrain
workflow step card, then this supervisor launches a child runner, owns safe
heartbeats/status/events, validates the receipt pointer, and returns a structured
complete/block decision. Tests inject fake runners; production execution remains
behind explicit execute modes.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from hermes_constants import get_hermes_home

from hermes_cli.openbrain_workflow_artifacts import (
    append_event,
    dashboard_summary_path,
    events_path,
    read_status,
    status_path,
    validate_workflow_run_id,
    workflow_run_dir,
    write_dashboard_summary,
    write_status,
)
from hermes_cli.openbrain_workflow_contracts import (
    ExecuteMode,
    StepHandoff,
    StepStatus,
    StatusArtifacts,
    WorkflowEvent,
    WorkflowStatus,
    build_heartbeat_note,
    redact_workflow_text,
    utc_now_iso,
)

OPENBRAIN_WORKFLOW_TEMPLATE_ID = "openbrain-source-v1"
OPENBRAIN_WORKFLOW_STEPS = {
    "panning_for_gold",
    "meeting_synthesis",
    "thought_enrichment",
    "dedupe",
    "human_review",
    "ready_cortexdb_import",
}
_EXECUTE_MODES = {"plan-only", "dry-run-execute", "production-execute", "production-import"}


@dataclass(frozen=True)
class StepResult:
    status: str
    summary: str
    metadata: dict[str, Any]
    block_reason: str | None = None
    block_kind: str = "capability"


@dataclass(frozen=True)
class StepAdapter:
    step_key: str
    command: list[str]
    prompt_path: str
    status_path: str
    events_path: str
    receipt_pointer_path: str
    workflow_run_id: str
    source_unit_id: str
    source_folder: str | None = None
    input_candidate_ids: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    child_session_path: str | None = None


@dataclass(frozen=True)
class ChildRunResult:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class ReceiptGateResult:
    ok: bool
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class ChildRunner(Protocol):
    def run(
        self,
        adapter: StepAdapter,
        *,
        timeout_seconds: float | None = None,
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 180,
        poll_interval_seconds: float = 2,
    ) -> ChildRunResult:
        ...


class SubprocessChildRunner:
    """Run a prepared child command while the supervisor emits heartbeats."""

    def _write_child_session(
        self,
        adapter: StepAdapter,
        payload: dict[str, Any],
    ) -> None:
        if not adapter.child_session_path:
            return
        path = Path(adapter.child_session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = dict(payload)
        safe_payload["command_preview"] = [
            redact_workflow_text(str(part), max_length=180) for part in adapter.command[:8]
        ]
        safe_payload["env_keys"] = sorted(adapter.env)
        path.write_text(json.dumps(safe_payload, indent=2, sort_keys=True), encoding="utf-8")

    def _write_logs(self, adapter: StepAdapter, stdout: str, stderr: str) -> None:
        if adapter.stdout_log_path:
            path = Path(adapter.stdout_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(redact_workflow_text(stdout, max_length=200_000), encoding="utf-8")
        if adapter.stderr_log_path:
            path = Path(adapter.stderr_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(redact_workflow_text(stderr, max_length=200_000), encoding="utf-8")

    def run(
        self,
        adapter: StepAdapter,
        *,
        timeout_seconds: float | None = None,
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 180,
        poll_interval_seconds: float = 2,
    ) -> ChildRunResult:
        if not adapter.command:
            return ChildRunResult(exit_code=127, stderr="child entrypoint not configured")
        started = time.monotonic()
        last_heartbeat = 0.0
        env = os.environ.copy()
        env.update(adapter.env)
        self._write_child_session(adapter, {
            "workflow_run_id": adapter.workflow_run_id,
            "step_key": adapter.step_key,
            "status": "started",
            "started_at_monotonic": started,
            "cwd": redact_workflow_text(adapter.cwd or "", max_length=500) or None,
        })
        process = subprocess.Popen(  # noqa: S603 -- argv is built by trusted adapter code.
            adapter.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=adapter.cwd or None,
        )
        termination: str | None = None
        try:
            while True:
                now = time.monotonic()
                if timeout_seconds and (now - started) > timeout_seconds:
                    termination = "SIGTERM"
                    process.terminate()
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        termination = "SIGKILL"
                        process.kill()
                        stdout, stderr = process.communicate(timeout=5)
                    self._write_logs(adapter, stdout, stderr)
                    self._write_child_session(adapter, {
                        "workflow_run_id": adapter.workflow_run_id,
                        "step_key": adapter.step_key,
                        "status": "timed_out",
                        "exit_code": process.returncode,
                        "timed_out": True,
                        "termination": termination,
                        "duration_seconds": round(time.monotonic() - started, 3),
                    })
                    return ChildRunResult(exit_code=process.returncode, stdout=stdout, stderr=stderr, timed_out=True)
                if heartbeat_callback and (now - last_heartbeat) >= heartbeat_interval_seconds:
                    heartbeat_callback("")
                    last_heartbeat = now
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    self._write_logs(adapter, stdout, stderr)
                    self._write_child_session(adapter, {
                        "workflow_run_id": adapter.workflow_run_id,
                        "step_key": adapter.step_key,
                        "status": "finished",
                        "exit_code": process.returncode,
                        "timed_out": False,
                        "termination": None,
                        "duration_seconds": round(time.monotonic() - started, 3),
                    })
                    return ChildRunResult(exit_code=process.returncode, stdout=stdout, stderr=stderr, timed_out=False)
                time.sleep(max(0.01, poll_interval_seconds))
        finally:
            if process.poll() is None:
                process.terminate()


class SyntheticDryRunChildRunner:
    """Deterministic safe runner used by dry-run execution and tests."""

    def run(
        self,
        adapter: StepAdapter,
        *,
        timeout_seconds: float | None = None,
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 180,
        poll_interval_seconds: float = 2,
    ) -> ChildRunResult:
        if heartbeat_callback:
            heartbeat_callback("")
        receipt_pointer = Path(adapter.receipt_pointer_path)
        receipt_pointer.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = receipt_pointer.parent / "synthetic-receipt.json"
        if not receipt_path.exists():
            receipt_path.write_text(json.dumps({"synthetic": True}, sort_keys=True), encoding="utf-8")
        candidate_ids = list(adapter.input_candidate_ids) or [f"{adapter.step_key}-candidate-001"]
        payload: dict[str, Any] = {
            "workflow_run_id": adapter.workflow_run_id,
            "step_key": adapter.step_key,
            "source_unit_id": adapter.source_unit_id,
            "candidate_ids": candidate_ids,
            "candidate_count": len(candidate_ids),
            "receipt_path": str(receipt_path),
            "dashboard_verification": {"ok": True, "visible_candidate_count": len(candidate_ids)},
        }
        if adapter.input_candidate_ids:
            payload["input_candidate_ids"] = list(adapter.input_candidate_ids)
        if adapter.step_key == "dedupe":
            payload["dedupe_mode"] = "exact-only"
            payload["semantic_dedupe_claimed"] = False
        receipt_pointer.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return ChildRunResult(exit_code=0, stdout=json.dumps({}), stderr="", timed_out=False)


STEP_SKILL_SCRIPT_DIRS = {
    "panning_for_gold": "panning-for-gold-dashboard-fresh",
    "meeting_synthesis": "meeting-synthesis-dashboard-fresh",
    "thought_enrichment": "thought-enrichment-dashboard-fresh",
}


def _skill_script_path(step_key: str) -> Path | None:
    skill_name = STEP_SKILL_SCRIPT_DIRS.get(step_key)
    if not skill_name:
        return None
    path = get_hermes_home() / "skills" / "productivity" / skill_name / "scripts" / "run_fresh_session.py"
    return path if path.exists() else None


def _safe_run_child(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def _coerce_execute_mode(value: str | None) -> ExecuteMode:
    mode = str(value or "plan-only").strip() or "plan-only"
    if mode not in _EXECUTE_MODES:
        return "plan-only"  # fail closed
    return mode  # type: ignore[return-value]


def extract_workflow_field(body: str | None, key: str) -> str | None:
    prefix = f"{key}:"
    for line in str(body or "").splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip() or None
    return None


def execute_mode_for_task(task: Any) -> ExecuteMode:
    body_mode = extract_workflow_field(getattr(task, "body", None), "execute_mode")
    if body_mode:
        return _coerce_execute_mode(body_mode)
    run_id = extract_workflow_field(getattr(task, "body", None), "workflow_run_id")
    if run_id:
        status = read_status(run_id)
        if status:
            return _coerce_execute_mode(status.execute_mode)
    return "plan-only"


def is_openbrain_workflow_task(task: Any) -> bool:
    return (
        getattr(task, "workflow_template_id", None) == OPENBRAIN_WORKFLOW_TEMPLATE_ID
        and getattr(task, "current_step_key", None) in OPENBRAIN_WORKFLOW_STEPS
    )


def runner_for_execute_mode(execute_mode: ExecuteMode) -> ChildRunner | None:
    if execute_mode == "dry-run-execute":
        return SyntheticDryRunChildRunner()
    if execute_mode in {"production-execute", "production-import"}:
        return SubprocessChildRunner()
    return None


def build_fresh_session_adapter(
    *,
    step_key: str,
    workflow_run_id: str,
    source_unit_id: str,
    source_folder: str,
    input_candidate_ids: Sequence[str] = (),
    assignee: str | None = None,
    workspace: str | None = None,
    execute_mode: ExecuteMode = "production-execute",
) -> StepAdapter:
    """Build a safe command/prompt adapter for fresh-session-capable steps.

    Dedupe/import steps intentionally have no command until a real entrypoint is
    configured. Dry-run execution injects ``SyntheticDryRunChildRunner`` and does
    not need the command.
    """

    workflow_run_id = validate_workflow_run_id(workflow_run_id)
    skill_name = STEP_SKILL_SCRIPT_DIRS.get(step_key)
    root = workflow_run_dir(workflow_run_id) / "steps" / step_key
    root.mkdir(parents=True, exist_ok=True)
    prompt_path = root / "prompt.md"
    receipt_pointer_path = root / "receipt-pointer.json"
    status = status_path(workflow_run_id)
    events = events_path(workflow_run_id)
    stdout_log_path = root / "child-stdout.log"
    stderr_log_path = root / "child-stderr.log"
    child_session_path = root / "child-session.json"
    safe_input_ids = [redact_workflow_text(c, max_length=120) for c in input_candidate_ids]
    prompt = (
        "# OpenBrain workflow step\n\n"
        f"workflow_run_id: {workflow_run_id}\n"
        f"step_key: {step_key}\n"
        f"source_unit_id: {redact_workflow_text(source_unit_id, max_length=120)}\n"
        f"source_folder: {redact_workflow_text(Path(source_folder).name or source_folder, max_length=200)}\n"
        f"status_path: {status}\n"
        f"events_path: {events}\n"
        f"receipt_pointer_path: {receipt_pointer_path}\n"
        f"input_candidate_ids: {','.join(safe_input_ids)}\n\n"
        "Write safe progress to status/events artifacts and finish by writing a receipt-pointer.json. "
        "Do not put raw transcript text or secrets in heartbeat/status summaries.\n"
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    env = {
        "HERMES_HOME": str(get_hermes_home()),
        "HERMES_OPENBRAIN_WORKFLOW_RUN_ID": workflow_run_id,
        "HERMES_OPENBRAIN_STEP_KEY": step_key,
        "HERMES_OPENBRAIN_STATUS_PATH": str(status),
        "HERMES_OPENBRAIN_EVENTS_PATH": str(events),
        "HERMES_OPENBRAIN_RECEIPT_POINTER": str(receipt_pointer_path),
        "HERMES_OPENBRAIN_EXECUTE_MODE": execute_mode,
    }
    if source_folder:
        env["HERMES_STRICT_SOURCE_FOLDER"] = source_folder
    command: list[str] = []
    if skill_name is not None:
        command = [os.environ.get("HERMES_BIN", "hermes")]
        if assignee:
            command.extend(["--profile", assignee])
        command.extend([
            "--skills",
            skill_name,
            "chat",
            "--source",
            f"openbrain-workflow-{step_key}",
            "--toolsets",
            "file,terminal,skills,browser",
            "-Q",
            "-q",
            prompt,
        ])
    return StepAdapter(
        step_key=step_key,
        command=command,
        prompt_path=str(prompt_path),
        status_path=str(status),
        events_path=str(events),
        receipt_pointer_path=str(receipt_pointer_path),
        workflow_run_id=workflow_run_id,
        source_unit_id=source_unit_id,
        source_folder=source_folder,
        input_candidate_ids=list(input_candidate_ids),
        env=env,
        cwd=workspace,
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
        child_session_path=str(child_session_path),
    )


def validate_receipt_pointer(
    workflow_run_id: str,
    step_key: str,
    receipt_pointer_path: str | Path | None = None,
    *,
    allow_external_receipts: bool = False,
) -> ReceiptGateResult:
    workflow_run_id = validate_workflow_run_id(workflow_run_id)
    run_root = workflow_run_dir(workflow_run_id)
    pointer = Path(receipt_pointer_path) if receipt_pointer_path else run_root / "steps" / step_key / "receipt-pointer.json"
    try:
        if not _safe_run_child(pointer, run_root):
            return ReceiptGateResult(False, "unsafe receipt pointer path outside workflow run", {})
    except Exception:
        return ReceiptGateResult(False, "unsafe receipt pointer path outside workflow run", {})
    if not pointer.exists():
        return ReceiptGateResult(False, f"missing receipt pointer for {step_key}", {})
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ReceiptGateResult(False, f"malformed receipt pointer for {step_key}", {})
    except OSError as exc:
        return ReceiptGateResult(False, f"unreadable receipt pointer for {step_key}: {redact_workflow_text(exc)}", {})
    if not isinstance(payload, dict):
        return ReceiptGateResult(False, f"malformed receipt pointer for {step_key}: expected object", {})
    if payload.get("workflow_run_id") and payload.get("workflow_run_id") != workflow_run_id:
        return ReceiptGateResult(False, "receipt workflow_run_id mismatch", payload)
    if payload.get("step_key") and payload.get("step_key") != step_key:
        return ReceiptGateResult(False, "receipt step_key mismatch", payload)
    receipt_path_value = payload.get("receipt_path")
    if receipt_path_value:
        receipt_path_raw = Path(str(receipt_path_value))
        if ".." in receipt_path_raw.parts:
            return ReceiptGateResult(False, "receipt target outside workflow run", payload)
        receipt_path = receipt_path_raw
        if not receipt_path.is_absolute():
            receipt_path = pointer.parent / receipt_path
        if not allow_external_receipts and not _safe_run_child(receipt_path, run_root):
            return ReceiptGateResult(False, "receipt target outside workflow run", payload)
        if not receipt_path.exists():
            return ReceiptGateResult(False, "receipt target missing", payload)
        payload["receipt_path"] = str(receipt_path)
    if not payload.get("source_unit_id"):
        return ReceiptGateResult(False, "receipt pointer missing source_unit_id", payload)
    return ReceiptGateResult(True, None, payload)


def review_approval_path(workflow_run_id: str) -> Path:
    return workflow_run_dir(workflow_run_id) / "review-approval.json"


def review_approved(workflow_run_id: str) -> bool:
    path = review_approval_path(workflow_run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("approved") is True


def evaluate_step_gate(
    *,
    workflow_run_id: str,
    step_key: str,
    receipt: ReceiptGateResult,
    review_approved: bool | None = None,
    import_receipt_ok: bool = False,
    expected_candidate_ids: Sequence[str] = (),
) -> ReceiptGateResult:
    """Apply completion policy for one OpenBrain workflow step."""

    if review_approved is None:
        review_approved = globals()["review_approved"](workflow_run_id)
    if step_key == "human_review":
        if not review_approved:
            return ReceiptGateResult(False, "review-required: approve before CortexDB import", receipt.payload)
        return ReceiptGateResult(True, None, receipt.payload)
    if not receipt.ok:
        return receipt
    payload = receipt.payload
    expected_ids = [str(candidate_id) for candidate_id in expected_candidate_ids]
    if expected_ids and step_key in {"thought_enrichment", "dedupe", "ready_cortexdb_import"}:
        receipt_inputs = payload.get("input_candidate_ids")
        if isinstance(receipt_inputs, str):
            receipt_inputs = [receipt_inputs]
        if not isinstance(receipt_inputs, list):
            return ReceiptGateResult(False, f"{step_key} receipt missing input candidate ids", payload)
        received_ids = [str(candidate_id) for candidate_id in receipt_inputs]
        if received_ids != expected_ids:
            return ReceiptGateResult(False, f"{step_key} receipt input candidate mismatch", payload)
    if step_key in {"panning_for_gold", "meeting_synthesis", "thought_enrichment"} and not payload.get("candidate_ids"):
        return ReceiptGateResult(False, f"{step_key} receipt missing candidate ids", payload)
    if step_key == "dedupe":
        if payload.get("dedupe_mode") == "exact-only" and payload.get("semantic_dedupe_claimed"):
            return ReceiptGateResult(False, "semantic dedupe cannot be claimed in exact-only mode", payload)
    if step_key == "ready_cortexdb_import":
        if not review_approved:
            return ReceiptGateResult(False, "ready/import requires review approval", payload)
        if not import_receipt_ok and not payload.get("import_receipt_path"):
            return ReceiptGateResult(False, "ready/import requires import receipt", payload)
        dashboard = payload.get("dashboard_verification") if isinstance(payload.get("dashboard_verification"), dict) else {}
        if dashboard.get("ok") is not True:
            return ReceiptGateResult(False, "ready/import requires dashboard verification", payload)
    return ReceiptGateResult(True, None, payload)


def _status_artifacts(workflow_run_id: str) -> StatusArtifacts:
    return StatusArtifacts(
        status_json=str(status_path(workflow_run_id)),
        events_jsonl=str(events_path(workflow_run_id)),
        dashboard_summary=str(dashboard_summary_path(workflow_run_id)),
    )


def parse_step_handoff(
    *,
    workflow_run_id: str,
    step_key: str,
    step_name: str,
    receipt_pointer_path: str | Path | None = None,
    child_output: str | None = None,
) -> StepHandoff:
    payload: dict[str, Any] = {}
    receipt = validate_receipt_pointer(workflow_run_id, step_key, receipt_pointer_path)
    if receipt.ok:
        payload.update(receipt.payload)
    if child_output:
        try:
            loaded = json.loads(child_output)
            if isinstance(loaded, dict):
                payload.update(loaded)
        except json.JSONDecodeError:
            pass
    candidate_ids = payload.get("candidate_ids") or payload.get("created_candidate_ids") or []
    if isinstance(candidate_ids, str):
        candidate_ids = [candidate_ids]
    source_unit_id = str(payload.get("source_unit_id") or "")
    if not source_unit_id:
        status = read_status(workflow_run_id)
        source_unit_id = status.source_unit_id if status else "unknown"
    receipt_path = payload.get("receipt_path") or (str(receipt_pointer_path) if receipt_pointer_path else None)
    return StepHandoff.model_validate({
        "workflow_run_id": workflow_run_id,
        "source_unit_id": source_unit_id,
        "source_folder": payload.get("source_folder"),
        "step_key": step_key,
        "step_name": step_name,
        "candidate_count": int(payload.get("candidate_count") or len(candidate_ids) or 0),
        "candidate_ids": [str(c) for c in candidate_ids],
        "receipt_path": receipt_path,
        "dashboard_verification": payload.get("dashboard_verification") or {},
        "status_artifacts": payload.get("status_artifacts") or _status_artifacts(workflow_run_id).model_dump(mode="json"),
        "next_eligible_step": payload.get("next_eligible_step"),
    })


def _ensure_status(
    *,
    workflow_run_id: str,
    source_unit_id: str,
    source_folder: str | None,
    execute_mode: ExecuteMode,
) -> WorkflowStatus:
    existing = read_status(workflow_run_id)
    if existing:
        return existing
    now = utc_now_iso()
    return WorkflowStatus(
        workflow_run_id=workflow_run_id,
        source_unit_id=source_unit_id,
        source_folder=source_folder,
        execute_mode=execute_mode,
        status="running",
        started_at=now,
        updated_at=now,
        last_heartbeat_at=now,
    )


def _write_step_state(
    *,
    workflow_run_id: str,
    step_key: str,
    task_id: str,
    source_unit_id: str,
    source_folder: str | None,
    execute_mode: ExecuteMode,
    step_status: str,
    candidate_ids: Sequence[str] = (),
    receipt_path_value: str | None = None,
    blocked_or_error: str | None = None,
) -> None:
    now = utc_now_iso()
    status = _ensure_status(
        workflow_run_id=workflow_run_id,
        source_unit_id=source_unit_id,
        source_folder=source_folder,
        execute_mode=execute_mode,
    )
    steps = dict(status.steps)
    prev = steps.get(step_key, StepStatus(task_id=task_id))
    steps[step_key] = prev.model_copy(update={
        "task_id": task_id,
        "status": step_status,
        "candidate_ids": list(candidate_ids) or prev.candidate_ids,
        "receipt_path": receipt_path_value or prev.receipt_path,
        "completed_at": now if step_status == "done" else prev.completed_at,
        "blocked_or_error": blocked_or_error,
    })
    workflow_state = "blocked" if step_status == "blocked" else "running"
    updated = status.model_copy(update={
        "execute_mode": execute_mode,
        "active_step": step_key,
        "active_task_id": task_id,
        "status": workflow_state,
        "updated_at": now,
        "last_heartbeat_at": now if step_status == "running" else status.last_heartbeat_at,
        "receipt_path": receipt_path_value or status.receipt_path,
        "blocked_or_error": blocked_or_error if step_status == "blocked" else None,
        "steps": steps,
    })
    write_status(updated)
    write_dashboard_summary(updated)


class WorkflowStepSupervisor:
    def __init__(
        self,
        *,
        workflow_run_id: str,
        step_key: str,
        task_id: str,
        run_id: int | None = None,
        child_command: Sequence[str] = (),
        runner: ChildRunner | None = None,
        adapter: StepAdapter | None = None,
        execute_mode: ExecuteMode = "production-execute",
        source_unit_id: str = "unknown",
        source_folder: str | None = None,
        input_candidate_ids: Sequence[str] = (),
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 180,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 2,
        receipt_pointer_path: str | Path | None = None,
    ) -> None:
        self.workflow_run_id = validate_workflow_run_id(workflow_run_id)
        self.step_key = step_key
        self.task_id = task_id
        self.run_id = run_id
        self.execute_mode = execute_mode
        self.source_unit_id = source_unit_id
        self.source_folder = source_folder
        if adapter is None:
            adapter = build_fresh_session_adapter(
                step_key=step_key,
                workflow_run_id=workflow_run_id,
                source_unit_id=source_unit_id,
                source_folder=source_folder or "",
                input_candidate_ids=input_candidate_ids,
                execute_mode=execute_mode,
            )
            if child_command:
                adapter = StepAdapter(**{**adapter.__dict__, "command": list(child_command)})
        elif child_command:
            adapter = StepAdapter(**{**adapter.__dict__, "command": list(child_command)})
        if receipt_pointer_path:
            adapter = StepAdapter(**{**adapter.__dict__, "receipt_pointer_path": str(receipt_pointer_path)})
        self.adapter = adapter
        if runner is not None:
            self.runner = runner
        elif child_command:
            self.runner = SubprocessChildRunner()
        else:
            self.runner = runner_for_execute_mode(execute_mode)
        self.heartbeat_callback = heartbeat_callback
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def _heartbeat_from_status(self) -> str:
        status = read_status(self.workflow_run_id)
        if status is None:
            return build_heartbeat_note(
                workflow_run_id=self.workflow_run_id,
                step_key=self.step_key,
                source_unit_id=self.source_unit_id,
                phase="waiting_for_status",
            )
        return build_heartbeat_note(
            workflow_run_id=status.workflow_run_id,
            step_key=status.active_step or self.step_key,
            source_unit_id=status.source_unit_id,
            elapsed_seconds=status.elapsed_seconds,
            phase=status.current_phase,
            completed_candidates=status.completed_candidates,
            total_candidates=status.total_candidates,
            current_candidate_id=status.current_candidate_id,
            current_candidate_title=status.current_candidate_title_sanitized,
        )

    def _heartbeat(self, note: str | None = None) -> None:
        safe_note = note or self._heartbeat_from_status()
        safe_note = redact_workflow_text(safe_note, max_length=500)
        if self.heartbeat_callback:
            self.heartbeat_callback(safe_note)
        append_event(WorkflowEvent(
            workflow_run_id=self.workflow_run_id,
            source_unit_id=self.source_unit_id,
            step_key=self.step_key,
            task_id=self.task_id,
            event_type="heartbeat_sent",
            created_at=utc_now_iso(),
            message=safe_note,
        ))

    def _block(self, reason: str, *, kind: str = "capability", stderr: str | None = None) -> StepResult:
        safe_reason = redact_workflow_text(reason, max_length=500)
        metadata: dict[str, Any] = {"workflow_run_id": self.workflow_run_id, "step_key": self.step_key}
        if stderr:
            metadata["stderr"] = redact_workflow_text(stderr, max_length=500)
        _write_step_state(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            task_id=self.task_id,
            source_unit_id=self.source_unit_id,
            source_folder=self.source_folder,
            execute_mode=self.execute_mode,
            step_status="blocked",
            blocked_or_error=safe_reason,
        )
        append_event(WorkflowEvent(
            workflow_run_id=self.workflow_run_id,
            source_unit_id=self.source_unit_id,
            step_key=self.step_key,
            task_id=self.task_id,
            event_type="task_blocked",
            created_at=utc_now_iso(),
            message=safe_reason,
        ))
        return StepResult("blocked", safe_reason, metadata, safe_reason, kind)

    def run(self) -> StepResult:
        _write_step_state(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            task_id=self.task_id,
            source_unit_id=self.source_unit_id,
            source_folder=self.source_folder,
            execute_mode=self.execute_mode,
            step_status="running",
        )
        append_event(WorkflowEvent(
            workflow_run_id=self.workflow_run_id,
            source_unit_id=self.source_unit_id,
            step_key=self.step_key,
            task_id=self.task_id,
            event_type="task_started",
            created_at=utc_now_iso(),
            message=f"Started {self.step_key} supervisor.",
        ))
        if self.step_key == "human_review":
            gate = evaluate_step_gate(
                workflow_run_id=self.workflow_run_id,
                step_key=self.step_key,
                receipt=ReceiptGateResult(True, None, {"source_unit_id": self.source_unit_id}),
            )
            if not gate.ok:
                return self._block(gate.reason or "review-required", kind="needs_input")
        if self.step_key == "ready_cortexdb_import" and self.execute_mode != "production-import":
            return self._block("ready/import requires production-import mode and review approval", kind="needs_input")
        if self.runner is None:
            return self._block(f"plan-only: {self.step_key} execution is disabled", kind="capability")
        if isinstance(self.runner, SubprocessChildRunner) and not self.adapter.command:
            return self._block(f"capability: {self.step_key} entrypoint not configured", kind="capability")
        child = self.runner.run(
            self.adapter,
            timeout_seconds=self.timeout_seconds,
            heartbeat_callback=self._heartbeat,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        )
        if child.timed_out:
            return self._block(
                f"transient: {self.step_key} timed out after {int(self.timeout_seconds or 0)}s",
                kind="transient",
                stderr=child.stderr,
            )
        if child.exit_code != 0:
            return self._block(
                f"transient: {self.step_key} child exited {child.exit_code}",
                kind="transient",
                stderr=child.stderr,
            )
        receipt = validate_receipt_pointer(self.workflow_run_id, self.step_key, self.adapter.receipt_pointer_path)
        gate = evaluate_step_gate(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            receipt=receipt,
            expected_candidate_ids=self.adapter.input_candidate_ids,
        )
        if not gate.ok:
            return self._block(gate.reason or "receipt gate failed", kind="capability")
        handoff = parse_step_handoff(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            step_name=self.step_key.replace("_", " ").title(),
            receipt_pointer_path=self.adapter.receipt_pointer_path,
            child_output=child.stdout.strip() or None,
        )
        _write_step_state(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            task_id=self.task_id,
            source_unit_id=handoff.source_unit_id,
            source_folder=self.source_folder,
            execute_mode=self.execute_mode,
            step_status="done",
            candidate_ids=handoff.candidate_ids,
            receipt_path_value=handoff.receipt_path,
        )
        append_event(WorkflowEvent(
            workflow_run_id=self.workflow_run_id,
            source_unit_id=handoff.source_unit_id,
            step_key=self.step_key,
            task_id=self.task_id,
            event_type="task_completed",
            created_at=utc_now_iso(),
            candidate_id=handoff.candidate_ids[0] if handoff.candidate_ids else None,
            completed_candidates=handoff.candidate_count,
            total_candidates=handoff.candidate_count,
            message=f"{handoff.step_name} completed with {handoff.candidate_count} candidate(s).",
        ))
        return StepResult(
            "done",
            f"{handoff.step_name} completed with {handoff.candidate_count} candidate(s).",
            handoff.model_dump(mode="json", exclude_none=True),
        )


def run_openbrain_step_task(
    conn: Any,
    task: Any,
    *,
    board: str | None = None,
    runner_factory: Callable[[Any, ExecuteMode], ChildRunner] | None = None,
    execute_mode: ExecuteMode | None = None,
) -> StepResult:
    """Execute one claimed OpenBrain task and write the Kanban outcome."""

    from hermes_cli import kanban_db as kb  # local import avoids module cycle

    workflow_run_id = extract_workflow_field(getattr(task, "body", None), "workflow_run_id") or ""
    source_unit_id = extract_workflow_field(getattr(task, "body", None), "source_unit_id") or "unknown"
    source_folder = extract_workflow_field(getattr(task, "body", None), "source_folder_path") or extract_workflow_field(getattr(task, "body", None), "source_folder")
    step_key = getattr(task, "current_step_key", None) or extract_workflow_field(getattr(task, "body", None), "step_key") or ""
    if not workflow_run_id or not step_key:
        result = StepResult(
            "blocked",
            "capability: OpenBrain workflow task is missing workflow_run_id or step_key",
            {"workflow_run_id": workflow_run_id, "step_key": step_key},
            "capability: OpenBrain workflow task is missing workflow_run_id or step_key",
            "capability",
        )
    else:
        mode = execute_mode or execute_mode_for_task(task)
        adapter = build_fresh_session_adapter(
            step_key=step_key,
            workflow_run_id=workflow_run_id,
            source_unit_id=source_unit_id,
            source_folder=source_folder or "",
            assignee=getattr(task, "assignee", None),
            workspace=getattr(task, "workspace_path", None),
            execute_mode=mode,
        )
        runner = runner_factory(task, mode) if runner_factory else runner_for_execute_mode(mode)

        def _kanban_heartbeat(note: str) -> None:
            kb.heartbeat_worker(conn, task.id, note=note, expected_run_id=getattr(task, "current_run_id", None))

        result = WorkflowStepSupervisor(
            workflow_run_id=workflow_run_id,
            step_key=step_key,
            task_id=task.id,
            run_id=getattr(task, "current_run_id", None),
            adapter=adapter,
            runner=runner,
            execute_mode=mode,
            source_unit_id=source_unit_id,
            source_folder=source_folder,
            heartbeat_callback=_kanban_heartbeat,
            heartbeat_interval_seconds=0,
            timeout_seconds=getattr(task, "max_runtime_seconds", None),
            poll_interval_seconds=0.05,
        ).run()
    if result.status == "done":
        kb.complete_task(
            conn,
            task.id,
            summary=result.summary,
            metadata=result.metadata,
            expected_run_id=getattr(task, "current_run_id", None),
        )
    else:
        kb.block_task(
            conn,
            task.id,
            reason=result.block_reason or result.summary,
            kind=result.block_kind if result.block_kind in {"dependency", "needs_input", "capability", "transient"} else "capability",
            expected_run_id=getattr(task, "current_run_id", None),
        )
    return result


def build_supervisor_process_command(*, task_id: str, board: str | None = None, execute_mode: ExecuteMode | None = None) -> list[str]:
    cmd = [os.environ.get("PYTHON", "python"), "-m", "hermes_cli.main", "openbrain-workflow", "run-step", "--task-id", task_id]
    if board:
        cmd.extend(["--board", board])
    if execute_mode:
        cmd.extend(["--execute-mode", execute_mode])
    return cmd
