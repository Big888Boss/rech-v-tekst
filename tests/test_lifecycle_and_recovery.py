"""Unit and integration tests for process lifecycle, bounded recovery, and lock cleanup (L01-L07)."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.capture import CAPTURE_MANAGER, is_process_alive_and_ours, stop_and_reap_process_group
from recorder.cli import cli_record
from recorder.constants import (
    OUT_DIR,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_RECORDING,
)
from recorder.lock import GLOBAL_LOCK, LockBusyError
from recorder.session import create_session, load_session, retry_session, save_session
from recorder.storage import get_session_dir, safe_make_dir
from recorder.transcribe import TRANSCRIBE_MANAGER
from tests.isolated_test import IsolatedTestCase


class TestLifecycleAndRecovery(IsolatedTestCase):
    def test_l01_active_recording_marker_blocks_concurrent_start(self) -> None:
        marker_file = self.data_root / ".active_capture.json"
        with patch("recorder.capture.is_process_alive_and_ours", return_value=True):
            marker_file.write_text(json.dumps({
                "session_id": "active_sess_1",
                "pid": os.getpid(),
                "token": "tok123",
                "start_time": time.time(),
            }), encoding="utf-8")

            with self.assertRaises(LockBusyError) as ctx:
                CAPTURE_MANAGER.start_capture("new_sess_2")
            self.assertIn("Another capture is actively recording", str(ctx.exception))

    def test_l02_reused_or_unrelated_pid_never_signaled(self) -> None:
        manifest = create_session("sess_l02_victim")
        manifest.status = STATE_RECORDING
        manifest.pid = 987654
        save_session(manifest)

        with patch("subprocess.run") as mock_ps, patch("os.kill") as mock_kill, patch("os.killpg") as mock_killpg:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "ffmpeg -i /home/user/some_other_video.mp4"
            mock_ps.return_value = mock_res
            mock_kill.return_value = None

            self.assertFalse(is_process_alive_and_ours(987654, expected_session_id="sess_l02_victim"))

            stopped = CAPTURE_MANAGER.stop_capture("sess_l02_victim")
            mock_killpg.assert_not_called()
            self.assertEqual(stopped.status, STATE_INTERRUPTED)

    def test_l03_duplicate_session_releases_lock_cleanly(self) -> None:
        create_session("sess_duplicate")

        with self.assertRaises(Exception):
            CAPTURE_MANAGER.start_capture("sess_duplicate")

        GLOBAL_LOCK.acquire("test", "sess_check")
        GLOBAL_LOCK.release()

    def test_l03_transcribe_missing_manifest_releases_lock_and_resets_state(self) -> None:
        with self.assertRaises(Exception):
            TRANSCRIBE_MANAGER.transcribe_session("non_existent_session_id")

        self.assertFalse(TRANSCRIBE_MANAGER.progress["is_running"])
        self.assertIsNone(TRANSCRIBE_MANAGER.active_session_id)

        GLOBAL_LOCK.acquire("test", "sess_check_transcribe")
        GLOBAL_LOCK.release()

    def test_l04_capture_redirects_logs_to_file_not_pipe(self) -> None:
        dummy_script = self.data_root / "fake_ffmpeg.sh"
        dummy_script.write_text("#!/bin/sh\necho start\nsleep 5\n", encoding="utf-8")
        dummy_script.chmod(0o755)
        self.register_allowed_fake_executable(dummy_script)

        with patch("recorder.capture.find_device", return_value={"index": 0, "name": "FakeDev"}), \
             patch("recorder.capture.probe_device_stream_info", return_value={"sample_rate": 48000, "channels": 2}):
            manifest = CAPTURE_MANAGER.start_capture("sess_l04_log", ffmpeg_bin=str(dummy_script))
            self.add_process_cleanup(CAPTURE_MANAGER)

            sess_dir = get_session_dir("sess_l04_log")
            log_file = sess_dir / "capture.log"

            self.assertIsNone(CAPTURE_MANAGER.active_process.stdout)
            self.assertIsNone(CAPTURE_MANAGER.active_process.stderr)
            self.assertTrue(log_file.exists())

            CAPTURE_MANAGER.stop_capture("sess_l04_log")

    def test_l05_bounded_stop_and_reap_escalates(self) -> None:
        trap_script = self.data_root / "trap_script.sh"
        trap_script.write_text("#!/bin/sh\ntrap '' INT\nwhile true; do sleep 0.1; done\n", encoding="utf-8")
        trap_script.chmod(0o755)
        self.register_allowed_fake_executable(trap_script)

        proc = subprocess.Popen(
            [str(trap_script)],
            start_new_session=True,
        )
        self.add_process_cleanup(proc)

        with patch("recorder.capture.is_process_alive_and_ours", return_value=True):
            t0 = time.time()
            stop_and_reap_process_group(proc, timeout_sec=0.5)
            t1 = time.time()

            self.assertLess(t1 - t0, 6.0)
            self.assertIsNotNone(proc.poll())

    def test_l06_cli_record_exits_immediately_on_unexpected_exit(self) -> None:
        args = argparse.Namespace(
            kind="zoom",
            session="sess_l06_fail",
            device=None,
            list_devices=False,
            preflight=False,
        )

        with patch("recorder.capture.find_device", return_value={"index": 0, "name": "FakeDev"}), \
             patch("recorder.capture.probe_device_stream_info", return_value={"sample_rate": 48000, "channels": 2}), \
             patch("recorder.cli.CAPTURE_MANAGER.start_capture") as mock_start:

            fake_proc = subprocess.Popen(["/bin/sh", "-c", "exit 42"])
            fake_proc.wait()

            def fake_start(*a, **kw):
                CAPTURE_MANAGER.active_session_id = "sess_l06_fail"
                CAPTURE_MANAGER.active_process = fake_proc
                m = create_session("sess_l06_fail")
                m.status = STATE_FAILED
                m.error_message = "Capture process failed (exit code 42)"
                save_session(m)
                return m

            mock_start.side_effect = fake_start

            exit_code = cli_record(args)
            self.assertEqual(exit_code, 1)

    def test_l07_startup_reconciles_processing_session_to_interrupted(self) -> None:
        manifest = create_session("sess_l07_processing")
        manifest.status = STATE_PROCESSING
        manifest.pid = 999998
        save_session(manifest)

        reconciled = CAPTURE_MANAGER.reconcile_on_startup()
        self.assertIn("sess_l07_processing", reconciled)

        updated = load_session("sess_l07_processing")
        self.assertEqual(updated.status, STATE_INTERRUPTED)
        self.assertIn("interrupted", updated.error_message.lower())

        retried = retry_session("sess_l07_processing")
        self.assertEqual(retried.status, STATE_READY)
        self.assertIsNone(retried.error_message)


if __name__ == "__main__":
    unittest.main()
