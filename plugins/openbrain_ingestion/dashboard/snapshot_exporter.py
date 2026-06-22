"""Panning-for-Gold run exporter for the OpenBrain ingestion dashboard.

This module converts a Panning-for-Gold run directory into the sanitized
snapshot consumed by :mod:`plugin_api`.  It intentionally delegates schema
normalization and privacy cleanup to the dashboard backend models/helpers so the
exporter does not drift from the API contract.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:  # Prefer normal package imports when Hermes loads the plugin as a package.
    from .plugin_api import CANONICAL_STAGES, ProducerInfo, Snapshot, _sanitize_snapshot
except Exception:  # pragma: no cover - exercised when loaded directly by tests/tools.
    _PLUGIN_API_PATH = Path(__file__).with_name("plugin_api.py")
    _spec = importlib.util.spec_from_file_location(
        "openbrain_ingestion_dashboard_plugin_api_for_exporter", _PLUGIN_API_PATH
    )
    if _spec is None or _spec.loader is None:  # pragma: no cover - defensive import guard.
        raise ImportError(f"Unable to load dashboard plugin_api from {_PLUGIN_API_PATH}")
    _plugin_api = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _plugin_api
    _spec.loader.exec_module(_plugin_api)
    CANONICAL_STAGES = _plugin_api.CANONICAL_STAGES
    ProducerInfo = _plugin_api.ProducerInfo
    Snapshot = _plugin_api.Snapshot
    _sanitize_snapshot = _plugin_api._sanitize_snapshot

try:  # Added in some dashboard API versions; keep this exporter compatible if absent.
    from . import plugin_api as _plugin_api_optional
except Exception:  # pragma: no cover - direct-file import path uses the fallback module above.
    _plugin_api_optional = globals().get("_plugin_api")
_api_normalize_thought_for_response = getattr(
    _plugin_api_optional, "_normalize_thought_for_response", None
)

KNOWN_ARTIFACTS: tuple[str, ...] = (
    "source-items.jsonl",
    "inventory.jsonl",
    "dedupe-receipts.jsonl",
    "capture-candidates.jsonl",
    "capture-audit.jsonl",
    "summary.json",
    "panning-run.sqlite",
)
OPTIONAL_JSONL_ARTIFACTS: tuple[str, ...] = (
    "inventory.jsonl",
    "dedupe-receipts.jsonl",
    "capture-candidates.jsonl",
    "capture-audit.jsonl",
)

_MATERIAL_SOURCE_TYPES_BY_ADAPTER: dict[str, str] = {
    "otter_package": "transcripts",
    "otter-package": "transcripts",
}

_IMPORTED_ACTIONS = {
    "capture",
    "captured",
    "create",
    "created",
    "import",
    "imported",
    "insert",
    "inserted",
    "success",
    "succeeded",
}
_DUPLICATE_DECISIONS = {
    "duplicate",
    "duplicate_exact",
    "exact_duplicate",
    "merged",
    "merged_exact",
    "merge_exact",
    "merged-exact",
}
_PRIVATE_URL_SCHEMES = {"file"}
_PATH_KEYS = (
    "path",
    "source_path",
    "absolute_path",
    "local_path",
    "raw_path",
    "file_path",
    "windows_path",
)
_DISPLAY_KEYS = ("display_path", "relative_path", "display_name", "filename")
_TITLE_KEYS = ("title", "name", "label", "display_title", "conversation_title", "meeting_title")
_OCCURRENCE_KEYS = ("occurred_at", "recorded_at", "meeting_started_at", "created_at", "started_at", "date")
_PROCESSED_KEYS = ("processed_at", "completed_at", "updated_at", "ingested_at")
_CANDIDATE_STAGE_ALIASES: dict[str, str] = {
    "raw_extraction": "extracted",
    "ready": "ready_for_cortexdb",
    "ready_to_import": "ready_for_cortexdb",
    "candidate": "ready_for_cortexdb",
    "capture_candidate": "ready_for_cortexdb",
}


def export_dashboard_snapshot(
    *,
    run_root: Path,
    source_type: str,
    adapter: str,
    out_path: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build and atomically write a dashboard snapshot for a Panning run."""

    snapshot = build_snapshot_from_panning_run(
        run_root=run_root,
        source_type=source_type,
        adapter=adapter,
        generated_at=generated_at,
    )
    write_snapshot_atomic(out_path, snapshot)
    return snapshot


def build_snapshot_from_panning_run(
    *,
    run_root: Path,
    source_type: str,
    adapter: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a sanitized dashboard snapshot from a Panning-for-Gold run.

    ``source_type`` is the dashboard material lane (for example,
    ``"transcripts"``), not necessarily the adapter's raw source item type
    (for example, ``"otter-package"``).  For known adapters this function also
    accepts the adapter/raw type and maps it to the dashboard lane.
    """

    run_root = Path(run_root)
    source_items_path = run_root / "source-items.jsonl"
    if not source_items_path.exists():
        raise FileNotFoundError("Required Panning artifact missing: source-items.jsonl")

    summary_path = run_root / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError("Required Panning artifact missing: summary.json")

    source_items = _read_jsonl(source_items_path, required=True)
    summary = _read_json(summary_path)
    inventory = _read_jsonl(run_root / "inventory.jsonl")
    dedupe_receipts = _read_jsonl(run_root / "dedupe-receipts.jsonl")
    capture_candidates = _read_jsonl(run_root / "capture-candidates.jsonl")
    capture_audit = _read_jsonl(run_root / "capture-audit.jsonl")

    material_source_type = _material_source_type(source_type, adapter)
    generated_at_value = _coerce_timestamp(
        generated_at
        or _dig(summary, "generated_at")
        or _dig(summary, "completed_at")
        or _dig(summary, "finished_at")
        or _dig(summary, "created_at")
        or _utc_now()
    ) or _utc_now()
    ingestion_run_id = str(
        _dig(summary, "ingestion_run_id")
        or _dig(summary, "run_id")
        or _dig(summary, "id")
        or run_root.name
    )

    source_units_by_source_id = _build_source_units(
        source_items=source_items,
        material_source_type=material_source_type,
        adapter=adapter,
        generated_at=generated_at_value,
    )

    inventory_index = _RecordIndex(inventory)
    dedupe_index = _RecordIndex(dedupe_receipts)
    audit_index = _RecordIndex(capture_audit)
    capture_candidate_index = _RecordIndex(capture_candidates)

    for candidate in capture_candidates:
        source_id = _candidate_source_id(candidate, inventory_index, dedupe_index, audit_index)
        if source_id is None:
            continue
        source_unit = source_units_by_source_id.get(source_id)
        if source_unit is None:
            # The dashboard is source-unit scoped.  If an orphaned candidate
            # appears in optional artifacts, skip it instead of inventing a
            # source unit not present in the required source-items artifact.
            continue

        thought = _thought_from_candidate(
            candidate=candidate,
            source_id=source_id,
            source_unit_id=str(source_unit["id"]),
            inventory_index=inventory_index,
            dedupe_index=dedupe_index,
            audit_index=audit_index,
            ingestion_run_id=ingestion_run_id,
            generated_at=generated_at_value,
        )
        if thought:
            source_unit.setdefault("thoughts", []).append(thought)

    for record in inventory:
        source_id = _string_or_none(_dig(record, "source_id"))
        if not source_id:
            continue
        source_unit = source_units_by_source_id.get(source_id)
        if source_unit is None:
            continue
        candidate_id = _string_or_none(_dig(record, "candidate_id") or _dig(record, "id"))
        fingerprint = _string_or_none(_dig(record, "content_fingerprint") or _dig(record, "fingerprint"))
        if capture_candidate_index.find(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint):
            continue
        thought = _stopped_thought_from_inventory(
            record=record,
            source_id=source_id,
            source_unit_id=str(source_unit["id"]),
            ingestion_run_id=ingestion_run_id,
            generated_at=generated_at_value,
        )
        if thought:
            source_unit.setdefault("thoughts", []).append(thought)

    source_units = list(source_units_by_source_id.values())
    producer = ProducerInfo.model_validate(
        {
            "kind": "panning_for_gold",
            "adapter": adapter,
            "source_type": material_source_type,
            "run_root_label": str(run_root),
            "artifacts": [str(run_root / name) for name in _existing_artifact_names(run_root)],
        }
    ).model_dump(mode="json", exclude_none=True)

    snapshot = {
        "schema_version": 1,
        "generated_at": generated_at_value,
        "ingestion_run_id": ingestion_run_id,
        "producer": producer,
        "source_types": [
            {
                "id": material_source_type,
                "label": _source_type_label(material_source_type),
                "count": len(source_units),
            }
        ],
        "source_units": source_units,
    }
    return _validate_and_sanitize_snapshot(snapshot)


def write_snapshot_atomic(out_path: Path, snapshot: dict[str, Any]) -> None:
    """Validate, sanitize, and atomically write ``snapshot`` to ``out_path``.

    Validation/sanitization happens before any output file is opened so an
    invalid snapshot cannot truncate or replace a previous good snapshot.
    """

    clean_snapshot = _validate_and_sanitize_snapshot(snapshot)
    payload = json.dumps(clean_snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    out_path = Path(out_path)
    parent = out_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_path = parent / f".{out_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"

    try:
        with tmp_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
        _fsync_parent_best_effort(parent)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_and_sanitize_snapshot(snapshot: Any) -> dict[str, Any]:
    clean = _sanitize_snapshot(snapshot)
    validated = Snapshot.model_validate(clean)
    return validated.model_dump(mode="json", exclude_none=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path.name}")
    return payload


def _read_jsonl(path: Path, *, required: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required Panning artifact missing: {path.name}")
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path.name}:{line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"Expected JSON object in {path.name}:{line_number}")
            rows.append(payload)
    return rows


def _existing_artifact_names(run_root: Path) -> list[str]:
    return [name for name in KNOWN_ARTIFACTS if (run_root / name).exists()]


def _material_source_type(source_type: str, adapter: str) -> str:
    raw = str(source_type or "").strip() or "transcripts"
    adapter_key = str(adapter or "").strip().lower()
    raw_key = raw.lower()
    if raw_key in {"otter-package", "otter_package"}:
        return _MATERIAL_SOURCE_TYPES_BY_ADAPTER.get(adapter_key, "transcripts")
    return _MATERIAL_SOURCE_TYPES_BY_ADAPTER.get(raw_key, raw)


def _build_source_units(
    *,
    source_items: Iterable[dict[str, Any]],
    material_source_type: str,
    adapter: str,
    generated_at: str,
) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for item in source_items:
        source_id_value = _dig(item, "source_id") or _dig(item, "id") or _dig(item, "adapter_source_id")
        if source_id_value is None:
            continue
        source_id = str(source_id_value)
        source_unit_id = _source_unit_id(material_source_type, source_id)
        unit = {
            "id": source_unit_id,
            "source_type": material_source_type,
            "label": _source_label(item, source_id),
            "subtitle": _source_subtitle(item, adapter),
            "source_ref": _source_ref(item, adapter, source_unit_id, source_id),
            "occurred_at": _first_timestamp(item, _OCCURRENCE_KEYS),
            "processed_at": _first_timestamp(item, _PROCESSED_KEYS) or generated_at,
            "adapter": adapter,
            "adapter_source_id": source_id,
            "panning_source_type": _string_or_none(_dig(item, "source_type")),
            "thoughts": [],
        }
        units[source_id] = {key: value for key, value in unit.items() if value not in (None, {}, []) or key == "thoughts"}
    return units


def _source_unit_id(material_source_type: str, source_id: str) -> str:
    source_type_slug = _slug(material_source_type) or "source"
    source_slug = _slug(_basenameish(source_id) or source_id) or "unit"
    digest = hashlib.sha256(f"{material_source_type}\0{source_id}".encode("utf-8")).hexdigest()[:12]
    return f"{source_type_slug}-{source_slug[:48]}-{digest}"


def _source_label(item: dict[str, Any], source_id: str) -> str:
    for key in _TITLE_KEYS:
        value = _string_or_none(_dig(item, key))
        if value:
            return value
    return _basenameish(source_id) or source_id


def _source_subtitle(item: dict[str, Any], adapter: str) -> str | None:
    explicit = _string_or_none(_dig(item, "subtitle"))
    if explicit:
        return explicit
    duration_minutes = _dig(item, "duration_minutes")
    if duration_minutes is None:
        duration_seconds = _dig(item, "duration_seconds") or _dig(item, "duration")
        if isinstance(duration_seconds, (int, float)) and not isinstance(duration_seconds, bool):
            duration_minutes = max(1, round(float(duration_seconds) / 60))
    if duration_minutes is not None:
        try:
            minutes = int(duration_minutes)
        except (TypeError, ValueError):
            minutes = None
        if minutes:
            prefix = "Otter transcript" if str(adapter).lower() in {"otter_package", "otter-package"} else "Source item"
            return f"{prefix} · {minutes} min"
    return None


def _source_ref(item: dict[str, Any], adapter: str, source_unit_id: str, source_id: str) -> dict[str, Any]:
    display_path = _safe_display_path(item) or _basenameish(source_id)
    ref = {
        "kind": _source_ref_kind(adapter),
        "source_unit_id": source_unit_id,
    }
    if display_path:
        ref["display_path"] = display_path
    return ref


def _source_ref_kind(adapter: str) -> str:
    adapter_key = str(adapter or "").strip().lower().replace("-", "_")
    if adapter_key == "otter_package":
        return "otter_transcript"
    return adapter_key or "panning_source"


def _safe_display_path(item: dict[str, Any]) -> str | None:
    for key in _DISPLAY_KEYS:
        value = _string_or_none(_dig(item, key))
        if value and not _looks_like_private_reference(value) and not _is_url(value):
            return value
    for key in _PATH_KEYS:
        value = _string_or_none(_dig(item, key))
        if value:
            base = _basenameish(value)
            if base:
                return base
    return None


def _candidate_source_id(
    candidate: dict[str, Any],
    inventory_index: "_RecordIndex",
    dedupe_index: "_RecordIndex",
    audit_index: "_RecordIndex",
) -> str | None:
    source_id = _string_or_none(_dig(candidate, "source_id")) or _string_or_none(_dig(candidate, "adapter_source_id"))
    if source_id:
        return source_id
    candidate_id = _string_or_none(_dig(candidate, "candidate_id") or _dig(candidate, "id"))
    fingerprint = _string_or_none(_dig(candidate, "content_fingerprint") or _dig(candidate, "fingerprint"))
    for index in (inventory_index, dedupe_index, audit_index):
        record = index.find(source_id=None, candidate_id=candidate_id, fingerprint=fingerprint)
        found = _string_or_none(_dig(record or {}, "source_id"))
        if found:
            return found
    return None


def _thought_from_candidate(
    *,
    candidate: dict[str, Any],
    source_id: str,
    source_unit_id: str,
    inventory_index: "_RecordIndex",
    dedupe_index: "_RecordIndex",
    audit_index: "_RecordIndex",
    ingestion_run_id: str,
    generated_at: str,
) -> dict[str, Any] | None:
    candidate_id = _string_or_none(_dig(candidate, "candidate_id") or _dig(candidate, "id"))
    fingerprint = _string_or_none(_dig(candidate, "content_fingerprint") or _dig(candidate, "fingerprint"))
    inventory = inventory_index.find(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint) or {}
    if fingerprint is None:
        fingerprint = _string_or_none(_dig(inventory, "content_fingerprint") or _dig(inventory, "fingerprint"))

    audit = audit_index.find(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint) or {}
    dedupe = dedupe_index.find(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint) or {}

    lineage_id = _lineage_id(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint, inventory=inventory)
    imported = _is_imported_audit(audit)
    duplicate = _is_duplicate_dedupe(dedupe)
    base_stage = _candidate_stage(candidate)

    if imported:
        current_stage = "cortexdb"
        disposition = "imported"
    elif duplicate:
        current_stage = "deduped"
        disposition = "stopped"
    else:
        current_stage = base_stage
        disposition = "needs_review" if _candidate_needs_review(candidate) else "in_progress"

    thought: dict[str, Any] = {
        "id": lineage_id,
        "lineage_id": lineage_id,
        "candidate_id": candidate_id,
        "title": _candidate_title(candidate),
        "summary": _candidate_summary(candidate),
        "current_stage": current_stage,
        "disposition": disposition,
        "needs_review": _candidate_needs_review(candidate),
        "topics": _list_of_strings(_dig(candidate, "topics") or _dig(candidate, "tags")),
        "confidence": _number_or_none(_dig(candidate, "confidence") or _dig(candidate, "score")),
        "source_snippet": _string_or_none(
            _dig(candidate, "source_snippet")
            or _dig(candidate, "source_excerpt")
            or _dig(candidate, "quote")
            or _dig(candidate, "excerpt")
        ),
        "final_memory_text": _string_or_none(
            _dig(candidate, "final_memory_text")
            or _dig(candidate, "memory_text")
            or _dig(candidate, "thought_text")
            or _dig(candidate, "content")
            or _dig(candidate, "text")
        ),
        "content_fingerprint": fingerprint,
    }

    if duplicate:
        related = _related_memories(dedupe)
        if related:
            thought["related_memories"] = related
            if related[0].get("id"):
                thought["matched_memory_id"] = related[0]["id"]
                thought["stop_target_id"] = related[0]["id"]
            if related[0].get("title"):
                thought["stop_target_label"] = related[0]["title"]
        stopped_reason = _string_or_none(_dig(dedupe, "reason") or _dig(dedupe, "stopped_reason"))
        thought["stopped_reason"] = stopped_reason or "merged_exact duplicate"
        thought["stop_code"] = "duplicate"
        thought["stop_stage_id"] = "deduped"

    if imported:
        receipt = _receipt_from_audit(
            audit=audit,
            candidate=candidate,
            ingestion_run_id=ingestion_run_id,
            source_unit_id=source_unit_id,
            candidate_id=candidate_id,
            generated_at=generated_at,
        )
        if receipt:
            thought["cortexdb_receipt"] = receipt
            thought["cortexdb_id"] = receipt.get("id")
        related = _related_memories(audit) or _related_memories(dedupe)
        if related:
            thought["related_memories"] = related

    thought = {key: value for key, value in thought.items() if value is not None and value != []}
    return _normalize_thought(thought)


def _inventory_is_stopped(record: dict[str, Any]) -> bool:
    return str(_dig(record, "final_capture_action") or "").strip().lower() == "not_applicable"


def _inventory_stop_metadata(record: dict[str, Any]) -> tuple[str, str]:
    parts = [
        _string_or_none(_dig(record, "verdict")),
        _string_or_none(_dig(record, "metadata", "historical_archive", "historical_verdict")),
        _string_or_none(_dig(record, "metadata", "historical_archive", "historical_memory_type")),
        _string_or_none(_dig(record, "dedupe", "decision")),
        _string_or_none(_dig(record, "dedupe", "capture_action")),
        _string_or_none(_dig(record, "capture_content")),
    ]
    normalized = " ".join(part for part in parts if part).lower().replace("-", "_").replace(" ", "_")
    if "obsolete" in normalized:
        return "obsolete", "policy"
    if "reference_or_merge" in normalized or "reference_merge" in normalized or "reference / merge" in normalized or "reference" in normalized:
        return "reference_merge", "policy"
    if "duplicate" in normalized or "merged" in normalized:
        return "duplicate", "deduped"
    if "policy" in normalized:
        return "policy", "policy"
    if "non_thought" in normalized or "not_a_thought" in normalized:
        return "non_thought", "extracted"
    return "other", "deduped"


def _stopped_thought_from_inventory(
    *,
    record: dict[str, Any],
    source_id: str,
    source_unit_id: str,
    ingestion_run_id: str,
    generated_at: str,
) -> dict[str, Any] | None:
    candidate_id = _string_or_none(_dig(record, "candidate_id") or _dig(record, "id"))
    fingerprint = _string_or_none(_dig(record, "content_fingerprint") or _dig(record, "fingerprint"))
    lineage_id = _lineage_id(source_id=source_id, candidate_id=candidate_id, fingerprint=fingerprint, inventory=record)
    stop_code, current_stage = _inventory_stop_metadata(record)

    related = _related_memories(record)
    stop_target_id = None
    stop_target_label = None
    if related:
        stop_target_id = _string_or_none(related[0].get("id"))
        stop_target_label = _string_or_none(related[0].get("title"))

    thought: dict[str, Any] = {
        "id": lineage_id,
        "lineage_id": lineage_id,
        "candidate_id": candidate_id,
        "title": _string_or_none(_dig(record, "title") or _dig(record, "idea") or _dig(record, "capture_content")),
        "summary": _string_or_none(_dig(record, "reason") or _dig(record, "capture_content")),
        "current_stage": current_stage,
        "stop_stage_id": current_stage,
        "disposition": "stopped",
        "stop_code": stop_code,
        "stop_target_id": stop_target_id,
        "stop_target_label": stop_target_label,
        "needs_review": False,
        "topics": _list_of_strings(_dig(record, "topics") or _dig(record, "connections")),
        "confidence": _number_or_none(_dig(record, "confidence") or _dig(record, "score")),
        "source_snippet": _string_or_none(
            _dig(record, "evidence_quote")
            or _dig(record, "source_snippet")
            or _dig(record, "capture_content")
        ),
        "final_memory_text": _string_or_none(
            _dig(record, "final_memory_text") or _dig(record, "idea") or _dig(record, "title")
        ),
        "stopped_reason": _string_or_none(_dig(record, "reason") or _dig(record, "capture_content")),
        "matched_memory_id": stop_target_id,
        "content_fingerprint": fingerprint,
    }
    if not _inventory_is_stopped(record):
        return None
    thought = {key: value for key, value in thought.items() if value is not None and value != []}
    return _normalize_thought(thought)


def _normalize_thought(thought: dict[str, Any]) -> dict[str, Any]:
    if callable(_api_normalize_thought_for_response):
        normalized = _api_normalize_thought_for_response(thought)
        if isinstance(normalized, dict):
            return normalized
    return thought


def _lineage_id(
    *,
    source_id: str,
    candidate_id: str | None,
    fingerprint: str | None,
    inventory: dict[str, Any],
) -> str:
    thread_id = _string_or_none(
        _dig(inventory, "thread_id")
        or _dig(inventory, "lineage_id")
        or _dig(inventory, "conversation_id")
        or _dig(inventory, "thread", "id")
    )
    if thread_id:
        return thread_id
    digest = hashlib.sha256(
        f"{source_id}\0{candidate_id or ''}\0{fingerprint or ''}".encode("utf-8")
    ).hexdigest()[:16]
    return f"lineage-{digest}"


def _candidate_stage(candidate: dict[str, Any]) -> str:
    field_name: str | None = None
    raw_stage: str | None = None
    for key in ("current_stage", "stage", "status"):
        value = _string_or_none(_dig(candidate, key))
        if value:
            field_name = key
            raw_stage = value
            break

    if raw_stage is None:
        return "ready_for_cortexdb"

    stage = raw_stage.strip().lower().replace("-", "_").replace(" ", "_")
    stage = _CANDIDATE_STAGE_ALIASES.get(stage, stage)
    if stage in CANONICAL_STAGES:
        return stage
    allowed = ", ".join((*CANONICAL_STAGES, *sorted(_CANDIDATE_STAGE_ALIASES)))
    raise ValueError(
        f"Unknown candidate stage {raw_stage!r} in {field_name or 'candidate'}; "
        f"expected one of: {allowed}"
    )


def _candidate_needs_review(candidate: dict[str, Any]) -> bool:
    value = _dig(candidate, "needs_review")
    if isinstance(value, bool):
        return value
    status = str(_dig(candidate, "review_status") or _dig(candidate, "status") or "").strip().lower()
    return status in {"needs_review", "review", "manual_review", "review_needed"}


def _candidate_title(candidate: dict[str, Any]) -> str | None:
    for path in (
        ("title",),
        ("thought", "title"),
        ("memory", "title"),
        ("proposal", "title"),
    ):
        value = _string_or_none(_dig(candidate, *path))
        if value:
            return value
    return None


def _candidate_summary(candidate: dict[str, Any]) -> str | None:
    for path in (
        ("summary",),
        ("thought", "summary"),
        ("memory", "summary"),
        ("rationale",),
    ):
        value = _string_or_none(_dig(candidate, *path))
        if value:
            return value
    return None


def _receipt_from_audit(
    *,
    audit: dict[str, Any],
    candidate: dict[str, Any],
    ingestion_run_id: str,
    source_unit_id: str,
    candidate_id: str | None,
    generated_at: str,
) -> dict[str, Any] | None:
    thought_id = _string_or_none(
        _dig(audit, "thought_id")
        or _dig(audit, "memory_id")
        or _dig(audit, "cortexdb_id")
        or _dig(audit, "receipt", "id")
        or _dig(audit, "result", "thought_id")
        or _dig(audit, "thought", "id")
    )
    if not thought_id:
        return None
    return {
        "id": thought_id,
        "type": _string_or_none(
            _dig(audit, "type")
            or _dig(audit, "thought_type")
            or _dig(audit, "receipt", "type")
            or _dig(candidate, "type")
            or _dig(candidate, "thought_type")
        )
        or "observation",
        "captured_at": _coerce_timestamp(
            _dig(audit, "captured_at")
            or _dig(audit, "created_at")
            or _dig(audit, "timestamp")
            or generated_at
        ),
        "ingestion_run_id": ingestion_run_id,
        "source_unit_id": source_unit_id,
        "candidate_id": candidate_id,
    }


def _is_imported_audit(audit: dict[str, Any]) -> bool:
    if not audit:
        return False
    if _dig(audit, "thought_id") or _dig(audit, "receipt", "id") or _dig(audit, "result", "thought_id"):
        status_text = " ".join(
            str(_dig(audit, key) or "") for key in ("action", "event", "status", "outcome", "operation")
        ).lower()
        return not any(token in status_text for token in ("fail", "error", "reject", "skip"))
    status_parts = [
        _string_or_none(_dig(audit, key))
        for key in ("action", "event", "status", "outcome", "operation")
    ]
    return any(str(part).strip().lower() in _IMPORTED_ACTIONS for part in status_parts if part)


def _is_duplicate_dedupe(dedupe: dict[str, Any]) -> bool:
    if not dedupe:
        return False
    parts = [
        _string_or_none(_dig(dedupe, key))
        for key in ("decision", "outcome", "status", "action", "disposition", "reason")
    ]
    normalized = {str(part).strip().lower().replace(" ", "_") for part in parts if part}
    if normalized & _DUPLICATE_DECISIONS:
        return True
    return any("merged_exact" in part or "exact_duplicate" in part for part in normalized)


def _related_memories(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        _dig(record, "related_memories")
        or _dig(record, "matches")
        or _dig(record, "matched_memories")
        or _dig(record, "duplicates")
    )
    if raw is None:
        single = (
            _dig(record, "matched_memory")
            or _dig(record, "existing_memory")
            or _dig(record, "duplicate_of")
        )
        raw = [single] if single is not None else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    memories: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            memories.append({"id": item})
            continue
        if not isinstance(item, dict):
            continue
        memory_id = _string_or_none(
            _dig(item, "id")
            or _dig(item, "thought_id")
            or _dig(item, "memory_id")
            or _dig(item, "cortexdb_id")
        )
        title = _string_or_none(_dig(item, "title") or _dig(item, "summary") or _dig(item, "text"))
        score = _number_or_none(_dig(item, "score") or _dig(item, "similarity") or _dig(item, "distance"))
        memory = {"id": memory_id, "title": title, "score": score}
        memories.append({key: value for key, value in memory.items() if value is not None})

    if not memories:
        memory_id = _string_or_none(
            _dig(record, "matched_memory_id")
            or _dig(record, "existing_memory_id")
            or _dig(record, "duplicate_of_id")
        )
        if memory_id:
            memories.append({"id": memory_id})
    return memories


def _first_timestamp(item: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = _dig(item, key)
        if value is not None:
            return _coerce_timestamp(value)
    return None


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return str(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return str(value).strip()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_type_label(source_type: str) -> str:
    return str(source_type or "").replace("_", " ").replace("-", " ").title() or "Sources"


def _dig(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;]", value)]
        return [part for part in parts if part]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or ""


def _basenameish(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if _is_url(text):
        parsed = urlparse(text)
        text = parsed.path or parsed.netloc or text
    text = text.rstrip("/\\")
    if not text:
        return None
    return re.split(r"[\\/]", text)[-1] or None


def _is_url(value: str) -> bool:
    return bool(re.match(r"(?i)^[a-z][a-z0-9+.-]*://", value.strip()))


def _looks_like_private_reference(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if _is_url(text):
        parsed = urlparse(text)
        if parsed.scheme.lower() in _PRIVATE_URL_SCHEMES:
            return True
        host = (parsed.hostname or "").lower().strip("[]")
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local")
    return bool(re.match(r"^/", text) or re.match(r"(?i)^[a-z]:[\\/]", text) or text.startswith("\\\\"))


def _fsync_parent_best_effort(parent: Path) -> None:
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class _RecordIndex:
    """Best-effort matcher for Panning records keyed with partial identity."""

    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        self.records = list(records)

    def find(
        self,
        *,
        source_id: str | None,
        candidate_id: str | None,
        fingerprint: str | None,
    ) -> dict[str, Any] | None:
        if not self.records:
            return None

        best: tuple[int, dict[str, Any]] | None = None
        for record in self.records:
            record_source = _string_or_none(_dig(record, "source_id") or _dig(record, "adapter_source_id"))
            record_candidate = _string_or_none(_dig(record, "candidate_id") or _dig(record, "id"))
            record_fingerprint = _string_or_none(_dig(record, "content_fingerprint") or _dig(record, "fingerprint"))

            score = 0
            if source_id and record_source == source_id:
                score += 4
            elif source_id and record_source and record_source != source_id:
                continue

            if candidate_id and record_candidate == candidate_id:
                score += 8
            elif candidate_id and record_candidate and record_candidate != candidate_id:
                continue

            if fingerprint and record_fingerprint == fingerprint:
                score += 2
            elif fingerprint and record_fingerprint and record_fingerprint != fingerprint:
                continue

            if score == 0:
                continue
            if best is None or score > best[0]:
                best = (score, record)

        return best[1] if best else None
