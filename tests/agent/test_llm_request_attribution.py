from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from agent import llm_request_attribution as attribution


def test_safe_credential_metadata_uses_label_and_demotes_priority_only():
    entry = SimpleNamespace(
        label="Primary Codex",
        priority=2,
        source="manual:device_code",
        id="raw-credential-id-not-used",
        access_token="sk-sec...pear",
        refresh_token="redacted-refresh-marker",
    )

    metadata = attribution.safe_credential_metadata(entry)
    payload = json.dumps(metadata)

    assert metadata["account_match_keys"] == ["primary-codex"]
    assert metadata["fallback_match_keys"] == ["priority-2"]
    assert metadata["match_confidence"] == "label"
    assert "raw-credential-id" not in payload
    assert "secret" not in payload
    assert "token" not in payload


def test_record_request_lifecycle_is_profile_safe_and_secret_free(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    event_id = attribution.record_request_start(
        provider="openai-codex",
        api_mode="codex_responses",
        model="gpt-5.5-codex",
        session_id="session-123",
        title_snapshot="Implement plan",
        credential_label="Primary Codex",
        credential_priority=1,
        credential_source="manual",
        account_match_keys=[
            "Primary Codex",
            "priority-1",
            "Authorization: Bearer should-not-survive",
        ],
        fallback_match_keys=["priority-1"],
        now=now,
    )

    assert event_id
    attribution.record_request_finish(event_id, status="ok", now=now)
    events = attribution.read_recent_events(provider="openai-codex", now=now)

    assert len(events) == 1
    event = events[0]
    assert event["status"] == "ok"
    assert event["session_id"] == "session-123"
    assert event["title_snapshot"] == "Implement plan"
    assert event["account_match_keys"] == ["primary-codex"]
    assert event["fallback_match_keys"] == ["priority-1"]

    payload = attribution.attribution_path().read_text(encoding="utf-8")
    assert str(hermes_home) in str(attribution.attribution_path())
    assert "should-not-survive" not in payload
    assert "Authorization" not in payload
    assert "access_token" not in payload


def test_record_request_start_refuses_fallback_only_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    event_id = attribution.record_request_start(
        provider="openai-codex",
        api_mode="codex_responses",
        model="gpt-5.5-codex",
        session_id="session-123",
        credential_priority=1,
        account_match_keys=["priority-1", "index-0"],
        fallback_match_keys=["priority-1", "index-0"],
    )

    assert event_id is None
    assert not attribution.attribution_path().exists()
