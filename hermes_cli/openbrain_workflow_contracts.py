"""Contracts and sanitizers for OpenBrain source-processing workflows.

These models are deliberately small, strict monitor contracts. Step receipts may
wrap external payloads, but dashboard/status/event payloads forbid extra fields
so private or accidental data does not leak into the live monitor surface.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WorkflowStatusName = Literal["planned", "running", "blocked", "failed", "done", "stale"]
StepRuntimeStatus = Literal["pending", "ready", "planned", "running", "blocked", "failed", "done", "stale"]
WorkflowEventType = Literal[
    "workflow_started",
    "task_created",
    "task_started",
    "phase_started",
    "candidate_started",
    "candidate_completed",
    "heartbeat_sent",
    "dashboard_snapshot_updated",
    "enrichment_completed",
    "dedupe_completed",
    "task_blocked",
    "task_completed",
    "workflow_completed",
    "workflow_failed",
]
BranchMode = Literal["panning-only", "meeting-only", "panning-and-meeting"]

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|credential|password|secret|token)\b\s*[:=]\s*[^\s,;]+"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")
_URL_RE = re.compile(r"\b(?:https?|file)://[^\s\"'<>]+")
_UNC_RE = re.compile(r"\\\\[^\s\"'<>]+")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>]+")
_POSIX_PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])/(?:mnt|home|Users|private|var|tmp)/(?:[^\s\"'<>]+)"
)
_WHITESPACE_RE = re.compile(r"\s+")


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for workflow artifacts."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp_text(value: Any, max_length: int = 500) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if max_length > 0 and len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text


def redact_workflow_text(value: Any, *, max_length: int = 500) -> str:
    """Redact text intended for heartbeat/dashboard summaries.

    Uses Hermes' central redactor when importable, then applies a conservative
    local pass for path/URL/assignment-style secrets that should not appear in
    Kanban heartbeats or live dashboard summaries.
    """

    text = str(value or "")
    try:
        from agent.redact import redact_sensitive_text

        text = redact_sensitive_text(text, force=True)
    except Exception:
        pass
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted-key]", text)
    text = _URL_RE.sub("[url]", text)
    text = _UNC_RE.sub("[path]", text)
    text = _WIN_PATH_RE.sub("[path]", text)
    text = _POSIX_PRIVATE_PATH_RE.sub("[path]", text)
    return clamp_text(text, max_length=max_length)


def format_elapsed(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_heartbeat_note(
    *,
    workflow_run_id: str,
    step_key: str | None,
    source_unit_id: str,
    elapsed_seconds: int | float | None = None,
    phase: str | None = None,
    completed_candidates: int | None = None,
    total_candidates: int | None = None,
    current_candidate_id: str | None = None,
    current_candidate_title: str | None = None,
) -> str:
    """Build the compact safe Kanban heartbeat note required by the plan."""

    parts = [
        f"obwf={redact_workflow_text(workflow_run_id, max_length=120)}",
        f"step={redact_workflow_text(step_key or '-', max_length=80)}",
        f"source={redact_workflow_text(source_unit_id, max_length=120)}",
        f"elapsed={format_elapsed(elapsed_seconds)}",
    ]
    if phase:
        parts.append(f"phase={redact_workflow_text(phase, max_length=80)}")
    if completed_candidates is not None or total_candidates is not None:
        done = 0 if completed_candidates is None else max(0, int(completed_candidates))
        total = "?" if total_candidates is None else str(max(0, int(total_candidates)))
        parts.append(f"progress={done}/{total}")
    if current_candidate_id:
        parts.append(f"current={redact_workflow_text(current_candidate_id, max_length=120)}")
    if current_candidate_title:
        title = redact_workflow_text(current_candidate_title, max_length=80)
        if title:
            parts.append(f'title="{title}"')
    return clamp_text(" ".join(parts), 500)


class StepStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    status: StepRuntimeStatus = "pending"
    candidate_ids: list[str] = Field(default_factory=list, max_length=5000)
    receipt_path: str | None = Field(default=None, max_length=500)
    completed_at: str | None = None
    blocked_or_error: str | None = Field(default=None, max_length=500)

    @field_validator("blocked_or_error")
    @classmethod
    def _redact_error(cls, value: str | None) -> str | None:
        return redact_workflow_text(value, max_length=500) if value else value

    @field_validator("receipt_path")
    @classmethod
    def _preserve_receipt_path(cls, value: str | None) -> str | None:
        # Receipt paths are proof pointers in the private dashboard/status contract.
        # They must not be collapsed to ``[path]`` like heartbeat text.
        return clamp_text(value, max_length=500) if value else value


class WorkflowStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workflow_run_id: str
    source_unit_id: str
    source_title: str | None = Field(default=None, max_length=160)
    source_folder: str | None = Field(default=None, max_length=260)
    branch_mode: BranchMode | None = None
    active_step: str | None = None
    active_task_id: str | None = None
    worker_session_id: str | None = None
    status: WorkflowStatusName
    started_at: str
    updated_at: str
    last_heartbeat_at: str | None = None
    elapsed_seconds: int | None = Field(default=None, ge=0)
    current_phase: str | None = Field(default=None, max_length=80)
    current_candidate_id: str | None = Field(default=None, max_length=120)
    current_candidate_title_sanitized: str | None = Field(default=None, max_length=120)
    completed_candidates: int = Field(default=0, ge=0)
    total_candidates: int = Field(default=0, ge=0)
    receipt_path: str | None = Field(default=None, max_length=500)
    dashboard_snapshot_revision: str | None = None
    blocked_or_error: str | None = Field(default=None, max_length=500)
    steps: dict[str, StepStatus] = Field(default_factory=dict)

    @field_validator("source_title", "current_candidate_title_sanitized", "blocked_or_error")
    @classmethod
    def _sanitize_text_fields(cls, value: str | None) -> str | None:
        return redact_workflow_text(value, max_length=500) if value else value

    @field_validator("source_folder", "receipt_path")
    @classmethod
    def _preserve_private_pointer_fields(cls, value: str | None) -> str | None:
        # These fields are private proof/source pointers, not public heartbeat text.
        # Preserve the usable path while still bounding the payload size.
        return clamp_text(value, max_length=500) if value else value

    @model_validator(mode="after")
    def _progress_is_sane(self) -> "WorkflowStatus":
        if self.total_candidates and self.completed_candidates > self.total_candidates:
            raise ValueError("completed_candidates cannot exceed total_candidates")
        return self


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    event_id: int | None = Field(default=None, ge=1)
    workflow_run_id: str
    source_unit_id: str
    step_key: str | None = None
    task_id: str | None = None
    event_type: WorkflowEventType
    created_at: str
    phase: str | None = Field(default=None, max_length=80)
    candidate_id: str | None = Field(default=None, max_length=120)
    candidate_title_sanitized: str | None = Field(default=None, max_length=120)
    completed_candidates: int | None = Field(default=None, ge=0)
    total_candidates: int | None = Field(default=None, ge=0)
    message: str | None = Field(default=None, max_length=500)
    artifact_paths: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("candidate_title_sanitized", "message")
    @classmethod
    def _sanitize_strings(cls, value: str | None) -> str | None:
        return redact_workflow_text(value, max_length=500) if value else value


class DashboardVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    source_unit_id: str | None = None
    snapshot_revision: str | None = None
    visible_candidate_count: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _sanitize_note(cls, value: str | None) -> str | None:
        return redact_workflow_text(value, max_length=500) if value else value


class StatusArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_json: str
    events_jsonl: str
    dashboard_summary: str | None = None


class StepHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    source_unit_id: str
    source_folder: str | None = None
    step_key: str
    step_name: str
    candidate_count: int = Field(default=0, ge=0)
    candidate_ids: list[str] = Field(default_factory=list, max_length=5000)
    receipt_path: str | None = None
    dashboard_verification: DashboardVerification = Field(default_factory=DashboardVerification)
    status_artifacts: StatusArtifacts | None = None
    next_eligible_step: str | None = None


class WorkflowDashboardRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    source_unit_id: str
    source_title: str | None = None
    status: WorkflowStatusName
    active_step: str | None = None
    active_task_id: str | None = None
    kanban_task_state: str | None = None
    elapsed_seconds: int | None = Field(default=None, ge=0)
    last_heartbeat_at: str | None = None
    stale: bool = False
    current_phase: str | None = None
    current_candidate_id: str | None = None
    current_candidate_title_sanitized: str | None = None
    completed_candidates: int = Field(default=0, ge=0)
    total_candidates: int = Field(default=0, ge=0)
    latest_receipt_path: str | None = None
    blocked_or_error: str | None = None
    updated_at: str | None = None


class WorkflowDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: str
    revision: str
    changed: bool = True
    runs: list[WorkflowDashboardRun] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class VideoManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    duration_seconds: float = Field(ge=0)
    shows_real_surface: bool
    annotations_visible: bool
    secrets_reviewed: bool
    filebrowser_url: str | None = None


class VideoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workflow_run_id: str
    created_at: str
    videos: list[VideoManifestEntry] = Field(default_factory=list)
