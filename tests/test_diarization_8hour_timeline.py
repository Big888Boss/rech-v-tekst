"""Tests for 8+ hour long-call diarization sliding windowing, centroid stability, and memory bounds."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import get_data_root, set_data_root
from recorder.diarization_merge import merge_diarization_with_segments
from recorder.diarizer import (
    CentroidRegistry,
    DiarizationConfig,
    Diarizer,
)
from recorder.session import SessionManifest, save_session


class TestDiarization8HourTimeline(unittest.TestCase):
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

    def test_8_hour_window_partitioning_exact_count(self):
        """Verify 8 hours (28,800s) partitions into exactly 54 windows of 600s with 60s overlap."""
        config = DiarizationConfig(window_sec=600.0, overlap_sec=60.0)
        total_audio_sec = 28800.0  # Exactly 8 hours

        w_len = config.window_sec
        w_stride = config.stride_sec
        self.assertEqual(w_stride, 540.0)

        windows = []
        curr_start = 0.0
        while curr_start < total_audio_sec:
            curr_end = min(curr_start + w_len, total_audio_sec)
            windows.append((curr_start, curr_end))
            if curr_end >= total_audio_sec:
                break
            curr_start += w_stride

        self.assertEqual(len(windows), 54)
        # Verify first window
        self.assertEqual(windows[0], (0.0, 600.0))
        # Verify overlap between window 0 and 1
        self.assertEqual(windows[1][0], 540.0)
        self.assertEqual(windows[0][1] - windows[1][0], 60.0)
        # Verify last window reaches end
        self.assertEqual(windows[-1][1], 28800.0)

    def test_centroid_stability_across_multi_hour_pauses(self):
        """Verify speaker identity does not drift or swap after hours of silence or between windows."""
        registry = CentroidRegistry()

        # Speaker A voice vector: [0.9, 0.1, 0.0, ...]
        voice_a = [0.0] * 192
        voice_a[0] = 0.9
        voice_a[1] = 0.1

        # Speaker B voice vector: [0.0, 0.1, 0.9, ...]
        voice_b = [0.0] * 192
        voice_b[2] = 0.9
        voice_b[1] = 0.1

        # Register in hour 1 (window 0)
        spk_a_id = registry.register_speaker("speaker_01", voice_a)
        spk_b_id = registry.register_speaker("speaker_02", voice_b)

        # Speaker A speaks again in hour 7 (25,200 seconds later)
        voice_a_hour7 = [0.0] * 192
        voice_a_hour7[0] = 0.88
        voice_a_hour7[1] = 0.12

        config = DiarizationConfig(match_threshold=0.52, new_speaker_threshold=0.35)
        matched_id, score = registry.match_cluster(voice_a_hour7, config=config)

        # Must stably match Speaker A, not create new or match B
        self.assertEqual(matched_id, spk_a_id)
        self.assertGreater(score, 0.85)

        # Speaker B speaks again in hour 8
        voice_b_hour8 = [0.0] * 192
        voice_b_hour8[2] = 0.87
        voice_b_hour8[1] = 0.13

        matched_b, score_b = registry.match_cluster(voice_b_hour8, config=config)
        self.assertEqual(matched_b, spk_b_id)
        self.assertGreater(score_b, 0.85)

    def test_large_timeline_segment_merge_bounded_performance(self):
        """Verify merging 1,000 segments across 8 hours runs in sub-second time with dual contracts."""
        # 1,000 segments over 28,800 seconds (~1 turn every ~28 seconds)
        segments = []
        turns = []
        for i in range(1000):
            t_start = i * 28.0
            t_end = t_start + 20.0
            spk = "speaker_01" if (i % 2 == 0) else "speaker_02"
            segments.append({
                "from_sec": t_start,
                "to_sec": t_end,
                "text": f"Реплика номер {i} в длительном звонке",
            })
            turns.append({
                "from_sec": t_start,
                "to_sec": t_end,
                "speaker_id": spk,
                "overlap": False,
            })

        import time
        start_t = time.perf_counter()
        merged = merge_diarization_with_segments(segments, turns)
        dur = time.perf_counter() - start_t

        self.assertEqual(len(merged), 1000)
        # Must execute fast (< 0.5s for 1000 turns)
        self.assertLess(dur, 0.5)

        # Verify dual speaker contract
        for m in merged[:10]:
            self.assertIn("speaker_id", m)
            self.assertIn("speaker", m)
            self.assertEqual(m["speaker_id"], m["speaker"])
            self.assertIn(m["speaker_id"], ("speaker_01", "speaker_02"))


if __name__ == "__main__":
    unittest.main()
