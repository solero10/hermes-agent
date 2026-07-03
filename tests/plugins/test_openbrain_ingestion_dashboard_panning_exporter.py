"""Tests for the Panning-for-Gold dashboard snapshot exporter."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_RUN_ROOT = REPO_ROOT / "tests" / "fixtures" / "openbrain_ingestion_panning_run"


def _load_module(relative_path: str, module_name: str):
    module_file = REPO_ROOT / relative_path
    assert module_file.exists(), f"module file missing: {module_file}"
    spec = importlib.util.spec_from_file_location(module_name, module_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_plugin_api():
    return _load_module(
        "plugins/openbrain_ingestion/dashboard/plugin_api.py",
        "openbrain_ingestion_dashboard_panning_api_test",
    )


def _load_exporter():
    return _load_module(
        "plugins/openbrain_ingestion/dashboard/snapshot_exporter.py",
        "openbrain_ingestion_dashboard_panning_exporter_test",
    )


def _snapshot(exporter, run_root=FIXTURE_RUN_ROOT):
    return exporter.build_snapshot_from_panning_run(
        run_root=run_root,
        source_type="transcripts",
        adapter="otter_package",
        generated_at="2026-06-21T04:39:54Z",
    )


def _all_cards(board: dict):
    return [card for row in board["rows"] for cards in row["columns"].values() for card in cards]


def test_panning_run_exports_valid_sanitized_dashboard_snapshot():
    exporter = _load_exporter()
    api = _load_plugin_api()

    snapshot = _snapshot(exporter)
    validated = api.Snapshot.model_validate(snapshot).model_dump(mode="json", exclude_none=True)

    assert validated["schema_version"] == 1
    assert validated["ingestion_run_id"] == "run_20260621_043954Z"
    assert validated["producer"] == {
        "kind": "panning_for_gold",
        "adapter": "otter_package",
        "source_type": "transcripts",
        "run_root_label": "openbrain_ingestion_panning_run",
        "artifacts": [
            "source-items.jsonl",
            "inventory.jsonl",
            "dedupe-receipts.jsonl",
            "capture-candidates.jsonl",
            "capture-audit.jsonl",
            "summary.json",
        ],
    }
    assert validated["source_types"] == [{"id": "transcripts", "label": "Transcripts", "count": 3}]
    assert {unit["source_type"] for unit in validated["source_units"]} == {"transcripts"}
    assert {unit.get("panning_source_type") for unit in validated["source_units"]} == {"otter-package"}

    rendered = json.dumps(validated)
    assert "/mnt/d/private" not in rendered
    assert "C:\\Users\\Ken" not in rendered
    assert "localhost:9123" not in rendered
    assert "should-not-leak" not in rendered

    first_unit = next(unit for unit in validated["source_units"] if unit["label"].startswith("2023-11-30"))
    assert first_unit["adapter_source_id"] == "otter-package:transcript-001"
    assert first_unit["id"].startswith("transcripts-otter-package-transcript-001-")
    assert first_unit["source_ref"] == {
        "kind": "otter_transcript",
        "source_unit_id": first_unit["id"],
        "display_path": "Otter/2023-11-30 EY AI Architecture Platform Development.md",
    }


def test_imported_duplicate_and_derived_lineage_rows_map_correctly_without_sqlite():
    exporter = _load_exporter()
    assert not (FIXTURE_RUN_ROOT / "panning-run.sqlite").exists()

    snapshot = _snapshot(exporter)
    thoughts = {
        thought["candidate_id"]: thought
        for unit in snapshot["source_units"]
        for thought in unit.get("thoughts", [])
    }

    imported = thoughts["cand_001_imported"]
    assert imported["lineage_id"] == "thread_imported_architecture"
    assert imported["current_stage"] == "cortexdb"
    assert imported["disposition"] == "imported"
    assert imported["cortexdb_receipt"]["id"] == "thought_demo_7f3a"
    assert imported["cortexdb_receipt"]["source_unit_id"]
    assert imported["stage_detail"]["cortexdb"]["id"] == "thought_demo_7f3a"
    assert imported["stage_detail"]["cortexdb"]["stored_text"] == "Stakeholder acceptance is delivery risk, not only a communications concern."
    assert imported["cortexdb_id"] == "thought_demo_7f3a"
    assert imported["related_memories"][0]["id"] == "thought_existing_a"

    duplicate = thoughts["cand_002_duplicate"]
    assert duplicate["lineage_id"] == "thread_duplicate_config"
    assert duplicate["current_stage"] == "deduped"
    assert duplicate["disposition"] == "stopped"
    assert duplicate["stopped_reason"] == "merged_exact duplicate"
    assert duplicate["matched_memory_id"] == "thought_existing_91c"
    assert duplicate["related_memories"] == [
        {"id": "thought_existing_91c", "title": "Start narrow before widening configurability", "score": 0.82}
    ]
    assert duplicate["stage_detail"]["deduped"]["decision"] == "duplicate"
    assert duplicate["stage_detail"]["deduped"]["matched_memory_id"] == "thought_existing_91c"
    assert "cortexdb_receipt" not in duplicate
    assert "cortexdb_id" not in duplicate

    pending = thoughts["cand_003_pending"]
    assert pending["lineage_id"].startswith("lineage-")
    assert pending["current_stage"] == "shaped"
    assert pending["disposition"] == "in_progress"


def test_degraded_exact_only_dedupe_keeps_candidate_in_shaped(tmp_path):
    exporter = _load_exporter()
    run_root = tmp_path / "semantic-pending-run"
    shutil.copytree(FIXTURE_RUN_ROOT, run_root)

    candidates_path = run_root / "capture-candidates.jsonl"
    candidate = {
        "source_id": "otter-package:transcript-003",
        "candidate_id": "cand_008_exact_only",
        "content_fingerprint": "sha256:fp_exact_only_008",
        "title": "Exact-only candidate should wait for semantic dedupe",
        "summary": "Candidate passed exact dedupe only and should not be Ready yet.",
        "final_memory_text": "Exact-only dedupe is not enough for OpenBrain capture readiness.",
        "stage": "ready",
    }
    with candidates_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate, sort_keys=True) + "\n")

    receipts_path = run_root / "dedupe-receipts.jsonl"
    receipt = {
        "source_id": "otter-package:transcript-003",
        "candidate_id": "cand_008_exact_only",
        "content_fingerprint": "sha256:fp_exact_only_008",
        "decision": "degraded_exact_only",
        "capture_action": "degraded_created",
        "degraded_reason": "semantic matcher not configured; exact-only batch check",
        "embedding_available": False,
        "matched_similarity": None,
        "matched_thought_id": None,
        "matches": [],
    }
    with receipts_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")

    thoughts = {
        thought["candidate_id"]: thought
        for unit in _snapshot(exporter, run_root=run_root)["source_units"]
        for thought in unit.get("thoughts", [])
    }

    exact_only = thoughts["cand_008_exact_only"]
    assert exact_only["current_stage"] == "shaped"
    assert exact_only["disposition"] == "in_progress"
    assert exact_only["stage_detail"]["deduped"]["method"] == "exact_only_degraded"
    assert exact_only["stage_detail"]["deduped"]["evidence_note"] == "semantic matcher not configured; exact-only batch check"


def test_candidate_formation_trace_flat_fields_export_to_dashboard_snapshot(tmp_path):
    exporter = _load_exporter()
    run_root = tmp_path / "formation-trace-run"
    shutil.copytree(FIXTURE_RUN_ROOT, run_root)

    candidate = {
        "source_id": "otter-package:transcript-003",
        "candidate_id": "cand_009_formation_trace",
        "content_fingerprint": "sha256:fp_formation_trace_009",
        "title": "Formation trace candidate",
        "summary": "Candidate carries shaping provenance.",
        "final_memory_text": "Formation trace should survive exporter normalization.",
        "stage": "deduped",
        "primary_lineage_ids": ["pan:trace-01", "pan:trace-02"],
        "primary_lineage_cards": [
            {"lineage_id": "pan:trace-01", "stage": "extracted", "title": "Input card"}
        ],
        "additional_context_used": [
            {"kind": "source_title", "label": "Source title", "text": "Formation trace source"}
        ],
        "llm_input_text": "Exact input package token=secret123 see /mnt/d/private/source.md",
        "llm_output_text": "Exact shaped output.",
        "merge_note": "Merged two extracted cards.",
        "created_by": {"kind": "llm", "model": "gpt-5.5"},
        "created_at": "2026-06-21T04:41:00Z",
        "gate_events": [{"stage": "policy", "status": "pending", "decision": "not_run"}],
    }
    with (run_root / "capture-candidates.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate, sort_keys=True) + "\n")

    thoughts = {
        thought["candidate_id"]: thought
        for unit in _snapshot(exporter, run_root=run_root)["source_units"]
        for thought in unit.get("thoughts", [])
    }

    trace = thoughts["cand_009_formation_trace"]["formation_trace"]
    stage_trace = thoughts["cand_009_formation_trace"]["stage_detail"]["shaped"]
    assert thoughts["cand_009_formation_trace"]["current_stage"] == "deduped"
    assert trace["stage"] == "shaped"
    assert trace["primary_lineage_ids"] == ["pan:trace-01", "pan:trace-02"]
    assert trace["primary_lineage_cards"][0]["title"] == "Input card"
    assert trace["additional_context_used"][0]["kind"] == "source_title"
    assert trace["llm_output_text"] == "Exact shaped output."
    assert trace["created_by"]["model"] == "gpt-5.5"
    assert trace["gate_events"][0]["stage"] == "policy"
    assert stage_trace["llm_output_text"] == trace["llm_output_text"]

    rendered = json.dumps(trace)
    assert "secret123" not in rendered
    assert "/mnt/d/private" not in rendered


def test_inventory_only_not_applicable_rows_surface_as_stopped_cards(tmp_path, monkeypatch):
    exporter = _load_exporter()
    api = _load_plugin_api()
    run_root = tmp_path / "stopped-run"
    shutil.copytree(FIXTURE_RUN_ROOT, run_root)

    inventory_path = run_root / "inventory.jsonl"
    inventory_rows = inventory_path.read_text(encoding="utf-8").splitlines()
    inventory_rows.extend(
        [
            json.dumps(
                {
                    "source_id": "otter-package:transcript-001",
                    "candidate_id": "cand_004_reference_merge",
                    "thread_id": "thread_reference_merge",
                    "content_fingerprint": "sha256:fp_reference_004",
                    "capture_content": "REFERENCE / MERGE: Historical background on patient coordination.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "REFERENCE / MERGE",
                    "reason": "Historical background that should be merged into a source summary instead of stored as a separate thought.",
                    "title": "Historical background on patient coordination",
                    "idea": "Ken uses patient advocacy to navigate healthcare systems.",
                    "topics": ["healthcare advocacy"],
                    "connections": ["healthcare advocacy"],
                    "metadata": {
                        "historical_archive": {
                            "historical_memory_type": "reference_or_merge",
                            "historical_verdict": "REFERENCE / MERGE",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_005_obsolete_skip",
                    "thread_id": "thread_obsolete_skip",
                    "content_fingerprint": "sha256:fp_obsolete_005",
                    "capture_content": "OBSOLETE / SKIP: Pack the car the night before early departure.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "OBSOLETE / SKIP",
                    "reason": "Time-specific travel logistics that should not surface as a durable thought.",
                    "title": "Pack the car the night before early departure",
                    "idea": "Pack items into the car the night before early departure.",
                    "topics": ["family travel logistics"],
                    "connections": ["family travel logistics"],
                    "metadata": {
                        "historical_archive": {
                            "historical_memory_type": "obsolete_or_skip",
                            "historical_verdict": "OBSOLETE / SKIP",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_006_durable_context",
                    "thread_id": "thread_durable_context",
                    "content_fingerprint": "sha256:fp_durable_006",
                    "capture_content": "REFERENCE / MERGE: Hawaii remains preferred long-term environment.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "REFERENCE / MERGE",
                    "reason": "Useful durable context for values and relocation motivation, but not a separate immediate action.",
                    "title": "Hawaii remains preferred long-term environment",
                    "idea": "Ken sees Hawaii as the preferred long-term environment.",
                    "category": "personal",
                    "connections": ["Hawaii relocation", "values"],
                    "metadata": {
                        "historical_archive": {
                            "historical_memory_type": "reference_or_merge",
                            "historical_verdict": "REFERENCE / MERGE",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_007_duplicate_wording_not_dedupe",
                    "thread_id": "thread_duplicate_wording_not_dedupe",
                    "content_fingerprint": "sha256:fp_duplicate_wording_007",
                    "capture_content": "RESEARCH MORE: A staged release might duplicate work if the architecture is wrong.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "RESEARCH MORE",
                    "reason": "This is a planning concern with prose about duplicate work, not a dedupe result.",
                    "title": "Staged release may duplicate work",
                    "idea": "A staged release might duplicate work if the architecture is wrong.",
                    "category": "technical",
                    "dedupe": {"decision": "not_capture_candidate", "capture_action": "not_applicable"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_009_needs_current_validation",
                    "thread_id": "thread_needs_current_validation",
                    "content_fingerprint": "sha256:fp_validation_009",
                    "capture_content": "NEEDS CURRENT VALIDATION: Relationship note from outline only.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "NEEDS CURRENT VALIDATION",
                    "reason": "Potentially useful, but verify against the full transcript before capture.",
                    "title": "Relationship note from outline only",
                    "idea": "A relationship note appears only in an auto-generated outline.",
                    "category": "relationship",
                    "metadata": {
                        "historical_archive": {
                            "historical_memory_type": "needs_current_validation",
                            "historical_verdict": "NEEDS CURRENT VALIDATION",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_010_sensitive_detail",
                    "thread_id": "thread_sensitive_detail",
                    "content_fingerprint": "sha256:fp_sensitive_010",
                    "capture_content": "PARK: Case/reference handle for a benefits issue.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "PARK",
                    "reason": "The raw identifier is sensitive and should not be broadly captured.",
                    "title": "Case/reference handle for follow-up",
                    "idea": "A case/reference handle exists for a benefits issue.",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_011_stale_task",
                    "thread_id": "thread_stale_task",
                    "content_fingerprint": "sha256:fp_stale_011",
                    "capture_content": "RESEARCH MORE: Follow up with a 2023 project owner.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "RESEARCH MORE",
                    "reason": "This should not be reactivated without confirming whether it was completed.",
                    "title": "Follow up with a 2023 project owner",
                    "idea": "Someone needed a 2023 follow-up.",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_012_obsolete_internal",
                    "thread_id": "thread_obsolete_internal",
                    "content_fingerprint": "sha256:fp_internal_012",
                    "capture_content": "PARK: EY internal process checklist.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "PARK",
                    "reason": "Obsolete EY internal process mechanics with no current use.",
                    "title": "EY internal process checklist",
                    "idea": "The source described an EY internal process checklist.",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "source_id": "otter-package:transcript-003",
                    "candidate_id": "cand_013_too_thin",
                    "thread_id": "thread_too_thin",
                    "content_fingerprint": "sha256:fp_too_thin_013",
                    "capture_content": "PARK: Calendar follow-up.",
                    "capture_recommendation": False,
                    "final_capture_action": "not_applicable",
                    "verdict": "PARK",
                    "reason": "Too vague and not enough detail to preserve safely.",
                    "title": "Calendar follow-up",
                    "idea": "There was a calendar follow-up.",
                },
                ensure_ascii=False,
            ),
        ]
    )
    inventory_path.write_text("\n".join(inventory_rows) + "\n", encoding="utf-8")

    validated = api.Snapshot.model_validate(_snapshot(exporter, run_root=run_root)).model_dump(mode="json", exclude_none=True)
    thoughts = {
        thought["candidate_id"]: thought
        for unit in validated["source_units"]
        for thought in unit.get("thoughts", [])
    }

    reference = thoughts["cand_004_reference_merge"]
    assert reference["disposition"] == "in_progress"
    assert reference["current_stage"] == "shaped"
    assert "stop_code" not in reference
    assert "stopped_reason" not in reference

    obsolete = thoughts["cand_005_obsolete_skip"]
    assert obsolete["disposition"] == "stopped"
    assert obsolete["current_stage"] == "policy"
    assert obsolete["stop_code"] == "no_durable_value"
    assert obsolete["stop_stage_id"] == "policy"
    assert obsolete["stopped_reason"].startswith("Time-specific travel logistics")

    durable_context = thoughts["cand_006_durable_context"]
    assert durable_context["disposition"] == "in_progress"
    assert durable_context["current_stage"] == "shaped"
    assert "stop_code" not in durable_context
    assert "stopped_reason" not in durable_context

    duplicate_wording = thoughts["cand_007_duplicate_wording_not_dedupe"]
    assert duplicate_wording["disposition"] == "in_progress"
    assert duplicate_wording["current_stage"] == "shaped"
    assert "stop_code" not in duplicate_wording

    needs_validation = thoughts["cand_009_needs_current_validation"]
    assert needs_validation["disposition"] == "stopped"
    assert needs_validation["current_stage"] == "policy"
    assert needs_validation["stop_code"] == "needs_source_validation"

    assert thoughts["cand_010_sensitive_detail"]["stop_code"] == "sensitive_detail"
    assert thoughts["cand_011_stale_task"]["stop_code"] == "stale_task"
    assert thoughts["cand_012_obsolete_internal"]["stop_code"] == "obsolete_internal"
    assert thoughts["cand_013_too_thin"]["stop_code"] == "too_thin"

    assert sum(len(unit.get("thoughts", [])) for unit in validated["source_units"]) == 12

    hermes_home = tmp_path / ".hermes"
    out_path = hermes_home / "openbrain-ingestion-dashboard" / "snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(validated), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    api = _load_plugin_api()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/openbrain_ingestion")
    client = TestClient(app)

    board = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts").json()
    assert board["total_counts"]["thoughts"] == 12
    assert board["total_counts"]["stopped"] == 7
    assert board["total_counts"]["deduped"] == 1
    assert board["total_counts"]["policy"] == 6
    assert board["total_counts"]["shaped"] == 4
    assert board["total_counts"]["ready_for_cortexdb"] == 0
    stop_codes = {card.get("stop_code") for card in _all_cards(board) if card.get("stop_code")}
    assert {
        "duplicate",
        "no_durable_value",
        "needs_source_validation",
        "sensitive_detail",
        "stale_task",
        "obsolete_internal",
        "too_thin",
    }.issubset(stop_codes)


def test_exported_snapshot_is_read_by_dashboard_board_api(tmp_path, monkeypatch):
    exporter = _load_exporter()
    hermes_home = tmp_path / ".hermes"
    out_path = hermes_home / "openbrain-ingestion-dashboard" / "snapshot.json"
    exporter.export_dashboard_snapshot(
        run_root=FIXTURE_RUN_ROOT,
        source_type="transcripts",
        adapter="otter_package",
        out_path=out_path,
        generated_at="2026-06-21T04:39:54Z",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    api = _load_plugin_api()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/openbrain_ingestion")
    client = TestClient(app)

    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts")
    assert response.status_code == 200
    board = response.json()
    assert board["source_type"] == "transcripts"
    assert board["total_counts"] == board["visible_counts"] == board["metrics"]
    assert board["total_counts"]["source_units"] == 3
    assert board["total_counts"]["thoughts"] == 3
    assert board["total_counts"]["imported"] == 1
    assert board["total_counts"]["stopped"] == 1
    assert board["total_counts"]["in_progress"] == 1
    assert board["total_counts"]["cortexdb"] == 1
    assert board["total_counts"]["deduped"] == 1
    assert board["total_counts"]["shaped"] == 1
    assert board["total_counts"]["ready_for_cortexdb"] == 0
    assert board["total_counts"]["zero_thoughts"] == 1

    first_row = next(row for row in board["rows"] if row["label"].startswith("2023-11-30"))
    assert first_row["columns"]["cortexdb"][0]["id"] == "thread_imported_architecture"
    assert first_row["columns"]["cortexdb"][0]["cortexdb_id"] == "thought_demo_7f3a"
    assert first_row["columns"]["deduped"][0]["id"] == "thread_duplicate_config"
    assert first_row["columns"]["deduped"][0].get("cortexdb_id") is None
    assert {card["candidate_id"] for card in _all_cards(board)} == {
        "cand_001_imported",
        "cand_002_duplicate",
        "cand_003_pending",
    }

    rendered = json.dumps(board)
    assert "/mnt/d/private" not in rendered
    assert "C:\\Users\\Ken" not in rendered
    assert "localhost:9123" not in rendered


def test_write_snapshot_atomic_preserves_previous_good_snapshot_and_removes_tmp(tmp_path):
    exporter = _load_exporter()
    snapshot = _snapshot(exporter)
    out_path = tmp_path / "snapshot.json"

    exporter.write_snapshot_atomic(out_path, snapshot)
    previous = out_path.read_bytes()
    assert json.loads(previous)["schema_version"] == 1
    assert not list(tmp_path.glob(".*.tmp"))

    invalid = copy.deepcopy(snapshot)
    invalid["schema_version"] = 2
    with pytest.raises(Exception):
        exporter.write_snapshot_atomic(out_path, invalid)

    assert out_path.read_bytes() == previous
    assert not list(tmp_path.glob(".*.tmp"))


def test_missing_source_items_jsonl_raises_clear_exception(tmp_path):
    exporter = _load_exporter()
    (tmp_path / "summary.json").write_text('{"run_id":"missing-source-items"}', encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="source-items.jsonl"):
        exporter.build_snapshot_from_panning_run(
            run_root=tmp_path,
            source_type="transcripts",
            adapter="otter_package",
        )


@pytest.mark.parametrize("stage_field", ("current_stage", "stage", "status"))
def test_invalid_explicit_candidate_stage_raises_export_error(tmp_path, stage_field):
    exporter = _load_exporter()
    run_root = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN_ROOT, run_root)

    candidates_path = run_root / "capture-candidates.jsonl"
    candidates = [
        json.loads(line)
        for line in candidates_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates[0][stage_field] = "not_a_stage"
    candidates_path.write_text(
        "\n".join(json.dumps(candidate, sort_keys=True) for candidate in candidates) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="not_a_stage"):
        exporter.build_snapshot_from_panning_run(
            run_root=run_root,
            source_type="transcripts",
            adapter="otter_package",
            generated_at="2026-06-21T04:39:54Z",
        )
