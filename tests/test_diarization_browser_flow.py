"""Automated Browser E2E Test Suite for Speaker Diarization Flow (v1.1).
Executes qa_diarization_flow.js via Playwright in Google Chrome against fake_ui_server.py.
Verifies:
- Visible upload control on initial screen
- Diarization options toggle and speaker count selector
- Queue deletion confirmation modal and queue restoration
- Capture -> Transcribe -> Diarize -> Cancel -> Retry -> Completed lifecycle
- Speaker legend, speaker rename, and live badge update in transcript view
- Verification and download of all 5 export formats (TXT, MD, SRT, VTT, JSON)
- Automatic generation of visual artifacts in work/qa/screenshots/
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

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_node_bin() -> Path | None:
    env_bin = os.environ.get("NODE_BIN")
    if env_bin and Path(env_bin).exists():
        return Path(env_bin)
    sys_bin = shutil.which("node")
    if sys_bin:
        return Path(sys_bin)
    home_cache = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node"
    if home_cache.exists():
        return home_cache
    return None


def _resolve_node_modules() -> Path | None:
    env_mod = os.environ.get("NODE_PATH")
    if env_mod and Path(env_mod).exists():
        return Path(env_mod)
    repo_mod = BASE_DIR / "node_modules"
    if repo_mod.exists():
        return repo_mod
    home_mod = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "node_modules"
    if home_mod.exists():
        return home_mod
    return None


NODE_BIN = _resolve_node_bin()
NODE_MODULES = _resolve_node_modules()

QA_SCRIPT = BASE_DIR / "tests" / "qa_diarization_flow.js"
SERVER_SCRIPT = BASE_DIR / "tests" / "fake_ui_server.py"
REPORT_FILE = BASE_DIR / "work" / "qa" / "diarization_flow_qa_report.md"


def _is_forbidden_target(id_val: int | None) -> bool:
    if id_val is None or id_val <= 1:
        return True
    forbidden = {0, 1}
    try:
        forbidden.add(os.getpid())
    except Exception:
        pass
    try:
        forbidden.add(os.getpgrp())
    except Exception:
        pass
    try:
        forbidden.add(os.getppid())
    except Exception:
        pass
    try:
        ppid = os.getppid()
        if ppid > 1:
            forbidden.add(os.getpgid(ppid))
    except Exception:
        pass
    return id_val in forbidden


def _reap_group_zombies(pgid: int) -> None:
    if _is_forbidden_target(pgid):
        return
    while True:
        try:
            wpid, _ = os.waitpid(-pgid, os.WNOHANG)
            if wpid <= 0:
                break
        except (ChildProcessError, ProcessLookupError):
            break


def _is_group_alive(pgid: int | None) -> bool:
    if _is_forbidden_target(pgid):
        return False
    _reap_group_zombies(pgid)
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _terminate_and_reap_group(pgid: int | None, proc: subprocess.Popen[str] | None = None, timeout_sec: float = 2.0) -> None:
    if _is_forbidden_target(pgid):
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
        return

    if not _is_group_alive(pgid):
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=0.2)
            except Exception:
                pass
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=0.05)
            except Exception:
                pass
        if not _is_group_alive(pgid):
            break
        time.sleep(0.05)

    if _is_group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

        deadline_kill = time.monotonic() + timeout_sec
        while time.monotonic() < deadline_kill:
            if proc is not None and proc.poll() is None:
                try:
                    proc.wait(timeout=0.05)
                except Exception:
                    pass
            if not _is_group_alive(pgid):
                break
            time.sleep(0.05)

    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=0.5)
        except Exception:
            pass
    _reap_group_zombies(pgid)


def _terminate_and_reap_pid(pid: int | None, timeout_sec: float = 1.0) -> None:
    if _is_forbidden_target(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class TestDiarizationBrowserFlow(unittest.TestCase):
    def setUp(self) -> None:
        if not NODE_BIN or not NODE_BIN.exists():
            self.skipTest("Node.js runtime not found; skipping browser automation")
        if not NODE_MODULES or not (NODE_MODULES / "playwright").exists():
            self.skipTest("Playwright not found in node_modules; skipping browser automation")

        self.temp_dir = tempfile.mkdtemp(prefix="rech_diar_browser_")
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_pgid: int | None = None
        self.server_port: int | None = None
        self.qa_proc: subprocess.Popen[str] | None = None
        self.qa_pgid: int | None = None
        self.browser_pid: int | None = None
        self.browser_pgid: int | None = None
        self.browser_proc: subprocess.Popen[str] | None = None
        self.registry_file = Path(self.temp_dir) / "qa_process_registry.json"

    def tearDown(self) -> None:
        if self.registry_file.exists():
            try:
                reg_data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                if reg_data.get("browser_pid"):
                    bpid = int(reg_data["browser_pid"])
                    if not _is_forbidden_target(bpid):
                        self.browser_pid = bpid
                if reg_data.get("browser_pgid"):
                    bpgid = int(reg_data["browser_pgid"])
                    if not _is_forbidden_target(bpgid):
                        self.browser_pgid = bpgid
            except Exception:
                pass

        if self.browser_pgid and not _is_forbidden_target(self.browser_pgid):
            _terminate_and_reap_group(self.browser_pgid, self.browser_proc, timeout_sec=2.0)
        if self.browser_pid and not _is_forbidden_target(self.browser_pid):
            _terminate_and_reap_pid(self.browser_pid, timeout_sec=1.0)

        if self.qa_proc is not None:
            if self.qa_proc.stdout:
                try:
                    self.qa_proc.stdout.close()
                except Exception:
                    pass
            if self.qa_proc.stderr:
                try:
                    self.qa_proc.stderr.close()
                except Exception:
                    pass
        qa_pgid = self.qa_pgid or (self.qa_proc.pid if self.qa_proc else None)
        if qa_pgid and not _is_forbidden_target(qa_pgid):
            _terminate_and_reap_group(qa_pgid, self.qa_proc, timeout_sec=2.0)

        if self.server_proc is not None:
            if self.server_proc.stdout:
                try:
                    self.server_proc.stdout.close()
                except Exception:
                    pass
            if self.server_proc.stderr:
                try:
                    self.server_proc.stderr.close()
                except Exception:
                    pass
        server_pgid = self.server_pgid or (self.server_proc.pid if self.server_proc else None)
        if server_pgid and not _is_forbidden_target(server_pgid):
            _terminate_and_reap_group(server_pgid, self.server_proc, timeout_sec=2.0)

        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass

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

    def test_browser_diarization_complete_flow(self) -> None:
        """Execute full Playwright diarization flow test in Google Chrome."""
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

        self.assertTrue(server_info.get("port"), "Fake UI server failed to report port in .fake_server_info.json")
        self.server_port = int(server_info["port"])
        server_identity = server_info["identity"]

        server_ready = self._wait_for_server(f"http://127.0.0.1:{self.server_port}/api/status", timeout_sec=8.0)
        self.assertTrue(server_ready, f"Fake UI server failed to start within 8 seconds on port {self.server_port}")

        client_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": str(self.server_port),
            "UI_IDENTITY": server_identity,
            "NODE_PATH": str(NODE_MODULES),
            "QA_REGISTRY_FILE": str(self.registry_file),
        }

        self.qa_proc = subprocess.Popen(
            [str(NODE_BIN), str(QA_SCRIPT)],
            env=client_env,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.qa_pgid = self.qa_proc.pid

        try:
            qa_stdout, qa_stderr = self.qa_proc.communicate(timeout=120)
            qa_code = self.qa_proc.returncode
        except subprocess.TimeoutExpired:
            if self.registry_file.exists():
                try:
                    reg_data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                    bpgid = reg_data.get("browser_pgid")
                    if bpgid and not _is_forbidden_target(int(bpgid)):
                        self.browser_pgid = int(bpgid)
                    bpid = reg_data.get("browser_pid")
                    if bpid and not _is_forbidden_target(int(bpid)):
                        self.browser_pid = int(bpid)
                except Exception:
                    pass
            if self.browser_pgid:
                _terminate_and_reap_group(self.browser_pgid, timeout_sec=1.5)
            _terminate_and_reap_group(self.qa_pgid, self.qa_proc, timeout_sec=2.0)
            try:
                qa_stdout, qa_stderr = self.qa_proc.communicate(timeout=2.0)
            except Exception:
                qa_stdout, qa_stderr = "TIMEOUT", "QA automation timed out after 120 seconds"
            qa_code = -1

        print(qa_stdout)
        if qa_stderr:
            print(qa_stderr, file=sys.stderr)

        self.assertEqual(
            qa_code,
            0,
            f"Playwright Diarization QA failed with exit code {qa_code}:\n{qa_stderr}",
        )
        self.assertTrue(REPORT_FILE.exists(), f"Expected report at {REPORT_FILE}")
        report_text = REPORT_FILE.read_text(encoding="utf-8")
        self.assertIn("ALL DIARIZATION BROWSER FLOW TESTS PASSED PERFECTLY", report_text)
        self.assertIn("Quick upload control", report_text)
        self.assertIn("Queue removal modal opens", report_text)
        self.assertIn("Speaker rename in legend", report_text)
        self.assertIn("All 5 export formats", report_text)


if __name__ == "__main__":
    unittest.main()
