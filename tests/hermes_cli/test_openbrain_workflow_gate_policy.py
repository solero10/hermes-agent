from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.openbrain_workflow_artifacts import workflow_run_dir
from hermes_cli.openbrain_workflow_contracts import build_heartbeat_note, redact_workflow_text
from hermes_cli.openbrain_workflow_supervisor import (
    ReceiptGateResult,
    evaluate_step_gate,
    validate_receipt_pointer,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _receipt_path(run_id="obwf_gate", step_key="panning_for_gold") -> Path:
    root = workflow_run_dir(run_id) / "steps" / step_key
    root.mkdir(parents=True, exist_ok=True)
    return root / "receipt-pointer.json"


def test_valid_receipt_pointer_passes(hermes_home):
    pointer = _receipt_path()
    receipt = pointer.parent / "candidate-receipt.json"
    receipt.write_text(json.dumps({"ok": True}), encoding="utf-8")
    pointer.write_text(json.dumps({
        "workflow_run_id": "obwf_gate",
        "step_key": "panning_for_gold",
        "source_unit_id": "source-a",
        "candidate_ids": ["cand_1"],
        "receipt_path": str(receipt),
        "dashboard_verification": {"ok": True, "visible_candidate_count": 1},
    }), encoding="utf-8")

    result = validate_receipt_pointer("obwf_gate", "panning_for_gold", pointer)

    assert result.ok is True
    assert result.payload["candidate_ids"] == ["cand_1"]


def test_missing_malformed_and_outside_receipts_block(hermes_home):
    missing = validate_receipt_pointer("obwf_gate", "panning_for_gold", _receipt_path())
    assert missing.ok is False
    assert "missing receipt pointer" in missing.reason

    pointer = _receipt_path()
    pointer.write_text("{not json", encoding="utf-8")
    malformed = validate_receipt_pointer("obwf_gate", "panning_for_gold", pointer)
    assert malformed.ok is False
    assert "malformed receipt pointer" in malformed.reason

    pointer.write_text(json.dumps({
        "source_unit_id": "source-a",
        "receipt_path": str(hermes_home / "outside-receipt.json"),
    }), encoding="utf-8")
    outside = validate_receipt_pointer("obwf_gate", "panning_for_gold", pointer)
    assert outside.ok is False
    assert "outside workflow run" in outside.reason


def test_receipt_path_traversal_blocks(hermes_home):
    pointer = _receipt_path()
    pointer.write_text(json.dumps({
        "source_unit_id": "source-a",
        "receipt_path": "../../outside.json",
    }), encoding="utf-8")

    result = validate_receipt_pointer("obwf_gate", "panning_for_gold", pointer)

    assert result.ok is False
    assert "outside workflow run" in result.reason


def test_gate_policy_blocks_review_and_import_without_approval(hermes_home):
    pointer = _receipt_path(step_key="human_review")
    pointer.write_text(json.dumps({"source_unit_id": "source-a"}), encoding="utf-8")
    receipt = validate_receipt_pointer("obwf_gate", "human_review", pointer)

    review_gate = evaluate_step_gate(
        workflow_run_id="obwf_gate",
        step_key="human_review",
        receipt=receipt,
    )
    assert review_gate.ok is False
    assert "review-required" in review_gate.reason

    import_gate = evaluate_step_gate(
        workflow_run_id="obwf_gate",
        step_key="ready_cortexdb_import",
        receipt=ReceiptGateResult(ok=True, reason=None, payload={
            "source_unit_id": "source-a",
            "dashboard_verification": {"ok": True},
        }),
        review_approved=False,
    )
    assert import_gate.ok is False
    assert "review approval" in import_gate.reason


def test_import_gate_requires_dashboard_verification(hermes_home):
    gate = evaluate_step_gate(
        workflow_run_id="obwf_gate",
        step_key="ready_cortexdb_import",
        receipt=ReceiptGateResult(ok=True, reason=None, payload={
            "source_unit_id": "source-a",
            "receipt_path": str(workflow_run_dir("obwf_gate") / "steps" / "ready_cortexdb_import" / "receipt.json"),
            "dashboard_verification": {"ok": False},
        }),
        review_approved=True,
        import_receipt_ok=True,
    )

    assert gate.ok is False
    assert "dashboard verification" in gate.reason


def test_dedupe_exact_only_cannot_claim_semantic(hermes_home):
    gate = evaluate_step_gate(
        workflow_run_id="obwf_gate",
        step_key="dedupe",
        receipt=ReceiptGateResult(ok=True, reason=None, payload={
            "source_unit_id": "source-a",
            "dedupe_mode": "exact-only",
            "semantic_dedupe_claimed": True,
            "dashboard_verification": {"ok": True},
        }),
    )

    assert gate.ok is False
    assert "semantic dedupe" in gate.reason


def test_status_heartbeat_text_sanitizes_private_sentinel():
    raw = "SECRET_SENTINEL_SHOULD_NOT_APPEAR api_key=SECRET123 /home/kernk/private/transcript.txt"

    note = build_heartbeat_note(
        workflow_run_id="obwf_gate",
        step_key="panning_for_gold",
        source_unit_id="source-a",
        phase=raw,
        current_candidate_title=raw,
    )
    redacted = redact_workflow_text(raw)

    assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in note
    assert "SECRET123" not in note
    assert "/home/kernk/private" not in note
    assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in redacted
