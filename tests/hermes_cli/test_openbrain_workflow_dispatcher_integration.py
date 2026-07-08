from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_artifacts import read_events, read_status
from hermes_cli.openbrain_workflow_dag import create_openbrain_workflow_cards
from hermes_cli.openbrain_workflow_supervisor import ChildRunResult, StepAdapter


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    kb.init_db()
    return home


class FakeRunner:
    def __init__(self, *, mode: str = "success") -> None:
        self.mode = mode
        self.calls: list[str] = []

    def run(
        self,
        adapter: StepAdapter,
        *,
        timeout_seconds=None,
        heartbeat_callback=None,
        heartbeat_interval_seconds=0,
        poll_interval_seconds=0,
    ) -> ChildRunResult:
        self.calls.append(adapter.step_key)
        receipt = Path(adapter.receipt_pointer_path)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        if heartbeat_callback:
            heartbeat_callback(f"obwf=obwf_dispatch step={adapter.step_key} source=source-a elapsed=00:00:01")
        if self.mode == "crash":
            return ChildRunResult(exit_code=2, stdout="", stderr="SECRET_SENTINEL_SHOULD_NOT_APPEAR crash", timed_out=False)
        if self.mode == "timeout":
            return ChildRunResult(exit_code=None, stdout="", stderr="timed out", timed_out=True)
        if self.mode == "missing_receipt":
            return ChildRunResult(exit_code=0, stdout="{}", stderr="", timed_out=False)
        if self.mode == "malformed_receipt":
            receipt.write_text("{not json", encoding="utf-8")
            return ChildRunResult(exit_code=0, stdout="{}", stderr="", timed_out=False)
        receipt.write_text(
            "{"
            f'"workflow_run_id":"obwf_dispatch",'
            f'"step_key":"{adapter.step_key}",'
            '"source_unit_id":"source-a",'
            f'"candidate_ids":["{adapter.step_key}-cand"],'
            f'"receipt_path":"{receipt.parent / "receipt.json"}",'
            '"dashboard_verification":{"ok":true,"visible_candidate_count":1}'
            "}",
            encoding="utf-8",
        )
        (receipt.parent / "receipt.json").write_text("{}", encoding="utf-8")
        return ChildRunResult(exit_code=0, stdout="{}", stderr="", timed_out=False)


def _create(conn, *, execute_mode="dry-run-execute", mode="panning-only", run_id="obwf_dispatch"):
    return create_openbrain_workflow_cards(
        workflow_run_id=run_id,
        source_unit_id="source-a",
        source_folder="/tmp/source-a",
        source_title="Synthetic Source",
        mode=mode,
        assignee="default",
        execute_mode=execute_mode,
        board="default",
        conn=conn,
    )


def _artifact_text(run_id: str) -> str:
    status = read_status(run_id)
    pieces = [status.model_dump_json() if status else ""]
    pieces.extend(json.dumps(event, sort_keys=True) for event in read_events(run_id))
    return "\n".join(pieces)


def test_dispatcher_routes_openbrain_step_through_supervisor_fake_runner(kanban_home):
    runner = FakeRunner()
    with kb.connect() as conn:
        created = _create(conn)
        panning_id = created.step_task_ids["panning_for_gold"]

        result = kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )

        assert result.spawned and result.spawned[0][0] == panning_id
        assert runner.calls == ["panning_for_gold"]
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "done"
        status = read_status("obwf_dispatch")
        assert status is not None
        assert status.steps["panning_for_gold"].status == "done"
        assert status.steps["panning_for_gold"].candidate_ids == ["panning_for_gold-cand"]
        assert any(event["event_type"] == "task_completed" for event in read_events("obwf_dispatch"))


def test_dispatcher_blocks_missing_and_malformed_receipts(kanban_home):
    for mode in ("missing_receipt", "malformed_receipt"):
        runner = FakeRunner(mode=mode)
        with kb.connect() as conn:
            created = create_openbrain_workflow_cards(
                workflow_run_id=f"obwf_{mode}",
                source_unit_id="source-a",
                source_folder="/tmp/source-a",
                mode="panning-only",
                assignee="default",
                execute_mode="dry-run-execute",
                conn=conn,
            )
            task_id = created.step_task_ids["panning_for_gold"]
            # Keep the fake runner payload aligned with the active run id.
            runner.calls.clear()
            kb.dispatch_once(
                conn,
                max_spawn=1,
                openbrain_runner_factory=lambda task, execute_mode, r=runner: r,
            )
            task = kb.get_task(conn, task_id)
            assert task.status == "blocked"
            assert kb.get_task(conn, created.step_task_ids["thought_enrichment"]).status == "todo"


def test_dispatcher_blocks_crash_and_timeout_safely_without_sentinel_leak(kanban_home):
    for mode in ("crash", "timeout"):
        run_id = f"obwf_{mode}_fault"
        runner = FakeRunner(mode=mode)
        with kb.connect() as conn:
            created = _create(conn, run_id=run_id)
            task_id = created.step_task_ids["panning_for_gold"]

            kb.dispatch_once(
                conn,
                max_spawn=1,
                openbrain_runner_factory=lambda task, execute_mode, r=runner: r,
            )

            task = kb.get_task(conn, task_id)
            assert task is not None
            assert task.status == "blocked"
            assert task.block_kind == "transient"
            downstream = kb.get_task(conn, created.step_task_ids["thought_enrichment"])
            assert downstream is not None
            assert downstream.status == "todo"
            text = _artifact_text(run_id) + "\n" + (task.last_failure_error or "")
            assert "timed out" in text or "child exited" in text
            assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in text


def test_retry_after_transient_block_is_idempotent_and_can_complete(kanban_home):
    with kb.connect() as conn:
        created = _create(conn)
        panning_id = created.step_task_ids["panning_for_gold"]

        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: FakeRunner(mode="crash"),
        )
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "blocked"
        assert kb.unblock_task(conn, panning_id) is True
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "ready"

        runner = FakeRunner()
        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )

        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "done"
        status = read_status("obwf_dispatch")
        assert status is not None
        assert status.steps["panning_for_gold"].candidate_ids == ["panning_for_gold-cand"]
        assert runner.calls == ["panning_for_gold"]
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 6
        events = read_events("obwf_dispatch")
        assert len([e for e in events if e["event_type"] == "task_created"]) == 6
        assert len([e for e in events if e["event_type"] == "task_completed" and e.get("step_key") == "panning_for_gold"]) == 1


def test_dispatcher_keeps_plan_only_workflow_from_generic_spawn(kanban_home):
    with kb.connect() as conn:
        created = _create(conn, execute_mode="plan-only")
        panning_id = created.step_task_ids["panning_for_gold"]

        result = kb.dispatch_once(
            conn,
            max_spawn=1,
            spawn_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic spawn used")),
        )

        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "ready"
        assert (panning_id, "openbrain_plan_only") in result.respawn_guarded


def test_dispatcher_routes_production_execute_through_supervisor_not_generic_spawn(kanban_home):
    runner = FakeRunner()
    with kb.connect() as conn:
        created = _create(conn, execute_mode="production-execute")
        panning_id = created.step_task_ids["panning_for_gold"]

        result = kb.dispatch_once(
            conn,
            max_spawn=1,
            spawn_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic spawn used")),
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )

        assert result.spawned and result.spawned[0][0] == panning_id
        assert runner.calls == ["panning_for_gold"]
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "done"


def test_dispatcher_dry_run_e2e_stops_at_human_review_gate(kanban_home):
    runner = FakeRunner()
    with kb.connect() as conn:
        created = _create(conn, mode="panning-only")
        # Run enough ticks for panning -> enrichment -> dedupe to complete. Human
        # review is created blocked and must not silently promote.
        for _ in range(5):
            kb.dispatch_once(
                conn,
                max_spawn=1,
                openbrain_runner_factory=lambda task, execute_mode: runner,
            )

        assert kb.get_task(conn, created.step_task_ids["panning_for_gold"]).status == "done"
        assert kb.get_task(conn, created.step_task_ids["thought_enrichment"]).status == "done"
        assert kb.get_task(conn, created.step_task_ids["dedupe"]).status == "done"
        assert kb.get_task(conn, created.step_task_ids["human_review"]).status == "blocked"
        assert kb.get_task(conn, created.step_task_ids["ready_cortexdb_import"]).status == "todo"


def test_duplicate_start_does_not_duplicate_cards_links_or_task_created_events(kanban_home):
    with kb.connect() as conn:
        first = _create(conn, mode="panning-only")
        second = _create(conn, mode="panning-only")
        assert first.root_task_id == second.root_task_id
        assert first.step_task_ids == second.step_task_ids
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        link_count = conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0]
        assert task_count == 6  # root + panning + enrichment + dedupe + human_review + ready/import
        assert link_count == 6
        events = read_events("obwf_dispatch")
        created_rows = [e for e in events if e["event_type"] == "task_created"]
        assert len(created_rows) == 6
