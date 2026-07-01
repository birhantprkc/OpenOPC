"""Runtime-managed tool hook bus for Native Runtime V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass
class RuntimeToolHookContext:
    phase: str
    tool_name: str
    call: dict[str, Any]
    task: Any = None
    tool: Any = None
    arguments: dict[str, Any] = field(default_factory=dict)
    predicted_permission: Any = None
    result: dict[str, Any] | None = None
    state: dict[str, Any] = field(default_factory=dict)


RuntimeToolHook = Callable[[RuntimeToolHookContext], Awaitable[Optional[dict[str, Any]]]]
RuntimeHookEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class RuntimeToolHookBus:
    """Composable pre/post/failure hook bus for runtime-managed tool execution."""

    def __init__(self, *, emit_event: RuntimeHookEmitter | None = None) -> None:
        self.emit_event = emit_event
        self._pre_hooks: list[tuple[str, RuntimeToolHook]] = []
        self._post_hooks: list[tuple[str, RuntimeToolHook]] = []
        self._failure_hooks: list[tuple[str, RuntimeToolHook]] = []

    def register_pre_hook(self, name: str, hook: RuntimeToolHook) -> None:
        self._pre_hooks.append((name, hook))

    def register_post_hook(self, name: str, hook: RuntimeToolHook) -> None:
        self._post_hooks.append((name, hook))

    def register_failure_hook(self, name: str, hook: RuntimeToolHook) -> None:
        self._failure_hooks.append((name, hook))

    async def run_pre_hooks(self, context: RuntimeToolHookContext) -> RuntimeToolHookContext:
        return await self._run_hooks(self._pre_hooks, context)

    async def run_post_hooks(self, context: RuntimeToolHookContext) -> RuntimeToolHookContext:
        return await self._run_hooks(self._post_hooks, context)

    async def run_failure_hooks(self, context: RuntimeToolHookContext) -> RuntimeToolHookContext:
        return await self._run_hooks(self._failure_hooks, context)

    async def _run_hooks(
        self,
        hooks: list[tuple[str, RuntimeToolHook]],
        context: RuntimeToolHookContext,
    ) -> RuntimeToolHookContext:
        for hook_name, hook in hooks:
            patch = await hook(context) or {}
            self._apply_patch(context, patch)
            if self.emit_event:
                await self.emit_event(
                    "tool_hook",
                    {
                        "phase": context.phase,
                        "tool_name": context.tool_name,
                        "tool_call_id": context.call.get("id", ""),
                        "hook_name": hook_name,
                        "stopped": bool(context.state.get("stop_execution")),
                        "result_overridden": context.result is not None,
                    },
                )
            if context.state.get("stop_execution"):
                break
        return context

    @staticmethod
    def _apply_patch(context: RuntimeToolHookContext, patch: dict[str, Any]) -> None:
        if not patch:
            return
        if isinstance(patch.get("arguments"), dict):
            context.arguments = dict(patch["arguments"])
        if isinstance(patch.get("result"), dict):
            context.result = dict(patch["result"])
        if isinstance(patch.get("metadata"), dict):
            context.state.setdefault("metadata", {}).update(dict(patch["metadata"]))
        if isinstance(patch.get("approval"), dict):
            context.state.setdefault("approval", {}).update(dict(patch["approval"]))
        if "stop_execution" in patch:
            context.state["stop_execution"] = bool(patch["stop_execution"])
        if "stop_batch_on_failure" in patch:
            context.state["stop_batch_on_failure"] = bool(patch["stop_batch_on_failure"])
        if "prevent_continuation" in patch:
            context.state["prevent_continuation"] = bool(patch["prevent_continuation"])
        if patch.get("stop_reason"):
            context.state["stop_reason"] = str(patch["stop_reason"])
