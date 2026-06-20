"""Codex Usage dashboard plugin — backend API routes.

Mounted by Hermes at ``/api/plugins/codex_usage_monitor``.  This backend is
intentionally small: it shells out to the existing sanitized Codex/ChatGPT OAuth
usage wrapper, normalizes the result for dashboard tiles, and persists only a
minimal sanitized history used for chart samples.

Security invariants for this plugin:

* Never read ``auth.json`` directly; only invoke the wrapper command.
* Never expose tokens, Authorization/Cookie headers, API keys, secrets, or raw
  auth payloads.  All wrapper output and error text is sanitized before it can
  enter an API response or history file.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Query

from hermes_constants import get_hermes_home

router = APIRouter()

POLL_INTERVAL_SECONDS = 30
CACHE_TTL_SECONDS = POLL_INTERVAL_SECONDS
COMMAND_TIMEOUT_SECONDS = 25
HISTORY_RETENTION = timedelta(days=8)
HISTORY_DIRNAME = "codex-usage-monitor"
HISTORY_OUTLIER_MIN_DEVIATION_PERCENT = 10.0
HISTORY_OUTLIER_NEIGHBOR_TOLERANCE_PERCENT = 2.0
HISTORY_OUTLIER_MAX_GAP_SECONDS = 10 * 60

WINDOW_PERIOD_SECONDS: dict[str, int] = {
    "five_hour": 5 * 60 * 60,
    "weekly": 7 * 24 * 60 * 60,
}

_WINDOW_LABELS: dict[str, str] = {
    "five_hour": "5-hour",
    "weekly": "Weekly",
}

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "api_key",
    "secret",
    "token",
)

_HEADER_VALUE_RE = re.compile(r"(?i)\b(authorization|cookie)\b\s*[:=]\s*[^\r\n]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|secret|token)\b"
    r"\s*[:=]\s*(['\"]?)[^\s,;{}\]\)]+\2"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
_OPENAI_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:sk|sess|org)-[A-Za-z0-9_-]{12,}(?![A-Za-z0-9_-])")
_TRUNCATED_OPENAI_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk|sess|org)-[A-Za-z0-9_-]{2,}\.\.\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])"
)
_LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_=-]{48,}(?![A-Za-z0-9_-])")

_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Time, percent, and ID helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    """Parse common timestamp shapes into a timezone-aware UTC datetime.

    Returns ``None`` for missing/unparseable values.  Naive datetimes are
    interpreted as UTC because wrapper JSON is expected to represent absolute
    quota reset times.
    """
    if value is None:
        return None

    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            if abs(timestamp) > 1_000_000_000_000:  # milliseconds
                timestamp /= 1000.0
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
            try:
                return parse_dt(float(raw))
            except ValueError:
                return None
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if text.endswith("%"):
            text = text[:-1]
        text = text.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _coerce_percent(value: Any) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0.0, min(100.0, number))


def _round_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(100.0, value)), 3)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "current", "eligible"}
    return bool(value)


def _slugify(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def stable_account_id(account: dict[str, Any], idx: int) -> str:
    """Return a stable, non-secret dashboard ID for an account.

    Preference order follows the wrapper metadata that is safe to expose:
    ``stored_label``, ``label``, then priority/index.  The resulting value is
    slugified so the frontend can safely use it as a DOM/data key.
    """
    fallback = f"account-{idx + 1}"
    for key in ("stored_label", "label"):
        value = account.get(key)
        if value not in (None, ""):
            return _slugify(value, fallback)
    if account.get("priority") not in (None, ""):
        return _slugify(f"priority-{account.get('priority')}", fallback)
    if account.get("index") not in (None, ""):
        return _slugify(f"index-{account.get('index')}", fallback)
    return fallback


def window_key(raw_key: Any) -> str:
    """Map wrapper window names onto the dashboard's canonical keys."""
    if raw_key is None:
        return "unknown"
    text = str(raw_key).strip().lower()
    norm = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    mapping = {
        "primary_window": "five_hour",
        "primary": "five_hour",
        "five_hour": "five_hour",
        "five_hours": "five_hour",
        "fivehour": "five_hour",
        "5_hour": "five_hour",
        "5_hours": "five_hour",
        "5h": "five_hour",
        "5hr": "five_hour",
        "5hrs": "five_hour",
        "secondary_window": "weekly",
        "secondary": "weekly",
        "week": "weekly",
        "weekly": "weekly",
        "7_day": "weekly",
        "7_days": "weekly",
        "7d": "weekly",
    }
    return mapping.get(norm, norm or "unknown")


def compute_on_pace_remaining(
    reset_at: Any,
    period_seconds: Any,
    now: Any,
) -> float | None:
    """Return the percent remaining for perfectly linear on-pace usage."""
    reset_dt = parse_dt(reset_at)
    now_dt = parse_dt(now) or _utcnow()
    period = _to_float(period_seconds)
    if reset_dt is None or period is None or period <= 0:
        return None
    seconds_left = (reset_dt - now_dt).total_seconds()
    expected_remaining = max(0.0, min(100.0, (seconds_left / period) * 100.0))
    return round(expected_remaining, 3)


def compute_pace(
    remaining_percent: Any,
    reset_at: Any,
    period_seconds: Any,
    now: Any,
) -> dict[str, Any]:
    """Return quota pace state plus current-vs-on-pace gap.

    Pace is computed per sample/window, not as global chart zones:

    * on-pace remaining = seconds left / period * 100
    * used = 100 - remaining
    * expected used = 100 - on-pace remaining
    * gap > +5 => over, gap < -5 => under, otherwise on

    ``pace_gap_percent`` is positive when usage is ahead of the expected pace and
    negative when usage is behind it.
    """
    remaining = _coerce_percent(remaining_percent)
    on_pace_remaining = compute_on_pace_remaining(reset_at, period_seconds, now)
    if remaining is None or on_pace_remaining is None:
        return {
            "pace_state": "unknown",
            "pace_gap_percent": None,
            "on_pace_remaining_percent": on_pace_remaining,
        }

    used_percent = 100.0 - remaining
    expected_used = 100.0 - on_pace_remaining
    gap = used_percent - expected_used
    if gap > 5.0:
        pace_state = "over"
    elif gap < -5.0:
        pace_state = "under"
    else:
        pace_state = "on"
    return {
        "pace_state": pace_state,
        "pace_gap_percent": round(gap, 3),
        "on_pace_remaining_percent": on_pace_remaining,
    }


def compute_pace_state(
    remaining_percent: Any,
    reset_at: Any,
    period_seconds: Any,
    now: Any,
) -> str:
    """Classify the current sample as under/on/over expected quota pace."""
    return str(compute_pace(remaining_percent, reset_at, period_seconds, now)["pace_state"])


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    norm = _normalized_key(key)
    return any(part in norm for part in _SENSITIVE_KEY_PARTS)


def _sanitize_text(text: str) -> str:
    redacted = _HEADER_VALUE_RE.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)
    redacted = _JWT_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _OPENAI_KEY_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _TRUNCATED_OPENAI_KEY_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _LONG_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)
    return redacted


def sanitize(obj: Any) -> Any:
    """Recursively remove secret-bearing keys and redact token-like strings."""
    if isinstance(obj, dict):
        clean: dict[Any, Any] = {}
        for key, value in obj.items():
            if _is_sensitive_key(key):
                continue
            clean[key] = sanitize(value)
        return clean
    if isinstance(obj, list):
        return [sanitize(item) for item in obj]
    if isinstance(obj, tuple):
        return [sanitize(item) for item in obj]
    if isinstance(obj, str):
        return _sanitize_text(obj)
    return obj


# ---------------------------------------------------------------------------
# Normalization and active-account inference
# ---------------------------------------------------------------------------


def _extract_accounts(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    candidates = [raw]
    for key in ("data", "result", "usage"):
        child = raw.get(key)
        if isinstance(child, dict):
            candidates.append(child)
    for candidate in candidates:
        accounts = candidate.get("accounts")
        if isinstance(accounts, list):
            return accounts
    return []


def _iter_windows(account: dict[str, Any]) -> Iterable[dict[str, Any]]:
    windows = account.get("windows")
    if isinstance(windows, list):
        for item in windows:
            if isinstance(item, dict):
                yield item
    elif isinstance(windows, dict):
        for key, value in windows.items():
            if isinstance(value, dict):
                merged = dict(value)
                merged.setdefault("key", key)
                yield merged
            else:
                yield {"key": key, "remaining_percent": value}

    for top_level_key in ("primary_window", "secondary_window", "five_hour", "weekly"):
        value = account.get(top_level_key)
        if isinstance(value, dict):
            merged = dict(value)
            merged.setdefault("key", top_level_key)
            yield merged


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_window(raw_window: dict[str, Any], now_dt: datetime) -> dict[str, Any] | None:
    clean = sanitize(raw_window)
    key = window_key(_first_present(clean, "key", "window", "name", "type"))
    if key == "unknown":
        return None

    default_period = WINDOW_PERIOD_SECONDS.get(key)
    period = _to_float(
        _first_present(
            clean,
            "period_seconds",
            "window_seconds",
            "duration_seconds",
            "reset_period_seconds",
        )
    )
    if period is None:
        period = float(default_period) if default_period is not None else None

    used = _coerce_percent(_first_present(clean, "used_percent", "usedPct", "used_pct", "used"))
    remaining = _coerce_percent(
        _first_present(clean, "remaining_percent", "remainingPct", "remaining_pct", "remaining")
    )
    if remaining is None and used is not None:
        remaining = 100.0 - used
    if used is None and remaining is not None:
        used = 100.0 - remaining

    reset_dt = parse_dt(_first_present(clean, "reset_at", "resets_at", "resetAt", "reset"))
    reset_at = _iso(reset_dt)
    reset_at_local = _first_present(clean, "reset_at_local", "resetAtLocal", "reset_local")

    period_int = int(period) if period is not None else None
    seconds_left = None
    if reset_dt is not None:
        seconds_left = max(0, int((reset_dt - now_dt).total_seconds()))
    pace = compute_pace(remaining, reset_dt, period_int, now_dt)

    return {
        "key": key,
        "label": _WINDOW_LABELS.get(key, key.replace("_", " ").title()),
        "used_percent": _round_percent(used),
        "remaining_percent": _round_percent(remaining),
        "reset_at": reset_at,
        "reset_at_local": reset_at_local,
        "period_seconds": period_int,
        "seconds_left": seconds_left,
        "delta_remaining_percent": None,
        "pace_state": pace["pace_state"],
        "pace_gap_percent": pace["pace_gap_percent"],
        "on_pace_remaining_percent": pace["on_pace_remaining_percent"],
        "history": [],
    }


def _account_map(accounts_or_snapshot: Any) -> dict[str, dict[str, Any]]:
    if accounts_or_snapshot is None:
        return {}
    accounts: Any
    if isinstance(accounts_or_snapshot, dict) and isinstance(accounts_or_snapshot.get("accounts"), list):
        accounts = accounts_or_snapshot.get("accounts")
    elif isinstance(accounts_or_snapshot, dict):
        # Already keyed by account id.
        return {
            str(account_id): account
            for account_id, account in accounts_or_snapshot.items()
            if isinstance(account, dict)
        }
    elif isinstance(accounts_or_snapshot, list):
        accounts = accounts_or_snapshot
    else:
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    for idx, account in enumerate(accounts):
        if not isinstance(account, dict):
            continue
        account_id = account.get("id") or stable_account_id(account, idx)
        mapped[str(account_id)] = account
    return mapped


def _window_map(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    windows = account.get("windows")
    if isinstance(windows, dict):
        return {window_key(key): value for key, value in windows.items() if isinstance(value, dict)}
    if isinstance(windows, list):
        mapped: dict[str, dict[str, Any]] = {}
        for item in windows:
            if isinstance(item, dict):
                key = window_key(_first_present(item, "key", "window", "name", "type"))
                mapped[key] = item
        return mapped
    return {}


def infer_active_accounts(
    previous_by_id: dict[str, dict[str, Any]] | None,
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark only the account with the largest remaining-percent drop active.

    Remaining increases (quota resets/refills) and tiny fluctuations do not count.
    The active marker is derived solely from quota deltas; it does not rely on any
    session/account mapping.
    """
    previous_by_id = previous_by_id or {}
    best_account: dict[str, Any] | None = None
    best_window_key: str | None = None
    best_drop = 0.0

    for account in accounts:
        account["active_now"] = False
        account.pop("active_drop_percent", None)
        account["active_reason"] = None
        account_id = str(account.get("id", ""))
        previous = previous_by_id.get(account_id)
        if not isinstance(previous, dict):
            continue
        previous_windows = _window_map(previous)
        current_windows = _window_map(account)
        for key, current_window in current_windows.items():
            previous_window = previous_windows.get(key)
            if not isinstance(previous_window, dict):
                continue
            previous_remaining = _coerce_percent(previous_window.get("remaining_percent"))
            current_remaining = _coerce_percent(current_window.get("remaining_percent"))
            if previous_remaining is None or current_remaining is None:
                continue
            current_window["delta_remaining_percent"] = round(current_remaining - previous_remaining, 3)
            drop = previous_remaining - current_remaining
            if drop > best_drop:
                best_drop = drop
                best_account = account
                best_window_key = key

    if best_account is not None and best_drop > 0.2:
        best_account["active_now"] = True
        best_account["active_drop_percent"] = round(best_drop, 3)
        label = _WINDOW_LABELS.get(
            best_window_key or "",
            (best_window_key or "Quota").replace("_", " ").title(),
        )
        drop_text = f"{round(best_drop, 3):g}"
        best_account["active_reason"] = (
            f"{label} remaining dropped {drop_text}% since previous sample"
        )
    return accounts


def normalize_snapshot(
    raw: Any,
    now: Any = None,
    previous: Any = None,
    history: Any = None,
) -> dict[str, Any]:
    """Convert wrapper JSON into the sanitized dashboard API shape."""
    now_dt = parse_dt(now) or _utcnow()
    clean = sanitize(raw)
    if isinstance(clean, list):
        clean = {"accounts": clean}
    if not isinstance(clean, dict):
        clean = {}

    generated_dt = (
        parse_dt(_first_present(clean, "generated_at", "timestamp", "created_at", "time"))
        or now_dt
    )

    accounts: list[dict[str, Any]] = []
    for idx, raw_account in enumerate(_extract_accounts(clean)):
        if not isinstance(raw_account, dict):
            continue
        account = sanitize(raw_account)
        account_id = stable_account_id(account, idx)
        stored_label = account.get("stored_label")
        label = account.get("label") or stored_label or f"Account {idx + 1}"
        display_label = account.get("display_label") or account.get("short_label") or label
        auth_status = account.get("auth_status") or account.get("status")
        plan_type = account.get("plan_type") or account.get("plan")
        auth_status_text = str(auth_status) if auth_status not in (None, "") else None

        normalized_account: dict[str, Any] = {
            "id": account_id,
            "label": str(label),
            "display_label": str(display_label),
            "stored_label": str(stored_label) if stored_label not in (None, "") else None,
            "priority": account.get("priority"),
            "index": account.get("index", idx),
            "is_current": _coerce_bool(account.get("is_current", account.get("current"))),
            "is_next_eligible": _coerce_bool(
                account.get("is_next_eligible", account.get("next_eligible"))
            ),
            "auth_status": auth_status_text,
            "plan_type": str(plan_type) if plan_type not in (None, "") else None,
            "active_now": False,
            "windows": {},
        }
        if auth_status_text and "reauth" in auth_status_text.lower():
            normalized_account["error"] = "Re-auth required"

        for raw_window in _iter_windows(account):
            window = _normalize_window(raw_window, now_dt)
            if window is not None:
                normalized_account["windows"][window["key"]] = window

        accounts.append(normalized_account)

    infer_active_accounts(_account_map(previous), accounts)

    ok_value = clean.get("ok", True)
    if isinstance(ok_value, str):
        ok = ok_value.strip().lower() not in {"false", "0", "no", "error", "failed"}
    else:
        ok = bool(ok_value)

    result: dict[str, Any] = {
        "ok": ok,
        "generated_at": _iso(generated_dt),
        "accounts": accounts,
    }
    if clean.get("error") is not None:
        result["error"] = sanitize(clean.get("error"))
    if history is not None:
        result["accounts"] = _attach_history_to_accounts(accounts, history, history_points=240)
    return result


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def history_path() -> Path:
    return get_hermes_home() / "cache" / HISTORY_DIRNAME / "history.jsonl"


def _history_row_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "generated_at": snapshot.get("generated_at") or _iso(_utcnow()),
        "accounts": [],
    }
    for account in snapshot.get("accounts", []) if isinstance(snapshot.get("accounts"), list) else []:
        if not isinstance(account, dict):
            continue
        windows_out: dict[str, Any] = {}
        for key, window in _window_map(account).items():
            windows_out[key] = {
                "key": key,
                "used_percent": window.get("used_percent"),
                "remaining_percent": window.get("remaining_percent"),
                "reset_at": window.get("reset_at"),
                "period_seconds": window.get("period_seconds"),
                "pace_state": window.get("pace_state"),
            }
        row["accounts"].append(
            {
                "id": account.get("id"),
                "label": account.get("label"),
                "stored_label": account.get("stored_label"),
                "priority": account.get("priority"),
                "index": account.get("index"),
                "active_now": account.get("active_now", False),
                "windows": windows_out,
            }
        )
    return sanitize(row)


def load_history_rows(
    path: Path | None = None,
    *,
    now: Any = None,
    prune: bool = False,
) -> list[dict[str, Any]]:
    """Read sanitized JSONL history, skipping corrupt/old rows."""
    path = path or history_path()
    now_dt = parse_dt(now) or _utcnow()
    cutoff = now_dt - HISTORY_RETENTION
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = sanitize(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                row_dt = parse_dt(row.get("generated_at"))
                if row_dt is None or row_dt < cutoff:
                    continue
                row["generated_at"] = _iso(row_dt)
                rows.append(row)
    except OSError:
        return []

    rows.sort(key=lambda item: parse_dt(item.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    if prune:
        _rewrite_history(path, rows)
    return rows


def _rewrite_history(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(sanitize(row), sort_keys=True, separators=(",", ":")) + "\n")
    tmp_path.replace(path)


def append_history_snapshot(
    snapshot: dict[str, Any],
    path: Path | None = None,
    *,
    now: Any = None,
) -> list[dict[str, Any]]:
    """Append one compact sanitized snapshot row and return pruned history."""
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = _history_row_from_snapshot(snapshot)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return load_history_rows(path, now=now, prune=True)


def downsample_points(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Uniformly downsample while preserving the first and last point."""
    if max_points <= 0:
        return []
    if len(points) <= max_points:
        return [copy.deepcopy(point) for point in points]
    if max_points == 1:
        return [copy.deepcopy(points[-1])]

    last_index = len(points) - 1
    step = last_index / float(max_points - 1)
    indices = [round(i * step) for i in range(max_points)]
    indices[0] = 0
    indices[-1] = last_index

    deduped: list[int] = []
    seen: set[int] = set()
    for index in indices:
        index = max(0, min(last_index, index))
        if index not in seen:
            deduped.append(index)
            seen.add(index)

    # Rounding should already produce max_points unique indices when
    # len(points) > max_points, but fill defensively if needed.
    candidate = 0
    while len(deduped) < max_points and candidate <= last_index:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
        candidate += 1
    deduped = sorted(deduped[:max_points])
    if deduped[-1] != last_index:
        deduped[-1] = last_index
    return [copy.deepcopy(points[index]) for index in deduped]


def _history_points_by_account_window(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        generated_at = row.get("generated_at")
        for account in row.get("accounts", []) if isinstance(row.get("accounts"), list) else []:
            if not isinstance(account, dict):
                continue
            account_id = account.get("id")
            if not account_id:
                continue
            for key, window in _window_map(account).items():
                point = {
                    "generated_at": generated_at,
                    "used_percent": window.get("used_percent"),
                    "remaining_percent": window.get("remaining_percent"),
                    "reset_at": window.get("reset_at"),
                    "period_seconds": window.get("period_seconds"),
                    "pace_state": window.get("pace_state"),
                }
                by_key.setdefault((str(account_id), key), []).append(point)
    return by_key


def _window_bounds(window: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    reset_dt = parse_dt(window.get("reset_at"))
    period = _to_float(window.get("period_seconds"))
    if reset_dt is None or period is None or period <= 0:
        return None, reset_dt
    return reset_dt - timedelta(seconds=period), reset_dt


def _history_points_for_current_window(
    points: list[dict[str, Any]],
    window: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return chart points for the current reset window, anchored at 100%.

    Persisted samples are sparse by design.  The chart should connect known
    values with straight lines; when the beginning of the usage period was not
    sampled, we still know that the period began at 100% remaining, so add a
    synthetic, non-persisted start point before downsampling.
    """
    window_start, window_end = _window_bounds(window)
    filtered: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        remaining = _coerce_percent(point.get("remaining_percent"))
        if remaining is None:
            continue
        generated_dt = parse_dt(point.get("generated_at"))
        point_reset_dt = parse_dt(point.get("reset_at"))
        if point_reset_dt is not None and window_end is not None:
            # Near a quota reset, the previous bucket's last sample can fall
            # inside the timestamp tolerance for the next bucket's period
            # start. Match reset_at too so stale samples do not draw a false
            # vertical drop at the left edge of the chart.
            if abs((point_reset_dt - window_end).total_seconds()) > 60:
                continue
        if window_start is not None and window_end is not None:
            if generated_dt is None:
                continue
            tolerance = timedelta(seconds=60)
            if generated_dt < window_start - tolerance or generated_dt > window_end + tolerance:
                continue
        filtered.append(copy.deepcopy(point))

    filtered.sort(
        key=lambda item: parse_dt(item.get("generated_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    filtered = _drop_isolated_history_outliers(filtered)

    if window_start is not None:
        first_dt = next(
            (parse_dt(point.get("generated_at")) for point in filtered if parse_dt(point.get("generated_at")) is not None),
            None,
        )
        # Avoid a duplicate dot when a real sample landed right at period start;
        # otherwise explicitly draw the straight segment from 100% at start to
        # the first known value.
        if first_dt is None or first_dt > window_start + timedelta(seconds=60):
            filtered.insert(
                0,
                {
                    "generated_at": _iso(window_start),
                    "used_percent": 0.0,
                    "remaining_percent": 100.0,
                    "reset_at": window.get("reset_at"),
                    "period_seconds": window.get("period_seconds"),
                    "pace_state": "unknown",
                    "synthetic": True,
                    "source": "period_start_anchor",
                },
            )

    return filtered


def _drop_isolated_history_outliers(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove one-sample history glitches that create false chart spikes.

    A single impossible-looking point can happen when the wrapper briefly emits
    an incomplete bucket.  If the prior and next samples are close together,
    are close in value, and the middle point jumps far outside that local
    value, omit the middle point from chart history.  Sustained drops are kept.
    """
    if len(points) < 3:
        return points

    cleaned: list[dict[str, Any]] = [points[0]]
    max_gap = timedelta(seconds=HISTORY_OUTLIER_MAX_GAP_SECONDS)
    for index in range(1, len(points) - 1):
        previous = points[index - 1]
        current = points[index]
        next_point = points[index + 1]
        previous_remaining = _coerce_percent(previous.get("remaining_percent"))
        current_remaining = _coerce_percent(current.get("remaining_percent"))
        next_remaining = _coerce_percent(next_point.get("remaining_percent"))
        previous_dt = parse_dt(previous.get("generated_at"))
        current_dt = parse_dt(current.get("generated_at"))
        next_dt = parse_dt(next_point.get("generated_at"))

        is_outlier = False
        if (
            previous_remaining is not None
            and current_remaining is not None
            and next_remaining is not None
            and previous_dt is not None
            and current_dt is not None
            and next_dt is not None
            and current_dt - previous_dt <= max_gap
            and next_dt - current_dt <= max_gap
            and abs(previous_remaining - next_remaining) <= HISTORY_OUTLIER_NEIGHBOR_TOLERANCE_PERCENT
        ):
            neighbor_floor = min(previous_remaining, next_remaining)
            neighbor_ceiling = max(previous_remaining, next_remaining)
            is_low_spike = current_remaining <= neighbor_floor - HISTORY_OUTLIER_MIN_DEVIATION_PERCENT
            is_high_spike = current_remaining >= neighbor_ceiling + HISTORY_OUTLIER_MIN_DEVIATION_PERCENT
            is_outlier = is_low_spike or is_high_spike

        if not is_outlier:
            cleaned.append(current)

    cleaned.append(points[-1])
    return cleaned


def _attach_history_to_accounts(
    accounts: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    history_points: int,
) -> list[dict[str, Any]]:
    accounts_out = copy.deepcopy(accounts)
    history_index = _history_points_by_account_window(history_rows)
    for account in accounts_out:
        account_id = str(account.get("id", ""))
        windows = account.get("windows")
        if not isinstance(windows, dict):
            continue
        for key, window in windows.items():
            points = history_index.get((account_id, window_key(key)), [])
            if isinstance(window, dict):
                current_points = _history_points_for_current_window(points, window)
                window["history"] = downsample_points(current_points, history_points)
    return accounts_out


def _previous_accounts_from_history(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows:
        return {}
    last = rows[-1]
    return _account_map(last)


# ---------------------------------------------------------------------------
# Wrapper command execution
# ---------------------------------------------------------------------------


def _command_string(binary: str, args: list[str]) -> str:
    return " ".join([binary, *args])


def _parse_json_output(output: str) -> Any:
    text = output.strip()
    if not text:
        raise ValueError("command produced no stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start != -1 and object_end > object_start:
        return json.loads(text[object_start : object_end + 1])
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start != -1 and array_end > array_start:
        return json.loads(text[array_start : array_end + 1])
    raise ValueError("command output did not contain JSON")


def _short_error(text: str, limit: int = 700) -> str:
    clean = sanitize(text.replace("\x00", " ").strip())
    if not isinstance(clean, str):
        clean = str(clean)
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return clean


def run_usage_command() -> tuple[Any | None, dict[str, Any]]:
    """Invoke the first available Codex OAuth usage wrapper.

    Preferred order is ``husage --json usage`` then ``husage usage --json``;
    if ``husage`` is unavailable or both argument orders fail, repeat the same
    safe argument-order fallback for ``hermes-codex-accounts``.
    """
    binaries = ("husage", "hermes-codex-accounts")
    arg_orders = (["--json", "usage"], ["usage", "--json"])
    missing: list[str] = []
    errors: list[str] = []
    last_command: str | None = None
    found_binary = False

    for binary in binaries:
        if shutil.which(binary) is None:
            missing.append(binary)
            continue
        found_binary = True
        for args in arg_orders:
            command = [binary, *args]
            last_command = _command_string(binary, args)
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                    check=False,
                )
            except FileNotFoundError:
                missing.append(binary)
                break
            except subprocess.TimeoutExpired:
                errors.append(f"{last_command} timed out after {COMMAND_TIMEOUT_SECONDS}s")
                continue
            except OSError as exc:
                errors.append(f"{last_command} failed to start: {_short_error(str(exc))}")
                continue

            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
                errors.append(f"{last_command} exited {completed.returncode}: {_short_error(detail)}")
                continue
            try:
                raw = _parse_json_output(completed.stdout)
            except (json.JSONDecodeError, ValueError) as exc:
                detail = completed.stdout.strip() or completed.stderr.strip() or str(exc)
                errors.append(f"{last_command} returned invalid JSON: {_short_error(detail)}")
                continue

            return raw, {"command": last_command, "available": True, "last_error": None}

    if not found_binary:
        last_error = (
            "No Codex usage wrapper command found. Install or enable `husage` "
            "or `hermes-codex-accounts`, then retry."
        )
    else:
        missing_text = f" Missing commands: {', '.join(sorted(set(missing)))}." if missing else ""
        last_error = ("; ".join(errors[-4:]) or "Codex usage wrapper command failed.") + missing_text
    return None, {"command": last_command, "available": found_binary, "last_error": _short_error(last_error)}


# ---------------------------------------------------------------------------
# Snapshot assembly and route
# ---------------------------------------------------------------------------


def _clamp_history_points(history_points: Any) -> int:
    try:
        points = int(history_points)
    except (TypeError, ValueError):
        points = 240
    return max(20, min(1000, points))


def _base_error_snapshot(now_dt: datetime, source: dict[str, Any]) -> dict[str, Any]:
    clean_source = sanitize(source)
    if not isinstance(clean_source, dict):
        clean_source = {}
    return {
        "ok": False,
        "generated_at": _iso(now_dt),
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "cached": False,
        "age_seconds": 0.0,
        "source": {
            "command": clean_source.get("command"),
            "available": bool(clean_source.get("available", False)),
            "last_error": clean_source.get("last_error") or "Unable to load Codex usage data.",
        },
        "accounts": [],
    }


def _with_runtime_fields(
    base_snapshot: dict[str, Any],
    *,
    cached: bool,
    now_dt: datetime,
    history_points: int,
    history_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = copy.deepcopy(base_snapshot)
    generated_dt = parse_dt(snapshot.get("generated_at")) or now_dt
    snapshot["cached"] = cached
    snapshot["age_seconds"] = round(max(0.0, (now_dt - generated_dt).total_seconds()), 3)
    snapshot["poll_interval_seconds"] = POLL_INTERVAL_SECONDS
    if history_rows is None:
        history_rows = load_history_rows(now=now_dt, prune=False)
    accounts = snapshot.get("accounts")
    if isinstance(accounts, list):
        snapshot["accounts"] = _attach_history_to_accounts(accounts, history_rows, history_points)
    snapshot["source"] = sanitize(snapshot.get("source") or {})
    return sanitize(snapshot)


def build_snapshot(history_points: int = 240, force: bool = False) -> dict[str, Any]:
    """Build the API snapshot, using a short TTL cache to coalesce tabs."""
    points = _clamp_history_points(history_points)
    now_dt = _utcnow()
    monotonic_now = time.monotonic()

    global _SNAPSHOT_CACHE
    with _CACHE_LOCK:
        if (
            not force
            and _SNAPSHOT_CACHE is not None
            and monotonic_now - float(_SNAPSHOT_CACHE.get("monotonic", 0.0)) < CACHE_TTL_SECONDS
        ):
            return _with_runtime_fields(
                _SNAPSHOT_CACHE["snapshot"],
                cached=True,
                now_dt=now_dt,
                history_points=points,
            )

        history_rows = load_history_rows(now=now_dt, prune=True)
        previous_by_id = _previous_accounts_from_history(history_rows)
        raw, source = run_usage_command()
        if raw is None:
            base_snapshot = _base_error_snapshot(now_dt, source)
        else:
            normalized = normalize_snapshot(raw, now=now_dt, previous=previous_by_id)
            source = sanitize(source)
            if not isinstance(source, dict):
                source = {}
            last_error = source.get("last_error")
            if not normalized.get("ok", True) and not last_error:
                last_error = normalized.get("error") or "Codex usage wrapper reported an error."
            base_snapshot = {
                "ok": bool(normalized.get("ok", True)),
                "generated_at": normalized.get("generated_at") or _iso(now_dt),
                "poll_interval_seconds": POLL_INTERVAL_SECONDS,
                "cached": False,
                "age_seconds": 0.0,
                "source": {
                    "command": source.get("command"),
                    "available": bool(source.get("available", True)),
                    "last_error": sanitize(last_error) if last_error else None,
                },
                "accounts": normalized.get("accounts", []),
            }
            history_rows = append_history_snapshot(base_snapshot, now=now_dt)

        _SNAPSHOT_CACHE = {
            "monotonic": monotonic_now,
            "snapshot": copy.deepcopy(base_snapshot),
        }
        return _with_runtime_fields(
            base_snapshot,
            cached=False,
            now_dt=now_dt,
            history_points=points,
            history_rows=history_rows,
        )


@router.get("/snapshot")
def get_snapshot(
    history_points: int = Query(240, ge=20, le=1000),
    force: bool = Query(False),
) -> dict[str, Any]:
    """Return the normalized Codex OAuth usage snapshot for dashboard tiles."""
    return build_snapshot(history_points=history_points, force=force)
