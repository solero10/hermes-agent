from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hermes_cli.openbrain_workflow_supervisor import (
    WorkflowStepSupervisor,
    build_fresh_session_adapter,
    parse_step_handoff,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def test_parse_step_handoff_reads_receipt_pointer(hermes_home):
    root = hermes_home / "openbrain-workflow-runs" / "obwf_test" / "steps" / "panning_for_gold"
    root.mkdir(parents=True)
    receipt = root / "receipt-pointer.json"
    receipt.write_text(json.dumps({
        "source_unit_id": "source-a",
        "candidate_ids": ["SHAPE-01", "SHAPE-02"],
        "dashboard_verification": {"ok": True, "visible_candidate_count": 2},
    }), encoding="utf-8")
    handoff = parse_step_handoff(
        workflow_run_id="obwf_test",
        step_key="panning_for_gold",
        step_name="Panning for Gold",
        receipt_pointer_path=receipt,
    )
    assert handoff.candidate_count == 2
    assert handoff.dashboard_verification.ok is True


def test_build_adapter_gates_missing_entrypoint_without_secret_args(hermes_home):
    adapter = build_fresh_session_adapter(
        step_key="dedupe",
        workflow_run_id="obwf_test",
        source_unit_id="source-a",
        source_folder="/mnt/d/private/source",
    )
    assert adapter.command == []
    prompt = Path(adapter.prompt_path).read_text(encoding="utf-8")
    assert "/mnt/d/private" not in prompt
    assert "workflow_run_id: obwf_test" in prompt


def test_supervisor_runs_child_and_requires_receipt(hermes_home):
    step_dir = hermes_home / "openbrain-workflow-runs" / "obwf_test" / "steps" / "panning_for_gold"
    step_dir.mkdir(parents=True)
    child = step_dir / "child.py"
    receipt = step_dir / "receipt-pointer.json"
    child.write_text(
        "import json, pathlib\n"
        f"pathlib.Path({str(receipt)!r}).write_text(json.dumps({{'source_unit_id':'source-a','candidate_ids':['c1']}}))\n"
        "print(json.dumps({'dashboard_verification': {'ok': True}}))\n",
        encoding="utf-8",
    )
    notes = []
    sup = WorkflowStepSupervisor(
        workflow_run_id="obwf_test",
        step_key="panning_for_gold",
        task_id="t1",
        child_command=[sys.executable, str(child)],
        heartbeat_callback=notes.append,
        heartbeat_interval_seconds=0,
        poll_interval_seconds=0.01,
        receipt_pointer_path=receipt,
    )
    result = sup.run()
    assert result.status == "done"
    assert result.metadata["candidate_ids"] == ["c1"]
    assert notes


def test_supervisor_blocks_missing_entrypoint(hermes_home):
    result = WorkflowStepSupervisor(
        workflow_run_id="obwf_test",
        step_key="dedupe",
        task_id="t1",
        child_command=[],
    ).run()
    assert result.status == "blocked"
    assert "entrypoint not configured" in (result.block_reason or "")
