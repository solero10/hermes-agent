"""CLI entrypoint for OpenBrain workflow orchestration."""
from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_contracts import ExecuteMode
from hermes_cli.openbrain_workflow_dag import create_openbrain_workflow_cards, generate_workflow_run_id
from hermes_cli.openbrain_workflow_supervisor import (
    SyntheticDryRunChildRunner,
    execute_mode_for_task,
    run_openbrain_step_task,
)


def _json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _run_dry_execute_loop(*, board: str | None, max_ticks: int = 50) -> dict:
    """Drain synthetic workflow work until no dry-run-executable card remains."""

    tick_summaries: list[dict] = []
    with kb.connect(board=board) as conn:
        for tick in range(max_ticks):
            result = kb.dispatch_once(
                conn,
                max_spawn=1,
                board=board,
                openbrain_runner_factory=lambda task, execute_mode: SyntheticDryRunChildRunner(),
            )
            tick_summaries.append({
                "tick": tick + 1,
                "spawned": result.spawned,
                "guarded": result.respawn_guarded,
                "auto_blocked": result.auto_blocked,
            })
            if not result.spawned:
                break
    return {"ticks": tick_summaries}


def _cmd_start(args: Namespace) -> int:
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
        execute_mode=args.execute_mode,
    )
    payload = {
        "workflow_run_id": created.workflow_run_id,
        "root_task_id": created.root_task_id,
        "step_task_ids": created.step_task_ids,
        "board": created.board,
        "mode": created.mode,
        "execute_mode": args.execute_mode,
        "status_artifacts": created.status_artifacts,
    }
    if args.execute_mode == "dry-run-execute":
        payload["dry_run_execute"] = _run_dry_execute_loop(board=args.board)
    if args.json:
        _json(payload)
    else:
        print(f"Started OpenBrain workflow {created.workflow_run_id}")
        print(f"root: {created.root_task_id}")
        for step, task_id in created.step_task_ids.items():
            print(f"{step}: {task_id}")
        if args.execute_mode == "dry-run-execute":
            print("dry-run execution drained synthetic runnable steps")
    return 0


def _cmd_run_step(args: Namespace) -> int:
    kb.init_db(board=args.board)
    with kb.connect(board=args.board) as conn:
        task = kb.get_task(conn, args.task_id)
        if task is None:
            print(f"task not found: {args.task_id}")
            return 1
        mode: ExecuteMode = args.execute_mode or execute_mode_for_task(task)
        runner_factory = None
        if mode == "dry-run-execute":
            runner_factory = lambda task, execute_mode: SyntheticDryRunChildRunner()
        result = run_openbrain_step_task(
            conn,
            task,
            board=args.board,
            runner_factory=runner_factory,
            execute_mode=mode,
        )
    payload = {
        "task_id": args.task_id,
        "status": result.status,
        "summary": result.summary,
        "block_reason": result.block_reason,
        "metadata": result.metadata,
    }
    if args.json:
        _json(payload)
    else:
        print(result.summary)
    return 0 if result.status == "done" else 2


def _cmd_openbrain_workflow(args: Namespace) -> int:
    action = getattr(args, "openbrain_workflow_action", None)
    if action == "start":
        return _cmd_start(args)
    if action == "run-step":
        return _cmd_run_step(args)
    print("openbrain-workflow requires an action. Try: openbrain-workflow start --help")
    return 1


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
    start.add_argument(
        "--execute-mode",
        choices=["plan-only", "dry-run-execute", "production-execute", "production-import"],
        default="plan-only",
        help="Execution policy. plan-only creates the DAG; dry-run-execute uses synthetic child runners; production modes are explicit.",
    )
    start.add_argument("--assignee", default="default", help="Hermes profile that should execute step cards")
    start.add_argument("--board", default=None, help="Kanban board slug")
    start.add_argument("--workflow-run-id", default=None, help="Deterministic id to reuse")
    start.add_argument("--no-approval-gate", action="store_true", help="Skip the human review gate")
    start.add_argument("--json", action="store_true", help="Emit JSON")
    start.set_defaults(func=_cmd_openbrain_workflow)

    run_step = sub.add_parser("run-step", help="Run one claimed OpenBrain workflow step through the supervisor")
    run_step.add_argument("--task-id", required=True)
    run_step.add_argument("--board", default=None)
    run_step.add_argument(
        "--execute-mode",
        choices=["plan-only", "dry-run-execute", "production-execute", "production-import"],
        default=None,
    )
    run_step.add_argument("--json", action="store_true")
    run_step.set_defaults(func=_cmd_openbrain_workflow)

    parser.set_defaults(func=_cmd_openbrain_workflow)
    return parser
