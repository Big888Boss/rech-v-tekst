"""Tracked Automated Browser E2E Test Suite.
Launches the isolated fake UI server on an ephemeral port with fail-closed barriers,
and drives headless Google Chrome via Playwright to verify the entire primary flow
(Start -> Stop -> Process -> Cancel -> Retry -> Inspect -> Transcript -> Export -> Summary)
and all 8 UI states, 3 responsive breakpoints, touch target sizes, and accessibility policies.
Includes rigorous process group supervision and cleanup (Q03) for Node, detached Chromium,
and all child descendants with bounded TERM -> KILL escalation and strict self-preservation.
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

QA_SCRIPT = BASE_DIR / "tests" / "qa_browser_e2e.js"
SERVER_SCRIPT = BASE_DIR / "tests" / "fake_ui_server.py"
REPORT_FILE = BASE_DIR / "work" / "qa" / "browser_qa_report.md"


def _is_forbidden_target(id_val: int | None) -> bool:
    """Check if process or process group ID belongs to test runner, parent, or system."""
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
    try:
        forbidden.add(os.getsid(0))
    except Exception:
        pass
    try:
        ppid = os.getppid()
        if ppid > 1:
            forbidden.add(os.getsid(ppid))
    except Exception:
        pass
    return id_val in forbidden


def _reap_group_zombies(pgid: int) -> None:
    """Reap any zombie child processes belonging to process group pgid."""
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
    """Check if any process in process group pgid is alive."""
    if _is_forbidden_target(pgid):
        return False
    _reap_group_zombies(pgid)
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _is_pid_alive(pid: int | None) -> bool:
    """Check if process pid is alive."""
    if _is_forbidden_target(pid):
        return False
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False
    except (ChildProcessError, ProcessLookupError):
        pass
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _wait_until_dead(pid: int | None, timeout_sec: float = 1.0) -> bool:
    """Wait up to timeout_sec for process to fully exit."""
    if pid is None:
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(0.02)
    return not _is_pid_alive(pid)


def _wait_until_group_dead(pgid: int | None, timeout_sec: float = 1.0) -> bool:
    """Wait up to timeout_sec for process group to fully exit."""
    if pgid is None:
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_group_alive(pgid):
            return True
        time.sleep(0.02)
    return not _is_group_alive(pgid)


def _terminate_and_reap_group(pgid: int | None, proc: subprocess.Popen[str] | None = None, timeout_sec: float = 2.0) -> None:
    """Terminate and reap an entire process group with bounded TERM -> KILL escalation.

    Operates correctly even if the session leader has already exited.
    Strictly avoids signaling current, parent, or unowned groups.
    """
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

    # If group has no survivors, just reap proc handle if needed
    if not _is_group_alive(pgid):
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=0.2)
            except Exception:
                pass
        return

    # 1. Send SIGTERM to entire process group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    # Bounded wait for all processes in the group to exit on SIGTERM
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

    # 2. Escalate to SIGKILL if group still has survivors (e.g. child ignoring SIGTERM)
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

    # Final reap of proc handle
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=0.5)
        except Exception:
            pass
    _reap_group_zombies(pgid)


def _terminate_and_reap_pid(pid: int | None, timeout_sec: float = 1.0) -> None:
    """Terminate and reap a single PID with bounded TERM -> KILL escalation."""
    if _is_forbidden_target(pid) or not _is_pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            return
        time.sleep(0.05)

    if _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

        deadline_kill = time.monotonic() + timeout_sec
        while time.monotonic() < deadline_kill:
            if not _is_pid_alive(pid):
                return
            time.sleep(0.05)


class TestBrowserE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="webinar_browser_qa_")
        self.registry_file = Path(self.temp_dir) / ".qa_process_registry.json"
        self.server_port = 8799
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_pgid: int | None = None
        self.qa_proc: subprocess.Popen[str] | None = None
        self.qa_pgid: int | None = None
        self.browser_pid: int | None = None
        self.browser_pgid: int | None = None
        self.browser_proc: subprocess.Popen[str] | None = None

        # Verify prerequisites
        if not NODE_BIN or not NODE_BIN.exists():
            self.skipTest("Node binary not found. Set NODE_BIN or install node in PATH.")
        if not NODE_MODULES or not NODE_MODULES.exists():
            self.skipTest("node_modules directory not found. Set NODE_PATH.")
        if not QA_SCRIPT.exists():
            self.fail(f"QA script not found at {QA_SCRIPT}")

    def tearDown(self) -> None:
        # 1. Read process registry if written by QA script
        reg_file = getattr(self, "registry_file", None)
        if reg_file is not None and Path(reg_file).exists():
            try:
                reg_data = json.loads(Path(reg_file).read_text(encoding="utf-8"))
                has_owned_creator = getattr(self, "qa_proc", None) is not None or getattr(self, "browser_proc", None) is not None
                if has_owned_creator:
                    if reg_data.get("browser_pid") and not getattr(self, "browser_pid", None):
                        bpid = int(reg_data["browser_pid"])
                        if not _is_forbidden_target(bpid):
                            self.browser_pid = bpid
                    if reg_data.get("browser_pgid") and not getattr(self, "browser_pgid", None):
                        bpgid = int(reg_data["browser_pgid"])
                        if not _is_forbidden_target(bpgid):
                            self.browser_pgid = bpgid
                # Only adopt node_pgid if self.qa_proc is set and matches
                if reg_data.get("node_pgid") and getattr(self, "qa_proc", None) is not None:
                    npgid = int(reg_data["node_pgid"])
                    if not _is_forbidden_target(npgid) and npgid == self.qa_proc.pid:
                        self.qa_pgid = npgid
            except Exception:
                pass

        # 2. Cleanup detached browser group and browser PID
        browser_pgid = getattr(self, "browser_pgid", None)
        browser_proc = getattr(self, "browser_proc", None)
        if browser_pgid and not _is_forbidden_target(browser_pgid):
            _terminate_and_reap_group(browser_pgid, browser_proc, timeout_sec=2.0)
        browser_pid = getattr(self, "browser_pid", None)
        if browser_pid and not _is_forbidden_target(browser_pid):
            _terminate_and_reap_pid(browser_pid, timeout_sec=1.0)

        # 3. Cleanup QA Node process and its entire process group
        qa_proc = getattr(self, "qa_proc", None)
        qa_pgid = getattr(self, "qa_pgid", None) or (qa_proc.pid if qa_proc else None)
        if qa_proc is not None:
            if qa_proc.stdout:
                try:
                    qa_proc.stdout.close()
                except Exception:
                    pass
            if qa_proc.stderr:
                try:
                    qa_proc.stderr.close()
                except Exception:
                    pass
        if qa_pgid and not _is_forbidden_target(qa_pgid):
            _terminate_and_reap_group(qa_pgid, qa_proc, timeout_sec=2.0)

        # 4. Cleanup Server process and its entire process group
        server_proc = getattr(self, "server_proc", None)
        server_pgid = getattr(self, "server_pgid", None) or (server_proc.pid if server_proc else None)
        if server_proc is not None:
            if server_proc.stdout:
                try:
                    server_proc.stdout.close()
                except Exception:
                    pass
            if server_proc.stderr:
                try:
                    server_proc.stderr.close()
                except Exception:
                    pass
        if server_pgid and not _is_forbidden_target(server_pgid):
            _terminate_and_reap_group(server_pgid, server_proc, timeout_sec=2.0)

        # 5. Only after all owned process groups and PIDs are proven dead, clean up temp directory
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        # Reset handles so repeat tearDown is a safe no-op
        self.qa_proc = None
        self.qa_pgid = None
        self.server_proc = None
        self.server_pgid = None
        self.browser_pid = None
        self.browser_pgid = None
        self.browser_proc = None

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

    def test_browser_e2e_primary_flow_and_8_states(self) -> None:
        """Execute full Playwright browser suite in Google Chrome."""
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
        server_identity = server_info["identity"]

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
            # Read registry if written to terminate detached browser
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
            f"Playwright QA automation failed with exit code {qa_code}:\n{qa_stderr}",
        )
        self.assertTrue(REPORT_FILE.exists(), f"Expected report at {REPORT_FILE}")
        report_text = REPORT_FILE.read_text(encoding="utf-8")
        self.assertIn("ALL BROWSER TESTS PASSED PERFECTLY", report_text)
        self.assertIn("State 1 (Ready)", report_text)
        self.assertIn("B01 Verified", report_text)

    def test_teardown_reaps_term_ignoring_child_when_leader_exits_early(self) -> None:
        """Verify tearDown reaps term-ignoring child even if session leader already exited."""
        marker = Path(self.temp_dir) / "child_early.pid"
        code = """import os, sys, time, signal
from pathlib import Path
marker = Path(sys.argv[1])
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    marker.write_text(str(os.getpid()))
    time.sleep(20)
    os._exit(0)
deadline = time.monotonic() + 3
while not marker.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
os._exit(0)
"""
        leader = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(marker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.qa_proc = leader
        self.qa_pgid = leader.pid
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "Child failed to write marker")
        child_pid = int(marker.read_text().strip())
        self.assertTrue(_is_pid_alive(child_pid), "Child should be alive before tearDown")
        leader.wait(timeout=3.0)
        self.assertIsNotNone(leader.poll(), "Leader should have already exited")

        # Execute tearDown
        self.tearDown()

        # Child must be dead, group must be dead, temp directory must be removed
        self.assertTrue(_wait_until_dead(child_pid), "Child must be dead after tearDown")
        self.assertTrue(_wait_until_group_dead(leader.pid), "Process group must be dead after tearDown")
        self.assertFalse(os.path.exists(self.temp_dir), "Temp directory must be removed after tearDown")

    def test_teardown_reaps_term_ignoring_child_when_leader_exits_on_term(self) -> None:
        """Verify tearDown escalates to SIGKILL and reaps term-ignoring child when leader exits on SIGTERM."""
        marker = Path(self.temp_dir) / "child_term.pid"
        code = """import os, sys, time, signal
from pathlib import Path
marker = Path(sys.argv[1])
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    marker.write_text(str(os.getpid()))
    time.sleep(20)
    os._exit(0)
time.sleep(20)
"""
        leader = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(marker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.qa_proc = leader
        self.qa_pgid = leader.pid
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "Child failed to write marker")
        child_pid = int(marker.read_text().strip())
        self.assertTrue(_is_pid_alive(child_pid), "Child should be alive before tearDown")
        self.assertIsNone(leader.poll(), "Leader should be alive before tearDown")

        # Execute tearDown
        self.tearDown()

        # Child must be dead, group must be dead, temp directory must be removed
        self.assertTrue(_wait_until_dead(child_pid), "Child must be dead after tearDown")
        self.assertTrue(_wait_until_group_dead(leader.pid), "Process group must be dead after tearDown")
        self.assertFalse(os.path.exists(self.temp_dir), "Temp directory must be removed after tearDown")

    def test_teardown_reaps_detached_browser_like_group(self) -> None:
        """Verify tearDown reaps a registered detached browser-like process group."""
        marker = Path(self.temp_dir) / "browser_child.pid"
        code = """import os, sys, time, signal
from pathlib import Path
marker = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
marker.write_text(str(os.getpid()))
time.sleep(20)
os._exit(0)
"""
        # Spawn detached browser-like process (own session / PGID)
        self.browser_proc = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(marker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "Detached browser child failed to write marker")
        browser_pid = int(marker.read_text().strip())
        self.assertTrue(_is_pid_alive(browser_pid), "Browser child should be alive before tearDown")

        # Record into process registry file (only browser fields; node empty because no node spawned in this fixture)
        reg_file = Path(self.temp_dir) / ".qa_process_registry.json"
        reg_file.write_text(
            json.dumps({
                "browser_pid": browser_pid,
                "browser_pgid": browser_pid,
            }),
            encoding="utf-8",
        )

        # Execute tearDown
        self.tearDown()

        # Detached browser child must be dead, temp directory removed
        self.assertTrue(_wait_until_dead(browser_pid), "Browser child must be dead after tearDown")
        self.assertTrue(_wait_until_group_dead(browser_pid), "Browser group must be dead after tearDown")
        self.assertFalse(os.path.exists(self.temp_dir), "Temp directory must be removed after tearDown")


if __name__ == "__main__":
    unittest.main()
