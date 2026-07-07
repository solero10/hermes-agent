"""Focused backend tests for the Codex Usage dashboard plugin."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "codex_usage_monitor"
    / "dashboard"
    / "plugin_api.py"
)
MANIFEST_PATH = PLUGIN_MODULE_PATH.with_name("manifest.json")
PLUGIN_YAML_PATH = PLUGIN_MODULE_PATH.parents[1] / "plugin.yaml"
FRONTEND_JS_PATH = PLUGIN_MODULE_PATH.with_name("dist") / "index.js"
FRONTEND_CSS_PATH = PLUGIN_MODULE_PATH.with_name("dist") / "style.css"
SYSTEMD_DIR = PLUGIN_MODULE_PATH.with_name("systemd")


@pytest.fixture
def plugin_api(tmp_path, monkeypatch):
    """Load the plugin module fresh with an isolated Hermes home."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    spec = importlib.util.spec_from_file_location(
        f"codex_usage_monitor_plugin_api_test_{id(tmp_path)}",
        PLUGIN_MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client(plugin_api) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/codex_usage_monitor")
    return TestClient(app)


def _raw_snapshot(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "accounts": [
            {
                "stored_label": "Acct One",
                "label": "Primary Codex",
                "display_label": "Acct A",
                "priority": 1,
                "is_current": True,
                "is_next_eligible": False,
                "auth_status": "ok",
                "plan_type": "plus",
                "windows": [
                    {
                        "key": "primary_window",
                        "used_percent": 30,
                        "reset_at": (now + timedelta(hours=2)).isoformat(),
                        "reset_at_local": "Jan 1, 02:00",
                    },
                    {
                        "key": "secondary_window",
                        "remaining_percent": 80,
                        "reset_at": (now + timedelta(days=4)).isoformat(),
                    },
                ],
            }
        ],
    }


def _raw_reset_snapshot(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "accounts": [
            {
                "stored_label": "Acct One",
                "label": "Primary Codex",
                "priority": 1,
                "available_count": 2,
                "total_earned_count": 3,
                "credits": [
                    {
                        "id_short": "RateLimitResetCredit-secret-fragment",
                        "credit_id": "must-not-leak",
                        "status": "available",
                        "reset_type": "codex_rate_limits",
                        "granted_at": now.isoformat(),
                        "granted_at_local": "Jan 1, 00:00 UTC",
                        "expires_at": (now + timedelta(days=30)).isoformat(),
                        "expires_at_local": "Jan 31, 00:00 UTC",
                        "title": "One free rate limit reset",
                        "description": "Thanks for using Codex!",
                    },
                    {
                        "status": "redeemed",
                        "id_short": "redeemed-secret",
                        "expires_at": (now + timedelta(days=20)).isoformat(),
                    },
                ],
            }
        ],
    }


def test_dynamic_module_load_exports_router(plugin_api):
    assert plugin_api.PLUGIN_MODULE_PATH if hasattr(plugin_api, "PLUGIN_MODULE_PATH") else True
    assert plugin_api.router is not None
    route_paths = {getattr(route, "path", None) for route in plugin_api.router.routes}
    assert "/snapshot" in route_paths
    assert "/refresh" in route_paths


def test_manifest_registers_expected_dashboard_plugin():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest == {
        "name": "codex_usage_monitor",
        "label": "Codex Usage",
        "description": "Real-time ChatGPT/Codex OAuth account usage monitor with 5-hour and weekly quota charts",
        "icon": "Activity",
        "version": "0.1.0",
        "tab": {"path": "/codex-usage", "position": "after:analytics"},
        "entry": "dist/index.js?v=20260704-session-request-grouping-v1",
        "css": "dist/style.css?v=20260704-session-request-grouping-v1",
        "api": "plugin_api.py",
    }

    plugin_yaml = PLUGIN_YAML_PATH.read_text(encoding="utf-8")
    assert "name: codex_usage_monitor" in plugin_yaml
    assert "kind: dashboard" in plugin_yaml
    assert "version: 0.1.0" in plugin_yaml
    assert "author: Hermes Agent" in plugin_yaml


def test_systemd_runner_assets_are_profile_safe_and_secret_free():
    service = (SYSTEMD_DIR / "hermes-codex-usage-monitor.service").read_text(encoding="utf-8")
    timer = (SYSTEMD_DIR / "hermes-codex-usage-monitor.timer").read_text(encoding="utf-8")
    readme = (SYSTEMD_DIR / "README.md").read_text(encoding="utf-8")
    combined = "\n".join([service, timer, readme])

    assert "collector.py once" in service
    assert "venv/bin/python" in service
    assert "Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin" in service
    assert "OnUnitActiveSec=15s" in timer
    assert "PYTHONPATH=%h/.hermes/hermes-agent" in service
    assert "WorkingDirectory=%h/.hermes/hermes-agent" in service
    assert "%h" in service
    assert "/home/kernk" not in combined
    assert "Authorization" not in combined
    assert "access_token" not in combined
    assert "refresh_token" not in combined
    assert "systemctl --user enable --now" in readme


def test_dashboard_poll_interval_is_ten_seconds_and_collector_timer_is_fifteen_seconds(plugin_api):
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    cache_source = PLUGIN_MODULE_PATH.with_name("cache.py").read_text(encoding="utf-8")
    collector_source = PLUGIN_MODULE_PATH.with_name("collector.py").read_text(encoding="utf-8")
    timer = (SYSTEMD_DIR / "hermes-codex-usage-monitor.timer").read_text(encoding="utf-8")
    readme = (SYSTEMD_DIR / "README.md").read_text(encoding="utf-8")

    assert plugin_api.POLL_INTERVAL_SECONDS == 10
    assert plugin_api.CACHE_TTL_SECONDS == 10
    assert plugin_api.cache_helpers.DASHBOARD_POLL_INTERVAL_SECONDS == 10
    assert plugin_api.cache_helpers.COLLECTOR_INTERVAL_SECONDS == 15
    assert plugin_api.cache_helpers.STALE_AFTER_SECONDS == 60
    assert "const POLL_MS = 10000" in frontend
    assert "Polls every 10 seconds" in frontend
    assert "Collector checks quota every 15 seconds" in frontend
    assert "DASHBOARD_POLL_INTERVAL_SECONDS = 10" in cache_source
    assert "COLLECTOR_INTERVAL_SECONDS = 15" in cache_source
    assert "STALE_AFTER_SECONDS = 60" in cache_source
    assert '"dashboard_poll_interval_seconds": cache_helpers.DASHBOARD_POLL_INTERVAL_SECONDS' in collector_source
    assert '"poll_interval_seconds": cache_helpers.DASHBOARD_POLL_INTERVAL_SECONDS' in collector_source
    assert "OnUnitActiveSec=15s" in timer
    assert "OnUnitActiveSec=60s" not in timer
    assert "collector every 15 seconds" in timer
    assert "collector every 15 seconds" in readme


def test_cache_helpers_are_profile_safe_and_atomic(plugin_api, tmp_path):
    cache_path = plugin_api.cache_helpers.latest_path()
    assert str(cache_path).startswith(str(tmp_path / ".hermes"))
    assert str(cache_path).endswith("codex-usage-monitor/latest.json")

    plugin_api.cache_helpers.write_json_atomic(cache_path, {"ok": True, "schema_version": 1})
    assert plugin_api.cache_helpers.read_json_file(cache_path) == {"ok": True, "schema_version": 1}

    def reject(_data):
        raise ValueError("reject candidate")

    with pytest.raises(ValueError):
        plugin_api.cache_helpers.write_json_atomic(cache_path, {"ok": False}, validator=reject)
    assert plugin_api.cache_helpers.read_json_file(cache_path) == {"ok": True, "schema_version": 1}


def test_cache_helpers_include_reset_and_maintenance_paths(plugin_api, tmp_path):
    assert str(plugin_api.cache_helpers.reset_credits_path()).startswith(str(tmp_path / ".hermes"))
    assert str(plugin_api.cache_helpers.reset_credits_path()).endswith("codex-usage-monitor/reset_credits.json")
    assert str(plugin_api.cache_helpers.history_maintenance_path()).endswith("codex-usage-monitor/history_maintenance.json")
    assert plugin_api.cache_helpers.RESET_CREDITS_INTERVAL_SECONDS == 30 * 60
    assert plugin_api.cache_helpers.LONG_HISTORY_REFRESH_SECONDS == 5 * 60
    assert plugin_api.cache_helpers.HISTORY_RECENT_SECONDS == 5 * 60


def test_collector_main_skips_overlapping_runs_without_failing(plugin_api, monkeypatch, capsys):
    def locked_collect_once(*_args, **_kwargs):
        raise plugin_api.cache_helpers.CacheLockTimeout("already running")

    monkeypatch.setattr(plugin_api.collector_core, "collect_once", locked_collect_once)

    assert plugin_api.collector_core.main(["once"]) == 0
    assert capsys.readouterr().out.strip() == "skipped_locked"


def test_parse_dt_returns_timezone_aware_utc(plugin_api):
    parsed = plugin_api.parse_dt("2026-01-01T12:34:56Z")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.isoformat() == "2026-01-01T12:34:56+00:00"
    assert plugin_api.parse_dt("not a date") is None


def test_normalize_strips_token_fields_and_maps_windows(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw = _raw_snapshot(now)
    raw["access_token"] = "root-secret-token"
    raw["error"] = 'Authorization: Bearer abcdef...890'
    raw["accounts"][0]["refresh_token"] = "refresh-secret"
    raw["accounts"][0]["headers"] = {"Authorization": "Bearer should-not-leak"}
    raw["accounts"][0]["nested"] = {"api_key": "api-secret", "safe": "kept"}

    normalized = plugin_api.normalize_snapshot(raw, now=now)
    payload = json.dumps(normalized)

    assert "root-secret-token" not in payload
    assert "refresh-secret" not in payload
    assert "should-not-leak" not in payload
    assert "api-secret" not in payload
    assert "access_token" not in payload
    assert "refresh_token" not in payload
    assert "api_key" not in payload
    assert "abcdef" not in payload
    assert "[REDACTED]" in normalized["error"]

    account = normalized["accounts"][0]
    assert account["id"] == "acct-one"
    assert account["display_label"] == "Acct A"
    assert account["is_current"] is True
    assert account["is_next_eligible"] is False
    assert account["auth_status"] == "ok"
    assert account["auth_exhausted"] is False
    assert account["window_exhausted"] is False
    assert account["exhausted_windows"] == []
    assert account["exhausted_reason"] is None
    assert account["cooldown_seconds_left"] is None
    assert account["cooldown_reset_at"] is None
    assert account["auth_exhausted_until"] is None
    assert account["is_exhausted"] is False
    assert account["plan_type"] == "plus"
    assert set(account["windows"]) == {"five_hour", "weekly"}
    five_hour = account["windows"]["five_hour"]
    assert five_hour["remaining_percent"] == 70.0
    assert five_hour["seconds_left"] == 2 * 60 * 60
    assert "pace_gap_percent" in five_hour
    assert five_hour["on_pace_remaining_percent"] == 40.0
    assert five_hour["delta_remaining_percent"] is None
    assert five_hour["pace_state"] == "under"
    assert account["windows"]["weekly"]["used_percent"] == 20.0

    reauth_raw = _raw_snapshot(now)
    reauth_raw["accounts"][0]["auth_status"] = "reauth_required"
    reauth_account = plugin_api.normalize_snapshot(reauth_raw, now=now)["accounts"][0]
    assert reauth_account["error"] == "Re-auth required"

    exhausted_raw = _raw_snapshot(now)
    exhausted_raw["accounts"][0]["auth_status"] = "exhausted"
    exhausted_raw["accounts"][0]["auth_exhausted"] = True
    exhausted_raw["accounts"][0]["auth_exhausted_until_local"] = "2026-01-01 02:00 PST"
    exhausted_account = plugin_api.normalize_snapshot(exhausted_raw, now=now)["accounts"][0]
    assert exhausted_account["auth_exhausted"] is True
    assert exhausted_account["is_exhausted"] is True
    assert exhausted_account["exhausted_reason"] == "Account exhausted"
    assert exhausted_account["auth_exhausted_until"] == "2026-01-01 02:00 PST"
    assert exhausted_account["cooldown_reset_at"] == "2026-01-01 02:00 PST"

    weekly_exhausted_raw = _raw_snapshot(now)
    weekly_exhausted_raw["accounts"][0]["windows"][1]["remaining_percent"] = 0
    weekly_exhausted = plugin_api.normalize_snapshot(weekly_exhausted_raw, now=now)["accounts"][0]
    assert weekly_exhausted["auth_status"] == "ok"
    assert weekly_exhausted["auth_exhausted"] is False
    assert weekly_exhausted["window_exhausted"] is True
    assert weekly_exhausted["is_exhausted"] is True
    assert weekly_exhausted["exhausted_reason"] == "Weekly exhausted"
    assert weekly_exhausted["exhausted_windows"] == [{"key": "weekly", "label": "Weekly"}]
    assert weekly_exhausted["cooldown_window_label"] == "Weekly"
    assert weekly_exhausted["cooldown_seconds_left"] == 4 * 24 * 60 * 60
    assert weekly_exhausted["windows"]["weekly"]["is_exhausted"] is True
    assert weekly_exhausted["windows"]["five_hour"].get("is_exhausted") is None


def test_one_percent_remaining_is_not_normalized_to_one_hundred(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw = _raw_snapshot(now)
    raw["accounts"][0]["windows"][1]["remaining_percent"] = 1.0
    raw["accounts"][0]["windows"][1]["used_percent"] = 99

    weekly = plugin_api.normalize_snapshot(raw, now=now)["accounts"][0]["windows"]["weekly"]

    assert weekly["remaining_percent"] == 1.0
    assert weekly["used_percent"] == 99.0


def test_inconsistent_cached_percent_pair_is_repaired_for_history(plugin_api):
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = period_start + timedelta(days=7)
    rows = [
        {
            "generated_at": (period_start + timedelta(minutes=10)).isoformat(),
            "accounts": [
                {
                    "id": "dads-chatgpt",
                    "windows": {
                        "weekly": {
                            "remaining_percent": 100.0,
                            "used_percent": 99.0,
                            "reset_at": reset_at.isoformat(),
                            "period_seconds": 7 * 24 * 60 * 60,
                        }
                    },
                }
            ],
        }
    ]

    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "dads-chatgpt",
                "windows": {
                    "weekly": {
                        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 7 * 24 * 60 * 60,
                        "remaining_percent": 1.0,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )

    history = attached[0]["windows"]["weekly"]["history"]
    assert history[-1]["remaining_percent"] == 1.0
    assert history[-1]["used_percent"] == 99.0
    assert plugin_api.collector_core._remaining_percent(
        {"remaining_percent": 100.0, "used_percent": 99.0}
    ) == 1.0


def _hermes_event(
    *,
    account_match_keys: list[str],
    fallback_match_keys: list[str] | None = None,
    session_id: str = "session-123",
    title_snapshot: str = "Fallback title",
    status: str = "in_flight",
    started_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-01T00:00:02Z",
    completed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "openai-codex",
        "api_mode": "codex_responses",
        "model": "gpt-5.5-codex",
        "session_id": session_id,
        "title_snapshot": title_snapshot,
        "credential_label": "Primary Codex",
        "account_match_keys": account_match_keys,
        "fallback_match_keys": fallback_match_keys or [],
        "match_confidence": "label",
        "status": status,
        "started_at": started_at,
        "updated_at": updated_at,
        "completed_at": completed_at,
    }


def test_hermes_session_attribution_attaches_only_matching_strong_keys(plugin_api, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)
    monkeypatch.setattr(plugin_api, "_session_title", lambda session_id: "Live DB title")

    attached = plugin_api.attach_hermes_session_attribution(
        snapshot,
        now_dt=now,
        events=[_hermes_event(account_match_keys=["Primary Codex"], fallback_match_keys=["priority-1"])],
    )

    account = attached["accounts"][0]
    assert account["hermes_session_count"] == 1
    assert account["hermes_sessions"][0]["session_id"] == "session-123"
    assert account["hermes_sessions"][0]["title"] == "Live DB title"
    assert account["hermes_sessions"][0]["match_keys"] == ["primary-codex"]
    assert "event_id" not in account["hermes_sessions"][0]


def test_hermes_session_attribution_groups_recent_requests_by_session(plugin_api, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)
    monkeypatch.setattr(plugin_api, "_session_title", lambda session_id: f"Live {session_id}")

    attached = plugin_api.attach_hermes_session_attribution(
        snapshot,
        now_dt=now,
        events=[
            _hermes_event(
                account_match_keys=["Primary Codex"],
                session_id="session-123",
                status="ok",
                started_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:05Z",
                completed_at="2026-01-01T00:00:05Z",
            ),
            _hermes_event(
                account_match_keys=["Primary Codex"],
                session_id="session-123",
                status="ok",
                started_at="2026-01-01T00:00:06Z",
                updated_at="2026-01-01T00:00:08Z",
                completed_at="2026-01-01T00:00:08Z",
            ),
            _hermes_event(
                account_match_keys=["Primary Codex"],
                session_id="session-123",
                status="in_flight",
                started_at="2026-01-01T00:00:09Z",
                updated_at="2026-01-01T00:00:09Z",
            ),
            _hermes_event(
                account_match_keys=["Primary Codex"],
                session_id="session-456",
                title_snapshot="Other session",
                status="ok",
                started_at="2026-01-01T00:00:03Z",
                updated_at="2026-01-01T00:00:04Z",
                completed_at="2026-01-01T00:00:04Z",
            ),
        ],
    )

    account = attached["accounts"][0]
    assert account["hermes_session_count"] == 2
    assert account["hermes_request_count"] == 4
    assert [item["session_id"] for item in account["hermes_sessions"]] == ["session-123", "session-456"]

    active_session = account["hermes_sessions"][0]
    assert active_session["title"] == "Live session-123"
    assert active_session["status"] == "in_flight"
    assert active_session["recent_request_count"] == 3
    assert active_session["updated_at"] == "2026-01-01T00:00:09Z"
    assert active_session["match_keys"] == ["primary-codex"]


def test_hermes_session_attribution_rejects_no_match_and_fallback_only(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    no_match = plugin_api.attach_hermes_session_attribution(
        plugin_api.normalize_snapshot(_raw_snapshot(now), now=now),
        now_dt=now,
        events=[_hermes_event(account_match_keys=["Other Codex"])],
    )
    assert "hermes_sessions" not in no_match["accounts"][0]

    fallback_only = plugin_api.attach_hermes_session_attribution(
        plugin_api.normalize_snapshot(_raw_snapshot(now), now=now),
        now_dt=now,
        events=[_hermes_event(account_match_keys=["priority-1"], fallback_match_keys=["priority-1"])],
    )
    assert "hermes_sessions" not in fallback_only["accounts"][0]


def test_hermes_session_attribution_is_runtime_only_and_not_history(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)
    plugin_api.attach_hermes_session_attribution(
        snapshot,
        now_dt=now,
        events=[_hermes_event(account_match_keys=["Primary Codex"])],
    )

    payload = json.dumps(plugin_api._history_row_from_snapshot(snapshot))
    assert "hermes_sessions" not in payload
    assert "Fallback title" not in payload


def test_snapshot_reattaches_hermes_session_attribution_from_cache(plugin_api, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin_api, "_utcnow", lambda: now)
    monkeypatch.setattr(
        plugin_api,
        "read_recent_events",
        lambda **_kwargs: [_hermes_event(account_match_keys=["Primary Codex"])],
    )
    base_snapshot = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)
    assert "hermes_sessions" not in base_snapshot["accounts"][0]
    plugin_api.cache_helpers.write_json_atomic(
        plugin_api.cache_helpers.latest_with_history_path(),
        base_snapshot,
    )
    plugin_api._SNAPSHOT_CACHE = None

    snapshot = plugin_api.build_snapshot(history_points=240, force=False)

    assert snapshot["accounts"][0]["hermes_sessions"][0]["title"] == "Fallback title"


def test_reset_credit_normalization_attaches_only_safe_available_credit_info(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    accounts = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)["accounts"]

    merged = plugin_api.merge_reset_credits(accounts, _raw_reset_snapshot(now))
    payload = json.dumps(merged)

    assert "must-not-leak" not in payload
    assert "secret-fragment" not in payload
    assert "redeemed-secret" not in payload
    assert "id_short" not in payload
    assert "credit_id" not in payload

    reset_info = merged[0]["reset_credits"]
    assert reset_info["available_count"] == 2
    assert reset_info["total_earned_count"] == 3
    assert len(reset_info["credits"]) == 1
    assert reset_info["credits"][0]["status"] == "available"
    assert reset_info["credits"][0]["expires_at_local"] == "Jan 31, 00:00 UTC"
    assert reset_info["credits"][0]["title"] == "One free rate limit reset"


def test_pace_state_weekly_halfway_examples(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period = 7 * 24 * 60 * 60
    reset_at = now + timedelta(days=3.5)

    assert plugin_api.compute_pace_state(60, reset_at, period, now) == "under"
    assert plugin_api.compute_pace_state(40, reset_at, period, now) == "over"
    assert plugin_api.compute_pace_state(50, reset_at, period, now) == "on"


def test_active_inference_marks_largest_drop_and_ignores_reset_increase(plugin_api):
    previous = {
        "acct-a": {"windows": {"five_hour": {"remaining_percent": 90}}},
        "acct-b": {
            "windows": {
                "five_hour": {"remaining_percent": 60},
                "weekly": {"remaining_percent": 80},
            }
        },
        "acct-c": {"windows": {"five_hour": {"remaining_percent": 10}}},
    }
    accounts = [
        {"id": "acct-a", "windows": {"five_hour": {"remaining_percent": 87}}},  # drop 3
        {
            "id": "acct-b",
            "windows": {
                "five_hour": {"remaining_percent": 50},  # drop 10
                "weekly": {"remaining_percent": 68},  # drop 12, largest
            },
        },
        {"id": "acct-c", "windows": {"five_hour": {"remaining_percent": 99}}},  # reset/increase
    ]

    result = plugin_api.infer_active_accounts(previous, accounts)

    assert [account["active_now"] for account in result] == [False, True, False]
    assert result[0]["active_reason"] is None
    assert result[2]["active_reason"] is None
    assert result[1]["active_drop_percent"] == 12
    assert "Weekly" in result[1]["active_reason"]
    assert "dropped 12%" in result[1]["active_reason"]
    assert result[0]["windows"]["five_hour"]["delta_remaining_percent"] == -3
    assert result[1]["windows"]["five_hour"]["delta_remaining_percent"] == -10
    assert result[1]["windows"]["weekly"]["delta_remaining_percent"] == -12
    assert result[2]["windows"]["five_hour"]["delta_remaining_percent"] == 89


def test_collector_marks_active_account_from_previous_history_sample(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    previous_raw = _raw_snapshot(start)
    current_raw = json.loads(json.dumps(previous_raw))
    current_raw["generated_at"] = (start + timedelta(seconds=15)).isoformat()
    current_raw["accounts"][0]["windows"][0]["used_percent"] = 34

    previous_snapshot = plugin_api.normalize_snapshot(previous_raw, now=start)
    plugin_api.append_history_snapshot(previous_snapshot, now=start)

    def fake_run_usage_command():
        return current_raw, {"command": "fake usage", "available": True}

    def fake_run_reset_credits_command():
        return None, {"command": None, "available": False}

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_snapshot": plugin_api.append_history_snapshot,
        "load_history_rows": plugin_api.load_history_rows,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    result = plugin_api.collector_core.collect_once(
        deps=deps,
        now=start + timedelta(seconds=15),
    )
    account = result["accounts"][0]

    assert account["active_now"] is True
    assert account["active_drop_percent"] == 4
    assert "5-hour remaining dropped 4% within the last 60 seconds" in account["active_reason"]
    assert result["last_active_account"]["label"] == "Primary Codex"
    assert result["last_active_account"]["window_label"] == "5-hour"
    assert result["last_active_account"]["drop_percent"] == 4
    assert result["last_active_account"]["active_now"] is True


def test_collector_keeps_recent_drop_active_across_stable_samples(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    before_drop = _raw_snapshot(start)
    dropped = json.loads(json.dumps(before_drop))
    dropped["generated_at"] = (start + timedelta(seconds=15)).isoformat()
    dropped["accounts"][0]["windows"][0]["used_percent"] = 35
    stable = json.loads(json.dumps(dropped))
    stable["generated_at"] = (start + timedelta(seconds=30)).isoformat()

    plugin_api.append_history_snapshot(
        plugin_api.normalize_snapshot(before_drop, now=start),
        now=start,
    )
    plugin_api.append_history_snapshot(
        plugin_api.normalize_snapshot(
            dropped,
            now=start + timedelta(seconds=15),
            previous=plugin_api.normalize_snapshot(before_drop, now=start),
        ),
        now=start + timedelta(seconds=15),
    )

    def fake_run_usage_command():
        return stable, {"command": "fake usage", "available": True}

    def fake_run_reset_credits_command():
        return None, {"command": None, "available": False}

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_snapshot": plugin_api.append_history_snapshot,
        "load_history_rows": plugin_api.load_history_rows,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    result = plugin_api.collector_core.collect_once(
        deps=deps,
        now=start + timedelta(seconds=30),
    )
    account = result["accounts"][0]

    assert account["active_now"] is True
    assert account["active_drop_percent"] == 5
    assert account["active_last_seen_at"] == "2026-01-01T00:00:15Z"
    assert result["last_active_account"]["seen_at"] == "2026-01-01T00:00:15Z"
    assert result["last_active_account"]["active_now"] is True


def test_collector_keeps_last_active_summary_after_pulse_expires(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    before_drop = _raw_snapshot(start)
    dropped = json.loads(json.dumps(before_drop))
    dropped["generated_at"] = (start + timedelta(seconds=15)).isoformat()
    dropped["accounts"][0]["windows"][0]["used_percent"] = 35
    stable = json.loads(json.dumps(dropped))
    stable["generated_at"] = (start + timedelta(seconds=120)).isoformat()

    previous_snapshot = plugin_api.normalize_snapshot(before_drop, now=start)
    plugin_api.append_history_snapshot(previous_snapshot, now=start)
    plugin_api.append_history_snapshot(
        plugin_api.normalize_snapshot(
            dropped,
            now=start + timedelta(seconds=15),
            previous=previous_snapshot,
        ),
        now=start + timedelta(seconds=15),
    )

    def fake_run_usage_command():
        return stable, {"command": "fake usage", "available": True}

    def fake_run_reset_credits_command():
        return None, {"command": None, "available": False}

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_snapshot": plugin_api.append_history_snapshot,
        "load_history_rows": plugin_api.load_history_rows,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    result = plugin_api.collector_core.collect_once(
        deps=deps,
        now=start + timedelta(seconds=120),
    )

    assert result["accounts"][0]["active_now"] is False
    assert result["last_active_account"]["label"] == "Primary Codex"
    assert result["last_active_account"]["drop_percent"] == 5
    assert result["last_active_account"]["active_now"] is False
    assert result["last_active_account"]["age_seconds"] == 105


def test_run_usage_command_available_semantics_and_sanitized_errors(plugin_api, monkeypatch):
    monkeypatch.setattr(plugin_api.shutil, "which", lambda binary: None)

    raw, source = plugin_api.run_usage_command()

    assert raw is None
    assert source["available"] is False
    assert source["command"] is None
    assert "No Codex usage wrapper command found" in source["last_error"]

    monkeypatch.setattr(
        plugin_api.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary == "husage" else None,
    )

    def fake_nonzero(command, **kwargs):
        return plugin_api.subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="Authorization: Bearer *** access_token=raw-secret-value",
        )

    monkeypatch.setattr(plugin_api.subprocess, "run", fake_nonzero)
    raw, source = plugin_api.run_usage_command()

    assert raw is None
    assert source["available"] is True
    assert source["command"] in {"husage --json usage --no-log", "husage usage --json --no-log"}
    assert "rawsecretbearer" not in source["last_error"]
    assert "raw-secret-value" not in source["last_error"]
    assert "[REDACTED]" in source["last_error"]

    def fake_invalid_json(command, **kwargs):
        return plugin_api.subprocess.CompletedProcess(
            command,
            0,
            stdout="not json token=raw-invalid-token sk-abc...mnop",
            stderr="",
        )

    monkeypatch.setattr(plugin_api.subprocess, "run", fake_invalid_json)
    raw, source = plugin_api.run_usage_command()

    assert raw is None
    assert source["available"] is True
    assert source["command"] in {"husage --json usage --no-log", "husage usage --json --no-log"}
    assert "raw-invalid-token" not in source["last_error"]
    assert "sk-abc...mnop" not in source["last_error"]
    assert "[REDACTED]" in source["last_error"]


def test_run_usage_command_requests_background_no_log_mode(plugin_api, monkeypatch):
    monkeypatch.setattr(
        plugin_api.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary == "husage" else None,
    )
    seen: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env") or {}
        return plugin_api.subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok": true, "accounts": []}',
            stderr="",
        )

    monkeypatch.setattr(plugin_api.subprocess, "run", fake_run)

    raw, source = plugin_api.run_usage_command()

    assert raw == {"ok": True, "accounts": []}
    assert source["available"] is True
    assert seen["env"].get("CODEX_USAGE_APPEND_LOG") == "0"
    assert seen["command"] == ["husage", "--json", "usage", "--no-log"]


def test_run_reset_credits_command_requests_background_no_log_mode(plugin_api, monkeypatch):
    monkeypatch.setattr(
        plugin_api.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary == "husage" else None,
    )
    seen: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env") or {}
        return plugin_api.subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok": true, "accounts": []}',
            stderr="",
        )

    monkeypatch.setattr(plugin_api.subprocess, "run", fake_run)

    raw, source = plugin_api.run_reset_credits_command()

    assert raw == {"ok": True, "accounts": []}
    assert source["available"] is True
    assert seen["env"].get("CODEX_USAGE_APPEND_LOG") == "0"
    assert seen["command"] == ["husage", "--json", "resets", "--no-log"]


def test_route_mounts_in_bare_fastapi_testclient_and_returns_json(plugin_api, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_build_snapshot(history_points: int = 240, force: bool = False):
        seen["history_points"] = history_points
        seen["force"] = force
        return {
            "ok": False,
            "generated_at": "2026-01-01T00:00:00Z",
            "poll_interval_seconds": plugin_api.POLL_INTERVAL_SECONDS,
            "cached": False,
            "age_seconds": 0,
            "source": {"command": None, "available": False, "last_error": "mocked"},
            "accounts": [],
        }

    monkeypatch.setattr(plugin_api, "build_snapshot", fake_build_snapshot)
    response = _client(plugin_api).get(
        "/api/plugins/codex_usage_monitor/snapshot?history_points=20&force=true"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["poll_interval_seconds"] == plugin_api.POLL_INTERVAL_SECONDS
    assert seen == {"history_points": 20, "force": True}


def test_refresh_endpoint_collects_and_writes_cached_normalized_accounts(plugin_api, monkeypatch):
    now = datetime.now(timezone.utc)

    def fake_run_usage_command():
        return _raw_snapshot(now), {
            "command": "husage --json usage",
            "available": True,
            "last_error": None,
        }

    def fake_run_reset_credits_command():
        return _raw_reset_snapshot(now), {
            "command": "husage --json resets",
            "available": True,
            "last_error": None,
        }

    monkeypatch.setattr(plugin_api, "run_usage_command", fake_run_usage_command)
    monkeypatch.setattr(plugin_api, "run_reset_credits_command", fake_run_reset_credits_command)
    response = _client(plugin_api).post("/api/plugins/codex_usage_monitor/refresh")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["cached"] is False
    assert data["collector_status"] == "fresh"
    assert data["snapshot_source"] == "collector_cache"
    assert data["history_source"] == "collector_compact"
    assert data["history_contract"]["compact_history_retention_days"] >= 183
    assert data["history_contract"]["raw_jsonl_retention_days"] == 30
    assert data["source"] == {
        "command": "husage --json usage",
        "available": True,
        "last_error": None,
    }
    assert data["reset_credits_source"] == {
        "command": "husage --json resets",
        "available": True,
        "last_error": None,
        "cached": False,
        "cache_age_seconds": None,
    }
    account = data["accounts"][0]
    assert account["id"] == "acct-one"
    assert account["windows"]["five_hour"]["remaining_percent"] == 70.0
    assert account["windows"]["weekly"]["remaining_percent"] == 80.0
    assert account["reset_credits"]["available_count"] == 2
    assert account["reset_credits"]["credits"][0]["expires_at_local"] == "Jan 31, 00:00 UTC"
    five_hour_history = account["windows"]["five_hour"]["history"]
    assert len(five_hour_history) == 2
    assert five_hour_history[0]["synthetic"] is True
    assert five_hour_history[0]["remaining_percent"] == 100.0
    assert five_hour_history[-1]["remaining_percent"] == 70.0
    assert set(account["windows"]["five_hour"]["long_history"]) == {"1h", "5h", "1d", "7d", "30d"}
    assert set(account["windows"]["weekly"]["long_history"]) == {"1w", "4w", "12w", "26w", "all"}
    assert (plugin_api.cache_helpers.latest_with_history_path()).exists()
    assert (plugin_api.cache_helpers.collector_status_path()).exists()


def test_normal_snapshot_uses_cache_and_does_not_shell_out(plugin_api, monkeypatch):
    calls = {"usage": 0, "resets": 0}
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def fake_run_usage_command():
        calls["usage"] += 1
        return _raw_snapshot(now), {
            "command": "husage --json usage",
            "available": True,
            "last_error": None,
        }

    def fake_run_reset_credits_command():
        calls["resets"] += 1
        return _raw_reset_snapshot(now), {
            "command": "husage --json resets",
            "available": True,
            "last_error": None,
        }

    monkeypatch.setattr(plugin_api, "run_usage_command", fake_run_usage_command)
    monkeypatch.setattr(plugin_api, "run_reset_credits_command", fake_run_reset_credits_command)

    refreshed = plugin_api.refresh_snapshot()
    assert refreshed["ok"] is True
    calls_after_refresh = dict(calls)
    first = plugin_api.build_snapshot(history_points=20)
    second = plugin_api.build_snapshot(history_points=20)

    assert calls_after_refresh == {"usage": 1, "resets": 1}
    assert calls == calls_after_refresh
    assert first["cached"] is True
    assert second["cached"] is True
    assert second["accounts"][0]["id"] == "acct-one"
    assert second["accounts"][0]["reset_credits"]["available_count"] == 2
    assert second["history_contract"]["presets"]["weekly"][-1] == "all"


def test_collector_reuses_reset_credits_cache_between_fast_usage_samples(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls = {"usage": 0, "resets": 0}

    def fake_run_usage_command():
        calls["usage"] += 1
        raw = _raw_snapshot(start + timedelta(seconds=15 * calls["usage"]))
        raw["accounts"][0]["windows"][0]["used_percent"] = 30 + calls["usage"]
        return raw, {"command": "husage --json usage --no-log", "available": True}

    def fake_run_reset_credits_command():
        calls["resets"] += 1
        return _raw_reset_snapshot(start), {"command": "husage --json resets --no-log", "available": True}

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_row": plugin_api.append_history_row,
        "load_recent_history_rows": plugin_api.load_recent_history_rows,
        "load_history_rows": plugin_api.load_history_rows,
        "history_prune_due": plugin_api.history_prune_due,
        "mark_history_pruned": plugin_api.mark_history_pruned,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    first = plugin_api.collector_core.collect_once(deps=deps, now=start)
    second = plugin_api.collector_core.collect_once(deps=deps, now=start + timedelta(seconds=15))

    assert calls == {"usage": 2, "resets": 1}
    assert first["accounts"][0]["reset_credits"]["available_count"] == 2
    assert second["accounts"][0]["reset_credits"]["available_count"] == 2
    assert second["reset_credits_source"].get("cached") is True


def test_collector_force_refresh_updates_reset_credits(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls = {"usage": 0, "resets": 0}

    def fake_run_usage_command():
        calls["usage"] += 1
        return _raw_snapshot(start), {"command": "husage --json usage --no-log", "available": True}

    def fake_run_reset_credits_command():
        calls["resets"] += 1
        raw = _raw_reset_snapshot(start)
        raw["accounts"][0]["available_count"] = calls["resets"]
        return raw, {"command": "husage --json resets --no-log", "available": True}

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_row": plugin_api.append_history_row,
        "load_recent_history_rows": plugin_api.load_recent_history_rows,
        "load_history_rows": plugin_api.load_history_rows,
        "history_prune_due": plugin_api.history_prune_due,
        "mark_history_pruned": plugin_api.mark_history_pruned,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    plugin_api.collector_core.collect_once(deps=deps, now=start)
    forced = plugin_api.collector_core.collect_once(
        deps=deps,
        now=start + timedelta(seconds=15),
        force_reset_credits=True,
    )

    assert calls == {"usage": 2, "resets": 2}
    assert forced["accounts"][0]["reset_credits"]["available_count"] == 2


def test_append_history_row_does_not_prune_full_history(plugin_api):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = plugin_api.normalize_snapshot(_raw_snapshot(now), now=now)

    row = plugin_api.append_history_row(snapshot, now=now)
    rows = plugin_api.load_recent_history_rows(now=now + timedelta(seconds=1), seconds=60)

    assert row["generated_at"] == "2026-01-01T00:00:00Z"
    assert len(rows) == 1
    assert rows[0]["generated_at"] == "2026-01-01T00:00:00Z"


def test_load_recent_history_rows_only_keeps_recent_tail(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for idx in range(20):
        sample_at = start + timedelta(seconds=15 * idx)
        raw = _raw_snapshot(sample_at)
        raw["accounts"][0]["windows"][0]["used_percent"] = 30 + idx
        plugin_api.append_history_row(
            plugin_api.normalize_snapshot(raw, now=sample_at),
            now=sample_at,
        )

    rows = plugin_api.load_recent_history_rows(
        now=start + timedelta(seconds=15 * 19),
        seconds=60,
        limit=10,
    )

    assert 1 <= len(rows) <= 10
    assert rows[-1]["generated_at"] == "2026-01-01T00:04:45Z"
    assert all(plugin_api.parse_dt(row["generated_at"]) >= start + timedelta(seconds=15 * 15) for row in rows)


def test_collector_writes_latest_each_tick_but_long_history_only_when_due(plugin_api):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls = {"usage": 0, "full_history": 0}

    def fake_run_usage_command():
        calls["usage"] += 1
        raw = _raw_snapshot(start + timedelta(seconds=15 * calls["usage"]))
        raw["accounts"][0]["windows"][0]["used_percent"] = 30 + calls["usage"]
        return raw, {"command": "husage --json usage --no-log", "available": True}

    def fake_run_reset_credits_command():
        return _raw_reset_snapshot(start), {"command": "husage --json resets --no-log", "available": True}

    original_load_history_rows = plugin_api.load_history_rows

    def counted_load_history_rows(*args, **kwargs):
        calls["full_history"] += 1
        return original_load_history_rows(*args, **kwargs)

    deps = {
        "normalize_snapshot": plugin_api.normalize_snapshot,
        "merge_reset_credits": plugin_api.merge_reset_credits,
        "sanitize": plugin_api.sanitize,
        "run_usage_command": fake_run_usage_command,
        "run_reset_credits_command": fake_run_reset_credits_command,
        "append_history_row": plugin_api.append_history_row,
        "load_recent_history_rows": plugin_api.load_recent_history_rows,
        "load_history_rows": counted_load_history_rows,
        "history_prune_due": plugin_api.history_prune_due,
        "mark_history_pruned": plugin_api.mark_history_pruned,
        "attach_history_to_accounts": plugin_api._attach_history_to_accounts,
    }

    first = plugin_api.collector_core.collect_once(deps=deps, now=start)
    second = plugin_api.collector_core.collect_once(deps=deps, now=start + timedelta(seconds=15))

    assert calls["usage"] == 2
    assert calls["full_history"] == 1
    assert first["generated_at"] != second["generated_at"]
    assert plugin_api.cache_helpers.latest_path().exists()
    assert plugin_api.cache_helpers.latest_with_history_path().exists()


def test_build_snapshot_uses_current_latest_and_cached_long_history(plugin_api):
    old_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new_time = old_time + timedelta(seconds=45)

    old_snapshot = plugin_api.normalize_snapshot(_raw_snapshot(old_time), now=old_time)
    old_with_history = plugin_api.attach_long_history_to_snapshot(
        old_snapshot,
        [plugin_api._history_row_from_snapshot(old_snapshot)],
        now=old_time,
    )
    plugin_api.cache_helpers.write_json_atomic(plugin_api.cache_helpers.latest_with_history_path(), old_with_history)

    new_raw = _raw_snapshot(new_time)
    new_raw["accounts"][0]["windows"][0]["used_percent"] = 45
    current_latest = plugin_api.normalize_snapshot(new_raw, now=new_time, previous=old_snapshot)
    current_latest["collector_status"] = "fresh"
    plugin_api.cache_helpers.write_json_atomic(plugin_api.cache_helpers.latest_path(), current_latest)

    plugin_api._SNAPSHOT_CACHE = None
    snapshot = plugin_api.build_snapshot(history_points=20)

    assert snapshot["generated_at"] == current_latest["generated_at"]
    assert snapshot["accounts"][0]["windows"]["five_hour"]["remaining_percent"] == 55.0
    assert "long_history" in snapshot["accounts"][0]["windows"]["five_hour"]
    assert snapshot["history_contract"]


def test_history_writes_sanitized_jsonl_and_downsampling_keeps_first_last(plugin_api, tmp_path):
    history_file = tmp_path / "history.jsonl"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = start + timedelta(hours=5)

    for idx in range(25):
        generated_at = start + timedelta(minutes=idx)
        snapshot = {
            "generated_at": generated_at.isoformat(),
            "accounts": [
                {
                    "id": "acct-one",
                    "label": "Acct One",
                    "access_token": "must-not-persist",
                    "windows": {
                        "five_hour": {
                            "used_percent": idx,
                            "remaining_percent": 100 - idx,
                            "reset_at": reset_at.isoformat(),
                            "period_seconds": 5 * 60 * 60,
                            "pace_state": "on",
                            "authorization": "Bearer must-not-persist",
                        }
                    },
                }
            ],
        }
        plugin_api.append_history_snapshot(snapshot, path=history_file, now=start)

    with history_file.open("a", encoding="utf-8") as handle:
        handle.write("{this is corrupt jsonl}\n")

    raw_history = history_file.read_text(encoding="utf-8")
    assert "must-not-persist" not in raw_history
    assert "access_token" not in raw_history
    assert "authorization" not in raw_history

    rows = plugin_api.load_history_rows(history_file, now=start + timedelta(hours=1), prune=False)
    assert len(rows) == 25

    accounts = [
        {
            "id": "acct-one",
            "windows": {"five_hour": {"history": []}},
        }
    ]
    attached = plugin_api._attach_history_to_accounts(accounts, rows, history_points=20)
    history = attached[0]["windows"]["five_hour"]["history"]

    assert len(history) == 20
    assert history[0]["generated_at"] == start.isoformat().replace("+00:00", "Z")
    assert history[-1]["generated_at"] == (start + timedelta(minutes=24)).isoformat().replace("+00:00", "Z")
    assert history[0]["remaining_percent"] == 100
    assert history[-1]["remaining_percent"] == 76


def test_history_missing_period_start_adds_100_percent_anchor(plugin_api, tmp_path):
    history_file = tmp_path / "history.jsonl"
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = period_start + timedelta(hours=5)
    first_sample = period_start + timedelta(hours=2)

    plugin_api.append_history_snapshot(
        {
            "generated_at": first_sample.isoformat(),
            "accounts": [
                {
                    "id": "acct-one",
                    "label": "Acct One",
                    "windows": {
                        "five_hour": {
                            "used_percent": 40,
                            "remaining_percent": 60,
                            "reset_at": reset_at.isoformat(),
                            "period_seconds": 5 * 60 * 60,
                            "pace_state": "over",
                        }
                    },
                }
            ],
        },
        path=history_file,
        now=first_sample,
    )

    rows = plugin_api.load_history_rows(history_file, now=first_sample, prune=False)
    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "acct-one",
                "windows": {
                    "five_hour": {
                        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 5 * 60 * 60,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )
    history = attached[0]["windows"]["five_hour"]["history"]

    assert len(history) == 2
    assert history[0] == {
        "generated_at": period_start.isoformat().replace("+00:00", "Z"),
        "used_percent": 0.0,
        "remaining_percent": 100.0,
        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
        "period_seconds": 5 * 60 * 60,
        "pace_state": "unknown",
        "synthetic": True,
        "source": "period_start_anchor",
    }
    assert history[1]["generated_at"] == first_sample.isoformat().replace("+00:00", "Z")
    assert history[1]["remaining_percent"] == 60


def test_history_excludes_previous_reset_sample_inside_period_start_tolerance(plugin_api):
    period_start = datetime(2026, 1, 1, 5, 15, 43, tzinfo=timezone.utc)
    previous_reset = period_start - timedelta(seconds=2)
    current_reset = period_start + timedelta(hours=5)
    rows = [
        {
            "generated_at": (period_start - timedelta(seconds=25)).isoformat(),
            "accounts": [
                {
                    "id": "kev1",
                    "windows": {
                        "five_hour": {
                            "remaining_percent": 63,
                            "used_percent": 37,
                            "reset_at": previous_reset.isoformat(),
                            "period_seconds": 5 * 60 * 60,
                            "pace_state": "under",
                        }
                    },
                }
            ],
        },
        {
            "generated_at": (period_start + timedelta(seconds=36)).isoformat(),
            "accounts": [
                {
                    "id": "kev1",
                    "windows": {
                        "five_hour": {
                            "remaining_percent": 100,
                            "used_percent": 0,
                            "reset_at": current_reset.isoformat(),
                            "period_seconds": 5 * 60 * 60,
                            "pace_state": "on",
                        }
                    },
                }
            ],
        },
    ]

    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "kev1",
                "windows": {
                    "five_hour": {
                        "reset_at": current_reset.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 5 * 60 * 60,
                        "remaining_percent": 100,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )

    history = attached[0]["windows"]["five_hour"]["history"]
    remaining_values = [point["remaining_percent"] for point in history]
    assert 63 not in remaining_values
    assert remaining_values == [100]


def test_history_filters_isolated_refill_spike_without_reset(plugin_api):
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = period_start + timedelta(hours=5)
    rows = []
    for minutes, remaining in ((10, 15), (11, 100), (12, 0), (13, 0)):
        rows.append(
            {
                "generated_at": (period_start + timedelta(minutes=minutes)).isoformat(),
                "accounts": [
                    {
                        "id": "dads-chatgpt",
                        "windows": {
                            "five_hour": {
                                "remaining_percent": remaining,
                                "used_percent": 100 - remaining,
                                "reset_at": reset_at.isoformat(),
                                "period_seconds": 5 * 60 * 60,
                                "pace_state": "over",
                            }
                        },
                    }
                ],
            }
        )

    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "dads-chatgpt",
                "windows": {
                    "five_hour": {
                        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 5 * 60 * 60,
                        "remaining_percent": 0,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )

    history = attached[0]["windows"]["five_hour"]["history"]
    spike_at = (period_start + timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
    assert spike_at not in {point["generated_at"] for point in history}
    assert history[-1]["remaining_percent"] == 0


def test_history_filters_isolated_remaining_outlier(plugin_api):
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = period_start + timedelta(days=7)
    rows = []
    for minutes, remaining in ((10, 24), (11, 0), (12, 24), (13, 24)):
        rows.append(
            {
                "generated_at": (period_start + timedelta(minutes=minutes)).isoformat(),
                "accounts": [
                    {
                        "id": "ken2",
                        "windows": {
                            "weekly": {
                                "remaining_percent": remaining,
                                "used_percent": 100 - remaining,
                                "reset_at": reset_at.isoformat(),
                                "period_seconds": 7 * 24 * 60 * 60,
                                "pace_state": "over",
                            }
                        },
                    }
                ],
            }
        )

    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "ken2",
                "windows": {
                    "weekly": {
                        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 7 * 24 * 60 * 60,
                        "remaining_percent": 24,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )

    history = attached[0]["windows"]["weekly"]["history"]
    remaining_values = [point["remaining_percent"] for point in history]
    assert 0 not in remaining_values
    assert remaining_values[-1] == 24


def test_history_keeps_sustained_zero_remaining(plugin_api):
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reset_at = period_start + timedelta(days=7)
    rows = []
    for minutes, remaining in ((10, 24), (11, 0), (12, 0), (13, 0)):
        rows.append(
            {
                "generated_at": (period_start + timedelta(minutes=minutes)).isoformat(),
                "accounts": [
                    {
                        "id": "ken2",
                        "windows": {
                            "weekly": {
                                "remaining_percent": remaining,
                                "used_percent": 100 - remaining,
                                "reset_at": reset_at.isoformat(),
                                "period_seconds": 7 * 24 * 60 * 60,
                                "pace_state": "over",
                            }
                        },
                    }
                ],
            }
        )

    attached = plugin_api._attach_history_to_accounts(
        [
            {
                "id": "ken2",
                "windows": {
                    "weekly": {
                        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
                        "period_seconds": 7 * 24 * 60 * 60,
                        "remaining_percent": 0,
                        "history": [],
                    }
                },
            }
        ],
        rows,
        history_points=20,
    )

    remaining_values = [point["remaining_percent"] for point in attached[0]["windows"]["weekly"]["history"]]
    assert 0 in remaining_values
    assert remaining_values[-1] == 0


def test_frontend_uses_dashboard_sdk_not_token_global():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "window.__HERMES_PLUGIN_SDK__" in frontend
    assert "window.__HERMES_PLUGINS__.register" in frontend
    assert "SDK.fetchJSON" in frontend
    assert "__HERMES_SESSION_TOKEN__" not in frontend


def test_frontend_draws_period_start_anchor_for_missing_initial_samples():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "new Date(windowStart).toISOString()" in frontend
    assert "remaining: 100" in frontend
    assert "synthetic: true" in frontend
    assert "firstDated.getTime() > windowStart + 60000" in frontend
    assert "resetAt: point ? point.reset_at : null" in frontend
    assert "Math.abs(pointReset.getTime() - resetDate.getTime()) > 60000" in frontend


def test_frontend_displays_on_pace_remaining_parenthetical():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "on_pace_remaining_percent" in frontend
    assert " on pace)" in frontend


def test_frontend_displays_plan_badges_next_to_account_titles():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "planLabelForAccount" in frontend
    assert "formatPlanLabel" in frontend
    assert "account.plan_type || account.plan" in frontend
    assert "Plus account" in frontend
    assert "Pro account" in frontend
    assert "Plan unknown" in frontend
    assert "kev1" not in frontend.lower()
    assert "codex-usage-plan-badge" in frontend


def test_frontend_greys_exhausted_account_cards():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "isAccountExhausted" in frontend
    assert "account.is_exhausted || account.auth_exhausted || account.window_exhausted" in frontend
    assert "remaining !== null && remaining <= 0" in frontend
    assert "codex-usage-card-exhausted" in frontend
    assert '"data-exhausted": exhausted ? "true" : "false"' in frontend
    assert "Cooldown ends in" in frontend
    assert "formatCooldownDuration" in frontend
    assert "codex-usage-cooldown" in frontend
    assert "codex-usage-exhausted-badge" in frontend
    assert "account.active_now && !exhausted" in frontend
    assert ".codex-usage-card-exhausted" in css
    assert ".codex-usage-cooldown" in css
    assert ".codex-usage-exhausted-badge" in css
    assert "filter: saturate" not in css
    assert "grayscale(1)" not in css


def test_frontend_styles_active_account_with_subtle_usage_pulse():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "function activeUsageLevel" in frontend
    assert "function activeInfoLevel" in frontend
    assert "function activeUsageLabel" in frontend
    assert "function activeUsageText" in frontend
    assert "function LastActiveNotice" in frontend
    assert "lastActiveFromSnapshot" in frontend
    assert "last_active_account" in frontend
    assert "active_drop_percent" in frontend
    assert "drop >= 3" in frontend
    assert "drop >= 1" in frontend
    assert "codex-usage-card-active" in frontend
    assert "codex-usage-card-active--" in frontend
    assert '"data-active": active ? "true" : "false"' in frontend
    assert '"data-active-level": activeLevel || undefined' in frontend
    assert "active · " in frontend
    assert "Active now" in frontend
    assert "Last active" in frontend
    assert " use" in frontend
    assert " drop" in frontend
    assert "account.active_now && !exhausted" in frontend

    assert ".codex-usage-card-active" in css
    assert ".codex-usage-card-active::before" in css
    assert ".codex-usage-last-active" in css
    assert ".codex-usage-last-active--live" in css
    assert ".codex-usage-card-active--light" in css
    assert ".codex-usage-card-active--moderate" in css
    assert ".codex-usage-card-active--heavy" in css
    assert "1.15rem rgba(var(--codex-active-rgb), 0.18)" in css
    assert "@keyframes codex-usage-active-breathe" in css
    assert "6.8s ease-in-out infinite" in css
    assert "opacity: 0.9;" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "pointer-events: none" in css
    assert "z-index: 0" in css
    assert "filter: saturate" not in css
    assert "grayscale(1)" not in css


def test_frontend_renders_hermes_session_attribution_without_old_mapping_copy():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "function HermesSessionAttribution" in frontend
    assert "account.hermes_sessions" in frontend
    assert "Hermes sessions" in frontend
    assert "Untitled Hermes session" in frontend
    assert "recent_request_count" in frontend
    assert "reqs" in frontend
    assert "h(HermesSessionAttribution, { account: account })" in frontend
    assert "Active pulses come from quota drops" in frontend
    assert "Hermes session titles appear only when Hermes recorded a matching local request" in frontend
    assert "Active account is inferred from quota drops, not session mapping" not in frontend

    assert ".codex-usage-hermes-sessions" in css
    assert ".codex-usage-hermes-session-title" in css
    assert ".codex-usage-hermes-session-status" in css


def test_css_keeps_codex_usage_banner_compact():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "display: grid;" in css
    assert "grid-template-columns: max-content minmax(0, 1fr) max-content;" in css
    assert "gap: 0.65rem;" in css
    assert "align-items: center;" in css
    assert "padding: 0.42rem 0.75rem;" in css
    assert "font-size: clamp(1.08rem, 1.45vw, 1.45rem);" in css
    assert ".codex-usage-poll-note" in css
    assert "white-space: nowrap;" in css
    assert "padding: 1rem 1.1rem;" not in css
    assert "clamp(1.45rem, 2.6vw, 2.35rem)" not in css

    title_index = frontend.index('className: "codex-usage-hero-title"')
    poll_index = frontend.index('className: "codex-usage-poll-note"')
    meta_index = frontend.index("h(SnapshotMeta")
    assert title_index < poll_index < meta_index
    assert 'h("p", null, "Polls every 30 seconds' not in frontend


def test_frontend_displays_remaining_reset_credits_only_when_available():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "resetCreditInfoForAccount" in frontend
    assert "available_count" in frontend
    assert "if (count <= 0) return null" in frontend
    assert "Codex reset " in frontend
    assert "Earliest expires " in frontend
    assert "h(ResetCredits, { account: account })" in frontend
    assert "codex-usage-reset-credits" in frontend
    assert ".codex-usage-reset-credits" in css
    assert ".codex-usage-reset-credits-expiry" in css
    assert "margin-top: 0.1rem;" in css

    five_hour_index = frontend.index('h(WindowMetric, { title: "5-hour"')
    weekly_index = frontend.index('h(WindowMetric, { title: "Weekly"')
    reset_credit_index = frontend.index("h(ResetCredits, { account: account })")
    assert five_hour_index < weekly_index < reset_credit_index


def test_frontend_displays_time_left_before_reset_time():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "formatHoursLeft" in frontend
    assert "formatDaysTimeLeft" in frontend
    assert "hours\") + \" left\"" in frontend
    assert "d \" + hours + \"h left" in frontend
    assert "leftText + \" · \"" in frontend
    assert "formatReset(windowData, props.title)" in frontend


def test_frontend_lifts_zero_percent_line_and_reduces_point_clutter():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "floorPadding: 8" in frontend
    assert "layout.plotBottom = layout.bottom - layout.floorPadding" in frontend
    assert "layout.plotHeight = layout.plotBottom - layout.top" in frontend
    assert "height: layout.bottom - layout.top" in frontend
    assert "visiblePointIndexes(points)" in frontend
    assert "point.pace !== previous.pace" in frontend
    assert "visiblePointIndexes(points).filter" in frontend
    assert "codex-usage-chart-point--latest" in frontend
    assert "isNearVerticalSegment" in frontend
    assert "NEAR_VERTICAL_MIN_DX" in frontend
    assert "NEAR_VERTICAL_MIN_DY" in frontend


def test_frontend_preserves_wrapper_account_order_and_single_sample_visibility():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "Preserve the wrapper/cache order exactly so the dashboard matches `husage`" in frontend
    assert "priorityA" not in frontend
    assert "indexA" not in frontend
    assert "localeCompare" not in frontend
    assert "points.length === 1" in frontend
    assert "codex-usage-chart-single-sample-guide" in frontend
    assert ".codex-usage-chart-single-sample-guide" in css
    assert "stroke-dasharray: 5 5;" in css


def test_frontend_does_not_render_pace_as_x_axis_zones():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8").lower()
    forbidden = [
        "pace-zone",
        "under-pace label",
        "on-pace label",
        "over-pace label",
        "under pace</text",
        "on pace</text",
        "over pace</text",
    ]

    for text in forbidden:
        assert text not in frontend


def test_frontend_supports_account_detail_url_routing():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "ACCOUNT_QUERY_PARAM" in frontend
    assert '"account"' in frontend
    assert "selectedAccountIdFromLocation" in frontend
    assert "window.history.pushState" in frontend
    assert "window.addEventListener(\"popstate\"" in frontend
    assert "window.removeEventListener(\"popstate\"" in frontend
    assert "openAccountDetail" in frontend
    assert "closeAccountDetail" in frontend
    assert "rangeStateFromLocation" in frontend
    assert "buildDetailUrl" in frontend
    assert "buildAccountListUrl" in frontend
    assert "five_hour_range" in frontend
    assert "weekly_range" in frontend
    assert "five_hour_from" in frontend
    assert "weekly_to" in frontend
    assert "setDetailRangeState(rangeStateFromLocation())" in frontend
    assert "codexUsageFromAccountList: true" in frontend
    assert "codexUsageFromAccountList: Boolean(currentState.codexUsageFromAccountList)" in frontend
    assert "window.history.replaceState({ codexUsageAccountId: selectedAccountId" in frontend
    assert "if (state.codexUsageAccountId && state.codexUsageFromAccountList && window.history.length > 1)" in frontend
    assert "window.history.replaceState({ codexUsageAccountId: \"\", codexUsageRangeState: rangeDefaults() }, \"\", buildAccountListUrl())" in frontend


def test_frontend_account_detail_range_controls_and_custom_dates():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert "function RangeControls" in frontend
    assert "FIVE_HOUR_RANGES" in frontend
    assert "WEEKLY_RANGES" in frontend
    assert '["1h", "5h", "1d", "7d", "30d"]' in frontend
    assert '["1w", "4w", "12w", "26w", "all"]' in frontend
    assert "rangePayloadForWindow" in frontend
    assert "long_history" in frontend
    assert "type: \"date\"" in frontend
    assert "invalid_range" in frontend
    assert "outside_retention" in frontend
    assert "custom_dates_required" in frontend
    assert "aria-pressed" in frontend
    assert "codex-usage-range-controls" in frontend
    assert "codex-usage-custom-range" in frontend
    assert "codex-usage-reset-marker" in frontend
    assert ".codex-usage-range-controls" in css
    assert ".codex-usage-custom-range" in css
    assert ".codex-usage-reset-marker" in css


def test_frontend_account_cards_are_clickable_and_accessible():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "onOpen" in frontend
    assert "role: clickable ? \"button\" : undefined" in frontend
    assert "tabIndex: clickable ? 0 : undefined" in frontend
    assert "onKeyDown" in frontend
    assert "event.key === \"Enter\"" in frontend
    assert "event.key === \" \"" in frontend
    assert "codex-usage-card-clickable" in frontend


def test_frontend_account_detail_page_stacks_five_hour_and_weekly_without_toggle():
    frontend = FRONTEND_JS_PATH.read_text(encoding="utf-8")

    assert "function AccountDetailPage" in frontend
    assert "codex-usage-detail" in frontend
    assert "codex-usage-detail-charts" in frontend
    assert "← Accounts" in frontend
    assert 'h(WindowMetric, { title: "5-hour"' in frontend
    assert 'h(WindowMetric, { title: "Weekly"' in frontend
    assert "codex-usage-detail-toggle" not in frontend
    assert "segmented" not in frontend.lower()


def test_css_styles_account_detail_page_and_clickable_cards():
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert ".codex-usage-card-clickable" in css
    assert "cursor: pointer;" in css
    assert ".codex-usage-detail" in css
    assert ".codex-usage-detail-head" in css
    assert ".codex-usage-detail-back" in css
    assert ".codex-usage-detail-charts" in css
    assert ".codex-usage-window-detail" in css
    assert "grid-template-columns: minmax(0, 1fr);" in css


def test_css_has_desktop_grid_and_mobile_stack():
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert ".codex-usage-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert "white-space: normal;" in css
    assert ".codex-usage-plan-badge" in css
    assert ".codex-usage-reset-credits" in css
    assert "@media (max-width: 700px)" in css
    assert ".codex-usage-grid { grid-template-columns: 1fr; }" in css
