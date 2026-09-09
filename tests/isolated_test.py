"""Base test case ensuring full isolation of OUT_DIR, UPLOADS_DIR, and LOCK_FILE,
as well as a strict hardware CLI and external provider deny/mock barrier.
Guarantees clean process reaping and rollback on setup failure (H1, H2, H3).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from recorder.constants import BASE_DIR, get_data_root, set_data_root


_ORIG_POPEN = subprocess.Popen
_ORIG_RUN = subprocess.run
_ALLOWED_FAKE_EXECUTABLES: set[str] = set()
_CURRENT_TEST: Any = None


def is_group_alive(pgid: int) -> bool:
    """Check if any process belonging to process group is still alive."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _deny_barrier_check(args: Any) -> None:
    """Check command arguments and block any real hardware or external provider invocations."""
    cmd_list: list[str] = []
    if isinstance(args, (list, tuple)):
        cmd_list = [str(x) for x in args]
    elif isinstance(args, str):
        cmd_list = args.split()

    if not cmd_list:
        return

    # Determine executable being invoked
    exe_token = cmd_list[0]
    if "recorder.guardian" in cmd_list and "--cmd-json" in cmd_list:
        try:
            c_idx = cmd_list.index("--cmd-json")
            inner_cmd = json.loads(cmd_list[c_idx + 1])
            if isinstance(inner_cmd, list) and inner_cmd:
                if inner_cmd[0] == "caffeinate" and len(inner_cmd) > 2 and inner_cmd[1] == "-i":
                    target_exe = inner_cmd[2]
                else:
                    target_exe = inner_cmd[0]
            else:
                target_exe = exe_token
        except Exception:
            target_exe = exe_token
    elif exe_token == "caffeinate" and len(cmd_list) > 2 and cmd_list[1] == "-i":
        target_exe = cmd_list[2]
    else:
        target_exe = exe_token

    try:
        resolved_exe = str(Path(target_exe).resolve())
    except Exception:
        resolved_exe = target_exe

    # Check if target_exe is an explicitly registered fake executable inside test fixture
    is_allowed_fake = (
        resolved_exe in _ALLOWED_FAKE_EXECUTABLES
        and Path(resolved_exe).is_file()
        and os.access(resolved_exe, os.X_OK)
    )

    if not is_allowed_fake:
        # Check for avfoundation hardware probe
        for i, token in enumerate(cmd_list):
            if token == "-f" and i + 1 < len(cmd_list) and cmd_list[i + 1] == "avfoundation":
                raise RuntimeError(f"Test safety barrier intercepted disallowed avfoundation call: {cmd_list}")
            if "avfoundation" in token:
                raise RuntimeError(f"Test safety barrier intercepted disallowed avfoundation call: {cmd_list}")

        exe_name = Path(target_exe).name.lower()
        disallowed = {"claude", "codex", "whisper-cli"}
        if exe_name in disallowed:
            raise RuntimeError(f"Test safety barrier intercepted disallowed external tool call: {cmd_list}")


class GuardedPopen(_ORIG_POPEN):  # type: ignore[misc]
    def __init__(self, args: Any, *a: Any, **kw: Any) -> None:
        _deny_barrier_check(args)
        super().__init__(args, *a, **kw)
        try:
            self.saved_pgid = os.getpgid(self.pid)
        except (ProcessLookupError, OSError):
            self.saved_pgid = None
        if _CURRENT_TEST is not None and hasattr(_CURRENT_TEST, "_tracked_processes"):
            _CURRENT_TEST._tracked_processes.append(self)


def guarded_run(args: Any, *a: Any, **kw: Any) -> Any:
    _deny_barrier_check(args)
    return _ORIG_RUN(args, *a, **kw)


class IsolatedTestCase(unittest.TestCase):
    """Base test case ensuring tests run against an isolated temporary directory,
    never touch project out/, and never access real audio hardware or external provider APIs.
    Guarantees LIFO rollback on setUp error and bounded group termination before teardown.
    """

    @classmethod
    def register_allowed_fake_executable(cls, path: str | Path) -> None:
        p = str(Path(path).resolve())
        _ALLOWED_FAKE_EXECUTABLES.add(p)

    def setUp(self) -> None:
        super().setUp()
        global _CURRENT_TEST
        _CURRENT_TEST = self

        # 1. Guaranteed root restoration (registered first so executes last in LIFO)
        self._prev_root = get_data_root()
        self.addCleanup(set_data_root, self._prev_root)

        # 2. Guaranteed temp directory cleanup
        self._isolated_temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._isolated_temp_dir.cleanup)

        # 3. Apply isolated root
        self.data_root = Path(self._isolated_temp_dir.name)
        set_data_root(self.data_root)

        # 4. Guaranteed environment restoration
        self._orig_env = dict(os.environ)
        def _restore_env() -> None:
            os.environ.clear()
            os.environ.update(self._orig_env)
        self.addCleanup(_restore_env)

        os.environ["WEBINAR_OUT_DIR"] = str(self.data_root)
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_API_KEY"):
            os.environ.pop(k, None)

        # 5. Guaranteed patches cleanup
        self._patch_popen = patch("subprocess.Popen", new=GuardedPopen)
        self._patch_popen.start()
        self.addCleanup(self._patch_popen.stop)

        self._patch_run = patch("subprocess.run", new=guarded_run)
        self._patch_run.start()
        self.addCleanup(self._patch_run.stop)

        self._fake_devices = [
            {"index": 0, "name": "Fake Mock Mic", "kind": "mic"},
            {"index": 1, "name": "Fake BlackHole 2ch", "kind": "blackhole"},
        ]
        self._patch_list_devices = patch(
            "recorder.device.list_audio_devices",
            return_value=(self._fake_devices, "")
        )
        self._patch_list_devices.start()
        self.addCleanup(self._patch_list_devices.stop)

        self._patch_probe = patch(
            "recorder.device.probe_device_stream_info",
            return_value={"sample_rate": 48000, "channels": 2, "format": "pcm_s16le", "probed": True}
        )
        self._patch_probe.start()
        self.addCleanup(self._patch_probe.stop)

        self._patch_volume = patch(
            "recorder.device.run_volume_check",
            return_value={"status": "ok", "max_volume_db": -12.0, "mean_volume_db": -24.0}
        )
        self._patch_volume.start()
        self.addCleanup(self._patch_volume.stop)

        # 6. Tracked process cleanup: runs FIRST in LIFO cleanups (BEFORE temp dir is removed!)
        self._tracked_processes: list[Any] = []
        self.addCleanup(self._reap_all_tracked_processes)

    def _reap_all_tracked_processes(self) -> None:
        global _CURRENT_TEST
        _CURRENT_TEST = None
        for target in list(reversed(self._tracked_processes)):
            self._reap_target(target)

    @staticmethod
    def _reap_target(target: Any) -> None:
        try:
            if isinstance(target, int):
                pgid = target
                if pgid > 0 and pgid != os.getpgrp() and is_group_alive(pgid):
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except OSError:
                        pass
                    t_end = time.time() + 0.2
                    while time.time() < t_end and is_group_alive(pgid):
                        time.sleep(0.02)
                    if is_group_alive(pgid):
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except OSError:
                            pass
            elif hasattr(target, "pid"):
                proc = target
                # H1: Even if leader process has exited (poll() is not None), kill entire group via saved_pgid!
                pgid = getattr(proc, "saved_pgid", None)
                if pgid is None:
                    try:
                        pgid = os.getpgid(proc.pid)
                    except (ProcessLookupError, OSError):
                        pgid = None

                if pgid is not None and pgid > 0 and pgid != os.getpgrp() and is_group_alive(pgid):
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except OSError:
                        pass
                    t_end = time.time() + 0.3
                    while time.time() < t_end and is_group_alive(pgid):
                        time.sleep(0.02)
                    if is_group_alive(pgid):
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except OSError:
                            pass
                    t_end = time.time() + 0.5
                    while time.time() < t_end and is_group_alive(pgid):
                        time.sleep(0.02)
                    if is_group_alive(pgid):
                        raise RuntimeError(f"Harness cleanup failed to reap process group {pgid}")

                if proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.3)
                    except Exception:
                        pass
            elif hasattr(target, "active_process") and target.active_process:
                IsolatedTestCase._reap_target(target.active_process)
        except Exception:
            pass

    def add_process_cleanup(self, target: Any) -> None:
        """Register guaranteed process / process group cleanup hook even if test assertion fails."""
        self._tracked_processes.append(target)
        self.addCleanup(self._reap_target, target)
