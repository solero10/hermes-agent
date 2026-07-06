"""Supervisor primitives for OpenBrain workflow step cards.

This module intentionally keeps the production supervisor small. Kanban workers
own the real task/run; fresh child sessions only write artifacts. The supervisor
watches those artifacts, heartbeats via callbacks, and returns a structured
result for the worker to complete or block with.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from hermes_constants import get_hermes_home

from hermes_cli.openbrain_workflow_artifacts import events_path, read_status, status_path, workflow_run_dir
from hermes_cli.openbrain_workflow_contracts import StepHandoff, build_heartbeat_note, redact_workflow_text


@dataclass(frozen=True)
class StepResult:
    status: str
    summary: str
    metadata: dict
    block_reason: str | None = None


@dataclass(frozen=True)
class StepAdapter:
    step_key: str
    command: list[str]
    prompt_path: str
    status_path: str
    events_path: str


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


def build_fresh_session_adapter(
    *,
    step_key: str,
    workflow_run_id: str,
    source_unit_id: str,
    source_folder: str,
    input_candidate_ids: Sequence[str] = (),
) -> StepAdapter:
    """Build a safe command/prompt adapter for fresh-session-capable steps.

    Dedupe/import steps intentionally return a gated capability result unless a
    real entrypoint is configured later; this prevents fake completion.
    """

    script = _skill_script_path(step_key)
    root = workflow_run_dir(workflow_run_id) / "steps" / step_key
    root.mkdir(parents=True, exist_ok=True)
    prompt_path = root / "prompt.md"
    status = status_path(workflow_run_id)
    events = events_path(workflow_run_id)
    prompt = (
        f"# OpenBrain workflow step\n\n"
        f"workflow_run_id: {workflow_run_id}\n"
        f"step_key: {step_key}\n"
        f"source_unit_id: {source_unit_id}\n"
        f"source_folder: {redact_workflow_text(Path(source_folder).name or source_folder, max_length=200)}\n"
        f"status_path: {status}\n"
        f"events_path: {events}\n"
        f"input_candidate_ids: {','.join(input_candidate_ids)}\n\n"
        "Write safe progress to status/events artifacts and finish by writing a receipt-pointer.json. "
        "Do not put raw transcript text or secrets in heartbeat/status summaries.\n"
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    if script is None:
        return StepAdapter(
            step_key=step_key,
            command=[],
            prompt_path=str(prompt_path),
            status_path=str(status),
            events_path=str(events),
        )
    command = [
        os.environ.get("PYTHON", "python"),
        str(script),
        "--prompt-file",
        str(prompt_path),
    ]
    return StepAdapter(step_key=step_key, command=command, prompt_path=str(prompt_path), status_path=str(status), events_path=str(events))


def parse_step_handoff(
    *,
    workflow_run_id: str,
    step_key: str,
    step_name: str,
    receipt_pointer_path: str | Path | None = None,
    child_output: str | None = None,
) -> StepHandoff:
    payload: dict = {}
    if receipt_pointer_path:
        path = Path(receipt_pointer_path)
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload.update(loaded)
            except json.JSONDecodeError:
                pass
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
        "status_artifacts": payload.get("status_artifacts"),
        "next_eligible_step": payload.get("next_eligible_step"),
    })


class WorkflowStepSupervisor:
    def __init__(
        self,
        *,
        workflow_run_id: str,
        step_key: str,
        task_id: str,
        run_id: int | None = None,
        child_command: Sequence[str] = (),
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 180,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 2,
        receipt_pointer_path: str | Path | None = None,
    ) -> None:
        self.workflow_run_id = workflow_run_id
        self.step_key = step_key
        self.task_id = task_id
        self.run_id = run_id
        self.child_command = list(child_command)
        self.heartbeat_callback = heartbeat_callback
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.receipt_pointer_path = Path(receipt_pointer_path) if receipt_pointer_path else workflow_run_dir(workflow_run_id) / "steps" / step_key / "receipt-pointer.json"

    def _heartbeat_from_status(self) -> str:
        status = read_status(self.workflow_run_id)
        if status is None:
            return build_heartbeat_note(
                workflow_run_id=self.workflow_run_id,
                step_key=self.step_key,
                source_unit_id="unknown",
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

    def run(self) -> StepResult:
        if not self.child_command:
            reason = f"capability: {self.step_key} entrypoint not configured"
            return StepResult("blocked", reason, {"workflow_run_id": self.workflow_run_id, "step_key": self.step_key}, reason)
        started = time.monotonic()
        last_heartbeat = 0.0
        process = subprocess.Popen(self.child_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout = ""
        stderr = ""
        try:
            while True:
                if self.timeout_seconds and (time.monotonic() - started) > self.timeout_seconds:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    reason = f"transient: {self.step_key} timed out after {int(self.timeout_seconds)}s"
                    return StepResult("blocked", reason, {"workflow_run_id": self.workflow_run_id, "step_key": self.step_key, "stderr": redact_workflow_text(stderr, max_length=500)}, reason)
                if self.heartbeat_callback and (time.monotonic() - last_heartbeat) >= self.heartbeat_interval_seconds:
                    self.heartbeat_callback(self._heartbeat_from_status())
                    last_heartbeat = time.monotonic()
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    break
                time.sleep(self.poll_interval_seconds)
        finally:
            if process.poll() is None:
                process.terminate()
        if process.returncode != 0:
            reason = f"transient: {self.step_key} child exited {process.returncode}"
            return StepResult("blocked", reason, {"workflow_run_id": self.workflow_run_id, "step_key": self.step_key, "stderr": redact_workflow_text(stderr, max_length=500)}, reason)
        if not self.receipt_pointer_path.exists():
            reason = f"capability: {self.step_key} missing receipt pointer"
            return StepResult("blocked", reason, {"workflow_run_id": self.workflow_run_id, "step_key": self.step_key}, reason)
        handoff = parse_step_handoff(
            workflow_run_id=self.workflow_run_id,
            step_key=self.step_key,
            step_name=self.step_key.replace("_", " ").title(),
            receipt_pointer_path=self.receipt_pointer_path,
            child_output=stdout.strip() or None,
        )
        return StepResult(
            "done",
            f"{handoff.step_name} completed with {handoff.candidate_count} candidate(s).",
            handoff.model_dump(mode="json", exclude_none=True),
        )
