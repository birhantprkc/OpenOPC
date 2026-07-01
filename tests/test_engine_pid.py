from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from opc import engine as engine_module
from opc.engine import OPCEngine


class EnginePidProbeTests(unittest.TestCase):
    def test_current_process_is_running(self) -> None:
        self.assertTrue(OPCEngine._pid_is_running(os.getpid()))

    def test_posix_probe_treats_unexpected_oserror_as_not_running(self) -> None:
        with patch.object(engine_module.os, "name", "posix"), patch.object(
            engine_module.os,
            "kill",
            side_effect=OSError("platform probe failed"),
        ):
            self.assertFalse(OPCEngine._pid_is_running(12345))

