"""Unit tests for upload disk reservation, state transitions, and media non-deletion."""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import (
    MIN_DISK_FREE_BYTES,
    OUT_DIR,
    STATE_FAILED,
    STATE_READY,
)
from recorder.http_server import STATE, HardenedHTTPHandler
from recorder.normalize import get_audio_metadata, import_user_media_file
from recorder.session import create_session, load_session, retry_session
from recorder.storage import (
    StorageError,
    check_disk_space,
    get_session_dir,
    safe_upload_filename,
)
from tests.isolated_test import IsolatedTestCase
from tests.test_audio_fidelity_and_normalization import create_synthetic_wav


class TestUploadAndState(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_upload_state_sess"
        self.sess_dir = self.data_root / self.session_id

    def test_safe_upload_filename_unique_and_sanitized(self) -> None:
        f1 = safe_upload_filename("lecture 01 (Zoom).m4a")
        f2 = safe_upload_filename("lecture 01 (Zoom).m4a")

        self.assertNotEqual(f1, f2)  # Unique suffix prevents collision!
        self.assertTrue(f1.startswith("lecture_01_Zoom_"))
        self.assertTrue(f1.endswith(".m4a"))

    @patch("recorder.normalize.run_guarded_command")
    def test_import_user_media_never_deletes_source(self, mock_guarded) -> None:
        src_file = self.data_root / "user_input.wav"
        create_synthetic_wav(src_file, duration_sec=2.0, sample_rate=16000, channels=1)

        def fake_ffmpeg(cmd, **kwargs):
            pattern = cmd[-1]
            out_dir = Path(pattern).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            create_synthetic_wav(out_dir / "chunk_000.wav", duration_sec=2.0, sample_rate=16000, channels=1)
            return 0, "", ""

        mock_guarded.side_effect = fake_ffmpeg

        manifest = import_user_media_file(src_file, session_id=self.session_id)

        # Source file must still exist!
        self.assertTrue(src_file.exists())
        self.assertEqual(manifest.status, STATE_READY)
        self.assertGreater(len(manifest.normalized_chunks), 0)

        src_file.unlink(missing_ok=True)

    def test_retry_session_state_transition(self) -> None:
        manifest = create_session(self.session_id, title="Failed Session")
        manifest.status = STATE_FAILED
        manifest.error_message = "Temporary GPU error"
        from recorder.session import save_session
        save_session(manifest)

        # Retry must clear error message and set status to STATE_READY
        retried = retry_session(self.session_id)
        self.assertEqual(retried.status, STATE_READY)
        self.assertIsNone(retried.error_message)

    def test_import_media_missing_ffprobe_for_non_wav(self) -> None:
        src_mp4 = self.data_root / "test_sample.mp4"
        src_mp4.write_bytes(b"dummy mp4 content")

        # get_audio_metadata directly raises FileNotFoundError when ffprobe is missing
        with self.assertRaises(FileNotFoundError) as ctx_meta:
            get_audio_metadata(src_mp4, ffprobe_bin="nonexistent_ffprobe_probe_bin")
        self.assertIn("ffprobe binary not found", str(ctx_meta.exception))

        # import_user_media_file raises RuntimeError with explicit diagnostic
        with self.assertRaises(RuntimeError) as ctx_import:
            import_user_media_file(
                src_mp4,
                session_id=self.session_id,
                ffprobe_bin="nonexistent_ffprobe_probe_bin",
            )
        self.assertIn("ffprobe binary not found", str(ctx_import.exception))

    def test_import_media_missing_ffmpeg(self) -> None:
        src_wav = self.data_root / "test_sample.wav"
        create_synthetic_wav(src_wav, duration_sec=2.0, sample_rate=16000, channels=1)

        with self.assertRaises(RuntimeError) as ctx:
            import_user_media_file(
                src_wav,
                session_id=self.session_id,
                ffmpeg_bin="nonexistent_ffmpeg_bin",
            )
        self.assertIn("FFmpeg binary not found", str(ctx.exception))

    def test_import_media_bad_or_corrupt_non_wav_distinguished_from_missing_probe(self) -> None:
        # Create a corrupt non-wav media file
        corrupt_mp4 = self.data_root / "bad_corrupt.mp4"
        corrupt_mp4.write_bytes(b"NOT_A_VALID_MP4_HEADER")

        # Create a fake ffprobe script that is present and executable, but exits with error code 1 (like real ffprobe on corrupt media)
        fake_ffprobe = self.data_root / "fake_ffprobe.sh"
        fake_ffprobe.write_text("#!/bin/sh\nexit 1\n")
        fake_ffprobe.chmod(0o755)
        self.register_allowed_fake_executable(fake_ffprobe)

        # Probing corrupt media with present ffprobe returns duration 0.0 (no FileNotFoundError!)
        meta = get_audio_metadata(corrupt_mp4, ffprobe_bin=str(fake_ffprobe))
        self.assertEqual(meta.get("duration_sec"), 0.0)

        # Importing corrupt media raises zero or unknown duration (bad media, NOT missing ffprobe)
        with self.assertRaises(RuntimeError) as ctx:
            import_user_media_file(
                corrupt_mp4,
                session_id=self.session_id,
                ffprobe_bin=str(fake_ffprobe),
            )
        self.assertIn("has zero or unknown duration", str(ctx.exception))

    @patch("shutil.which")
    def test_upload_handler_rejects_early_when_tools_missing(self, mock_which) -> None:
        mock_which.return_value = None  # simulate missing ffmpeg and ffprobe

        handler = HardenedHTTPHandler.__new__(HardenedHTTPHandler)
        handler.headers = {"Content-Length": "1024"}

        # Must raise RuntimeError before attempting to stream upload body
        with self.assertRaises(RuntimeError) as ctx:
            handler.handle_upload("name=lecture.mp4")
        self.assertIn("Отсутствуют необходимые зависимости для импорта", str(ctx.exception))

        # Verify error was logged in STATE.logs
        self.assertTrue(any("Upload rejected:" in log for log in STATE.logs))

    def test_status_endpoint_reports_tools_availability(self) -> None:
        sent_payloads: list[dict] = []
        handler = HardenedHTTPHandler.__new__(HardenedHTTPHandler)
        handler.send_json = lambda payload, **kwargs: sent_payloads.append(payload)

        handler.handle_api_status()
        self.assertEqual(len(sent_payloads), 1)
        payload = sent_payloads[0]
        self.assertIn("tools", payload)
        self.assertIn("ffmpeg", payload["tools"])
        self.assertIn("ffprobe", payload["tools"])
        self.assertIsInstance(payload["tools"]["ffmpeg"], bool)
        self.assertIsInstance(payload["tools"]["ffprobe"], bool)


if __name__ == "__main__":
    unittest.main()
