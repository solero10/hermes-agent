from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import pytest

from hermes_cli.openbrain_workflow_supervisor import (
    ChildRunResult,
    StepAdapter,
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


class MismatchedInputRunner:
    def run(
        self,
        adapter: StepAdapter,
        *,
        timeout_seconds: float | None = None,
        heartbeat_callback: Callable[[str], None] | None = None,
        heartbeat_interval_seconds: float = 0,
        poll_interval_seconds: float = 0,
    ) -> ChildRunResult:
        pointer = Path(adapter.receipt_pointer_path)
        pointer.parent.mkdir(parents=True, exist_ok=True)
        receipt_target = pointer.parent / "receipt.json"
        receipt_target.write_text("{}", encoding="utf-8")
        pointer.write_text(json.dumps({
            "workflow_run_id": adapter.workflow_run_id,
            "step_key": adapter.step_key,
            "source_unit_id": adapter.source_unit_id,
            "input_candidate_ids": ["wrong-input"],
            "candidate_ids": ["output-1"],
            "receipt_path": str(receipt_target),
            "dashboard_verification": {"ok": True},
        }), encoding="utf-8")
        return ChildRunResult(exit_code=0, stdout="{}", stderr="", timed_out=False)


def test_supervisor_blocks_receipt_input_candidate_mismatch(hermes_home):
    root = hermes_home / "openbrain-workflow-runs" / "obwf_test" / "steps" / "thought_enrichment"
    root.mkdir(parents=True)
    adapter = StepAdapter(
        step_key="thought_enrichment",
        command=[],
        prompt_path=str(root / "prompt.md"),
        status_path=str(root.parent.parent / "status.json"),
        events_path=str(root.parent.parent / "events.jsonl"),
        receipt_pointer_path=str(root / "receipt-pointer.json"),
        workflow_run_id="obwf_test",
        source_unit_id="source-a",
        input_candidate_ids=["expected-input"],
    )

    result = WorkflowStepSupervisor(
        workflow_run_id="obwf_test",
        step_key="thought_enrichment",
        task_id="t1",
        adapter=adapter,
        runner=MismatchedInputRunner(),
        source_unit_id="source-a",
    ).run()

    assert result.status == "blocked"
    assert "candidate" in (result.block_reason or "")
    assert "mismatch" in (result.block_reason or "")
