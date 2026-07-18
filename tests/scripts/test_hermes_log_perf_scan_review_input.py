"""Regression tests for the bounded 5:45 AM Hermes log-review wrapper."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

WRAPPER = Path("/home/kernk/.hermes/scripts/hermes_log_perf_scan_review_input.py")


def load_wrapper():
    spec = importlib.util.spec_from_file_location("hermes_log_review_wrapper_test", WRAPPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeScanner:
    PT = ZoneInfo("America/Los_Angeles")

    def __init__(self, health_rows):
        self.health_rows = list(health_rows)
        self.health_calls = 0

    def iter_recent_log_lines(self, _since):
        return iter(())

    def group_incidents(self, _matches):
        return []

    def gateway_health(self):
        row = self.health_rows[min(self.health_calls, len(self.health_rows) - 1)]
        self.health_calls += 1
        return dict(row)


def health(process_running, state, telegram_state, pending=0):
    return {
        "process_running": process_running,
        "state": state,
        "telegram_state": telegram_state,
        "pending_update_count": pending,
        "notes": [],
    }


def test_zero_log_incidents_still_probe_and_report_gateway_outage(monkeypatch):
    module = load_wrapper()
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    scanner = FakeScanner([
        health(False, "stopped", "disconnected"),
        health(False, "stopped", "disconnected"),
    ])

    candidate = module.build_safe_candidate(scanner, datetime.now(UTC))

    assert scanner.health_calls == 2
    assert candidate["unique_incidents"] == 0
    assert candidate["groups"] == []
    assert candidate["gateway_health"]["process_running"] is False
    assert candidate["gateway_health"]["telegram_state"] == "disconnected"


def test_transient_disconnected_state_is_reprobed(monkeypatch):
    module = load_wrapper()
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))
    scanner = FakeScanner([
        health(True, "running", "disconnected"),
        health(True, "running", "connected"),
    ])

    result = module.safe_gateway_health(scanner)

    assert sleeps == [10]
    assert scanner.health_calls == 2
    assert result["process_running"] is True
    assert result["telegram_state"] == "connected"
