from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opc.core.models import SessionMessageRecord, SessionRecord, Task, TaskResult, TaskStatus
from opc.engine import OPCEngine
from opc.layer5_memory.memory_manager import MemoryManager


class SharedRoleSessionIdTests(unittest.TestCase):
    def test_final_decider_reuses_root_session(self) -> None:
        self.assertEqual(
            OPCEngine._shared_company_role_session_id(
                "app14",
                "ceo",
                final_decider_role_id="ceo",
            ),
            "app14",
        )

    def test_same_role_reuses_same_session_id(self) -> None:
        first = OPCEngine._shared_company_role_session_id("app14", "cmo")
        second = OPCEngine._shared_company_role_session_id("app14", "cmo")
        self.assertEqual(first, "app14:role:cmo")
        self.assertEqual(first, second)


class _MemoryStoreStub:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.session_messages: list[SessionMessageRecord] = []

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    async def save_session(self, session: SessionRecord) -> None:
        self.sessions[session.session_id] = session

    async def save_session_link(self, _link: object) -> None:
        return None

    async def save_session_message(self, message: SessionMessageRecord) -> None:
        self.session_messages.append(message)

    async def save_session_part(self, _part: object) -> None:
        return None


class MemoryManagerCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_session_message_does_not_trigger_history_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryManager(Path(tmpdir), project_id="proj", store=_MemoryStoreStub())
            compactor = SimpleNamespace(maybe_compact_after_message=AsyncMock())
            memory.set_history_compactor(compactor)

            await memory.append_session_message(
                "sess-1",
                "assistant",
                text="hello",
                project_id="proj",
                metadata={"role_id": "cmo"},
            )

            compactor.maybe_compact_after_message.assert_not_awaited()


class SharedRoleSessionExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_shared_role_session_keeps_results_local(self) -> None:
        engine = OPCEngine()
        engine.store = SimpleNamespace(
            get_task=AsyncMock(return_value=None),
            save_task=AsyncMock(),
        )
        engine.memory = SimpleNamespace(
            record_assistant_turn=AsyncMock(),
            record_child_session_result=AsyncMock(),
            record_task_completion_async=AsyncMock(),
        )
        engine._active_task_runs = set()
        engine._run_task_once = AsyncMock(
            return_value=TaskResult(status=TaskStatus.DONE, content="done", artifacts={})
        )
        engine._apply_runtime_state_to_task = lambda task, result: None

        task = Task(
            id="task-cmo-review",
            title="Review Turn: cmo",
            assigned_to="cmo",
            status=TaskStatus.PENDING,
            project_id="new16",
            session_id="app14:role:cmo",
            parent_session_id="app14",
            metadata={
                "shared_role_session": True,
                "execution_mode": "company_mode",
                "work_item_projection_id": "review::demo",
                "employee_assignment": {"employee_id": "emp-cmo", "role_id": "cmo"},
            },
        )

        await engine._execute_task(task)

        engine.memory.record_assistant_turn.assert_awaited_once()
        engine.memory.record_child_session_result.assert_not_awaited()

