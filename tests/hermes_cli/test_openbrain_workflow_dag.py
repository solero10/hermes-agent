from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_dag import build_source_workflow_steps, create_openbrain_workflow_cards
from hermes_cli.openbrain_workflow_artifacts import read_events, read_status


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_build_source_workflow_steps_parallel_producers():
    steps = build_source_workflow_steps("panning-and-meeting")
    by_key = {s.key: s for s in steps}
    assert {"panning_for_gold", "meeting_synthesis", "thought_enrichment", "dedupe", "ready_cortexdb_import"} <= set(by_key)
    assert set(by_key["thought_enrichment"].parents) == {"panning_for_gold", "meeting_synthesis"}
    assert set(by_key["ready_cortexdb_import"].parents) == {"dedupe", "human_review"}


def test_build_source_workflow_steps_modes():
    assert [s.key for s in build_source_workflow_steps("panning-only", approval_gate=False)] == [
        "panning_for_gold", "thought_enrichment", "dedupe", "ready_cortexdb_import"
    ]
    assert [s.key for s in build_source_workflow_steps("meeting-only", approval_gate=False)] == [
        "meeting_synthesis", "thought_enrichment", "dedupe", "ready_cortexdb_import"
    ]


def test_create_openbrain_workflow_cards_links_parallel_producers(kanban_home):
    with kb.connect() as conn:
        created = create_openbrain_workflow_cards(
            workflow_run_id="obwf_test",
            source_unit_id="source-a",
            source_folder="/tmp/source-a",
            mode="panning-and-meeting",
            assignee="default",
            board="default",
            conn=conn,
        )
        assert created.root_task_id
        assert set(created.step_task_ids) >= {"panning_for_gold", "meeting_synthesis", "thought_enrichment", "dedupe", "ready_cortexdb_import"}
        enrichment = created.step_task_ids["thought_enrichment"]
        parents = set(kb.parent_ids(conn, enrichment))
        assert parents == {created.step_task_ids["panning_for_gold"], created.step_task_ids["meeting_synthesis"]}
        panning = kb.get_task(conn, created.step_task_ids["panning_for_gold"])
        assert panning.workflow_template_id == "openbrain-source-v1"
        assert panning.current_step_key == "panning_for_gold"

    status = read_status("obwf_test")
    assert status is not None
    assert status.active_step in {"panning_for_gold", "meeting_synthesis"}
    events = read_events("obwf_test")
    assert events[0]["event_type"] == "workflow_started"
    assert [event["event_type"] for event in events].count("task_created") >= 3
