"""Unit tests for Whisper transcription, source-bound normalization checkpoints, and retry recovery."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import STATE_COMPLETED
from recorder.session import create_session, load_session, save_session
from recorder.storage import get_session_dir
from recorder.transcribe import TRANSCRIBE_MANAGER
from tests.isolated_test import IsolatedTestCase
from tests.test_audio_fidelity_and_normalization import create_synthetic_wav


class TestTranscribeRecovery(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_transcribe_recovery_sess"
        self.manifest = create_session(self.session_id, title="Recovery Session")
        self.sess_dir = get_session_dir(self.session_id)
        self.raw_dir = self.sess_dir / "raw"
        self.norm_dir = self.sess_dir / "normalized"

        # Create two raw chunks
        create_synthetic_wav(self.raw_dir / "raw_chunk_000.wav", duration_sec=2.0, sample_rate=48000, channels=2)
        create_synthetic_wav(self.raw_dir / "raw_chunk_001.wav", duration_sec=2.0, sample_rate=48000, channels=2)
        self.manifest.raw_chunks = [
            {"filename": "raw_chunk_000.wav", "duration_sec": 2.0, "sample_rate": 48000, "channels": 2, "size_bytes": 384044, "closed": True},
            {"filename": "raw_chunk_001.wav", "duration_sec": 2.0, "sample_rate": 48000, "channels": 2, "size_bytes": 384044, "closed": True},
        ]
        self.manifest.total_duration_sec = 4.0
        save_session(self.manifest)

        # Simulate prior run having normalized ONLY chunk 0, leaving chunk 1 unnormalized
        create_synthetic_wav(self.norm_dir / "chunk_000.wav", duration_sec=2.0, sample_rate=16000, channels=1)
        self.manifest.normalized_chunks = [
            {"filename": "chunk_000.wav", "duration_sec": 2.0, "sample_rate": 16000, "channels": 1, "size_bytes": 64044}
        ]
        save_session(self.manifest)

    @patch("recorder.media_tools.get_media_tools_status")
    @patch("recorder.transcribe.TranscriptionManager._run_whisper_chunk")
    @patch("recorder.transcribe.normalize_chunk")
    def test_transcribe_normalizes_missing_raw_chunk_on_retry(self, mock_norm, mock_whisper, mock_tools) -> None:
        from recorder.media_tools import MediaToolsStatus
        mock_tools.return_value = MediaToolsStatus(
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
            model_path=str(self.sess_dir / "dummy_model.bin"),
            whisper_version="whisper.cpp version: 1.9.3",
            model_size_bytes=10,
            model_state="verified",
            model_error=None,
            whisper_error=None,
        )

        # Mock normalize_chunk to create normalized file
        def fake_norm(raw_path, out_path, **kwargs):
            create_synthetic_wav(out_path, duration_sec=2.0, sample_rate=16000, channels=1)
            return {
                "filename": out_path.name,
                "duration_sec": 2.0,
                "sample_rate": 16000,
                "channels": 1,
                "size_bytes": 64044,
            }

        mock_norm.side_effect = fake_norm
        mock_whisper.return_value = ("Текст куска", [{"from_sec": 0.0, "to_sec": 2.0, "text": "Текст куска"}])

        # Fake model file
        dummy_model = self.sess_dir / "dummy_model.bin"
        dummy_model.write_bytes(b"MODEL_DATA")

        # Run transcription with dummy whisper binary
        manifest = TRANSCRIBE_MANAGER.transcribe_session(
            session_id=self.session_id,
            model_path=dummy_model,
            whisper_bin="/mock/whisper-cli",
        )

        # Per F03: normalize_chunk must be called for all closed raw chunks to validate sources
        self.assertEqual(mock_norm.call_count, 2)
        called_raws = [c[0][0].name for c in mock_norm.call_args_list]
        self.assertEqual(called_raws, ["raw_chunk_000.wav", "raw_chunk_001.wav"])

        # Both chunks must have been transcribed
        self.assertEqual(mock_whisper.call_count, 2)
        self.assertEqual(manifest.status, STATE_COMPLETED)

        # Verify final transcript.txt exists and contains both
        t_file = self.sess_dir / "transcript.txt"
        self.assertTrue(t_file.exists())
        self.assertIn("Текст куска", t_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
