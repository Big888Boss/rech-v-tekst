"""Regression test for test harness process group reaping (H1) with exited leader."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest

from tests.isolated_test import GuardedPopen, IsolatedTestCase, is_group_alive


class TestHarnessProcessReaping(IsolatedTestCase):
    def test_h1_exited_leader_reaps_orphaned_child_via_saved_pgid(self) -> None:
        """Verify that when a process group leader exits, cleanup still reaps surviving children."""
        # Leader script that forks a child and exits immediately
        script = (
            "import os, sys, time, signal\n"
            "pid = os.fork()\n"
            "if pid == 0:\n"
            "    # Child process ignores SIGTERM initially to test escalation\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    time.sleep(10)\n"
            "    sys.exit(0)\n"
            "else:\n"
            "    # Leader exits immediately\n"
            "    sys.exit(0)\n"
        )

        proc = GuardedPopen(
            [sys.executable, "-c", script],
            start_new_session=True,
        )
        self.assertIsNotNone(proc.saved_pgid)
        saved_pgid = proc.saved_pgid

        # Wait for leader to exit
        proc.wait(timeout=2.0)
        self.assertEqual(proc.poll(), 0)

        # Leader is dead, but child in saved_pgid is still alive!
        self.assertTrue(is_group_alive(saved_pgid))

        # Reaping target must kill the surviving child using saved_pgid
        self._reap_target(proc)

        # Verify entire group is dead
        self.assertFalse(is_group_alive(saved_pgid))


if __name__ == "__main__":
    unittest.main()
