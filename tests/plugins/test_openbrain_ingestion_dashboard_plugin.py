"""Tests for the OpenBrain ingestion dashboard plugin backend."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load_plugin_api():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "openbrain_ingestion" / "dashboard" / "plugin_api.py"
    assert plugin_file.exists(), f"plugin file missing: {plugin_file}"
    spec = importlib.util.spec_from_file_location("openbrain_ingestion_dashboard_test", plugin_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    data_dir = home / "openbrain-ingestion-dashboard"
    data_dir.mkdir(parents=True)
    snapshot = {
        "schema_version": 1,
        "generated_at": "2026-06-21T04:39:54Z",
        "ingestion_run_id": "run_20260621_043954Z",
        "producer": {
            "kind": "panning_for_gold",
            "adapter": "otter_package",
            "source_type": "transcripts",
            "run_root_label": "otter-panning-20260621-043954Z",
            "artifacts": ["source-items.jsonl", "inventory.jsonl", "summary.json"],
        },
        "source_types": [{"id": "transcripts", "label": "Transcripts", "count": 3}],
        "source_units": [
            {
                "id": "transcript-a",
                "source_type": "transcripts",
                "label": "2023-11-30 EY AI Architecture Platform Development",
                "subtitle": "Otter transcript · 74 min",
                "source_ref": {
                    "kind": "otter_transcript",
                    "source_unit_id": "transcript-a",
                    "display_path": "/mnt/d/private/Otter/2023-11-30.md",
                    "absolute_path": "/mnt/d/private/Otter/2023-11-30.md",
                    "url": "http://localhost:9123/private",
                },
                "occurred_at": "2023-11-30T20:00:00Z",
                "processed_at": "2026-06-21T04:39:54Z",
                "thoughts": [
                    {
                        "id": "lineage_demo_7f3a",
                        "lineage_id": "lineage_demo_7f3a",
                        "candidate_id": "cand_42_18_a",
                        "title": "Political resistance risk",
                        "summary": "Imported durable lesson.",
                        "current_stage": "cortexdb",
                        "disposition": "imported",
                        "needs_review": False,
                        "topics": ["delivery", "stakeholder-risk"],
                        "confidence": 0.86,
                        "source_snippet": "The work was not only technical.",
                        "final_memory_text": "Stakeholder acceptance is delivery risk.",
                        "cortexdb_receipt": {
                            "id": "thought_demo_7f3a",
                            "type": "observation",
                            "captured_at": "2026-06-21T04:39:54Z",
                            "ingestion_run_id": "run_20260621_043954Z",
                            "source_unit_id": "transcript-a",
                            "candidate_id": "cand_42_18_a",
                        },
                        "related_memories": [
                            {"id": "thought_existing_a", "title": "Stakeholder acceptance", "score": 0.74}
                        ],
                    },
                    {
                        "id": "lineage_dup_1",
                        "lineage_id": "lineage_dup_1",
                        "candidate_id": "cand_dup_1",
                        "title": "Staged configurability",
                        "summary": "Merged duplicate configuration lesson.",
                        "current_stage": "deduped",
                        "disposition": "stopped",
                        "stopped_reason": "duplicate / merged",
                        "matched_memory_id": "thought_existing_91c",
                        "needs_review": False,
                        "topics": ["configuration"],
                        "source_snippet": "Start with a small configurable path.",
                        "related_memories": [
                            {"id": "thought_existing_91c", "title": "Start narrow", "score": 0.82}
                        ],
                    },
                    {
                        "id": "lineage_ready_1",
                        "lineage_id": "lineage_ready_1",
                        "candidate_id": "cand_ready_1",
                        "title": "Review source-backed wording",
                        "summary": "Needs review before import.",
                        "current_stage": "ready",
                        "disposition": "needs_review",
                        "needs_review": True,
                        "topics": ["review"],
                        "source_snippet": "This should be manually reviewed.",
                    },
                    {
                        "id": "lineage_stopped_cortexdb",
                        "lineage_id": "lineage_stopped_cortexdb",
                        "candidate_id": "cand_stopped_cortexdb",
                        "title": "Stopped row with accidental receipt",
                        "summary": "Must not expose receipt data.",
                        "current_stage": "cortexdb",
                        "disposition": "stopped",
                        "stopped_reason": "policy stop after attempted import",
                        "cortexdb_receipt": {"id": "must-not-leak", "type": "observation"},
                    },
                ],
            },
            {
                "id": "transcript-empty",
                "source_type": "transcripts",
                "label": "2024-06-04 Vendor status check-in",
                "subtitle": "Short transcript · no durable memory found",
                "occurred_at": "2024-06-04T20:00:00Z",
                "thoughts": [],
            },
            {
                "id": "transcript-b",
                "source_type": "transcripts",
                "label": "2023-12-01 Second transcript",
                "subtitle": "Otter transcript · duplicate candidate id fixture",
                "occurred_at": "2023-12-01T20:00:00Z",
                "thoughts": [
                    {
                        "id": "lineage_same_candidate_other_source",
                        "lineage_id": "lineage_same_candidate_other_source",
                        "candidate_id": "cand_42_18_a",
                        "title": "Same candidate ID, different lineage",
                        "summary": "Candidate IDs are not globally unique.",
                        "current_stage": "extracted",
                        "disposition": "in_progress",
                        "topics": ["identity"],
                    }
                ],
            },
        ],
    }
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


@pytest.fixture
def api_module(hermes_home):
    return _load_plugin_api()


@pytest.fixture
def client(api_module):
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/plugins/openbrain_ingestion")
    return TestClient(app)


def _all_cards(board: dict):
    return [card for row in board["rows"] for cards in row["columns"].values() for card in cards]


def test_source_types_prefers_transcripts_default(client):
    response = client.get("/api/plugins/openbrain_ingestion/source-types")
    assert response.status_code == 200
    payload = response.json()
    assert payload["default_source_type"] == "transcripts"
    assert payload["source_types"] == [{"id": "transcripts", "label": "Transcripts", "count": 3}]
    assert any(item["id"] == "needs_source_validation" for item in payload["policy_stop_definitions"])


def test_board_groups_thoughts_by_current_stage_once_and_splits_counts(client):
    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts")
    assert response.status_code == 200
    payload = response.json()
    assert [c["id"] for c in payload["columns"]] == [
        "extracted",
        "shaped",
        "deduped",
        "policy",
        "ready_for_cortexdb",
        "cortexdb",
    ]
    assert next(c for c in payload["columns"] if c["id"] == "ready_for_cortexdb")["label"] == "Ready for CortexDB"
    assert any(item["id"] == "sensitive_detail" for item in payload["policy_stop_definitions"])
    assert payload["total_counts"] == payload["visible_counts"] == payload["metrics"]
    assert payload["total_counts"]["source_units"] == 3
    assert payload["total_counts"]["thoughts"] == 5
    assert payload["total_counts"]["imported"] == 1
    assert payload["total_counts"]["stopped"] == 2
    assert payload["total_counts"]["ready_for_cortexdb"] == 1
    assert payload["total_counts"]["zero_thoughts"] == 1

    first = next(row for row in payload["rows"] if row["id"] == "transcript-a")
    all_card_ids = [card["id"] for cards in first["columns"].values() for card in cards]
    assert all_card_ids.count("lineage_demo_7f3a") == 1
    assert all_card_ids.count("lineage_dup_1") == 1
    assert all_card_ids.count("lineage_ready_1") == 1
    assert first["columns"]["cortexdb"][0]["id"] == "lineage_demo_7f3a"
    assert first["columns"]["deduped"][0]["id"] == "lineage_dup_1"
    assert first["columns"]["ready_for_cortexdb"][0]["id"] == "lineage_ready_1"

    for card in _all_cards(payload):
        assert card["source_unit_id"]
        assert card["lineage_id"]


def test_board_filter_imported_prunes_cards_and_excludes_zero_rows(client):
    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&filter=imported")
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload["rows"]] == ["transcript-a"]
    cards = _all_cards(payload)
    assert [card["id"] for card in cards] == ["lineage_demo_7f3a"]
    assert cards[0]["cortexdb_id"] == "thought_demo_7f3a"
    assert payload["total_counts"]["zero_thoughts"] == 1
    assert payload["visible_counts"]["zero_thoughts"] == 0
    assert payload["visible_counts"]["thoughts"] == 1


def test_board_filter_stopped_prunes_cards_and_does_not_expose_accidental_receipts(client):
    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&filter=stopped")
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload["rows"]] == ["transcript-a"]
    cards = _all_cards(payload)
    assert [card["id"] for card in cards] == ["lineage_dup_1", "lineage_stopped_cortexdb"]
    assert all(card.get("cortexdb_id") is None for card in cards)
    assert payload["visible_counts"]["stopped"] == 2
    assert payload["visible_counts"]["zero_thoughts"] == 0


def test_board_filter_needs_review_and_zero_thoughts(client):
    needs_review = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&filter=needs_review"
    ).json()
    assert [card["id"] for card in _all_cards(needs_review)] == ["lineage_ready_1"]
    assert needs_review["visible_counts"]["needs_review"] == 1
    assert [row["id"] for row in needs_review["rows"]] == ["transcript-a"]

    zero = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&filter=zero_thoughts"
    ).json()
    assert [row["id"] for row in zero["rows"]] == ["transcript-empty"]
    assert zero["visible_counts"]["source_units"] == 1
    assert zero["visible_counts"]["thoughts"] == 0


def test_board_search_matches_thought_topic_and_prunes_other_cards(client):
    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&search=configuration")
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload["rows"]] == ["transcript-a"]
    cards = _all_cards(payload)
    assert [card["id"] for card in cards] == ["lineage_dup_1"]
    assert payload["total_counts"]["thoughts"] == 5
    assert payload["visible_counts"]["thoughts"] == 1


def test_board_search_source_label_date_does_not_show_unrelated_cards(client):
    response = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&search=2023-11-30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == []
    assert _all_cards(payload) == []
    assert payload["total_counts"]["thoughts"] == 5
    assert payload["visible_counts"]["source_units"] == 0
    assert payload["visible_counts"]["thoughts"] == 0


def test_board_sort_options(client):
    newest = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&sort=newest").json()
    assert [row["id"] for row in newest["rows"]] == ["transcript-empty", "transcript-b", "transcript-a"]

    oldest = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts&sort=oldest").json()
    assert [row["id"] for row in oldest["rows"]] == ["transcript-a", "transcript-b", "transcript-empty"]

    most_thoughts = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&sort=most_thoughts"
    ).json()
    assert [row["id"] for row in most_thoughts["rows"]][0] == "transcript-a"

    most_stopped = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&sort=most_stopped"
    ).json()
    assert [row["id"] for row in most_stopped["rows"]][0] == "transcript-a"


def test_thought_detail_imported_has_normalized_timeline_and_receipt(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_demo_7f3a"
    )
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["lineage_id"] == "lineage_demo_7f3a"
    assert thought["source_unit"]["id"] == "transcript-a"
    assert thought["disposition"] == "imported"
    assert thought["cortexdb_receipt"]["id"] == "thought_demo_7f3a"
    assert thought["related_memories"][0]["title"] == "Stakeholder acceptance"
    assert [stage["id"] for stage in thought["stages"]] == [
        "extracted",
        "shaped",
        "deduped",
        "policy",
        "ready_for_cortexdb",
        "cortexdb",
    ]
    assert next(stage for stage in thought["stages"] if stage["id"] == "ready_for_cortexdb")["label"] == "Ready for CortexDB"


def test_thought_detail_stopped_duplicate_strips_receipt(client):
    response = client.get("/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_dup_1")
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["disposition"] == "stopped"
    assert thought["current_stage"] == "deduped"
    assert thought["stopped_reason"] == "duplicate / merged"
    assert thought["matched_memory_id"] == "thought_existing_91c"
    assert thought["stop_code"] == "duplicate"
    assert thought["stop_target_id"] == "thought_existing_91c"
    assert thought["stop_target_label"] == "Start narrow"
    assert thought["stop_stage_id"] == "deduped"
    assert "cortexdb_receipt" not in thought or thought["cortexdb_receipt"] in ({}, None)
    later = [stage for stage in thought["stages"] if stage["id"] in {"policy", "ready_for_cortexdb", "cortexdb"}]
    assert all(stage["status"] == "not_reached" for stage in later)


def test_source_scoped_lineage_identity_allows_duplicate_candidate_ids(client):
    first = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_demo_7f3a"
    ).json()["thought"]
    second = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-b/thoughts/lineage_same_candidate_other_source"
    ).json()["thought"]
    assert first["candidate_id"] == second["candidate_id"] == "cand_42_18_a"
    assert first["lineage_id"] != second["lineage_id"]

    by_candidate_only = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-b/thoughts/cand_42_18_a"
    )
    assert by_candidate_only.status_code == 404


def test_not_imported_current_stage_cortexdb_does_not_expose_receipts(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_stopped_cortexdb"
    )
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["current_stage"] == "cortexdb"
    assert thought["disposition"] == "stopped"
    assert "must-not-leak" not in json.dumps(thought)
    assert "cortexdb_receipt" not in thought or thought["cortexdb_receipt"] in ({}, None)


def test_unknown_thought_404(client):
    response = client.get("/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/missing")
    assert response.status_code == 404


def test_unknown_filter_and_sort_return_400(client):
    assert client.get("/api/plugins/openbrain_ingestion/board?filter=not-real").status_code == 400
    assert client.get("/api/plugins/openbrain_ingestion/board?sort=not-real").status_code == 400


def test_missing_snapshot_uses_sample(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    api = _load_plugin_api()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/openbrain_ingestion")
    c = TestClient(app)

    response = c.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "transcripts"
    assert payload["metrics"]["source_units"] >= 0


def test_snapshot_redacts_sensitive_fields_and_paths_in_api(client, hermes_home):
    path = hermes_home / "openbrain-ingestion-dashboard" / "snapshot.json"
    data = json.loads(path.read_text())
    data["producer"] = {
        "kind": "panning_for_gold",
        "adapter": "otter_package",
        "source_type": "transcripts",
        "run_root_label": "/mnt/d/private/run-root",
        "artifacts": ["/mnt/d/private/source-items.jsonl"],
    }
    data["source_units"][0]["source_path"] = "/mnt/d/private/Otter/source.md"
    data["source_units"][0]["raw_url"] = "http://localhost:9123/private"
    data["source_units"][0]["label"] = "see /mnt/d/private/My Folder/file.md"
    data["source_units"][0]["subtitle"] = "see C:\\Users\\Ken\\Private Folder\\file.md"
    data["source_units"][0]["thoughts"][0]["api_token"] = "super-secret-value"
    data["source_units"][0]["thoughts"][0]["summary"] = (
        "api_key=SECRET123 password: hunter2 "
        "https://example.test/callback?access_token=urlsecret&ok=1"
    )
    data["source_units"][0]["thoughts"][0]["final_memory_text"] = (
        "durable ordinary ID cand_42_18_a with token=tok123"
    )
    data["source_units"][0]["thoughts"][0]["source_snippet"] = "safe words " * 120
    data["source_units"][0]["thoughts"][0]["file_url"] = "file:///mnt/d/private/raw.txt"
    path.write_text(json.dumps(data), encoding="utf-8")

    thought = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_demo_7f3a"
    ).json()["thought"]
    assert thought["api_token"] == "[REDACTED]"
    assert thought["summary"] == (
        "api_key=[REDACTED] password: [REDACTED] "
        "https://example.test/callback?access_token=[REDACTED]&ok=1"
    )
    assert thought["final_memory_text"] == "durable ordinary ID cand_42_18_a with token=[REDACTED]"
    assert len(thought["source_snippet"]) <= 500
    assert "file_url" not in thought

    board = client.get("/api/plugins/openbrain_ingestion/board?source_type=transcripts").json()
    rendered = json.dumps(board)
    assert "/mnt/d/private" not in rendered
    assert "My Folder/file.md" not in rendered
    assert "C:\\\\Users\\\\Ken" not in rendered
    assert "Private Folder" not in rendered
    assert "http://localhost:9123/private" not in rendered
    for leaked in ("super-secret-value", "SECRET123", "hunter2", "urlsecret", "tok123"):
        assert leaked not in rendered
    assert "source-items.jsonl" in rendered or board["total_counts"]["source_units"] == 3
