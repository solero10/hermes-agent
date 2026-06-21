"""Regression tests for dashboard session-detail transcript hydration."""

import asyncio

from hermes_state import SessionDB
from hermes_cli import web_server


def test_session_messages_endpoint_returns_full_compression_lineage(tmp_path, monkeypatch):
    """Desktop session details should show the whole logical conversation.

    A compression split can leave real messages on both the parent/root and the
    continuation/tip. The dashboard previously returned only the selected tip,
    so long sessions appeared truncated even though the DB held the full chain.
    """
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("root", "tui")
    db.append_message("root", role="user", content="first prompt")
    db.append_message("root", role="assistant", content="first answer")
    db.end_session("root", end_reason="compression")
    db.create_session("tip", "tui", parent_session_id="root")
    db.append_message("tip", role="user", content="second prompt")
    db.append_message("tip", role="assistant", content="second answer")

    monkeypatch.setattr(web_server, "_open_session_db_for_profile", lambda _profile: db)

    try:
        response = asyncio.run(web_server.get_session_messages("tip"))
    finally:
        db.close()

    assert response["session_id"] == "tip"
    assert response["lineage"] == ["root", "tip"]
    assert [m["content"] for m in response["messages"]] == [
        "first prompt",
        "first answer",
        "second prompt",
        "second answer",
    ]


def test_session_messages_endpoint_keeps_branch_transcripts_isolated(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("root", "tui")
    db.append_message("root", role="user", content="parent prompt")
    db.end_session("root", end_reason="branched")
    db.create_session("branch", "tui", parent_session_id="root")
    db.append_message("branch", role="user", content="branch prompt")

    monkeypatch.setattr(web_server, "_open_session_db_for_profile", lambda _profile: db)

    try:
        response = asyncio.run(web_server.get_session_messages("branch"))
    finally:
        db.close()

    assert response["session_id"] == "branch"
    assert response["lineage"] == ["branch"]
    assert [m["content"] for m in response["messages"]] == ["branch prompt"]
