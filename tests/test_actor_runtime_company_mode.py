from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opc.core.config import OPCConfig, RoleConfig, SeatConfig, TeamConfig
from opc.core.events import EventBus
from opc.core.models import CompanyMemberSession, DelegationWorkItem, Phase, SeatState, Task, TaskResult, TaskStatus
from opc.database.store import OPCStore
from opc.layer2_organization.communication import CommunicationManager
from opc.layer2_organization.company_mode import CompanyWorkItemExecutor
from opc.layer2_organization.company_runtime import CompanyRuntime
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.org_work_item_planner import WorkItemGatePolicy
from opc.layer2_organization.work_item_runtime import (
    WORK_ITEM_RUNTIME_KEY,
    WORK_ITEM_RUNTIME_VERSION_KEY,
    is_work_item_runtime_metadata,
    mark_work_item_runtime,
    migrate_work_item_runtime_metadata,
    work_item_runtime_version,
)
from opc.layer2_organization.work_item_identity import (
    GATE_REWORK_PROJECTION_ID_KEY,
    GATE_TARGET_PROJECTION_ID_KEY,
    WORK_ITEM_PROJECTION_ID_KEY,
    WORK_ITEM_TURN_TYPE_KEY,
    canonical_work_item_turn_type_for_kind,
    gate_rework_payload,
    mark_gate_rework_projection,
    mark_projected_work_item_task,
    mark_work_item_projection,
    migrate_work_item_projection_metadata,
    projection_id_for_work_item,
    rework_projection_id_for_gate,
    target_projection_id_for_decision,
    target_projection_ids_for_decision,
    work_item_identity_payload,
    work_item_identity_payload_for_task,
    work_item_identity_payload_from_metadata,
    work_item_projection_id_from_metadata,
    work_item_turn_type_from_metadata,
)
from opc.layer2_organization.work_item_links import linked_work_item_id_for_task, set_linked_work_item_id


class ActorRuntimeOrgEngineTests(unittest.TestCase):
    def test_configured_teams_preserve_multiple_seats_for_middle_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OPCConfig()
            config.org.company_profile = "custom"
            config.org.final_decider_role_id = "ceo"
            config.org.roles = [
                RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
                RoleConfig(id="cto", name="CTO", responsibility="Lead engineering.", reports_to="ceo"),
                RoleConfig(id="engineer", name="Engineer", responsibility="Implement the work.", reports_to="cto"),
            ]
            config.org.teams = [
                TeamConfig(
                    team_id="team::ceo",
                    seats=[
                        SeatConfig(seat_id="seat::team::ceo::ceo", role_id="ceo", seat_kind="lead"),
                        SeatConfig(seat_id="seat::team::ceo::cto", role_id="cto"),
                    ],
                ),
                TeamConfig(
                    team_id="team::cto",
                    metadata={"parent_team_id": "team::ceo"},
                    seats=[
                        SeatConfig(
                            seat_id="seat::team::cto::cto",
                            role_id="cto",
                            seat_kind="lead",
                            manager_role_id="ceo",
                            manager_seat_id="seat::team::ceo::cto",
                        ),
                        SeatConfig(seat_id="seat::team::cto::engineer", role_id="engineer"),
                    ],
                ),
            ]

            engine = OrgEngine(config, Path(tmpdir))
            topology = engine.build_runtime_delegation_topology()
            seats_by_id = {seat["seat_id"]: seat for seat in topology["seats"]}

            self.assertEqual(engine.get_execution_model(), "actor_runtime")
            self.assertIn("seat::team::ceo::cto", seats_by_id)
            self.assertIn("seat::team::cto::cto", seats_by_id)
            self.assertEqual(seats_by_id["seat::team::ceo::cto"]["manager_role_id"], "ceo")
            self.assertEqual(seats_by_id["seat::team::cto::cto"]["manager_seat_id"], "seat::team::ceo::cto")
            self.assertEqual(seats_by_id["seat::team::ceo::cto"]["managed_team_id"], "team::cto")
            self.assertTrue(seats_by_id["seat::team::ceo::cto"]["metadata"]["configured_seat"])


class WorkItemRuntimeMetadataTests(unittest.TestCase):
    def test_work_item_runtime_marker_reads_only_new_field(self) -> None:
        self.assertTrue(is_work_item_runtime_metadata({WORK_ITEM_RUNTIME_KEY: True}))
        self.assertFalse(is_work_item_runtime_metadata({}))

    def test_mark_work_item_runtime_writes_new_fields_only_by_default(self) -> None:
        metadata = mark_work_item_runtime({"kept": "value"}, version=3)
        self.assertTrue(metadata[WORK_ITEM_RUNTIME_KEY])
        self.assertEqual(metadata[WORK_ITEM_RUNTIME_VERSION_KEY], 3)
        self.assertEqual(metadata["kept"], "value")
        self.assertEqual(work_item_runtime_version(metadata), 3)

    def test_migrate_work_item_runtime_metadata_normalizes_new_marker_version(self) -> None:
        migrated, changed = migrate_work_item_runtime_metadata(
            {
                WORK_ITEM_RUNTIME_KEY: True,
                "kept": "value",
            },
            default_version=4,
        )

        self.assertTrue(changed)
        self.assertTrue(migrated[WORK_ITEM_RUNTIME_KEY])
        self.assertEqual(migrated[WORK_ITEM_RUNTIME_VERSION_KEY], 4)
        self.assertEqual(migrated["kept"], "value")


class WorkItemProjectionIdentityTests(unittest.TestCase):
    def test_projection_identity_reads_new_fields_only(self) -> None:
        metadata = {
            WORK_ITEM_PROJECTION_ID_KEY: "new-projection",
            WORK_ITEM_TURN_TYPE_KEY: "review",
        }

        self.assertEqual(work_item_projection_id_from_metadata(metadata), "new-projection")
        self.assertEqual(work_item_turn_type_from_metadata(metadata), "review")
        self.assertEqual(work_item_projection_id_from_metadata({}, fallback="fallback-projection"), "fallback-projection")
        self.assertEqual(work_item_turn_type_from_metadata({}, fallback="execute"), "execute")

    def test_projection_turn_type_normalizes_delivery_alias(self) -> None:
        self.assertEqual(canonical_work_item_turn_type_for_kind("delivery"), "deliver")
        self.assertEqual(canonical_work_item_turn_type_for_kind("self-evolution"), "self_evolution")
        self.assertEqual(canonical_work_item_turn_type_for_kind("self evolution"), "self_evolution")
        self.assertEqual(
            work_item_turn_type_from_metadata({"work_kind": "delivery"}),
            "deliver",
        )
        self.assertEqual(
            work_item_turn_type_from_metadata({"work_kind": "self-evolution"}),
            "self_evolution",
        )
        self.assertEqual(
            work_item_turn_type_from_metadata({WORK_ITEM_TURN_TYPE_KEY: "delivery"}),
            "deliver",
        )
        self.assertEqual(
            mark_work_item_projection({}, projection_id="proj-1", turn_type="delivery")[WORK_ITEM_TURN_TYPE_KEY],
            "deliver",
        )

    def test_mark_work_item_projection_writes_new_fields_only(self) -> None:
        metadata = mark_work_item_projection({"kept": "value"}, projection_id="proj-1", turn_type="deliver")

        self.assertEqual(metadata[WORK_ITEM_PROJECTION_ID_KEY], "proj-1")
        self.assertEqual(metadata[WORK_ITEM_TURN_TYPE_KEY], "deliver")
        self.assertEqual(metadata["kept"], "value")

    def test_mark_projected_work_item_task_writes_new_fields_only(self) -> None:
        metadata = mark_projected_work_item_task(
            {
                "kept": "value",
            },
            projection_id="proj-task",
            turn_type="execute",
        )

        self.assertEqual(metadata[WORK_ITEM_PROJECTION_ID_KEY], "proj-task")
        self.assertEqual(metadata[WORK_ITEM_TURN_TYPE_KEY], "execute")
        self.assertEqual(metadata["kept"], "value")

    def test_work_item_identity_payload_outputs_new_fields_only(self) -> None:
        payload = work_item_identity_payload(
            projection_id="proj-payload",
            turn_type="Deliver",
            source={
                WORK_ITEM_PROJECTION_ID_KEY: "source-projection",
                WORK_ITEM_TURN_TYPE_KEY: "review",
            },
        )

        self.assertEqual(payload, {
            WORK_ITEM_PROJECTION_ID_KEY: "proj-payload",
            WORK_ITEM_TURN_TYPE_KEY: "deliver",
        })

    def test_work_item_identity_payload_uses_explicit_fallbacks_without_identity(self) -> None:
        payload = work_item_identity_payload_from_metadata(
            {
                "unrelated": "value",
            },
            projection_id_fallback="fallback-projection",
            turn_type_fallback="execute",
        )

        self.assertEqual(payload[WORK_ITEM_PROJECTION_ID_KEY], "fallback-projection")
        self.assertEqual(payload[WORK_ITEM_TURN_TYPE_KEY], "execute")

    def test_work_item_identity_payload_for_task_reads_projection_helpers(self) -> None:
        task = SimpleNamespace(
            id="task-1",
            metadata={
                WORK_ITEM_PROJECTION_ID_KEY: "task-proj",
                WORK_ITEM_TURN_TYPE_KEY: "report",
            },
        )

        payload = work_item_identity_payload_for_task(task)

        self.assertEqual(payload[WORK_ITEM_PROJECTION_ID_KEY], "task-proj")
        self.assertEqual(payload[WORK_ITEM_TURN_TYPE_KEY], "report")

    def test_migrate_projection_metadata_backfills_from_fallbacks_without_overwriting(self) -> None:
        migrated, changed = migrate_work_item_projection_metadata(
            {},
            projection_id_fallback="fallback-projection",
            turn_type_fallback="execute",
        )

        self.assertTrue(changed)
        self.assertEqual(migrated[WORK_ITEM_PROJECTION_ID_KEY], "fallback-projection")
        self.assertEqual(migrated[WORK_ITEM_TURN_TYPE_KEY], "execute")

        migrated_again, changed_again = migrate_work_item_projection_metadata(
            {
                WORK_ITEM_PROJECTION_ID_KEY: "new-projection",
                WORK_ITEM_TURN_TYPE_KEY: "review",
            }
        )
        self.assertFalse(changed_again)
        self.assertEqual(migrated_again[WORK_ITEM_PROJECTION_ID_KEY], "new-projection")
        self.assertEqual(migrated_again[WORK_ITEM_TURN_TYPE_KEY], "review")

    def test_projection_id_for_work_item_falls_back_to_projection_id(self) -> None:
        item = SimpleNamespace(metadata={}, projection_id="projection-column-value", work_item_id="wi-1", kind="execute")

        self.assertEqual(projection_id_for_work_item(item), "projection-column-value")

    def test_rework_projection_id_for_gate_prefers_new_metadata(self) -> None:
        gate = WorkItemGatePolicy(
            gate_type="review",
            rework_projection_id="field-projection",
            metadata={GATE_REWORK_PROJECTION_ID_KEY: "new-projection"},
        )

        self.assertEqual(rework_projection_id_for_gate(gate), "new-projection")
        self.assertEqual(
            rework_projection_id_for_gate(WorkItemGatePolicy(gate_type="review", rework_projection_id="field-projection")),
            "field-projection",
        )

    def test_mark_gate_rework_projection_syncs_new_metadata_and_field(self) -> None:
        gate = WorkItemGatePolicy(gate_type="review")

        marked = mark_gate_rework_projection(gate, "projection-target")

        self.assertIs(marked, gate)
        self.assertEqual(gate.metadata[GATE_REWORK_PROJECTION_ID_KEY], "projection-target")
        self.assertEqual(gate.rework_projection_id, "projection-target")

    def test_target_projection_helpers_read_projection_fields_only(self) -> None:
        decision = SimpleNamespace(
            target_projection_id="new-target",
            target_projection_ids=["new-target", "second-target"],
        )

        self.assertEqual(target_projection_id_for_decision(decision), "new-target")
        self.assertEqual(target_projection_ids_for_decision(decision), ["new-target", "second-target"])
        self.assertEqual(target_projection_id_for_decision(SimpleNamespace()), "")
        self.assertEqual(target_projection_ids_for_decision(SimpleNamespace()), [])

    def test_gate_rework_payload_defaults_to_projection_fields(self) -> None:
        payload = gate_rework_payload(
            review_projection_id="review-proj",
            target_projection_id="target-proj",
            rework_projection_id="rework-proj",
        )

        self.assertEqual(payload["review_projection_id"], "review-proj")
        self.assertEqual(payload[GATE_TARGET_PROJECTION_ID_KEY], "target-proj")
        self.assertEqual(payload[GATE_REWORK_PROJECTION_ID_KEY], "rework-proj")
        self.assertEqual(
            set(payload),
            {"review_projection_id", GATE_TARGET_PROJECTION_ID_KEY, GATE_REWORK_PROJECTION_ID_KEY},
        )

    def test_delegation_work_item_projection_field_is_canonical(self) -> None:
        item = DelegationWorkItem(
            work_item_id="wi-1",
            projection_id="column-projection",
            metadata={WORK_ITEM_PROJECTION_ID_KEY: "metadata-projection"},
        )

        self.assertEqual(item.projection_id, "column-projection")
        self.assertEqual(projection_id_for_work_item(item), "column-projection")

    def test_delegation_work_item_projection_field_no_longer_backfills_metadata(self) -> None:
        item = DelegationWorkItem(work_item_id="wi-1", projection_id="column-projection", metadata={})

        self.assertEqual(item.projection_id, "column-projection")
        item.projection_id = "new-projection"
        self.assertEqual(item.projection_id, "new-projection")
        self.assertNotIn(WORK_ITEM_PROJECTION_ID_KEY, item.metadata)

    def test_company_gate_metadata_prefers_rework_projection_id(self) -> None:
        executor = CompanyWorkItemExecutor.__new__(CompanyWorkItemExecutor)

        gate = executor._gate_from_metadata({
            "type": "review",
            "rework_projection_id": "projection-target",
            "metadata": {},
        })

        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(rework_projection_id_for_gate(gate), "projection-target")
        self.assertEqual(gate.metadata[GATE_REWORK_PROJECTION_ID_KEY], "projection-target")


class ActorRuntimeCompanyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_item_runtime_bootstrap_collapses_role_sessions_across_seats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OPCConfig()
            config.org.company_profile = "custom"
            config.org.final_decider_role_id = "ceo"
            config.org.roles = [
                RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
                RoleConfig(id="cto", name="CTO", responsibility="Lead engineering.", reports_to="ceo"),
                RoleConfig(id="engineer", name="Engineer", responsibility="Implement the work.", reports_to="cto"),
            ]
            config.org.teams = [
                TeamConfig(
                    team_id="team::ceo",
                    seats=[
                        SeatConfig(seat_id="seat::team::ceo::ceo", role_id="ceo", seat_kind="lead"),
                        SeatConfig(seat_id="seat::team::ceo::cto", role_id="cto"),
                    ],
                ),
                TeamConfig(
                    team_id="team::cto",
                    metadata={"parent_team_id": "team::ceo"},
                    seats=[
                        SeatConfig(
                            seat_id="seat::team::cto::cto",
                            role_id="cto",
                            seat_kind="lead",
                            manager_role_id="ceo",
                            manager_seat_id="seat::team::ceo::cto",
                        ),
                        SeatConfig(seat_id="seat::team::cto::engineer", role_id="engineer"),
                    ],
                ),
            ]

            org_engine = OrgEngine(config, Path(tmpdir))
            topology = org_engine.build_runtime_delegation_topology()
            runtime = CompanyRuntime(org_engine=org_engine, communication=None, store=None)
            root_task = Task(
                id="root-task",
                title="Root intake",
                project_id="proj1",
                assigned_to="ceo",
                status=TaskStatus.PENDING,
                metadata={
                    "work_item_runtime": True,
                    "delegation_run_id": "run-1",
                    "runtime_topology": topology,
                    "delegation_seat_id": "seat::team::ceo::ceo",
                },
            )

            await runtime.bootstrap([root_task])

            # Role-instance model: CTO's two seats (subordinate-to-CEO
            # and leader-of-CTO-team) share ONE session. The role_session
            # lists both seats for org lookups.
            cto_sessions = [
                session
                for session in runtime.member_sessions.values()
                if session.role_id == "cto"
            ]
            self.assertEqual(len(cto_sessions), 1)
            self.assertEqual(
                cto_sessions[0].role_session_id,
                "role-runtime::run-1::cto",
            )
            cto_role_session = runtime.role_sessions["role-runtime::run-1::cto"]
            self.assertEqual(
                sorted(cto_role_session.seat_ids),
                [
                    "seat::team::ceo::cto",
                    "seat::team::cto::cto",
                ],
            )

    async def test_work_item_runtime_bootstrap_recovers_direct_reports_from_persisted_seat_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = OPCConfig()
            config.org.company_profile = "custom"
            config.org.final_decider_role_id = "ceo"
            config.org.roles = [
                RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
                RoleConfig(id="cto", name="CTO", responsibility="Lead engineering.", reports_to="ceo"),
            ]

            org_engine = OrgEngine(config, root)
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            try:
                runtime = CompanyRuntime(org_engine=org_engine, communication=None, store=store)
                await store.save_delegation_seat_state(
                    SeatState(
                        seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                        team_instance_id="team-instance::run-1::team::ceo",
                        run_id="run-1",
                        project_id="proj1",
                        team_id="team::ceo",
                        seat_id="seat::team::ceo::ceo",
                        role_id="ceo",
                        metadata={
                            "managed_team_id": "team::ceo",
                            "allowed_delegate_role_ids": ["cto"],
                        },
                    )
                )
                await store.save_delegation_seat_state(
                    SeatState(
                        seat_state_id="seat-state::run-1::seat::team::ceo::cto",
                        team_instance_id="team-instance::run-1::team::ceo",
                        run_id="run-1",
                        project_id="proj1",
                        team_id="team::ceo",
                        seat_id="seat::team::ceo::cto",
                        role_id="cto",
                        manager_role_id="ceo",
                        manager_seat_id="seat::team::ceo::ceo",
                    )
                )
                root_task = Task(
                    id="root-task",
                    title="CEO Intake",
                    project_id="proj1",
                    assigned_to="ceo",
                    status=TaskStatus.PENDING,
                    metadata={
                        "execution_mode": "company_mode",
                        "runtime_model": "multi_team_org",
                        "work_item_runtime": True,
                        "delegation_run_id": "run-1",
                        "delegation_seat_id": "seat::team::ceo::ceo",
                        "runtime_topology": {"seats": []},
                    },
                )
                set_linked_work_item_id(root_task, "ceo-work-item")

                await runtime.bootstrap([root_task])

                # Fix 5 PR4: member_session key is role-scoped —
                # ``(project, scope, role, employee)``. No team_instance
                # slot; same role = one session across every team context.
                session = runtime.member_sessions[
                    "role-session::proj1::ceo::ceo-default-session"
                ]
                self.assertEqual(session.metadata["direct_report_role_ids"], ["cto"])
                self.assertEqual(session.metadata["direct_report_seat_ids"], ["seat::team::ceo::cto"])
                runtime.prepare_task_for_session(session, root_task)
                self.assertEqual(root_task.metadata["current_turn_mode"], "dispatch_required")
            finally:
                await store.close()


class ActorRuntimeAttentionWorkItemTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbox_attention_upserts_work_item_instead_of_synthetic_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()

            config = OPCConfig()
            config.org.company_profile = "custom"
            config.org.final_decider_role_id = "ceo"
            config.org.roles = [
                RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
            ]
            org_engine = OrgEngine(config, root)
            communication = CommunicationManager(store, EventBus(), llm=None, org_engine=org_engine)
            executor = CompanyWorkItemExecutor(
                org_engine=org_engine,
                communication=communication,
                approval_engine=SimpleNamespace(),
                memory=None,
                execute_task=AsyncMock(),
                save_task=store.save_task,
                store=store,
                llm=None,
            )

            topology = org_engine.build_runtime_delegation_topology()
            root_task = Task(
                id="root-task",
                title="CEO Intake",
                project_id="proj1",
                session_id="sess-root",
                parent_session_id="sess-root",
                assigned_to="ceo",
                status=TaskStatus.PENDING,
                metadata={
                    "mode": "company",
                    "execution_mode": "company_mode",
                    "execution_model": "multi_team_org",
                    "runtime_model": "multi_team_org",
                    "work_item_runtime": True,
                    "delegation_run_id": "run-1",
                    "runtime_topology": topology,
                    "delegation_seat_id": "seat::team::ceo::ceo",
                    "delegation_team_id": "team::ceo",
                    "delegation_role_session_id": "role-runtime::run-1::seat::team::ceo::ceo",
                    "original_message": "Build the feature",
                },
            )
            set_linked_work_item_id(root_task, "root-work-item")
            await store.save_task(root_task)
            await store.save_delegation_work_item(
                DelegationWorkItem(
                    work_item_id="root-work-item",
                    run_id="run-1",
                    cell_id="team::ceo",
                    team_instance_id="team-instance::run-1::team::ceo",
                    team_id="team::ceo",
                    role_id="ceo",
                    seat_id="seat::team::ceo::ceo",
                    seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                    role_runtime_session_id="role-runtime::run-1::seat::team::ceo::ceo",
                    title="CEO Intake",
                    summary="Build the feature",
                    kind="intake",
                    projection_id="root-work-item",
                    phase=Phase.APPROVED,
                    metadata={"work_item_runtime": True, "runtime_model": "multi_team_org"},
                )
            )
            await store.link_work_item_runtime_task("root-work-item", root_task.id)

            session = CompanyMemberSession(
                member_session_id="seat-session::proj1::seat::team::ceo::ceo",
                role_session_id="role-runtime::run-1::seat::team::ceo::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="ceo",
                seat_id="seat::team::ceo::ceo",
                seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                employee_id="ceo-default-session",
                status="idle",
                resident_status="idle",
                current_turn_mode="dispatch_required",
                actionable_chat=[
                    {
                        "msg_id": "msg-1",
                        "from_agent": "owner",
                        "subject": "Please continue",
                        "body": "Keep the team moving.",
                        "message_class": "chat",
                        "actionable": True,
                    }
                ],
                inbox_state={"current_turn_mode": "dispatch_required"},
                metadata={
                    "team_id": "team::ceo",
                    "seat_id": "seat::team::ceo::ceo",
                    "manager_seat_id": "",
                    "managed_team_id": "",
                    "contact_role_ids": [],
                    "allowed_delegate_role_ids": [],
                },
            )
            executor.runtime.member_sessions[session.member_session_id] = session

            tasks, work_items = await executor._queue_multi_team_response_tasks(
                [root_task],
                await store.list_delegation_work_items("run-1"),
            )

            attention_items = [
                item for item in work_items
                if bool((item.metadata or {}).get("attention_work_item", False))
            ]
            self.assertEqual(len(attention_items), 1)
            self.assertEqual(attention_items[0].kind, "dispatch")
            self.assertEqual(attention_items[0].phase, Phase.READY)
            self.assertEqual(attention_items[0].seat_id, "seat::team::ceo::ceo")
            self.assertTrue(
                all(not bool((task.metadata or {}).get("synthetic_inbox_turn", False)) for task in tasks)
            )
            projected = next(
                task for task in tasks
                if linked_work_item_id_for_task(task) == attention_items[0].work_item_id
            )
            self.assertEqual(projected.status, TaskStatus.PENDING)

    async def test_delivery_attention_not_created_before_dependencies_are_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = OPCStore(root / "tasks.db")
            await store.initialize()

            config = OPCConfig()
            config.org.company_profile = "custom"
            config.org.final_decider_role_id = "ceo"
            config.org.roles = [
                RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
            ]
            org_engine = OrgEngine(config, root)
            communication = CommunicationManager(store, EventBus(), llm=None, org_engine=org_engine)
            executor = CompanyWorkItemExecutor(
                org_engine=org_engine,
                communication=communication,
                approval_engine=SimpleNamespace(),
                memory=None,
                execute_task=AsyncMock(),
                save_task=store.save_task,
                store=store,
                llm=None,
            )

            root_task = Task(
                id="root-task",
                title="CEO Intake",
                project_id="proj1",
                session_id="sess-root",
                parent_session_id="sess-root",
                assigned_to="ceo",
                status=TaskStatus.BLOCKED,
                metadata={
                    "mode": "company",
                    "execution_mode": "company_mode",
                    "execution_model": "multi_team_org",
                    "runtime_model": "multi_team_org",
                    "work_item_runtime": True,
                    "delegation_run_id": "run-1",
                    "delegation_seat_id": "seat::team::ceo::ceo",
                    "delegation_team_id": "team::ceo",
                    "delegation_role_session_id": "role-runtime::run-1::seat::team::ceo::ceo",
                },
            )
            set_linked_work_item_id(root_task, "root-work-item")
            await store.save_task(root_task)
            root_item = DelegationWorkItem(
                work_item_id="root-work-item",
                run_id="run-1",
                cell_id="team::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="ceo",
                seat_id="seat::team::ceo::ceo",
                seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                role_runtime_session_id="role-runtime::run-1::seat::team::ceo::ceo",
                title="CEO Intake",
                summary="Waiting for child work.",
                kind="intake",
                projection_id="root-work-item",
                phase=Phase.WAITING_FOR_CHILDREN,
                metadata={
                    "work_item_runtime": True,
                    "runtime_model": "multi_team_org",
                    "dependency_work_item_ids": ["child-work-item"],
                },
            )
            child_item = DelegationWorkItem(
                work_item_id="child-work-item",
                run_id="run-1",
                cell_id="team::cto",
                team_instance_id="team-instance::run-1::team::cto",
                team_id="team::cto",
                role_id="cto",
                seat_id="seat::team::ceo::cto",
                parent_work_item_id="root-work-item",
                title="CTO child",
                summary="Still running.",
                kind="execute",
                projection_id="child-work-item",
                phase=Phase.RUNNING,
                manager_role_id="ceo",
                manager_seat_id="seat::team::ceo::ceo",
                metadata={"work_item_runtime": True, "runtime_model": "multi_team_org"},
            )
            await store.save_delegation_work_item(root_item)
            await store.save_delegation_work_item(child_item)

            session = CompanyMemberSession(
                member_session_id="seat-session::proj1::seat::team::ceo::ceo",
                role_session_id="role-runtime::run-1::seat::team::ceo::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="ceo",
                seat_id="seat::team::ceo::ceo",
                seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                employee_id="ceo-default-session",
                status="blocked",
                resident_status="blocked",
                focused_work_item_id="root-work-item",
                current_turn_mode="deliver_required",
                inbox_state={"current_turn_mode": "deliver_required"},
                current_work_item={"work_item_id": "root-work-item"},
                metadata={
                    "team_id": "team::ceo",
                    "seat_id": "seat::team::ceo::ceo",
                    "manager_seat_id": "",
                    "managed_team_id": "team::ceo",
                    "contact_role_ids": ["cto"],
                    "allowed_delegate_role_ids": ["cto"],
                },
            )

            _tasks, work_items = await executor._upsert_attention_work_item(
                root_task=root_task,
                tasks=[root_task],
                work_items=[root_item, child_item],
                session=session,
                source_message={
                    "msg_id": "msg-blocked",
                    "from_agent": "cto",
                    "subject": "Blocked child",
                    "body": "Child work is still running.",
                },
            )

            self.assertFalse(
                any(bool((item.metadata or {}).get("attention_work_item", False)) for item in work_items)
            )
            persisted = await store.list_delegation_work_items("run-1")
            self.assertFalse(
                any(bool((item.metadata or {}).get("attention_work_item", False)) for item in persisted)
            )
            await store.close()


class ActorRuntimeManagerDispatchGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.store = OPCStore(self.root / "tasks.db")
        await self.store.initialize()
        self.config = OPCConfig()
        self.config.org.company_profile = "custom"
        self.config.org.final_decider_role_id = "ceo"
        self.config.org.roles = [
            RoleConfig(id="ceo", name="CEO", responsibility="Set direction.", reports_to="owner"),
            RoleConfig(id="cto", name="CTO", responsibility="Lead engineering.", reports_to="ceo"),
        ]
        self.org_engine = OrgEngine(self.config, self.root)
        self.communication = CommunicationManager(self.store, EventBus(), llm=None, org_engine=self.org_engine)
        self.executor = CompanyWorkItemExecutor(
            org_engine=self.org_engine,
            communication=self.communication,
            approval_engine=SimpleNamespace(),
            memory=None,
            execute_task=AsyncMock(),
            save_task=self.store.save_task,
            store=self.store,
            llm=None,
        )
        self.task = Task(
            id="ceo-dispatch-task",
            title="CEO Dispatch",
            project_id="proj1",
            assigned_to="ceo",
            status=TaskStatus.PENDING,
            metadata={
                "execution_mode": "company_mode",
                "runtime_model": "multi_team_org",
                "delegation_run_id": "run-1",
                "delegation_seat_id": "seat::team::ceo::ceo",
                "current_turn_mode": "dispatch_required",
                "direct_report_role_ids": ["cto"],
                "direct_report_seat_ids": ["seat::team::ceo::cto"],
            },
        )
        set_linked_work_item_id(self.task, "ceo-work-item")
        await self.store.save_delegation_work_item(
            DelegationWorkItem(
                work_item_id="ceo-work-item",
                run_id="run-1",
                cell_id="team::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="ceo",
                seat_id="seat::team::ceo::ceo",
                seat_state_id="seat-state::run-1::seat::team::ceo::ceo",
                role_runtime_session_id="role-runtime::run-1::seat::team::ceo::ceo",
                title="CEO Dispatch",
                summary="Route the work.",
                kind="dispatch",
                projection_id="ceo-work-item",
                phase=Phase.READY,
                metadata={"work_item_runtime": True, "runtime_model": "multi_team_org"},
            )
        )

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.tmpdir.cleanup()

    async def test_manager_dispatch_guard_requires_child_work_or_justification(self) -> None:
        before = await self.executor._snapshot_manager_dispatch_state(self.task)

        issues = await self.executor._enforce_manager_dispatch_guard(
            self.task,
            TaskResult(status=TaskStatus.DONE, content="I handled the work locally."),
            before_state=before,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("NO_DELEGATION_JUSTIFICATION", issues[0])

    async def test_manager_dispatch_guard_accepts_explicit_no_delegation_justification(self) -> None:
        before = await self.executor._snapshot_manager_dispatch_state(self.task)

        issues = await self.executor._enforce_manager_dispatch_guard(
            self.task,
            TaskResult(
                status=TaskStatus.DONE,
                content="NO_DELEGATION_JUSTIFICATION: This turn is a root-only scoping decision before any downstream split.",
            ),
            before_state=before,
        )

        self.assertEqual(issues, [])
        self.assertEqual(
            self.task.metadata["manager_no_delegation_justification"],
            "This turn is a root-only scoping decision before any downstream split.",
        )

    async def test_manager_dispatch_guard_rejects_no_delegation_for_collab_infra_failure(self) -> None:
        before = await self.executor._snapshot_manager_dispatch_state(self.task)

        issues = await self.executor._enforce_manager_dispatch_guard(
            self.task,
            TaskResult(
                status=TaskStatus.DONE,
                content=(
                    "NO_DELEGATION_JUSTIFICATION: manager_board_read failed with "
                    "collaboration broker RPC failed: disk I/O error"
                ),
                artifacts={
                    "collaboration_infrastructure_failure": {
                        "error_type": "infrastructure",
                        "retryable": True,
                        "tool_name": "manager_board_read",
                    }
                },
            ),
            before_state=before,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("collaboration infrastructure failure", issues[0])
        self.assertNotIn("manager_no_delegation_justification", self.task.metadata)

    async def test_manager_dispatch_guard_accepts_new_child_work_item(self) -> None:
        before = await self.executor._snapshot_manager_dispatch_state(self.task)
        await self.store.save_delegation_work_item(
            DelegationWorkItem(
                work_item_id="cto-child-item",
                run_id="run-1",
                cell_id="team::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="cto",
                seat_id="seat::team::ceo::cto",
                seat_state_id="seat-state::run-1::seat::team::ceo::cto",
                role_runtime_session_id="role-runtime::run-1::seat::team::ceo::cto",
                parent_work_item_id="ceo-work-item",
                title="CTO Work",
                summary="Investigate architecture options.",
                kind="execute",
                projection_id="cto-child-item",
                phase=Phase.READY,
                manager_role_id="ceo",
                manager_seat_id="seat::team::ceo::ceo",
                metadata={"work_item_runtime": True, "runtime_model": "multi_team_org"},
            )
        )

        issues = await self.executor._enforce_manager_dispatch_guard(
            self.task,
            TaskResult(status=TaskStatus.DONE, content="Delegated downstream."),
            before_state=before,
        )

        self.assertEqual(issues, [])

    async def test_manager_dispatch_guard_accepts_existing_child_mutation(self) -> None:
        await self.store.save_delegation_work_item(
            DelegationWorkItem(
                work_item_id="cto-child-item",
                run_id="run-1",
                cell_id="team::ceo",
                team_instance_id="team-instance::run-1::team::ceo",
                team_id="team::ceo",
                role_id="cto",
                seat_id="seat::team::ceo::cto",
                seat_state_id="seat-state::run-1::seat::team::ceo::cto",
                role_runtime_session_id="role-runtime::run-1::seat::team::ceo::cto",
                parent_work_item_id="ceo-work-item",
                title="Old CTO Work",
                summary="Build the obsolete version.",
                kind="execute",
                projection_id="cto-child-item",
                phase=Phase.RUNNING,
                manager_role_id="ceo",
                manager_seat_id="seat::team::ceo::ceo",
                metadata={
                    "work_item_runtime": True,
                    "runtime_model": "multi_team_org",
                    "manager_mutation_revision": 0,
                },
            )
        )
        before = await self.executor._snapshot_manager_dispatch_state(self.task)
        await self.store.amend_delegation_work_item(
            "cto-child-item",
            title="Revised CTO Work",
            metadata_set={
                "manager_mutation_revision": 1,
                "manager_mutation_action": "modify",
            },
        )

        issues = await self.executor._enforce_manager_dispatch_guard(
            self.task,
            TaskResult(status=TaskStatus.DONE, content="Revised the existing child work item."),
            before_state=before,
        )

        self.assertEqual(issues, [])


class CompanyModeParallelIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_multi_team_org_isolates_claimed_work_item_exception(self) -> None:
        executor = CompanyWorkItemExecutor(
            org_engine=SimpleNamespace(),
            communication=SimpleNamespace(),
            approval_engine=SimpleNamespace(),
            memory=None,
            execute_task=AsyncMock(),
            save_task=AsyncMock(),
            store=None,
            llm=None,
        )
        executor.on_kanban_changed = AsyncMock()

        task_a = Task(
            id="task-a",
            title="COO dispatch",
            project_id="proj1",
            assigned_to="coo",
            status=TaskStatus.PENDING,
            metadata={
                "runtime_model": "multi_team_org",
                "work_item_projection_id": "coo::execute::dispatch",
            },
        )
        task_b = Task(
            id="task-b",
            title="CTO dispatch",
            project_id="proj1",
            assigned_to="cto",
            status=TaskStatus.PENDING,
            metadata={
                "runtime_model": "multi_team_org",
                "work_item_projection_id": "cto::execute::dispatch",
            },
        )
        set_linked_work_item_id(task_a, "wi-a")
        set_linked_work_item_id(task_b, "wi-b")
        work_items = [
            DelegationWorkItem(
                work_item_id="wi-a",
                run_id="run-1",
                cell_id="team::ceo",
                team_id="team::ceo",
                role_id="coo",
                seat_id="seat::team::ceo::coo",
                title="COO dispatch",
                kind="dispatch",
                projection_id="coo::execute::dispatch",
                phase=Phase.READY,
                metadata={"runtime_model": "multi_team_org"},
            ),
            DelegationWorkItem(
                work_item_id="wi-b",
                run_id="run-1",
                cell_id="team::ceo",
                team_id="team::ceo",
                role_id="cto",
                seat_id="seat::team::ceo::cto",
                title="CTO dispatch",
                kind="dispatch",
                projection_id="cto::execute::dispatch",
                phase=Phase.READY,
                metadata={"runtime_model": "multi_team_org"},
            ),
        ]
        session_a = CompanyMemberSession(
            member_session_id="session-a",
            role_id="coo",
            seat_id="seat::team::ceo::coo",
            status="idle",
            resident_status="idle",
            metadata={"seat_id": "seat::team::ceo::coo"},
        )
        session_b = CompanyMemberSession(
            member_session_id="session-b",
            role_id="cto",
            seat_id="seat::team::ceo::cto",
            status="idle",
            resident_status="idle",
            metadata={"seat_id": "seat::team::ceo::cto"},
        )
        executor.runtime.member_sessions = {
            session_a.member_session_id: session_a,
            session_b.member_session_id: session_b,
        }
        executor.runtime.bootstrap = AsyncMock()
        executor.runtime.refresh_inbox_state = AsyncMock()
        executor.runtime.enqueue_runnable_work_items = lambda *args, **kwargs: None
        executor.runtime.enqueue_runnable_tasks = lambda *args, **kwargs: None
        executor._load_delegation_work_items = AsyncMock(return_value=work_items)
        executor._refresh_ready_work_items = AsyncMock(side_effect=lambda items, tasks=None: items)
        executor._materialize_work_item_tasks = AsyncMock(side_effect=lambda tasks, work_items: tasks)
        executor._queue_multi_team_response_tasks = AsyncMock(side_effect=lambda tasks, work_items: (tasks, work_items))
        executor._sync_task_projection_from_work_items = lambda tasks, work_items: None
        executor._work_item_is_runnable = lambda item, work_item_by_id, task_by_work_item_id: True
        executor._summarize_multi_team_org_results = lambda tasks: "isolated"
        claimed_once = False

        async def fake_claim_runnable_tasks(tasks, work_items=None):
            nonlocal claimed_once
            _ = (tasks, work_items)
            if claimed_once:
                return []
            claimed_once = True
            executor.runtime._claimed_task_ids = {task_a.id, task_b.id}
            executor.runtime._claimed_work_item_ids = {"wi-a", "wi-b"}
            session_a.status = session_a.resident_status = "running"
            session_a.current_task_id = task_a.id
            session_a.focused_work_item_id = "wi-a"
            session_b.status = session_b.resident_status = "running"
            session_b.current_task_id = task_b.id
            session_b.focused_work_item_id = "wi-b"
            return [(session_a, task_a), (session_b, task_b)]

        async def fake_complete_claim(session, task, result=None):
            _ = result
            executor.runtime._claimed_task_ids.discard(task.id)
            executor.runtime._claimed_work_item_ids.discard(
                linked_work_item_id_for_task(task)
            )
            session.status = session.resident_status = "idle"
            session.current_task_id = ""
            session.focused_work_item_id = ""

        async def fake_run_claimed_work_item(member_session, task, task_by_projection_id):
            _ = (member_session, task_by_projection_id)
            if task.id == "task-a":
                raise RuntimeError("sync blew up")
            task.status = TaskStatus.DONE
            result = TaskResult(status=TaskStatus.DONE, content="done")
            await fake_complete_claim(session_b, task, result=result)
            return result

        executor.runtime.claim_runnable_tasks = AsyncMock(side_effect=fake_claim_runnable_tasks)
        executor.runtime.complete_claim = AsyncMock(side_effect=fake_complete_claim)
        executor._run_claimed_work_item = AsyncMock(side_effect=fake_run_claimed_work_item)

        result = await executor.execute(SimpleNamespace(metadata={}), [task_a, task_b])

        self.assertEqual(result, "isolated")
        self.assertEqual(task_a.status, TaskStatus.FAILED)
        self.assertIn("RuntimeError", str((task_a.result or {}).get("content", "")))
        self.assertEqual(session_a.status, "idle")
        self.assertEqual(session_a.current_task_id, "")
        self.assertEqual(task_b.status, TaskStatus.DONE)
        self.assertEqual(session_b.status, "idle")
