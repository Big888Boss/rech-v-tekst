"""Unit tests for Queue Management and Upfront Process Admission.
Tests cover:
- Upfront admission rejection when Whisper CLI or model is missing/unsafe
- Preservation of previous session status (ready, failed, interrupted) on admission failure (zero disk writes)
- 409 Conflict rejection when attempting to process an unqueued session (in_queue=False)
- Queue remove and restore operations, preserving all files/chunks on disk
- Prevention of queue operations on active (recording/processing) sessions (409 Conflict)
- Full lock acquisition across remove, restore, and retry operations (LockBusyError -> 409)
- Preservation of in_queue flag during retry_session
- Lock release session_id guard preventing foreign/premature lock release
- Model path resolver symlink preservation
- CSRF validation on all new endpoints
"""
from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from recorder.constants import (
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_RECORDING,
)
from recorder.http_server import (
    GLOBAL_LOCK,
    STATE,
    TRANSCRIBE_MANAGER,
    HardenedHTTPHandler,
)
from recorder.lock import LockBusyError, ProcessLock
from recorder.media_tools import (
    MediaToolsStatus,
    get_media_tools_status,
    resolve_model_path,
    resolve_whisper_bin,
)
from recorder.session import (
    SessionManifest,
    create_session,
    list_sessions,
    load_session,
    remove_from_queue,
    restore_to_queue,
    retry_session,
    save_session,
)
from recorder.storage import (
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
)
from tests.isolated_test import IsolatedTestCase
from tests.test_audio_fidelity_and_normalization import create_synthetic_wav


class TestQueueAndAdmission(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "queue_adm_test_sess"
        self.sess_dir = get_session_dir(self.session_id)
        (self.sess_dir / "normalized").mkdir(parents=True, exist_ok=True)
        (self.sess_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.sess_dir / "transcripts").mkdir(parents=True, exist_ok=True)

        # Create synthetic chunk
        create_synthetic_wav(
            self.sess_dir / "normalized" / "chunk_000.wav",
            duration_sec=2.0,
            sample_rate=16000,
            channels=1,
        )

        self.manifest = SessionManifest(
            session_id=self.session_id,
            title="Test Session",
            source_kind="mic",
            normalized_chunks=[{
                "index": 0,
                "filename": "chunk_000.wav",
                "duration_sec": 2.0,
                "sample_rate": 16000,
                "channels": 1,
                "closed": True,
                "size_bytes": (self.sess_dir / "normalized" / "chunk_000.wav").stat().st_size,
            }],
            total_duration_sec=2.0,
            status=STATE_READY,
            in_queue=True,
        )
        save_session(self.manifest)
        STATE.csrf_token = "test_csrf_token_secret_12345"

        # Start isolated server on ephemeral port
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), HardenedHTTPHandler)
        self.port = self.server.server_port
        self.server.server_port = self.port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            GLOBAL_LOCK.release(force_unlock=True)
        except Exception:
            pass
        super().tearDown()

    def _post(self, path: str, body: dict, csrf: str | None = None) -> tuple[int, dict]:
        body_dict = dict(body)
        token = csrf if csrf is not None else STATE.csrf_token
        body_dict["csrf_token"] = token
        payload = json.dumps(body_dict).encode("utf-8")

        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "X-CSRF-Token": token,
        }
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            conn.request("POST", path, body=payload, headers=headers)
            res = conn.getresponse()
            data = res.read().decode("utf-8")
            try:
                parsed = json.loads(data)
            except Exception:
                parsed = {"raw": data}
            return res.status, parsed
        finally:
            conn.close()

    def _mock_tools_status(
        self,
        whisper: bool = True,
        whisper_error: str | None = None,
        model: bool = True,
        model_error: str | None = None,
        whisper_path: str = "/fake/bin/whisper-cli",
        model_path: str = "/fake/models/ggml-large-v3-turbo.bin",
    ) -> MediaToolsStatus:
        return MediaToolsStatus(
            ffmpeg_path="/usr/bin/ffmpeg",
            ffprobe_path="/usr/bin/ffprobe",
            ffmpeg=True,
            ffprobe=True,
            ready=True,
            missing=(),
            remediation=None,
            whisper=whisper,
            model=model,
            whisper_path=whisper_path if whisper else None,
            model_path=model_path if model else None,
            whisper_version="1.9.3-dev" if whisper else None,
            model_size_bytes=1624555275 if model else 0,
            model_state="verified" if model else ("corrupt" if "не совпадает" in (model_error or "") else "missing"),
            model_error=model_error,
            whisper_error=whisper_error,
        )

    # =========================================================================
    # 1. UPFRONT ADMISSION TESTS
    # =========================================================================

    def test_admission_missing_whisper_cli_preserves_ready_status(self) -> None:
        """When Whisper CLI is missing, /api/process must return an error and NOT modify session status on disk."""
        mock_status = self._mock_tools_status(
            whisper=False,
            whisper_error="Движок распознавания (whisper-cli) не найден или повреждён. Установите whisper.cpp.",
        )
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.BAD_REQUEST)
            self.assertIn("whisper", resp.get("error", "").lower())

            # Verify session on disk remained STATE_READY with zero writes/mutations
            s = load_session(self.session_id)
            self.assertIsNotNone(s)
            self.assertEqual(s.status, STATE_READY)
            self.assertIsNone(s.error_message)

    def test_admission_missing_model_preserves_failed_status(self) -> None:
        """When model is missing, /api/process must preserve existing failed status on disk without changing it."""
        self.manifest.status = STATE_FAILED
        self.manifest.error_message = "Previous failure message"
        save_session(self.manifest)

        mock_status = self._mock_tools_status(
            model=False,
            model_error="Файл модели Whisper не найден или повреждён. Установите модель перед запуском.",
        )
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.BAD_REQUEST)
            self.assertIn("модели", resp.get("error", ""))

            # Crucial: status must NOT be changed to ready or anything else
            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_FAILED)
            self.assertEqual(s.error_message, "Previous failure message")

    def test_admission_symlink_model_rejected_as_unsafe(self) -> None:
        """Symlinked model file must be detected and rejected as unsafe file."""
        mock_status = self._mock_tools_status(
            model=False,
            model_error="Файл модели не является безопасным регулярным файлом (символические ссылки запрещены): /fake/symlink_model.bin",
        )
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.BAD_REQUEST)
            self.assertIn("не является безопасным регулярным файлом", resp.get("error", ""))

            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_READY)

    def test_admission_corrupt_model_rejected_preserves_ready_status(self) -> None:
        """Corrupted model (SHA-256 mismatch or wrong size) must be rejected upfront without transitioning session."""
        mock_status = self._mock_tools_status(
            model=False,
            model_error="Контрольная сумма SHA-256 модели не совпадает с официальной large-v3-turbo",
        )
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.BAD_REQUEST)
            self.assertIn("контрольная сумма sha-256", resp.get("error", "").lower())

            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_READY)

    def test_admission_unqueued_session_rejected_with_409_conflict(self) -> None:
        """Attempting to run /api/process on a session with in_queue=False must return 409 Conflict."""
        self.manifest.in_queue = False
        save_session(self.manifest)

        mock_status = self._mock_tools_status(whisper=True, model=True)
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.CONFLICT)
            self.assertIn("убрана из очереди", resp.get("error", ""))

            s = load_session(self.session_id)
            self.assertFalse(s.in_queue)
            self.assertEqual(s.status, STATE_READY)

    # =========================================================================
    # 2. QUEUE REMOVE & RESTORE TESTS
    # =========================================================================

    def test_queue_remove_and_restore_cycle(self) -> None:
        """Test removing session from queue, verifying disk files preserved, and restoring it."""
        # 1. Remove from queue
        code, resp = self._post("/api/session/queue/remove", {"session_id": self.session_id})
        self.assertEqual(code, HTTPStatus.OK)
        self.assertTrue(resp.get("ok"))
        self.assertFalse(resp.get("in_queue"))

        s_removed = load_session(self.session_id)
        self.assertFalse(s_removed.in_queue)
        # Media chunk must be completely intact
        self.assertTrue((self.sess_dir / "normalized" / "chunk_000.wav").exists())

        # Verify listing with include_removed=False excludes it
        active_list = list_sessions(include_removed=False)
        self.assertNotIn(self.session_id, [x.session_id for x in active_list])

        # Verify listing with include_removed=True includes it
        all_list = list_sessions(include_removed=True)
        self.assertIn(self.session_id, [x.session_id for x in all_list])

        # 2. Restore to queue
        code2, resp2 = self._post("/api/session/queue/restore", {"session_id": self.session_id})
        self.assertEqual(code2, HTTPStatus.OK)
        self.assertTrue(resp2.get("ok"))
        self.assertTrue(resp2.get("in_queue"))

        s_restored = load_session(self.session_id)
        self.assertTrue(s_restored.in_queue)
        active_list2 = list_sessions(include_removed=False)
        self.assertIn(self.session_id, [x.session_id for x in active_list2])

    def test_queue_remove_active_recording_session_rejected_409(self) -> None:
        """Queue remove must be rejected with 409 Conflict if session is currently recording."""
        self.manifest.status = STATE_RECORDING
        save_session(self.manifest)

        code, resp = self._post("/api/session/queue/remove", {"session_id": self.session_id})
        self.assertEqual(code, HTTPStatus.CONFLICT)

        s = load_session(self.session_id)
        self.assertTrue(s.in_queue)

    def test_queue_restore_active_processing_session_rejected_409(self) -> None:
        """Queue restore must be rejected with 409 Conflict if session is currently processing."""
        self.manifest.status = STATE_PROCESSING
        self.manifest.in_queue = False
        save_session(self.manifest)

        code, resp = self._post("/api/session/queue/restore", {"session_id": self.session_id})
        self.assertEqual(code, HTTPStatus.CONFLICT)

        s = load_session(self.session_id)
        self.assertFalse(s.in_queue)

    def test_queue_remove_busy_lock_returns_409_conflict(self) -> None:
        """When GLOBAL_LOCK is held by another operation, queue remove must return 409 Conflict (not 500)."""
        GLOBAL_LOCK.acquire("record", "other_active_sess")
        try:
            code, resp = self._post("/api/session/queue/remove", {"session_id": self.session_id})
            self.assertEqual(code, HTTPStatus.CONFLICT)
            self.assertIn("other_active_sess", resp.get("error", ""))
        finally:
            GLOBAL_LOCK.release(expected_session_id="other_active_sess")

    def test_queue_restore_busy_lock_returns_409_conflict(self) -> None:
        """When GLOBAL_LOCK is held by another operation, queue restore must return 409 Conflict (not 500)."""
        GLOBAL_LOCK.acquire("record", "other_active_sess")
        try:
            code, resp = self._post("/api/session/queue/restore", {"session_id": self.session_id})
            self.assertEqual(code, HTTPStatus.CONFLICT)
        finally:
            GLOBAL_LOCK.release(expected_session_id="other_active_sess")

    # =========================================================================
    # 3. RETRY SESSION TESTS & IN_QUEUE PRESERVATION
    # =========================================================================

    def test_retry_session_preserves_in_queue_flag(self) -> None:
        """Retrying a session must preserve in_queue=False or in_queue=True without overwriting it."""
        self.manifest.status = STATE_FAILED
        self.manifest.in_queue = False
        self.manifest.error_message = "Some error"
        save_session(self.manifest)

        code, resp = self._post("/api/session/retry", {"session_id": self.session_id})
        self.assertEqual(code, HTTPStatus.OK)
        self.assertTrue(resp.get("ok"))

        s = load_session(self.session_id)
        self.assertEqual(s.status, STATE_READY)
        self.assertFalse(s.in_queue)  # Must remain False!
        self.assertIsNone(s.error_message)

    def test_retry_session_busy_lock_returns_409_conflict(self) -> None:
        """When GLOBAL_LOCK is busy, /api/session/retry must return 409 Conflict."""
        GLOBAL_LOCK.acquire("record", "other_active_sess")
        try:
            code, resp = self._post("/api/session/retry", {"session_id": self.session_id})
            self.assertEqual(code, HTTPStatus.CONFLICT)
        finally:
            GLOBAL_LOCK.release(expected_session_id="other_active_sess")

    # =========================================================================
    # 4. LOCK RELEASE ISOLATION TESTS
    # =========================================================================

    def test_lock_release_with_mismatched_session_id_is_guarded(self) -> None:
        """ProcessLock.release() must reject releasing a lock if expected_session_id does not match."""
        lock = ProcessLock(self.data_root / ".custom.lock")
        lock.acquire("process", "sess_alpha")
        self.assertEqual(lock.get_current_holder()["session_id"], "sess_alpha")

        # Wrong expected_session_id must NOT release the lock
        lock.release(expected_session_id="sess_beta")
        self.assertIsNotNone(lock.get_current_holder())
        self.assertEqual(lock.get_current_holder()["session_id"], "sess_alpha")

        # Matching expected_session_id releases the lock
        lock.release(expected_session_id="sess_alpha")
        self.assertIsNone(lock.get_current_holder())

    # =========================================================================
    # 5. MODEL RESOLVER SYMLINK PRESERVATION
    # =========================================================================

    def test_resolve_model_path_preserves_symlink_identity(self) -> None:
        """resolve_model_path must return an absolute path without resolving symlink targets."""
        target_file = self.data_root / "real_model_target.bin"
        target_file.touch()
        symlink_path = self.data_root / "symlink_model.bin"
        os.symlink(target_file, symlink_path)

        resolved = resolve_model_path(symlink_path)
        self.assertTrue(resolved.is_symlink())
        self.assertFalse(is_safe_regular_file(resolved))

    # =========================================================================
    # 6. CSRF VALIDATION TESTS
    # =========================================================================

    def test_csrf_token_required_for_queue_endpoints(self) -> None:
        """Missing or invalid CSRF token on queue endpoints must return 403 Forbidden."""
        code, _ = self._post("/api/session/queue/remove", {"session_id": self.session_id}, csrf="wrong_token")
        self.assertEqual(code, HTTPStatus.FORBIDDEN)

    # =========================================================================
    # 7. CONCURRENCY & TOCTOU RACE TESTS
    # =========================================================================

    def test_race_between_initial_read_and_admission_acquire(self) -> None:
        """Simulate remove_from_queue occurring between initial check and admission acquire:
        re-check under lock detects in_queue=False, returns 409 Conflict, preserves disk status,
        and safely releases the lock."""
        fake_whisper = self.data_root / "fake_whisper"
        fake_whisper.touch(mode=0o755)
        fake_model = self.data_root / "fake_model.bin"
        fake_model.write_bytes(b"model data")

        orig_acquire = GLOBAL_LOCK.acquire

        def hooked_acquire(op_type: str, session_id: str | None = None) -> None:
            if op_type == "process" and session_id == self.session_id:
                # Concurrent remove occurs right before admission lock is acquired
                remove_from_queue(self.session_id)
            return orig_acquire(op_type, session_id)

        mock_status = self._mock_tools_status(whisper=True, model=True)
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status), \
             patch.object(GLOBAL_LOCK, "acquire", side_effect=hooked_acquire):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            self.assertEqual(code, HTTPStatus.CONFLICT)
            self.assertIn("убрана из очереди", resp.get("error", ""))

            # Session on disk must have in_queue=False and its status must NOT be modified
            s = load_session(self.session_id)
            self.assertIsNotNone(s)
            self.assertFalse(s.in_queue)
            self.assertEqual(s.status, STATE_READY)

            # GLOBAL_LOCK must be fully released
            self.assertIsNone(GLOBAL_LOCK.get_current_holder())

    def test_thread_start_failure_unregisters_worker_and_releases_lock(self) -> None:
        """If threading.Thread.start raises an exception, the handler must unregister the
        worker and release the acquired GLOBAL_LOCK immediately."""
        fake_whisper = self.data_root / "fake_whisper"
        fake_whisper.touch(mode=0o755)
        fake_model = self.data_root / "fake_model.bin"
        fake_model.write_bytes(b"model data")

        orig_start = threading.Thread.start

        def failing_worker_start(thread_self: threading.Thread) -> None:
            target = getattr(thread_self, "_target", None)
            if target is not None and getattr(target, "__name__", "") == "_run_bg":
                raise RuntimeError("thread spawn failed")
            return orig_start(thread_self)

        mock_status = self._mock_tools_status(whisper=True, model=True)
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status), \
             patch.object(threading.Thread, "start", side_effect=failing_worker_start, autospec=True):
            code, resp = self._post("/api/process", {"session_id": self.session_id})

            # Handled gracefully, returns 409 Conflict (from RuntimeError)
            self.assertEqual(code, HTTPStatus.CONFLICT)
            self.assertIn("thread spawn failed", resp.get("error", ""))

            # GLOBAL_LOCK must NOT be left hanging
            self.assertIsNone(GLOBAL_LOCK.get_current_holder())

            # No active worker left registered for this session
            with STATE._workers_lock:
                active_sids = [w.get("session_id") for w in STATE.active_workers.values()]
                self.assertNotIn(self.session_id, active_sids)

            # Session status on disk is still ready and in queue
            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_READY)
            self.assertTrue(s.in_queue)

    # =========================================================================
    # 8. BORROWED LOCK OWNERSHIP & EXPORT ISOLATION
    # =========================================================================

    def test_borrowed_lock_ownership_transcribe_manager_does_not_release_and_wrapper_releases_once(self) -> None:
        """Verify real handle_process and background worker wrapper:
        Manager borrows the lock, exports execute while holder is verified, and wrapper releases
        the lock exactly once on both success and error paths."""
        fake_whisper = self.data_root / "fake_whisper"
        fake_whisper.touch(mode=0o755)
        fake_model = self.data_root / "fake_model.bin"
        fake_model.write_bytes(b"model data")

        orig_start = threading.Thread.start
        orig_release = GLOBAL_LOCK.release

        def sync_worker_start(thread_self: threading.Thread) -> None:
            target = getattr(thread_self, "_target", None)
            if target is not None and getattr(target, "__name__", "") == "_run_bg":
                # Run the background worker synchronously to test wrapper inline
                target()
                return
            return orig_start(thread_self)

        valid_segments = [{"from_sec": 0.0, "to_sec": 2.0, "text": "Текст"}]

        # 1. SUCCESS PATH
        release_call_count = 0
        exports_called = 0
        holder_during_exports = None

        def spy_release(*args: Any, **kwargs: Any) -> None:
            nonlocal release_call_count
            release_call_count += 1
            return orig_release(*args, **kwargs)

        def spy_exports(session_id: str) -> None:
            nonlocal exports_called, holder_during_exports
            exports_called += 1
            holder_during_exports = GLOBAL_LOCK.get_current_holder()

        mock_status = self._mock_tools_status(whisper=True, model=True)
        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status), \
             patch("recorder.media_tools.get_media_tools_status", return_value=mock_status), \
             patch.object(TRANSCRIBE_MANAGER, "_run_whisper_chunk", return_value=("Текст", valid_segments)), \
             patch("recorder.http_server.generate_all_exports", side_effect=spy_exports), \
             patch.object(GLOBAL_LOCK, "release", side_effect=spy_release), \
             patch.object(threading.Thread, "start", side_effect=sync_worker_start, autospec=True):

            code, resp = self._post("/api/process", {"session_id": self.session_id})
            self.assertEqual(code, HTTPStatus.OK)
            self.assertTrue(resp.get("ok"))

            # Exports must have run while holding the lock
            self.assertEqual(exports_called, 1)
            self.assertIsNotNone(holder_during_exports)
            self.assertEqual(holder_during_exports.get("session_id"), self.session_id)

            # Exactly ONE release occurred across the entire lifecycle (in wrapper finally)
            self.assertEqual(release_call_count, 1)
            self.assertIsNone(GLOBAL_LOCK.get_current_holder())

            # Worker unregistered
            with STATE._workers_lock:
                active_sids = [w.get("session_id") for w in STATE.active_workers.values()]
                self.assertNotIn(self.session_id, active_sids)

            # Manifest completed
            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_COMPLETED)

        # 2. ERROR PATH
        # Reset session to ready
        self.manifest.status = STATE_READY
        self.manifest.in_queue = True
        save_session(self.manifest)

        release_call_count = 0
        exports_called = 0

        with patch("recorder.http_server.get_media_tools_status", return_value=mock_status), \
             patch("recorder.media_tools.get_media_tools_status", return_value=mock_status), \
             patch.object(TRANSCRIBE_MANAGER, "_run_whisper_chunk", side_effect=RuntimeError("Whisper crashed")), \
             patch("recorder.http_server.generate_all_exports", side_effect=spy_exports), \
             patch.object(GLOBAL_LOCK, "release", side_effect=spy_release), \
             patch.object(threading.Thread, "start", side_effect=sync_worker_start, autospec=True):

            code, resp = self._post("/api/process", {"session_id": self.session_id})
            # handle_process returns OK since background worker was launched, but worker fails internally
            self.assertEqual(code, HTTPStatus.OK)

            # Exports must NOT have been called
            self.assertEqual(exports_called, 0)

            # Exactly ONE release occurred (in wrapper finally, not by manager)
            self.assertEqual(release_call_count, 1)
            self.assertIsNone(GLOBAL_LOCK.get_current_holder())

            # Worker unregistered
            with STATE._workers_lock:
                active_sids = [w.get("session_id") for w in STATE.active_workers.values()]
                self.assertNotIn(self.session_id, active_sids)

            # Manifest status marked failed
            s = load_session(self.session_id)
            self.assertEqual(s.status, STATE_FAILED)

    # =========================================================================
    # 9. MALFORMED SESSION ID VALIDATION
    # =========================================================================

    def test_malformed_session_ids_on_queue_endpoints(self) -> None:
        """Queue remove and restore must return 400 Bad Request on empty, traversal, or invalid IDs."""
        bad_ids = [
            "",
            "   ",
            "../escape",
            "../../etc/passwd",
            "sub/dir/sess",
            "sess*#@!",
            "invalid space id",
        ]

        for bad_id in bad_ids:
            # 1. Remove endpoint
            code, resp = self._post("/api/session/queue/remove", {"session_id": bad_id})
            self.assertEqual(code, HTTPStatus.BAD_REQUEST, f"Expected 400 for remove with id={bad_id!r}")
            self.assertIn("error", resp)

            # 2. Restore endpoint
            code2, resp2 = self._post("/api/session/queue/restore", {"session_id": bad_id})
            self.assertEqual(code2, HTTPStatus.BAD_REQUEST, f"Expected 400 for restore with id={bad_id!r}")
            self.assertIn("error", resp2)

        # Missing session_id key entirely
        code3, resp3 = self._post("/api/session/queue/remove", {})
        self.assertEqual(code3, HTTPStatus.BAD_REQUEST)
        self.assertIn("error", resp3)

        code4, resp4 = self._post("/api/session/queue/restore", {})
        self.assertEqual(code4, HTTPStatus.BAD_REQUEST)
        self.assertIn("error", resp4)


if __name__ == "__main__":
    unittest.main()

