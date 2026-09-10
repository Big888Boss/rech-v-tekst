"""Tests for Settings modal backend, strict verification, and installer lifecycle."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.config import AppSettings, get_effective_settings, load_settings, save_settings
from tests.isolated_test import IsolatedTestCase
from recorder.installer import (
    InstallerManager,
    PINNED_CMAKE_VERSION,
    PINNED_MODEL_FILENAME,
    PINNED_MODEL_SHA256,
    PINNED_MODEL_SIZE,
)
from recorder.lock import GLOBAL_LOCK, LockBusyError
from recorder.media_tools import (
    get_media_tools_status,
    invalidate_media_tools_cache,
    verify_whisper_engine,
    verify_whisper_model,
)


class TestMediaToolsVerification(unittest.TestCase):
    """Test strict verification of whisper engine and model files."""

    def setUp(self):
        invalidate_media_tools_cache()

    def tearDown(self):
        invalidate_media_tools_cache()

    def test_verify_whisper_engine_rejects_usr_bin_true(self):
        """Probe must reject arbitrary executables like /usr/bin/true."""
        if Path("/usr/bin/true").exists():
            ready, path, ver, err = verify_whisper_engine("/usr/bin/true", force_recheck=True)
            self.assertFalse(ready)
            self.assertIn("не опознан как whisper.cpp", err)

    def test_verify_whisper_engine_real_binary(self):
        """Probe must verify our real compiled whisper-cli."""
        real_bin = Path(__file__).resolve().parent.parent / "work" / "bin" / "whisper-cli"
        if real_bin.exists():
            ready, path, ver, err = verify_whisper_engine(str(real_bin), force_recheck=True)
            self.assertTrue(ready)
            self.assertIsNotNone(ver)
            self.assertIn("1.9.3", ver)
            self.assertIsNone(err)

    def test_resolve_whisper_bin_rejects_missing_saved_bin(self):
        """When saved whisper_bin is missing, resolve_whisper_bin must return None without fallback."""
        from recorder.config import AppSettings
        from recorder.media_tools import resolve_whisper_bin
        with patch("recorder.config.load_settings", return_value=AppSettings(whisper_bin="/missing/review19/whisper-cli")):
            resolved = resolve_whisper_bin()
            self.assertIsNone(resolved)
            ready, path, ver, err = verify_whisper_engine(force_recheck=True)
            self.assertFalse(ready)
            self.assertIn("не найден", err)

    def test_verify_whisper_engine_cache_hit_performance(self):
        """Repeated verification must hit cache in < 5ms."""
        real_bin = Path(__file__).resolve().parent.parent / "work" / "bin" / "whisper-cli"
        if real_bin.exists():
            verify_whisper_engine(str(real_bin), force_recheck=True)
            t0 = time.perf_counter()
            for _ in range(10):
                ready, _, _, _ = verify_whisper_engine(str(real_bin), force_recheck=False)
                self.assertTrue(ready)
            t1 = time.perf_counter()
            avg_ms = (t1 - t0) / 10.0 * 1000.0
            self.assertLess(avg_ms, 5.0)

    def test_verify_whisper_model_rejects_sparse_or_fake_magic(self):
        """Probe must reject sparse junk or files with fake magic bytes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            junk = Path(tmpdir) / "fake_model.bin"
            # Write fake 4-byte magic + 1MB zeros
            junk.write_bytes(b"lmgg" + b"\x00" * (1024 * 1024))
            res = verify_whisper_model(junk, force_recheck=True)
            self.assertFalse(res["ready"])
            self.assertEqual(res["state"], "unverified")

    def test_verify_whisper_model_rejects_catalog_name_wrong_size(self):
        """Catalog named file with wrong size must be rejected as corrupt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir) / PINNED_MODEL_FILENAME
            corrupt.write_bytes(b"lmgg" + b"\x00" * 1000)
            res = verify_whisper_model(corrupt, force_recheck=True)
            self.assertFalse(res["ready"])
            self.assertEqual(res["state"], "corrupt")

    def test_verify_whisper_model_real_model(self):
        """Probe must verify our official downloaded model."""
        real_model = Path(__file__).resolve().parent.parent / "models" / PINNED_MODEL_FILENAME
        if real_model.exists():
            res = verify_whisper_model(real_model, force_recheck=False)
            self.assertTrue(res["ready"])
            self.assertEqual(res["state"], "verified")
            self.assertEqual(res["sha256"], PINNED_MODEL_SHA256)
            self.assertEqual(res["size_bytes"], PINNED_MODEL_SIZE)


class TestConfigAndSettings(unittest.TestCase):
    """Test settings persistence, validation, and environment priority."""

    def test_boolean_parsing_strictness(self):
        """String 'false' or invalid bool values must be handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_settings = Path(tmpdir) / "settings.json"
            with patch("recorder.config.get_settings_path", return_value=custom_settings):
                # Dict with no_gpu: False
                s = save_settings({"no_gpu": False, "threads": 4, "cpu_threads": 2})
                self.assertFalse(s.no_gpu)

                # Invalid string 'false' should fail closed with ValueError
                with self.assertRaises(ValueError):
                    save_settings({"no_gpu": "false"})

    def test_env_priority_over_saved_settings(self):
        """WHISPER_NO_GPU=0 must override saved no_gpu=true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_settings = Path(tmpdir) / "settings.json"
            with patch("recorder.config.get_settings_path", return_value=custom_settings):
                save_settings({"no_gpu": True, "threads": 4, "cpu_threads": 2})

                # Test WHISPER_NO_GPU=0 overrides to False
                with patch.dict(os.environ, {"WHISPER_NO_GPU": "0", "WHISPER_THREADS": "8"}):
                    eff = get_effective_settings()
                    self.assertFalse(eff["effective"]["no_gpu"])
                    self.assertTrue(eff["overrides"]["no_gpu"]["active"])
                    self.assertEqual(eff["effective"]["threads"], 8)
                    self.assertTrue(eff["overrides"]["threads"]["active"])


class TestInstallerLifecycle(unittest.TestCase):
    """Test installer locking, thread lifecycle, rollback, and cleanup."""

    def setUp(self):
        try:
            GLOBAL_LOCK.release()
        except Exception:
            pass

    def tearDown(self):
        try:
            GLOBAL_LOCK.release()
        except Exception:
            pass

    def test_pinned_cmake_version(self):
        """Pinned cmake version must be 4.4.3."""
        self.assertEqual(PINNED_CMAKE_VERSION, "4.4.3")

    def test_register_before_thread_start_and_rollback(self):
        """Register-before-thread.start must register worker and rollback if thread fails."""
        installer = InstallerManager()
        worker_registered = []
        worker_rolled_back = []

        def _on_before_start(th):
            worker_registered.append(th)

        def _on_rollback():
            worker_rolled_back.append(True)

        # Mock Thread.start to raise RuntimeError
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("Cannot start thread")):
            with self.assertRaises(RuntimeError):
                installer.start_install(
                    force=False,
                    on_before_start=_on_before_start,
                    on_rollback=_on_rollback,
                )

        # Verify on_before_start ran, then on_rollback ran
        self.assertEqual(len(worker_registered), 1)
        self.assertEqual(len(worker_rolled_back), 1)
        # Lock must be released
        self.assertIsNone(GLOBAL_LOCK.fd)

    def test_on_finish_called_even_on_instant_completion(self):
        """Worker that finishes immediately must call on_finish."""
        installer = InstallerManager()
        finished = threading.Event()

        def _on_finish():
            finished.set()

        # Mock _run_install_worker to be a no-op
        with patch.object(installer, "_run_install_worker", side_effect=lambda force, on_finish=None: on_finish() if on_finish else None):
            installer.start_install(force=False, on_finish=_on_finish)
            self.assertTrue(finished.wait(timeout=2.0))

    def test_download_closes_fd_on_urlopen_failure(self):
        """_download_and_verify_model must close fd and unlink staging file on urlopen failure."""
        installer = InstallerManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "model.bin"
            with patch("recorder.installer.MODELS_DIR", Path(tmpdir)):
                with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("Network down")):
                    with self.assertRaises(ConnectionRefusedError):
                        installer._download_and_verify_model(target)

            # Check no leaked part files
            parts = list(Path(tmpdir).glob(".*.part.*"))
            self.assertEqual(len(parts), 0)

    def test_run_cmd_with_reap_terminates_process_group_parent_and_child(self):
        """When a command times out, _run_cmd_with_reap must terminate both the parent and child process."""
        installer = InstallerManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            helper_script = (
                "import subprocess, time, sys, os\n"
                f"proc = subprocess.Popen(['sleep', '60'])\n"
                f"with open(r'{pid_file}', 'w') as f:\n"
                "    f.write(str(proc.pid))\n"
                "time.sleep(60)\n"
            )
            script_file = Path(tmpdir) / "parent.py"
            script_file.write_text(helper_script)

            with self.assertRaises(TimeoutError):
                installer._run_cmd_with_reap([sys.executable, str(script_file)], timeout_sec=0.5)

            time.sleep(0.3)
            self.assertTrue(pid_file.exists())
            child_pid = int(pid_file.read_text().strip())

            try:
                os.kill(child_pid, 0)
                alive = True
            except OSError:
                alive = False
            self.assertFalse(alive, f"Child process {child_pid} was not terminated by process group reap!")

    def test_installer_busy_rejection(self):
        """Starting an install when one is already running must raise LockBusyError or RuntimeError."""
        installer = InstallerManager()
        blocker = threading.Event()

        def _blocking_dl(target):
            blocker.wait(timeout=2.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            with patch("recorder.installer.BASE_DIR", tmp_root), \
                 patch("recorder.installer.MODELS_DIR", tmp_root / "models"), \
                 patch.object(installer, "_download_and_verify_model", side_effect=_blocking_dl), \
                 patch.object(installer, "_build_whisper_static"):

                installer.start_install(force=True)
                try:
                    with self.assertRaises((RuntimeError, LockBusyError)):
                        installer.start_install(force=True)
                finally:
                    blocker.set()
                    installer.cancel_install()
                    if installer._thread:
                        installer._thread.join(timeout=3.0)

    def test_installer_failure_then_retry(self):
        """When an install attempt fails, lock is released and a subsequent retry can succeed."""
        installer = InstallerManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            with patch("recorder.installer.BASE_DIR", tmp_root), \
                 patch("recorder.installer.MODELS_DIR", tmp_root / "models"):

                # 1st attempt: fails in download
                with patch.object(installer, "_download_and_verify_model", side_effect=RuntimeError("Simulated download failure")), \
                     patch.object(installer, "_build_whisper_static"):
                    installer.start_install(force=True)
                    if installer._thread:
                        installer._thread.join(timeout=3.0)

                st = installer.get_status()
                self.assertEqual(st["status"], "failed")
                self.assertIn("Simulated download failure", st["error"])
                self.assertIsNone(GLOBAL_LOCK.get_current_holder())

                # 2nd attempt (retry): succeeds
                with patch.object(installer, "_download_and_verify_model"), \
                     patch.object(installer, "_build_whisper_static"), \
                     patch("recorder.installer.install_diarization_components", return_value={"ready": True}), \
                     patch("recorder.installer.get_diarization_install_status", return_value={"ready": True}), \
                     patch("recorder.installer.verify_whisper_engine", return_value=(True, str(tmp_root / "work" / "bin" / "whisper-cli"), "1.9.3", None)), \
                     patch("recorder.installer.verify_whisper_model", return_value={"ready": True, "state": "verified"}):
                    installer.start_install(force=True)
                    if installer._thread:
                        installer._thread.join(timeout=3.0)

                st2 = installer.get_status()
                self.assertEqual(st2["status"], "completed")
                self.assertIsNone(GLOBAL_LOCK.get_current_holder())

    def test_installer_existing_valid_runtime_idempotence(self):
        """When target model and whisper binary are already verified, start_install skips work."""
        installer = InstallerManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            mock_model = tmp_root / "models" / "ggml-large-v3-turbo.bin"
            mock_model.parent.mkdir(parents=True)
            mock_model.write_bytes(b"dummy")

            mock_bin = tmp_root / "work" / "bin" / "whisper-cli"
            mock_bin.parent.mkdir(parents=True)
            mock_bin.touch(mode=0o755)

            with patch("recorder.installer.BASE_DIR", tmp_root), \
                 patch("recorder.installer.MODELS_DIR", tmp_root / "models"), \
                 patch("recorder.installer.verify_whisper_model", return_value={"ready": True, "state": "verified"}), \
                 patch("recorder.installer.verify_whisper_engine", return_value=(True, str(mock_bin), "1.9.3-dev", None)), \
                 patch.object(installer, "_download_and_verify_model") as mock_dl, \
                 patch.object(installer, "_build_whisper_static") as mock_bld:

                installer.start_install(force=False)
                if installer._thread:
                    installer._thread.join(timeout=2.0)

                st = installer.get_status()
                self.assertEqual(st["status"], "completed")
                self.assertEqual(st["progress_percent"], 100.0)
                mock_dl.assert_not_called()
                mock_bld.assert_not_called()

    def test_installer_integrity_mismatch(self):
        """_download_and_verify_model must raise ValueError and cleanup on SHA mismatch."""
        installer = InstallerManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "model.bin"

            class FakeResponse:
                def __init__(self):
                    self.headers = {"Content-Length": "1024"}
                    self._chunks = [b"A" * 1024, b""]
                def read(self, size):
                    return self._chunks.pop(0) if self._chunks else b""
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            with patch("recorder.installer.MODELS_DIR", Path(tmpdir)), \
                 patch("recorder.installer.PINNED_MODEL_SIZE", 1024), \
                 patch("urllib.request.urlopen", return_value=FakeResponse()):
                with self.assertRaises(ValueError) as ctx:
                    installer._download_and_verify_model(target)
                self.assertIn("SHA-256", str(ctx.exception))
                # Staging part file should be unlinked on failure
                parts = list(Path(tmpdir).glob(".*.part.*"))
                self.assertEqual(len(parts), 0)


class TestSettingsHTTPHandler(IsolatedTestCase):
    """Test HTTP endpoints /api/settings and /api/install/status."""

    def setUp(self):
        super().setUp()
        try:
            GLOBAL_LOCK.release(force_unlock=True)
        except Exception:
            pass
        from recorder.capture import CAPTURE_MANAGER
        from recorder.transcribe import TRANSCRIBE_MANAGER
        from recorder.installer import INSTALLER
        from recorder.http_server import STATE
        CAPTURE_MANAGER.active_session_id = None
        TRANSCRIBE_MANAGER.active_session_id = None
        TRANSCRIBE_MANAGER.progress["is_running"] = False
        with INSTALLER._lock:
            INSTALLER._state["status"] = "idle"
            INSTALLER._state["error"] = None
        with STATE._workers_lock:
            STATE.active_workers.clear()

    def tearDown(self):
        try:
            GLOBAL_LOCK.release(force_unlock=True)
        except Exception:
            pass
        super().tearDown()

    def test_settings_get_and_post_flow(self):
        from recorder.http_server import STATE, run_server
        import urllib.request
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_settings = Path(tmpdir) / "settings.json"
            with patch("recorder.config.get_settings_path", return_value=custom_settings):
                server, thread = run_server(host="127.0.0.1", port=0)
                port = server.server_port
                try:
                    # 1. GET /api/settings
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        data = json.loads(resp.read().decode())
                        self.assertIn("effective", data)
                        self.assertIn("tools", data)

                    # 2. POST /api/settings (valid payload with CSRF)
                    payload = json.dumps({
                        "threads": 6,
                        "cpu_threads": 3,
                        "no_gpu": False,
                        "language": "ru",
                        "csrf_token": STATE.csrf_token,
                    }).encode()
                    req_post = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/settings",
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Origin": f"http://127.0.0.1:{port}",
                            "X-CSRF-Token": STATE.csrf_token,
                        },
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(req_post, timeout=5) as resp:
                            self.assertEqual(resp.status, 200)
                            res_data = json.loads(resp.read().decode())
                            self.assertTrue(res_data["ok"])
                            self.assertEqual(res_data["settings"]["configured"]["threads"], 6)
                    except urllib.error.HTTPError as err:
                        body_err = err.read().decode("utf-8", errors="replace")
                        self.fail(f"HTTPError {err.code}: {body_err}")

                    # 3. GET /api/install/status
                    req_inst = urllib.request.Request(f"http://127.0.0.1:{port}/api/install/status")
                    with urllib.request.urlopen(req_inst, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        inst_data = json.loads(resp.read().decode())
                        self.assertIn("status", inst_data)
                        self.assertIn("is_running", inst_data)

                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
