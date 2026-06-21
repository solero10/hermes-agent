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


def _snapshot(exporter):
    return exporter.build_snapshot_from_panning_run(
        run_root=FIXTURE_RUN_ROOT,
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
    assert "cortexdb_receipt" not in duplicate
    assert "cortexdb_id" not in duplicate

    pending = thoughts["cand_003_pending"]
    assert pending["lineage_id"].startswith("lineage-")
    assert pending["current_stage"] == "ready_for_cortexdb"
    assert pending["disposition"] == "in_progress"


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
    assert board["total_counts"]["ready_for_cortexdb"] == 1
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
