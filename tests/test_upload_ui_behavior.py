"""Narrow behavioral UI test for upload dependency gating and error handling.
Validates:
1. Missing/unknown tools disable upload submit button and strictly block POST.
2. HTTP 500 error displays persistent uploadError banner without success text.
Reuses existing proven cleanup helpers from tests.test_browser_e2e.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from tests.test_browser_e2e import (
    BASE_DIR,
    NODE_BIN,
    NODE_MODULES,
    SERVER_SCRIPT,
    _is_forbidden_target,
    _is_pid_alive,
    _terminate_and_reap_group,
    _terminate_and_reap_pid,
)

QA_UPLOAD_SCRIPT = BASE_DIR / "tests" / "qa_upload_behavior.js"


class TestUploadUIBehavior(unittest.TestCase):
    """Reuses exact cleanup helpers from tests.test_browser_e2e without running full e2e suite."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="webinar_upload_qa_")
        self.registry_file = Path(self.temp_dir) / ".qa_process_registry.json"
        self.server_port = 8799
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_pgid: int | None = None
        self.qa_proc: subprocess.Popen[str] | None = None
        self.qa_pgid: int | None = None
        self.browser_pid: int | None = None
        self.browser_pgid: int | None = None
        self.browser_proc: subprocess.Popen[str] | None = None

        if not NODE_BIN or not NODE_BIN.exists():
            self.skipTest("Node binary not found. Set NODE_BIN or install node in PATH.")
        if not NODE_MODULES or not NODE_MODULES.exists():
            self.skipTest("node_modules directory not found. Set NODE_PATH.")
        if not QA_UPLOAD_SCRIPT.exists():
            self.fail(f"QA upload script not found at {QA_UPLOAD_SCRIPT}")

    def tearDown(self) -> None:
        # 1. Read process registry if written by QA script
        if self.registry_file.exists():
            try:
                reg_data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                bpid = reg_data.get("browser_pid")
                if bpid and not _is_forbidden_target(int(bpid)):
                    self.browser_pid = int(bpid)
                bpgid = reg_data.get("browser_pgid")
                if bpgid and not _is_forbidden_target(int(bpgid)):
                    self.browser_pgid = int(bpgid)
            except Exception:
                pass

        # 2. Cleanup browser
        if self.browser_pgid and not _is_forbidden_target(self.browser_pgid):
            _terminate_and_reap_group(self.browser_pgid, self.browser_proc, timeout_sec=2.0)
        if self.browser_pid and not _is_forbidden_target(self.browser_pid):
            _terminate_and_reap_pid(self.browser_pid, timeout_sec=1.0)

        # 3. Cleanup QA Node
        if self.qa_proc is not None:
            if self.qa_proc.stdout:
                try: self.qa_proc.stdout.close()
                except Exception: pass
            if self.qa_proc.stderr:
                try: self.qa_proc.stderr.close()
                except Exception: pass
        if self.qa_pgid and not _is_forbidden_target(self.qa_pgid):
            _terminate_and_reap_group(self.qa_pgid, self.qa_proc, timeout_sec=2.0)

        # 4. Cleanup Server
        if self.server_proc is not None:
            if self.server_proc.stdout:
                try: self.server_proc.stdout.close()
                except Exception: pass
            if self.server_proc.stderr:
                try: self.server_proc.stderr.close()
                except Exception: pass
        if self.server_pgid and not _is_forbidden_target(self.server_pgid):
            _terminate_and_reap_group(self.server_pgid, self.server_proc, timeout_sec=2.0)

        # 5. Cleanup temp dir
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _wait_for_server(self, url: str, timeout_sec: float = 8.0) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                with urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                time.sleep(0.1)
        return False

    def test_upload_ui_missing_tools_blocks_post_and_http500_shows_persistent_error(self) -> None:
        """Run Playwright browser test with ephemeral port and strict identity check."""
        server_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": "0",
            "WEBINAR_OUT_DIR": self.temp_dir,
            "PYTHONPYCACHEPREFIX": "/tmp/pycache",
        }

        # Start isolated fake UI server on ephemeral port 0
        self.server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT), "0"],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.server_pgid = self.server_proc.pid

        # Read actual port and identity from .fake_server_info.json
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

        self.assertTrue(server_info.get("port"), "Fake UI server failed to report port in .fake_server_info.json")
        self.server_port = int(server_info["port"])
        self.assertNotEqual(self.server_port, 8787, "Ephemeral port must never be 8787")
        self.assertTrue(self.server_port > 1024, f"Ephemeral port must be > 1024, got {self.server_port}")

        server_identity = server_info.get("identity", "")
        self.assertTrue(server_identity, "Fake UI server must report identity")

        server_ready = self._wait_for_server(f"http://127.0.0.1:{self.server_port}/api/status", timeout_sec=8.0)
        self.assertTrue(server_ready, f"Fake UI server failed to start within 8 seconds on port {self.server_port}")

        # Run Playwright Node script with minimal environment and process registry path
        client_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": str(self.server_port),
            "UI_IDENTITY": server_identity,
            "NODE_PATH": str(NODE_MODULES),
            "QA_REGISTRY_FILE": str(self.registry_file),
        }

        self.qa_proc = subprocess.Popen(
            [str(NODE_BIN), str(QA_UPLOAD_SCRIPT)],
            env=client_env,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.qa_pgid = self.qa_proc.pid

        qa_stdout, qa_stderr = self.qa_proc.communicate(timeout=60)
        log_dest = BASE_DIR.parent / "outputs" / "antigravity" / "refactor-16" / "qa_upload_ui_behavior.log"
        try:
            log_dest.parent.mkdir(parents=True, exist_ok=True)
            log_dest.write_text(f"STDOUT:\n{qa_stdout}\nSTDERR:\n{qa_stderr}\n", encoding="utf-8")
        except Exception:
            pass
        self.assertEqual(
            self.qa_proc.returncode,
            0,
            f"QA upload behavioral automation failed (code {self.qa_proc.returncode}):\n{qa_stderr}\n{qa_stdout}",
        )

        self.assertIn("[PASSED] Missing tools strictly blocked POST", qa_stdout)
        self.assertIn("[PASSED] HTTP 500 showed persistent uploadError without success text", qa_stdout)


if __name__ == "__main__":
    unittest.main()
