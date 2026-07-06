from __future__ import annotations

import json
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_artifacts import read_events, read_status
from hermes_cli.subcommands.openbrain_workflow import build_openbrain_workflow_parser


def test_openbrain_workflow_start_command_creates_cards(tmp_path, monkeypatch, capsys):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()

    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    build_openbrain_workflow_parser(sub)
    args = parser.parse_args([
        "openbrain-workflow", "start",
        "--source-folder", "/tmp/source-a",
        "--source-unit-id", "source-a",
        "--workflow-run-id", "obwf_cli",
        "--mode", "panning-only",
        "--assignee", "default",
        "--json",
    ])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow_run_id"] == "obwf_cli"
    assert "panning_for_gold" in payload["step_task_ids"]
    assert read_status("obwf_cli") is not None
    assert [event["event_type"] for event in read_events("obwf_cli")][:2] == ["workflow_started", "task_created"]
