from __future__ import annotations

import pytest

from hermes_cli.openbrain_workflow_contracts import StepStatus, WorkflowEvent, WorkflowStatus, build_heartbeat_note


def test_status_contract_accepts_minimal_running_payload():
    payload = {
        "workflow_run_id": "obwf_20260706T101205Z_source_a_1a2b3c4d",
        "source_unit_id": "source-a",
        "active_step": "thought_enrichment",
        "status": "running",
        "started_at": "2026-07-06T10:12:05Z",
        "updated_at": "2026-07-06T10:17:05Z",
    }
    status = WorkflowStatus.model_validate(payload)
    assert status.workflow_run_id.startswith("obwf_")


def test_event_contract_rejects_unknown_event_type():
    with pytest.raises(Exception):
        WorkflowEvent.model_validate({
            "workflow_run_id": "obwf_x",
            "source_unit_id": "source-a",
            "event_type": "made_up",
            "created_at": "2026-07-06T10:17:05Z",
        })


def test_heartbeat_note_redacts_private_values():
    note = build_heartbeat_note(
        workflow_run_id="obwf_x",
        step_key="thought_enrichment",
        source_unit_id="source-a",
        elapsed_seconds=317,
        phase="enriching",
        completed_candidates=7,
        total_candidates=18,
        current_candidate_id="SHAPE-07",
        current_candidate_title="Token sk-secret at /mnt/d/private/file.txt",
    )
    assert "sk-secret" not in note
    assert "/mnt/d/private" not in note
    assert "progress=7/18" in note
    assert "elapsed=00:05:17" in note


def test_receipt_and_source_paths_remain_usable_private_pointers():
    status = WorkflowStatus.model_validate({
        "workflow_run_id": "obwf_x",
        "source_unit_id": "source-a",
        "source_folder": "/tmp/source-a/full-path",
        "receipt_path": "/tmp/source-a/receipt.md",
        "active_step": "thought_enrichment",
        "status": "running",
        "started_at": "2026-07-06T10:12:05Z",
        "updated_at": "2026-07-06T10:17:05Z",
    })
    step = StepStatus(status="done", receipt_path="/tmp/source-a/step-receipt.md")
    assert status.source_folder == "/tmp/source-a/full-path"
    assert status.receipt_path == "/tmp/source-a/receipt.md"
    assert step.receipt_path == "/tmp/source-a/step-receipt.md"
