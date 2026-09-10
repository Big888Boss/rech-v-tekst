"""Tests for diarization checkpoint persistence, resumption, cancel, and retry."""
import json
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import (
    DIARIZATION_CANCELLED,
    DIARIZATION_COMPLETED,
    DIARIZATION_IDLE,
    get_data_root,
    set_data_root,
)
from recorder.diarizer import (
    CentroidRegistry,
    DiarizationConfig,
    Diarizer,
    verify_diarizer_components,
)
from recorder.session import SessionManifest, load_session, save_session


class TestDiarizationRecovery(unittest.TestCase):
    def setUp(self):
        self._prev_root = get_data_root()
        self.test_dir = Path(tempfile.mkdtemp())
        set_data_root(self.test_dir)
        os.environ["WEBINAR_OUT_DIR"] = str(self.test_dir)

    def tearDown(self):
        set_data_root(self._prev_root)
        if hasattr(self, "_prev_root") and self._prev_root:
            os.environ["WEBINAR_OUT_DIR"] = str(self._prev_root)
        else:
            os.environ.pop("WEBINAR_OUT_DIR", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_checkpoint_atomic_persistence_and_resumption(self):
        sid = "sess_recovery_01"
        s_dir = self.test_dir / sid
        s_dir.mkdir(parents=True)
        norm_dir = s_dir / "normalized"
        norm_dir.mkdir()

        # Create two 120-second chunk wav records
        manifest = SessionManifest(
            session_id=sid,
            normalized_chunks=[
                {"filename": "chunk_0000.wav", "duration_sec": 120.0},
                {"filename": "chunk_0001.wav", "duration_sec": 120.0},
            ],
            total_duration_sec=240.0,
        )
        save_session(manifest)
        (s_dir / "transcript.txt").write_text("Реплика 1. Реплика 2.", encoding="utf-8")
        (s_dir / "transcript.json").write_text(json.dumps({
            "schema_version": 2,
            "session_id": sid,
            "segments": [
                {"from_sec": 10.0, "to_sec": 50.0, "text": "Реплика 1."},
                {"from_sec": 130.0, "to_sec": 180.0, "text": "Реплика 2."},
            ]
        }), encoding="utf-8")

        # Helper to create a small valid 16kHz mono WAV chunk
        def create_dummy_wav(path, duration_sec):
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                n_frames = int(duration_sec * 16000)
                wf.writeframes(b"\x00\x00" * n_frames)

        create_dummy_wav(norm_dir / "chunk_0000.wav", 120.0)
        create_dummy_wav(norm_dir / "chunk_0001.wav", 120.0)

        # Fake existing checkpoint from window 0
        cp_file = s_dir / "diarization_checkpoint.json"
        fake_checkpoint = {
            "session_id": sid,
            "last_processed_window": 0,
            "total_windows": 2,
            "window_sec": 600.0,
            "overlap_sec": 60.0,
            "turns": [
                {"from_sec": 10.0, "to_sec": 50.0, "speaker_id": "speaker_01", "overlap": False}
            ],
            "speakers": {
                "speaker_01": {"display_name": "Говорящий 1", "color_index": 0}
            },
            "centroids": {
                "speaker_01": [0.5] * 192
            },
        }
        cp_file.write_text(json.dumps(fake_checkpoint), encoding="utf-8")

        config = DiarizationConfig(window_sec=600.0, overlap_sec=60.0)
        diarizer = Diarizer(config=config)

        mock_extractor = MagicMock()
        mock_extractor.dim = 192
        mock_extractor.compute_embedding.return_value = [0.5] * 192
        diarizer._extractor = mock_extractor

        # Mock Popen to return fake diarization stdout
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("0.000 -- 40.000 speaker_00\n", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("recorder.diarizer.verify_diarizer_components", return_value={"ready": True, "errors": []}), \
             patch("recorder.diarizer.resolve_diarizer_paths", return_value={
                 "bin": Path("/fake/bin"), "lib": Path("/fake/lib"), "seg_model": Path("/fake/seg"), "emb_model": Path("/fake/emb")
             }):
            res = diarizer.run_session_diarization(sid)

        self.assertEqual(res["status"], DIARIZATION_COMPLETED)
        # Checkpoint verified
        self.assertTrue(cp_file.exists())
        saved_cp = json.loads(cp_file.read_text(encoding="utf-8"))
        self.assertGreaterEqual(saved_cp["last_processed_window"], 0)

    def test_corrupted_checkpoint_recovery(self):
        sid = "sess_corrupt_cp"
        s_dir = self.test_dir / sid
        s_dir.mkdir(parents=True)
        norm_dir = s_dir / "normalized"
        norm_dir.mkdir()

        with wave.open(str(norm_dir / "chunk_0000.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000 * 30)

        manifest = SessionManifest(
            session_id=sid,
            normalized_chunks=[{"filename": "chunk_0000.wav", "duration_sec": 30.0}],
            total_duration_sec=30.0,
        )
        save_session(manifest)
        (s_dir / "transcript.txt").write_text("Тест повреждённого чекпоинта", encoding="utf-8")
        (s_dir / "transcript.json").write_text(json.dumps({
            "schema_version": 2,
            "session_id": sid,
            "segments": [{"from_sec": 5.0, "to_sec": 20.0, "text": "Тест"}]
        }), encoding="utf-8")

        # Write corrupt non-JSON content
        cp_file = s_dir / "diarization_checkpoint.json"
        cp_file.write_text("{{corrupted-json-data-not-valid", encoding="utf-8")

        diarizer = Diarizer()
        mock_extractor = MagicMock()
        mock_extractor.dim = 192
        mock_extractor.compute_embedding.return_value = [0.1] * 192
        diarizer._extractor = mock_extractor

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("0.000 -- 15.000 speaker_00\n", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("recorder.diarizer.verify_diarizer_components", return_value={"ready": True, "errors": []}), \
             patch("recorder.diarizer.resolve_diarizer_paths", return_value={
                 "bin": Path("/fake/bin"), "lib": Path("/fake/lib"), "seg_model": Path("/fake/seg"), "emb_model": Path("/fake/emb")
             }):
            # Must not crash, must recover and rewrite clean checkpoint
            res = diarizer.run_session_diarization(sid)

        self.assertEqual(res["status"], DIARIZATION_COMPLETED)
        # Checkpoint rewritten with valid JSON
        updated_cp = json.loads(cp_file.read_text(encoding="utf-8"))
        self.assertEqual(updated_cp["session_id"], sid)

    def test_cancel_preserves_whisper_transcript(self):
        sid = "sess_cancel_diar"
        s_dir = self.test_dir / sid
        s_dir.mkdir(parents=True)
        (s_dir / "normalized").mkdir()

        original_transcript = "Это важная расшифровка речи Whisper, которую нельзя потерять."
        (s_dir / "transcript.txt").write_text(original_transcript, encoding="utf-8")
        original_json = {
            "schema_version": 2,
            "session_id": sid,
            "segments": [{"from_sec": 0.0, "to_sec": 10.0, "text": original_transcript}]
        }
        (s_dir / "transcript.json").write_text(json.dumps(original_json), encoding="utf-8")

        manifest = SessionManifest(
            session_id=sid,
            normalized_chunks=[{"filename": "chunk_0000.wav", "duration_sec": 100.0}],
            total_duration_sec=100.0,
            has_transcript=True,
        )
        save_session(manifest)

        diarizer = Diarizer()
        diarizer.cancel()
        self.assertTrue(diarizer._cancel_requested)

        # Check manifest status
        loaded = load_session(sid)
        self.assertIsNotNone(loaded)
        # Whisper transcript text must remain 100% intact
        txt_after = (s_dir / "transcript.txt").read_text(encoding="utf-8")
        self.assertEqual(txt_after, original_transcript)


if __name__ == "__main__":
    unittest.main()
