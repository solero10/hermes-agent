"""Demo fixture and recording-manifest helpers for OpenBrain workflow dashboards."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home

from hermes_cli.openbrain_workflow_artifacts import (
    append_event,
    atomic_write_json,
    manifest_path,
    write_dashboard_summary,
    write_status,
)
from hermes_cli.openbrain_workflow_contracts import (
    VideoManifest,
    VideoManifestEntry,
    WorkflowEvent,
    WorkflowStatus,
    utc_now_iso,
)


def build_demo_snapshot(*, workflow_run_id: str = "obwf_demo", source_unit_id: str = "demo-transcript") -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "schema_version": 1,
        "generated_at": now,
        "ingestion_run_id": workflow_run_id,
        "producer": {
            "kind": "openbrain_workflow_demo",
            "adapter": "demo_fixture",
            "source_type": "transcripts",
            "run_root_label": workflow_run_id,
            "artifacts": [],
        },
        "source_types": [{"id": "transcripts", "label": "Transcripts", "count": 1}],
        "source_units": [
            {
                "id": source_unit_id,
                "source_type": "transcripts",
                "label": "Workflow demo transcript",
                "subtitle": "Synthetic fixture for dashboard/video smoke tests",
                "processed_at": now,
                "thoughts": [
                    {
                        "id": "demo_lineage_1",
                        "lineage_id": "demo_lineage_1",
                        "candidate_id": "demo_candidate_1",
                        "title": "Demo enrichment candidate",
                        "summary": "Synthetic source-backed candidate used for workflow monitor demos.",
                        "current_stage": "enrich",
                        "disposition": "in_progress",
                        "needs_review": False,
                        "topics": ["workflow", "demo"],
                        "workflow_statuses": {
                            "extracted": {"status": "complete"},
                            "shaped": {"status": "complete"},
                            "enrich": {"status": "current", "note": "enriching demo candidate"},
                            "deduped": {"status": "not_reached"},
                            "ready_for_cortexdb": {"status": "not_reached"},
                            "cortexdb": {"status": "not_reached"},
                        },
                    },
                    {
                        "id": "demo_lineage_2",
                        "lineage_id": "demo_lineage_2",
                        "candidate_id": "demo_candidate_2",
                        "title": "Demo ready candidate",
                        "summary": "Synthetic candidate parked at review/import readiness.",
                        "current_stage": "ready_for_cortexdb",
                        "disposition": "needs_review",
                        "needs_review": True,
                        "topics": ["workflow", "review"],
                        "workflow_statuses": {
                            "extracted": {"status": "complete"},
                            "shaped": {"status": "complete"},
                            "enrich": {"status": "complete"},
                            "deduped": {"status": "complete"},
                            "ready_for_cortexdb": {"status": "needs_review"},
                            "cortexdb": {"status": "not_reached"},
                        },
                    },
                ],
            }
        ],
    }


def write_demo_fixture(*, workflow_run_id: str = "obwf_demo", source_unit_id: str = "demo-transcript") -> dict[str, str]:
    """Write a self-contained synthetic dashboard/workflow fixture.

    The fixture intentionally contains no raw transcript text, secrets, or private
    filesystem paths. It is safe for local screenshots and video smoke tests.
    """

    now = utc_now_iso()
    snapshot_dir = get_hermes_home() / "openbrain-ingestion-dashboard"
    snapshot_path = snapshot_dir / "snapshot.json"
    snapshot = build_demo_snapshot(workflow_run_id=workflow_run_id, source_unit_id=source_unit_id)
    atomic_write_json(snapshot_path, snapshot)

    status = WorkflowStatus(
        workflow_run_id=workflow_run_id,
        source_unit_id=source_unit_id,
        source_title="Workflow demo transcript",
        status="running",
        active_step="thought_enrichment",
        active_task_id="t_demo_workflow",
        started_at=now,
        updated_at=now,
        last_heartbeat_at=now,
        current_phase="enriching",
        current_candidate_id="demo_candidate_1",
        current_candidate_title_sanitized="Demo enrichment candidate",
        completed_candidates=1,
        total_candidates=2,
    )
    status_file = write_status(status)
    summary_file = write_dashboard_summary(status)
    events_file = append_event(
        WorkflowEvent(
            workflow_run_id=workflow_run_id,
            source_unit_id=source_unit_id,
            step_key="thought_enrichment",
            task_id="t_demo_workflow",
            event_type="candidate_completed",
            candidate_id="demo_candidate_1",
            created_at=now,
            completed_candidates=1,
            total_candidates=2,
            message="Demo candidate enriched.",
        )
    )
    return {
        "snapshot_json": str(snapshot_path),
        "status_json": str(status_file),
        "events_jsonl": str(events_file),
        "dashboard_summary": str(summary_file),
    }


def build_video_manifest_entry(
    *,
    path: str,
    duration_seconds: float,
    shows_real_surface: bool,
    annotations_visible: bool,
    secrets_reviewed: bool,
    filebrowser_url: str | None = None,
) -> VideoManifestEntry:
    entry = VideoManifestEntry(
        id=Path(path).stem or "recording",
        path=str(path),
        duration_seconds=duration_seconds,
        shows_real_surface=shows_real_surface,
        annotations_visible=annotations_visible,
        secrets_reviewed=secrets_reviewed,
        filebrowser_url=filebrowser_url,
    )
    if not entry.secrets_reviewed:
        raise ValueError("video recordings must be secrets-reviewed before being added to the manifest")
    return entry


def write_video_manifest(
    *,
    workflow_run_id: str,
    videos: Iterable[VideoManifestEntry | dict[str, Any]],
    output_path: str | Path | None = None,
) -> Path:
    entries: list[VideoManifestEntry] = []
    for video in videos:
        entry = video if isinstance(video, VideoManifestEntry) else VideoManifestEntry.model_validate(video)
        if not entry.secrets_reviewed:
            raise ValueError("video manifest contains an unreviewed recording")
        entries.append(entry)
    manifest = VideoManifest(workflow_run_id=workflow_run_id, created_at=utc_now_iso(), videos=entries)
    path = Path(output_path) if output_path else manifest_path(workflow_run_id)
    atomic_write_json(path, manifest.model_dump(mode="json", exclude_none=True))
    return path
