"""Automated Browser E2E Test Suite for Layout (320, 624, 760, 1280) and 6 Tooltip Fixes.
Uses isolated fake UI server on ephemeral port without touching real user data.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from tests.isolated_test import IsolatedTestCase
from tests.test_browser_e2e import (
    NODE_BIN,
    NODE_MODULES,
    SERVER_SCRIPT,
    _terminate_and_reap_group,
    _terminate_and_reap_pid,
    _is_forbidden_target,
)

BASE_DIR = Path(__file__).resolve().parent.parent
QA_LAYOUT_SCRIPT = BASE_DIR / "tests" / "qa_layout_and_tooltips.js"


class TestLayoutAndTooltipsE2E(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_proc = None
        self.server_pgid = None
        self.qa_proc = None
        self.qa_pgid = None
        self.temp_dir = tempfile.mkdtemp(prefix="qa_layout_out_")
        self.registry_file = Path(self.temp_dir) / ".qa_registry.json"

    def tearDown(self) -> None:
        # Reap browser from registry if present
        if self.registry_file.exists():
            try:
                reg = json.loads(self.registry_file.read_text(encoding="utf-8"))
                b_pid = reg.get("browser_pid")
                b_pgid = reg.get("browser_pgid")
                if b_pgid and not _is_forbidden_target(b_pgid):
                    _terminate_and_reap_group(b_pgid, None, timeout_sec=1.0)
                elif b_pid and not _is_forbidden_target(b_pid):
                    _terminate_and_reap_pid(b_pid, timeout_sec=1.0)
            except Exception:
                pass

        if self.qa_pgid and not _is_forbidden_target(self.qa_pgid):
            _terminate_and_reap_group(self.qa_pgid, self.qa_proc, timeout_sec=1.0)

        if self.server_pgid and not _is_forbidden_target(self.server_pgid):
            _terminate_and_reap_group(self.server_pgid, self.server_proc, timeout_sec=1.0)

        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
        super().tearDown()

    def _wait_for_server(self, url: str, timeout_sec: float = 8.0) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                with urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                time.sleep(0.2)
        return False

    def test_layout_breakpoints_and_tooltip_system(self) -> None:
        if not NODE_BIN or not NODE_BIN.exists():
            self.skipTest("Node binary not found. Set NODE_BIN or install node in PATH.")
        if not NODE_MODULES or not NODE_MODULES.exists():
            self.skipTest("node_modules directory not found. Set NODE_PATH.")

        server_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": "0",
            "WEBINAR_OUT_DIR": self.temp_dir,
            "PYTHONPYCACHEPREFIX": "/tmp/pycache",
        }

        self.server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT), "0"],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.server_pgid = self.server_proc.pid

        info_file = Path(self.temp_dir) / ".fake_server_info.json"
        deadline = time.time() + 8.0
        server_info: dict[str, Any] = {}
        while time.time() < deadline:
            if info_file.exists():
                try:
                    server_info = json.loads(info_file.read_text(encoding="utf-8"))
                    if server_info.get("port") and server_info.get("identity"):
                        break
                except Exception:
                    pass
            time.sleep(0.1)

        self.assertTrue(server_info.get("port"), "Fake UI server failed to report port")
        server_port = int(server_info["port"])
        server_identity = server_info["identity"]

        ready = self._wait_for_server(f"http://127.0.0.1:{server_port}/api/status", timeout_sec=8.0)
        self.assertTrue(ready, f"Server failed to start on port {server_port}")

        client_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": str(server_port),
            "UI_IDENTITY": server_identity,
            "NODE_PATH": str(NODE_MODULES),
            "QA_REGISTRY_FILE": str(self.registry_file),
        }

        self.qa_proc = subprocess.Popen(
            [str(NODE_BIN), str(QA_LAYOUT_SCRIPT)],
            env=client_env,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.qa_pgid = self.qa_proc.pid

        qa_stdout, qa_stderr = self.qa_proc.communicate(timeout=60)
        print("\n--- QA Script Output ---")
        print(qa_stdout)
        if qa_stderr:
            print("--- QA Script Stderr ---")
            print(qa_stderr)

        self.assertEqual(
            self.qa_proc.returncode,
            0,
            f"QA layout & tooltips failed with code {self.qa_proc.returncode}:\n{qa_stderr}\n{qa_stdout}",
        )


if __name__ == "__main__":
    unittest.main()
