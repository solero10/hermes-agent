"""OpenBrain ingestion dashboard backend API.

The dashboard reads a sanitized ingestion snapshot from
``get_hermes_home()/openbrain-ingestion-dashboard/snapshot.json`` and exposes a
small, source-scoped board API.  Snapshot validation intentionally normalizes
legacy stage names while rejecting unknown stages so the frontend only ever sees
canonical stage IDs.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status as http_status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_constants import get_hermes_home

router = APIRouter()

SNAPSHOT_DIRNAME = "openbrain-ingestion-dashboard"
SNAPSHOT_FILENAME = "snapshot.json"
ARCHIVE_STATE_FILENAME = "archive_state.json"
ARCHIVE_STATE_SCHEMA_VERSION = 1

StageId = Literal[
    "extracted",
    "shaped",
    "enrich",
    "deduped",
    "ready_for_cortexdb",
    "cortexdb",
]
Disposition = Literal["in_progress", "needs_review", "stopped", "imported"]
StageStatus = Literal[
    "complete",
    "current",
    "pending",
    "not_reached",
    "skipped",
    "failed",
    "review_needed",
]

CANONICAL_STAGES: tuple[str, ...] = (
    "extracted",
    "shaped",
    "enrich",
    "deduped",
    "ready_for_cortexdb",
    "cortexdb",
)

_STAGE_ALIASES: dict[str, str] = {
    "raw_extraction": "extracted",
    "raw-extraction": "extracted",
    "raw extraction": "extracted",
    # Legacy snapshots used a Policy stage.  The dashboard no longer shows a
    # Policy column; keep those cards visible in the candidate lane instead of
    # rejecting old data.
    "policy": "shaped",
    "policy_review": "shaped",
    "policy-review": "shaped",
    "thought_enrichment": "enrich",
    "thought-enrichment": "enrich",
    "enriched": "enrich",
    "ready": "ready_for_cortexdb",
    "ready_to_import": "ready_for_cortexdb",
    "ready-to-import": "ready_for_cortexdb",
}

_REMOVED_STAGE_ALIASES: dict[str, str] = {
    # Atomize was removed from Ken's OpenBrain dashboard on 2026-07-05. Keep a
    # compatibility shim so stale snapshots do not crash, but do not expose a
    # visible Atomize column or detail stage.
    "atomize": "enrich",
    "atomized": "enrich",
    "atomizer": "enrich",
    # Provenance and Entities/action recipes are retired for captured thoughts.
    # Map stale stage names back to the last remaining recipe lane instead of
    # exposing retired columns or pretending Dedupe has run.
    "provenance": "enrich",
    "provenance_chains": "enrich",
    "provenance-chains": "enrich",
    "schema_aware_routing": "enrich",
    "schema-aware-routing": "enrich",
    "entities/action": "enrich",
    "entities_action": "enrich",
    "entity_action": "enrich",
    "entities": "enrich",
}

_STAGE_LABELS: dict[str, str] = {
    "extracted": "Evidence cards",
    "shaped": "Thought candidates",
    "enrich": "Enrich",
    "deduped": "Deduped",
    "ready_for_cortexdb": "Ready for CortexDB",
    "cortexdb": "CortexDB",
}

RECIPE_STAGE_IDS: tuple[str, ...] = ("enrich",)
_RECIPE_STAGE_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "enrich": ("thought_enrichment", "thought-enrichment", "enriched"),
}

POLICY_STOP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "needs_source_validation",
        "label": "Needs source validation",
        "definition": "Potentially useful, but the supporting evidence is weak, outline-only, voicemail-derived, or ambiguous. Verify against the source before capture.",
    },
    {
        "id": "sensitive_detail",
        "label": "Sensitive detail",
        "definition": "Contains a raw private identifier, case/reference/account number, emergency/contact detail, or similar information that should stay in controlled evidence rather than general memory.",
    },
    {
        "id": "stale_task",
        "label": "Stale task",
        "definition": "Looks like an old action item or status update. Do not store it as current memory until completion/current relevance is checked or rewritten as history.",
    },
    {
        "id": "obsolete_internal",
        "label": "Obsolete internal process",
        "definition": "Old employer/company-specific process mechanics with no reusable lesson. Keep auditable in source artifacts, but do not capture as CortexDB memory.",
    },
    {
        "id": "too_thin",
        "label": "Too thin / missing context",
        "definition": "Not self-contained enough to become a reliable memory: missing who/what/why, identifiers, or enough detail to avoid misleading future retrieval.",
    },
    {
        "id": "no_durable_value",
        "label": "No durable value",
        "definition": "Purely incidental, time-specific, already-expired, or not useful enough to keep as long-term memory.",
    },
)

_ALLOWED_FILTERS = {
    "all",
    "imported",
    "stopped",
    "needs_review",
    "review_needed",
    "in_progress",
    "zero_thoughts",
}
_ALLOWED_SORTS = {"default", "newest", "oldest", "most_thoughts", "most_stopped"}

# Known public.thoughts columns from current and legacy OpenBrain schema/migrations.
# The dashboard is snapshot-backed, so detail responses fill only values that
# are present in the sanitized snapshot/receipt or safe schema defaults; missing
# values stay visible but blank in the UI.
THOUGHT_DATABASE_FIELDS: tuple[dict[str, str], ...] = (
    {"name": "id", "description": "CortexDB thought UUID"},
    {"name": "content", "description": "Captured thought text"},
    {"name": "embedding", "description": "Semantic-search vector; omitted from dashboard payload"},
    {"name": "metadata", "description": "Structured JSON metadata"},
    {"name": "source", "description": "Legacy capture source column, when present"},
    {"name": "created_at", "description": "Database creation timestamp"},
    {"name": "updated_at", "description": "Database update timestamp"},
    {"name": "content_fingerprint", "description": "Normalized-content SHA-256 fingerprint"},
    {"name": "type", "description": "Thought type"},
    {"name": "source_type", "description": "Source family/type"},
    {"name": "importance", "description": "OpenBrain importance score, 0–6"},
    {"name": "quality_score", "description": "Capture quality score, 0–100"},
    {"name": "sensitivity_tier", "description": "OpenBrain sensitivity tier"},
    {"name": "status", "description": "Workflow status for tasks/ideas"},
    {"name": "status_updated_at", "description": "Workflow status timestamp"},
    {"name": "enriched", "description": "Whether enrichment has completed"},
    {"name": "derived_from", "description": "Parent thought IDs for derived artifacts"},
    {"name": "derivation_method", "description": "How a derived thought was generated"},
    {"name": "derivation_layer", "description": "primary or derived"},
    {"name": "supersedes", "description": "Prior thought UUID replaced by this row"},
)

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "cookie",
    "private_key",
)

# Deliberately do not use a broad "long alphanumeric" token regex here: stable
# source and lineage IDs can be long and must remain intact for source-scoped
# dashboard navigation.
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])"
    r"(?P<key>authorization)(?![A-Za-z0-9_-])"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"
)
_GENERIC_SECRET_KEY_RE = (
    r"(?:access[_-]?token|refresh[_-]?token|(?:x[_-]?)?api[_-]?key|apikey|"
    r"authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9_-])(?P<key>{_GENERIC_SECRET_KEY_RE})(?![A-Za-z0-9_-])"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?:(?P<quote>['\"])(?P<quoted_value>[^'\"\r\n]*)(?P=quote)|(?P<bare_value>[^&\s'\"),;<>#]+))"
)
_OPENAI_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk|sess|org|rk)-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_TRUNCATED_OPENAI_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk|sess|org|rk)-[A-Za-z0-9_-]{2,}\.\.\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])"
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_PATH_SEGMENT_RE = r"[^\\/\s'\"()<>]+"
_PATH_SEGMENT_WITH_SPACES_RE = rf"{_PATH_SEGMENT_RE}(?: {_PATH_SEGMENT_RE})*"
_PATH_SEGMENT_WITH_DOT_RE = r"[^\\/\s'\"()<>]*\.[^\\/\s'\"()<>]*"
_PATH_SPACED_FINAL_SEGMENT_RE = (
    rf"{_PATH_SEGMENT_RE}(?: {_PATH_SEGMENT_RE})* {_PATH_SEGMENT_WITH_DOT_RE}"
)
_PATH_FINAL_SEGMENT_RE = rf"(?:{_PATH_SPACED_FINAL_SEGMENT_RE}|{_PATH_SEGMENT_RE})"
_POSIX_PATH_BODY_RE = rf"/(?!/)(?:{_PATH_SEGMENT_WITH_SPACES_RE}/)*{_PATH_FINAL_SEGMENT_RE}"
_WINDOWS_DRIVE_PATH_BODY_RE = (
    rf"[A-Z]:[\\/](?:{_PATH_SEGMENT_WITH_SPACES_RE}[\\/])*{_PATH_FINAL_SEGMENT_RE}"
)
_UNC_PATH_BODY_RE = rf"\\\\(?:{_PATH_SEGMENT_WITH_SPACES_RE}[\\/])+{_PATH_FINAL_SEGMENT_RE}"
_PRIVATE_PATH_RE = re.compile(
    rf"(?i)(?P<prefix>^|[\s'\"`(=\[:{{])"
    rf"(?P<path>{_POSIX_PATH_BODY_RE}|{_WINDOWS_DRIVE_PATH_BODY_RE}|{_UNC_PATH_BODY_RE})"
)
_URL_RE = re.compile(r"(?i)\b(?:file|https?)://[^\s'\")<>]+")
_REFERENCE_TAIL_HARD_BOUNDARY_CHARS = frozenset("\r\n'\"`()<>{}[]")
_REFERENCE_TAIL_SENTENCE_BOUNDARY_CHARS = frozenset(",;!?|")

_BOUND_SOURCE_TEXT_KEYS = {
    "source_snippet",
    "source_text",
    "source_quote",
    "source_excerpt",
    "raw_text",
    "raw_content",
    "raw_snippet",
    "raw_quote",
    "quote",
    "excerpt",
}

_FORMATION_TEXT_LIMITS = {
    "llm_input_text": 20_000,
    "llm_output_text": 8_000,
    "merge_note": 2_000,
    "text": 4_000,
    "summary": 1_000,
    "quote": 1_000,
    "source_snippet": 1_000,
}
_FORMATION_DEFAULT_TEXT_LIMIT = 4_000
_FORMATION_TRACE_META_ONLY_KEYS = {"version", "stage"}

_KNOWN_GENERATION_TECHNIQUES: dict[str, dict[str, str]] = {
    "panning-for-gold": {
        "label": "Panning for Gold",
        "short_label": "Panning",
        "kind": "recipe",
    },
    "meeting-synthesis": {
        "label": "Meeting Synthesis",
        "short_label": "Meeting",
        "kind": "skill",
    },
}

_RAW_REFERENCE_KEYS = {
    "path",
    "source_path",
    "absolute_path",
    "local_path",
    "raw_path",
    "url",
    "source_url",
    "raw_url",
    "file_url",
    "link",
    "source_link",
    "raw_link",
    "hidden_reasoning",
    "chain_of_thought",
    "cot",
    "reasoning_trace",
    "raw_prompt",
    "system_prompt",
    "developer_prompt",
    "internal_prompt",
    "raw_transcript",
    "transcript_dump",
}

_OMIT = object()


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _normalize_stage(value: Any) -> str:
    """Return the canonical dashboard stage ID for ``value``.

    Known legacy aliases are normalized.  Unknown values are returned as-is so
    Pydantic Literal validation can raise a clear schema error in Snapshot
    validation.
    """

    text = str(value or "").strip()
    lowered = text.lower()
    canonical = _STAGE_ALIASES.get(lowered, lowered)
    return _REMOVED_STAGE_ALIASES.get(canonical, canonical)


def _stage_label(stage_id: Any) -> str:
    return _STAGE_LABELS.get(_normalize_stage(stage_id), str(stage_id or "").strip())


def _empty_columns() -> dict[str, list[dict[str, Any]]]:
    return {stage_id: [] for stage_id in CANONICAL_STAGES}


# ---------------------------------------------------------------------------
# Privacy helpers
# ---------------------------------------------------------------------------


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key or "").strip().lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _is_url(text: str) -> bool:
    return bool(re.match(r"(?i)^[a-z][a-z0-9+.-]*://", text.strip()))


def _is_file_url(text: str) -> bool:
    return text.strip().lower().startswith("file://")


def _is_private_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower().strip("[]")
    if host in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        nums = [int(part) for part in parts]
        if nums[0] == 10 or nums[0] == 127 or nums[0] == 0:
            return True
        if nums[0] == 192 and nums[1] == 168:
            return True
        if nums[0] == 172 and 16 <= nums[1] <= 31:
            return True
    return False


def _is_private_url(text: str) -> bool:
    raw = text.strip()
    if not _is_url(raw):
        return False
    if _is_file_url(raw):
        return True
    parsed = urlparse(raw)
    return _is_private_hostname(parsed.hostname)


def _is_posix_absolute_path(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("/") and not stripped.startswith("//")


def _is_windows_absolute_path(text: str) -> bool:
    stripped = text.strip()
    return bool(re.match(r"(?i)^[a-z]:[\\/]", stripped)) or stripped.startswith("\\\\")


def _looks_like_private_reference(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return (
        _is_file_url(stripped)
        or _is_private_url(stripped)
        or _is_posix_absolute_path(stripped)
        or _is_windows_absolute_path(stripped)
    )


def _basename_from_path_or_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _is_url(text):
        parsed = urlparse(text)
        text = parsed.path or ""
    text = text.rstrip("/\\")
    if not text:
        return None
    # pathlib on POSIX does not split Windows separators, so split both.
    base = re.split(r"[\\/]", text)[-1].strip()
    return base or None


def _reference_needs_tail_extension(candidate: str) -> bool:
    """Return True when a matched private reference may have stopped at a spaced tail.

    The base path/URL matchers intentionally stop at whitespace to avoid eating
    ordinary prose.  That can split unquoted private references such as
    ``/mnt/d/private/My Folder`` or ``file:///tmp/My Folder/file.md`` after the
    first spaced component.  Extension is limited to candidates whose matched
    basename has no dot; dotted basenames already cover the common file case.
    """

    base = _basename_from_path_or_url(candidate)
    return bool(base and "." not in base)


def _extend_private_reference_tail(text: str, end: int) -> int:
    """Extend a redaction span over an unescaped private path/URL tail.

    For ambiguous unquoted paths with spaces, privacy is preferable to leaking a
    final directory/file tail.  Extend only when the next character is a space,
    and stop at likely sentence or structural boundaries so unrelated surrounding
    prose is not removed across clauses, quoted strings, or lines.
    """

    if end >= len(text) or text[end] != " ":
        return end

    cursor = end
    while cursor < len(text):
        char = text[cursor]
        if char in _REFERENCE_TAIL_HARD_BOUNDARY_CHARS:
            break
        if char in _REFERENCE_TAIL_SENTENCE_BOUNDARY_CHARS:
            break
        if char == "." and (cursor + 1 == len(text) or text[cursor + 1].isspace()):
            break
        cursor += 1
    return cursor


def _redact_private_urls(text: str) -> str:
    clean: list[str] = []
    cursor = 0
    changed = False
    for match in _URL_RE.finditer(text):
        if match.start() < cursor:
            continue
        candidate = match.group(0)
        if not (_is_private_url(candidate) or _is_file_url(candidate)):
            continue
        end = match.end()
        if _reference_needs_tail_extension(candidate):
            end = _extend_private_reference_tail(text, end)
        clean.append(text[cursor : match.start()])
        clean.append("[REDACTED_URL]")
        cursor = end
        changed = True
    if not changed:
        return text
    clean.append(text[cursor:])
    return "".join(clean)


def _redact_private_paths(text: str) -> str:
    clean: list[str] = []
    cursor = 0
    changed = False
    for match in _PRIVATE_PATH_RE.finditer(text):
        path_start = match.start("path")
        if path_start < cursor:
            continue
        candidate = match.group("path")
        if not _looks_like_private_reference(candidate):
            continue
        end = match.end("path")
        if _reference_needs_tail_extension(candidate):
            end = _extend_private_reference_tail(text, end)
        clean.append(text[cursor:path_start])
        clean.append("[REDACTED_PATH]")
        cursor = end
        changed = True
    if not changed:
        return text
    clean.append(text[cursor:])
    return "".join(clean)


def _redact_sensitive_text(value: str) -> str:
    def _secret_assignment_repl(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('key')}{match.group('sep')}{quote}[REDACTED]{quote}"

    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]", value)
    text = _BEARER_RE.sub("[REDACTED]", text)
    text = _TRUNCATED_OPENAI_KEY_RE.sub("[REDACTED]", text)
    text = _OPENAI_KEY_RE.sub("[REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(_secret_assignment_repl, text)
    text = _redact_private_urls(text)
    return _redact_private_paths(text)


def _should_bound_source_text(key: Any) -> bool:
    lowered = str(key or "").strip().lower()
    return lowered in _BOUND_SOURCE_TEXT_KEYS


def _bound_source_text(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _bound_formation_text(value: str, key: Any) -> str:
    limit = _FORMATION_TEXT_LIMITS.get(str(key or "").strip().lower(), _FORMATION_DEFAULT_TEXT_LIMIT)
    return _bound_source_text(value, limit)


def _sanitize_formation_node(value: Any, key: Any = None) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if _is_raw_reference_key(key):
        return _OMIT
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            sanitized = _sanitize_formation_node(raw_value, raw_key)
            if sanitized is not _OMIT:
                clean[raw_key] = sanitized
        return clean
    if isinstance(value, list):
        return [_sanitize_formation_node(item, key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_formation_node(item, key) for item in value]
    if isinstance(value, str):
        return _bound_formation_text(_redact_sensitive_text(value), key)
    return value


def _is_raw_reference_key(key: Any) -> bool:
    lowered = str(key or "").strip().lower()
    if lowered in _RAW_REFERENCE_KEYS:
        return True
    if lowered.startswith("raw_") and any(part in lowered for part in ("path", "url", "link")):
        return True
    if lowered.startswith("file_") and any(part in lowered for part in ("path", "url", "link")):
        return True
    return False


def _sanitize_source_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    clean: dict[str, Any] = {}
    for key in ("kind", "source_unit_id", "display_path"):
        if key not in value:
            continue
        item = value[key]
        if key == "display_path":
            if not isinstance(item, str) or _looks_like_private_reference(item) or _is_url(item):
                continue
        sanitized = _sanitize_node(item, key)
        if sanitized is not _OMIT:
            clean[key] = sanitized
    return clean


def _sanitize_node(value: Any, key: Any = None) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"

    if key == "source_ref":
        return _sanitize_source_ref(value)

    if key in {"formation_trace", "stage_detail"}:
        return _sanitize_formation_node(value)

    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key_text = str(raw_key)
            if key_text == "source_ref":
                source_ref = _sanitize_source_ref(raw_value)
                if source_ref:
                    clean[key_text] = source_ref
                continue
            if _is_raw_reference_key(key_text):
                # Raw path/url/link fields are implementation artifacts and can
                # contain private local files or localhost import URLs.  The
                # frontend only needs source_ref.display_path when it is safe.
                continue
            if (
                isinstance(raw_value, str)
                and any(part in key_text.lower() for part in ("path", "url", "link"))
                and _looks_like_private_reference(raw_value)
            ):
                continue
            sanitized = _sanitize_node(raw_value, key_text)
            if sanitized is not _OMIT:
                clean[key_text] = sanitized
        return clean

    if isinstance(value, list):
        clean_items = []
        for item in value:
            sanitized = _sanitize_node(item, key)
            if sanitized is not _OMIT:
                clean_items.append(sanitized)
        return clean_items

    if isinstance(value, tuple):
        return [_sanitize_node(item, key) for item in value]

    if isinstance(value, str):
        if _looks_like_private_reference(value):
            # If a private path/URL escaped a raw reference field, avoid leaking
            # directories or hosts.  Keep a basename for non-URL path-like labels
            # where it may still be useful and non-sensitive.
            if _is_file_url(value) or _is_private_url(value):
                return "[REDACTED_URL]"
            base = _basename_from_path_or_url(value)
            return _redact_sensitive_text(base or "[REDACTED_PATH]")
        text = _redact_sensitive_text(value)
        if _should_bound_source_text(key):
            text = _bound_source_text(text)
        return text

    return value


def _sanitize_snapshot(snapshot: Any) -> dict[str, Any]:
    """Return a privacy-safe snapshot dictionary for API/model consumption."""

    if not isinstance(snapshot, dict):
        return {}
    return _sanitize_node(copy.deepcopy(snapshot))


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: StageId
    status: StageStatus = "pending"
    label: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _canonical_id(cls, value: Any) -> str:
        return _normalize_stage(value)

    @model_validator(mode="after")
    def _canonical_label(self) -> "StageRecord":
        self.label = _stage_label(self.id)
        return self


class RelatedMemory(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    score: float | None = None


class CortexDBReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str | None = None
    captured_at: str | None = None
    ingestion_run_id: str | None = None
    source_unit_id: str | None = None
    candidate_id: str | None = None
    stored_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    update_note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_receipt_payload(cls, value: Any) -> Any:
        return _sanitize_formation_node(value)


class ExtractedEvidenceDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    source_section: str | None = None
    extraction_method: str | None = None
    used_by_shape_ids: list[str] = Field(default_factory=list)
    promotion_note: str | None = None
    not_promoted_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_extracted_payload(cls, value: Any) -> Any:
        return _sanitize_formation_node(value)


class PolicyDecisionDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: str = "not_run"
    stop_code: str | None = None
    reason: str | None = None
    redaction_note: str | None = None
    candidate_text_reviewed: str | None = None
    redacted_text: str | None = None
    fix_note: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_policy_payload(cls, value: Any) -> Any:
        return _sanitize_formation_node(value)


class WorkflowStepDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str | None = None
    result: str | None = None
    decision: str | None = None
    recipe: str | None = None
    method: str | None = None
    note: str | None = None
    reason: str | None = None
    skipped_reason: str | None = None
    created_count: int | None = None
    created_candidate_ids: list[str] = Field(default_factory=list)
    parent_candidate_id: str | None = None
    parent_lineage_id: str | None = None
    evidence_note: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_workflow_payload(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"status": value}
        return _sanitize_formation_node(value)


class DedupeEvidenceDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method: str = "not_run"
    decision: str = "not_run"
    matched_memory_id: str | None = None
    matched_memory_title: str | None = None
    similarity_score: float | None = None
    nearest_memory_id: str | None = None
    nearest_memory_title: str | None = None
    nearest_similarity_score: float | None = None
    semantic_duplicate_cutoff: float | None = None
    search_threshold: float | None = None
    content_fingerprint: str | None = None
    exact_match: bool | None = None
    merge_note: str | None = None
    evidence_note: str | None = None
    decided_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_dedupe_payload(cls, value: Any) -> Any:
        return _sanitize_formation_node(value)


class ReadyPackageDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    final_memory_text: str | None = None
    memory_type: str | None = None
    topics: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    source_receipt: str | None = None
    policy_check: str | None = None
    dedupe_check: str | None = None
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    import_payload_preview: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _sanitize_ready_payload(cls, value: Any) -> Any:
        return _sanitize_formation_node(value)


class DatabaseFieldRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    value: Any = None
    populated: bool = False
    note: str | None = None


class GenerationTechnique(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    label: str | None = None
    kind: str | None = None
    short_label: str | None = None
    version: str | None = None
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        return _normalize_generation_technique(value) or value

    @model_validator(mode="after")
    def _fill_known_defaults(self) -> "GenerationTechnique":
        if not self.id and self.label:
            self.id = _generation_technique_id(self.label)
        if self.id and not self.label:
            self.label = _generation_technique_label(self.id)
        if self.id and not self.short_label:
            self.short_label = _generation_technique_short_label(self.id, self.label)
        if self.id and not self.kind:
            self.kind = _generation_technique_kind(self.id)
        return self


class FormationActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str | None = None
    provider: str | None = None
    model: str | None = None
    tool: str | None = None


class FormationLineageCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lineage_id: str | None = None
    id: str | None = None
    stage: StageId | None = None
    title: str | None = None
    summary: str | None = None
    quote: str | None = None
    source_snippet: str | None = None
    topics: list[str] = Field(default_factory=list)

    @field_validator("stage", mode="before")
    @classmethod
    def _canonical_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _normalize_stage(text) if text else None


class FormationContextItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = "other"
    label: str | None = None
    text: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    lineage_id: str | None = None
    title: str | None = None
    summary: str | None = None
    quote: str | None = None
    source_snippet: str | None = None
    why_used: str | None = None


class FormationGateEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: StageId | None = None
    status: str | None = None
    decision: str | None = None
    reason: str | None = None
    target_id: str | None = None
    target_label: str | None = None
    created_at: str | None = None

    @field_validator("stage", mode="before")
    @classmethod
    def _canonical_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return _normalize_stage(value)


class FormationTrace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    stage: StageId | None = None
    generation_technique: GenerationTechnique | None = None
    created_at: str | None = None
    created_by: FormationActor | None = None
    primary_lineage_ids: list[str] = Field(default_factory=list)
    primary_lineage_cards: list[FormationLineageCard] = Field(default_factory=list)
    additional_context_used: list[FormationContextItem] = Field(default_factory=list)
    llm_input_text: str | None = None
    llm_output_text: str | None = None
    output_title: str | None = None
    suggested_type: str | None = None
    merge_note: str | None = None
    gate_events: list[FormationGateEvent] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _sanitize_trace_payload(cls, value: Any) -> Any:
        clean = _sanitize_formation_node(value)
        if isinstance(clean, dict):
            technique = _generation_technique_from_record(clean)
            if technique:
                clean["generation_technique"] = technique
        return clean

    @field_validator("stage", mode="before")
    @classmethod
    def _canonical_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _normalize_stage(text) if text else None


def _formation_trace_has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json", exclude_none=True)
    elif isinstance(value, dict):
        data = value
    else:
        return False
    for key, item in data.items():
        if key in _FORMATION_TRACE_META_ONLY_KEYS:
            continue
        if item not in (None, "", [], {}):
            return True
    return False


def _detail_has_content(value: Any, *, meta_only_keys: set[str] | None = None) -> bool:
    if value is None:
        return False
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json", exclude_none=True)
    elif isinstance(value, dict):
        data = value
    else:
        return False
    ignored = meta_only_keys or set()
    for key, item in data.items():
        if key in ignored:
            continue
        if item not in (None, "", [], {}):
            return True
    return False


class StageDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    extracted: ExtractedEvidenceDetail | None = None
    shaped: FormationTrace | None = None
    policy: PolicyDecisionDetail | None = None
    enrich: WorkflowStepDetail | None = None
    deduped: DedupeEvidenceDetail | None = None
    ready_for_cortexdb: ReadyPackageDetail | None = None
    cortexdb: CortexDBReceipt | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_stage_detail_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return _sanitize_formation_node(value)
        clean = _sanitize_formation_node(value)
        if isinstance(clean, dict):
            for alias, canonical in (
                ("thought_enrichment", "enrich"),
                ("thought-enrichment", "enrich"),
                ("enriched", "enrich"),
            ):
                if alias in clean and canonical not in clean:
                    clean[canonical] = clean[alias]
        return clean

    @model_validator(mode="after")
    def _drop_empty_stage_sections(self) -> "StageDetail":
        if not _formation_trace_has_content(self.shaped):
            self.shaped = None
        if not _detail_has_content(self.extracted):
            self.extracted = None
        if not _detail_has_content(self.policy, meta_only_keys={"result"}):
            self.policy = None
        for key in RECIPE_STAGE_IDS:
            if not _detail_has_content(getattr(self, key), meta_only_keys={"status", "result", "decision"}):
                status_detail = getattr(self, key)
                status = getattr(status_detail, "status", None) if status_detail else None
                result = getattr(status_detail, "result", None) if status_detail else None
                decision = getattr(status_detail, "decision", None) if status_detail else None
                if str(status or result or decision or "").strip().lower() not in {"skipped", "skip", "complete", "completed", "done", "failed", "error"}:
                    setattr(self, key, None)
        if not _detail_has_content(self.deduped, meta_only_keys={"method", "decision"}):
            self.deduped = None
        if not _detail_has_content(self.ready_for_cortexdb):
            self.ready_for_cortexdb = None
        if not _detail_has_content(self.cortexdb):
            self.cortexdb = None
        return self


def _stage_detail_has_content(value: Any) -> bool:
    return _detail_has_content(value)


class ProducerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    adapter: str
    source_type: str | None = None
    generation_technique: GenerationTechnique | None = None
    run_root_label: str | None = None
    artifacts: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_producer_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("generation_technique"):
            technique = _normalize_generation_technique(
                data.get("technique") or data.get("recipe") or data.get("skill") or data.get("kind")
            )
            if technique:
                data["generation_technique"] = technique
        data.pop("technique", None)
        data.pop("recipe", None)
        data.pop("skill", None)
        return data

    @field_validator("run_root_label", mode="before")
    @classmethod
    def _safe_run_root_label(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if _is_private_url(text) or _is_file_url(text):
            return None
        return _redact_sensitive_text(_basename_from_path_or_url(text) or text)

    @field_validator("artifacts", mode="before")
    @classmethod
    def _safe_artifacts(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]
        clean: list[str] = []
        for item in items:
            if item is None:
                continue
            text = str(item).strip()
            if not text or _is_private_url(text) or _is_file_url(text):
                continue
            base = _basename_from_path_or_url(text)
            if base:
                clean.append(_redact_sensitive_text(base))
        return clean


class SourceType(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    count: int = 0


class ThoughtRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    lineage_id: str
    candidate_id: str | None = None
    title: str | None = None
    summary: str | None = None
    current_stage: StageId = "extracted"
    disposition: Disposition = "in_progress"
    needs_review: bool | None = None
    stop_code: str | None = None
    stop_target_id: str | None = None
    stop_target_label: str | None = None
    stop_stage_id: StageId | None = None
    topics: list[str] = Field(default_factory=list)
    confidence: float | None = None
    source_snippet: str | None = None
    raw_text: str | None = None
    quote: str | None = None
    final_memory_text: str | None = None
    stopped_reason: str | None = None
    matched_memory_id: str | None = None
    generation_technique: GenerationTechnique | None = None
    workflow_status: dict[str, WorkflowStepDetail] | None = None
    formation_trace: FormationTrace | None = None
    stage_detail: StageDetail | None = None
    stages: list[StageRecord] | None = None
    related_memories: list[RelatedMemory] = Field(default_factory=list)
    cortexdb_receipt: CortexDBReceipt | None = None
    cortexdb_id: str | None = None
    archived: bool = False
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_before(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("lineage_id") and data.get("id"):
            data["lineage_id"] = data["id"]
        if not data.get("id") and data.get("lineage_id"):
            data["id"] = data["lineage_id"]
        if "current_stage" in data:
            data["current_stage"] = _normalize_stage(data["current_stage"])
        if data.get("stop_stage_id") is not None:
            data["stop_stage_id"] = _normalize_stage(data["stop_stage_id"])
        technique = _generation_technique_from_record(data)
        if technique:
            data["generation_technique"] = technique
        if "formation_trace" in data and not _formation_trace_has_content(data.get("formation_trace")):
            data.pop("formation_trace", None)
        stage_detail = data.get("stage_detail")
        stage_detail = dict(stage_detail) if isinstance(stage_detail, dict) else {}
        if _formation_trace_has_content(data.get("formation_trace")) and not stage_detail.get("shaped"):
            stage_detail["shaped"] = data.get("formation_trace")
        if data.get("cortexdb_receipt") and not stage_detail.get("cortexdb"):
            stage_detail["cortexdb"] = data.get("cortexdb_receipt")
        if stage_detail:
            data["stage_detail"] = stage_detail
        else:
            data.pop("stage_detail", None)
        if str(data.get("disposition") or "").strip() == "stopped":
            stopped_reason = str(data.get("stopped_reason") or "").lower()
            stop_code = str(data.get("stop_code") or "").strip() or None
            if not stop_code:
                if "duplicate" in stopped_reason or data.get("matched_memory_id"):
                    stop_code = "duplicate"
                elif "reference" in stopped_reason or "merge" in stopped_reason:
                    stop_code = "reference_merge"
                elif "obsolete" in stopped_reason or data.get("current_stage") == "policy":
                    stop_code = "obsolete"
                elif "policy" in stopped_reason:
                    stop_code = "policy"
                else:
                    stop_code = "other"
            data["stop_code"] = stop_code
            if data.get("stop_stage_id") is None:
                data["stop_stage_id"] = data.get("current_stage")
            if data.get("stop_target_id") is None and data.get("matched_memory_id"):
                data["stop_target_id"] = data["matched_memory_id"]
            if data.get("stop_target_label") is None:
                related = data.get("related_memories")
                if isinstance(related, list) and related:
                    first = related[0]
                    if isinstance(first, dict):
                        if first.get("title"):
                            data["stop_target_label"] = first.get("title")
                        if data.get("stop_target_id") is None and first.get("id"):
                            data["stop_target_id"] = first.get("id")
        return data

    @field_validator("current_stage", mode="before")
    @classmethod
    def _canonical_current_stage(cls, value: Any) -> str:
        return _normalize_stage(value)

    @field_validator("stop_stage_id", mode="before")
    @classmethod
    def _canonical_stop_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _normalize_stage(text) if text else None

    @model_validator(mode="after")
    def _enforce_receipt_rule(self) -> "ThoughtRecord":
        if not self.id:
            self.id = self.lineage_id
        if not _formation_trace_has_content(self.formation_trace):
            self.formation_trace = None
        if self.stage_detail:
            if not self.formation_trace and _formation_trace_has_content(self.stage_detail.shaped):
                self.formation_trace = self.stage_detail.shaped
            if self.disposition != "imported":
                self.stage_detail.cortexdb = None
            elif not self.cortexdb_receipt and self.stage_detail.cortexdb:
                self.cortexdb_receipt = self.stage_detail.cortexdb
            if not _stage_detail_has_content(self.stage_detail):
                self.stage_detail = None
        if self.disposition != "imported":
            self.cortexdb_receipt = None
            self.cortexdb_id = None
            extra = getattr(self, "__pydantic_extra__", None)
            if isinstance(extra, dict):
                extra.pop("cortexdb_receipt", None)
                extra.pop("cortexdb_id", None)
                if self.stage_detail is None:
                    extra.pop("stage_detail", None)
        elif self.cortexdb_id is None and self.cortexdb_receipt and self.cortexdb_receipt.id:
            self.cortexdb_id = self.cortexdb_receipt.id
        return self


class SourceUnit(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source_type: str
    label: str | None = None
    subtitle: str | None = None
    source_ref: dict[str, Any] | None = None
    occurred_at: str | None = None
    processed_at: str | None = None
    thoughts: list[ThoughtRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_before(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("thoughts") is None:
            data["thoughts"] = []
        if "source_ref" in data:
            data["source_ref"] = _sanitize_source_ref(data.get("source_ref"))
        return data


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1]
    generated_at: str | None = None
    ingestion_run_id: str | None = None
    producer: ProducerInfo | None = None
    source_types: list[SourceType] = Field(default_factory=list)
    source_units: list[SourceUnit] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _inherit_producer_technique(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = copy.deepcopy(value)
        producer = data.get("producer")
        producer_technique = None
        if isinstance(producer, dict):
            producer_technique = _normalize_generation_technique(
                producer.get("generation_technique")
                or producer.get("technique")
                or producer.get("recipe")
                or producer.get("skill")
                or producer.get("kind")
            )
        if not producer_technique:
            return data
        for source_unit in data.get("source_units") or []:
            if not isinstance(source_unit, dict):
                continue
            for thought in source_unit.get("thoughts") or []:
                if isinstance(thought, dict) and not _generation_technique_from_record(thought):
                    thought["generation_technique"] = copy.deepcopy(producer_technique)
        return data


class ThoughtCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source_unit_id: str
    lineage_id: str
    candidate_id: str | None = None
    title: str | None = None
    summary: str | None = None
    current_stage: StageId
    disposition: Disposition
    needs_review: bool | None = None
    stop_code: str | None = None
    stop_target_id: str | None = None
    stop_target_label: str | None = None
    stop_stage_id: StageId | None = None
    topics: list[str] = Field(default_factory=list)
    confidence: float | None = None
    stopped_reason: str | None = None
    matched_memory_id: str | None = None
    generation_technique: GenerationTechnique | None = None
    workflow_status: dict[str, WorkflowStepDetail] | None = None
    dedupe_similarity_score: float | None = None
    dedupe_nearest_similarity_score: float | None = None
    cortexdb_id: str | None = None
    archived: bool = False
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None

    @field_validator("current_stage", mode="before")
    @classmethod
    def _canonical_current_stage(cls, value: Any) -> str:
        return _normalize_stage(value)

    @field_validator("stop_stage_id", mode="before")
    @classmethod
    def _canonical_stop_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _normalize_stage(text) if text else None

    @model_validator(mode="after")
    def _enforce_card_receipt_rule(self) -> "ThoughtCard":
        if self.disposition != "imported":
            self.cortexdb_id = None
        return self


class ThoughtDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    lineage_id: str
    candidate_id: str | None = None
    title: str | None = None
    summary: str | None = None
    current_stage: StageId
    disposition: Disposition
    needs_review: bool | None = None
    stop_code: str | None = None
    stop_target_id: str | None = None
    stop_target_label: str | None = None
    stop_stage_id: StageId | None = None
    topics: list[str] = Field(default_factory=list)
    confidence: float | None = None
    source_snippet: str | None = None
    raw_text: str | None = None
    quote: str | None = None
    final_memory_text: str | None = None
    stopped_reason: str | None = None
    matched_memory_id: str | None = None
    generation_technique: GenerationTechnique | None = None
    workflow_status: dict[str, WorkflowStepDetail] | None = None
    formation_trace: FormationTrace | None = None
    stage_detail: StageDetail | None = None
    source_unit: dict[str, Any]
    stages: list[StageRecord]
    related_memories: list[RelatedMemory] = Field(default_factory=list)
    cortexdb_receipt: CortexDBReceipt | None = None
    cortexdb_id: str | None = None
    database_fields: list[DatabaseFieldRecord] = Field(default_factory=list)
    archived: bool = False
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None

    @field_validator("current_stage", mode="before")
    @classmethod
    def _canonical_current_stage(cls, value: Any) -> str:
        return _normalize_stage(value)

    @field_validator("stop_stage_id", mode="before")
    @classmethod
    def _canonical_stop_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _normalize_stage(text) if text else None

    @model_validator(mode="after")
    def _enforce_detail_receipt_rule(self) -> "ThoughtDetail":
        if not _formation_trace_has_content(self.formation_trace):
            self.formation_trace = None
        if self.stage_detail:
            if not self.formation_trace and _formation_trace_has_content(self.stage_detail.shaped):
                self.formation_trace = self.stage_detail.shaped
            if self.disposition != "imported":
                self.stage_detail.cortexdb = None
            elif not self.cortexdb_receipt and self.stage_detail.cortexdb:
                self.cortexdb_receipt = self.stage_detail.cortexdb
            if not _stage_detail_has_content(self.stage_detail):
                self.stage_detail = None
        if self.disposition != "imported":
            self.cortexdb_receipt = None
            self.cortexdb_id = None
            extra = getattr(self, "__pydantic_extra__", None)
            if isinstance(extra, dict):
                extra.pop("cortexdb_receipt", None)
                extra.pop("cortexdb_id", None)
                if self.stage_detail is None:
                    extra.pop("stage_detail", None)
        elif self.cortexdb_id is None and self.cortexdb_receipt and self.cortexdb_receipt.id:
            self.cortexdb_id = self.cortexdb_receipt.id
        return self


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------


def _snapshot_path() -> Path:
    return get_hermes_home() / SNAPSHOT_DIRNAME / SNAPSHOT_FILENAME


def _archive_state_path() -> Path:
    return get_hermes_home() / SNAPSHOT_DIRNAME / ARCHIVE_STATE_FILENAME


def _empty_archive_state() -> dict[str, Any]:
    return {"schema_version": ARCHIVE_STATE_SCHEMA_VERSION, "updated_at": None, "source_units": {}}


def _load_archive_state() -> dict[str, Any]:
    path = _archive_state_path()
    if not path.exists():
        return _empty_archive_state()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _empty_archive_state()
    if not isinstance(payload, dict) or payload.get("schema_version") != ARCHIVE_STATE_SCHEMA_VERSION:
        return _empty_archive_state()
    source_units = payload.get("source_units")
    if not isinstance(source_units, dict):
        payload["source_units"] = {}
    return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fsync_parent_best_effort(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_archive_state_atomic(state: dict[str, Any]) -> None:
    clean = copy.deepcopy(state)
    clean["schema_version"] = ARCHIVE_STATE_SCHEMA_VERSION
    clean.setdefault("source_units", {})
    payload = json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    path = _archive_state_path()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_path = parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_best_effort(parent)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _sample_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "1970-01-01T00:00:00Z",
        "ingestion_run_id": "sample",
        "producer": {
            "kind": "sample",
            "adapter": "sample",
            "source_type": "transcripts",
            "run_root_label": "sample",
            "artifacts": [],
        },
        "source_types": [{"id": "transcripts", "label": "Transcripts", "count": 0}],
        "source_units": [],
    }


def _read_snapshot_dict() -> dict[str, Any]:
    path = _snapshot_path()
    if not path.exists():
        return _sample_snapshot()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _sample_snapshot()
    return payload if isinstance(payload, dict) else _sample_snapshot()


def _load_snapshot() -> Snapshot:
    clean = _sanitize_snapshot(_read_snapshot_dict())
    if not clean:
        clean = _sample_snapshot()
    return Snapshot.model_validate(clean)


# ---------------------------------------------------------------------------
# Board/detail helpers
# ---------------------------------------------------------------------------


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return {}


def _normalize_thought_for_response(thought: dict[str, Any]) -> dict[str, Any]:
    """Normalize one thought through the dashboard API contract.

    Snapshot exporters use this helper so stage-specific detail payloads are
    sanitized and backward-compatible before they are written to disk.  The
    public API still validates the full snapshot again when serving it.
    """

    clean = _sanitize_node(copy.deepcopy(thought))
    return ThoughtRecord.model_validate(clean).model_dump(mode="json", exclude_none=True)


def _receipt_id(thought: dict[str, Any]) -> str | None:
    if thought.get("disposition") != "imported":
        return None
    if thought.get("cortexdb_id"):
        return str(thought["cortexdb_id"])
    receipt = thought.get("cortexdb_receipt")
    if isinstance(receipt, dict) and receipt.get("id"):
        return str(receipt["id"])
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if value == [] or value == {}:
            continue
        return value
    return None


def _clean_text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _generation_technique_id(value: Any) -> str | None:
    text = _clean_text_or_none(value)
    if not text:
        return None
    text = re.sub(r"(?i)^(?:skills?|recipes?)[:/\\\s]+", "", text)
    text = text.strip().lower().replace("_", "-")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or None


def _generation_technique_label(identifier: Any) -> str | None:
    slug = _generation_technique_id(identifier)
    if not slug:
        return None
    known = _KNOWN_GENERATION_TECHNIQUES.get(slug)
    if known:
        return known["label"]
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _generation_technique_short_label(identifier: Any, label: Any = None) -> str | None:
    slug = _generation_technique_id(identifier)
    if slug and slug in _KNOWN_GENERATION_TECHNIQUES:
        return _KNOWN_GENERATION_TECHNIQUES[slug]["short_label"]
    text = _clean_text_or_none(label) or _generation_technique_label(slug)
    if not text:
        return None
    words = [part for part in re.split(r"\s+", text) if part]
    return " ".join(words[:2]) if words else None


def _generation_technique_kind(identifier: Any) -> str | None:
    slug = _generation_technique_id(identifier)
    if slug and slug in _KNOWN_GENERATION_TECHNIQUES:
        return _KNOWN_GENERATION_TECHNIQUES[slug]["kind"]
    return None


def _normalize_generation_technique(value: Any, *, default_kind: str | None = None) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, str):
        identifier = _generation_technique_id(value)
        if not identifier:
            return None
        label = _generation_technique_label(identifier)
        technique = {
            "id": identifier,
            "label": label,
            "short_label": _generation_technique_short_label(identifier, label),
            "kind": default_kind or _generation_technique_kind(identifier),
        }
        return {key: item for key, item in technique.items() if item not in (None, "", [], {})}
    if not isinstance(value, dict):
        return None

    raw_identifier = _first_present(
        value.get("id"),
        value.get("slug"),
        value.get("name"),
        value.get("technique"),
        value.get("method"),
        value.get("recipe"),
        value.get("skill"),
    )
    raw_label = _first_present(value.get("label"), value.get("display_name"), value.get("title"))
    identifier = _generation_technique_id(raw_identifier or raw_label)
    label = _clean_text_or_none(raw_label) or _generation_technique_label(identifier)
    if not identifier and not label:
        return None
    kind = _clean_text_or_none(value.get("kind") or value.get("type") or default_kind)
    if not kind and identifier:
        kind = _generation_technique_kind(identifier)
    technique = {
        "id": identifier,
        "label": label,
        "kind": kind,
        "short_label": _clean_text_or_none(value.get("short_label"))
        or _generation_technique_short_label(identifier, label),
        "version": _clean_text_or_none(value.get("version")),
        "source": _clean_text_or_none(value.get("source")),
    }
    return {key: item for key, item in technique.items() if item not in (None, "", [], {})} or None


def _generation_technique_from_record(record: Any) -> dict[str, Any] | None:
    if isinstance(record, BaseModel):
        record = record.model_dump(mode="json", exclude_none=True)
    if not isinstance(record, dict):
        return None

    direct_candidates: tuple[tuple[str, str | None], ...] = (
        ("generation_technique", None),
        ("technique", None),
        ("generation_method", None),
        ("derivation_method", None),
        ("recipe", "recipe"),
        ("generation_recipe", "recipe"),
        ("created_by_recipe", "recipe"),
        ("skill", "skill"),
        ("generation_skill", "skill"),
        ("created_by_skill", "skill"),
    )
    for key, default_kind in direct_candidates:
        technique = _normalize_generation_technique(record.get(key), default_kind=default_kind)
        if technique:
            return technique

    for nested_key in ("created_by", "producer", "formation_trace"):
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            technique = _generation_technique_from_record(nested)
            if technique:
                return technique
    return None


def _database_metadata(thought: dict[str, Any], source_unit: Any) -> dict[str, Any]:
    raw_metadata = thought.get("metadata")
    metadata = copy.deepcopy(raw_metadata) if isinstance(raw_metadata, dict) else {}

    for key in ("type", "source", "source_type", "importance", "quality_score", "sensitivity_tier"):
        value = thought.get(key)
        if value is not None and key not in metadata:
            metadata[key] = value
    for key in ("topics", "people", "dates_mentioned", "action_items", "tags"):
        value = thought.get(key)
        if value and key not in metadata:
            metadata[key] = copy.deepcopy(value)
    for key in ("lineage_id", "candidate_id"):
        value = thought.get(key)
        if value and key not in metadata:
            metadata[key] = value

    technique = _generation_technique_from_record(thought)
    if technique and "generation_technique" not in metadata:
        metadata["generation_technique"] = technique

    source_summary = _source_unit_summary(source_unit)
    if source_summary.get("id") and "source_unit_id" not in metadata:
        metadata["source_unit_id"] = source_summary["id"]
    if source_summary.get("label") and "source_label" not in metadata:
        metadata["source_label"] = source_summary["label"]
    if source_summary.get("source_type") and "dashboard_source_type" not in metadata:
        metadata["dashboard_source_type"] = source_summary["source_type"]

    return {key: value for key, value in metadata.items() if value is not None and value != [] and value != {}}


def _embedding_field_value(value: Any, *, imported: bool) -> tuple[Any, str | None]:
    if isinstance(value, list):
        return f"[embedding vector omitted; {len(value)} dimensions]", "Vector values are intentionally omitted from dashboard detail payloads."
    if isinstance(value, str) and value.strip():
        return "[embedding vector omitted]", "Vector values are intentionally omitted from dashboard detail payloads."
    if imported:
        return "[embedding vector omitted]", "Imported CortexDB rows normally have an embedding; the raw vector is intentionally omitted."
    return None, "No embedding is stored until the thought is captured."


def _receipt_field_value(receipt: dict[str, Any], field_name: str) -> Any:
    aliases = {
        "id": ("id", "thought_id", "uuid"),
        "created_at": ("created_at", "captured_at"),
        "content_fingerprint": ("content_fingerprint", "fingerprint"),
    }
    for key in aliases.get(field_name, (field_name,)):
        value = receipt.get(key)
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return None


def _content_fingerprint(content: Any) -> str | None:
    if not isinstance(content, str) or not content.strip():
        return None
    normalized = re.sub(r"\s+", " ", content).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _database_default_value(field_name: str, metadata: dict[str, Any]) -> tuple[Any, str | None]:
    if field_name == "type":
        return "observation", "Database/upsert default shown because no explicit value was present in the snapshot."
    if field_name == "source_type":
        source = _first_present(metadata.get("source"), "unknown")
        note = "Database/upsert derives source_type from metadata.source when source_type is absent."
        return source, note
    if field_name == "status":
        thought_type = _first_present(metadata.get("type"), "observation")
        if thought_type in {"task", "idea"}:
            return "new", "Database/upsert default for task/idea rows when status is absent."
        return None, None
    defaults = {
        "importance": 3,
        "quality_score": 70,
        "sensitivity_tier": "standard",
        "source": "mcp",
        "enriched": False,
        "derivation_layer": "primary",
    }
    if field_name not in defaults:
        return None, None
    return defaults[field_name], "Database/upsert default shown because no explicit value was present in the snapshot."


def _database_field_value(
    *,
    field_name: str,
    thought: dict[str, Any],
    source_unit: Any,
    metadata: dict[str, Any],
) -> tuple[Any, str | None]:
    raw_receipt = thought.get("cortexdb_receipt")
    receipt: dict[str, Any] = raw_receipt if isinstance(raw_receipt, dict) else {}
    imported = thought.get("disposition") == "imported"

    receipt_value = _receipt_field_value(receipt, field_name)
    explicit_value = _first_present(thought.get(field_name), receipt_value, metadata.get(field_name))
    if field_name == "id":
        return _receipt_id(thought), None
    if field_name == "content":
        return _first_present(explicit_value, receipt.get("content"), thought.get("final_memory_text")), None
    if field_name == "embedding":
        return _embedding_field_value(thought.get("embedding"), imported=imported)
    if field_name == "metadata":
        return _first_present(receipt.get("metadata"), metadata or None), None
    if field_name == "created_at":
        return _first_present(explicit_value, receipt.get("captured_at")), None
    if field_name == "content_fingerprint":
        if explicit_value is not None:
            return explicit_value, None
        if imported:
            content = _first_present(receipt.get("content"), thought.get("final_memory_text"), thought.get("content"))
            fingerprint = _content_fingerprint(content)
            if fingerprint:
                return fingerprint, "Derived with database normalization because the snapshot did not include this column."
        return None, None
    if field_name == "source":
        if explicit_value is not None:
            return explicit_value, None
        return _first_present(metadata.get("source")), None
    if field_name == "source_type":
        return _first_present(explicit_value, metadata.get("source_type"), metadata.get("source")), None
    if explicit_value is not None:
        return explicit_value, None
    if imported:
        return _database_default_value(field_name, metadata)
    return None, None


def _database_fields(thought: dict[str, Any], source_unit: Any) -> list[dict[str, Any]]:
    metadata = _database_metadata(thought, source_unit)
    fields: list[dict[str, Any]] = []
    for spec in THOUGHT_DATABASE_FIELDS:
        name = spec["name"]
        value, note = _database_field_value(
            field_name=name,
            thought=thought,
            source_unit=source_unit,
            metadata=metadata,
        )
        populated = value is not None and value != "" and value != [] and value != {}
        entry: dict[str, Any] = {
            "name": name,
            "description": spec.get("description"),
            "populated": populated,
        }
        if populated:
            entry["value"] = _sanitize_node(value, name)
        if note:
            entry["note"] = note
        fields.append(DatabaseFieldRecord.model_validate(entry).model_dump(mode="json", exclude_none=True))
    return fields


def _workflow_stage_aliases(stage_id: str) -> tuple[str, ...]:
    return (stage_id, *_RECIPE_STAGE_KEY_ALIASES.get(stage_id, ()))


def _workflow_payload_from_mapping(mapping: Any, stage_id: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in _workflow_stage_aliases(stage_id):
        if key in mapping:
            return mapping.get(key)
    return None


def _normalize_workflow_status(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in {"skip", "skipped", "inherited", "already_atomic"}:
        return "skipped"
    if text in {"complete", "completed", "done", "passed", "ok", "success", "succeeded", "split"}:
        return "complete"
    if text in {"fail", "failed", "error", "errored"}:
        return "failed"
    if text in {"current", "running", "started", "in_progress", "processing"}:
        return "current"
    if text in {"review", "needs_review", "review_required", "review_needed"}:
        return "needs_review"
    if text in {"pending", "not_run", "not_reached", "todo", "queued"}:
        return "not_reached"
    return text


def _workflow_status_entry_from_payload(payload: Any) -> dict[str, Any] | None:
    if payload in (None, "", [], {}):
        return None
    if isinstance(payload, str):
        status = _normalize_workflow_status(payload)
        return {"status": status} if status else None
    if not isinstance(payload, dict):
        return None
    status = _normalize_workflow_status(
        _first_present(payload.get("status"), payload.get("result"), payload.get("decision"), payload.get("outcome"))
    )
    entry = {
        key: payload.get(key)
        for key in (
            "label",
            "recipe",
            "method",
            "note",
            "reason",
            "skipped_reason",
            "created_count",
            "created_candidate_ids",
            "parent_candidate_id",
            "parent_lineage_id",
            "evidence_note",
        )
        if payload.get(key) not in (None, "", [], {})
    }
    if status:
        entry["status"] = status
    elif entry:
        entry["status"] = "complete"
    return entry or None


def _workflow_statuses(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_stage_detail = record.get("stage_detail")
    stage_detail: dict[str, Any] = raw_stage_detail if isinstance(raw_stage_detail, dict) else {}
    raw_metadata = record.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    sources = (
        record.get("workflow_statuses"),
        record.get("workflow_status"),
        record.get("recipe_statuses"),
        record.get("recipe_status"),
        record.get("recipe_workflow"),
        metadata.get("workflow_statuses"),
        metadata.get("workflow_status"),
        metadata.get("recipe_statuses"),
        metadata.get("recipe_status"),
        metadata.get("recipe_workflow"),
        stage_detail,
    )
    statuses: dict[str, dict[str, Any]] = {}
    for stage_id in RECIPE_STAGE_IDS:
        for source in sources:
            payload = _workflow_payload_from_mapping(source, stage_id)
            entry = _workflow_status_entry_from_payload(payload)
            if entry:
                statuses[stage_id] = entry
                break
    return statuses


def _archive_metadata_for(state: dict[str, Any], source_unit_id: str, lineage_id: str) -> dict[str, Any] | None:
    source_units = state.get("source_units")
    if not isinstance(source_units, dict):
        return None
    unit = source_units.get(source_unit_id)
    if not isinstance(unit, dict):
        return None
    thoughts = unit.get("thoughts")
    if not isinstance(thoughts, dict):
        return None
    meta = thoughts.get(lineage_id)
    return meta if isinstance(meta, dict) else None


def _safe_archive_text(value: Any) -> str | None:
    text = _clean_text_or_none(value)
    if not text:
        return None
    return _bound_source_text(_redact_sensitive_text(text), 500)


def _apply_archive_overlay(thought: Any, source_unit_id: str, archive_state: dict[str, Any]) -> dict[str, Any]:
    data = _model_dump(thought)
    lineage_id = str(data.get("lineage_id") or data.get("id") or "")
    meta = _archive_metadata_for(archive_state, source_unit_id, lineage_id)
    if not meta:
        data.setdefault("archived", False)
        return data
    data["archived"] = True
    data["archived_at"] = _safe_archive_text(meta.get("archived_at"))
    data["archived_by"] = _safe_archive_text(meta.get("archived_by")) or "dashboard"
    data["archive_reason"] = _safe_archive_text(meta.get("reason") or meta.get("archive_reason"))
    return data


def _card(thought: Any, source_unit_id: str) -> dict[str, Any]:
    t = _model_dump(thought)
    lineage_id = str(t.get("lineage_id") or t.get("id") or "")
    disposition = str(t.get("disposition") or "in_progress")
    card: dict[str, Any] = {
        "id": lineage_id,
        "source_unit_id": source_unit_id,
        "lineage_id": lineage_id,
        "candidate_id": t.get("candidate_id"),
        "title": t.get("title"),
        "summary": t.get("summary"),
        "current_stage": _normalize_stage(t.get("current_stage") or "extracted"),
        "disposition": disposition,
        "needs_review": t.get("needs_review"),
        "stop_code": t.get("stop_code"),
        "stop_target_id": t.get("stop_target_id"),
        "stop_target_label": t.get("stop_target_label"),
        "stop_stage_id": _normalize_stage(t.get("stop_stage_id")) if t.get("stop_stage_id") else None,
        "topics": t.get("topics") or [],
        "confidence": t.get("confidence"),
        "stopped_reason": t.get("stopped_reason"),
        "matched_memory_id": t.get("matched_memory_id"),
        "generation_technique": _generation_technique_from_record(t),
        "archived": bool(t.get("archived")),
        "archived_at": t.get("archived_at"),
        "archived_by": t.get("archived_by"),
        "archive_reason": t.get("archive_reason"),
    }
    stage_detail_raw = t.get("stage_detail")
    stage_detail: dict[str, Any] = stage_detail_raw if isinstance(stage_detail_raw, dict) else {}
    policy_detail_raw = stage_detail.get("policy")
    policy_detail: dict[str, Any] = policy_detail_raw if isinstance(policy_detail_raw, dict) else {}
    policy_result = policy_detail.get("result")
    if policy_result:
        card["policy_result"] = policy_result
    dedupe_detail_raw = stage_detail.get("deduped")
    dedupe_detail: dict[str, Any] = dedupe_detail_raw if isinstance(dedupe_detail_raw, dict) else {}
    dedupe_decision = dedupe_detail.get("decision")
    if dedupe_decision:
        card["dedupe_decision"] = dedupe_decision
    if dedupe_detail.get("similarity_score") is not None:
        card["dedupe_similarity_score"] = dedupe_detail.get("similarity_score")
    if dedupe_detail.get("nearest_similarity_score") is not None:
        card["dedupe_nearest_similarity_score"] = dedupe_detail.get("nearest_similarity_score")
    workflow_status = _workflow_statuses(t)
    if workflow_status:
        card["workflow_status"] = workflow_status
    cortexdb_id = _receipt_id(t)
    if cortexdb_id:
        card["cortexdb_id"] = cortexdb_id
    return ThoughtCard.model_validate(card).model_dump(mode="json", exclude_none=True)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _source_date_value(source_unit: Any) -> Any:
    su = _model_dump(source_unit)
    return _first_present(su.get("source_date"), su.get("occurred_at"), su.get("processed_at"))


def _source_unit_summary(source_unit: Any) -> dict[str, Any]:
    su = _model_dump(source_unit)
    source_date = _source_date_value(su)
    summary: dict[str, Any] = {
        "id": su.get("id"),
        "source_type": su.get("source_type"),
        "label": su.get("label"),
        "subtitle": su.get("subtitle"),
        "source_date": source_date,
        "occurred_at": su.get("occurred_at"),
        "processed_at": su.get("processed_at"),
        "source_ref": su.get("source_ref") or None,
    }
    return {key: value for key, value in summary.items() if value is not None}


def _blank_counts() -> dict[str, int]:
    counts: dict[str, int] = {
        "source_units": 0,
        "thoughts": 0,
        "imported": 0,
        "stopped": 0,
        "needs_review": 0,
        "in_progress": 0,
        "zero_thoughts": 0,
        "archived": 0,
    }
    for stage_id in CANONICAL_STAGES:
        counts[stage_id] = 0
    return counts


def _count_thought(counts: dict[str, int], thought: dict[str, Any]) -> None:
    counts["thoughts"] += 1
    if thought.get("archived") is True:
        counts["archived"] += 1
    stage_id = _normalize_stage(thought.get("current_stage") or "extracted")
    if stage_id in CANONICAL_STAGES:
        counts[stage_id] += 1
    disposition = str(thought.get("disposition") or "in_progress")
    if disposition == "imported":
        counts["imported"] += 1
    elif disposition == "stopped":
        counts["stopped"] += 1
    elif disposition == "needs_review":
        counts["needs_review"] += 1
    elif disposition == "in_progress":
        counts["in_progress"] += 1
    if thought.get("needs_review") is True and disposition != "needs_review":
        counts["needs_review"] += 1


def _counts_for_units(units: list[SourceUnit], archive_state: dict[str, Any] | None = None) -> dict[str, int]:
    counts = _blank_counts()
    counts["source_units"] = len(units)
    for source_unit in units:
        thoughts = [
            _apply_archive_overlay(thought, source_unit.id, archive_state)
            if archive_state is not None
            else _model_dump(thought)
            for thought in source_unit.thoughts
        ]
        if not thoughts:
            counts["zero_thoughts"] += 1
        for thought in thoughts:
            _count_thought(counts, thought)
    return counts


def _counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = _blank_counts()
    counts["source_units"] = len(rows)
    for row in rows:
        visible_cards = [card for cards in row["columns"].values() for card in cards]
        if not visible_cards and row.get("thought_count", 0) == 0:
            counts["zero_thoughts"] += 1
        for card in visible_cards:
            _count_thought(counts, card)
    return counts


def _matches_filter(thought: dict[str, Any], filter_value: str) -> bool:
    if filter_value == "all":
        return True
    disposition = str(thought.get("disposition") or "in_progress")
    if filter_value == "review_needed":
        filter_value = "needs_review"
    if filter_value == "needs_review":
        return disposition == "needs_review" or thought.get("needs_review") is True
    if filter_value in {"imported", "stopped", "in_progress"}:
        return disposition == filter_value
    return True


def _flatten_search_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten_search_values(item))
        return parts
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_flatten_search_values(item))
        return parts
    return []


def _search_field_value(value: dict[str, Any], field: str) -> Any:
    current: Any = value
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


_BOARD_CARD_SEARCH_FIELDS: tuple[str, ...] = (
    "id",
    "lineage_id",
    "candidate_id",
    "title",
    "summary",
    "current_stage",
    "disposition",
    "topics",
    "stopped_reason",
    "stop_code",
    "stop_target_id",
    "stop_target_label",
    "stop_stage_id",
    "matched_memory_id",
    "cortexdb_id",
    "generation_technique.id",
    "generation_technique.label",
    "generation_technique.kind",
    "generation_technique.short_label",
    "final_memory_text",
    "formation_trace.generation_technique.id",
    "formation_trace.generation_technique.label",
    "formation_trace.generation_technique.kind",
    "formation_trace.primary_lineage_ids",
    "formation_trace.output_title",
    "formation_trace.llm_output_text",
    "formation_trace.merge_note",
    "stage_detail.extracted.promotion_note",
    "stage_detail.enrich.note",
    "stage_detail.enrich.evidence_note",
    "workflow_status.enrich.status",
    "stage_detail.policy.reason",
    "stage_detail.policy.redacted_text",
    "stage_detail.deduped.merge_note",
    "stage_detail.deduped.evidence_note",
    "stage_detail.ready_for_cortexdb.final_memory_text",
    "stage_detail.cortexdb.stored_text",
)


def _matches_search(thought: dict[str, Any], query: str | None) -> bool:
    if not query or not query.strip():
        return True
    # Board search controls which *cards* are visible.  Source-unit labels,
    # dates, IDs, and other row metadata must not make every card in a matching
    # row visible for a non-empty query.
    haystack_parts: list[str] = []
    for field in _BOARD_CARD_SEARCH_FIELDS:
        haystack_parts.extend(_flatten_search_values(_search_field_value(thought, field)))
    haystack = "\n".join(haystack_parts).lower()
    return query.strip().lower() in haystack


def _stage_timeline(thought: dict[str, Any]) -> list[dict[str, Any]]:
    current_stage = _normalize_stage(thought.get("current_stage") or "extracted")
    stop_stage = _normalize_stage(thought.get("stop_stage_id") or current_stage)
    disposition = str(thought.get("disposition") or "in_progress")
    needs_review = thought.get("needs_review") is True or disposition == "needs_review"
    try:
        current_idx = CANONICAL_STAGES.index(stop_stage)
    except ValueError:
        current_idx = 0
    workflow_statuses = _workflow_statuses(thought)

    provided: dict[str, dict[str, Any]] = {}
    for raw_stage in thought.get("stages") or []:
        if not isinstance(raw_stage, dict):
            continue
        stage_id = _normalize_stage(raw_stage.get("id"))
        if stage_id in CANONICAL_STAGES:
            item = dict(raw_stage)
            item["id"] = stage_id
            item["label"] = _stage_label(stage_id)
            provided[stage_id] = item

    timeline: list[dict[str, Any]] = []
    for idx, stage_id in enumerate(CANONICAL_STAGES):
        if disposition == "imported":
            status: str = "complete"
        elif disposition == "stopped" and idx > current_idx:
            status = "not_reached"
        elif needs_review and idx == current_idx:
            status = "review_needed"
        elif idx < current_idx:
            status = "complete"
        elif idx == current_idx:
            status = "current"
        else:
            status = "pending"

        item = {"id": stage_id, "label": _stage_label(stage_id), "status": status}
        workflow_entry = workflow_statuses.get(stage_id)
        workflow_stage_status = workflow_entry.get("status") if workflow_entry else None
        if workflow_stage_status:
            timeline_status = "review_needed" if workflow_stage_status == "needs_review" else workflow_stage_status
            if timeline_status in {"complete", "current", "pending", "not_reached", "skipped", "failed", "review_needed"}:
                item["status"] = timeline_status
        if stage_id in provided:
            # Preserve producer-provided safe metadata, but keep canonical ID and
            # label.  For stopped thoughts, later stages must remain not_reached.
            merged = {**provided[stage_id], **item}
            if disposition != "stopped" or idx <= current_idx:
                merged["status"] = item["status"] if workflow_stage_status else provided[stage_id].get("status", item["status"])
            item = merged
        timeline.append(StageRecord.model_validate(item).model_dump(mode="json", exclude_none=True))
    return timeline


def _thought_detail(thought: ThoughtRecord, source_unit: SourceUnit) -> dict[str, Any]:
    t = thought.model_dump(mode="json", exclude_none=True)
    if t.get("disposition") != "imported":
        t.pop("cortexdb_receipt", None)
        t.pop("cortexdb_id", None)
    elif _receipt_id(t):
        t["cortexdb_id"] = _receipt_id(t)
    t["source_unit"] = _source_unit_summary(source_unit)
    t["stages"] = _stage_timeline(t)
    workflow_status = _workflow_statuses(t)
    if workflow_status:
        t["workflow_status"] = workflow_status
    t["database_fields"] = _database_fields(t, source_unit)
    return ThoughtDetail.model_validate(t).model_dump(mode="json", exclude_none=True)


def _default_source_type(snapshot: Snapshot) -> str:
    source_types = [source_type.id for source_type in snapshot.source_types]
    if "transcripts" in source_types:
        return "transcripts"
    if source_types:
        return source_types[0]
    unit_types = [source_unit.source_type for source_unit in snapshot.source_units]
    if "transcripts" in unit_types:
        return "transcripts"
    return unit_types[0] if unit_types else "transcripts"


def _source_types_payload(snapshot: Snapshot) -> list[dict[str, Any]]:
    if snapshot.source_types:
        return [source_type.model_dump(mode="json", exclude_none=True) for source_type in snapshot.source_types]

    counts: dict[str, int] = {}
    for source_unit in snapshot.source_units:
        counts[source_unit.source_type] = counts.get(source_unit.source_type, 0) + 1
    return [
        {"id": source_type, "label": source_type.replace("_", " ").title(), "count": count}
        for source_type, count in sorted(counts.items())
    ]


def _row_base(source_unit: SourceUnit) -> dict[str, Any]:
    thoughts = [_model_dump(thought) for thought in source_unit.thoughts]
    stopped_count = sum(1 for thought in thoughts if thought.get("disposition") == "stopped")
    row = {
        **_source_unit_summary(source_unit),
        "thought_count": len(thoughts),
        "stopped_count": stopped_count,
        "columns": _empty_columns(),
    }
    return row


def _sort_rows(rows: list[dict[str, Any]], sort_value: str) -> list[dict[str, Any]]:
    if sort_value == "default":
        return rows

    def timestamp(row: dict[str, Any]) -> float | None:
        dt = _parse_datetime(row.get("source_date") or row.get("occurred_at") or row.get("processed_at"))
        return dt.timestamp() if dt else None

    def timestamp_or(row: dict[str, Any], fallback: float) -> float:
        value = timestamp(row)
        return value if value is not None else fallback

    if sort_value == "newest":
        rows.sort(key=lambda row: timestamp_or(row, -math.inf), reverse=True)
    elif sort_value == "oldest":
        rows.sort(key=lambda row: timestamp_or(row, math.inf))
    elif sort_value == "most_thoughts":
        rows.sort(key=lambda row: row.get("thought_count", 0), reverse=True)
    elif sort_value == "most_stopped":
        rows.sort(key=lambda row: row.get("stopped_count", 0), reverse=True)
    return rows


def _parse_date_filter(value: str | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    us_date_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if us_date_match:
        month, day, year = (int(part) for part in us_date_match.groups())
        try:
            dt = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid source date filter: {value}") from exc
        return dt + timedelta(days=1) if end else dt
    is_date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
    dt = _parse_datetime(raw)
    if dt is None:
        raise HTTPException(status_code=400, detail=f"Invalid source date filter: {value}")
    if end and is_date_only:
        return dt + timedelta(days=1)
    return dt


def _matches_source_date_filter(
    source_unit: SourceUnit,
    *,
    start: datetime | None,
    end_exclusive: datetime | None,
) -> bool:
    if start is None and end_exclusive is None:
        return True
    source_dt = _parse_datetime(_source_date_value(source_unit))
    if source_dt is None:
        return False
    if start is not None and source_dt < start:
        return False
    if end_exclusive is not None and source_dt >= end_exclusive:
        return False
    return True


# ---------------------------------------------------------------------------
# Workflow monitor helpers
# ---------------------------------------------------------------------------

_WORKFLOW_POLL_SECONDS = 1.0


def _snapshot_revision() -> str:
    path = _snapshot_path()
    try:
        st = path.stat()
    except OSError:
        return "snapshot:0:0"
    return f"snapshot:{int(st.st_mtime_ns)}:{int(st.st_size)}"


def _workflow_dashboard_response(since_revision: str | None = None) -> dict[str, Any]:
    from hermes_cli.openbrain_workflow_artifacts import dashboard_response

    return dashboard_response(since_revision=since_revision)


def _workflow_detail_payload(workflow_run_id: str) -> dict[str, Any]:
    from hermes_cli.openbrain_workflow_artifacts import validate_workflow_run_id, workflow_detail

    try:
        validate_workflow_run_id(workflow_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workflow_run_id") from exc
    try:
        return workflow_detail(workflow_run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow run not found") from exc


def _workflow_events_payload(workflow_run_id: str, after: int = 0) -> dict[str, Any]:
    from hermes_cli.openbrain_workflow_artifacts import read_events, validate_workflow_run_id, workflow_revision

    try:
        validate_workflow_run_id(workflow_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workflow_run_id") from exc
    return {"workflow_run_id": workflow_run_id, "events": read_events(workflow_run_id, after=max(0, int(after))), "revision": workflow_revision()}


def _ws_upgrade_authorized(ws: WebSocket) -> bool:
    try:
        from hermes_cli import web_server as _ws
    except Exception:
        return True
    return bool(_ws._ws_auth_ok(ws))


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@router.get("/workflow-runs")
def workflow_runs(since_revision: str | None = None) -> dict[str, Any]:
    """Return sanitized OpenBrain workflow run summaries for the live monitor."""

    return _workflow_dashboard_response(since_revision=since_revision)


@router.get("/workflow-runs/{workflow_run_id}")
def workflow_run_detail(workflow_run_id: str) -> dict[str, Any]:
    return _workflow_detail_payload(workflow_run_id)


@router.get("/workflow-runs/{workflow_run_id}/events")
def workflow_run_events(workflow_run_id: str, after: int = 0) -> dict[str, Any]:
    return _workflow_events_payload(workflow_run_id, after=after)


@router.websocket("/workflow-events")
async def stream_workflow_events(ws: WebSocket):
    if not _ws_upgrade_authorized(ws):
        await ws.close(code=http_status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    since_revision = ws.query_params.get("revision") or ws.query_params.get("since_revision")
    try:
        while True:
            payload = await asyncio.to_thread(_workflow_dashboard_response, since_revision)
            if payload.get("changed"):
                since_revision = payload.get("revision")
                await ws.send_json({"type": "workflow_events", **payload})
            await asyncio.sleep(_WORKFLOW_POLL_SECONDS)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass


@router.get("/source-types")
def source_types() -> dict[str, Any]:
    snapshot = _load_snapshot()
    source_types_payload = _source_types_payload(snapshot)
    return {
        "default_source_type": _default_source_type(snapshot),
        "source_types": source_types_payload,
        "generated_at": snapshot.generated_at,
        "ingestion_run_id": snapshot.ingestion_run_id,
        "policy_stop_definitions": list(POLICY_STOP_DEFINITIONS),
    }


@router.get("/board")
def board(
    source_type: str | None = None,
    filter_value: str | None = Query(default=None, alias="filter"),
    sort: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    snapshot = _load_snapshot()
    archive_state = _load_archive_state()
    selected_source_type = source_type or _default_source_type(snapshot)

    normalized_filter = (filter_value or "all").strip().lower()
    normalized_sort = (sort or "default").strip().lower()
    if normalized_filter not in _ALLOWED_FILTERS:
        raise HTTPException(status_code=400, detail=f"Unknown filter: {filter_value}")
    if normalized_sort not in _ALLOWED_SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")
    source_date_start = _parse_date_filter(date_from)
    source_date_end = _parse_date_filter(date_to, end=True)
    if source_date_start is not None and source_date_end is not None and source_date_start >= source_date_end:
        raise HTTPException(status_code=400, detail="Source date from must be before or equal to source date to")

    units = [unit for unit in snapshot.source_units if unit.source_type == selected_source_type]
    total_counts = _counts_for_units(units, archive_state)

    rows: list[dict[str, Any]] = []
    search_query = search.strip() if isinstance(search, str) else None
    for source_unit in units:
        if not _matches_source_date_filter(
            source_unit,
            start=source_date_start,
            end_exclusive=source_date_end,
        ):
            continue
        all_thoughts = list(source_unit.thoughts)
        row = _row_base(source_unit)

        if normalized_filter == "zero_thoughts":
            if all_thoughts:
                continue
            # With no cards to search, zero-thought rows only match an empty
            # query.  This keeps search/filter behavior card-level.
            if search_query:
                continue
            rows.append(row)
            continue

        visible_thoughts: list[dict[str, Any]] = []
        for thought in all_thoughts:
            thought_dict = _apply_archive_overlay(thought, source_unit.id, archive_state)
            if thought_dict.get("archived") is True and not include_archived:
                continue
            if not _matches_filter(thought_dict, normalized_filter):
                continue
            if not _matches_search(thought_dict, search_query):
                continue
            visible_thoughts.append(thought_dict)

        if not visible_thoughts:
            if all_thoughts or normalized_filter != "all" or search_query:
                continue
            rows.append(row)
            continue

        for thought in visible_thoughts:
            card = _card(thought, source_unit.id)
            stage_id = card["current_stage"]
            row["columns"].setdefault(stage_id, []).append(card)
        rows.append(row)

    rows = _sort_rows(rows, normalized_sort)
    visible_counts = _counts_for_rows(rows)
    columns = [{"id": stage_id, "label": _stage_label(stage_id)} for stage_id in CANONICAL_STAGES]

    return {
        "schema_version": snapshot.schema_version,
        "generated_at": snapshot.generated_at,
        "ingestion_run_id": snapshot.ingestion_run_id,
        "revision": _snapshot_revision(),
        "source_type": selected_source_type,
        "columns": columns,
        "rows": rows,
        "total_counts": total_counts,
        "visible_counts": visible_counts,
        "metrics": total_counts,
        "policy_stop_definitions": list(POLICY_STOP_DEFINITIONS),
        "filter": normalized_filter,
        "sort": normalized_sort,
        "search": search_query or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "include_archived": include_archived,
    }


class ArchiveThoughtBody(BaseModel):
    reason: str | None = None


class BulkArchiveThoughtItem(BaseModel):
    source_unit_id: str
    lineage_id: str


class BulkArchiveThoughtBody(BaseModel):
    archived: bool
    items: list[BulkArchiveThoughtItem] = Field(default_factory=list)
    reason: str | None = None


def _find_thought(snapshot: Snapshot, source_unit_id: str, lineage_id: str) -> tuple[SourceUnit, ThoughtRecord]:
    for source_unit in snapshot.source_units:
        if source_unit.id != source_unit_id:
            continue
        for thought in source_unit.thoughts:
            if thought.lineage_id == lineage_id:
                return source_unit, thought
        raise HTTPException(status_code=404, detail="Thought not found")
    raise HTTPException(status_code=404, detail="Source unit not found")


def _set_archived_entries(
    entries: list[tuple[str, str]],
    *,
    archived: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    state = _load_archive_state()
    now = _utc_now_iso()
    state["updated_at"] = now
    source_units = state.setdefault("source_units", {})
    for source_unit_id, lineage_id in entries:
        unit = source_units.setdefault(source_unit_id, {"thoughts": {}})
        thoughts = unit.setdefault("thoughts", {})
        if archived:
            thoughts[lineage_id] = {
                "source_unit_id": source_unit_id,
                "lineage_id": lineage_id,
                "archived_at": now,
                "archived_by": "dashboard",
                "reason": _safe_archive_text(reason) or "manual",
            }
        else:
            thoughts.pop(lineage_id, None)
            if not thoughts:
                source_units.pop(source_unit_id, None)
    _write_archive_state_atomic(state)
    return state


def _set_archived_state(
    source_unit_id: str,
    lineage_id: str,
    *,
    archived: bool,
    reason: str | None = None,
) -> dict[str, Any] | None:
    state = _set_archived_entries([(source_unit_id, lineage_id)], archived=archived, reason=reason)
    return _archive_metadata_for(state, source_unit_id, lineage_id)


@router.post("/thoughts/archive")
def bulk_archive_thoughts(payload: BulkArchiveThoughtBody) -> dict[str, Any]:
    if not payload.items:
        raise HTTPException(status_code=400, detail="No thoughts selected")

    snapshot = _load_snapshot()
    found: list[tuple[SourceUnit, ThoughtRecord]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload.items:
        key = (item.source_unit_id, item.lineage_id)
        if key in seen:
            continue
        seen.add(key)
        found.append(_find_thought(snapshot, item.source_unit_id, item.lineage_id))

    _set_archived_entries(list(seen), archived=payload.archived, reason=payload.reason)
    archive_state = _load_archive_state()
    thoughts = []
    for source_unit, thought in found:
        overlaid = ThoughtRecord.model_validate(_apply_archive_overlay(thought, source_unit.id, archive_state))
        thoughts.append(_thought_detail(overlaid, source_unit))
    return {"archived": payload.archived, "count": len(thoughts), "thoughts": thoughts}


@router.post("/source-units/{source_unit_id}/thoughts/{lineage_id}/archive")
def archive_thought(
    source_unit_id: str,
    lineage_id: str,
    payload: ArchiveThoughtBody | None = None,
) -> dict[str, Any]:
    snapshot = _load_snapshot()
    source_unit, thought = _find_thought(snapshot, source_unit_id, lineage_id)
    _set_archived_state(source_unit_id, lineage_id, archived=True, reason=(payload.reason if payload else None))
    archive_state = _load_archive_state()
    overlaid = ThoughtRecord.model_validate(_apply_archive_overlay(thought, source_unit.id, archive_state))
    return {"thought": _thought_detail(overlaid, source_unit)}


@router.post("/source-units/{source_unit_id}/thoughts/{lineage_id}/unarchive")
def unarchive_thought(source_unit_id: str, lineage_id: str) -> dict[str, Any]:
    snapshot = _load_snapshot()
    source_unit, thought = _find_thought(snapshot, source_unit_id, lineage_id)
    _set_archived_state(source_unit_id, lineage_id, archived=False)
    archive_state = _load_archive_state()
    overlaid = ThoughtRecord.model_validate(_apply_archive_overlay(thought, source_unit.id, archive_state))
    return {"thought": _thought_detail(overlaid, source_unit)}


@router.get("/source-units/{source_unit_id}/thoughts/{lineage_id}")
def thought_detail(source_unit_id: str, lineage_id: str) -> dict[str, Any]:
    snapshot = _load_snapshot()
    source_unit, thought = _find_thought(snapshot, source_unit_id, lineage_id)
    archive_state = _load_archive_state()
    overlaid = ThoughtRecord.model_validate(_apply_archive_overlay(thought, source_unit.id, archive_state))
    return {"thought": _thought_detail(overlaid, source_unit)}
