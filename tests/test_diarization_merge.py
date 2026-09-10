"""Tests for diarization_merge.py in milestone v1.1.0."""
import unittest

from recorder.diarization_merge import (
    format_speaker_name,
    merge_diarization_with_segments,
)


class TestDiarizationMerge(unittest.TestCase):
    def test_format_speaker_name_indexing(self):
        """Test speaker_01 -> Говорящий 1 (not Говорящий 2)."""
        self.assertEqual(format_speaker_name("speaker_01"), "Говорящий 1")
        self.assertEqual(format_speaker_name("speaker_02"), "Говорящий 2")
        self.assertEqual(format_speaker_name("speaker_00"), "Говорящий 1")
        self.assertEqual(format_speaker_name("speaker_unknown"), "Неизвестный")
        self.assertEqual(format_speaker_name("speaker_05", {"speaker_05": "Анна"}), "Анна")
        self.assertEqual(format_speaker_name("speaker_05", {"speaker_05": {"display_name": "Борис"}}), "Борис")

    def test_merge_diarization_with_segments_basic(self):
        segments = [
            {"from_sec": 0.0, "to_sec": 5.0, "text": "Здравствуйте, коллеги."},
            {"from_sec": 5.5, "to_sec": 10.0, "text": "Добрый день, рад всех видеть."},
        ]
        turns = [
            {"from_sec": 0.0, "to_sec": 5.2, "speaker_id": "speaker_01"},
            {"from_sec": 5.4, "to_sec": 10.5, "speaker_id": "speaker_02"},
        ]

        result = merge_diarization_with_segments(segments, turns)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["speaker_id"], "speaker_01")
        self.assertFalse(result[0]["overlap"])
        self.assertEqual(result[1]["speaker_id"], "speaker_02")
        self.assertFalse(result[1]["overlap"])

    def test_merge_diarization_with_overlap(self):
        segments = [
            {"from_sec": 0.0, "to_sec": 10.0, "text": "Да, конечно, я абсолютно согласен с вами."},
        ]
        # Two speakers speaking simultaneously over significant portion (>= 0.3s each)
        turns = [
            {"from_sec": 0.0, "to_sec": 7.0, "speaker_id": "speaker_01"},
            {"from_sec": 4.0, "to_sec": 10.0, "speaker_id": "speaker_02"},
        ]

        result = merge_diarization_with_segments(segments, turns)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["overlap"])
        # speaker_01 has 7.0s overlap, speaker_02 has 6.0s overlap -> primary is speaker_01
        self.assertEqual(result[0]["speaker_id"], "speaker_01")

    def test_merge_diarization_unmatched_becomes_unknown(self):
        segments = [
            {"from_sec": 20.0, "to_sec": 25.0, "text": "Фрагмент в тишине или без распознанного голоса."},
        ]
        turns = [
            {"from_sec": 0.0, "to_sec": 5.0, "speaker_id": "speaker_01"},
        ]

        result = merge_diarization_with_segments(segments, turns)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["speaker_id"], "speaker_unknown")
        self.assertFalse(result[0]["overlap"])

    def test_merge_diarization_empty(self):
        self.assertEqual(merge_diarization_with_segments([], []), [])
        segments = [{"from_sec": 0.0, "to_sec": 1.0, "text": "тест"}]
        result = merge_diarization_with_segments(segments, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["speaker_id"], "speaker_unknown")


if __name__ == "__main__":
    unittest.main()
