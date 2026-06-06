"""Ken-safe v15 dashboard/API compatibility tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db as kb


def _load_plugin_router():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "kanban" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_kanban_ken_safe_test", plugin_file,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.router


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/kanban")
    return TestClient(app)


def _columns(board):
    return {col["name"]: col["tasks"] for col in board["columns"]}


def test_board_exposes_backlog_and_human_review_columns(client):
    board = client.get("/api/plugins/kanban/board").json()
    names = [col["name"] for col in board["columns"]]

    assert "backlog" in names
    assert "review" in names
    assert "human_review" in names
    assert names.index("backlog") < names.index("triage")
    assert names.index("review") < names.index("human_review") < names.index("done")


def test_create_backlog_card_stays_in_backlog_not_todo(client):
    response = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "intake", "assignee": "default", "status": "backlog"},
    )
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"] == "backlog"

    columns = _columns(client.get("/api/plugins/kanban/board").json())
    assert [t["id"] for t in columns["backlog"]] == [task["id"]]
    assert task["id"] not in [t["id"] for t in columns["todo"]]


def test_dashboard_can_move_cards_into_manual_review_lanes(client):
    task = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "needs eyes", "assignee": "default"},
    ).json()["task"]

    for status in ("review", "human_review"):
        response = client.patch(
            f"/api/plugins/kanban/tasks/{task['id']}",
            json={"status": status},
        )
        assert response.status_code == 200, response.text
        assert response.json()["task"]["status"] == status

        columns = _columns(client.get("/api/plugins/kanban/board").json())
        assert task["id"] in [t["id"] for t in columns[status]]


def test_bundled_dashboard_dist_includes_ken_safe_columns():
    repo_root = Path(__file__).resolve().parents[2]
    dist_js = repo_root / "plugins" / "kanban" / "dashboard" / "dist" / "index.js"
    text = dist_js.read_text(encoding="utf-8")

    assert '"backlog"' in text
    assert '"scheduled"' in text
    assert '"review"' in text
    assert '"human_review"' in text
    assert 'Backlog' in text
    assert 'Human Review' in text
