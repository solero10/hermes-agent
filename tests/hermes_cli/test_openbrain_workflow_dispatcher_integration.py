from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.openbrain_workflow_artifacts import read_events, read_status
from hermes_cli.openbrain_workflow_dag import create_openbrain_workflow_cards
from hermes_cli.openbrain_workflow_supervisor import ChildRunResult, StepAdapter, review_approval_path


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
        self.adapters: list[StepAdapter] = []

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
        self.adapters.append(adapter)
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
        payload = {
            "workflow_run_id": adapter.workflow_run_id,
            "step_key": adapter.step_key,
            "source_unit_id": "source-a",
            "candidate_ids": [f"{adapter.step_key}-cand"],
            "receipt_path": str(receipt.parent / "receipt.json"),
            "dashboard_verification": {"ok": True, "visible_candidate_count": 1},
        }
        if adapter.input_candidate_ids:
            payload["input_candidate_ids"] = ["wrong-input"] if self.mode == "wrong_input" else list(adapter.input_candidate_ids)
        if self.mode in {"import_success", "missing_dashboard_verification"}:
            import_receipt = receipt.parent / "import-receipt.json"
            import_receipt.write_text(json.dumps({"imported": False, "synthetic": True}), encoding="utf-8")
            payload["import_receipt_path"] = str(import_receipt)
        if self.mode == "missing_dashboard_verification":
            payload["dashboard_verification"] = {"ok": False}
        receipt.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
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


def _complete_through_dedupe(conn, created):
    runner = FakeRunner()
    for _ in range(4):
        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )
    panning = kb.get_task(conn, created.step_task_ids["panning_for_gold"])
    enrichment = kb.get_task(conn, created.step_task_ids["thought_enrichment"])
    dedupe = kb.get_task(conn, created.step_task_ids["dedupe"])
    assert panning is not None
    assert enrichment is not None
    assert dedupe is not None
    assert panning.status == "done"
    assert enrichment.status == "done"
    assert dedupe.status == "done"
    return runner


def _make_ready_import_dispatchable(conn, created, *, approved: bool) -> None:
    if approved:
        path = review_approval_path("obwf_dispatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"approved": True, "synthetic": True}), encoding="utf-8")
    conn.execute(
        "UPDATE tasks SET status = 'done' WHERE id = ?",
        (created.step_task_ids["human_review"],),
    )
    kb.recompute_ready(conn)
    ready_task = kb.get_task(conn, created.step_task_ids["ready_cortexdb_import"])
    assert ready_task is not None
    assert ready_task.status == "ready"


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


def test_dispatcher_threads_parent_candidate_ids_into_enrichment_adapter(kanban_home):
    runner = FakeRunner()
    with kb.connect() as conn:
        created = _create(conn)

        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )
        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )

        assert runner.calls == ["panning_for_gold", "thought_enrichment"]
        assert runner.adapters[1].step_key == "thought_enrichment"
        assert runner.adapters[1].input_candidate_ids == ["panning_for_gold-cand"]
        task = kb.get_task(conn, created.step_task_ids["thought_enrichment"])
        assert task is not None
        assert task.status == "done"
        status = read_status("obwf_dispatch")
        assert status is not None
        assert status.steps["thought_enrichment"].candidate_ids == ["thought_enrichment-cand"]


def test_dispatcher_blocks_enrichment_receipt_with_mismatched_input_candidates(kanban_home):
    first_runner = FakeRunner()
    second_runner = FakeRunner(mode="wrong_input")
    with kb.connect() as conn:
        created = _create(conn)

        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: first_runner,
        )
        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: second_runner,
        )

        assert second_runner.calls == ["thought_enrichment"]
        assert second_runner.adapters[0].input_candidate_ids == ["panning_for_gold-cand"]
        task = kb.get_task(conn, created.step_task_ids["thought_enrichment"])
        assert task is not None
        assert task.status == "blocked"
        text = _artifact_text("obwf_dispatch")
        assert "candidate mismatch" in text
        downstream = kb.get_task(conn, created.step_task_ids["dedupe"])
        assert downstream is not None
        assert downstream.status == "todo"
        assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in _artifact_text("obwf_dispatch")


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


def test_stale_claim_reclaim_marks_openbrain_artifacts_and_retry_stays_idempotent(kanban_home):
    with kb.connect() as conn:
        created = _create(conn)
        panning_id = created.step_task_ids["panning_for_gold"]
        host = kb._claimer_id().split(":", 1)[0]
        claimed = kb.claim_task(conn, panning_id, claimer=f"{host}:stale-openbrain")
        assert claimed is not None
        now = int(time.time())
        conn.execute(
            "UPDATE tasks SET claim_expires = ?, last_heartbeat_at = ? WHERE id = ?",
            (
                now - 60,
                now - kb.DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS - 60,
                panning_id,
            ),
        )

        before_task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        before_link_count = conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0]
        reclaimed = kb.release_stale_claims(conn, signal_fn=lambda _pid, _sig: None)

        assert reclaimed == 1
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "ready"
        status = read_status("obwf_dispatch")
        assert status is not None
        assert status.status == "stale"
        assert status.steps["panning_for_gold"].status == "stale"
        assert "stale" in (status.steps["panning_for_gold"].blocked_or_error or "")
        assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in _artifact_text("obwf_dispatch")

        runner = FakeRunner()
        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: runner,
        )

        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status == "done"
        assert runner.calls == ["panning_for_gold"]
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before_task_count
        assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == before_link_count
        events = read_events("obwf_dispatch")
        assert len([e for e in events if e["event_type"] == "task_created"]) == 6
        assert len([e for e in events if e["event_type"] == "task_completed" and e.get("step_key") == "panning_for_gold"]) == 1


def test_detect_crashed_openbrain_worker_marks_artifacts_failed(kanban_home, monkeypatch):
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(kb, "_resolve_crash_grace_seconds", lambda: 0)
    with kb.connect() as conn:
        created = _create(conn, run_id="obwf_crash_reclaim")
        panning_id = created.step_task_ids["panning_for_gold"]
        host = kb._claimer_id().split(":", 1)[0]
        claimed = kb.claim_task(conn, panning_id, claimer=f"{host}:crashed-openbrain")
        assert claimed is not None
        kb._set_worker_pid(conn, panning_id, 987654321)

        crashed = kb.detect_crashed_workers(conn)

        assert panning_id in crashed
        task = kb.get_task(conn, panning_id)
        assert task is not None
        assert task.status in {"ready", "blocked"}
        status = read_status("obwf_crash_reclaim")
        assert status is not None
        assert status.status == "failed"
        assert status.steps["panning_for_gold"].status == "failed"
        assert "crashed" in (status.steps["panning_for_gold"].blocked_or_error or "")
        assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in _artifact_text("obwf_crash_reclaim")


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


def test_ready_import_dispatcher_blocks_before_runner_without_review_approval(kanban_home):
    with kb.connect() as conn:
        created = _create(conn, execute_mode="production-import")
        _complete_through_dedupe(conn, created)
        _make_ready_import_dispatchable(conn, created, approved=False)
        import_runner = FakeRunner(mode="import_success")

        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: import_runner,
        )

        assert import_runner.calls == []
        ready = kb.get_task(conn, created.step_task_ids["ready_cortexdb_import"])
        assert ready is not None
        assert ready.status == "blocked"
        assert ready.block_kind == "needs_input"
        text = _artifact_text("obwf_dispatch")
        assert "review approval" in text
        assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in text


def test_ready_import_dispatcher_blocks_missing_import_receipt_or_dashboard_verification(kanban_home):
    for mode, expected in (
        ("missing_import_receipt", "import receipt"),
        ("missing_dashboard_verification", "dashboard verification"),
    ):
        with kb.connect() as conn:
            run_id = f"obwf_{mode}"
            created = _create(conn, execute_mode="production-import", run_id=run_id)
            _complete_through_dedupe(conn, created)
            if run_id != "obwf_dispatch":
                path = review_approval_path(run_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"approved": True, "synthetic": True}), encoding="utf-8")
                conn.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?",
                    (created.step_task_ids["human_review"],),
                )
                kb.recompute_ready(conn)
            import_runner = FakeRunner(mode=mode)

            kb.dispatch_once(
                conn,
                max_spawn=1,
                openbrain_runner_factory=lambda task, execute_mode, r=import_runner: r,
            )

            assert import_runner.calls == ["ready_cortexdb_import"]
            ready = kb.get_task(conn, created.step_task_ids["ready_cortexdb_import"])
            assert ready is not None
            assert ready.status == "blocked"
            text = _artifact_text(run_id)
            assert expected in text
            assert "SECRET_SENTINEL_SHOULD_NOT_APPEAR" not in text


def test_ready_import_dispatcher_completes_with_review_import_receipt_and_dashboard_verification(kanban_home):
    with kb.connect() as conn:
        created = _create(conn, execute_mode="production-import")
        _complete_through_dedupe(conn, created)
        _make_ready_import_dispatchable(conn, created, approved=True)
        import_runner = FakeRunner(mode="import_success")

        kb.dispatch_once(
            conn,
            max_spawn=1,
            openbrain_runner_factory=lambda task, execute_mode: import_runner,
        )

        assert import_runner.calls == ["ready_cortexdb_import"]
        ready = kb.get_task(conn, created.step_task_ids["ready_cortexdb_import"])
        assert ready is not None
        assert ready.status == "done"
        status = read_status("obwf_dispatch")
        assert status is not None
        assert status.steps["ready_cortexdb_import"].status == "done"
        assert status.steps["ready_cortexdb_import"].receipt_path


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
