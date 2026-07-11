"""Long-history cache contract helpers for the Codex Usage dashboard."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1
RAW_JSONL_RETENTION_DAYS = 30
COMPACT_HISTORY_RETENTION_DAYS = 183
MAX_POINTS_PER_RANGE = 240
FIVE_HOUR_PRESETS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "5h": timedelta(hours=5),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
WEEKLY_PRESETS: dict[str, timedelta | None] = {
    "1w": timedelta(weeks=1),
    "4w": timedelta(weeks=4),
    "12w": timedelta(weeks=12),
    "26w": timedelta(weeks=26),
    "all": None,
}


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            if abs(timestamp) > 1_000_000_000_000:
                timestamp /= 1000.0
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _window_key(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "primary_window": "five_hour",
        "primary": "five_hour",
        "5h": "five_hour",
        "5_hour": "five_hour",
        "five_hour": "five_hour",
        "secondary_window": "weekly",
        "secondary": "weekly",
        "week": "weekly",
        "weekly": "weekly",
        "7d": "weekly",
    }
    return mapping.get(text, text or "unknown")


def _account_map(accounts: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(accounts, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for item in accounts:
        if isinstance(item, dict) and item.get("id"):
            mapped[str(item["id"])] = item
    return mapped


def _iter_history_points(rows: list[dict[str, Any]], account_id: str, window_key: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        generated_at = iso(parse_dt(row.get("generated_at")))
        if not generated_at:
            continue
        for account in row.get("accounts", []) if isinstance(row.get("accounts"), list) else []:
            if not isinstance(account, dict) or str(account.get("id")) != account_id:
                continue
            windows = account.get("windows")
            if not isinstance(windows, dict):
                continue
            window = windows.get(window_key) or windows.get(_window_key(window_key))
            if not isinstance(window, dict):
                continue
            remaining = _to_float(window.get("remaining_percent"))
            used = _to_float(window.get("used_percent"))
            if remaining is None and used is None:
                continue
            point = {
                "generated_at": generated_at,
                "used_percent": used,
                "remaining_percent": remaining,
                "reset_at": iso(parse_dt(window.get("reset_at"))),
                "period_seconds": int(_to_float(window.get("period_seconds")) or 0) or None,
                "pace_state": str(window.get("pace_state") or "unknown"),
            }
            points.append(point)
    points.sort(key=lambda item: parse_dt(item.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return points


def _downsample(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return [copy.deepcopy(point) for point in points]
    if max_points <= 1:
        return [copy.deepcopy(points[-1])]
    last = len(points) - 1
    step = last / float(max_points - 1)
    indices = [round(i * step) for i in range(max_points)]
    indices[0] = 0
    indices[-1] = last
    return [copy.deepcopy(points[index]) for index in sorted(set(indices))]


def _history_bounds(rows: list[dict[str, Any]]) -> tuple[datetime | None, datetime | None]:
    timestamps = [parse_dt(row.get("generated_at")) for row in rows]
    timestamps = [dt for dt in timestamps if dt is not None]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def history_contract(rows: list[dict[str, Any]], now: Any = None) -> dict[str, Any]:
    now_dt = parse_dt(now) or datetime.now(timezone.utc)
    available_from, available_to = _history_bounds(rows)
    compact_cutoff = now_dt - timedelta(days=COMPACT_HISTORY_RETENTION_DAYS)
    all_since = available_from if available_from and available_from > compact_cutoff else compact_cutoff
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now_dt),
        "available_from": iso(available_from),
        "available_to": iso(available_to),
        "timezone": "UTC",
        "raw_jsonl_retention_days": RAW_JSONL_RETENTION_DAYS,
        "compact_history_retention_days": COMPACT_HISTORY_RETENTION_DAYS,
        "all_history_since": iso(all_since),
        "presets": {
            "five_hour": list(FIVE_HOUR_PRESETS.keys()),
            "weekly": list(WEEKLY_PRESETS.keys()),
        },
        "max_points_per_range": MAX_POINTS_PER_RANGE,
    }


def _reset_metadata(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_reset: dict[tuple[str, int | None], dict[str, Any]] = {}
    for point in points:
        reset_at = point.get("reset_at")
        if not reset_at:
            continue
        period = point.get("period_seconds") if isinstance(point.get("period_seconds"), int) else None
        key = (str(reset_at), period)
        if key in by_reset:
            continue
        reset_dt = parse_dt(reset_at)
        start = iso(reset_dt - timedelta(seconds=period)) if reset_dt and period else None
        by_reset[key] = {
            "reset_at": reset_at,
            "period_seconds": period,
            "start": start,
            "end": reset_at,
        }
    markers = [{"at": item["reset_at"], "period_seconds": item["period_seconds"]} for item in by_reset.values()]
    bands = [
        {"start": item["start"], "end": item["end"], "period_seconds": item["period_seconds"]}
        for item in by_reset.values()
        if item.get("start") and item.get("end")
    ]
    return markers, bands


def _range_payload(
    points: list[dict[str, Any]],
    *,
    preset: str,
    duration: timedelta | None,
    now_dt: datetime,
    all_history_since: datetime | None,
    available_from: datetime | None,
    available_to: datetime | None,
    window_key: str,
) -> dict[str, Any]:
    requested_to = now_dt
    requested_from = all_history_since if duration is None else now_dt - duration
    if requested_from is None:
        requested_from = available_from or now_dt
    clamped_from = requested_from
    clamped_to = requested_to
    if available_from and clamped_from < available_from:
        clamped_from = available_from
    if available_to and clamped_to > available_to:
        clamped_to = available_to
    selected: list[dict[str, Any]] = []
    for point in points:
        generated = parse_dt(point.get("generated_at"))
        if not generated:
            continue
        if generated < clamped_from or generated > clamped_to:
            continue
        selected.append(point)
    empty_reason = "none"
    if not points:
        empty_reason = "still_collecting"
    elif clamped_from > clamped_to:
        empty_reason = "outside_retention"
    elif not selected:
        empty_reason = "outside_retention" if available_from or available_to else "still_collecting"
    bucket_seconds = 60
    span_seconds = max(0, int((requested_to - requested_from).total_seconds()))
    if span_seconds >= 90 * 24 * 3600:
        bucket_seconds = 6 * 3600
    elif span_seconds >= 14 * 24 * 3600:
        bucket_seconds = 3600
    sampled = _downsample(selected, MAX_POINTS_PER_RANGE)
    # Reset metadata must follow the same bounded sample as chart points.  Building
    # markers from every 15-second collector row made the snapshot grow past
    # 20 MB and kept the page trapped in its loading state.
    markers, bands = _reset_metadata(sampled)
    return {
        "preset": preset,
        "points": sampled,
        "available_from": iso(available_from),
        "available_to": iso(available_to),
        "requested_from": iso(requested_from),
        "requested_to": iso(requested_to),
        "clamped_from": iso(clamped_from),
        "clamped_to": iso(clamped_to),
        "bucket_seconds": bucket_seconds,
        "aggregation": "raw_downsampled",
        "reset_markers": [dict(item, window_key=window_key) for item in markers],
        "reset_bands": [dict(item, window_key=window_key) for item in bands],
        "empty_reason": empty_reason,
        "source": "collector_compact",
    }


def build_long_history(
    accounts: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    now: Any = None,
) -> dict[str, Any]:
    now_dt = parse_dt(now) or datetime.now(timezone.utc)
    contract = history_contract(history_rows, now_dt)
    available_from = parse_dt(contract.get("available_from"))
    available_to = parse_dt(contract.get("available_to"))
    all_since = parse_dt(contract.get("all_history_since"))
    accounts_out: dict[str, dict[str, Any]] = {}
    for account_id, account in _account_map(accounts).items():
        windows_out: dict[str, Any] = {}
        raw_windows = account.get("windows")
        windows: dict[str, Any] = raw_windows if isinstance(raw_windows, dict) else {}
        for key in ("five_hour", "weekly"):
            if key not in windows:
                continue
            points = _iter_history_points(history_rows, account_id, key)
            presets = FIVE_HOUR_PRESETS if key == "five_hour" else WEEKLY_PRESETS
            windows_out[key] = {
                preset: _range_payload(
                    points,
                    preset=preset,
                    duration=duration,
                    now_dt=now_dt,
                    all_history_since=all_since,
                    available_from=available_from,
                    available_to=available_to,
                    window_key=key,
                )
                for preset, duration in presets.items()
            }
        accounts_out[account_id] = {"windows": windows_out}
    return {"history_contract": contract, "accounts": accounts_out}


def attach_long_history_to_snapshot(
    snapshot: dict[str, Any],
    history_rows: list[dict[str, Any]],
    now: Any = None,
) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    raw_accounts = result.get("accounts")
    accounts: list[dict[str, Any]] = [item for item in raw_accounts if isinstance(item, dict)] if isinstance(raw_accounts, list) else []
    long_history = build_long_history(accounts, history_rows, now=now)
    result["history_contract"] = long_history["history_contract"]
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("id") or "")
        long_accounts = long_history.get("accounts") if isinstance(long_history.get("accounts"), dict) else {}
        account_long = long_accounts.get(account_id, {}) if isinstance(long_accounts, dict) else {}
        raw_windows = account.get("windows")
        windows: dict[str, Any] = raw_windows if isinstance(raw_windows, dict) else {}
        for key, payloads in account_long.get("windows", {}).items():
            if isinstance(windows.get(key), dict):
                windows[key]["long_history"] = payloads
    return result
