"""Background collector core for the Codex Usage dashboard."""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# Allow script execution from the dashboard directory without installing a package.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import cache as cache_helpers  # noqa: E402
from history_contract import attach_long_history_to_snapshot  # noqa: E402

CollectorDeps = dict[str, Callable[..., Any]]

_WINDOW_LABELS = {
    "five_hour": "5-hour",
    "primary_window": "5-hour",
    "weekly": "Weekly",
    "secondary_window": "Weekly",
}
_PERCENT_PAIR_TOLERANCE = 1.0


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _remaining_percent(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None
    remaining: float | None = None
    value = window.get("remaining_percent")
    if isinstance(value, (int, float)):
        remaining = float(value)
    used = window.get("used_percent")
    used_value: float | None = None
    if isinstance(used, (int, float)):
        used_value = float(used)
    if used_value is not None and remaining is not None:
        if abs((used_value + remaining) - 100.0) > _PERCENT_PAIR_TOLERANCE:
            return 100.0 - used_value
        return remaining
    if remaining is not None:
        return remaining
    if used_value is not None:
        return 100.0 - used_value
    return None


def _account_windows(account: Any) -> dict[str, dict[str, Any]]:
    windows = account.get("windows") if isinstance(account, dict) else None
    if isinstance(windows, dict):
        return {str(key): value for key, value in windows.items() if isinstance(value, dict)}
    if isinstance(windows, list):
        result: dict[str, dict[str, Any]] = {}
        for window in windows:
            if not isinstance(window, dict):
                continue
            key = str(window.get("key") or window.get("name") or "")
            if key:
                result[key] = window
        return result
    return {}


def _active_label(window_key: str | None) -> str:
    if not window_key:
        return "Quota"
    return _WINDOW_LABELS.get(window_key, window_key.replace("_", " ").title())


def _active_event_from_history(
    history_rows: list[dict[str, Any]],
    *,
    now: datetime,
    stale_after_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Return the most recent quota-drop event from compact history rows."""
    if not history_rows:
        return None

    now_dt = now.astimezone(timezone.utc)
    cutoff = now_dt - timedelta(seconds=stale_after_seconds) if stale_after_seconds is not None else None
    sorted_rows = sorted(
        [row for row in history_rows if isinstance(row, dict)],
        key=lambda row: _parse_dt(row.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    previous_by_id: dict[str, dict[str, Any]] = {}
    best: dict[str, Any] | None = None

    for row in sorted_rows:
        row_dt = _parse_dt(row.get("generated_at"))
        if row_dt is None or row_dt > now_dt:
            continue
        raw_accounts = row.get("accounts")
        row_accounts = raw_accounts if isinstance(raw_accounts, list) else []
        for account in row_accounts:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id") or "")
            if not account_id:
                continue
            previous = previous_by_id.get(account_id)
            if isinstance(previous, dict) and (cutoff is None or row_dt >= cutoff):
                current_windows = _account_windows(account)
                previous_windows = _account_windows(previous)
                for key, current_window in current_windows.items():
                    previous_window = previous_windows.get(key)
                    previous_remaining = _remaining_percent(previous_window)
                    current_remaining = _remaining_percent(current_window)
                    if previous_remaining is None or current_remaining is None:
                        continue
                    drop = previous_remaining - current_remaining
                    if drop <= 0.2:
                        continue
                    if best is None or row_dt > best["seen_dt"] or (
                        row_dt == best["seen_dt"] and drop > best["drop_percent"]
                    ):
                        best = {
                            "account_id": account_id,
                            "label": str(
                                account.get("label")
                                or account.get("stored_label")
                                or account.get("display_label")
                                or account_id
                            ),
                            "window_key": key,
                            "window_label": _active_label(key),
                            "drop_percent": round(drop, 3),
                            "seen_at": _iso(row_dt),
                            "seen_dt": row_dt,
                            "age_seconds": max(0, int((now_dt - row_dt).total_seconds())),
                        }
            previous_by_id[account_id] = account

    if best is None:
        return None
    best.pop("seen_dt", None)
    return best


def mark_recent_active_account(
    accounts: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    *,
    now: datetime,
    stale_after_seconds: int = cache_helpers.STALE_AFTER_SECONDS,
) -> list[dict[str, Any]]:
    """Keep the most recent quota-drop account visibly active for a short window."""
    for account in accounts:
        account["active_now"] = False
        account.pop("active_drop_percent", None)
        account["active_reason"] = None

    event = _active_event_from_history(
        history_rows,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    if event is None:
        return accounts

    drop = float(event["drop_percent"])
    account_id = str(event["account_id"])
    current_by_id = {str(account.get("id") or ""): account for account in accounts}
    active_account = current_by_id.get(account_id)
    if active_account is None or active_account.get("is_exhausted"):
        return accounts

    active_account["active_now"] = True
    active_account["active_drop_percent"] = round(drop, 3)
    label = str(event.get("window_label") or "Quota")
    active_account["active_reason"] = (
        f"{label} remaining dropped {round(drop, 3):g}% within the last "
        f"{stale_after_seconds} seconds"
    )
    active_account["active_last_seen_at"] = event.get("seen_at")
    return accounts


def last_active_account_summary(
    accounts: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Return the most recent active account event, even after the pulse window expires."""
    event = _active_event_from_history(history_rows, now=now, stale_after_seconds=None)
    if event is None:
        return None
    current_by_id = {str(account.get("id") or ""): account for account in accounts}
    account = current_by_id.get(str(event.get("account_id") or ""))
    if isinstance(account, dict):
        event["label"] = str(account.get("label") or account.get("stored_label") or event.get("label"))
        event["active_now"] = bool(account.get("active_now"))
    else:
        event["active_now"] = False
    return event


def _status(
    *,
    state: str,
    now: datetime,
    last_success_at: str | None = None,
    last_error: str | None = None,
    snapshot_source: str = "collector_cache",
) -> dict[str, Any]:
    return {
        "schema_version": cache_helpers.SCHEMA_VERSION,
        "collector_enabled": True,
        "collector_interval_seconds": cache_helpers.COLLECTOR_INTERVAL_SECONDS,
        "stale_after_seconds": cache_helpers.STALE_AFTER_SECONDS,
        "collector_status": state,
        "collector_last_attempt_at": _iso(now),
        "collector_last_success_at": last_success_at,
        "collector_last_error_at": _iso(now) if last_error else None,
        "collector_last_error": last_error,
        "snapshot_source": snapshot_source,
        "history_source": "collector_compact",
    }


def _parse_cache_generated_at(payload: dict[str, Any] | None) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    generated = payload.get("fetched_at") or payload.get("generated_at")
    return _parse_dt(generated)


def _reset_cache_fresh(payload: dict[str, Any] | None, *, now: datetime) -> bool:
    fetched = _parse_cache_generated_at(payload)
    if fetched is None:
        return False
    age = (now.astimezone(timezone.utc) - fetched).total_seconds()
    return 0 <= age < cache_helpers.RESET_CREDITS_INTERVAL_SECONDS


def _read_reset_credits_cache(now: datetime) -> tuple[Any | None, dict[str, Any]]:
    cached = cache_helpers.read_json_file(cache_helpers.reset_credits_path())
    if not isinstance(cached, dict):
        return None, {"command": None, "available": False, "last_error": "reset-credit cache missing"}
    raw = cached.get("raw_reset_credits")
    source_raw = cached.get("source")
    source: dict[str, Any] = dict(source_raw) if isinstance(source_raw, dict) else {}
    source["cached"] = True
    source["cache_age_seconds"] = round(
        max(0.0, (now - (_parse_cache_generated_at(cached) or now)).total_seconds()),
        3,
    )
    return raw, source


def _write_reset_credits_cache(*, raw_reset_credits: Any, reset_source: dict[str, Any], now: datetime) -> None:
    cache_helpers.write_json_atomic(
        cache_helpers.reset_credits_path(),
        {
            "schema_version": cache_helpers.SCHEMA_VERSION,
            "fetched_at": _iso(now),
            "raw_reset_credits": raw_reset_credits,
            "source": reset_source,
        },
    )


def _resolve_reset_credits(
    *,
    deps: CollectorDeps,
    now: datetime,
    force: bool,
) -> tuple[Any | None, dict[str, Any]]:
    cached = cache_helpers.read_json_file(cache_helpers.reset_credits_path())
    if not force and _reset_cache_fresh(cached, now=now):
        return _read_reset_credits_cache(now)

    reset_raw, reset_source = deps["run_reset_credits_command"]()
    if reset_raw is not None:
        reset_source = reset_source if isinstance(reset_source, dict) else {}
        _write_reset_credits_cache(raw_reset_credits=reset_raw, reset_source=reset_source, now=now)
        return reset_raw, reset_source

    cached_raw, cached_source = _read_reset_credits_cache(now)
    if cached_raw is not None:
        cached_source["last_error"] = (reset_source or {}).get("last_error")
        cached_source["snapshot_source"] = "previous_reset_credits_cache"
        return cached_raw, cached_source
    return reset_raw, reset_source if isinstance(reset_source, dict) else {}


def build_latest_artifacts(
    *,
    raw_usage: Any,
    raw_reset_credits: Any,
    usage_source: dict[str, Any],
    reset_source: dict[str, Any],
    history_rows: list[dict[str, Any]],
    now: datetime,
    deps: CollectorDeps,
    previous_snapshot: dict[str, Any] | None = None,
    include_long_history: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    normalize_snapshot = deps["normalize_snapshot"]
    merge_reset_credits = deps["merge_reset_credits"]
    sanitize = deps["sanitize"]
    attach_history = deps.get("attach_history_to_accounts")

    normalized = normalize_snapshot(raw_usage, now=now, previous=previous_snapshot)
    accounts = normalized.get("accounts", [])
    if raw_reset_credits is not None and isinstance(accounts, list):
        accounts = merge_reset_credits(accounts, raw_reset_credits)
    usage_source = sanitize(usage_source)
    reset_source = sanitize(reset_source)
    if not isinstance(usage_source, dict):
        usage_source = {}
    if not isinstance(reset_source, dict):
        reset_source = {}

    latest = {
        "schema_version": cache_helpers.SCHEMA_VERSION,
        "ok": bool(normalized.get("ok", True)),
        "generated_at": normalized.get("generated_at") or _iso(now),
        "dashboard_poll_interval_seconds": cache_helpers.DASHBOARD_POLL_INTERVAL_SECONDS,
        "poll_interval_seconds": cache_helpers.DASHBOARD_POLL_INTERVAL_SECONDS,
        "collector_enabled": True,
        "collector_interval_seconds": cache_helpers.COLLECTOR_INTERVAL_SECONDS,
        "stale_after_seconds": cache_helpers.STALE_AFTER_SECONDS,
        "collector_status": "fresh",
        "snapshot_source": "collector_cache",
        "history_source": "collector_compact",
        "source": {
            "command": usage_source.get("command"),
            "available": bool(usage_source.get("available", True)),
            "last_error": usage_source.get("last_error"),
        },
        "reset_credits_source": {
            "command": reset_source.get("command"),
            "available": bool(reset_source.get("available", False)),
            "last_error": reset_source.get("last_error"),
            "cached": bool(reset_source.get("cached", False)),
            "cache_age_seconds": reset_source.get("cache_age_seconds"),
        },
        "accounts": accounts if isinstance(accounts, list) else [],
    }
    if callable(attach_history) and isinstance(latest.get("accounts"), list):
        latest["accounts"] = attach_history(latest["accounts"], history_rows, 240)
    if isinstance(latest.get("accounts"), list):
        latest["accounts"] = mark_recent_active_account(
            latest["accounts"],
            history_rows,
            now=now,
        )
        latest["last_active_account"] = last_active_account_summary(
            latest["accounts"],
            history_rows,
            now=now,
        )
    if include_long_history:
        latest_with_history = attach_long_history_to_snapshot(copy.deepcopy(latest), history_rows, now=now)
        return latest, latest_with_history
    return latest, None


def collect_once(
    *,
    deps: CollectorDeps,
    now: datetime | None = None,
    lock_timeout_seconds: float = 10.0,
    force_reset_credits: bool = False,
) -> dict[str, Any]:
    """Run one collector pass and update cache artifacts."""
    now_dt = now or datetime.now(timezone.utc)
    sanitize = deps["sanitize"]
    with cache_helpers.collector_lock(timeout_seconds=lock_timeout_seconds):
        run_usage_command = deps["run_usage_command"]
        load_history_rows = deps["load_history_rows"]
        load_recent_history_rows = deps.get("load_recent_history_rows") or load_history_rows
        append_history_row = deps.get("append_history_row") or deps.get("append_history_snapshot")
        history_prune_due = deps.get("history_prune_due")
        mark_history_pruned = deps.get("mark_history_pruned")

        raw_usage, usage_source = run_usage_command()
        if raw_usage is None:
            error = None
            if isinstance(usage_source, dict):
                error = usage_source.get("last_error")
            error = sanitize(error or "Codex usage wrapper did not return data.")
            status = _status(state="error", now=now_dt, last_error=str(error), snapshot_source="previous_good_cache")
            previous = cache_helpers.read_json_file(cache_helpers.latest_with_history_path())
            if previous and previous.get("generated_at"):
                status["collector_last_success_at"] = previous.get("generated_at")
            cache_helpers.write_json_atomic(cache_helpers.collector_status_path(), status)
            if previous:
                previous = copy.deepcopy(previous)
                previous.update(status)
                previous["ok"] = True
                return previous
            warming = {
                "schema_version": cache_helpers.SCHEMA_VERSION,
                "ok": False,
                "generated_at": _iso(now_dt),
                "accounts": [],
                **status,
            }
            warming["collector_status"] = "warming"
            cache_helpers.write_json_atomic(cache_helpers.latest_with_history_path(), warming)
            return warming

        reset_raw, reset_source = _resolve_reset_credits(
            deps=deps,
            now=now_dt,
            force=force_reset_credits,
        )
        previous_recent_rows = load_recent_history_rows(now=now_dt)
        if not isinstance(previous_recent_rows, list):
            previous_recent_rows = []
        previous_snapshot = previous_recent_rows[-1] if previous_recent_rows else None
        normalized_for_history = deps["normalize_snapshot"](
            raw_usage,
            now=now_dt,
            previous=previous_snapshot,
        )
        if reset_raw is not None and isinstance(normalized_for_history.get("accounts"), list):
            normalized_for_history["accounts"] = deps["merge_reset_credits"](
                normalized_for_history["accounts"], reset_raw
            )
        if callable(append_history_row):
            new_row_or_rows = append_history_row(normalized_for_history, now=now_dt)
        else:
            new_row_or_rows = load_history_rows(now=now_dt, prune=False)
        if isinstance(new_row_or_rows, list):
            recent_rows = new_row_or_rows
        elif isinstance(new_row_or_rows, dict):
            recent_rows = [*previous_recent_rows, new_row_or_rows]
        else:
            recent_rows = previous_recent_rows

        latest, _unused = build_latest_artifacts(
            raw_usage=raw_usage,
            raw_reset_credits=reset_raw,
            usage_source=usage_source if isinstance(usage_source, dict) else {},
            reset_source=reset_source if isinstance(reset_source, dict) else {},
            history_rows=recent_rows,
            now=now_dt,
            deps=deps,
            previous_snapshot=previous_snapshot,
            include_long_history=False,
        )
        status = _status(state="fresh", now=now_dt, last_success_at=latest["generated_at"])
        latest.update(status)
        cache_helpers.write_json_atomic(cache_helpers.latest_path(), latest)

        long_history_due = force_reset_credits
        long_history_existing = cache_helpers.read_json_file(cache_helpers.latest_with_history_path())
        long_generated = _parse_dt((long_history_existing or {}).get("generated_at"))
        if long_generated is None:
            long_history_due = True
        elif (now_dt - long_generated).total_seconds() >= cache_helpers.LONG_HISTORY_REFRESH_SECONDS:
            long_history_due = True

        latest_with_history: dict[str, Any] | None = None
        if long_history_due:
            prune = bool(history_prune_due(now_dt)) if callable(history_prune_due) else False
            full_history_rows = load_history_rows(now=now_dt, prune=prune)
            if prune and callable(mark_history_pruned):
                mark_history_pruned(now_dt, rows=len(full_history_rows))
            latest_with_history = attach_long_history_to_snapshot(copy.deepcopy(latest), full_history_rows, now=now_dt)
            latest_with_history.update(status)
            cache_helpers.write_json_atomic(cache_helpers.latest_with_history_path(), latest_with_history)

        cache_helpers.write_json_atomic(cache_helpers.collector_status_path(), status)
        return latest_with_history or latest


def _deps_from_plugin_api() -> CollectorDeps:
    import plugin_api  # type: ignore

    return {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": plugin_api.run_usage_command,
        "run_reset_credits_command": plugin_api.run_reset_credits_command,
        "append_history_snapshot": plugin_api.append_history_snapshot,
        "append_history_row": plugin_api.append_history_row,
        "load_history_rows": plugin_api.load_history_rows,
        "load_recent_history_rows": plugin_api.load_recent_history_rows,
        "history_prune_due": plugin_api.history_prune_due,
        "mark_history_pruned": plugin_api.mark_history_pruned,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Codex Usage dashboard collector")
    parser.add_argument("command", choices=["once"], help="collector command to run")
    args = parser.parse_args(argv)
    if args.command == "once":
        try:
            result = collect_once(deps=_deps_from_plugin_api())
        except cache_helpers.CacheLockTimeout:
            print("skipped_locked")
            return 0
        print(result.get("collector_status") or "ok")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
