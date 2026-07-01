from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opc.core.config import OPCConfig, RoleConfig


class UpdateRoleHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_role_persists_tools(self) -> None:
        from opc.plugins.office_ui.ws_handler import WSHandler

        cfg = OPCConfig()
        cfg.org.roles = [
            RoleConfig(
                id="student",
                name="Student",
                responsibility="Learn.",
                reports_to="owner",
                tools=["file_read"],
            )
        ]

        handler = WSHandler.__new__(WSHandler)
        handler.engine = SimpleNamespace(config=cfg, org_engine=MagicMock())
        handler._clients = set()
        handler._shutting_down = False
        handler._ws_is_open = lambda _ws: True
        handler._config_lock = AsyncMock()
        handler._config_lock.__aenter__ = AsyncMock(return_value=None)
        handler._config_lock.__aexit__ = AsyncMock(return_value=None)
        handler._broadcast_org_info = AsyncMock()

        ws = AsyncMock()
        with patch.object(OPCConfig, "save", autospec=True) as save:
            await handler._handle_update_role(
                ws,
                {
                    "role_id": "student",
                    "tools": ["file_read", " ", "web_search", ""],
                },
            )

        self.assertEqual(cfg.org.roles[0].tools, ["file_read", "web_search"])
        handler.engine.org_engine.reload_from_config.assert_called_once()
        save.assert_called_once_with(cfg)
        handler._broadcast_org_info.assert_awaited_once()
        ws.send_json.assert_awaited()
        payload = ws.send_json.call_args.args[0]
        self.assertEqual(payload["type"], "ack")
        self.assertTrue(payload["payload"]["ok"])
