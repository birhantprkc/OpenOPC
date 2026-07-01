"""Regression tests for Fix 1 (empty-verdict rework loop) and Fix 3
(dep-frontier refresh on all terminal child transitions).

Both bugs were observed live in project ``new16`` session ``app12``
(session_id ``875bdbde-7b97-4239-8e32-f2e52c96b289``), 2026-04-20:

Fix 1 — reviewer produced
``{"label":"reject","summary":"reject","blocking_issues":[],"followups":[]}``
for 5–6 consecutive rounds; each rework turn had no actionable guidance
and reproduced the same output, burning the full ``max_review_reworks``
budget before forced escalation.

Fix 3 — cto parent ``cdb248d8`` sat in WAITING_FOR_CHILDREN for 13+
minutes with the claim held by an idle session. Cause: refresh only
fired on the APPROVED-verdict branch of ``_finalize_review_work_item``
(line 5480). When children escalated to AWAITING_HUMAN or got
CANCELLED/FAILED, the parent was never re-evaluated.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from opc.core.models import (
    DelegationWorkItem,
    Phase,
    Task,
    TaskResult,
    TaskStatus,
)
from opc.database.store import OPCStore
from opc.layer2_organization import phase_hooks  # noqa: F401  (register hooks)
from opc.layer2_organization.company_mode import CompanyWorkItemExecutor
from opc.layer2_organization.work_item_links import set_linked_work_item_id
from opc.layer2_organization.work_item_transition import refresh_dependents_for_run


# ── Fix 1 / Fix 4 verdict-shape tests removed: runtime no longer makes
# shape-based decisions about verdicts. The reviewer agent's verdict is
# applied mechanically. See tests/test_verdict_parse_retry.py for the
# only remaining structural fallback (verdict cannot be parsed at all).


# ── Fix 3: refresh_dependents_for_run + hook wiring ──────────────────────


def _make_work_item(
    *,
    work_item_id: str,
    run_id: str,
    phase: Phase,
    role_id: str = "w",
    dependency_ids: list[str] | None = None,
    claimed_by: str = "",
    team_instance_id: str = "ti",
    team_id: str = "team::x",
    parent_work_item_id: str | None = None,
    metadata: dict | None = None,
) -> DelegationWorkItem:
    metadata = dict(metadata or {})
    if dependency_ids:
        metadata["dependency_work_item_ids"] = list(dependency_ids)
    return DelegationWorkItem(
        work_item_id=work_item_id,
        run_id=run_id,
        cell_id="c",
        team_instance_id=team_instance_id,
        team_id=team_id,
        role_id=role_id,
        seat_id=f"seat::{role_id}",
        parent_work_item_id=parent_work_item_id,
        manager_role_id="m",
        manager_seat_id="ms",
        title=f"item-{work_item_id}",
        phase=phase,
        claimed_by_role_runtime_session_id=claimed_by,
        claimed_by_seat_id=f"seat::{role_id}" if claimed_by else "",
        metadata=metadata,
    )


class RefreshDependentsForRunTests(unittest.IsolatedAsyncioTestCase):
    """Integration tests against a real OPCStore. Each test constructs a
    parent + children, transitions children, and asserts the parent's
    phase + claim after the refresh pass."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _save(self, *items: DelegationWorkItem) -> None:
        for it in items:
            await self.store.save_delegation_work_item(it)

    def _executor(self) -> CompanyWorkItemExecutor:
        async def execute_task(task: Task) -> TaskResult:
            return TaskResult(status=task.status, content="", artifacts={})

        return CompanyWorkItemExecutor(
            org_engine=SimpleNamespace(),
            communication=SimpleNamespace(on_kanban_changed=None, on_work_items_created=None),
            approval_engine=SimpleNamespace(),
            memory=None,
            execute_task=execute_task,
            save_task=self.store.save_task,
            store=self.store,
        )

    async def test_waiting_dependency_work_item_releases_when_dependencies_already_approved(self) -> None:
        dep_a = _make_work_item(work_item_id="dep-a", run_id="run-follow", phase=Phase.APPROVED)
        dep_b = _make_work_item(work_item_id="dep-b", run_id="run-follow", phase=Phase.APPROVED)
        follow = _make_work_item(
            work_item_id="follow",
            run_id="run-follow",
            phase=Phase.WAITING_DEPENDENCIES,
            role_id="report_producer",
            dependency_ids=["dep-a", "dep-b"],
        )
        await self._save(dep_a, dep_b, follow)
        task = Task(
            id="follow-task",
            title="Follow-up",
            project_id="proj1",
            assigned_to="report_producer",
            status=TaskStatus.BLOCKED,
            metadata={"work_item_projection_id": "follow", "work_item_turn_type": "execute"},
        )
        set_linked_work_item_id(task, "follow")
        await self.store.save_task(task)
        await self.store.link_work_item_runtime_task("follow", task.id)

        executor = self._executor()
        work_items = await executor._refresh_ready_work_items(
            await self.store.list_delegation_work_items("run-follow"),
            tasks=[task],
        )
        await executor._sync_task_projection_from_work_items([task], work_items)

        refreshed = await self.store.get_delegation_work_item("follow")
        refreshed_task = await self.store.get_task(task.id)
        self.assertEqual(refreshed.phase, Phase.READY)
        self.assertEqual(refreshed_task.status, TaskStatus.PENDING)

    async def test_waiting_dependency_work_item_stays_waiting_until_hard_dependencies_approve(self) -> None:
        dep_a = _make_work_item(work_item_id="dep-a", run_id="run-wait", phase=Phase.APPROVED)
        dep_b = _make_work_item(work_item_id="dep-b", run_id="run-wait", phase=Phase.RUNNING)
        follow = _make_work_item(
            work_item_id="follow",
            run_id="run-wait",
            phase=Phase.WAITING_DEPENDENCIES,
            role_id="report_producer",
            dependency_ids=["dep-a", "dep-b"],
        )
        await self._save(dep_a, dep_b, follow)

        executor = self._executor()
        await executor._refresh_ready_work_items(
            await self.store.list_delegation_work_items("run-wait"),
            tasks=[],
        )

        refreshed = await self.store.get_delegation_work_item("follow")
        self.assertEqual(refreshed.phase, Phase.WAITING_DEPENDENCIES)
        self.assertEqual(refreshed.metadata["waiting_on_work_item_ids"], ["dep-b"])

    async def test_materialized_follow_up_refreshes_already_approved_dependencies(self) -> None:
        parent = _make_work_item(
            work_item_id="parent",
            run_id="run-materialize",
            phase=Phase.RUNNING,
            role_id="chief_analyst",
        )
        dep_a = _make_work_item(work_item_id="dep-a", run_id="run-materialize", phase=Phase.APPROVED)
        dep_b = _make_work_item(work_item_id="dep-b", run_id="run-materialize", phase=Phase.APPROVED)
        await self._save(parent, dep_a, dep_b)

        manager_task = Task(
            id="manager-task",
            title="Chief Analyst Intake",
            project_id="proj1",
            assigned_to="chief_analyst",
            status=TaskStatus.RUNNING,
            metadata={
                "work_item_runtime": True,
                "work_item_runtime_version": 1,
                "runtime_model": "multi_team_org",
                "delegation_run_id": "run-materialize",
                "delegation_seat_id": "seat::chief_analyst",
                "runtime_topology": {
                    "seats": [
                        {
                            "role_id": "report_producer",
                            "seat_id": "seat::report_producer",
                            "team_id": "team::report",
                            "team_instance_id": "team-instance::report",
                            "seat_state_id": "seat-state::report",
                        }
                    ]
                },
            },
        )
        set_linked_work_item_id(manager_task, "parent")
        await self.store.save_task(manager_task)
        await self.store.link_work_item_runtime_task("parent", manager_task.id)

        executor = self._executor()
        executor._active_tasks = [manager_task]
        created = await executor._materialize_follow_up_work_items(
            manager_task,
            TaskResult(
                status=TaskStatus.DONE,
                content="Create a PPT deck.",
                artifacts={
                    "follow_up_actions": [
                        {
                            "action": "delegate_followup",
                            "target_role_id": "report_producer",
                            "title": "Generate PPT deck",
                            "summary": "Create a PPT with image2 visuals.",
                            "depends_on_work_item_ids": ["dep-a", "dep-b"],
                        }
                    ]
                },
            ),
        )

        self.assertEqual(len(created), 1)
        follow = await self.store.get_delegation_work_item(created[0])
        parent_after = await self.store.get_delegation_work_item("parent")
        self.assertEqual(follow.phase, Phase.READY)
        self.assertEqual(parent_after.phase, Phase.WAITING_FOR_CHILDREN)

    async def test_parent_wakes_when_last_child_approved(self) -> None:
        """The canonical app12 fix: parent in WAITING_FOR_CHILDREN with a
        stale claim unblocks to RUNNING and releases the claim when all
        children reach APPROVED."""
        parent = _make_work_item(
            work_item_id="parent",
            run_id="run-a",
            phase=Phase.RUNNING,  # will move to WAITING_FOR_CHILDREN via refresh
            role_id="cto",
            dependency_ids=["child-1", "child-2"],
            claimed_by="role-runtime::run-a::seat::team::ceo::cto",
        )
        child1 = _make_work_item(
            work_item_id="child-1",
            run_id="run-a",
            phase=Phase.APPROVED,
            parent_work_item_id="parent",
        )
        child2 = _make_work_item(
            work_item_id="child-2",
            run_id="run-a",
            phase=Phase.RUNNING,  # still in progress
            parent_work_item_id="parent",
        )
        await self._save(parent, child1, child2)

        # First pass: one child not approved → parent transitions
        # RUNNING → WAITING_FOR_CHILDREN.
        await refresh_dependents_for_run(self.store, run_id="run-a")
        after = await self.store.get_delegation_work_item("parent")
        self.assertEqual(after.phase, Phase.WAITING_FOR_CHILDREN)
        self.assertEqual(
            after.claimed_by_role_runtime_session_id,
            "role-runtime::run-a::seat::team::ceo::cto",
        )

        # Second child completes → hook fires refresh → parent unblocks.
        await self.store.update_delegation_work_item(
            "child-2", phase=Phase.APPROVED
        )
        after = await self.store.get_delegation_work_item("parent")
        self.assertEqual(after.phase, Phase.RUNNING)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(after.claimed_by_seat_id, "")

    async def test_final_delivery_parent_resumes_as_delivery_after_followup_child_approved(self) -> None:
        """A final delivery card reopened by owner follow-up parks while its
        new child runs, then resumes as a delivery/synthesis turn when that
        child is approved."""
        parent = _make_work_item(
            work_item_id="delivery-parent",
            run_id="run-delivery-followup",
            phase=Phase.WAITING_FOR_CHILDREN,
            role_id="ceo",
            dependency_ids=["old-research", "new-ppt"],
            claimed_by="role-runtime::run-delivery-followup::ceo",
            metadata={
                "work_kind": "delivery",
                "delegation_turn_kind": "delivery",
                "work_item_turn_type": "deliver",
                "current_turn_mode": "dispatch_required",
                "feedback_scope": "final",
                "authoritative_output": True,
                "user_visible": True,
                "requires_user_feedback": True,
            },
        )
        old_research = _make_work_item(
            work_item_id="old-research",
            run_id="run-delivery-followup",
            phase=Phase.APPROVED,
            parent_work_item_id="delivery-parent",
        )
        new_ppt = _make_work_item(
            work_item_id="new-ppt",
            run_id="run-delivery-followup",
            phase=Phase.RUNNING,
            parent_work_item_id="delivery-parent",
        )
        await self._save(parent, old_research, new_ppt)

        await self.store.update_delegation_work_item("new-ppt", phase=Phase.APPROVED)

        after = await self.store.get_delegation_work_item("delivery-parent")
        self.assertEqual(after.phase, Phase.RUNNING)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(after.claimed_by_seat_id, "")
        self.assertEqual(after.metadata.get("work_kind"), "delivery")
        self.assertEqual(after.metadata.get("delegation_turn_kind"), "delivery")
        self.assertEqual(after.metadata.get("work_item_turn_type"), "deliver")
        self.assertEqual(after.metadata.get("current_turn_mode"), "deliver_required")
        self.assertEqual(after.metadata.get("waiting_on_work_item_ids"), [])

    async def test_child_cancelled_triggers_parent_refresh(self) -> None:
        """Fix 3 core: non-APPROVED terminal (CANCELLED) must still fire
        the refresh hook. Before Fix 3, only APPROVED did."""
        parent = _make_work_item(
            work_item_id="parent-c",
            run_id="run-b",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["child-a", "child-b"],
            claimed_by="claim-x",
        )
        child_a = _make_work_item(
            work_item_id="child-a",
            run_id="run-b",
            phase=Phase.APPROVED,
        )
        child_b = _make_work_item(
            work_item_id="child-b",
            run_id="run-b",
            phase=Phase.RUNNING,
        )
        await self._save(parent, child_a, child_b)

        await self.store.update_delegation_work_item(
            "child-b", phase=Phase.CANCELLED
        )
        # Parent still WAITING_FOR_CHILDREN (not all approved), but the
        # hook ran — verify by checking waiting_on_work_item_ids.
        after = await self.store.get_delegation_work_item("parent-c")
        self.assertEqual(after.phase, Phase.WAITING_FOR_CHILDREN)
        # Waiting list should reflect the deps (hook wrote it).
        self.assertEqual(
            list(after.metadata.get("waiting_on_work_item_ids", [])),
            ["child-a", "child-b"],
        )

    async def test_manager_deleted_child_is_pruned_from_parent_dependencies(self) -> None:
        """Manager-deleted hidden children are graph edits, not hard blockers.

        A normal CANCELLED dependency still blocks (covered above). This case
        mirrors a recovery dispatch: an obsolete child was cancelled/hidden by
        the manager, a replacement child finished, and the parent should not
        wait forever on the stale id.
        """
        parent = _make_work_item(
            work_item_id="parent-prune",
            run_id="run-prune",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["approved-child", "deleted-child", "replacement-child"],
            claimed_by="stale-parent-claim",
        )
        approved_child = _make_work_item(
            work_item_id="approved-child",
            run_id="run-prune",
            phase=Phase.APPROVED,
            parent_work_item_id="parent-prune",
        )
        deleted_child = _make_work_item(
            work_item_id="deleted-child",
            run_id="run-prune",
            phase=Phase.CANCELLED,
            parent_work_item_id="parent-prune",
        )
        deleted_child.metadata.update(
            {
                "deleted_by_manager_tool": True,
                "hidden_from_company_kanban": True,
                "upstream_visibility": "hidden",
            }
        )
        replacement_child = _make_work_item(
            work_item_id="replacement-child",
            run_id="run-prune",
            phase=Phase.APPROVED,
            parent_work_item_id="parent-prune",
        )
        await self._save(parent, approved_child, deleted_child, replacement_child)

        changed = await refresh_dependents_for_run(self.store, run_id="run-prune")

        self.assertTrue(changed)
        after = await self.store.get_delegation_work_item("parent-prune")
        self.assertEqual(after.phase, Phase.RUNNING)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(
            list(after.metadata.get("dependency_work_item_ids", [])),
            ["approved-child", "replacement-child"],
        )
        self.assertEqual(after.metadata.get("waiting_on_work_item_ids"), [])
        self.assertIn("deleted-child", after.metadata.get("pruned_dependency_work_item_ids", []))

    async def test_child_awaiting_human_triggers_refresh(self) -> None:
        """Fix 3 regression: when max_review_reworks escalates a child to
        AWAITING_HUMAN, the hook must fire so the parent's dep metadata
        is updated. Previously this transition was silent and the parent
        drifted into a zombie state."""
        parent = _make_work_item(
            work_item_id="parent-h",
            run_id="run-c",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["child-h1", "child-h2"],
            claimed_by="claim-z",
        )
        child_h1 = _make_work_item(
            work_item_id="child-h1",
            run_id="run-c",
            phase=Phase.APPROVED,
        )
        child_h2 = _make_work_item(
            work_item_id="child-h2",
            run_id="run-c",
            phase=Phase.AWAITING_MANAGER_REVIEW,
        )
        await self._save(parent, child_h1, child_h2)

        await self.store.update_delegation_work_item(
            "child-h2", phase=Phase.AWAITING_HUMAN
        )
        # Parent stays waiting (AWAITING_HUMAN is not APPROVED), but the
        # hook ran. Next step: a human approves → parent should unblock.
        await self.store.update_delegation_work_item(
            "child-h2", phase=Phase.APPROVED
        )
        after = await self.store.get_delegation_work_item("parent-h")
        self.assertEqual(after.phase, Phase.RUNNING)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")

    async def test_refresh_is_reentrancy_safe(self) -> None:
        """The hook fires during a write; the write itself can be
        triggered by the hook's update (parent phase change). Verify we
        don't recurse forever — 3 levels deep should converge."""
        grandparent = _make_work_item(
            work_item_id="gp",
            run_id="run-d",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["parent-d"],
            claimed_by="gp-claim",
        )
        parent_d = _make_work_item(
            work_item_id="parent-d",
            run_id="run-d",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["leaf"],
            claimed_by="p-claim",
            parent_work_item_id="gp",
        )
        leaf = _make_work_item(
            work_item_id="leaf",
            run_id="run-d",
            phase=Phase.RUNNING,
            parent_work_item_id="parent-d",
        )
        await self._save(grandparent, parent_d, leaf)

        await self.store.update_delegation_work_item(
            "leaf", phase=Phase.APPROVED
        )
        # One approval should cascade: leaf approved → parent_d runs →
        # parent_d needs to hit APPROVED for grandparent to unblock.
        # parent_d will run but not be approved in this test, so
        # grandparent should still be WAITING_FOR_CHILDREN but its dep
        # waiting_on_work_item_ids should reflect the current state.
        p_after = await self.store.get_delegation_work_item("parent-d")
        gp_after = await self.store.get_delegation_work_item("gp")
        self.assertEqual(p_after.phase, Phase.RUNNING)
        self.assertEqual(p_after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(gp_after.phase, Phase.WAITING_FOR_CHILDREN)

    async def test_refresh_no_change_returns_false(self) -> None:
        """Calling refresh with no eligible work items is a no-op."""
        lonely = _make_work_item(
            work_item_id="solo",
            run_id="run-e",
            phase=Phase.RUNNING,
            # no dependency_ids → nothing to refresh
        )
        await self._save(lonely)
        changed = await refresh_dependents_for_run(
            self.store, run_id="run-e"
        )
        self.assertFalse(changed)

    # ── RC3 Step-1 fixes: READY_FOR_REWORK triggers refresh + broadened claim release ──

    async def test_child_ready_for_rework_bubbles_refresh(self) -> None:
        """Step-1 core: reviewer rejects child with rework → child flips to
        READY_FOR_REWORK. Before the fix, this transition was not in
        _DEPENDENT_REFRESH_TARGETS so the parent's waiting_on_work_item_ids
        stayed stale. Verify the hook now fires and parent's frontier
        reflects the rework child."""
        parent = _make_work_item(
            work_item_id="parent-r",
            run_id="run-r",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["child-r1", "child-r2"],
            claimed_by="stale-claim",
        )
        child_r1 = _make_work_item(
            work_item_id="child-r1",
            run_id="run-r",
            phase=Phase.APPROVED,
        )
        child_r2 = _make_work_item(
            work_item_id="child-r2",
            run_id="run-r",
            phase=Phase.AWAITING_MANAGER_REVIEW,
        )
        await self._save(parent, child_r1, child_r2)

        # Reviewer returns rework verdict → child flips to READY_FOR_REWORK.
        # Before Step 1 fix: parent frontier untouched, claim stale forever.
        # After fix: hook runs, waiting_on_work_item_ids is rewritten.
        await self.store.update_delegation_work_item(
            "child-r2", phase=Phase.READY_FOR_REWORK
        )
        after = await self.store.get_delegation_work_item("parent-r")
        self.assertEqual(after.phase, Phase.WAITING_FOR_CHILDREN)
        self.assertEqual(
            list(after.metadata.get("waiting_on_work_item_ids", [])),
            ["child-r1", "child-r2"],
        )

    async def test_parent_wakes_from_waiting_with_stale_claim_released(self) -> None:
        """Step-1 broadened claim release: parent in WAITING_FOR_CHILDREN
        exits toward ANY non-terminal phase → stale claim is cleared.
        Earlier, claim release only fired when target==RUNNING and
        all_approved; if the parent instead went to e.g. READY through
        dependency regression propagation, claim stayed held.
        This test drives the WAITING_FOR_CHILDREN → RUNNING path (the
        canonical case), but the new condition also covers other wakes.
        """
        parent = _make_work_item(
            work_item_id="parent-w",
            run_id="run-w",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["child-w"],
            claimed_by="idle-session-123",
        )
        child_w = _make_work_item(
            work_item_id="child-w",
            run_id="run-w",
            phase=Phase.RUNNING,
        )
        await self._save(parent, child_w)

        await self.store.update_delegation_work_item(
            "child-w", phase=Phase.APPROVED
        )
        after = await self.store.get_delegation_work_item("parent-w")
        self.assertEqual(after.phase, Phase.RUNNING)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(after.claimed_by_seat_id, "")

    async def test_parent_terminal_keeps_claim_as_audit(self) -> None:
        """Step-1 broadened claim release explicitly excludes DONE_PHASES:
        a WAITING_FOR_CHILDREN parent that transitions to CANCELLED/FAILED
        MUST retain the claim as a "last executor" audit record.
        Force this path by simulating a higher-level cancel that flips the
        parent directly to CANCELLED via the store.
        """
        parent = _make_work_item(
            work_item_id="parent-t",
            run_id="run-t",
            phase=Phase.WAITING_FOR_CHILDREN,
            dependency_ids=["child-t"],
            claimed_by="historical-session",
        )
        child_t = _make_work_item(
            work_item_id="child-t",
            run_id="run-t",
            phase=Phase.RUNNING,
        )
        await self._save(parent, child_t)

        # Directly cancel the parent (as if a higher-level cancel cascaded).
        await self.store.update_delegation_work_item(
            "parent-t", phase=Phase.CANCELLED
        )
        after = await self.store.get_delegation_work_item("parent-t")
        self.assertEqual(after.phase, Phase.CANCELLED)
        # Claim preserved — it is a historical audit record for terminal items.
        self.assertEqual(
            after.claimed_by_role_runtime_session_id, "historical-session"
        )


if __name__ == "__main__":
    unittest.main()
