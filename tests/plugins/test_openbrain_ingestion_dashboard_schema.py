"""Schema and privacy tests for OpenBrain ingestion dashboard snapshots."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_plugin_api():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "openbrain_ingestion" / "dashboard" / "plugin_api.py"
    assert plugin_file.exists(), f"plugin file missing: {plugin_file}"
    spec = importlib.util.spec_from_file_location("openbrain_ingestion_schema_test", plugin_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _base_snapshot(api):
    return {
        "schema_version": 1,
        "generated_at": "2026-06-21T04:39:54Z",
        "ingestion_run_id": "run_schema_test",
        "producer": {
            "kind": "panning_for_gold",
            "adapter": "otter_package",
            "source_type": "transcripts",
            "run_root_label": "safe-run-root",
            "artifacts": ["source-items.jsonl"],
        },
        "source_types": [{"id": "transcripts", "label": "Transcripts", "count": 1}],
        "source_units": [
            {
                "id": "transcript-a",
                "source_type": "transcripts",
                "label": "A transcript",
                "source_ref": {
                    "kind": "otter_transcript",
                    "source_unit_id": "transcript-a",
                    "display_path": "Otter/2023-11-30.md",
                },
                "thoughts": [
                    {
                        "lineage_id": "lineage-a",
                        "candidate_id": "candidate-a",
                        "title": "Alias test",
                        "summary": "stage aliases normalize",
                        "current_stage": "raw_extraction",
                        "disposition": "in_progress",
                        "stages": [
                            {"id": "raw_extraction", "status": "complete", "label": "Raw extraction"},
                            {"id": "ready", "status": "pending", "label": "Ready"},
                        ],
                    }
                ],
            }
        ],
    }


def test_canonical_stage_aliases_are_normalized_in_snapshot_output():
    api = _load_plugin_api()
    assert api._normalize_stage("raw_extraction") == "extracted"
    assert api._normalize_stage("policy") == "shaped"
    assert api._normalize_stage("thought_enrichment") == "enrich"
    assert api._normalize_stage("atomized") == "enrich"
    assert api._normalize_stage("provenance_chains") == "enrich"
    assert api._normalize_stage("entities/action") == "enrich"
    assert api._normalize_stage("ready") == "ready_for_cortexdb"
    assert api._normalize_stage("ready_to_import") == "ready_for_cortexdb"

    snapshot = api.Snapshot.model_validate(_base_snapshot(api)).model_dump(mode="json", exclude_none=True)
    thought = snapshot["source_units"][0]["thoughts"][0]
    assert thought["current_stage"] == "extracted"
    stage_ids = [stage["id"] for stage in thought["stages"]]
    assert "raw_extraction" not in stage_ids
    assert "atomize" not in stage_ids
    assert "provenance" not in stage_ids
    assert "entities_action" not in stage_ids
    assert "ready" not in stage_ids
    assert "extracted" in stage_ids
    ready_stage = next(stage for stage in thought["stages"] if stage["id"] == "ready_for_cortexdb")
    assert ready_stage["label"] == "Ready for CortexDB"


def test_snapshot_model_rejects_unknown_stage():
    api = _load_plugin_api()
    bad = _base_snapshot(api)
    bad["source_units"][0]["thoughts"][0]["current_stage"] = "not_a_stage"
    with pytest.raises(Exception):
        api.Snapshot.model_validate(bad)


def test_snapshot_model_rejects_bad_schema_version_disposition_and_stage_status():
    api = _load_plugin_api()

    bad_version = _base_snapshot(api)
    bad_version["schema_version"] = 2
    with pytest.raises(Exception):
        api.Snapshot.model_validate(bad_version)

    bad_disposition = _base_snapshot(api)
    bad_disposition["source_units"][0]["thoughts"][0]["disposition"] = "maybe"
    with pytest.raises(Exception):
        api.Snapshot.model_validate(bad_disposition)

    bad_stage_status = _base_snapshot(api)
    bad_stage_status["source_units"][0]["thoughts"][0]["stages"] = [
        {"id": "extracted", "status": "wat"}
    ]
    with pytest.raises(Exception):
        api.Snapshot.model_validate(bad_stage_status)


def test_producer_info_is_bounded_forbids_extras_and_artifacts_are_basenames():
    api = _load_plugin_api()
    producer = api.ProducerInfo.model_validate(
        {
            "kind": "panning_for_gold",
            "adapter": "otter_package",
            "source_type": "transcripts",
            "run_root_label": "/mnt/d/private/panning-run-20260621",
            "artifacts": [
                "/mnt/d/private/source-items.jsonl",
                "C:\\Users\\Ken\\Private\\capture-candidates.jsonl",
                "https://localhost/private/skip.jsonl",
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    assert producer["run_root_label"] == "panning-run-20260621"
    assert producer["artifacts"] == ["source-items.jsonl", "capture-candidates.jsonl"]

    with pytest.raises(Exception):
        api.ProducerInfo.model_validate({"kind": "x", "adapter": "y", "secret": "nope"})


def test_stopped_or_not_imported_thought_receipt_is_stripped_even_at_cortexdb_stage():
    api = _load_plugin_api()
    snapshot = _base_snapshot(api)
    thought = snapshot["source_units"][0]["thoughts"][0]
    thought.update(
        {
            "lineage_id": "lineage-not-imported",
            "current_stage": "cortexdb",
            "disposition": "stopped",
            "stopped_reason": "policy stop",
            "cortexdb_receipt": {"id": "must-not-leak", "type": "observation"},
            "cortexdb_id": "also-must-not-leak",
        }
    )

    clean = api.Snapshot.model_validate(snapshot).model_dump(mode="json", exclude_none=True)
    clean_thought = clean["source_units"][0]["thoughts"][0]
    assert "cortexdb_receipt" not in clean_thought
    assert "cortexdb_id" not in clean_thought

    card = api._card(clean_thought, "transcript-a")
    assert card.get("cortexdb_id") is None


def test_sensitive_values_are_redacted_and_only_source_text_fields_are_bounded():
    api = _load_plugin_api()
    long_snippet = "safe words " * 80
    long_final_memory = "durable final memory " * 80
    clean = api._sanitize_snapshot(
        {
            "source_units": [
                {
                    "id": "source_unit_with_long_identifier_" + "x" * 120,
                    "label": "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
                    "thoughts": [
                        {
                            "lineage_id": "lineage-long-" + "y" * 120,
                            "title": "OpenAI key sk-pro...3456",
                            "api_token": "super-secret-value",
                            "authorization": "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
                            "source_snippet": long_snippet,
                            "raw_text": long_snippet,
                            "quote": long_snippet,
                            "final_memory_text": long_final_memory,
                        }
                    ],
                }
            ]
        }
    )
    rendered = str(clean)
    assert "Bearer" not in rendered
    assert "sk-pro...3456" not in rendered
    assert "super-secret-value" not in rendered
    assert "[REDACTED" in rendered

    thought = clean["source_units"][0]["thoughts"][0]
    assert len(thought["source_snippet"]) <= 500
    assert len(thought["raw_text"]) <= 500
    assert len(thought["quote"]) <= 500
    assert thought["final_memory_text"] == long_final_memory
    assert clean["source_units"][0]["id"].startswith("source_unit_with_long_identifier_")
    assert thought["lineage_id"].startswith("lineage-long-")


def test_embedded_private_paths_with_spaces_are_fully_redacted_in_text_fields():
    api = _load_plugin_api()
    clean = api._sanitize_snapshot(
        {
            "source_units": [
                {
                    "id": "source-unit-path-redaction",
                    "label": "see /mnt/d/private/My Folder/file.md for context",
                    "subtitle": "also see C:\\Users\\Ken\\Private Folder\\file.md before review",
                    "thoughts": [
                        {
                            "lineage_id": "lineage-path-redaction",
                            "title": "Title with /mnt/d/private/My Folder/file.md embedded",
                            "summary": "Windows path C:\\Users\\Ken\\Private Folder\\file.md embedded",
                            "final_memory_text": "ordinary note remains after /mnt/d/private/My Folder/file.md",
                        }
                    ],
                }
            ]
        }
    )

    rendered = json.dumps(clean)
    assert "/mnt/d/private" not in rendered
    assert "My Folder/file.md" not in rendered
    assert "C:\\\\Users\\\\Ken" not in rendered
    assert "Private Folder" not in rendered
    assert "[REDACTED_PATH]" in rendered
    assert "ordinary note remains after" in clean["source_units"][0]["thoughts"][0]["final_memory_text"]


@pytest.mark.parametrize(
    ("raw", "marker", "leaked_fragments"),
    [
        (
            "see /mnt/d/private/My Folder for context",
            "[REDACTED_PATH]",
            ("/mnt/d/private", "My Folder", "Folder for context"),
        ),
        (
            "see C:\\Users\\Ken\\Private Folder for context",
            "[REDACTED_PATH]",
            ("C:\\Users\\Ken", "Private Folder", "Folder for context"),
        ),
        (
            "see \\\\server\\share\\Private Folder for context",
            "[REDACTED_PATH]",
            ("\\\\server\\share", "Private Folder", "Folder for context"),
        ),
        (
            "file:///mnt/d/private/My Folder/file.md",
            "[REDACTED_URL]",
            ("file:///mnt/d/private", "My Folder/file.md", "Folder/file.md"),
        ),
        (
            "http://localhost:9123/My Folder/file.md",
            "[REDACTED_URL]",
            ("localhost:9123", "My Folder/file.md", "Folder/file.md"),
        ),
    ],
)
def test_private_path_and_url_phrases_with_unescaped_spaces_do_not_leak_tails(
    raw, marker, leaked_fragments
):
    api = _load_plugin_api()

    redacted = api._redact_sensitive_text(raw)

    assert marker in redacted
    for leaked in leaked_fragments:
        assert leaked not in redacted


def test_generic_secret_assignments_are_redacted_inside_safe_text_fields():
    api = _load_plugin_api()
    secret_text = (
        "api_key=SECRET123 api-key: HEADERSECRET password: hunter2 secret=s3cr3t token=tok123 "
        "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ== "
        "Authorization: Bearer abcdefghijklmnop "
        "https://example.test/callback?access_token=urlsecret&token=urltok&api_key=urlkey&ok=1"
    )
    clean = api._sanitize_snapshot(
        {
            "source_units": [
                {
                    "id": "source-unit-secret-redaction",
                    "label": secret_text,
                    "thoughts": [
                        {
                            "lineage_id": "lineage-secret-redaction",
                            "title": "ordinary title cand_42_18_a remains",
                            "summary": secret_text,
                            "final_memory_text": "ordinary final memory with password: hunter2 and ID cand_42_18_a",
                        }
                    ],
                }
            ]
        }
    )

    rendered = json.dumps(clean)
    for leaked in (
        "SECRET123",
        "HEADERSECRET",
        "hunter2",
        "s3cr3t",
        "tok123",
        "QWxhZGRpbjpvcGVuIHNlc2FtZQ",
        "abcdefghijklmnop",
        "urlsecret",
        "urltok",
        "urlkey",
    ):
        assert leaked not in rendered
    assert "api_key=[REDACTED]" in rendered
    assert 'api-key: [REDACTED]' in rendered
    assert "password: [REDACTED]" in rendered
    assert "secret=[REDACTED]" in rendered
    assert "token=[REDACTED]" in rendered
    assert "access_token=[REDACTED]" in rendered
    assert "ok=1" in rendered
    thought = clean["source_units"][0]["thoughts"][0]
    assert thought["title"] == "ordinary title cand_42_18_a remains"
    assert thought["lineage_id"] == "lineage-secret-redaction"
    assert thought["final_memory_text"] == "ordinary final memory with password: [REDACTED] and ID cand_42_18_a"


def test_source_refs_omit_absolute_paths_private_urls_and_raw_url_fields():
    api = _load_plugin_api()
    raw = {
        "source_units": [
            {
                "id": "transcript-a",
                "source_type": "transcripts",
                "label": "Transcript A",
                "source_path": "/mnt/d/private/Otter/source.md",
                "raw_url": "http://localhost:9123/private",
                "source_ref": {
                    "kind": "otter_transcript",
                    "source_unit_id": "transcript-a",
                    "display_path": "/mnt/d/private/transcript.md",
                    "absolute_path": "C:\\Users\\Ken\\secret.md",
                    "url": "http://localhost:9123/private",
                    "link": "file:///mnt/d/private/raw.txt",
                },
            }
        ]
    }
    clean = api._sanitize_snapshot(raw)
    payload = str(clean)
    assert "/mnt/d/private" not in payload
    assert "C:\\Users" not in payload
    assert "localhost" not in payload
    assert "file://" not in payload
    assert "raw_url" not in payload
    assert "absolute_path" not in payload
    assert "url" not in clean["source_units"][0]["source_ref"]
    assert set(clean["source_units"][0]["source_ref"]) <= {"kind", "source_unit_id", "display_path"}
