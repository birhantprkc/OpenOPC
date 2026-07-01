"""CLI-board-specific company runtime recovery manager.

Independent from office_ui/recovery_manager.py — same Core Engine APIs,
different notification path (TUI event bridge instead of WebSocket broadcast).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from opc.layer2_organization.work_item_identity import work_item_projection_id_from_metadata
from opc.layer2_organization.work_item_transition import apply_task_status_transition

if TYPE_CHECKING:
    from .engine_facade import EngineFacade

logger = logging.getLogger(__name__)


@dataclass
class RecoverableWorkItem:
    projection_id: str
    title: str
    task_id: str
    status: str
    interrupted: bool
    previous_status: str = ""


@dataclass
class InterruptedCompanyRuntime:
    parent_session_id: str
    parent_task_id: str
    project_id: str
    title: str
    profile: str
    interrupted_at: str
    work_items: list[RecoverableWorkItem] = field(default_factory=list)


@dataclass
class RecoveryStatus:
    interrupted: list[InterruptedCompanyRuntime] = field(default_factory=list)
    active_recoveries: list[str] = field(default_factory=list)
    scanned_at: float = 0.0


def _is_interrupted(task: Any) -> bool:
    from opc.core.models import TaskStatus
    if task.status != TaskStatus.FAILED:
        return False
    meta = getattr(task, "metadata", {}) or {}
    if meta.get("interrupted_recovery"):
        return True
    result = getattr(task, "result", {}) or {}
    artifacts = result.get("artifacts", {}) or {}
    return bool(artifacts.get("interrupted"))


class CliRecoveryManager:
    """Scan for interrupted company runtimes and provide resume/cancel."""

    _CACHE_TTL = 10.0

    def __init__(self, facade: EngineFacade) -> None:
        self._facade = facade
        self._lock = asyncio.Lock()
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._cached: RecoveryStatus | None = None
        self._cache_until: float = 0.0

    @property
    def _project_id(self) -> str:
        return self._facade.project_id or "default"

    async def get_status(self) -> RecoveryStatus:
        now = time.time()
        if self._cached is not None and now < self._cache_until:
            self._cached.active_recoveries = list(self._active.keys())
            return self._cached
        status = await self.scan()
        self._cached = status
        self._cache_until = now + self._CACHE_TTL
        return status

    async def scan(self) -> RecoveryStatus:
        engine = await self._facade.ensure_ready()
        if not engine.store:
            return RecoveryStatus()

        try:
            all_tasks = await engine.store.get_tasks(project_id=self._project_id)
        except Exception as exc:
            logger.warning("Recovery scan failed: %s", exc)
            return RecoveryStatus()

        groups: dict[str, list[Any]] = {}
        tasks_by_session: dict[str, Any] = {}
        for task in all_tasks:
            sid = str(getattr(task, "session_id", "") or "").strip()
            if sid:
                tasks_by_session[sid] = task
            parent_sid = str(getattr(task, "parent_session_id", "") or "").strip()
            projection_id = work_item_projection_id_from_metadata(getattr(task, "metadata", {}) or {})
            if parent_sid and projection_id:
                groups.setdefault(parent_sid, []).append(task)

        from opc.core.models import TaskStatus
        interrupted: list[InterruptedCompanyRuntime] = []

        for parent_sid, tasks in groups.items():
            if not any(_is_interrupted(t) for t in tasks):
                continue
            non_terminal = [t for t in tasks if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)]
            if not non_terminal:
                continue

            parent_task = tasks_by_session.get(parent_sid)
            parent_task_id = parent_task.id if parent_task else parent_sid
            title = parent_task.title if parent_task else "Unknown company runtime"

            work_items: list[RecoverableWorkItem] = []
            earliest = ""
            for t in sorted(tasks, key=lambda x: (x.created_at, x.id)):
                meta = dict(getattr(t, "metadata", {}) or {})
                rmeta = meta.get("interrupted_recovery", {})
                is_int = _is_interrupted(t)
                if is_int and rmeta.get("detected_at", ""):
                    det = rmeta["detected_at"]
                    if not earliest or det < earliest:
                        earliest = det
                work_items.append(RecoverableWorkItem(
                    projection_id=work_item_projection_id_from_metadata(meta, fallback=t.id),
                    title=t.title,
                    task_id=t.id,
                    status=t.status.value if hasattr(t.status, "value") else str(t.status),
                    interrupted=is_int,
                    previous_status=rmeta.get("previous_status", ""),
                ))

            profile = ""
            for t in tasks:
                p = (getattr(t, "metadata", {}) or {}).get("company_profile", "")
                if p:
                    profile = p
                    break

            interrupted.append(InterruptedCompanyRuntime(
                parent_session_id=parent_sid,
                parent_task_id=parent_task_id,
                project_id=self._project_id,
                title=title,
                profile=profile,
                interrupted_at=earliest or datetime.now().isoformat(),
                work_items=work_items,
            ))

        return RecoveryStatus(
            interrupted=interrupted,
            active_recoveries=list(self._active.keys()),
            scanned_at=time.time(),
        )

    async def resume(self, parent_task_id: str) -> dict[str, Any]:
        async with self._lock:
            if parent_task_id in self._active:
                return {"ok": False, "error": "already_in_progress"}

            status = await self.scan()
            wf = next((w for w in status.interrupted if w.parent_task_id == parent_task_id), None)
            if not wf:
                return {"ok": False, "error": "not_found"}

            engine = await self._facade.ensure_ready()
            snapshot = await engine._load_company_runtime_snapshot(wf.parent_session_id)
            if not snapshot:
                return {"ok": False, "error": "snapshot_unavailable"}

            plan, tasks = snapshot

            await self._clean_checkpoints(wf, tasks)

            from opc.core.models import TaskStatus
            resumed_ids: list[str] = []
            for task in tasks:
                if task.status == TaskStatus.DONE:
                    continue
                if task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
                    task.result = None
                    task.execution_lock = False
                    task.execution_locked_at = None
                    meta = dict(task.metadata)
                    meta.pop("interrupted_recovery", None)
                    progress = list(meta.get("progress_log", []))
                    progress.append(f"[Recovery] Resumed at {datetime.now().isoformat()}")
                    meta["progress_log"] = progress[-20:]
                    task.metadata = meta
                    try:
                        await apply_task_status_transition(
                            engine.store,
                            task,
                            target_status_or_phase=TaskStatus.PENDING,
                            reason="cli_recovery_resume",
                            release_claim=True,
                        )
                    except Exception as exc:
                        logger.warning("Recovery resume skipped %s: %s", task.id, exc)
                        continue
                    if task.status != TaskStatus.PENDING:
                        logger.warning("Recovery resume preserved non-runnable phase for %s", task.id)
                        continue
                    await engine.store.save_task(task)
                    resumed_ids.append(work_item_projection_id_from_metadata(meta, fallback=task.id))

            if not resumed_ids:
                return {"ok": False, "error": "no_work_items_to_resume"}

            self._cache_until = 0.0
            bg = asyncio.create_task(self._execute(parent_task_id, plan, tasks))
            self._active[parent_task_id] = bg
            return {"ok": True, "resumed_work_item_projection_ids": resumed_ids}

    async def cancel(self, parent_task_id: str) -> dict[str, Any]:
        async with self._lock:
            bg = self._active.pop(parent_task_id, None)
            if bg and not bg.done():
                bg.cancel()

            status = await self.scan()
            wf = next((w for w in status.interrupted if w.parent_task_id == parent_task_id), None)
            if not wf:
                return {"ok": False, "error": "not_found"}

            engine = await self._facade.ensure_ready()
            snapshot = await engine._load_company_runtime_snapshot(wf.parent_session_id)
            if not snapshot:
                return {"ok": False, "error": "snapshot_unavailable"}

            _, tasks = snapshot
            from opc.core.models import TaskStatus
            cancelled = 0
            for task in tasks:
                if task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
                    try:
                        await apply_task_status_transition(
                            engine.store,
                            task,
                            target_status_or_phase=TaskStatus.CANCELLED,
                            reason="cli_recovery_cancel",
                            release_claim=True,
                        )
                    except Exception as exc:
                        logger.warning("Recovery cancel skipped %s: %s", task.id, exc)
                        continue
                    if task.status != TaskStatus.CANCELLED:
                        logger.warning("Recovery cancel preserved non-cancelled phase for %s", task.id)
                        continue
                    cancelled += 1

            await self._clean_checkpoints(wf, tasks)
            self._cache_until = 0.0
            return {"ok": True, "cancelled_count": cancelled}

    async def _execute(self, parent_task_id: str, plan: Any, tasks: list[Any]) -> None:
        try:
            engine = await self._facade.ensure_ready()
            executor = engine.company_executor
            if not executor:
                raise RuntimeError("company_executor not available")
            await executor.execute(plan, tasks)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Recovery execution failed for %s: %s", parent_task_id, exc)
        finally:
            self._active.pop(parent_task_id, None)
            self._cache_until = 0.0

    async def _clean_checkpoints(self, wf: InterruptedCompanyRuntime, tasks: list[Any]) -> None:
        engine = await self._facade.ensure_ready()
        if not engine.store:
            return
        session_ids = {str(getattr(t, "session_id", "") or "").strip() for t in tasks}
        session_ids.add(wf.parent_session_id)
        session_ids.discard("")
        try:
            pending = await engine.store.get_pending_checkpoints(project_id=wf.project_id)
            for cp in pending:
                if str(cp.session_id or "").strip() in session_ids:
                    await engine.store.resolve_execution_checkpoint(cp.checkpoint_id, status="cancelled")
        except Exception as exc:
            logger.debug("Checkpoint cleanup error: %s", exc)
