"""Escalation engine — handles human-in-the-loop decision points."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from opc.core.models import EscalationType, OPCEvent, Task
from opc.core.events import EventBus


UserReplyCallback = Callable[[str, list[dict]], Coroutine[Any, Any, Optional[str]]]


class EscalationEngine:
    """Manages escalation to the human owner for decisions, info, and risk warnings."""

    def __init__(
        self,
        event_bus: EventBus,
        timeout_seconds: int = 300,
        user_reply_callback: UserReplyCallback | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.timeout_seconds = timeout_seconds
        self.user_reply_callback = user_reply_callback
        self._pending: dict[str, asyncio.Event] = {}
        self._replies: dict[str, str] = {}

    async def escalate(
        self,
        task: Task,
        escalation_type: EscalationType,
        message: str,
        options: list[dict[str, str]] | None = None,
        default_action: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """Escalate to the user and wait for a reply.

        Returns the user's reply or the default action on timeout.
        ``context`` carries structured approval data (action, allowlist
        patterns, scopes) into the UI card so a decision can still be applied
        after this inline wait has expired.
        """
        # Use a unique escalation id per prompt so repeated approvals for the
        # same task do not alias to older UI cards or stale pending state.
        escalation_id = f"esc_{task.id}_{uuid.uuid4().hex}"

        await self.event_bus.publish(OPCEvent(
            event_type="escalation_created",
            payload={
                "escalation_id": escalation_id,
                "task_id": task.id,
                "type": escalation_type.value,
                "message": message,
                "options": options or [],
                "default_action": default_action,
                "approval_context": dict(context or {}),
            },
        ))

        logger.info(f"Escalation [{escalation_type.value}] for task {task.id}: {message}")

        if self.user_reply_callback:
            try:
                reply = await asyncio.wait_for(
                    self.user_reply_callback(message, options or []),
                    timeout=self.timeout_seconds,
                )
                if reply is not None:
                    await self.event_bus.publish(OPCEvent(
                        event_type="escalation_resolved",
                        payload={"escalation_id": escalation_id, "reply": reply},
                    ))
                    return reply
            except asyncio.TimeoutError:
                logger.warning(f"Escalation {escalation_id} timed out, using default: {default_action}")
                await self.event_bus.publish(OPCEvent(
                    event_type="escalation_timeout",
                    payload={"escalation_id": escalation_id, "default_action": default_action},
                ))
                return default_action
            except Exception as e:
                logger.error(f"Escalation callback error: {e}")

        return default_action

    async def escalate_info_needed(self, task: Task, info_description: str) -> str | None:
        return await self.escalate(
            task=task,
            escalation_type=EscalationType.INFO_NEEDED,
            message=f"[INFO NEEDED] Task: {task.title}\nMissing: {info_description}\nPlease provide to continue.",
        )

    async def escalate_decision(
        self,
        task: Task,
        question: str,
        options: list[dict[str, str]],
        default_action: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        metadata = dict(getattr(task, "metadata", {}) or {})
        execution_mode = str(metadata.get("execution_mode", "") or "").strip()
        mode = str(metadata.get("mode", "") or "").strip()
        runtime_kind = str(metadata.get("runtime_kind", "") or "").strip()
        is_task_mode = (
            execution_mode == "task_mode"
            or mode == "task"
            or runtime_kind == "task_mode_agent_turn"
        )
        task_label = (
            str(metadata.get("original_message") or getattr(task, "description", "") or task.title).strip()
            if is_task_mode
            else task.title
        )
        return await self.escalate(
            task=task,
            escalation_type=EscalationType.DECISION_NEEDED,
            message=f"[DECISION NEEDED] Task: {task_label}\n{question}",
            options=options,
            default_action=default_action,
            context=context,
        )

    async def escalate_risk(self, task: Task, risk_description: str) -> str | None:
        return await self.escalate(
            task=task,
            escalation_type=EscalationType.RISK_WARNING,
            message=f"[RISK WARNING] {risk_description}",
            options=[{"id": "proceed", "label": "Proceed"}, {"id": "abort", "label": "Abort"}],
            default_action="abort",
        )
