"""OpenBrain source-workflow DAG planning and Kanban card creation."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_artifacts import (
    append_event,
    dashboard_summary_path,
    events_path,
    status_path,
    validate_workflow_run_id,
    write_dashboard_summary,
    write_status,
)
from hermes_cli.openbrain_workflow_contracts import (
    BranchMode,
    StepStatus,
    WorkflowEvent,
    WorkflowStatus,
    redact_workflow_text,
    utc_now_iso,
)

WORKFLOW_TEMPLATE_ID = "openbrain-source-v1"
STEP_LABELS = {
    "panning_for_gold": "Panning for Gold",
    "meeting_synthesis": "Meeting Synthesis",
    "thought_enrichment": "Thought Enrichment",
    "dedupe": "Dedupe",
    "human_review": "Review / Approval Gate",
    "ready_cortexdb_import": "Ready / CortexDB Import",
}


@dataclass(frozen=True)
class WorkflowStepSpec:
    key: str
    label: str
    parents: tuple[str, ...] = ()
    required_skill: str | None = None
    produces_candidates: bool = False
    consumes_candidates: bool = False
    objective: str = "Update workflow status/events and write a receipt pointer."


@dataclass(frozen=True)
class CreatedWorkflowCards:
    workflow_run_id: str
    root_task_id: str
    step_task_ids: dict[str, str] = field(default_factory=dict)
    board: str | None = None
    mode: BranchMode = "panning-only"
    status_artifacts: dict[str, str] = field(default_factory=dict)


WorkflowMode = BranchMode


def build_source_workflow_steps(mode: WorkflowMode, *, approval_gate: bool = True) -> list[WorkflowStepSpec]:
    if mode not in ("panning-only", "meeting-only", "panning-and-meeting"):
        raise ValueError("mode must be panning-only, meeting-only, or panning-and-meeting")
    producer_keys: list[str] = []
    steps: list[WorkflowStepSpec] = []
    if mode in ("panning-only", "panning-and-meeting"):
        steps.append(WorkflowStepSpec(
            "panning_for_gold",
            STEP_LABELS["panning_for_gold"],
            required_skill="panning-for-gold-dashboard-fresh",
            produces_candidates=True,
            objective="Shape source material into OpenBrain candidate thoughts and write a receipt pointer.",
        ))
        producer_keys.append("panning_for_gold")
    if mode in ("meeting-only", "panning-and-meeting"):
        steps.append(WorkflowStepSpec(
            "meeting_synthesis",
            STEP_LABELS["meeting_synthesis"],
            required_skill="meeting-synthesis-dashboard-fresh",
            produces_candidates=True,
            objective="Run meeting synthesis for the source and write candidate/receipt outputs.",
        ))
        producer_keys.append("meeting_synthesis")
    steps.append(WorkflowStepSpec(
        "thought_enrichment",
        STEP_LABELS["thought_enrichment"],
        parents=tuple(producer_keys),
        required_skill="thought-enrichment-dashboard-fresh",
        consumes_candidates=True,
        objective="Enrich producer candidates and update the OpenBrain dashboard snapshot.",
    ))
    steps.append(WorkflowStepSpec(
        "dedupe",
        STEP_LABELS["dedupe"],
        parents=("thought_enrichment",),
        consumes_candidates=True,
        objective="Dedupe enriched candidates; block clearly if no dedupe entrypoint is configured.",
    ))
    import_parents: tuple[str, ...] = ("dedupe",)
    if approval_gate:
        steps.append(WorkflowStepSpec(
            "human_review",
            STEP_LABELS["human_review"],
            parents=("dedupe",),
            consumes_candidates=True,
            objective="Human approval gate before CortexDB import.",
        ))
        import_parents = ("dedupe", "human_review")
    steps.append(WorkflowStepSpec(
        "ready_cortexdb_import",
        STEP_LABELS["ready_cortexdb_import"],
        parents=import_parents,
        consumes_candidates=True,
        objective="Prepare/import approved candidates to CortexDB; block if approval or import capability is missing.",
    ))
    return steps


def _slug(value: str, *, max_length: int = 48) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    return (slug or "source")[:max_length].strip("-") or "source"


def generate_workflow_run_id(source_unit_id: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(source_unit_id)
    digest = hashlib.sha1(f"{source_unit_id}|{timestamp}".encode("utf-8")).hexdigest()[:8]
    return f"obwf_{timestamp}_{slug}_{digest}"


def format_step_body(
    spec: WorkflowStepSpec,
    *,
    workflow_run_id: str,
    source_unit_id: str,
    source_folder: str,
    producer_steps: tuple[str, ...] = (),
    candidate_ids: tuple[str, ...] = (),
    prior_receipt_path: str | None = None,
) -> str:
    safe_folder = redact_workflow_text(Path(source_folder).name or source_folder, max_length=200)
    source_folder_path = str(source_folder)
    status = status_path(workflow_run_id)
    events = events_path(workflow_run_id)
    return f"""# OpenBrain workflow step: {spec.label}

workflow_run_id: {workflow_run_id}
source_unit_id: {source_unit_id}
source_folder_display: {safe_folder}
source_folder_path: {source_folder_path}
step_key: {spec.key}
producer_steps: {','.join(producer_steps)}
input_candidate_ids: {','.join(candidate_ids)}
prior_receipt_path: {prior_receipt_path or ''}
status_artifact: {status}
workflow_events: {events}

## Step objective
{spec.objective}

## Completion requirements
- Update status/event artifacts.
- Update OpenBrain dashboard snapshot when this step changes candidates/thoughts.
- Verify dashboard API reflects the change.
- Complete with structured metadata that includes receipt path and dashboard verification result.
- Do not include raw transcript text or secrets in Kanban heartbeats.
"""


def create_openbrain_workflow_cards(
    *,
    workflow_run_id: str,
    source_unit_id: str,
    source_folder: str,
    mode: WorkflowMode,
    assignee: str,
    board: str | None = None,
    source_title: str | None = None,
    approval_gate: bool = True,
    tenant: str = "openbrain",
    conn=None,
) -> CreatedWorkflowCards:
    """Create root + step Kanban cards with explicit dependency links.

    The root card is completed immediately so producer steps become dispatchable,
    while still leaving an auditable DAG anchor in the board.
    """

    workflow_run_id = validate_workflow_run_id(workflow_run_id)
    close_conn = False
    if conn is None:
        kb.init_db(board=board)
        conn = kb.connect(board=board)
        close_conn = True
    try:
        root_body = (
            f"# OpenBrain workflow root\n\n"
            f"workflow_run_id: {workflow_run_id}\n"
            f"source_unit_id: {source_unit_id}\n"
            f"source_folder_display: {redact_workflow_text(Path(source_folder).name or source_folder, max_length=200)}\n"
            f"source_folder_path: {source_folder}\n"
            f"mode: {mode}\n"
            f"status_artifact: {status_path(workflow_run_id)}\n"
            f"workflow_events: {events_path(workflow_run_id)}\n"
        )
        root_task_id = kb.create_task(
            conn,
            title=f"OpenBrain workflow: {source_title or source_unit_id}",
            body=root_body,
            assignee=assignee,
            tenant=tenant,
            idempotency_key=f"openbrain:{workflow_run_id}:root",
            workflow_template_id=WORKFLOW_TEMPLATE_ID,
            current_step_key="root",
            board=board,
        )
        root_task = kb.get_task(conn, root_task_id)
        if root_task and root_task.status != "done":
            kb.complete_task(
                conn,
                root_task_id,
                summary="OpenBrain workflow DAG initialized.",
                metadata={"workflow_run_id": workflow_run_id, "source_unit_id": source_unit_id, "mode": mode},
            )

        step_task_ids: dict[str, str] = {}
        steps = build_source_workflow_steps(mode, approval_gate=approval_gate)
        for spec in steps:
            parent_keys = spec.parents or ("root",)
            parents = [root_task_id if key == "root" else step_task_ids[key] for key in parent_keys]
            initial_status = "blocked" if spec.key == "human_review" else "running"
            task_id = kb.create_task(
                conn,
                title=f"OpenBrain {spec.label}: {source_title or source_unit_id}",
                body=format_step_body(
                    spec,
                    workflow_run_id=workflow_run_id,
                    source_unit_id=source_unit_id,
                    source_folder=source_folder,
                    producer_steps=tuple(spec.parents),
                ),
                assignee=assignee,
                parents=parents,
                tenant=tenant,
                idempotency_key=f"openbrain:{workflow_run_id}:{spec.key}",
                workflow_template_id=WORKFLOW_TEMPLATE_ID,
                current_step_key=spec.key,
                skills=[spec.required_skill] if spec.required_skill else None,
                initial_status=initial_status,
                board=board,
            )
            if spec.key == "human_review":
                # ``initial_status='blocked'`` parks the card, but recompute_ready()
                # only treats an explicit ``blocked`` event as a sticky human gate.
                # Add that event at creation so the review gate cannot auto-promote
                # when dedupe completes; a human/unblock action must clear it.
                kb._append_event(  # type: ignore[attr-defined]
                    conn,
                    task_id,
                    "blocked",
                    {"reason": "review-required: approve before CortexDB import", "kind": "needs_input"},
                )
            step_task_ids[spec.key] = task_id

        now = utc_now_iso()
        root_ready_keys = [spec.key for spec in steps if not spec.parents]
        first_step_key = root_ready_keys[0] if root_ready_keys else next(iter(step_task_ids), None)
        first_task_id = step_task_ids.get(first_step_key) if first_step_key else None
        step_statuses = {
            key: StepStatus(
                task_id=task_id,
                status=(
                    "blocked" if key == "human_review"
                    else "running" if key == first_step_key
                    else "ready" if key in root_ready_keys
                    else "pending"
                ),
            )
            for key, task_id in step_task_ids.items()
        }
        workflow_status = WorkflowStatus(
            workflow_run_id=workflow_run_id,
            source_unit_id=source_unit_id,
            source_title=source_title,
            source_folder=source_folder,
            branch_mode=mode,
            active_step=first_step_key,
            active_task_id=first_task_id,
            status="running",
            started_at=now,
            updated_at=now,
            last_heartbeat_at=now,
            steps=step_statuses,
        )
        write_status(workflow_status)
        write_dashboard_summary(workflow_status)
        append_event(
            WorkflowEvent(
                workflow_run_id=workflow_run_id,
                source_unit_id=source_unit_id,
                event_type="workflow_started",
                created_at=now,
                task_id=root_task_id,
                message=f"OpenBrain workflow started in {mode} mode.",
            )
        )
        append_event(
            WorkflowEvent(
                workflow_run_id=workflow_run_id,
                source_unit_id=source_unit_id,
                event_type="task_created",
                created_at=now,
                step_key="root",
                task_id=root_task_id,
                message="Root workflow card created.",
            )
        )
        for step_key, task_id in step_task_ids.items():
            append_event(
                WorkflowEvent(
                    workflow_run_id=workflow_run_id,
                    source_unit_id=source_unit_id,
                    event_type="task_created",
                    created_at=now,
                    step_key=step_key,
                    task_id=task_id,
                    message=f"Step card created: {STEP_LABELS.get(step_key, step_key)}.",
                )
            )
        return CreatedWorkflowCards(
            workflow_run_id=workflow_run_id,
            root_task_id=root_task_id,
            step_task_ids=step_task_ids,
            board=board,
            mode=mode,
            status_artifacts={
                "status_json": str(status_path(workflow_run_id)),
                "events_jsonl": str(events_path(workflow_run_id)),
                "dashboard_summary": str(dashboard_summary_path(workflow_run_id)),
            },
        )
    finally:
        if close_conn:
            conn.close()
