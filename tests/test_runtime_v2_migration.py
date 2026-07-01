from __future__ import annotations

import contextlib
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

from opc.core.models import ExecutionCheckpoint, SessionCompactionRecord, Task, TaskStatus
from opc.database.store import OPCStore
from opc.engine import OPCEngine


@contextlib.contextmanager
def _workspace_tempdir() -> Path:
    base = Path.cwd() / ".tmp-test" / f"runtime-migration-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class RuntimeV2MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_lookup_migrates_legacy_payload_to_runtime_v2(self) -> None:
        with _workspace_tempdir() as tmpdir:
            store = OPCStore(tmpdir / "tasks.db")
            await store.initialize()
            task = Task(
                id="task-1",
                title="Need approval",
                session_id="sess-1",
                project_id="proj1",
                status=TaskStatus.AWAITING_REVIEW,
                metadata={},
            )
            await store.save_task(task)
            checkpoint = ExecutionCheckpoint(
                project_id="proj1",
                session_id="sess-1",
                checkpoint_type="task_user_input",
                task_id=task.id,
                payload={
                    "task_id": task.id,
                    "pause_request": {"reason": "Need confirmation"},
                    "tool_name": "shell_exec",
                },
            )
            await store.save_execution_checkpoint(checkpoint)

            engine = OPCEngine()
            engine.project_id = "proj1"
            engine.store = store

            migrated = await engine.get_latest_pending_checkpoint_for_session("sess-1")
            assert migrated is not None
            self.assertIn("runtime_v2", migrated.payload)
            runtime_state = migrated.payload["runtime_v2"]
            self.assertTrue(runtime_state["runtime_session_id"].startswith("rtmig_"))
            self.assertTrue(runtime_state["migrated_from_legacy"])
            refreshed_task = await store.get_task(task.id)
            self.assertEqual(refreshed_task.metadata["migration_status"], "runtime_v2_migrated")
            self.assertEqual(
                refreshed_task.metadata["runtime_v2"]["runtime_session_id"],
                runtime_state["runtime_session_id"],
            )
            await store.close()

    async def test_migrated_runtime_state_carries_legacy_compaction_boundary(self) -> None:
        with _workspace_tempdir() as tmpdir:
            store = OPCStore(tmpdir / "tasks.db")
            await store.initialize()
            task = Task(
                id="task-legacy",
                title="Legacy compacted session",
                session_id="sess-legacy",
                project_id="proj1",
                status=TaskStatus.AWAITING_REVIEW,
            )
            await store.save_task(task)
            await store.save_session_compaction(
                SessionCompactionRecord(
                    session_id="sess-legacy",
                    compaction_message_id="msg-compact",
                    source_boundary_message_id="msg-boundary",
                )
            )
            engine = OPCEngine()
            engine.project_id = "proj1"
            engine.store = store

            runtime_state = await engine._build_migrated_runtime_state(
                task,
                checkpoint_type="task_user_input",
                payload={},
            )

            self.assertEqual(runtime_state["compaction_boundaries"][0]["source_boundary_message_id"], "msg-boundary")
            await store.close()

    async def test_resume_task_checkpoint_restores_migrated_runtime_state(self) -> None:
        with _workspace_tempdir() as tmpdir:
            store = OPCStore(tmpdir / "tasks.db")
            await store.initialize()
            task = Task(
                id="task-2",
                title="Resume task",
                session_id="sess-2",
                project_id="proj1",
                status=TaskStatus.AWAITING_REVIEW,
                metadata={},
            )
            await store.save_task(task)
            checkpoint = ExecutionCheckpoint(
                project_id="proj1",
                session_id="sess-2",
                checkpoint_type="task_user_input",
                task_id=task.id,
                payload={
                    "task_id": task.id,
                    "task_ids": [task.id],
                    "execution_mode": "task_mode",
                    "pause_request": {"reason": "Need confirmation"},
                },
            )
            await store.save_execution_checkpoint(checkpoint)

            engine = OPCEngine()
            engine.project_id = "proj1"
            engine.store = store
            engine._execute_single_agent = AsyncMock(return_value="resumed")  # type: ignore[method-assign]

            response = await engine._resume_task_checkpoint(checkpoint, "continue")

            self.assertEqual(response, "resumed")
            resumed_task = await store.get_task(task.id)
            self.assertIn("runtime_resume", resumed_task.context_snapshot)
            self.assertTrue(
                resumed_task.context_snapshot["runtime_resume"]["runtime_session_id"].startswith("rtmig_")
            )
            await store.close()


if __name__ == "__main__":
    unittest.main()
