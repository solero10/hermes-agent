#!/usr/bin/env python3
"""Remove Atomize/Atomizer rows and metadata from an OpenBrain dashboard snapshot."""
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

REMOVED_STAGE_ALIASES = {"atomize", "atomized", "atomizer"}
ATOMIZER_GENERATION_IDS = {"atomizer", "atomize"}
SPLIT_ID_RE = re.compile(r"-split-\d+$")


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


def generation_id(thought: dict[str, Any]) -> str:
    technique = thought.get("generation_technique")
    if isinstance(technique, dict):
        return str(technique.get("id") or "").strip().lower()
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


def strip_atomize_mapping(mapping: Any) -> Any:
    if not isinstance(mapping, dict):
        return mapping
    clean = deepcopy(mapping)
    for key in ("atomize", "atomized", "atomizer"):
        clean.pop(key, None)
    return clean


def normalize_removed_stage(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in REMOVED_STAGE_ALIASES:
        return "provenance"
    return value


def clean_surviving_thought(thought: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(thought)
    for key in ("current_stage", "stop_stage_id"):
        if key in clean:
            clean[key] = normalize_removed_stage(clean[key])

    if isinstance(clean.get("stages"), list):
        stages = []
        for stage in clean["stages"]:
            if not isinstance(stage, dict):
                stages.append(stage)
                continue
            raw_stage_id = str(stage.get("id") or "").strip().lower().replace("-", "_").replace(" ", "_")
            if raw_stage_id in REMOVED_STAGE_ALIASES:
                continue
            stages.append(deepcopy(stage))
        clean["stages"] = stages

    if isinstance(clean.get("stage_detail"), dict):
        clean["stage_detail"] = strip_atomize_mapping(clean["stage_detail"])
        if not clean["stage_detail"]:
            clean.pop("stage_detail", None)

    for key in ("workflow_status", "workflow_statuses", "recipe_status", "recipe_statuses", "recipe_workflow"):
        if isinstance(clean.get(key), dict):
            clean[key] = strip_atomize_mapping(clean[key])
            if not clean[key]:
                clean.pop(key, None)

    if isinstance(clean.get("metadata"), dict):
        metadata = deepcopy(clean["metadata"])
        metadata.pop("atomization", None)
        for key in ("workflow_status", "workflow_statuses", "recipe_status", "recipe_statuses", "recipe_workflow"):
            if isinstance(metadata.get(key), dict):
                metadata[key] = strip_atomize_mapping(metadata[key])
                if not metadata[key]:
                    metadata.pop(key, None)
        clean["metadata"] = metadata
        if not metadata:
            clean.pop("metadata", None)

    return clean


def update_source_subtitle(source_unit: dict[str, Any]) -> None:
    if source_unit.get("id") == "transcripts-otter-565f988677":
        source_unit["subtitle"] = (
            "31 Evidence cards; 26 enriched parent candidates; "
            "30 split child candidates removed per Ken request; 57 dashboard thoughts remain."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path.home() / ".hermes/openbrain-ingestion-dashboard/snapshot.json",
    )
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--expected-remove", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshot_path = args.snapshot.expanduser()
    receipt_dir = args.receipt_dir.expanduser()
    artifact_dir = receipt_dir / "33_remove_atomize_cleanup_artifacts"
    stamp = utc_stamp()

    data = load_json(snapshot_path)
    before_sources = len(data.get("source_units", []))
    before_thoughts = sum(len(unit.get("thoughts", [])) for unit in data.get("source_units", []))
    removed: list[dict[str, Any]] = []

    updated = deepcopy(data)
    for source_unit in updated.get("source_units", []):
        kept = []
        for thought in source_unit.get("thoughts", []):
            if is_atomizer_created(thought):
                removed.append(
                    {
                        "source_unit_id": source_unit.get("id"),
                        "id": thought.get("id"),
                        "lineage_id": lineage_id(thought),
                        "candidate_id": candidate_id(thought),
                        "title": thought.get("title") or thought.get("summary"),
                        "current_stage": thought.get("current_stage"),
                        "generation_technique": thought.get("generation_technique"),
                        "atomization": atomization_metadata(thought),
                    }
                )
                continue
            kept.append(clean_surviving_thought(thought))
        source_unit["thoughts"] = kept
        update_source_subtitle(source_unit)

    if len(removed) != args.expected_remove:
        raise SystemExit(f"Expected to remove {args.expected_remove} Atomizer rows, found {len(removed)}")

    updated["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated["ingestion_run_id"] = f"remove-split-children-{stamp}"

    after_thoughts = sum(len(unit.get("thoughts", [])) for unit in updated.get("source_units", []))
    result = {
        "timestamp": stamp,
        "snapshot": str(snapshot_path),
        "dry_run": args.dry_run,
        "before_sources": before_sources,
        "before_thoughts": before_thoughts,
        "removed_count": len(removed),
        "after_thoughts": after_thoughts,
        "removed_candidate_ids": [row["candidate_id"] for row in removed],
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "removed_atomizer_rows.json").write_text(
        json.dumps(removed, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "snapshot_cleanup_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    receipt = receipt_dir / "33_remove_atomize_cleanup_receipt.md"
    receipt.write_text(
        "# Remove Atomize Cleanup Receipt\n\n"
        f"- Timestamp: `{stamp}`\n"
        f"- Snapshot: `{snapshot_path}`\n"
        f"- Dry run: `{args.dry_run}`\n"
        f"- Before thoughts: {before_thoughts}\n"
        f"- Removed split child dashboard thoughts: {len(removed)}\n"
        f"- After thoughts: {after_thoughts}\n"
        "- Scope: dashboard snapshot only; no Supabase/CortexDB deletion performed by this script.\n",
        encoding="utf-8",
    )

    if args.dry_run:
        print(json.dumps(result, indent=2))
        return 0

    backup = snapshot_path.with_name(f"{snapshot_path.stem}.before-remove-atomize-{stamp}{snapshot_path.suffix}")
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
