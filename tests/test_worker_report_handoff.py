"""Tests for the two-turn worker→review handoff.

After a worker DONE the runtime no longer treats the last execute-turn
prose as the canonical completion_report. Instead it spawns a hidden
`report::<wid>::v1` work item that resumes the same worker session
under a dedicated report-generation prompt; only after that report
turn finishes does the review card get created. dispatch / delivery
work items skip the report step (they don't need one).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from opc.core.config import OPCConfig, RoleConfig
from opc.core.events import EventBus
from opc.core.models import (
    DelegationWorkItem,
    Phase,
    Task,
    TaskResult,
    TaskStatus,
)
from opc.database.store import OPCStore
from opc.layer2_organization.communication import CommunicationManager
from opc.layer2_organization.company_mode import (
    CompanyWorkItemExecutor,
    report_work_item_id_for_attempt,
    review_work_item_id_for_attempt,
)
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.work_item_links import set_linked_work_item_id


def _build_executor(store: OPCStore, org_engine: OrgEngine) -> CompanyWorkItemExecutor:
    communication = CommunicationManager(store, EventBus(), org_engine=org_engine)
    return CompanyWorkItemExecutor(
        org_engine=org_engine,
        communication=communication,
        approval_engine=MagicMock(),
        memory=None,
        execute_task=AsyncMock(),
        save_task=store.save_task,
        store=store,
    )


def _make_org_engine(root: Path) -> OrgEngine:
    config = OPCConfig()
    config.org.company_profile = "custom"
    config.org.roles = [
        RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
        RoleConfig(id="cto", name="CTO", responsibility="Lead engineering.", reports_to="ceo"),
        RoleConfig(id="engineer", name="Engineer", responsibility="Build features.", reports_to="cto"),
    ]
    return OrgEngine(config, root)


def _build_child_work_item() -> DelegationWorkItem:
    return DelegationWorkItem(
        work_item_id="wi-child",
        run_id="run-1",
        cell_id="team::cto",
        team_id="team::cto",
        role_id="engineer",
        seat_id="seat::team::cto::engineer",
        manager_role_id="cto",
        manager_seat_id="seat::team::cto::cto",
        title="Build feature",
        summary="Ship the feature.",
        kind="execute",
        projection_id="wi-child",
        phase=Phase.RUNNING,
        metadata={
            "team_id": "team::cto",
            "seat_id": "seat::team::cto::engineer",
            "manager_role_id": "cto",
            "manager_seat_id": "seat::team::cto::cto",
            "runtime_model": "multi_team_org",
            "activation_state": "active",
            "work_kind": "execute",
        },
    )


def _build_worker_task() -> Task:
    task = Task(
        id="task-engineer",
        title="Build feature",
        project_id="proj1",
        session_id="session-root",
        parent_session_id="session-root",
        assigned_to="engineer",
        status=TaskStatus.DONE,
        metadata={
            "execution_mode": "company_mode",
            "runtime_model": "multi_team_org",
            "work_item_runtime": True,
            "delegation_run_id": "run-1",
            "delegation_team_id": "team::cto",
            "delegation_seat_id": "seat::team::cto::engineer",
            "work_item_role_id": "engineer",
            "manager_role_id": "cto",
            "manager_seat_id": "seat::team::cto::cto",
            "work_item_projection_id": "wi-child",
            "work_kind": "execute",
        },
    )
    set_linked_work_item_id(task, "wi-child")
    return task


class WorkerExecuteDoneSpawnsReportTests(unittest.IsolatedAsyncioTestCase):
    """Phase 1 of the handoff: worker execute turn finishes.

    Expectation: a hidden report work item is created in the worker
    seat's queue. The review work item is NOT created yet — that
    happens only after the report turn finishes.
    """

    async def test_worker_done_spawns_report_card_and_no_review_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            try:
                org_engine = _make_org_engine(root)
                executor = _build_executor(store, org_engine)

                await store.save_delegation_work_item(_build_child_work_item())
                worker_task = _build_worker_task()
                await store.save_task(worker_task)

                await executor._apply_done_transition(
                    worker_task,
                    result=TaskResult(
                        status=TaskStatus.DONE,
                        content="All shipped — handoff prose from execute turn.",
                    ),
                )

                report_id = report_work_item_id_for_attempt("wi-child", 1)
                report_card = await store.get_delegation_work_item(report_id)
                self.assertIsNotNone(
                    report_card,
                    "report card must be spawned when worker execute turn finishes",
                )
                self.assertEqual(report_card.kind, "report")
                self.assertEqual(report_card.phase, Phase.READY)
                self.assertTrue(report_card.metadata.get("report_execution_work_item"))
                self.assertTrue(report_card.metadata.get("hidden_from_company_kanban"))
                self.assertEqual(
                    report_card.metadata.get("current_turn_mode"),
                    "report_required",
                )
                self.assertEqual(
                    report_card.metadata.get("report_target_work_item_id"),
                    "wi-child",
                )
                # The same worker seat owns the report card — it's the
                # worker's own session being resumed for the handoff.
                self.assertEqual(report_card.role_id, "engineer")
                self.assertEqual(report_card.seat_id, "seat::team::cto::engineer")

                # The review card must NOT exist yet.
                review_id = review_work_item_id_for_attempt("wi-child", 1)
                self.assertIsNone(
                    await store.get_delegation_work_item(review_id),
                    "review card must wait for the report turn to finish",
                )
            finally:
                await store.close()

    async def test_dispatch_kind_skips_report_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            try:
                org_engine = _make_org_engine(root)
                executor = _build_executor(store, org_engine)

                # Dispatch card that actually delegated (a live child card
                # exists in the store). Delegated output routes directly to
                # APPROVED — the children carry the reviewable output, so no
                # review and no report turn for the dispatch card itself.
                # (A dispatch card WITHOUT children is the self-produced
                # case and does get the report/review chain.)
                child = _build_child_work_item()
                child.metadata = dict(child.metadata or {})
                child.metadata["work_kind"] = "dispatch"
                await store.save_delegation_work_item(child)
                delegated = _build_child_work_item()
                delegated.work_item_id = "wi-grandchild"
                delegated.projection_id = "wi-grandchild"
                delegated.parent_work_item_id = "wi-child"
                await store.save_delegation_work_item(delegated)
                worker_task = _build_worker_task()
                worker_task.metadata = dict(worker_task.metadata or {})
                worker_task.metadata["work_kind"] = "dispatch"
                await store.save_task(worker_task)

                await executor._apply_done_transition(
                    worker_task,
                    result=TaskResult(status=TaskStatus.DONE, content="dispatched"),
                )

                self.assertIsNone(
                    await store.get_delegation_work_item(
                        report_work_item_id_for_attempt("wi-child", 1)
                    ),
                    "dispatch DONE must not spawn a report turn",
                )
                self.assertIsNone(
                    await store.get_delegation_work_item(
                        review_work_item_id_for_attempt("wi-child", 1)
                    ),
                    "dispatch DONE must not spawn a review turn",
                )
            finally:
                await store.close()


class ReportTurnDoneSpawnsReviewTests(unittest.IsolatedAsyncioTestCase):
    """Phase 2 of the handoff: the report turn finishes.

    Expectation: the review work item is now spawned, and the
    completion_report it carries is the report turn's output (not the
    original execute prose). The hidden report card itself transitions
    to APPROVED.
    """

    async def _setup_after_execute_done(
        self, store: OPCStore, executor: CompanyWorkItemExecutor
    ) -> tuple[Task, str]:
        await store.save_delegation_work_item(_build_child_work_item())
        worker_task = _build_worker_task()
        await store.save_task(worker_task)
        await executor._apply_done_transition(
            worker_task,
            result=TaskResult(
                status=TaskStatus.DONE,
                content="execute turn prose — should NOT end up as completion_report",
            ),
        )
        report_id = report_work_item_id_for_attempt("wi-child", 1)
        report_card = await store.get_delegation_work_item(report_id)
        self.assertIsNotNone(report_card)
        # In production the dispatcher claims the report card
        # (READY → RUNNING) before the worker actually runs the report
        # turn. We bypass the dispatcher here, so flip it manually so
        # _apply_report_done_transition can later close it RUNNING →
        # APPROVED via the canonical transition table.
        await store.update_delegation_work_item(report_id, phase=Phase.RUNNING)
        return worker_task, report_id

    def _report_turn_task(
        self, *, report_card_id: str, target_work_item_id: str
    ) -> Task:
        # The materialized Task that the dispatcher would build for the
        # report card. We construct it directly here.
        task = Task(
            id="task-report-1",
            title="Write handoff report",
            project_id="proj1",
            session_id="session-root",
            parent_session_id="session-root",
            assigned_to="engineer",
            status=TaskStatus.DONE,
            metadata={
                "execution_mode": "company_mode",
                "runtime_model": "multi_team_org",
                "work_item_runtime": True,
                "delegation_run_id": "run-1",
                "delegation_team_id": "team::cto",
                "delegation_seat_id": "seat::team::cto::engineer",
                "work_item_role_id": "engineer",
                "manager_role_id": "cto",
                "manager_seat_id": "seat::team::cto::cto",
                "report_execution_work_item": True,
                "report_target_work_item_id": target_work_item_id,
                "work_kind": "report",
                "work_item_turn_type": "report",
                "current_turn_mode": "report_required",
            },
        )
        set_linked_work_item_id(task, report_card_id)
        set_linked_work_item_id(task, report_card_id)
        return task

    async def test_report_turn_done_spawns_review_with_report_as_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            try:
                org_engine = _make_org_engine(root)
                executor = _build_executor(store, org_engine)

                _worker_task, report_id = await self._setup_after_execute_done(
                    store, executor
                )

                # Need to also set the parent's review_owner_role_id /
                # review_owner_seat_id, which the canonical execute-DONE
                # path stamps on the parent metadata. (Done by the
                # earlier _apply_done_transition call.) Sanity check:
                parent = await store.get_delegation_work_item("wi-child")
                self.assertIsNotNone(parent)
                self.assertEqual(parent.metadata.get("review_owner_role_id"), "cto")

                report_task = self._report_turn_task(
                    report_card_id=report_id, target_work_item_id="wi-child"
                )
                report_payload = (
                    "Handoff report:\n\n"
                    '{"summary":"Built the feature with tests.",\n'
                    ' "deliverables":[{"name":"feature.py","path":"/tmp/feature.py","status":"complete"}],\n'
                    ' "acceptance_status":[{"criterion":"feature works","met":true,"evidence":"tests pass"}],\n'
                    ' "risks":["minor flakiness on Windows"],\n'
                    ' "next_actions":["reviewer to verify integration test"]}'
                )
                await executor._apply_done_transition(
                    report_task,
                    result=TaskResult(status=TaskStatus.DONE, content=report_payload),
                )

                review_id = review_work_item_id_for_attempt("wi-child", 1)
                review_card = await store.get_delegation_work_item(review_id)
                self.assertIsNotNone(
                    review_card,
                    "review card must be spawned after report turn finishes",
                )
                self.assertEqual(review_card.kind, "review")
                self.assertEqual(
                    review_card.metadata.get("review_completion_report"),
                    report_payload,
                    "review_completion_report must come from the report turn, not the execute turn",
                )
                evidence = review_card.metadata.get("review_evidence", {}) or {}
                worker_report = evidence.get("worker_report") or {}
                self.assertIn(
                    "Built the feature",
                    str(worker_report.get("summary", "")),
                    "parsed worker report should be merged into review_evidence",
                )

                # The hidden report card itself is now APPROVED.
                report_card_after = await store.get_delegation_work_item(report_id)
                self.assertEqual(report_card_after.phase, Phase.APPROVED)
            finally:
                await store.close()

    async def test_report_json_parsing_failure_falls_back_to_full_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            try:
                org_engine = _make_org_engine(root)
                executor = _build_executor(store, org_engine)
                _worker_task, report_id = await self._setup_after_execute_done(
                    store, executor
                )

                # Pure prose handoff — no JSON. Per design we DO NOT
                # re-prompt the worker; we hand the prose to the
                # reviewer as-is.
                pure_prose = "I built the thing. It works. Tests pass. No JSON."
                report_task = self._report_turn_task(
                    report_card_id=report_id, target_work_item_id="wi-child"
                )
                await executor._apply_done_transition(
                    report_task,
                    result=TaskResult(status=TaskStatus.DONE, content=pure_prose),
                )

                review_id = review_work_item_id_for_attempt("wi-child", 1)
                review_card = await store.get_delegation_work_item(review_id)
                self.assertIsNotNone(review_card)
                self.assertEqual(
                    review_card.metadata.get("review_completion_report"),
                    pure_prose,
                    "prose handoff must reach the reviewer verbatim",
                )
                # No worker_report field when parsing failed (or it's empty).
                evidence = review_card.metadata.get("review_evidence", {}) or {}
                self.assertFalse(
                    evidence.get("worker_report"),
                    "no worker_report block expected when parsing failed",
                )
            finally:
                await store.close()


class ReportCardRunnableFilterTests(unittest.TestCase):
    """Pin the dispatcher-runnability filters for report cards.

    Two parallel filters in company_mode.py independently decide whether
    a hidden card is runnable: ``_work_item_is_runnable`` (the engine
    enqueue gate) and the materialization filter inside
    ``_materialize_work_item_tasks``. Both must let report cards
    through, or the worker session is never re-engaged for the handoff
    turn and the parent stays at AWAITING_MANAGER_REVIEW forever — the
    new22/app-4 stuck-at-review pathology.
    """

    def _make_parent_and_report(self) -> tuple[DelegationWorkItem, DelegationWorkItem]:
        parent = DelegationWorkItem(
            work_item_id="wi-parent",
            run_id="run-1",
            cell_id="team::cto",
            team_id="team::cto",
            role_id="engineer",
            seat_id="seat::team::cto::engineer",
            manager_role_id="cto",
            manager_seat_id="seat::team::cto::cto",
            title="Build feature",
            summary="Ship the feature.",
            kind="execute",
            projection_id="wi-parent",
            phase=Phase.AWAITING_MANAGER_REVIEW,
            metadata={
                "runtime_model": "multi_team_org",
                "team_id": "team::cto",
            },
        )
        report = DelegationWorkItem(
            work_item_id="report::wi-parent::v1",
            run_id="run-1",
            cell_id="team::cto",
            team_id="team::cto",
            role_id="engineer",
            seat_id="seat::team::cto::engineer",
            parent_work_item_id="wi-parent",
            kind="report",
            projection_id="report::wi-parent::v1",
            phase=Phase.READY,
            metadata={
                "runtime_model": "multi_team_org",
                "report_execution_work_item": True,
                "hidden_from_company_kanban": True,
                "report_target_work_item_id": "wi-parent",
                "team_id": "team::cto",
            },
        )
        return parent, report

    def test_runnable_filter_passes_report_card(self) -> None:
        parent, report = self._make_parent_and_report()
        wi_map = {parent.work_item_id: parent, report.work_item_id: report}
        self.assertTrue(
            CompanyWorkItemExecutor._work_item_is_runnable(report, wi_map),
            "report card must be considered runnable so the engine enqueues "
            "it; the new22/app-4 pathology was that the hidden+not-review "
            "filter excluded it and the parent stayed at AWAITING_MANAGER_REVIEW.",
        )

    def test_runnable_filter_skips_report_when_parent_left_review(self) -> None:
        parent, report = self._make_parent_and_report()
        # Parent has somehow advanced past review (e.g. CANCELLED). The
        # report card is now obsolete and must NOT be claimed.
        parent.phase = Phase.CANCELLED
        wi_map = {parent.work_item_id: parent, report.work_item_id: report}
        self.assertFalse(
            CompanyWorkItemExecutor._work_item_is_runnable(report, wi_map),
            "report card must not run when its parent is no longer in review",
        )


if __name__ == "__main__":
    unittest.main()
