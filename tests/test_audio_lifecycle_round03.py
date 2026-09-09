"""Regression tests for Round 3 audio and lifecycle requirements (R01, R02, R09, R10)."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder.capture import CAPTURE_MANAGER, is_group_alive, is_process_alive_and_ours
from recorder.constants import (
    STATE_COMPLETED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_RECORDING,
)
from recorder.lock import GLOBAL_LOCK, LockBusyError, ProcessLock
from recorder.session import create_session, load_session, save_session
from recorder.transcribe import TRANSCRIBE_MANAGER
from tests.isolated_test import GuardedPopen, IsolatedTestCase


def _mock_valid_tools():
    from recorder.media_tools import MediaToolsStatus
    return MediaToolsStatus(
        ffmpeg_path="/usr/bin/ffmpeg",
        ffprobe_path="/usr/bin/ffprobe",
        ffmpeg=True,
        ffprobe=True,
        ready=True,
        missing=(),
        remediation=None,
        whisper=True,
        model=True,
        whisper_path="/mock/whisper-cli",
        model_path="/mock/model.bin",
        whisper_version="whisper.cpp version: 1.9.3",
        model_size_bytes=1000,
        model_state="verified",
        model_error=None,
        whisper_error=None,
    )


class TestAudioLifecycleRound03(IsolatedTestCase):
    def test_r01_pipe_deadlock_eliminated_with_large_stderr(self) -> None:
        """Verify that a child process producing large stderr output (>100KB) does not deadlock."""
        # Python child script writing 256KB to stderr and exiting cleanly
        child_script = (
            "import sys\n"
            "data = 'A' * 65536\n"
            "for _ in range(4):\n"
            "    sys.stderr.write(data)\n"
            "sys.stderr.flush()\n"
            "sys.exit(0)\n"
        )
        cmd = [sys.executable, "-c", child_script]

        t0 = time.time()
        ret, stdout, stderr = TRANSCRIBE_MANAGER._run_process_isolated(cmd, timeout=5.0)
        t1 = time.time()

        self.assertEqual(ret, 0)
        self.assertEqual(len(stderr), 262144)
        self.assertLess(t1 - t0, 2.0)

    def test_r02_kernel_flock_held_by_child_across_parent_death(self) -> None:
        """Verify that child process holding inherited lock_fd keeps kernel flock active across parent exit."""
        lock_file = self.data_root / "test_kernel_flock.lock"
        lock_file.touch()

        # Parent process opens and flocks the file
        parent_lock = ProcessLock(lock_file)
        parent_lock.acquire("test", "sess_flock")
        lock_fd = parent_lock.prepare_inheritable_fd()
        self.assertIsNotNone(lock_fd)

        # Child process keeps the descriptor open for a short time
        child_code = (
            "import sys, time\n"
            "time.sleep(1.5)\n"
            "sys.exit(0)\n"
        )
        child = GuardedPopen(
            [sys.executable, "-c", child_code],
            pass_fds=[lock_fd],
            start_new_session=True,
        )
        self.add_process_cleanup(child)

        # Simulate parent exit by closing parent's local descriptor
        os.close(parent_lock._fd)
        parent_lock._fd = None

        # While child is alive, second acquire on the same lock file MUST fail with LockBusyError!
        second_lock = ProcessLock(lock_file)
        with self.assertRaises(LockBusyError):
            second_lock.acquire("second", "sess_flock_2")

        # Wait for child to exit
        child.wait(timeout=3.0)

        # Now that child has exited, second acquire MUST succeed!
        second_lock.acquire("second", "sess_flock_2")
        second_lock.release()

    def test_r09_legacy_session_chunks_in_session_dir_are_processed(self) -> None:
        """Verify that legacy sessions with chunk_000.wav in session root are processed without missing chunk errors."""
        sess = create_session("sess_legacy_root_chunks")
        sess_dir = self.data_root / "sess_legacy_root_chunks"
        chunk_file = sess_dir / "chunk_000.wav"

        # Create a valid synthetic 16kHz mono WAV file in session_dir
        import struct, wave
        with wave.open(str(chunk_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            samples = [0] * 16000
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        sess.normalized_chunks = [{
            "index": 0,
            "filename": "chunk_000.wav",
            "duration_sec": 1.0,
            "sample_rate": 16000,
            "channels": 1,
            "size_bytes": chunk_file.stat().st_size,
        }]
        sess.status = STATE_READY
        save_session(sess)

        fake_model = self.data_root / "fake_model.bin"
        fake_model.touch()

        # Mock whisper runner to return success
        with patch.object(TRANSCRIBE_MANAGER, "_run_whisper_chunk") as mock_transcribe, \
             patch("recorder.media_tools.get_media_tools_status", return_value=_mock_valid_tools()):
            mock_transcribe.return_value = ("Test transcription text", [{"from_sec": 0.0, "to_sec": 1.0, "text": "Test"}])

            manifest = TRANSCRIBE_MANAGER.transcribe_session("sess_legacy_root_chunks", model_path=fake_model)
            self.assertEqual(manifest.status, STATE_COMPLETED)
            self.assertTrue(manifest.has_transcript)
            # Verify the chunk was safely copied to normalized/ as well
            self.assertTrue((sess_dir / "normalized" / "chunk_000.wav").exists())
            # And original chunk in sess_dir is preserved
            self.assertTrue(chunk_file.exists())

    def test_r10_whisper_missing_or_invalid_json_is_rejected(self) -> None:
        """Verify that transcription fails if Whisper produces text without valid JSON timestamps."""
        sess = create_session("sess_r10_invalid_json")
        sess_dir = self.data_root / "sess_r10_invalid_json"
        norm_dir = sess_dir / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)
        chunk_file = norm_dir / "chunk_000.wav"

        import struct, wave
        with wave.open(str(chunk_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            samples = [0] * 16000
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        sess.normalized_chunks = [{
            "index": 0,
            "filename": "chunk_000.wav",
            "duration_sec": 1.0,
            "sample_rate": 16000,
            "channels": 1,
            "size_bytes": chunk_file.stat().st_size,
        }]
        sess.status = STATE_READY
        save_session(sess)

        fake_model = self.data_root / "fake_model.bin"
        fake_model.touch()

        # Mock _run_process_isolated to simulate Whisper exiting 0 with only TXT (no JSON)
        def mock_run_no_json(cmd, timeout):
            # Find the output base from cmd
            out_base = Path(cmd[cmd.index("-of") + 1])
            txt_file = out_base.parent / f"{out_base.name}.txt"
            txt_file.write_text("Extracted words", encoding="utf-8")
            # Do NOT create json file!
            return 0, "", ""

        with patch.object(TRANSCRIBE_MANAGER, "_run_process_isolated", side_effect=mock_run_no_json), \
             patch("recorder.media_tools.get_media_tools_status", return_value=_mock_valid_tools()):
            with self.assertRaises(RuntimeError) as ctx:
                TRANSCRIBE_MANAGER.transcribe_session("sess_r10_invalid_json", model_path=fake_model)
            self.assertIn("JSON timestamp output", str(ctx.exception))

    def test_f01_guardian_supervises_and_reaps_on_parent_death(self) -> None:
        """F01: Real guardian supervisor cleans up worker and frees lock when parent dies."""
        from recorder.guardian import launch_guarded_process

        sess_id = "sess_f01_guardian_parent_death"
        manifest = create_session(sess_id)
        sess_dir = self.data_root / sess_id
        manifest.status = STATE_RECORDING
        save_session(manifest)

        # Parent process acquires lock
        GLOBAL_LOCK.acquire("record", sess_id)
        lock_fd = GLOBAL_LOCK.prepare_inheritable_fd()

        # Sleeping child script
        child_script = "import time\ntime.sleep(30)\n"
        cmd = [sys.executable, "-c", child_script]

        # Launch guardian
        guardian_proc = launch_guarded_process(
            cmd,
            action="record",
            session_id=sess_id,
            token="tok_f01_test",
            cwd=sess_dir,
            session_dir=sess_dir,
            lock_fd=lock_fd,
        )
        self.add_process_cleanup(guardian_proc)
        GLOBAL_LOCK.transfer_ownership()

        # Wait for guardian to spawn child and write marker
        time.sleep(0.5)
        marker_file = self.data_root / ".active_capture.json"
        self.assertTrue(marker_file.exists())
        marker_data = json.loads(marker_file.read_text(encoding="utf-8"))
        child_pid = marker_data["pid"]

        # Simulate parent death by closing guardian's stdin (mimicking EOF when parent dies)
        guardian_proc.stdin.close()

        # Wait for guardian to detect EOF and cleanly terminate
        guardian_proc.wait(timeout=4.0)

        # 1. Child worker process must be terminated
        self.assertFalse(is_group_alive(child_pid))

        # 2. Lock must be released and re-acquirable
        GLOBAL_LOCK.acquire("test", "sess_after_guardian")
        GLOBAL_LOCK.release()

    def test_f02_transcribe_cancel_during_normalization_stops_worker(self) -> None:
        """F02: Cancelling transcription while normalize_chunk is running reaps the worker immediately."""
        from tests.test_audio_fidelity_and_normalization import create_synthetic_wav
        import threading

        sess_id = "sess_f02_cancel_norm"
        manifest = create_session(sess_id)
        sess_dir = self.data_root / sess_id
        raw_dir = sess_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / "raw_chunk_000.wav"
        create_synthetic_wav(raw_file, duration_sec=2.0, sample_rate=48000, channels=2)

        manifest.raw_chunks = [{
            "index": 0,
            "filename": "raw_chunk_000.wav",
            "duration_sec": 2.0,
            "sample_rate": 48000,
            "channels": 2,
            "size_bytes": raw_file.stat().st_size,
            "closed": True,
        }]
        manifest.status = STATE_READY
        save_session(manifest)

        fake_model = self.data_root / "fake_model.bin"
        fake_model.touch()

        # Create a fake ffmpeg script that writes its PID to marker and sleeps
        child_pid_marker = self.data_root / "fake_ffmpeg_child.pid"
        fake_ffmpeg = self.data_root / "fake_ffmpeg_sleep.sh"
        fake_ffmpeg.write_text(
            f"#!/bin/sh\necho $$ > \"{child_pid_marker}\"\nexec sleep 30\n",
            encoding="utf-8",
        )
        fake_ffmpeg.chmod(0o755)
        self.register_allowed_fake_executable(fake_ffmpeg)

        error_holder = []
        result_holder = []
        def _run_transcribe():
            try:
                res = TRANSCRIBE_MANAGER.transcribe_session(
                    sess_id,
                    model_path=fake_model,
                    ffmpeg_bin=str(fake_ffmpeg),
                )
                result_holder.append(res)
            except Exception as exc:
                error_holder.append(exc)

        t = threading.Thread(target=_run_transcribe)
        t.start()

        # Wait until normalize guardian process is started and child has written PID
        proc = None
        child_pid = None
        for _ in range(60):
            with TRANSCRIBE_MANAGER._lock:
                proc = TRANSCRIBE_MANAGER._active_proc
            if proc is not None and child_pid_marker.exists():
                try:
                    txt = child_pid_marker.read_text(encoding="utf-8").strip()
                    if txt:
                        child_pid = int(txt)
                        break
                except Exception:
                    pass
            time.sleep(0.05)

        self.assertIsNotNone(proc, "Active proc should be registered during normalization")
        self.assertIsNotNone(child_pid, "Child worker PID must be registered")
        self.assertTrue(is_group_alive(child_pid), "Child worker must be running before cancellation")

        # Verify kernel lock is busy while child is alive
        probe_lock = ProcessLock(self.data_root / ".recorder.lock")
        with self.assertRaises(LockBusyError):
            probe_lock.acquire("probe", "sess_probe")

        # Trigger cancellation
        TRANSCRIBE_MANAGER.cancel_transcription()
        t.join(timeout=4.0)
        self.assertFalse(t.is_alive(), "Transcribe thread must terminate promptly upon cancellation")
        self.assertTrue(
            any(isinstance(e, InterruptedError) for e in error_holder)
            or any(r.status == STATE_INTERRUPTED for r in result_holder),
            "Operation must be interrupted upon cancellation",
        )
        self.assertIsNotNone(proc.poll(), "Guardian supervisor process must be reaped")
        self.assertFalse(is_group_alive(child_pid), "Real child worker process must be terminated")

        # Verify kernel lock is freed after child is terminated
        probe_lock.acquire("probe", "sess_probe")
        probe_lock.release()

    def test_f03_source_bound_validation_cannot_be_bypassed_by_stale_normalized(self) -> None:
        """F03: Stale normalized record must not bypass source validation in transcribe_session."""
        from tests.test_audio_fidelity_and_normalization import create_synthetic_wav

        sess_id = "sess_f03_stale_norm"
        manifest = create_session(sess_id)
        sess_dir = self.data_root / sess_id
        raw_dir = sess_dir / "raw"
        norm_dir = sess_dir / "normalized"
        raw_dir.mkdir(parents=True, exist_ok=True)
        norm_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create a 1.0s raw chunk (48kHz stereo)
        raw_file = raw_dir / "raw_chunk_000.wav"
        create_synthetic_wav(raw_file, duration_sec=1.0, sample_rate=48000, channels=2)

        # 2. Pre-create a stale/mismatched normalized file (0.2s duration, wrong sample rate)
        stale_norm = norm_dir / "chunk_000.wav"
        create_synthetic_wav(stale_norm, duration_sec=0.2, sample_rate=48000, channels=2)

        manifest.raw_chunks = [{
            "index": 0,
            "filename": "raw_chunk_000.wav",
            "duration_sec": 1.0,
            "sample_rate": 48000,
            "channels": 2,
            "size_bytes": raw_file.stat().st_size,
            "closed": True,
        }]
        manifest.normalized_chunks = [{
            "index": 0,
            "filename": "chunk_000.wav",
            "duration_sec": 0.2,
            "sample_rate": 48000,
            "channels": 2,
            "size_bytes": stale_norm.stat().st_size,
        }]
        manifest.status = STATE_READY
        save_session(manifest)

        fake_model = self.data_root / "fake_model.bin"
        fake_model.touch()

        # Mock normalize_chunk to verify it is called despite the existing stale normalized record
        norm_called = []
        def spy_normalize_chunk(raw_path, out_path, **kw):
            norm_called.append(raw_path)
            # Re-normalize to valid 16kHz mono
            create_synthetic_wav(out_path, duration_sec=1.0, sample_rate=16000, channels=1)
            return {
                "filename": out_path.name,
                "duration_sec": 1.0,
                "sample_rate": 16000,
                "channels": 1,
                "frames": 16000,
                "source_hash": "dummy_hash",
                "size_bytes": out_path.stat().st_size,
            }

        with patch("recorder.transcribe.normalize_chunk", side_effect=spy_normalize_chunk), \
             patch.object(TRANSCRIBE_MANAGER, "_run_whisper_chunk", return_value=("Transcribed text", [{"from_sec": 0.0, "to_sec": 1.0, "text": "Transcribed text"}])), \
             patch("recorder.media_tools.get_media_tools_status", return_value=_mock_valid_tools()):
            res = TRANSCRIBE_MANAGER.transcribe_session(sess_id, model_path=fake_model)
            self.assertEqual(len(norm_called), 1, "normalize_chunk MUST be called, bypassing stale manifest record")
            self.assertEqual(res.status, STATE_COMPLETED)

    def test_f04_unrelated_process_with_matching_path_never_signaled(self) -> None:
        """F04: Verify unrelated process with matching session path is rejected and never signaled."""
        sess_id = "sess_f04_unrelated"
        create_session(sess_id)
        sess_dir = self.data_root / sess_id
        dummy_path = sess_dir / "raw" / "raw_chunk_000.wav"

        # Start an unrelated Python sleep process with the session path in its command line
        unrelated_code = "import time\ntime.sleep(10)\n"
        unrelated_proc = subprocess.Popen(
            [sys.executable, "-c", unrelated_code, f"--dummy={dummy_path}"],
            start_new_session=True,
        )
        self.add_process_cleanup(unrelated_proc)

        try:
            # 1. is_process_alive_and_ours must return False because it lacks authoritative ownership
            self.assertFalse(
                is_process_alive_and_ours(unrelated_proc.pid, expected_session_id=sess_id, substrings=("python", "ffmpeg")),
                "Unrelated process must not be declared ours even with matching session path in cmdline",
            )

            # 2. stop_and_reap_process_group must return False and NOT send any signal
            from recorder.capture import stop_and_reap_process_group
            reaped = stop_and_reap_process_group(unrelated_proc.pid, timeout_sec=0.5, expected_session_id=sess_id)
            self.assertFalse(reaped)

            # 3. Verify process is still alive and running
            self.assertIsNone(unrelated_proc.poll(), "Unrelated process must remain alive and untouched")
        finally:
            try:
                unrelated_proc.terminate()
                unrelated_proc.wait(timeout=1.0)
            except Exception:
                pass

    def test_f04_startup_does_not_interrupt_live_owner_session(self) -> None:
        """F04/F01: reconcile_on_startup must not declare a live session interrupted if lock owner is running."""
        sess_id = "sess_f04_live"
        manifest = create_session(sess_id)
        manifest.status = STATE_RECORDING
        save_session(manifest)

        # Acquire lock and simulate living owner
        GLOBAL_LOCK.acquire("record", sess_id)
        try:
            reconciled = CAPTURE_MANAGER.reconcile_on_startup()
            self.assertNotIn(sess_id, reconciled)
            m = load_session(sess_id)
            self.assertIsNotNone(m)
            self.assertEqual(m.status, STATE_RECORDING)
        finally:
            GLOBAL_LOCK.release()

    def test_f04_exact_token_matching_no_prefix_suffix_collision(self) -> None:
        """F04: is_process_alive_and_ours must match exact token argument, not prefixes or substrings."""
        from unittest.mock import MagicMock, patch
        from recorder.capture import _extract_tokens_from_cmd, is_process_alive_and_ours

        # Direct verification of _extract_tokens_from_cmd
        cmd1 = "python3 -m recorder.guardian --session=sess1 --token=tok123-other"
        cmd2 = "python3 -m recorder.guardian --session sess1 --token tok123"
        cmd3 = "python3 -m recorder.guardian --session=sess1 --token=tok123"
        cmd4 = "python3 -m recorder.guardian --session=sess1 --token prefix-tok123"

        self.assertEqual(_extract_tokens_from_cmd(cmd1), {"tok123-other"})
        self.assertEqual(_extract_tokens_from_cmd(cmd2), {"tok123"})
        self.assertEqual(_extract_tokens_from_cmd(cmd3), {"tok123"})
        self.assertEqual(_extract_tokens_from_cmd(cmd4), {"prefix-tok123"})

        # Substring/prefix must NOT match expected_token="tok123"
        with patch("os.kill"):
            mock_ps = MagicMock()
            mock_ps.returncode = 0
            with patch("subprocess.run", return_value=mock_ps):
                # 1. Prefix collision: tok123-other vs tok123
                mock_ps.stdout = cmd1
                self.assertFalse(is_process_alive_and_ours(1234, expected_session_id="sess1", expected_token="tok123"))

                # 2. Suffix collision: prefix-tok123 vs tok123
                mock_ps.stdout = cmd4
                self.assertFalse(is_process_alive_and_ours(1234, expected_session_id="sess1", expected_token="tok123"))

                # 3. Exact match with space: --token tok123
                mock_ps.stdout = cmd2
                self.assertTrue(is_process_alive_and_ours(1234, expected_session_id="sess1", expected_token="tok123"))

                # 4. Exact match with equal: --token=tok123
                mock_ps.stdout = cmd3
                self.assertTrue(is_process_alive_and_ours(1234, expected_session_id="sess1", expected_token="tok123"))

    def test_f04_supervisor_argv_identity_and_rejection_of_unrelated_executables(self) -> None:
        """F04: is_process_alive_and_ours must validate authentic supervisor argv structure:
        reject unrelated executables (printf) and unrelated python modules (python3 -m unrelated).
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from recorder.capture import is_process_alive_and_ours

        cases = [
            ("python3 -m recorder.guardian --session-id probe --token exact", True),
            ("python3 -m recorder.guardian --session=probe --token=exact", True),
            ("python3 -m recorder.guardian --session-id probe --token exact-other", False),
            ("python3 -m recorder.guardian --session-id probe --token other-exact", False),
            ("/usr/bin/printf recorder.guardian --session-id probe --token exact", False),
            ("/usr/bin/printf recorder.guardian --session-id probe --token=exact", False),
            ("python3 -m unrelated --note recorder.guardian --session-id probe --token exact", False),
        ]

        for cmd, expected in cases:
            with patch("os.kill"), patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=cmd)):
                res = is_process_alive_and_ours(4242, expected_session_id="probe", expected_token="exact")
                self.assertEqual(res, expected, f"Failed for cmd: {cmd} (got {res}, expected {expected})")


if __name__ == "__main__":
    unittest.main()
