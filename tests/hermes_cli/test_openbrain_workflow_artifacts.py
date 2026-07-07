from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hermes_cli.openbrain_workflow_artifacts import (
    append_event,
    atomic_write_json,
    dashboard_response,
    read_dashboard_summary,
    read_events,
    summarize_staleness,
    workflow_run_dir,
    write_dashboard_summary,
    write_status,
)
from hermes_cli.openbrain_workflow_contracts import StepStatus, WorkflowEvent, WorkflowStatus


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _status(**overrides):
    payload = {
        "workflow_run_id": "obwf_test",
        "source_unit_id": "source-a",
        "status": "running",
        "started_at": "2026-07-06T10:00:00Z",
        "updated_at": "2026-07-06T10:01:00Z",
        "last_heartbeat_at": "2026-07-06T10:01:00Z",
    }
    payload.update(overrides)
    return WorkflowStatus.model_validate(payload)


def test_workflow_run_dir_resolves_under_hermes_home(hermes_home):
    assert workflow_run_dir("obwf_test") == hermes_home / "openbrain-workflow-runs" / "obwf_test"
    with pytest.raises(ValueError):
        workflow_run_dir("../bad")


def test_atomic_write_preserves_prior_good_file_on_replace_failure(hermes_home, monkeypatch):
    path = hermes_home / "openbrain-workflow-runs" / "obwf_test" / "status.json"
    atomic_write_json(path, {"ok": 1})

    def fail_replace(src, dst):
        raise OSError("boom")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(path, {"ok": 2})
    assert json.loads(path.read_text()) == {"ok": 1}


def test_append_event_assigns_incrementing_ids(hermes_home):
    event = WorkflowEvent(
        workflow_run_id="obwf_test",
        source_unit_id="source-a",
        event_type="workflow_started",
        created_at="2026-07-06T10:00:00Z",
    )
    append_event(event)
    append_event(event.model_copy(update={"event_type": "task_started"}))
    events = read_events("obwf_test")
    assert [e["event_id"] for e in events] == [1, 2]


def test_dashboard_summary_falls_back_to_status(hermes_home):
    write_status(_status(current_phase="enriching", completed_candidates=1, total_candidates=3))
    summary = read_dashboard_summary("obwf_test")
    assert summary["current_phase"] == "enriching"
    write_dashboard_summary(_status(status="done", updated_at="2026-07-06T10:10:00Z"))
    assert read_dashboard_summary("obwf_test")["status"] == "done"


def test_summarize_staleness_only_marks_running_old_heartbeats(hermes_home):
    now = datetime(2026, 7, 6, 10, 20, tzinfo=timezone.utc)
    assert summarize_staleness(_status(), now=now, stale_after_seconds=600)["stale"] is True
    assert summarize_staleness(_status(status="done"), now=now, stale_after_seconds=600)["stale"] is False


def test_dashboard_response_uses_revision_change(hermes_home):
    write_status(_status(
        active_step="thought_enrichment",
        receipt_path="/tmp/workflow/receipt.md",
        steps={"thought_enrichment": StepStatus(status="running", receipt_path="/tmp/workflow/step-receipt.md")},
    ))
    append_event(WorkflowEvent(
        workflow_run_id="obwf_test",
        source_unit_id="source-a",
        step_key="thought_enrichment",
        event_type="heartbeat_sent",
        created_at="2026-07-06T10:01:00Z",
        message="heartbeat ok",
    ))
    first = dashboard_response()
    same = dashboard_response(since_revision=first["revision"])
    assert first["changed"] is True
    assert first["runs"][0]["kanban_task_state"] == "running"
    assert first["runs"][0]["latest_receipt_path"] == "/tmp/workflow/receipt.md"
    assert first["events"][0]["event_type"] == "heartbeat_sent"
    assert same["changed"] is False
    assert same["runs"] == []
    assert same["events"] == []
