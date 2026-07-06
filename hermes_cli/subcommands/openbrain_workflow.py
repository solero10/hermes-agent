"""CLI entrypoint for OpenBrain workflow orchestration."""
from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone

from hermes_cli.openbrain_workflow_dag import create_openbrain_workflow_cards, generate_workflow_run_id


def _cmd_openbrain_workflow(args: Namespace) -> int:
    action = getattr(args, "openbrain_workflow_action", None)
    if action != "start":
        print("openbrain-workflow requires an action. Try: openbrain-workflow start --help")
        return 1
    run_id = args.workflow_run_id or generate_workflow_run_id(args.source_unit_id, now=datetime.now(timezone.utc))
    created = create_openbrain_workflow_cards(
        workflow_run_id=run_id,
        source_unit_id=args.source_unit_id,
        source_folder=args.source_folder,
        source_title=args.source_title,
        mode=args.mode,
        assignee=args.assignee,
        board=args.board,
        approval_gate=not args.no_approval_gate,
    )
    payload = {
        "workflow_run_id": created.workflow_run_id,
        "root_task_id": created.root_task_id,
        "step_task_ids": created.step_task_ids,
        "board": created.board,
        "mode": created.mode,
        "status_artifacts": created.status_artifacts,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Started OpenBrain workflow {created.workflow_run_id}")
        print(f"root: {created.root_task_id}")
        for step, task_id in created.step_task_ids.items():
            print(f"{step}: {task_id}")
    return 0


def build_openbrain_workflow_parser(subparsers) -> ArgumentParser:
    parser = subparsers.add_parser(
        "openbrain-workflow",
        help="Start and inspect OpenBrain source-processing Kanban workflows",
    )
    sub = parser.add_subparsers(dest="openbrain_workflow_action")
    start = sub.add_parser("start", help="Create an OpenBrain workflow DAG on the Kanban board")
    start.add_argument("--source-folder", required=True, help="Source folder or package path")
    start.add_argument("--source-unit-id", required=True, help="Stable source unit id")
    start.add_argument("--source-title", default=None, help="Safe dashboard/source title")
    start.add_argument(
        "--mode",
        choices=["panning-only", "meeting-only", "panning-and-meeting"],
        default="panning-and-meeting",
    )
    start.add_argument("--assignee", default="default", help="Hermes profile that should execute step cards")
    start.add_argument("--board", default=None, help="Kanban board slug")
    start.add_argument("--workflow-run-id", default=None, help="Deterministic id to reuse")
    start.add_argument("--no-approval-gate", action="store_true", help="Skip the human review gate")
    start.add_argument("--json", action="store_true", help="Emit JSON")
    parser.set_defaults(func=_cmd_openbrain_workflow)
    start.set_defaults(func=_cmd_openbrain_workflow)
    return parser
