"""Integration tests for Diarizer on real multi-speaker audio."""
import json
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from recorder.constants import BASE_DIR
from recorder.diarizer import (
    CentroidRegistry,
    DiarizationConfig,
    Diarizer,
    verify_diarizer_components,
)
from recorder.session import SessionManifest


class TestDiarizationPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status = verify_diarizer_components()
        cls.diarizer_ready = status["ready"]
        cls.sample_wav = BASE_DIR / "work" / "diarization-spike" / "0-four-speakers-zh.wav"

    def setUp(self):
        from recorder.constants import get_data_root, set_data_root
        self._prev_root = get_data_root()
        self.test_dir = Path(tempfile.mkdtemp())
        set_data_root(self.test_dir)
        os.environ["WEBINAR_OUT_DIR"] = str(self.test_dir)

    def tearDown(self):
        from recorder.constants import set_data_root
        set_data_root(self._prev_root)
        if hasattr(self, "_prev_root") and self._prev_root:
            os.environ["WEBINAR_OUT_DIR"] = str(self._prev_root)
        else:
            os.environ.pop("WEBINAR_OUT_DIR", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_centroid_registry_cosine_similarity(self):
        registry = CentroidRegistry()
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.95, 0.05, 0.0]
        v3 = [0.0, 1.0, 0.0]

        spk1 = registry.register_speaker("speaker_01", v1)
        self.assertEqual(spk1, "speaker_01")
        self.assertEqual(registry.speakers["speaker_01"]["display_name"], "Говорящий 1")

        # Match close vector
        matched_spk, score = registry.match_cluster(v2, config=DiarizationConfig(match_threshold=0.8, new_speaker_threshold=0.4))
        self.assertEqual(matched_spk, "speaker_01")
        self.assertGreater(score, 0.9)

        # Distant vector should register as new speaker
        matched_new, score_new = registry.match_cluster(v3, config=DiarizationConfig(match_threshold=0.8, new_speaker_threshold=0.4))
        self.assertNotEqual(matched_new, "speaker_01")
        self.assertEqual(matched_new, "speaker_02")

        # Update centroid with alpha
        registry.update_centroid("speaker_01", v2, alpha=0.5)
        self.assertTrue(len(registry.centroids["speaker_01"]) == 3)

    def test_end_to_end_on_real_audio_if_available(self):
        if not self.diarizer_ready or not self.sample_wav.exists():
            self.skipTest("Diarization components or sample audio not available")

        # Setup mock session directory structure
        session_id = "test_diar_session"
        sess_dir = self.test_dir / session_id
        sess_dir.mkdir(parents=True)
        chunks_dir = sess_dir / "normalized"
        chunks_dir.mkdir()

        # Copy sample wav as chunk_0000.wav
        dest_chunk = chunks_dir / "chunk_0000.wav"
        shutil.copyfile(self.sample_wav, dest_chunk)

        # Inspect duration
        with wave.open(str(dest_chunk), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            dur = frames / float(rate)

        # Create manifest directly in session dir
        manifest = SessionManifest(
            session_id=session_id,
            source_kind="upload",
            title="Test Diarization Session",
            normalized_chunks=[{"filename": "chunk_0000.wav", "duration_sec": dur}],
            total_duration_sec=dur,
            diarization_params={"enabled": True, "num_speakers": 4},
        )
        (sess_dir / "session.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

        # Create dummy transcript.json
        transcript_data = {
            "schema_version": 2,
            "session_id": session_id,
            "segments": [
                {"from_sec": 0.0, "to_sec": 3.0, "text": "Segment 1"},
                {"from_sec": 3.2, "to_sec": 8.0, "text": "Segment 2"},
                {"from_sec": 8.5, "to_sec": 15.0, "text": "Segment 3"},
                {"from_sec": 15.5, "to_sec": 25.0, "text": "Segment 4"},
            ],
        }
        (sess_dir / "transcript.json").write_text(json.dumps(transcript_data, indent=2), encoding="utf-8")

        config = DiarizationConfig(
            window_sec=600.0,
            overlap_sec=60.0,
            num_speakers=4,
        )

        diarizer = Diarizer(config=config)
        progress_calls = []

        def on_prog(p):
            progress_calls.append(p)

        def mock_get_session_dir(sid, *args, **kwargs):
            return sess_dir

        with patch("recorder.storage.get_session_dir", side_effect=mock_get_session_dir), \
             patch("recorder.session.get_session_dir", side_effect=mock_get_session_dir), \
             patch("recorder.diarizer.get_session_dir", side_effect=mock_get_session_dir), \
             patch("recorder.export.get_session_dir", side_effect=mock_get_session_dir):
            res = diarizer.run_session_diarization(session_id, on_progress=on_prog)

        self.assertIn("speakers", res)
        self.assertIn("turns", res)
        # Should detect multiple speakers on 0-four-speakers-zh.wav
        detected_speakers = set(t.get("speaker_id") for t in res["turns"])
        self.assertGreaterEqual(len(detected_speakers), 2)

        # Verify checkpoint file was generated
        chk = sess_dir / "diarization_checkpoint.json"
        self.assertTrue(chk.exists())


if __name__ == "__main__":
    unittest.main()
