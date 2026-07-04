"""Best-effort local request attribution sidecar for LLM calls.

This module records small, sanitized, profile-local request events that other
Hermes components can use for UI attribution.  It intentionally stores only
non-secret routing/session metadata.  Tokens, headers, prompts, request bodies,
raw auth payloads, token hashes, and provider errors do not belong here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
EVENT_TTL_SECONDS = 30 * 60
DEFAULT_COMPLETED_VISIBLE_SECONDS = 5 * 60
_MAX_SAFE_TEXT_CHARS = 240
_ALLOWED_STATUSES = {"in_flight", "ok", "error", "interrupted"}

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "api_key",
    "secret",
    "token",
    "bearer",
    "password",
)
_HEADER_VALUE_RE = re.compile(r"(?i)\b(authorization|cookie)\b\s*[:=]\s*[^\r\n]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|secret|token|password)\b"
    r"\s*[:=]\s*(['\"]?)[^\s,;{}\]\)]+\2"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_OPENAI_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:sk|sess|org)-[A-Za-z0-9_-]{12,}(?![A-Za-z0-9_-])")
_LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_=-]{48,}(?![A-Za-z0-9_-])")


def attribution_path() -> Path:
    """Return the profile-safe attribution sidecar path."""
    return get_hermes_home() / "runtime" / "llm_request_attribution.json"


def _lock_path(path: Path | None = None) -> Path:
    path = path or attribution_path()
    return path.with_suffix(".lock")


@contextlib.contextmanager
def _file_lock(path: Path | None = None) -> Iterator[None]:
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl  # type: ignore

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Locking is best-effort; atomic replacement still protects readers.
            pass
        yield
    finally:
        try:
            try:
                import fcntl  # type: ignore

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        finally:
            handle.close()


def _now_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _now_utc(dt).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _now_utc(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return _now_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _looks_sensitive_key(key: Any) -> bool:
    text = str(key or "").strip().lower()
    return any(part in text for part in _SENSITIVE_KEY_PARTS)


def _looks_sensitive_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    if not text:
        return False
    return bool(
        _HEADER_VALUE_RE.search(text)
        or _ASSIGNMENT_RE.search(text)
        or _BEARER_RE.search(text)
        or _JWT_RE.search(text)
        or _OPENAI_KEY_RE.search(text)
        or _LONG_TOKEN_RE.search(text)
    )


def _safe_text(value: Any, *, max_chars: int = _MAX_SAFE_TEXT_CHARS) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or _looks_sensitive_value(text):
        return None
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_match_key(value: Any) -> str | None:
    """Normalize a safe account/session match key.

    This is deliberately slug-like and conservative.  Secret-looking values are
    discarded instead of hashed; token hashes are still credential-derived data.
    """
    text = _safe_text(value, max_chars=120)
    if not text:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or None


def _is_fallback_match_key(key: str | None) -> bool:
    if not key:
        return False
    return bool(re.fullmatch(r"(?:priority|index)-\d+", key))


def _unique_keys(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for value in values:
        key = normalize_match_key(value)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def safe_account_match_keys(
    *,
    credential_label: str | None,
    credential_priority: int | None,
    credential_id: str | None = None,
) -> dict[str, list[str] | str]:
    """Build high-confidence and fallback match keys from safe credential metadata.

    V1 intentionally omits credential_id unless a future wrapper/runtime pair
    proves it is a stable, non-secret account id shared by both sides.  Priority
    is useful as a diagnostic/corroborating fallback only; it must never become
    an authoritative account match by itself.
    """
    account_keys = _unique_keys([credential_label])
    fallback_values: list[str] = []
    if credential_priority is not None:
        fallback_values.append(f"priority-{credential_priority}")
    fallback_keys = _unique_keys(fallback_values)
    confidence = "label" if account_keys else "none"
    return {
        "account_match_keys": account_keys,
        "fallback_match_keys": fallback_keys,
        "match_confidence": confidence,
    }


def sanitize_attribution_credential(value: Any) -> dict[str, Any]:
    """Return a sanitized request-attribution credential metadata dict."""
    if not isinstance(value, dict):
        return {}
    label = _safe_text(value.get("credential_label"))
    source = _safe_text(value.get("credential_source"), max_chars=80)
    priority = _coerce_int(value.get("credential_priority"))

    strong_keys = _unique_keys(list(value.get("account_match_keys") or []))
    fallback_keys = _unique_keys(list(value.get("fallback_match_keys") or []))

    # Enforce the v1 confidence split defensively even if a caller handed us
    # priority/index keys in the high-confidence field.
    demoted = [key for key in strong_keys if _is_fallback_match_key(key)]
    strong_keys = [key for key in strong_keys if not _is_fallback_match_key(key)]
    fallback_keys = _unique_keys(fallback_keys + demoted)
    if priority is not None:
        fallback_keys = _unique_keys(fallback_keys + [f"priority-{priority}"])
    if not strong_keys and label:
        strong_keys = _unique_keys([label])

    confidence = _safe_text(value.get("match_confidence"), max_chars=40) or "label"
    if not strong_keys:
        confidence = "none"
    elif confidence == "none":
        confidence = "label"

    result: dict[str, Any] = {
        "credential_label": label,
        "credential_priority": priority,
        "credential_source": source,
        "account_match_keys": strong_keys,
        "fallback_match_keys": fallback_keys,
        "match_confidence": confidence,
    }
    return {key: item for key, item in result.items() if item not in (None, "", [])}


def safe_credential_metadata(entry: Any) -> dict[str, Any]:
    """Extract safe attribution metadata from a selected credential entry."""
    try:
        if entry is None:
            return {}
        label = _safe_text(getattr(entry, "label", None))
        source = _safe_text(getattr(entry, "source", None), max_chars=80)
        priority = _coerce_int(getattr(entry, "priority", None))
        key_info = safe_account_match_keys(
            credential_label=label,
            credential_priority=priority,
            credential_id=None,
        )
        return sanitize_attribution_credential(
            {
                "credential_label": label,
                "credential_priority": priority,
                "credential_source": source,
                **key_info,
            }
        )
    except Exception:
        logger.debug("safe_credential_metadata failed", exc_info=True)
        return {}


def _empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "events": []}


def _load_store(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_store()
    except Exception:
        logger.debug("Ignoring corrupt LLM request attribution sidecar", exc_info=True)
        return _empty_store()
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return _empty_store()
    events = raw.get("events")
    if not isinstance(events, list):
        events = []
    cleaned_events = [event for event in events if isinstance(event, dict)]
    return {"schema_version": SCHEMA_VERSION, "events": cleaned_events}


def _write_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(store, sort_keys=True, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _pid_alive(pid: Any) -> bool:
    pid_int = _coerce_int(pid)
    if pid_int is None or pid_int <= 0:
        return True
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def _prune_events(events: list[dict[str, Any]], now_dt: datetime) -> list[dict[str, Any]]:
    cutoff = now_dt - timedelta(seconds=EVENT_TTL_SECONDS)
    kept: list[dict[str, Any]] = []
    for event in events:
        updated_dt = _parse_dt(event.get("updated_at")) or _parse_dt(event.get("started_at"))
        if updated_dt is None or updated_dt < cutoff:
            continue
        if event.get("status") == "in_flight" and not _pid_alive(event.get("pid")):
            continue
        kept.append(event)
    return kept


def _clean_event_fields(event: dict[str, Any]) -> dict[str, Any] | None:
    cleaned: dict[str, Any] = {}
    for key, value in event.items():
        if _looks_sensitive_key(key) or _looks_sensitive_value(value):
            continue
        if key in {"event_id", "provider", "api_mode", "model", "session_id", "title_snapshot", "credential_label", "credential_source", "match_confidence", "status", "started_at", "updated_at", "completed_at"}:
            cleaned[key] = _safe_text(value) if value is not None else None
        elif key == "credential_priority":
            cleaned[key] = _coerce_int(value)
        elif key in {"account_match_keys", "fallback_match_keys"}:
            keys = _unique_keys(list(value or []) if isinstance(value, list) else [])
            if key == "account_match_keys":
                keys = [item for item in keys if not _is_fallback_match_key(item)]
            cleaned[key] = keys
        elif key == "pid":
            cleaned[key] = _coerce_int(value)
    account_keys = cleaned.get("account_match_keys")
    if not isinstance(account_keys, list) or not account_keys:
        return None
    status = cleaned.get("status") or "in_flight"
    cleaned["status"] = status if status in _ALLOWED_STATUSES else "in_flight"
    return {key: value for key, value in cleaned.items() if value not in (None, "", [])}


def record_request_start(
    *,
    provider: str,
    api_mode: str,
    model: str,
    session_id: str,
    title_snapshot: str | None = None,
    credential_label: str | None = None,
    credential_priority: int | None = None,
    credential_source: str | None = None,
    account_match_keys: list[str] | None = None,
    fallback_match_keys: list[str] | None = None,
    match_confidence: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Record the start of a request and return its event id.

    Returns ``None`` when required safe identity metadata is missing.  All
    failures are best-effort and are logged at debug level only.
    """
    try:
        now_dt = _now_utc(now)
        credential = sanitize_attribution_credential(
            {
                "credential_label": credential_label,
                "credential_priority": credential_priority,
                "credential_source": credential_source,
                "account_match_keys": account_match_keys or [],
                "fallback_match_keys": fallback_match_keys or [],
                "match_confidence": match_confidence or "label",
            }
        )
        if not credential.get("account_match_keys"):
            return None
        event_id = uuid.uuid4().hex[:16]
        event = _clean_event_fields(
            {
                "event_id": event_id,
                "provider": provider,
                "api_mode": api_mode,
                "model": model,
                "session_id": session_id,
                "title_snapshot": title_snapshot,
                **credential,
                "status": "in_flight",
                "started_at": _iso(now_dt),
                "updated_at": _iso(now_dt),
                "completed_at": None,
                "pid": os.getpid(),
            }
        )
        if event is None:
            return None
        path = attribution_path()
        with _file_lock(path):
            store = _load_store(path)
            store["events"] = _prune_events(store.get("events", []), now_dt)
            store["events"].append(event)
            _write_store(path, store)
        return event_id
    except Exception:
        logger.debug("record_request_start failed", exc_info=True)
        return None


def record_request_finish(
    event_id: str | None,
    *,
    status: str,
    now: datetime | None = None,
) -> None:
    """Mark a request attribution event finished."""
    try:
        clean_event_id = _safe_text(event_id, max_chars=80)
        if not clean_event_id:
            return
        status_text = str(status or "").strip().lower()
        if status_text not in {"ok", "error", "interrupted"}:
            status_text = "error"
        now_dt = _now_utc(now)
        path = attribution_path()
        with _file_lock(path):
            store = _load_store(path)
            changed = False
            events = _prune_events(store.get("events", []), now_dt)
            for event in events:
                if event.get("event_id") == clean_event_id:
                    event["status"] = status_text
                    event["updated_at"] = _iso(now_dt)
                    event["completed_at"] = _iso(now_dt)
                    changed = True
                    break
            store["events"] = events
            if changed:
                _write_store(path, store)
    except Exception:
        logger.debug("record_request_finish failed", exc_info=True)


def read_recent_events(
    *,
    provider: str | None = None,
    now: datetime | None = None,
    include_completed_seconds: int = DEFAULT_COMPLETED_VISIBLE_SECONDS,
) -> list[dict[str, Any]]:
    """Read recent safe attribution events, optionally filtered by provider."""
    try:
        now_dt = _now_utc(now)
        provider_key = normalize_match_key(provider) if provider else None
        include_completed_seconds = max(0, int(include_completed_seconds))
        path = attribution_path()
        with _file_lock(path):
            store = _load_store(path)
            pruned = _prune_events(store.get("events", []), now_dt)
            if len(pruned) != len(store.get("events", [])):
                store["events"] = pruned
                _write_store(path, store)
        visible: list[dict[str, Any]] = []
        completed_cutoff = now_dt - timedelta(seconds=include_completed_seconds)
        for raw_event in pruned:
            event = _clean_event_fields(raw_event)
            if event is None:
                continue
            if provider_key and normalize_match_key(event.get("provider")) != provider_key:
                continue
            status = event.get("status") or "in_flight"
            if status != "in_flight":
                completed_at = _parse_dt(event.get("completed_at")) or _parse_dt(event.get("updated_at"))
                if completed_at is None or completed_at < completed_cutoff:
                    continue
            visible.append(event)
        visible.sort(key=lambda item: item.get("updated_at") or item.get("started_at") or "", reverse=True)
        return visible
    except Exception:
        logger.debug("read_recent_events failed", exc_info=True)
        return []
