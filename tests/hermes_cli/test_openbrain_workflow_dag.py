from __future__ import annotations

import concurrent.futures
from pathlib import Path
import sqlite3

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_dag import build_source_workflow_steps, create_openbrain_workflow_cards
from hermes_cli.openbrain_workflow_artifacts import (
    dashboard_summary_path,
    events_path,
    read_events,
    read_status,
    status_path,
)


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
        assert panning is not None
        assert panning.workflow_template_id == "openbrain-source-v1"
        assert panning.current_step_key == "panning_for_gold"
        assert "source_folder_path: /tmp/source-a" in (panning.body or "")
        root = kb.get_task(conn, created.root_task_id)
        assert root is not None
        assert "source_folder_path: /tmp/source-a" in (root.body or "")
        conn.execute(
            "UPDATE tasks SET status = 'done' WHERE id IN (?, ?, ?)",
            (
                created.step_task_ids["panning_for_gold"],
                created.step_task_ids["meeting_synthesis"],
                created.step_task_ids["dedupe"],
            ),
        )
        kb.recompute_ready(conn)
        review = kb.get_task(conn, created.step_task_ids["human_review"])
        assert review is not None
        assert review.status == "blocked"

    status = read_status("obwf_test")
    assert status is not None
    assert status.active_step in {"panning_for_gold", "meeting_synthesis"}
    assert status.source_folder == "/tmp/source-a"
    assert status.steps["panning_for_gold"].status == "running"
    assert status.steps["meeting_synthesis"].status == "ready"
    assert status.steps["thought_enrichment"].status == "pending"
    events = read_events("obwf_test")
    assert events[0]["event_type"] == "workflow_started"
    assert [event["event_type"] for event in events].count("task_created") >= 3


def test_duplicate_start_with_stale_precheck_does_not_duplicate_cards_links_or_events(kanban_home, monkeypatch):
    with kb.connect() as conn:
        first = create_openbrain_workflow_cards(
            workflow_run_id="obwf_duplicate_start",
            source_unit_id="source-a",
            source_folder="/tmp/source-a",
            mode="panning-only",
            assignee="default",
            board="default",
            conn=conn,
        )
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 6

        monkeypatch.setattr(
            "hermes_cli.openbrain_workflow_dag._existing_task_id",
            lambda _conn, _key: None,
        )
        second = create_openbrain_workflow_cards(
            workflow_run_id="obwf_duplicate_start",
            source_unit_id="source-a",
            source_folder="/tmp/source-a",
            mode="panning-only",
            assignee="default",
            board="default",
            conn=conn,
        )

        assert second.root_task_id == first.root_task_id
        assert second.step_task_ids == first.step_task_ids
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 6

    events = read_events("obwf_duplicate_start")
    assert [event["event_type"] for event in events].count("workflow_started") == 1
    task_created = [event for event in events if event["event_type"] == "task_created"]
    assert len(task_created) == 6
    assert {event.get("step_key") for event in task_created} == {
        "root",
        "panning_for_gold",
        "thought_enrichment",
        "dedupe",
        "human_review",
        "ready_cortexdb_import",
    }


def test_concurrent_duplicate_starts_share_one_workflow_graph_and_event_set(kanban_home):
    def start_once():
        with kb.connect() as conn:
            created = create_openbrain_workflow_cards(
                workflow_run_id="obwf_concurrent_start",
                source_unit_id="source-a",
                source_folder="/tmp/source-a",
                mode="panning-only",
                assignee="default",
                board="default",
                conn=conn,
            )
            return created.root_task_id, dict(created.step_task_ids)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _index: start_once(), range(6)))

    root_ids = {root_id for root_id, _steps in results}
    step_maps = [steps for _root_id, steps in results]
    assert len(root_ids) == 1
    assert all(steps == step_maps[0] for steps in step_maps)

    with kb.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE workflow_template_id = ?",
            ("openbrain-source-v1",),
        ).fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 6

    events = read_events("obwf_concurrent_start")
    assert [event["event_type"] for event in events].count("workflow_started") == 1
    task_created = [event for event in events if event["event_type"] == "task_created"]
    assert len(task_created) == 6
    assert {event.get("step_key") for event in task_created} == {
        "root",
        "panning_for_gold",
        "thought_enrichment",
        "dedupe",
        "human_review",
        "ready_cortexdb_import",
    }


def test_mid_start_lock_failure_leaves_no_partial_workflow_cards_links_or_artifacts(kanban_home, monkeypatch):
    original_create_task = kb.create_task

    def fail_on_thought_enrichment(conn, **kwargs):
        if "Thought Enrichment" in kwargs.get("title", ""):
            raise sqlite3.OperationalError("database is locked")
        return original_create_task(conn, **kwargs)

    monkeypatch.setattr(kb, "create_task", fail_on_thought_enrichment)

    with kb.connect() as conn:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            create_openbrain_workflow_cards(
                workflow_run_id="obwf_atomic_start",
                source_unit_id="source-a",
                source_folder="/tmp/source-a",
                mode="panning-only",
                assignee="default",
                board="default",
                conn=conn,
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE workflow_template_id = ?",
            ("openbrain-source-v1",),
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 0

    assert not status_path("obwf_atomic_start").exists()
    assert not events_path("obwf_atomic_start").exists()
    assert not dashboard_summary_path("obwf_atomic_start").exists()
