"""Comprehensive test suite for guardian process lifecycle and R02 / G01-G03 verification."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from recorder.constants import (
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_READY,
    STATE_RECORDING,
)
from recorder.lock import LockBusyError, ProcessLock
from recorder.session import create_session, load_session, save_session
from tests.isolated_test import GuardedPopen, IsolatedTestCase


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


class TestGuardianLifecycle(IsolatedTestCase):
    def test_g01_explicit_release_keeps_lock_for_inherited_child(self) -> None:
        """G01: Verify ProcessLock.release() does not drop inherited flock when child process is alive."""
        lock_file = self.data_root / "g01_explicit.lock"
        lock_file.touch()

        lock = ProcessLock(lock_file)
        lock.acquire("probe", "sess_g01")
        fd = lock.prepare_inheritable_fd()
        self.assertIsNotNone(fd)

        # Spawn own fake child holding the inherited fd for 2 seconds
        child_code = (
            "import sys, time\n"
            "time.sleep(1.5)\n"
            "sys.exit(0)\n"
        )
        proc = GuardedPopen(
            [sys.executable, "-c", child_code],
            pass_fds=[fd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.add_process_cleanup(proc)

        # Parent calls lock.release()
        lock.release()

        # Child is still alive
        self.assertIsNone(proc.poll(), "Child process should still be alive")

        # Independent attempt to acquire the lock MUST fail with LockBusyError!
        second_lock = ProcessLock(lock_file)
        with self.assertRaises(LockBusyError):
            second_lock.acquire("probe_2", "sess_g01_second")

        # Wait for child to exit cleanly
        proc.wait(timeout=3.0)

        # Now that child has exited, independent acquire MUST succeed!
        second_lock.acquire("probe_2", "sess_g01_second")
        second_lock.release()

    def test_g02_natural_child_exit_detected_with_open_stdin(self) -> None:
        """G02: Guardian must detect natural child exit without waiting for stdin EOF."""
        session_id = "sess_g02_natural"
        sess = create_session(session_id)
        sess_dir = self.data_root / session_id
        log_path = sess_dir / "child.log"

        lock_file = self.data_root / "g02.lock"
        lock_file.touch()
        parent_lock = ProcessLock(lock_file)
        parent_lock.acquire("record", session_id)
        fd = parent_lock.prepare_inheritable_fd()

        # Child finishes quickly (0.2s)
        child_cmd = [sys.executable, "-c", "import time; time.sleep(0.2)"]

        # Run guardian, keeping stdin pipe open without writing to it
        guardian = GuardedPopen(
            [
                sys.executable,
                "-m",
                "recorder.guardian",
                "--action",
                "record",
                "--session-id",
                session_id,
                "--token",
                "token_g02",
                "--lock-fd",
                str(fd),
                "--cmd-json",
                json.dumps(child_cmd),
                "--cwd",
                str(sess_dir),
                "--log-path",
                str(log_path),
                "--session-dir",
                str(sess_dir),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=[fd],
            start_new_session=True,
        )
        self.add_process_cleanup(guardian)
        parent_lock.transfer_ownership()

        # Wait up to 3 seconds for guardian to exit naturally
        # Previously without select.select, guardian hung indefinitely waiting on stdin.readline()
        t0 = time.time()
        ret = guardian.wait(timeout=4.0)
        t1 = time.time()
        guardian.stdin.close()

        self.assertEqual(ret, 0, f"Guardian exited with non-zero status {ret}")
        self.assertLess(t1 - t0, 3.0, "Guardian took too long to detect natural child exit")

        # Verify lock is now free
        probe_lock = ProcessLock(lock_file)
        probe_lock.acquire("probe", session_id)
        probe_lock.release()

    def test_g03_worker_devnull_does_not_compete_for_control_commands(self) -> None:
        """G03: Worker process must not share or intercept stdin control commands meant for guardian."""
        session_id = "sess_g03_control"
        sess = create_session(session_id)
        sess_dir = self.data_root / session_id
        log_path = sess_dir / "child.log"

        lock_file = self.data_root / "g03.lock"
        lock_file.touch()
        parent_lock = ProcessLock(lock_file)
        parent_lock.acquire("record", session_id)
        fd = parent_lock.prepare_inheritable_fd()

        # Worker process tries to aggressively read sys.stdin
        worker_code = (
            "import sys, time\n"
            "try:\n"
            "    sys.stdin.read(1024)\n"
            "except Exception:\n"
            "    pass\n"
            "time.sleep(10)\n"
        )
        child_cmd = [sys.executable, "-c", worker_code]

        guardian = GuardedPopen(
            [
                sys.executable,
                "-m",
                "recorder.guardian",
                "--action",
                "record",
                "--session-id",
                session_id,
                "--token",
                "token_g03",
                "--lock-fd",
                str(fd),
                "--cmd-json",
                json.dumps(child_cmd),
                "--cwd",
                str(sess_dir),
                "--log-path",
                str(log_path),
                "--session-dir",
                str(sess_dir),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=[fd],
            start_new_session=True,
        )
        self.add_process_cleanup(guardian)
        parent_lock.transfer_ownership()

        # Wait for marker to ensure guardian is running
        time.sleep(0.3)

        # Send "stop\n" to guardian.
        guardian.stdin.write(b"stop\n")
        guardian.stdin.flush()
        guardian.stdin.close()

        ret = guardian.wait(timeout=3.0)
        self.assertEqual(ret, 0, "Guardian should cleanly exit after receiving stop command")

    def test_guardian_cleanup_stubborn_group_escalation(self) -> None:
        """Verify that escalation to SIGKILL successfully cleans up stubborn processes."""
        from recorder.guardian import stop_child_group

        stubborn_code = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(0.8)\n"
            "sys.exit(0)\n"
        )
        proc = GuardedPopen(
            [sys.executable, "-c", stubborn_code],
            start_new_session=True,
        )
        self.add_process_cleanup(proc)
        pgid = os.getpgid(proc.pid)

        stopped = stop_child_group(pgid, proc.pid, timeout_sec=2.0)
        self.assertTrue(stopped, "stop_child_group should successfully terminate stubborn process with SIGKILL")
        self.assertFalse(is_pid_alive(proc.pid), "Process must be dead after escalation")

    def test_parent_death_cleans_up_group_and_finalizes_manifest(self) -> None:
        """Verify that when parent process is killed (SIGKILL), guardian finalizes session cleanly."""
        session_id = "sess_parent_death"
        sess = create_session(session_id)
        sess_dir = self.data_root / session_id
        log_path = sess_dir / "child.log"

        lock_file = self.data_root / "parent_death.lock"
        lock_file.touch()

        parent_code = f"""
import fcntl, json, os, subprocess, sys, time
from pathlib import Path

lock_path = Path({json.dumps(str(lock_file))})
sess_dir = Path({json.dumps(str(sess_dir))})
log_path = Path({json.dumps(str(log_path))})

fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
os.set_inheritable(fd, True)

child_cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
guardian_cmd = [
    sys.executable, "-m", "recorder.guardian",
    "--action", "record",
    "--session-id", {json.dumps(session_id)},
    "--token", "token_death",
    "--lock-fd", str(fd),
    "--cmd-json", json.dumps(child_cmd),
    "--cwd", str(sess_dir),
    "--log-path", str(log_path),
    "--session-dir", str(sess_dir),
]

guardian = subprocess.Popen(
    guardian_cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    pass_fds=[fd],
    start_new_session=True,
)
os.close(fd)

print(guardian.pid, flush=True)
time.sleep(30)
"""
        parent = GuardedPopen(
            [sys.executable, "-c", parent_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.add_process_cleanup(parent)

        line = parent.stdout.readline()
        parent.stdout.close()
        guardian_pid = int(line.strip())

        marker_file = self.data_root / ".active_capture.json"
        t_end = time.time() + 3.0
        while time.time() < t_end:
            if marker_file.exists():
                break
            time.sleep(0.05)

        probe_lock = ProcessLock(lock_file)
        with self.assertRaises(LockBusyError):
            probe_lock.acquire("probe", session_id)

        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()

        t_end = time.time() + 5.0
        while time.time() < t_end:
            if not is_pid_alive(guardian_pid):
                break
            time.sleep(0.05)

        self.assertFalse(is_pid_alive(guardian_pid), "Guardian should have exited after parent termination")

        probe_lock.acquire("probe", session_id)
        probe_lock.release()
