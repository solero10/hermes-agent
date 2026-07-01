"""Background collector core for the Codex Usage dashboard."""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Allow script execution from the dashboard directory without installing a package.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import cache as cache_helpers  # noqa: E402
from history_contract import attach_long_history_to_snapshot  # noqa: E402

CollectorDeps = dict[str, Callable[..., Any]]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def build_latest_artifacts(
    *,
    raw_usage: Any,
    raw_reset_credits: Any,
    usage_source: dict[str, Any],
    reset_source: dict[str, Any],
    history_rows: list[dict[str, Any]],
    now: datetime,
    deps: CollectorDeps,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalize_snapshot = deps["normalize_snapshot"]
    merge_reset_credits = deps["merge_reset_credits"]
    sanitize = deps["sanitize"]
    attach_history = deps.get("attach_history_to_accounts")

    normalized = normalize_snapshot(raw_usage, now=now, previous=None)
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
        "dashboard_poll_interval_seconds": 30,
        "poll_interval_seconds": 30,
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
        },
        "accounts": accounts if isinstance(accounts, list) else [],
    }
    if callable(attach_history) and isinstance(latest.get("accounts"), list):
        latest["accounts"] = attach_history(latest["accounts"], history_rows, 240)
    latest_with_history = attach_long_history_to_snapshot(copy.deepcopy(latest), history_rows, now=now)
    return latest, latest_with_history


def collect_once(
    *,
    deps: CollectorDeps,
    now: datetime | None = None,
    lock_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Run one collector pass and update cache artifacts."""
    now_dt = now or datetime.now(timezone.utc)
    sanitize = deps["sanitize"]
    with cache_helpers.collector_lock(timeout_seconds=lock_timeout_seconds):
        run_usage_command = deps["run_usage_command"]
        run_reset_credits_command = deps["run_reset_credits_command"]
        append_history_snapshot = deps["append_history_snapshot"]
        load_history_rows = deps["load_history_rows"]

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

        reset_raw, reset_source = run_reset_credits_command()
        normalized_for_history = deps["normalize_snapshot"](raw_usage, now=now_dt, previous=None)
        if reset_raw is not None and isinstance(normalized_for_history.get("accounts"), list):
            normalized_for_history["accounts"] = deps["merge_reset_credits"](
                normalized_for_history["accounts"], reset_raw
            )
        history_rows = append_history_snapshot(normalized_for_history, now=now_dt)
        if not isinstance(history_rows, list):
            history_rows = load_history_rows(now=now_dt, prune=False)

        latest, latest_with_history = build_latest_artifacts(
            raw_usage=raw_usage,
            raw_reset_credits=reset_raw,
            usage_source=usage_source if isinstance(usage_source, dict) else {},
            reset_source=reset_source if isinstance(reset_source, dict) else {},
            history_rows=history_rows,
            now=now_dt,
            deps=deps,
        )
        status = _status(state="fresh", now=now_dt, last_success_at=latest["generated_at"])
        latest.update(status)
        latest_with_history.update(status)
        cache_helpers.write_json_atomic(cache_helpers.latest_path(), latest)
        cache_helpers.write_json_atomic(cache_helpers.latest_with_history_path(), latest_with_history)
        cache_helpers.write_json_atomic(cache_helpers.collector_status_path(), status)
        return latest_with_history


def _deps_from_plugin_api() -> CollectorDeps:
    import plugin_api  # type: ignore

    return {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": plugin_api.run_usage_command,
        "run_reset_credits_command": plugin_api.run_reset_credits_command,
        "append_history_snapshot": plugin_api.append_history_snapshot,
        "load_history_rows": plugin_api.load_history_rows,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Codex Usage dashboard collector")
    parser.add_argument("command", choices=["once"], help="collector command to run")
    args = parser.parse_args(argv)
    if args.command == "once":
        result = collect_once(deps=_deps_from_plugin_api())
        print(result.get("collector_status") or "ok")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
