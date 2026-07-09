"""Crash-safe artifact helpers for OpenBrain workflow runs."""
from __future__ import annotations

import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

from hermes_cli.openbrain_workflow_contracts import (
    WorkflowDashboardResponse,
    WorkflowDashboardRun,
    WorkflowEvent,
    WorkflowStatus,
    redact_workflow_text,
    utc_now_iso,
)

ROOT_DIRNAME = "openbrain-workflow-runs"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def validate_workflow_run_id(workflow_run_id: str) -> str:
    run_id = str(workflow_run_id or "").strip()
    if not run_id or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError("invalid workflow_run_id")
    return run_id


def workflow_runs_root() -> Path:
    return get_hermes_home() / ROOT_DIRNAME


def workflow_run_dir(workflow_run_id: str) -> Path:
    return workflow_runs_root() / validate_workflow_run_id(workflow_run_id)


def status_path(workflow_run_id: str) -> Path:
    return workflow_run_dir(workflow_run_id) / "status.json"


def events_path(workflow_run_id: str) -> Path:
    return workflow_run_dir(workflow_run_id) / "events.jsonl"


def dashboard_summary_path(workflow_run_id: str) -> Path:
    return workflow_run_dir(workflow_run_id) / "dashboard-summary.json"


def manifest_path(workflow_run_id: str) -> Path:
    return workflow_run_dir(workflow_run_id) / "manifest.json"


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(path.parent), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_parent(path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def write_status(status: WorkflowStatus) -> Path:
    path = status_path(status.workflow_run_id)
    atomic_write_json(path, status.model_dump(mode="json", exclude_none=True))
    return path


def read_status(workflow_run_id: str) -> WorkflowStatus | None:
    path = status_path(workflow_run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return WorkflowStatus.model_validate(payload)
    except Exception:
        return None


def append_event(event: WorkflowEvent) -> Path:
    root = workflow_run_dir(event.workflow_run_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "events.jsonl"
    next_id = 1
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                next_id = sum(1 for line in handle if line.strip()) + 1
        except OSError:
            next_id = 1
    payload = event.model_copy(update={"event_id": event.event_id or next_id})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload.model_dump(mode="json", exclude_none=True), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def read_events(
    workflow_run_id: str,
    *,
    after: int = 0,
    limit: int = 200,
    newest: bool = False,
) -> list[dict[str, Any]]:
    path = events_path(workflow_run_id)
    if limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    recent: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return []
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                event = WorkflowEvent.model_validate(payload)
            except Exception:
                continue
            if event.event_id and event.event_id > after:
                event_payload = event.model_dump(mode="json", exclude_none=True)
                if newest:
                    recent.append(event_payload)
                else:
                    out.append(event_payload)
                    if len(out) >= limit:
                        break
    if newest:
        return list(recent)
    return out


def write_dashboard_summary(status: WorkflowStatus) -> Path:
    summary = summarize_status_for_dashboard(status)
    path = dashboard_summary_path(status.workflow_run_id)
    atomic_write_json(path, summary)
    return path


def read_dashboard_summary(workflow_run_id: str) -> dict[str, Any] | None:
    path = dashboard_summary_path(workflow_run_id)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return payload
    status = read_status(workflow_run_id)
    return summarize_status_for_dashboard(status) if status else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def summarize_staleness(
    status: WorkflowStatus,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 600,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if status.status not in {"running", "planned"}:
        return {"stale": False, "age_seconds": 0, "reason": None}
    heartbeat = _parse_iso(status.last_heartbeat_at) or _parse_iso(status.updated_at) or _parse_iso(status.started_at)
    if heartbeat is None:
        return {"stale": True, "age_seconds": None, "reason": "missing heartbeat"}
    age = max(0, int((now - heartbeat).total_seconds()))
    return {
        "stale": age > stale_after_seconds,
        "age_seconds": age,
        "reason": "heartbeat stale" if age > stale_after_seconds else None,
    }


def summarize_status_for_dashboard(status: WorkflowStatus, *, now: datetime | None = None) -> dict[str, Any]:
    stale_info = summarize_staleness(status, now=now)
    active_step_state = None
    if status.active_step and status.active_step in status.steps:
        active_step_state = status.steps[status.active_step].status
    latest_receipt_path = status.receipt_path
    if not latest_receipt_path and status.active_step and status.active_step in status.steps:
        latest_receipt_path = status.steps[status.active_step].receipt_path
    run = WorkflowDashboardRun(
        workflow_run_id=status.workflow_run_id,
        source_unit_id=status.source_unit_id,
        source_title=status.source_title,
        status="stale" if stale_info.get("stale") and status.status == "running" else status.status,
        active_step=status.active_step,
        active_task_id=status.active_task_id,
        kanban_task_state=active_step_state,
        elapsed_seconds=status.elapsed_seconds,
        last_heartbeat_at=status.last_heartbeat_at,
        stale=bool(stale_info.get("stale")),
        current_phase=status.current_phase,
        current_candidate_id=status.current_candidate_id,
        current_candidate_title_sanitized=status.current_candidate_title_sanitized,
        completed_candidates=status.completed_candidates,
        total_candidates=status.total_candidates,
        latest_receipt_path=latest_receipt_path,
        blocked_or_error=redact_workflow_text(status.blocked_or_error, max_length=500) if status.blocked_or_error else None,
        updated_at=status.updated_at,
    )
    return run.model_dump(mode="json", exclude_none=True)


def _status_files() -> list[Path]:
    root = workflow_runs_root()
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*/status.json") if p.is_file())


def list_statuses() -> list[WorkflowStatus]:
    statuses: list[WorkflowStatus] = []
    for path in _status_files():
        try:
            run_id = path.parent.name
            status = read_status(run_id)
        except ValueError:
            status = None
        if status:
            statuses.append(status)
    statuses.sort(key=lambda s: s.updated_at or s.started_at, reverse=True)
    return statuses


def workflow_revision() -> str:
    root = workflow_runs_root()
    max_mtime = 0
    total_size = 0
    count = 0
    if root.exists():
        for path in root.glob("*/*"):
            if path.name not in {"status.json", "events.jsonl", "dashboard-summary.json"} or not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            max_mtime = max(max_mtime, int(st.st_mtime_ns))
            total_size += int(st.st_size)
            count += 1
    return f"workflow-runs:{max_mtime}:{total_size}:{count}"


def dashboard_response(*, since_revision: str | None = None) -> dict[str, Any]:
    revision = workflow_revision()
    changed = since_revision != revision
    statuses = [] if not changed and since_revision else list_statuses()
    runs = [summarize_status_for_dashboard(status) for status in statuses]
    events: list[dict[str, Any]] = []
    if changed or not since_revision:
        for status in statuses:
            events.extend(read_events(status.workflow_run_id, after=0, limit=50, newest=True))
        events.sort(key=lambda event: (event.get("created_at") or "", int(event.get("event_id") or 0)), reverse=True)
        events = events[:200]
    return WorkflowDashboardResponse(
        generated_at=utc_now_iso(),
        revision=revision,
        changed=changed,
        runs=runs,
        events=events,
    ).model_dump(mode="json", exclude_none=True)


def workflow_detail(workflow_run_id: str) -> dict[str, Any]:
    status = read_status(workflow_run_id)
    if status is None:
        raise FileNotFoundError(workflow_run_id)
    return {
        "run": summarize_status_for_dashboard(status),
        "status": status.model_dump(mode="json", exclude_none=True),
        "events": read_events(workflow_run_id, after=0, limit=500),
        "revision": workflow_revision(),
    }
