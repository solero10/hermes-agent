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


def test_manifest_registers_expected_dashboard_plugin():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest == {
        "name": "codex_usage_monitor",
        "label": "Codex Usage",
        "description": "Real-time ChatGPT/Codex OAuth account usage monitor with 5-hour and weekly quota charts",
        "icon": "Activity",
        "version": "0.1.0",
        "tab": {"path": "/codex-usage", "position": "after:analytics"},
        "entry": "dist/index.js?v=20260620-compact-banner-3col-v4",
        "css": "dist/style.css",
        "api": "plugin_api.py",
    }

    plugin_yaml = PLUGIN_YAML_PATH.read_text(encoding="utf-8")
    assert "name: codex_usage_monitor" in plugin_yaml
    assert "kind: dashboard" in plugin_yaml
    assert "version: 0.1.0" in plugin_yaml
    assert "author: Hermes Agent" in plugin_yaml


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
    assert source["command"] == "husage usage --json"
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
    assert source["command"] == "husage usage --json"
    assert "raw-invalid-token" not in source["last_error"]
    assert "sk-abc...mnop" not in source["last_error"]
    assert "[REDACTED]" in source["last_error"]


def test_route_mounts_in_bare_fastapi_testclient_and_returns_json(plugin_api, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_build_snapshot(history_points: int = 240, force: bool = False):
        seen["history_points"] = history_points
        seen["force"] = force
        return {
            "ok": False,
            "generated_at": "2026-01-01T00:00:00Z",
            "poll_interval_seconds": 30,
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
    assert data["poll_interval_seconds"] == 30
    assert seen == {"history_points": 20, "force": True}


def test_mocked_snapshot_endpoint_returns_normalized_accounts(plugin_api, monkeypatch):
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
    response = _client(plugin_api).get("/api/plugins/codex_usage_monitor/snapshot?history_points=20")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["cached"] is False
    assert data["source"] == {
        "command": "husage --json usage",
        "available": True,
        "last_error": None,
    }
    assert data["reset_credits_source"] == {
        "command": "husage --json resets",
        "available": True,
        "last_error": None,
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


def test_build_snapshot_cache_coalesces_immediate_calls(plugin_api, monkeypatch):
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

    first = plugin_api.build_snapshot(history_points=20)
    second = plugin_api.build_snapshot(history_points=20)

    assert calls == {"usage": 1, "resets": 1}
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["accounts"][0]["id"] == "acct-one"
    assert second["accounts"][0]["reset_credits"]["available_count"] == 2


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


def test_css_has_desktop_grid_and_mobile_stack():
    css = FRONTEND_CSS_PATH.read_text(encoding="utf-8")

    assert ".codex-usage-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert "white-space: normal;" in css
    assert ".codex-usage-plan-badge" in css
    assert ".codex-usage-reset-credits" in css
    assert "@media (max-width: 700px)" in css
    assert ".codex-usage-grid { grid-template-columns: 1fr; }" in css
