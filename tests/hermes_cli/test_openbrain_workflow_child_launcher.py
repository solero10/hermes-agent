from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import pytest

from hermes_cli.openbrain_workflow_supervisor import (
    StepAdapter,
    SubprocessChildRunner,
    build_fresh_session_adapter,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def test_production_adapter_prepares_sanitized_launch_envelope(hermes_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_BIN", "/usr/local/bin/hermes-test")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    adapter = build_fresh_session_adapter(
        step_key="panning_for_gold",
        workflow_run_id="obwf_launcher",
        source_unit_id="source-a",
        source_folder="/mnt/d/private/SECRET_SENTINEL_SHOULD_NOT_APPEAR/source package",
        input_candidate_ids=("CAND-001", "SECRET_SENTINEL_SHOULD_NOT_APPEAR"),
        assignee="test-profile",
        workspace=str(workspace),
        execute_mode="production-execute",
    )

    assert adapter.command[:3] == ["/usr/local/bin/hermes-test", "--profile", "test-profile"]
    assert "chat" in adapter.command
    assert "-q" in adapter.command
    assert adapter.cwd == str(workspace)
    assert adapter.env["HERMES_HOME"] == str(hermes_home)
    assert adapter.env["HERMES_OPENBRAIN_WORKFLOW_RUN_ID"] == "obwf_launcher"
    assert adapter.env["HERMES_OPENBRAIN_STEP_KEY"] == "panning_for_gold"
    assert adapter.env["HERMES_OPENBRAIN_RECEIPT_POINTER"] == adapter.receipt_pointer_path
    assert adapter.env["HERMES_STRICT_SOURCE_FOLDER"].endswith("source package")
    assert Path(adapter.stdout_log_path).name == "child-stdout.log"
    assert Path(adapter.stderr_log_path).name == "child-stderr.log"
    assert Path(adapter.child_session_path).name == "child-session.json"

    prompt = Path(adapter.prompt_path).read_text(encoding="utf-8")
    assert "workflow_run_id: obwf_launcher" in prompt
    assert "step_key: panning_for_gold" in prompt
    assert "CAND-001" in prompt
    assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in prompt
    assert "/mnt/d/private" not in prompt


def _adapter_for_command(tmp_path, hermes_home, command: list[str]) -> StepAdapter:
    step_dir = hermes_home / "openbrain-workflow-runs" / "obwf_launcher" / "steps" / "panning_for_gold"
    step_dir.mkdir(parents=True, exist_ok=True)
    return StepAdapter(
        step_key="panning_for_gold",
        command=command,
        prompt_path=str(step_dir / "prompt.md"),
        status_path=str(hermes_home / "openbrain-workflow-runs" / "obwf_launcher" / "status.json"),
        events_path=str(hermes_home / "openbrain-workflow-runs" / "obwf_launcher" / "events.jsonl"),
        receipt_pointer_path=str(step_dir / "receipt-pointer.json"),
        workflow_run_id="obwf_launcher",
        source_unit_id="source-a",
        source_folder=str(tmp_path / "source"),
        input_candidate_ids=["CAND-001"],
        env={
            "HERMES_HOME": str(hermes_home),
            "HERMES_OPENBRAIN_WORKFLOW_RUN_ID": "obwf_launcher",
            "HERMES_OPENBRAIN_STEP_KEY": "panning_for_gold",
            "HERMES_OPENBRAIN_RECEIPT_POINTER": str(step_dir / "receipt-pointer.json"),
        },
        cwd=str(tmp_path),
        stdout_log_path=str(step_dir / "child-stdout.log"),
        stderr_log_path=str(step_dir / "child-stderr.log"),
        child_session_path=str(step_dir / "child-session.json"),
    )


def test_subprocess_runner_captures_logs_env_and_child_session(hermes_home, tmp_path):
    code = """
import json, os, pathlib, sys
receipt = pathlib.Path(os.environ['HERMES_OPENBRAIN_RECEIPT_POINTER'])
receipt.parent.mkdir(parents=True, exist_ok=True)
receipt_target = receipt.parent / 'receipt.json'
receipt_target.write_text('{}', encoding='utf-8')
receipt.write_text(json.dumps({
    'workflow_run_id': os.environ['HERMES_OPENBRAIN_WORKFLOW_RUN_ID'],
    'step_key': os.environ['HERMES_OPENBRAIN_STEP_KEY'],
    'source_unit_id': 'source-a',
    'candidate_ids': ['CAND-001'],
    'receipt_path': str(receipt_target),
    'dashboard_verification': {'ok': True},
}), encoding='utf-8')
print('child stdout ok')
print('child stderr ok', file=sys.stderr)
"""
    adapter = _adapter_for_command(tmp_path, hermes_home, [sys.executable, "-c", code])

    result = SubprocessChildRunner().run(adapter, timeout_seconds=5, poll_interval_seconds=0.01)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "child stdout ok" in Path(adapter.stdout_log_path).read_text(encoding="utf-8")
    assert "child stderr ok" in Path(adapter.stderr_log_path).read_text(encoding="utf-8")
    session = json.loads(Path(adapter.child_session_path).read_text(encoding="utf-8"))
    assert session["exit_code"] == 0
    assert session["timed_out"] is False
    assert session["workflow_run_id"] == "obwf_launcher"
    assert session["step_key"] == "panning_for_gold"
    assert "HERMES_OPENBRAIN_RECEIPT_POINTER" in session["env_keys"]


def test_subprocess_runner_timeout_terminates_cleanly_and_records_session(hermes_home, tmp_path):
    marker = tmp_path / "terminated.txt"
    code = f"""
import pathlib, signal, sys, time
marker = pathlib.Path({str(marker)!r})
def handler(signum, frame):
    marker.write_text('terminated', encoding='utf-8')
    sys.exit(0)
signal.signal(signal.SIGTERM, handler)
while True:
    time.sleep(0.05)
"""
    adapter = _adapter_for_command(tmp_path, hermes_home, [sys.executable, "-c", code])

    result = SubprocessChildRunner().run(adapter, timeout_seconds=0.2, poll_interval_seconds=0.01)

    assert result.timed_out is True
    assert marker.read_text(encoding="utf-8") == "terminated"
    session = json.loads(Path(adapter.child_session_path).read_text(encoding="utf-8"))
    assert session["timed_out"] is True
    assert session["termination"] == "SIGTERM"
