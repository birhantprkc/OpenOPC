"""Runtime and global execution-mode service."""

from __future__ import annotations

from typing import Any

from opc.plugins.office_ui.snapshot_builder import build_collab_sync, build_snapshot

from .context import OfficeServiceContext
from .models import ServiceError, ServiceEvent, ServiceResult
from .session import SessionService


class RuntimeService:
    def __init__(self, context: OfficeServiceContext, session_service: SessionService) -> None:
        self.context = context
        self.session_service = session_service

    async def mode_show(self) -> ServiceResult:
        active_org = ""
        if self.context.mode_state.exec_mode == "org" and self.context.get_active_saved_org_name is not None:
            active_org = await self.context.get_active_saved_org_name()
        return ServiceResult({
            "mode": self.context.mode_state.exec_mode,
            "profile": self.context.mode_state.company_profile,
            "org_id": active_org,
            "preferred_agent": self.context.mode_state.task_preferred_agent,
        })

    async def status(self, *, project_id: str, limit: int = 50) -> ServiceResult:
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        payload: dict[str, Any] = {
            "project_id": project_id,
            "mode": self.context.mode_state.exec_mode,
            "profile": self.context.mode_state.company_profile,
            "preferred_agent": self.context.mode_state.task_preferred_agent,
            "active_tasks": [],
            "runtime_sessions": [],
            "external_sessions": [],
            "checkpoints": [],
        }
        if not self.context.store_is_ready(store):
            payload["available"] = False
            payload["reason"] = "store_not_ready"
            return ServiceResult(payload)
        from opc.core.models import TaskStatus

        terminal = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
        tasks = await store.get_tasks(project_id=project_id) if hasattr(store, "get_tasks") else []
        payload["active_tasks"] = [
            {
                "task_id": getattr(task, "id", ""),
                "title": getattr(task, "title", ""),
                "status": getattr(getattr(task, "status", None), "value", getattr(task, "status", "")),
                "session_id": getattr(task, "session_id", ""),
                "assigned_to": getattr(task, "assigned_to", ""),
            }
            for task in tasks
            if getattr(task, "status", None) not in terminal
        ][:limit]
        if hasattr(store, "list_runtime_sessions"):
            payload["runtime_sessions"] = await store.list_runtime_sessions(project_id=project_id, limit=limit)
        if hasattr(store, "list_external_sessions"):
            payload["external_sessions"] = await store.list_external_sessions(project_id=project_id, limit=limit)
        if hasattr(store, "get_pending_checkpoints"):
            payload["checkpoints"] = await store.get_pending_checkpoints(project_id=project_id)
            payload["checkpoints"] = payload["checkpoints"][:limit]
        return ServiceResult(payload)

    async def mode_set(
        self,
        *,
        mode: str,
        profile: str = "corporate",
        preferred_agent: str | None = None,
        org_id: str | None = None,
        sync_config: bool = True,
    ) -> ServiceResult:
        new_mode = self.session_service.normalize_exec_mode(mode)
        normalized_org_id = self.session_service.normalize_org_id(org_id)
        if new_mode == "org":
            profile = "custom"
            if sync_config and normalized_org_id and self.context.load_active_org_config:
                if not self.context.load_active_org_config(normalized_org_id):
                    raise ServiceError("org_not_found", "org_not_found", {"org_id": normalized_org_id})
                if self.context.set_active_saved_org_name:
                    await self.context.set_active_saved_org_name(normalized_org_id)
        else:
            normalized_org_id = ""
            profile = self.session_service.normalize_company_profile(profile)
            if new_mode == "company" and profile == "custom":
                profile = "corporate"
        agent = self.session_service.normalize_preferred_agent(
            preferred_agent if preferred_agent is not None else self.context.mode_state.task_preferred_agent,
            default=self.context.mode_state.task_preferred_agent,
        )
        self.context.mode_state.exec_mode = new_mode
        self.context.mode_state.company_profile = profile
        self.context.mode_state.task_preferred_agent = agent
        if self.context.agent_store:
            await self.context.agent_store.set_server_state("exec_mode", new_mode)
            await self.context.agent_store.set_server_state("company_profile", profile)
            await self.context.agent_store.set_server_state("task_preferred_agent", agent)
        if getattr(self.context.engine, "org_engine", None) and self.context.agent_store:
            await self.context.agent_store.load_preset("custom" if new_mode == "org" else profile, self.context.engine.org_engine)
        snapshot = await build_snapshot(
            self.context.engine,
            self.context.agent_store,
            self.context.chat_store,
            self.context.event_adapter,
        )
        snapshot["exec_mode"] = new_mode
        snapshot["company_profile"] = profile
        snapshot["task_preferred_agent"] = agent
        collab = await build_collab_sync(
            self.context.engine,
            self.context.agent_store,
            self.context.chat_store,
            self.context.event_adapter,
            exec_mode=new_mode,
        )
        payload = {"mode": new_mode, "profile": profile, "org_id": normalized_org_id, "preferred_agent": agent}
        return ServiceResult(payload, [ServiceEvent("snapshot", snapshot), ServiceEvent("collab_sync_push", collab)])

    async def run_task(self, *, project_id: str, task_id: str) -> ServiceResult:
        engine = await self.context.engine_for_project(project_id)
        task = await engine.store.get_task(task_id) if getattr(engine, "store", None) else None
        if not task:
            raise ServiceError("task_not_found", "task_not_found", {"task_id": task_id})
        prompt = f"{getattr(task, 'title', '')}\n{getattr(task, 'description', '')}".strip()
        return await self.session_service.send(
            project_id=project_id,
            task_id=task_id,
            content=prompt,
        )

    async def checkpoints(self, *, project_id: str, limit: int = 50) -> ServiceResult:
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        checkpoints = await store.get_pending_checkpoints(project_id=project_id) if store and hasattr(store, "get_pending_checkpoints") else []
        return ServiceResult({"project_id": project_id, "checkpoints": checkpoints[-limit:]})

    async def logs(self, *, project_id: str, task_id: str, limit: int = 100) -> ServiceResult:
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        task = await store.get_task(task_id) if store else None
        if not task:
            raise ServiceError("task_not_found", "task_not_found", {"task_id": task_id})
        metadata = dict(getattr(task, "metadata", {}) or {})
        transcript = await store.get_session_transcript(task.session_id) if getattr(task, "session_id", None) else []
        runtime_sessions = []
        runtime_events: list[dict[str, Any]] = []
        if hasattr(store, "list_runtime_sessions"):
            runtime_sessions = await store.list_runtime_sessions(project_id=project_id, task_id=task_id, limit=limit)
        if runtime_sessions and hasattr(store, "list_runtime_events"):
            for session in runtime_sessions:
                runtime_id = str(session.get("runtime_session_id", "") or "")
                if runtime_id:
                    runtime_events.extend(await store.list_runtime_events(runtime_id, limit=limit))
        enriched_events = [self._runtime_event_payload(event) for event in runtime_events[-limit:]]
        return ServiceResult({
            "project_id": project_id,
            "task_id": task_id,
            "target": {
                "task_id": task_id,
                "session_id": str(getattr(task, "session_id", "") or ""),
                "title": str(getattr(task, "title", "") or ""),
                "status": str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or ""),
                "role_id": str(metadata.get("role_id") or getattr(task, "assigned_to", "") or ""),
                "agent_id": str(metadata.get("agent_id") or metadata.get("preferred_agent") or ""),
                "work_item_id": str(
                    metadata.get("work_item_id")
                    or metadata.get("linked_work_item_id")
                    or ""
                ),
            },
            "transcript": transcript[-limit:],
            "runtime_sessions": runtime_sessions,
            "runtime_events": enriched_events,
        })

    @staticmethod
    def _runtime_event_payload(event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            payload = dict(event)
        elif hasattr(event, "model_dump"):
            payload = dict(event.model_dump())
        else:
            payload = dict(getattr(event, "__dict__", {}) or {})
        event_type = str(payload.get("event_type") or payload.get("type") or "")
        raw_payload = payload.get("payload")
        if isinstance(raw_payload, dict):
            tool_name = str(raw_payload.get("tool_name") or raw_payload.get("name") or "")
            summary = str(raw_payload.get("summary") or raw_payload.get("result_summary") or raw_payload.get("text") or "")
        else:
            tool_name = ""
            summary = ""
        display_parts = [part for part in (event_type, tool_name, summary) if part]
        payload["display_text"] = " | ".join(display_parts)
        payload["event_type"] = event_type
        return payload
