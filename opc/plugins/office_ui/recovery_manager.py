"""Pluggable work-item crash-recovery manager.

Detects interrupted company-mode work items after server restart and provides
deterministic Resume / Cancel operations — no LLM needed.

Hooks into existing engine capabilities without modifying core code:
- engine.store              → task queries + persistence
- engine._load_company_runtime_snapshot() → reconstruct work-item plan + tasks
- engine.company_executor.execute()        → resume execution
- ws_handler.broadcast()                   → push status to all clients
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from opc.layer2_organization.work_item_identity import work_item_projection_id_from_metadata
from opc.layer2_organization.work_item_transition import apply_task_status_transition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RecoverableWorkItem:
    work_item_projection_id: str
    title: str
    task_id: str
    status: str  # "done" | "failed" | "pending" | "blocked" | "cancelled"
    interrupted: bool  # has interrupted_recovery metadata
    previous_status: str  # what it was before reconciliation


@dataclass
class InterruptedWorkItemRun:
    parent_session_id: str
    parent_task_id: str
    project_id: str
    title: str
    profile: str
    interrupted_at: str
    work_items: list[RecoverableWorkItem] = field(default_factory=list)


@dataclass
class RecoveryStatus:
    interrupted: list[InterruptedWorkItemRun] = field(default_factory=list)
    active_recoveries: list[str] = field(default_factory=list)
    scanned_at: float = 0.0


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class RuntimeRecoveryManager:
    """Scans for interrupted work-item runs and provides deterministic recovery."""

    _CACHE_TTL = 10.0  # seconds

    def __init__(
        self,
        engine: Any,
        broadcast_fn: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._engine = engine
        self._broadcast = broadcast_fn
        self._lock = asyncio.Lock()
        self._active_recoveries: dict[str, asyncio.Task[Any]] = {}
        self._cached: RecoveryStatus | None = None
        self._cache_until: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_recovery_status(self) -> RecoveryStatus:
        """Return cached scan results, re-scanning if stale."""
        now = time.time()
        if self._cached is not None and now < self._cache_until:
            # Keep active_recoveries up to date even from cache
            self._cached.active_recoveries = list(self._active_recoveries.keys())
            return self._cached
        status = await self.scan()
        self._cached = status
        self._cache_until = now + self._CACHE_TTL
        return status

    async def scan(self) -> RecoveryStatus:
        """Scan the database for recoverable interrupted work-item runs."""
        store = self._engine.store
        if not store:
            return RecoveryStatus()

        project_id = self._engine.project_id or "default"
        try:
            all_tasks = await store.get_tasks(project_id=project_id)
        except Exception as exc:
            logger.warning(f"Recovery scan failed: {exc}")
            return RecoveryStatus()

        # Group projected work-item tasks by parent_session_id.
        groups: dict[str, list[Any]] = {}
        all_tasks_by_session: dict[str, Any] = {}
        for task in all_tasks:
            session_id = str(getattr(task, "session_id", "") or "").strip()
            if session_id:
                all_tasks_by_session[session_id] = task

            parent_sid = str(getattr(task, "parent_session_id", "") or "").strip()
            projection_id = work_item_projection_id_from_metadata(getattr(task, "metadata", {}) or {})
            if parent_sid and projection_id:
                groups.setdefault(parent_sid, []).append(task)

        interrupted: list[InterruptedWorkItemRun] = []

        for parent_sid, tasks in groups.items():
            # Check if any task has interrupted_recovery metadata
            has_interrupted = any(
                _is_interrupted(t) for t in tasks
            )
            if not has_interrupted:
                continue

            # Skip if all tasks are terminal (DONE or CANCELLED)
            from opc.core.models import TaskStatus
            non_terminal = [
                t for t in tasks
                if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
            ]
            if not non_terminal:
                continue

            # Find the parent (primary) task
            parent_task = all_tasks_by_session.get(parent_sid)
            parent_task_id = parent_task.id if parent_task else parent_sid
            title = parent_task.title if parent_task else "Unknown work-item run"

            # Build work-item list.
            work_items: list[RecoverableWorkItem] = []
            earliest_interrupt = ""
            for t in sorted(tasks, key=lambda x: (x.created_at, x.id)):
                meta = dict(getattr(t, "metadata", {}) or {})
                recovery_meta = meta.get("interrupted_recovery", {})
                is_int = _is_interrupted(t)
                if is_int and recovery_meta.get("detected_at", ""):
                    detected = recovery_meta["detected_at"]
                    if not earliest_interrupt or detected < earliest_interrupt:
                        earliest_interrupt = detected

                work_items.append(RecoverableWorkItem(
                    work_item_projection_id=work_item_projection_id_from_metadata(meta, fallback=t.id),
                    title=t.title,
                    task_id=t.id,
                    status=t.status.value if hasattr(t.status, "value") else str(t.status),
                    interrupted=is_int,
                    previous_status=recovery_meta.get("previous_status", ""),
                ))

            profile = ""
            for t in tasks:
                p = (getattr(t, "metadata", {}) or {}).get("company_profile", "")
                if p:
                    profile = p
                    break

            interrupted.append(InterruptedWorkItemRun(
                parent_session_id=parent_sid,
                parent_task_id=parent_task_id,
                project_id=project_id,
                title=title,
                profile=profile,
                interrupted_at=earliest_interrupt or datetime.now().isoformat(),
                work_items=work_items,
            ))

        return RecoveryStatus(
            interrupted=interrupted,
            active_recoveries=list(self._active_recoveries.keys()),
            scanned_at=time.time(),
        )

    async def resume(self, parent_task_id: str) -> dict[str, Any]:
        """Deterministically resume an interrupted work-item run."""
        async with self._lock:
            if parent_task_id in self._active_recoveries:
                return {"ok": False, "error": "already_in_progress"}

            # Find the interrupted work-item run.
            status = await self.scan()
            wf = next((w for w in status.interrupted if w.parent_task_id == parent_task_id), None)
            if not wf:
                return {"ok": False, "error": "not_found"}

            # Load snapshot
            snapshot = await self._engine._load_company_runtime_snapshot(wf.parent_session_id)
            if not snapshot:
                return {"ok": False, "error": "snapshot_unavailable"}

            plan, tasks = snapshot

            # Clean orphaned checkpoints
            await self._clean_orphaned_checkpoints(wf, tasks)

            # Reset interrupted/failed/blocked tasks → PENDING
            from opc.core.models import TaskStatus
            resumed_ids: list[str] = []
            failed_ids: list[str] = []
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
                    projection_id = work_item_projection_id_from_metadata(meta, fallback=task.id)
                    try:
                        await apply_task_status_transition(
                            self._engine.store,
                            task,
                            target_status_or_phase=TaskStatus.PENDING,
                            reason="office_recovery_resume",
                            release_claim=True,
                        )
                    except Exception as exc:
                        logger.warning("Recovery resume skipped %s: %s", task.id, exc)
                        failed_ids.append(projection_id)
                        continue
                    if task.status != TaskStatus.PENDING:
                        logger.warning("Recovery resume preserved non-runnable phase for %s", task.id)
                        failed_ids.append(projection_id)
                        continue
                    await self._engine.store.save_task(task)
                    resumed_ids.append(projection_id)

            if not resumed_ids:
                return {
                    "ok": False,
                    "error": "no_work_items_to_resume",
                    "failed_work_item_projection_ids": failed_ids,
                }

            # Invalidate cache
            self._cache_until = 0.0

            # Launch execution in background
            await self._set_run_recovery_state(
                wf.parent_session_id,
                status="resuming",
                lifecycle_status="active",
            )
            bg_task = asyncio.create_task(
                self._execute_recovery(parent_task_id, wf.parent_session_id, plan, tasks)
            )
            self._active_recoveries[parent_task_id] = bg_task

            return {
                "ok": True,
                "resumed_work_item_projection_ids": resumed_ids,
                "failed_work_item_projection_ids": failed_ids,
            }

    async def cancel(self, parent_task_id: str) -> dict[str, Any]:
        """Cancel an interrupted work-item run and clean up."""
        async with self._lock:
            # Cancel active recovery if running
            bg = self._active_recoveries.pop(parent_task_id, None)
            if bg and not bg.done():
                bg.cancel()

            # Find the interrupted work-item run.
            status = await self.scan()
            wf = next((w for w in status.interrupted if w.parent_task_id == parent_task_id), None)
            if not wf:
                return {"ok": False, "error": "not_found"}

            # Load tasks and cancel non-terminal ones
            snapshot = await self._engine._load_company_runtime_snapshot(wf.parent_session_id)
            if not snapshot:
                return {"ok": False, "error": "snapshot_unavailable"}

            _, tasks = snapshot
            from opc.core.models import TaskStatus
            cancelled_count = 0
            failed_ids: list[str] = []
            for task in tasks:
                if task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
                    projection_id = work_item_projection_id_from_metadata(
                        getattr(task, "metadata", {}) or {},
                        fallback=task.id,
                    )
                    try:
                        await apply_task_status_transition(
                            self._engine.store,
                            task,
                            target_status_or_phase=TaskStatus.CANCELLED,
                            reason="office_recovery_cancel",
                            release_claim=True,
                        )
                    except Exception as exc:
                        logger.warning("Recovery cancel skipped %s: %s", task.id, exc)
                        failed_ids.append(projection_id)
                        continue
                    if task.status != TaskStatus.CANCELLED:
                        logger.warning("Recovery cancel preserved non-cancelled phase for %s", task.id)
                        failed_ids.append(projection_id)
                        continue
                    cancelled_count += 1

            # Clean orphaned checkpoints
            await self._clean_orphaned_checkpoints(wf, tasks)

            # Invalidate cache
            self._cache_until = 0.0

        await self._set_run_recovery_state(
            wf.parent_session_id,
            status="cancelled",
            lifecycle_status="cancelled",
        )
        await self._broadcast_status()
        return {
            "ok": True,
            "cancelled_count": cancelled_count,
            "failed_work_item_projection_ids": failed_ids,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_recovery(
        self,
        parent_task_id: str,
        parent_session_id: str,
        plan: Any,
        tasks: list[Any],
    ) -> None:
        """Run the work-item executor in the background."""
        project_id = self._engine.project_id or "default"
        try:
            await self._set_run_recovery_state(
                parent_session_id,
                status="started",
                lifecycle_status="active",
            )
            await self._broadcast({"type": "recovery_result", "payload": {
                "project_id": project_id,
                "parent_task_id": parent_task_id, "status": "started",
            }})

            executor = self._engine.company_executor
            if not executor:
                raise RuntimeError("company_executor not available")

            result = await executor.execute(plan, tasks)

            await self._broadcast({"type": "recovery_result", "payload": {
                "project_id": project_id,
                "parent_task_id": parent_task_id, "status": "completed",
                "summary": result[:500] if result else "",
            }})
            await self._set_run_recovery_state(
                parent_session_id,
                status="completed",
                lifecycle_status="active",
            )
        except asyncio.CancelledError:
            await self._broadcast({"type": "recovery_result", "payload": {
                "project_id": project_id,
                "parent_task_id": parent_task_id, "status": "cancelled",
            }})
            await self._set_run_recovery_state(
                parent_session_id,
                status="cancelled",
                lifecycle_status="cancelled",
            )
        except Exception as exc:
            logger.warning(f"Recovery execution failed for {parent_task_id}: {exc}")
            await self._broadcast({"type": "recovery_result", "payload": {
                "project_id": project_id,
                "parent_task_id": parent_task_id, "status": "failed",
                "error": str(exc),
            }})
            await self._set_run_recovery_state(
                parent_session_id,
                status="failed",
                lifecycle_status="blocked",
                extra={"error": str(exc)},
            )
        finally:
            self._active_recoveries.pop(parent_task_id, None)
            self._cache_until = 0.0
            await self._broadcast_status()

    async def _clean_orphaned_checkpoints(
        self,
        wf: InterruptedWorkItemRun,
        tasks: list[Any],
    ) -> int:
        """Resolve pending checkpoints whose tasks are no longer active."""
        store = self._engine.store
        if not store:
            return 0

        session_ids = {
            str(getattr(t, "session_id", "") or "").strip()
            for t in tasks
        }
        session_ids.add(wf.parent_session_id)
        session_ids.discard("")

        cleaned = 0
        try:
            pending = await store.get_pending_checkpoints(
                project_id=wf.project_id,
            )
            for cp in pending:
                cp_session = str(cp.session_id or "").strip()
                if cp_session in session_ids:
                    await store.resolve_execution_checkpoint(
                        cp.checkpoint_id, status="cancelled"
                    )
                    cleaned += 1
        except Exception as exc:
            logger.debug(f"Checkpoint cleanup error: {exc}")

        return cleaned

    async def _broadcast_status(self) -> None:
        """Push updated recovery status to all connected clients."""
        try:
            status = await self.get_recovery_status()
            await self._broadcast({"type": "recovery_status", "payload":
                _serialize_status(status, project_id=self._engine.project_id or "default")
            })
        except Exception as exc:
            logger.debug(f"Recovery status broadcast failed: {exc}")

    def _invalidate_cache(self) -> None:
        """Force next get_recovery_status to re-scan."""
        self._cache_until = 0.0
        self._cached = None

    async def _set_run_recovery_state(
        self,
        key: str,
        *,
        status: str,
        lifecycle_status: str | None = None,
        match_task_id: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        store = getattr(self._engine, "store", None)
        if not store or not hasattr(store, "list_delegation_runs") or not hasattr(store, "save_delegation_run"):
            return
        runs = await store.list_delegation_runs(project_id=self._engine.project_id or "default")
        target = None
        if match_task_id:
            for run in runs:
                metadata = dict(getattr(run, "metadata", {}) or {})
                if str(metadata.get("origin_task_id", "") or "").strip() == key:
                    target = run
                    break
        if target is None:
            for run in runs:
                if str(run.session_id or "").strip() == key:
                    target = run
                    break
        if target is None:
            return
        target.recovery_pointer = {
            **dict(getattr(target, "recovery_pointer", {}) or {}),
            "status": status,
            "updated_at": datetime.now().isoformat(),
            **dict(extra or {}),
        }
        if lifecycle_status:
            target.lifecycle_status = lifecycle_status
        target.updated_at = datetime.now()
        await store.save_delegation_run(target)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_interrupted(task: Any) -> bool:
    """Check if a task was interrupted by crash."""
    from opc.core.models import TaskStatus
    if task.status != TaskStatus.FAILED:
        return False
    meta = getattr(task, "metadata", {}) or {}
    if meta.get("interrupted_recovery"):
        return True
    result = getattr(task, "result", {}) or {}
    artifacts = result.get("artifacts", {}) or {}
    return bool(artifacts.get("interrupted"))


def _serialize_status(status: RecoveryStatus, *, project_id: str | None = None) -> dict[str, Any]:
    """Convert RecoveryStatus to JSON-safe dict."""
    resolved_project_id = str(project_id or "").strip()
    if not resolved_project_id:
        for item in status.interrupted:
            if item.project_id:
                resolved_project_id = item.project_id
                break
    payload = {
        "interrupted": [
            {
                "parent_session_id": w.parent_session_id,
                "parent_task_id": w.parent_task_id,
                "project_id": w.project_id,
                "title": w.title,
                "profile": w.profile,
                "interrupted_at": w.interrupted_at,
                "work_items": [
                    {
                        "work_item_projection_id": s.work_item_projection_id,
                        "title": s.title,
                        "task_id": s.task_id,
                        "status": s.status,
                        "interrupted": s.interrupted,
                        "previous_status": s.previous_status,
                    }
                    for s in w.work_items
                ],
            }
            for w in status.interrupted
        ],
        "active_recoveries": status.active_recoveries,
        "scanned_at": status.scanned_at,
    }
    if resolved_project_id:
        payload["project_id"] = resolved_project_id
    return payload
