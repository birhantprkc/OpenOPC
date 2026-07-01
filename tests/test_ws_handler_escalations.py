from __future__ import annotations

import unittest

from opc.plugins.office_ui.ws_handler import WSHandler


class WSHandlerEscalationResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_scope_reply_resolves_matching_sibling_escalations(self) -> None:
        handler = object.__new__(WSHandler)
        handler._pending_escalations = {}
        handler._pending_escalation_order = []

        first = handler._remember_pending_escalation({
            "escalation_id": "esc-ext-first",
            "task_id": "session-root",
            "source_task_id": "exec-1",
            "message": (
                "[DECISION NEEDED] Task: CEO Intake\n"
                "Approve external_agent 'codex'?\n"
                "Risk: high\n"
                "Allowlist target: external_agent:codex"
            ),
            "options": [
                {"id": "approve_once", "label": "Approve once"},
                {"id": "approve_session", "label": "Allow for this session"},
                {"id": "deny", "label": "Deny"},
            ],
            "default_action": "approve_once",
            "escalation_type": "decision_needed",
        })
        second = handler._remember_pending_escalation({
            "escalation_id": "esc-ext-second",
            "task_id": "session-root",
            "source_task_id": "exec-2",
            "message": (
                "[DECISION NEEDED] Task: CTO Planning\n"
                "Approve external_agent 'codex'?\n"
                "Risk: high\n"
                "Allowlist target: external_agent:codex"
            ),
            "options": [
                {"id": "approve_once", "label": "Approve once"},
                {"id": "approve_session", "label": "Allow for this session"},
                {"id": "deny", "label": "Deny"},
            ],
            "default_action": "approve_once",
            "escalation_type": "decision_needed",
        })

        first["future"].set_result("approve_session")
        resolved_ids = handler._resolve_related_pending_escalations(first, "approve_session")

        self.assertTrue(first["future"].done())
        self.assertEqual(first["future"].result(), "approve_session")
        self.assertTrue(second["future"].done())
        self.assertEqual(second["future"].result(), "approve_session")
        self.assertEqual(resolved_ids, ["esc-ext-second"])

    async def test_project_scope_keeps_parallel_approval_groups_isolated(self) -> None:
        handler = object.__new__(WSHandler)
        handler._pending_escalations = {}
        handler._pending_escalation_order = []

        first = handler._remember_pending_escalation({
            "escalation_id": "esc-project-a",
            "project_id": "project-a",
            "task_id": "session-root",
            "source_task_id": "exec-a",
            "message": "Approve external_agent 'codex'?",
            "options": [
                {"id": "approve_once", "label": "Approve once"},
                {"id": "approve_session", "label": "Allow for this session"},
            ],
            "default_action": "approve_once",
            "escalation_type": "decision_needed",
            "approval_group_key": "external_agent:codex",
        })
        second = handler._remember_pending_escalation({
            "escalation_id": "esc-project-b",
            "project_id": "project-b",
            "task_id": "session-root",
            "source_task_id": "exec-b",
            "message": "Approve external_agent 'codex'?",
            "options": [
                {"id": "approve_once", "label": "Approve once"},
                {"id": "approve_session", "label": "Allow for this session"},
            ],
            "default_action": "approve_once",
            "escalation_type": "decision_needed",
            "approval_group_key": "external_agent:codex",
        })

        try:
            resolved_ids = handler._resolve_related_pending_escalations(first, "approve_session")

            self.assertEqual(resolved_ids, [])
            self.assertFalse(second["future"].done())
            self.assertIs(handler._find_pending_escalation(task_id="session-root", project_id="project-a"), first)
            self.assertIs(handler._find_pending_escalation(task_id="session-root", project_id="project-b"), second)
        finally:
            for record in (first, second):
                future = record.get("future")
                if future and not future.done():
                    future.cancel()


if __name__ == "__main__":
    unittest.main()
