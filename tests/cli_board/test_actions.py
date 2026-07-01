from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from opc.core.models import ExecutionCheckpoint, Task, TaskStatus
from opc.plugins.cli_board.services.actions import BoardActions


class _StubStore:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.checkpoints: list[ExecutionCheckpoint] = []
        self.resolved: list[tuple[str, str]] = []

    async def save_task(self, task: Task) -> None:
        self.tasks[task.id] = task

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def get_tasks(self, **_kw):
        return list(self.tasks.values())

    async def get_pending_checkpoints(self, **_kw):
        return [checkpoint for checkpoint in self.checkpoints if checkpoint.status == "pending"]

    async def resolve_execution_checkpoint(self, checkpoint_id: str, status: str = "resolved") -> None:
        self.resolved.append((checkpoint_id, status))
        for checkpoint in self.checkpoints:
            if checkpoint.checkpoint_id == checkpoint_id:
                checkpoint.status = status


class _StubMemory:
    def __init__(self) -> None:
        self.ensure_session = AsyncMock()


class _StubEngine:
    def __init__(self) -> None:
        self.store = _StubStore()
        self.memory = _StubMemory()
        self.process_message = AsyncMock(return_value="ok")


class _StubFacade:
    def __init__(self, engine: _StubEngine) -> None:
        self.project_id = "demo"
        self._engine = engine

    async def ensure_ready(self):
        return self._engine


class BoardActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_task_creates_session_backed_placeholder(self) -> None:
        engine = _StubEngine()
        actions = BoardActions(_StubFacade(engine), project_id="demo")

        task = await actions.create_task(title="Draft feature", description="Initial plan")

        self.assertIn(task.id, engine.store.tasks)
        engine.memory.ensure_session.assert_awaited()
        self.assertEqual(engine.store.tasks[task.id].title, "Draft feature")
        self.assertEqual(engine.store.tasks[task.id].metadata["source"], "cli_board")

    async def test_send_session_message_routes_through_origin_task(self) -> None:
        engine = _StubEngine()
        task = Task(
            id="task-1",
            title="Feature task",
            description="Implement the feature",
            status=TaskStatus.PENDING,
            session_id="session-1",
            project_id="demo",
        )
        await engine.store.save_task(task)
        actions = BoardActions(_StubFacade(engine), project_id="demo")

        response = await actions.send_session_message("task-1", "please continue")

        self.assertEqual(response, "ok")
        engine.process_message.assert_awaited_once()
        kwargs = engine.process_message.await_args.kwargs
        self.assertEqual(kwargs["session_id"], "session-1")
        self.assertEqual(kwargs["origin_task_id"], "task-1")
        self.assertEqual(engine.store.tasks["task-1"].status, TaskStatus.IDLE)

    async def test_cancel_task_marks_related_tasks_and_checkpoints_cancelled(self) -> None:
        engine = _StubEngine()
        root = Task(
            id="root",
            title="Root task",
            description="Run runtime",
            status=TaskStatus.RUNNING,
            session_id="session-root",
            project_id="demo",
        )
        linked = Task(
            id="child",
            title="Child task",
            description="Background child",
            status=TaskStatus.RUNNING,
            session_id="session-child",
            project_id="demo",
            metadata={"origin_task_id": "root"},
        )
        await engine.store.save_task(root)
        await engine.store.save_task(linked)
        engine.store.checkpoints.append(
            ExecutionCheckpoint(
                checkpoint_id="cp-root",
                project_id="demo",
                session_id="session-root",
                task_id="root",
            )
        )
        actions = BoardActions(_StubFacade(engine), project_id="demo")

        await actions.cancel_task("root")

        self.assertEqual(engine.store.tasks["root"].status, TaskStatus.CANCELLED)
        self.assertEqual(engine.store.tasks["child"].status, TaskStatus.CANCELLED)
        self.assertEqual(engine.store.resolved, [("cp-root", "cancelled")])

