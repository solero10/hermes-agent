"""Ken-safe v15 Kanban behavior regression tests.

These tests protect Ken's local operating model during the v15 port:
Backlog is a human-only intake lane, Review/Human Review are manual gates,
and none of those lanes may be dispatcher-spawnable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _set_task_status(conn, task_id: str, status: str) -> None:
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def test_backlog_initial_status_is_human_only_and_not_dispatched(kanban_home, monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    spawned = []

    def capture_spawn(task, workspace, board=None):
        spawned.append(task.id)
        return 42

    with kb.connect() as conn:
        tid = kb.create_task(
            conn,
            title="human intake",
            assignee="default",
            initial_status="backlog",
        )
        task = kb.get_task(conn, tid)
        assert task.status == "backlog"

        res = kb.dispatch_once(conn, spawn_fn=capture_spawn, dry_run=True)

        assert not res.spawned
        assert not spawned
        assert kb.get_task(conn, tid).status == "backlog"


def test_review_and_human_review_are_manual_gates_not_spawnable(kanban_home, monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)

    with kb.connect() as conn:
        review_id = kb.create_task(conn, title="manual review", assignee="default")
        human_review_id = kb.create_task(conn, title="legacy human review", assignee="default")
        _set_task_status(conn, review_id, "review")
        _set_task_status(conn, human_review_id, "human_review")

        assert kb.has_spawnable_review(conn) is False
        res = kb.dispatch_once(conn, dry_run=True)

        assert not res.spawned
        assert kb.get_task(conn, review_id).status == "review"
        assert kb.get_task(conn, human_review_id).status == "human_review"


def test_human_review_is_a_valid_status_for_existing_copied_boards(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="copied legacy card")
        _set_task_status(conn, tid, "human_review")
        rows = kb.list_tasks(conn, status="human_review")

    assert [task.id for task in rows] == [tid]
