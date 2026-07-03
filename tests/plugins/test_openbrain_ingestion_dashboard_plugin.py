"""Tests for the OpenBrain ingestion dashboard plugin backend."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import re
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
                        "metadata": {
                            "type": "observation",
                            "source": "panning",
                            "source_type": "transcripts",
                            "topics": ["delivery", "stakeholder-risk"],
                        },
                        "confidence": 0.86,
                        "source_snippet": "The work was not only technical.",
                        "final_memory_text": "Stakeholder acceptance is delivery risk.",
                        "cortexdb_receipt": {
                            "id": "thought_demo_7f3a",
                            "type": "observation",
                            "captured_at": "2026-06-21T04:39:54Z",
                            "updated_at": "2026-06-21T04:40:11Z",
                            "content_fingerprint": "receipt-fingerprint-demo",
                            "importance": 4,
                            "quality_score": 82,
                            "sensitivity_tier": "standard",
                            "enriched": True,
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
                        "formation_trace": {},
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
                        "formation_trace": {
                            "version": 1,
                            "stage": "shaped",
                            "created_at": "2026-06-21T04:40:00Z",
                            "created_by": {"kind": "llm", "provider": "openai-codex", "model": "gpt-5.5"},
                            "primary_lineage_ids": ["pan:test-01", "pan:test-02"],
                            "primary_lineage_cards": [
                                {
                                    "lineage_id": "pan:test-01",
                                    "stage": "extracted",
                                    "title": "First extracted input",
                                    "summary": "The first source-backed input for shaping.",
                                    "quote": "Q" * 800,
                                    "topics": ["review"],
                                }
                            ],
                            "additional_context_used": [
                                {
                                    "kind": "source_title",
                                    "label": "Source title",
                                    "text": "2023-11-30 EY AI Architecture Platform Development",
                                    "source": "source_unit.label",
                                },
                                {
                                    "kind": "neighboring_card",
                                    "lineage_id": "pan:test-03",
                                    "title": "Neighboring extracted input",
                                    "summary": "Neighboring summary.",
                                    "quote": "Neighboring source quote.",
                                },
                                {
                                    "kind": "source_metadata",
                                    "label": "Source metadata",
                                    "values": {
                                        "section": "Formation trace fixture",
                                        "api_key": "super-secret-source-metadata-value",
                                        "debug_path": "/mnt/d/private/source metadata.txt",
                                    },
                                }
                            ],
                            "llm_input_text": "Exact input package api_key=super-secret-value see /mnt/d/private/Input Folder/file.md "
                            + ("safe words " * 2200),
                            "llm_output_text": "Shaped output text for review.",
                            "output_title": "Review source-backed wording",
                            "suggested_type": "observation",
                            "merge_note": "Merged extracted inputs into one shaped candidate.",
                            "hidden_reasoning": "Do not expose hidden reasoning.",
                            "raw_prompt": "Do not expose raw debug prompts.",
                            "gate_events": [
                                {"stage": "policy", "status": "pending", "decision": "not_run"}
                            ],
                        },
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
                        "stage_detail": {
                            "cortexdb": {
                                "id": "must-not-leak-stage",
                                "stored_text": "must not leak stage receipt",
                            }
                        },
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
        "policy",
        "deduped",
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
    assert first["source_date"] == "2023-11-30T20:00:00Z"
    assert first["occurred_at"] == "2023-11-30T20:00:00Z"
    assert first["processed_at"] == "2026-06-21T04:39:54Z"
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
    detail = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_stopped_cortexdb"
    ).json()["thought"]
    rendered = json.dumps(detail)
    assert "cortexdb_receipt" not in detail
    assert "must-not-leak" not in rendered
    assert "must-not-leak-stage" not in rendered
    assert "must not leak stage receipt" not in rendered
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


def test_board_sort_uses_explicit_source_date_before_runtime_dates(api_module):
    rows = [
        {"id": "runtime-newer", "source_date": None, "occurred_at": "2024-06-01T00:00:00Z"},
        {"id": "source-date-newest", "source_date": "2024-07-01T00:00:00Z", "occurred_at": "2020-01-01T00:00:00Z"},
    ]

    newest = api_module._sort_rows([dict(row) for row in rows], "newest")
    assert [row["id"] for row in newest] == ["source-date-newest", "runtime-newer"]

    oldest = api_module._sort_rows([dict(row) for row in rows], "oldest")
    assert [row["id"] for row in oldest] == ["runtime-newer", "source-date-newest"]


def test_board_source_date_filters_are_inclusive_and_source_scoped(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&date_from=2023-12-01&date_to=2023-12-01"
    )
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload["rows"]] == ["transcript-b"]
    assert payload["total_counts"]["source_units"] == 3
    assert payload["visible_counts"]["source_units"] == 1
    assert payload["date_from"] == "2023-12-01"
    assert payload["date_to"] == "2023-12-01"

    open_ended = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&date_from=2024-01-01"
    ).json()
    assert [row["id"] for row in open_ended["rows"]] == ["transcript-empty"]

    us_date = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&date_from=12/01/2023&date_to=12/01/2023"
    ).json()
    assert [row["id"] for row in us_date["rows"]] == ["transcript-b"]


def test_board_source_date_filter_rejects_bad_bounds(client):
    assert client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&date_from=not-a-date"
    ).status_code == 400
    assert client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&date_from=2024-01-01&date_to=2023-01-01"
    ).status_code == 400


def test_thought_detail_imported_has_normalized_timeline_and_receipt(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_demo_7f3a"
    )
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["lineage_id"] == "lineage_demo_7f3a"
    assert thought["source_unit"]["id"] == "transcript-a"
    assert thought["source_unit"]["source_date"] == "2023-11-30T20:00:00Z"
    assert thought["disposition"] == "imported"
    assert thought["cortexdb_receipt"]["id"] == "thought_demo_7f3a"
    assert thought["related_memories"][0]["title"] == "Stakeholder acceptance"
    assert [stage["id"] for stage in thought["stages"]] == [
        "extracted",
        "shaped",
        "policy",
        "deduped",
        "ready_for_cortexdb",
        "cortexdb",
    ]
    assert next(stage for stage in thought["stages"] if stage["id"] == "ready_for_cortexdb")["label"] == "Ready for CortexDB"

    db_fields = {field["name"]: field for field in thought["database_fields"]}
    assert list(db_fields)[:4] == ["id", "content", "embedding", "metadata"]
    assert db_fields["id"]["value"] == "thought_demo_7f3a"
    assert db_fields["content"]["value"] == "Stakeholder acceptance is delivery risk."
    assert db_fields["embedding"]["value"] == "[embedding vector omitted]"
    assert db_fields["source"]["value"] == "panning"
    assert db_fields["source_type"]["value"] == "transcripts"
    assert db_fields["metadata"]["value"]["topics"] == ["delivery", "stakeholder-risk"]
    assert db_fields["metadata"]["value"]["source_unit_id"] == "transcript-a"
    assert db_fields["created_at"]["value"] == "2026-06-21T04:39:54Z"
    assert db_fields["updated_at"]["value"] == "2026-06-21T04:40:11Z"
    assert db_fields["content_fingerprint"]["value"] == "receipt-fingerprint-demo"
    assert db_fields["importance"]["value"] == 4
    assert db_fields["quality_score"]["value"] == 82
    assert db_fields["sensitivity_tier"]["value"] == "standard"
    assert db_fields["enriched"]["value"] is True
    assert db_fields["derivation_layer"]["value"] == "primary"
    assert thought["stage_detail"]["cortexdb"]["id"] == "thought_demo_7f3a"
    assert thought["stage_detail"]["cortexdb"]["source_unit_id"] == "transcript-a"


def test_thought_detail_accepts_column_specific_stage_detail_payloads(client, hermes_home):
    path = hermes_home / "openbrain-ingestion-dashboard" / "snapshot.json"
    data = json.loads(path.read_text())
    extracted = data["source_units"][2]["thoughts"][0]
    extracted["stage_detail"] = {
        "extracted": {
            "status": "covered_by_shaped",
            "source_section": "Therapy transcript",
            "extraction_method": "panning_for_gold",
            "used_by_shape_ids": ["shape:test-01"],
            "promotion_note": "Covered by a shaped card.",
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-b/thoughts/lineage_same_candidate_other_source"
    )
    assert response.status_code == 200
    stage_detail = response.json()["thought"]["stage_detail"]
    assert stage_detail["extracted"]["status"] == "covered_by_shaped"
    assert stage_detail["extracted"]["used_by_shape_ids"] == ["shape:test-01"]

    data = json.loads(path.read_text())
    shaped = data["source_units"][0]["thoughts"][2]
    trace = shaped.pop("formation_trace")
    shaped["stage_detail"] = {"shaped": trace}
    path.write_text(json.dumps(data), encoding="utf-8")

    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_ready_1"
    )
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["formation_trace"]["llm_output_text"] == "Shaped output text for review."
    assert thought["stage_detail"]["shaped"]["llm_output_text"] == "Shaped output text for review."


def test_thought_detail_includes_formation_trace_with_sanitized_inputs(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_ready_1"
    )
    assert response.status_code == 200
    thought = response.json()["thought"]
    trace = thought["formation_trace"]

    assert trace["version"] == 1
    assert trace["stage"] == "shaped"
    assert trace["created_by"]["model"] == "gpt-5.5"
    assert trace["primary_lineage_ids"] == ["pan:test-01", "pan:test-02"]
    assert trace["primary_lineage_cards"][0]["title"] == "First extracted input"
    assert len(trace["primary_lineage_cards"][0]["quote"]) == 800
    assert trace["additional_context_used"][0]["kind"] == "source_title"
    assert trace["additional_context_used"][1]["kind"] == "neighboring_card"
    assert trace["additional_context_used"][1]["quote"] == "Neighboring source quote."
    assert trace["additional_context_used"][2]["kind"] == "source_metadata"
    assert trace["additional_context_used"][2]["values"]["section"] == "Formation trace fixture"
    assert trace["additional_context_used"][2]["values"]["api_key"] == "[REDACTED]"
    assert "[REDACTED_PATH]" in trace["additional_context_used"][2]["values"]["debug_path"]
    assert "Exact input package" in trace["llm_input_text"]
    assert "Shaped output text" in trace["llm_output_text"]
    assert trace["gate_events"][0]["stage"] == "policy"
    assert len(trace["llm_input_text"]) <= 20_000
    assert trace["llm_input_text"].endswith("...")
    assert thought["stage_detail"]["shaped"]["llm_output_text"] == trace["llm_output_text"]

    rendered = json.dumps(trace)
    assert "super-secret-value" not in rendered
    assert "super-secret-source-metadata-value" not in rendered
    assert "Do not expose hidden reasoning" not in rendered
    assert "Do not expose raw debug prompts" not in rendered
    assert "/mnt/d/private" not in rendered
    assert "Input Folder" not in rendered


def test_board_search_matches_formation_trace_without_source_row_broadening(client):
    response = client.get(
        "/api/plugins/openbrain_ingestion/board?source_type=transcripts&search=Shaped%20output%20text"
    )
    assert response.status_code == 200
    payload = response.json()

    cards = _all_cards(payload)
    assert [card["lineage_id"] for card in cards] == ["lineage_ready_1"]


def test_thought_detail_stopped_duplicate_strips_receipt(client):
    response = client.get("/api/plugins/openbrain_ingestion/source-units/transcript-a/thoughts/lineage_dup_1")
    assert response.status_code == 200
    thought = response.json()["thought"]
    assert thought["disposition"] == "stopped"
    assert "formation_trace" not in thought
    assert thought["current_stage"] == "deduped"
    assert thought["stopped_reason"] == "duplicate / merged"
    assert thought["matched_memory_id"] == "thought_existing_91c"
    assert thought["stop_code"] == "duplicate"
    assert thought["stop_target_id"] == "thought_existing_91c"
    assert thought["stop_target_label"] == "Start narrow"
    assert thought["stop_stage_id"] == "deduped"
    assert "cortexdb_receipt" not in thought or thought["cortexdb_receipt"] in ({}, None)
    assert next(stage for stage in thought["stages"] if stage["id"] == "policy")["status"] == "complete"
    later = [stage for stage in thought["stages"] if stage["id"] in {"ready_for_cortexdb", "cortexdb"}]
    assert all(stage["status"] == "not_reached" for stage in later)

    db_fields = {field["name"]: field for field in thought["database_fields"]}
    assert db_fields["id"]["populated"] is False
    assert db_fields["content"]["populated"] is False
    assert db_fields["source"]["populated"] is False
    assert db_fields["importance"]["populated"] is False
    assert db_fields["quality_score"]["populated"] is False
    assert db_fields["sensitivity_tier"]["populated"] is False
    assert db_fields["derivation_layer"]["populated"] is False


def test_database_fields_derive_imported_content_fingerprint_when_snapshot_omits_column(api_module):
    thought = {
        "lineage_id": "lineage-imported-no-fingerprint",
        "current_stage": "cortexdb",
        "disposition": "imported",
        "final_memory_text": "  Normalized\nContent  ",
        "cortexdb_receipt": {"id": "thought-no-fingerprint"},
    }
    source_unit = {"id": "source-a", "source_type": "transcripts", "label": "Source A"}

    fields = {field["name"]: field for field in api_module._database_fields(thought, source_unit)}
    expected = hashlib.sha256(
        re.sub(r"\s+", " ", "  Normalized\nContent  ").strip().lower().encode("utf-8")
    ).hexdigest()

    assert fields["content_fingerprint"]["value"] == expected
    assert "Derived with database normalization" in fields["content_fingerprint"]["note"]


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
    data["source_units"][0]["thoughts"][0]["stage_detail"] = {
        "policy": {
            "result": "passed",
            "candidate_text_reviewed": "password: hunter2 api_key=SECRET123",
            "redacted_text": "token=tok123",
        },
        "ready_for_cortexdb": {
            "final_memory_text": "Candidate ready for import.",
            "import_payload_preview": {
                "content": "safe content",
                "hidden_reasoning": "do not expose this reasoning",
                "raw_prompt": "do not expose this prompt",
            },
        },
        "cortexdb": {
            "id": "thought_demo_7f3a",
            "stored_text": "api_key=SECRET123",
            "hidden_reasoning": "do not expose receipt reasoning",
            "raw_prompt": "do not expose receipt prompt",
        },
    }
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
    assert thought["stage_detail"]["policy"]["candidate_text_reviewed"] == "password: [REDACTED] api_key=[REDACTED]"
    assert thought["stage_detail"]["policy"]["redacted_text"] == "token=[REDACTED]"
    assert thought["stage_detail"]["ready_for_cortexdb"]["import_payload_preview"] == {"content": "safe content"}
    assert thought["stage_detail"]["cortexdb"]["stored_text"] == "api_key=[REDACTED]"
    assert "hidden_reasoning" not in json.dumps(thought["stage_detail"])
    assert "raw_prompt" not in json.dumps(thought["stage_detail"])
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
