#!/usr/bin/env python3
"""Clean retired OpenBrain dashboard recipe stages from a snapshot.

This is dashboard-snapshot cleanup only. It does not touch Supabase/CortexDB.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RETIRED_STAGE_KEYS = {
    "atomize",
    "atomized",
    "atomizer",
    "provenance",
    "provenance_chains",
    "provenance-chains",
    "entities_action",
    "schema_aware_routing",
    "schema-aware-routing",
    "entities/action",
    "entities",
    "entity_action",
}
RETIRED_STAGE_TO_VISIBLE_STAGE = {key: "enrich" for key in RETIRED_STAGE_KEYS}
ATOMIZER_GENERATION_IDS = {"atomizer", "atomize"}
SPLIT_ID_RE = re.compile(r"-split-\d+$")
WORKFLOW_MAP_KEYS = ("workflow_status", "workflow_statuses", "recipe_status", "recipe_statuses", "recipe_workflow")
DEFAULT_SOURCE_UNIT_ID = "transcripts-otter-565f988677"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    tmp.replace(path)


def normalized_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def stage_key(value: Any) -> str:
    return normalized_key(value).replace("-", "_")


def generation_id(thought: dict[str, Any]) -> str:
    technique = thought.get("generation_technique")
    if isinstance(technique, dict):
        return normalized_key(technique.get("id"))
    return ""


def atomization_metadata(thought: dict[str, Any]) -> dict[str, Any]:
    metadata = thought.get("metadata") if isinstance(thought.get("metadata"), dict) else {}
    atomization = metadata.get("atomization") or thought.get("atomization")
    return atomization if isinstance(atomization, dict) else {}


def candidate_id(thought: dict[str, Any]) -> str:
    return str(thought.get("candidate_id") or thought.get("memoryId") or thought.get("memory_id") or "")


def lineage_id(thought: dict[str, Any]) -> str:
    return str(thought.get("lineage_id") or thought.get("id") or "")


def is_atomizer_created(thought: dict[str, Any]) -> bool:
    cid = candidate_id(thought)
    lineage = lineage_id(thought)
    gen_id = generation_id(thought)
    atom = atomization_metadata(thought)
    if gen_id in ATOMIZER_GENERATION_IDS:
        return True
    if lineage.startswith("atom:"):
        return True
    if SPLIT_ID_RE.search(cid) and atom:
        return True
    return False


def normalize_retired_stage(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    key = normalized_key(value)
    dashed_key = key.replace("_", "-")
    slash_key = key.replace("_", "/")
    for candidate in (key, dashed_key, slash_key):
        if candidate in RETIRED_STAGE_TO_VISIBLE_STAGE:
            return RETIRED_STAGE_TO_VISIBLE_STAGE[candidate], True
    return value, False


def strip_retired_mapping(mapping: Any) -> tuple[Any, int]:
    if not isinstance(mapping, dict):
        return mapping, 0
    clean = deepcopy(mapping)
    removed = 0
    for key in list(clean):
        if str(key) in RETIRED_STAGE_KEYS:
            clean.pop(key, None)
            removed += 1
    return clean, removed


def clean_surviving_thought(thought: dict[str, Any]) -> tuple[dict[str, Any], int]:
    clean = deepcopy(thought)
    stripped = 0

    for key in ("current_stage", "stop_stage_id"):
        if key in clean:
            clean[key], changed = normalize_retired_stage(clean[key])
            stripped += int(changed)

    if isinstance(clean.get("stages"), list):
        stages = []
        for stage in clean["stages"]:
            if not isinstance(stage, dict):
                stages.append(stage)
                continue
            raw_stage_id = str(stage.get("id") or "")
            normalized_stage, changed = normalize_retired_stage(raw_stage_id)
            if changed:
                stripped += 1
                # Drop retired history stages; the canonical API now creates the
                # visible Enrich/Deduped/Ready/CortexDB timeline.
                continue
            stage_copy = deepcopy(stage)
            stage_copy["id"] = normalized_stage
            stages.append(stage_copy)
        clean["stages"] = stages

    if isinstance(clean.get("stage_detail"), dict):
        clean_stage_detail, count = strip_retired_mapping(clean["stage_detail"])
        stripped += count
        clean["stage_detail"] = clean_stage_detail
        if not clean["stage_detail"]:
            clean.pop("stage_detail", None)

    for key in WORKFLOW_MAP_KEYS:
        if isinstance(clean.get(key), dict):
            clean_value, count = strip_retired_mapping(clean[key])
            stripped += count
            clean[key] = clean_value
            if not clean[key]:
                clean.pop(key, None)

    if isinstance(clean.get("metadata"), dict):
        metadata = deepcopy(clean["metadata"])
        if "atomization" in metadata:
            metadata.pop("atomization", None)
            stripped += 1
        for key in WORKFLOW_MAP_KEYS:
            if isinstance(metadata.get(key), dict):
                clean_value, count = strip_retired_mapping(metadata[key])
                stripped += count
                metadata[key] = clean_value
                if not metadata[key]:
                    metadata.pop(key, None)
        clean["metadata"] = metadata
        if not metadata:
            clean.pop("metadata", None)

    return clean, stripped


def row_summary(source_unit: dict[str, Any], thought: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_unit_id": source_unit.get("id"),
        "id": thought.get("id"),
        "lineage_id": lineage_id(thought),
        "candidate_id": candidate_id(thought),
        "title": thought.get("title") or thought.get("summary"),
        "current_stage": thought.get("current_stage"),
        "generation_technique": thought.get("generation_technique"),
        "atomization": atomization_metadata(thought),
    }


def count_thoughts(snapshot: dict[str, Any]) -> int:
    return sum(len(unit.get("thoughts", [])) for unit in snapshot.get("source_units", []))


def target_source_count(snapshot: dict[str, Any], source_unit_id: str) -> int | None:
    for unit in snapshot.get("source_units", []):
        if unit.get("id") == source_unit_id:
            return len(unit.get("thoughts", []))
    return None


def verify_no_retired_recipe_metadata(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for unit in snapshot.get("source_units", []):
        source_id = str(unit.get("id") or "")
        for thought in unit.get("thoughts", []):
            cid = candidate_id(thought) or lineage_id(thought)
            for key in ("current_stage", "stop_stage_id"):
                if str(thought.get(key) or "") in RETIRED_STAGE_KEYS:
                    findings.append({"source_unit_id": source_id, "candidate_id": cid, "field": key})
            for index, stage in enumerate(thought.get("stages") or []):
                if isinstance(stage, dict) and str(stage.get("id") or "") in RETIRED_STAGE_KEYS:
                    findings.append({"source_unit_id": source_id, "candidate_id": cid, "field": f"stages[{index}].id"})
            for key in WORKFLOW_MAP_KEYS:
                value = thought.get(key)
                if isinstance(value, dict):
                    for retired in RETIRED_STAGE_KEYS.intersection(value):
                        findings.append({"source_unit_id": source_id, "candidate_id": cid, "field": f"{key}.{retired}"})
            stage_detail = thought.get("stage_detail")
            if isinstance(stage_detail, dict):
                for retired in RETIRED_STAGE_KEYS.intersection(stage_detail):
                    findings.append({"source_unit_id": source_id, "candidate_id": cid, "field": f"stage_detail.{retired}"})
            metadata = thought.get("metadata")
            if isinstance(metadata, dict):
                for key in WORKFLOW_MAP_KEYS:
                    value = metadata.get(key)
                    if isinstance(value, dict):
                        for retired in RETIRED_STAGE_KEYS.intersection(value):
                            findings.append({"source_unit_id": source_id, "candidate_id": cid, "field": f"metadata.{key}.{retired}"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path.home() / ".hermes/openbrain-ingestion-dashboard/snapshot.json",
    )
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--expected-removed-thoughts", type=int, default=0)
    parser.add_argument("--expected-total-thoughts", type=int, default=None)
    parser.add_argument("--expected-target-source-thoughts", type=int, default=None)
    parser.add_argument("--target-source-unit-id", default=DEFAULT_SOURCE_UNIT_ID)
    parser.add_argument("--remove-atomizer-children", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshot_path = args.snapshot.expanduser()
    receipt_dir = args.receipt_dir.expanduser()
    artifact_dir = receipt_dir / "34_remove_provenance_entities_cleanup_artifacts"
    stamp = utc_stamp()

    data = load_json(snapshot_path)
    before_sources = len(data.get("source_units", []))
    before_thoughts = count_thoughts(data)
    before_target = target_source_count(data, args.target_source_unit_id)
    removed: list[dict[str, Any]] = []
    stripped_metadata_count = 0

    updated = deepcopy(data)
    for source_unit in updated.get("source_units", []):
        kept = []
        for thought in source_unit.get("thoughts", []):
            if args.remove_atomizer_children and is_atomizer_created(thought):
                removed.append(row_summary(source_unit, thought))
                continue
            clean_thought, stripped = clean_surviving_thought(thought)
            stripped_metadata_count += stripped
            kept.append(clean_thought)
        source_unit["thoughts"] = kept

    if len(removed) != args.expected_removed_thoughts:
        raise SystemExit(
            f"Expected to remove {args.expected_removed_thoughts} retired recipe rows, found {len(removed)}"
        )

    after_thoughts = count_thoughts(updated)
    after_target = target_source_count(updated, args.target_source_unit_id)
    if args.expected_total_thoughts is not None and after_thoughts != args.expected_total_thoughts:
        raise SystemExit(f"Expected {args.expected_total_thoughts} total thoughts after cleanup, found {after_thoughts}")
    if args.expected_target_source_thoughts is not None and after_target != args.expected_target_source_thoughts:
        raise SystemExit(
            f"Expected {args.expected_target_source_thoughts} thoughts in {args.target_source_unit_id}, found {after_target}"
        )

    remaining_retired = verify_no_retired_recipe_metadata(updated)
    if remaining_retired:
        raise SystemExit(f"Retired recipe metadata remains after cleanup: {remaining_retired[:10]}")

    updated["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated["ingestion_run_id"] = f"remove-retired-recipes-{stamp}"

    result = {
        "timestamp": stamp,
        "snapshot": str(snapshot_path),
        "dry_run": args.dry_run,
        "before_sources": before_sources,
        "before_thoughts": before_thoughts,
        "before_target_source_thoughts": before_target,
        "removed_count": len(removed),
        "stripped_metadata_count": stripped_metadata_count,
        "after_thoughts": after_thoughts,
        "after_target_source_thoughts": after_target,
        "remaining_retired_metadata_count": len(remaining_retired),
        "removed_candidate_ids": [row["candidate_id"] for row in removed],
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "retired_stage_cleanup_rows.json").write_text(
        json.dumps(removed, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "snapshot_cleanup_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    receipt = receipt_dir / "34_remove_provenance_entities_cleanup_receipt.md"
    receipt.write_text(
        "# Remove Provenance and Entities/action Cleanup Receipt\n\n"
        f"- Timestamp: `{stamp}`\n"
        f"- Snapshot: `{snapshot_path}`\n"
        f"- Dry run: `{args.dry_run}`\n"
        f"- Before thoughts: {before_thoughts}\n"
        f"- Removed dashboard thoughts: {len(removed)}\n"
        f"- Retired workflow metadata stripped: {stripped_metadata_count}\n"
        f"- After thoughts: {after_thoughts}\n"
        f"- Target source thoughts after cleanup: {after_target}\n"
        "- Scope: dashboard snapshot only; no Supabase/CortexDB deletion performed by this script.\n",
        encoding="utf-8",
    )

    if args.dry_run:
        print(json.dumps(result, indent=2))
        return 0

    backup = snapshot_path.with_name(
        f"{snapshot_path.stem}.before-remove-retired-recipes-{stamp}{snapshot_path.suffix}"
    )
    shutil.copy2(snapshot_path, backup)
    result["backup"] = str(backup)
    (artifact_dir / "snapshot_cleanup_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    atomic_write_json(snapshot_path, updated)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
