from __future__ import annotations

import unittest

from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2
from opc.layer4_tools.registry import ToolDefinition, ToolRegistry
from opc.llm.provider import _parse_tool_arguments


class ToolArgumentParsingTests(unittest.TestCase):
    def test_parse_tool_arguments_returns_error_context_for_invalid_json(self) -> None:
        parsed, raw, error = _parse_tool_arguments("shell_exec", '{"command": "echo hi"')
        self.assertEqual(parsed, '{"command": "echo hi"')
        self.assertEqual(raw, '{"command": "echo hi"')
        self.assertIsNotNone(error)
        self.assertIn("Invalid tool arguments JSON for `shell_exec`", error)

    def test_runtime_v2_tool_argument_finalize_preserves_unicode(self) -> None:
        runtime = NativeRuntimeV2.__new__(NativeRuntimeV2)
        calls = runtime._finalize_tool_calls(
            {
                0: {
                    "id": "call-1",
                    "function": "todo_write",
                    "arguments_chunks": [
                        '{"todos":[{"id":"1","title":"梳理需求","status":"in_progress"}]}'
                    ],
                }
            }
        )

        self.assertEqual(calls[0]["function"], "todo_write")
        self.assertIsNone(calls[0]["arguments_parse_error"])
        self.assertEqual(calls[0]["arguments"]["todos"][0]["title"], "梳理需求")


class ToolRegistryUnknownArgumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_argument_returns_structured_error_with_valid_params(self) -> None:
        """Unknown tool arguments must surface as a tool-call error, not be
        silently dropped. The error message must list the valid parameters
        so the agent can retry with the right names instead of the LLM
        hallucination (e.g. `digest` vs `summary`)."""
        registry = ToolRegistry()

        async def example_stub(summary: str = "", parent: str = "") -> dict[str, str]:
            return {"summary": summary, "parent": parent}

        registry.register(
            ToolDefinition(
                name="example_tool",
                description="stub",
                parameters={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "parent": {"type": "string"},
                    },
                },
                func=example_stub,
            )
        )

        result = await registry.invoke(
            "example_tool",
            {"digest": "3 children done"},
        )

        self.assertFalse(result.get("success", True))
        error = str(result.get("error", ""))
        self.assertIn("example_tool", error)
        self.assertIn("'digest'", error)
        # Error message must enumerate valid params so the agent can retry.
        self.assertIn("summary", error)
        self.assertIn("retry", error.lower())

    async def test_known_arguments_still_execute_normally(self) -> None:
        """Guardrail: making unknown-arg errors strict must not reject
        legitimate calls or the `task` auto-injection path."""
        registry = ToolRegistry()

        async def echo_tool(message: str, task=None) -> dict[str, str]:
            return {"echoed": message, "task_injected": str(bool(task))}

        registry.register(
            ToolDefinition(
                name="echo",
                description="stub",
                parameters={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                },
                func=echo_tool,
            )
        )

        result = await registry.invoke(
            "echo",
            {"message": "hello"},
            task=object(),
        )

        self.assertTrue(result.get("success"))
        self.assertEqual(result["result"]["echoed"], "hello")
        self.assertEqual(result["result"]["task_injected"], "True")

if __name__ == "__main__":
    unittest.main()
