from __future__ import annotations

import contextlib
import shutil
import unittest
import uuid
from pathlib import Path

from opc.core.config import AutonomyConfig, OPCConfig
from opc.core.models import ApprovalAction, ApprovalDecision, PermissionResolution, RiskLevel, Task, TaskResult, TaskStatus
from opc.layer2_organization.approval import ApprovalEngine
from opc.layer3_agent.runtime_v2.permissions import RuntimePermissionAdapter
from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2
from opc.layer3_agent.runtime_v2.streaming_tool_executor import StreamingToolExecutor
from opc.layer3_agent.runtime_v2.subagents import SubagentManager
from opc.layer3_agent.runtime_v2.tool_planner import ToolPlanner
from opc.layer4_tools.registry import ToolDefinition, ToolRegistry


@contextlib.contextmanager
def _workspace_tempdir() -> Path:
    base = Path.cwd() / ".tmp-test" / f"runtime-hooks-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class _StubLLM:
    def __init__(self) -> None:
        self.config = type("Cfg", (), {"max_tokens": 2048})()


class _PrefsStub:
    def get_autonomy_preferences(self, project_id=None):
        _ = project_id
        return {"learned_actions": {}}

    def record_autonomy_feedback(self, **kwargs):
        _ = kwargs


class _StoreStub:
    async def record_approval(self, **kwargs):
        _ = kwargs


class _MemoryStub:
    def append_autonomy_event(self, event, project=False):
        _ = (event, project)


def _policy_adapter() -> RuntimePermissionAdapter:
    return RuntimePermissionAdapter(ApprovalEngine(
        llm=object(),
        store=_StoreStub(),
        preferences=_PrefsStub(),
        memory=_MemoryStub(),
        escalation=None,
        config=AutonomyConfig(),
    ))


class RuntimeHookBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_hook_permission_gate_blocks_execution(self) -> None:
        registry = ToolRegistry()
        executed: list[str] = []

        async def shell_tool(command: str) -> dict[str, str]:
            executed.append(command)
            return {"stdout": command}

        registry.register(ToolDefinition(
            name="shell_exec",
            description="shell",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}},
            func=shell_tool,
            concurrency_safe=False,
            read_only=False,
            requires_confirmation=True,
        ))

        async def approval_callback(tool, arguments, task, on_progress):
            _ = (tool, arguments, task, on_progress)
            return False, ApprovalDecision(
                action=ApprovalAction.ESCALATE,
                risk_level=RiskLevel.HIGH,
                rationale="Need approval first.",
                confidence=1.0,
                policy_source="test",
            )

        runtime = NativeRuntimeV2(
            llm=_StubLLM(),
            tool_registry=registry,
            config=OPCConfig(),
            approval_callback=approval_callback,
        )
        task = Task(id="task-hook", session_id="sess-hook", project_id="proj1")
        hook_bus = runtime._build_tool_hook_bus(
            runtime_session_id="rt_hook",
            task=task,
            permission_resolver=_policy_adapter(),
        )
        executor = StreamingToolExecutor(
            registry=registry,
            planner=ToolPlanner(registry),
            permission_resolver=_policy_adapter(),
            hook_bus=hook_bus,
        )

        results = await executor.execute([
            {"id": "call-1", "function": "shell_exec", "arguments": {"command": "git commit -m test"}},
        ], task=task)

        self.assertEqual(executed, [])
        self.assertFalse(results[0]["result"]["success"])
        self.assertEqual(results[0]["permission_decision"].resolution, PermissionResolution.ASK)
        self.assertEqual(results[0]["result"]["approval"]["policy_source"], "test")

    async def test_parallel_batch_failure_converges_remaining_calls(self) -> None:
        registry = ToolRegistry()
        executed: list[str] = []

        async def fail_read(path: str) -> dict[str, str]:
            raise RuntimeError(f"boom:{path}")

        async def second_read(path: str) -> dict[str, str]:
            executed.append(path)
            return {"content": path}

        registry.register(ToolDefinition(
            name="file_read",
            description="read",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            func=fail_read,
            concurrency_safe=True,
            read_only=True,
        ))
        registry.register(ToolDefinition(
            name="web_fetch",
            description="fetch",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            func=second_read,
            concurrency_safe=True,
            read_only=True,
        ))

        runtime = NativeRuntimeV2(
            llm=_StubLLM(),
            tool_registry=registry,
            config=OPCConfig(),
        )
        hook_bus = runtime._build_tool_hook_bus(
            runtime_session_id="rt_parallel",
            task=Task(id="task-parallel", session_id="sess-parallel", project_id="proj1"),
            permission_resolver=_policy_adapter(),
        )
        executor = StreamingToolExecutor(
            registry=registry,
            planner=ToolPlanner(registry, max_parallel_read_tools=1),
            permission_resolver=_policy_adapter(),
            hook_bus=hook_bus,
            max_parallel_read_tools=1,
            converge_on_parallel_failure=True,
        )

        results = await executor.execute([
            {"id": "call-a", "function": "file_read", "arguments": {"path": "a.txt"}},
            {"id": "call-b", "function": "web_fetch", "arguments": {"path": "b.txt"}},
        ])

        self.assertFalse(results[0]["result"]["success"])
        self.assertFalse(results[1]["result"]["success"])
        self.assertTrue(results[1]["result"]["converged"])
        self.assertEqual(executed, [])


class SubagentPermissionBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_bridge_routes_child_approval_through_parent_task(self) -> None:
        captured: dict[str, object] = {}

        class _ApprovalEngine:
            async def authorize_tool_call(self, **kwargs):
                captured["approval_kwargs"] = dict(kwargs)
                return True, ApprovalDecision(
                    action=ApprovalAction.AUTO_APPROVE,
                    risk_level=RiskLevel.LOW,
                    rationale="approved",
                    confidence=1.0,
                    policy_source="human_escalation",
                    metadata={"human_reply": "approve_session"},
                )

        approval_engine = _ApprovalEngine()

        class _ChildAgent:
            async def execute(self, child_task: Task) -> TaskResult:
                captured["child_task_metadata"] = dict(child_task.metadata)
                bridge = getattr(child_task, "_runtime_permission_bridge")
                tool = ToolDefinition(
                    name="shell_exec",
                    description="shell",
                    parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                    func=lambda **_: None,  # type: ignore[arg-type]
                    concurrency_safe=False,
                    read_only=False,
                    requires_confirmation=True,
                )
                allowed, decision = await bridge(
                    tool=tool,
                    arguments={"command": "git status"},
                    approval_engine=approval_engine,
                    on_progress=None,
                )
                captured["allowed"] = allowed
                captured["decision"] = decision
                return TaskResult(status=TaskStatus.DONE, content="bridge-ok")

        manager = SubagentManager(
            parent_task=Task(id="parent-task", session_id="parent-session", project_id="proj1"),
            config=OPCConfig(),
            child_agent_factory=lambda profile, allowed_tools, prompt, overrides: _ChildAgent(),
            runtime_session_id="rt_parent",
        )

        result = await manager.spawn(
            profile="implement",
            prompt="Need approval",
            background=False,
            name="bridge-worker",
        )

        self.assertTrue(result["success"])
        self.assertTrue(captured["allowed"])
        approval_kwargs = dict(captured["approval_kwargs"])
        self.assertEqual(approval_kwargs["task"].id, "parent-task")
        self.assertEqual(approval_kwargs["metadata"]["subagent_name"], "bridge-worker")
        self.assertEqual(captured["child_task_metadata"]["_permission_bridge_runtime_session_id"], "rt_parent")


if __name__ == "__main__":
    unittest.main()
