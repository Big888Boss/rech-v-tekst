import unittest
from recorder.export import update_speaker_names
from recorder.diarization_merge import format_speaker_name
from recorder.config import AppSettings, load_settings, save_settings
from recorder.http_server import STATE
import tempfile
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

class TestSpeakerIdentityIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        # Mock settings paths
        self.settings_path = os.path.join(self.tmp_dir.name, "settings.json")
        from pathlib import Path
        patcher = patch("recorder.config.get_data_root", return_value=Path(self.tmp_dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_patch_single_speaker_without_corruption(self):
        manifest = MagicMock()
        manifest.speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "auto_intro", "name_evidence": "Меня зовут Анна"},
            "speaker_02": {"display_name": "Boris", "name_source": "manual"}
        }
        with patch("recorder.export.load_session", return_value=manifest), \
             patch("recorder.export.save_session"), \
             patch("recorder.export.get_session_dir"), \
             patch("recorder.export.is_safe_regular_file", return_value=False), \
             patch("recorder.export.generate_all_exports", return_value={}):

             res = update_speaker_names("test", {"speaker_01": {"display_name": "Anna Smith", "name_source": "manual"}})
             self.assertIn("speaker_02", res["speakers"])
             self.assertEqual(res["speakers"]["speaker_02"]["display_name"], "Boris")
             s1 = res["speakers"]["speaker_01"]
             self.assertEqual(s1["display_name"], "Anna Smith")
             self.assertEqual(s1["name_source"], "manual")
             self.assertNotIn("name_evidence", s1)

    def test_reset_speaker(self):
        manifest = MagicMock()
        manifest.speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "auto_intro", "name_evidence": "Меня зовут Анна", "detected_at": 123.0}
        }
        with patch("recorder.export.load_session", return_value=manifest), \
             patch("recorder.export.save_session"), \
             patch("recorder.export.get_session_dir"), \
             patch("recorder.export.is_safe_regular_file", return_value=False), \
             patch("recorder.export.generate_all_exports", return_value={}):

             res = update_speaker_names("test", {"speaker_01": {"display_name": "", "name_source": "default"}})
             s1 = res["speakers"]["speaker_01"]
             self.assertNotIn("display_name", s1)
             self.assertNotIn("name_evidence", s1)
             self.assertNotIn("detected_at", s1)
             self.assertEqual(s1["name_source"], "default")

    def test_same_name_formatting(self):
        speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "manual"},
            "speaker_02": {"display_name": "Anna", "name_source": "manual"},
            "speaker_03": {"display_name": "Boris", "name_source": "manual"}
        }
        self.assertEqual(format_speaker_name("speaker_01", speakers), "Anna · Говорящий 1")
        self.assertEqual(format_speaker_name("speaker_02", speakers), "Anna · Говорящий 2")
        self.assertEqual(format_speaker_name("speaker_03", speakers), "Boris")

    def test_setting_false_save_reload(self):
        # Save false
        s = load_settings()
        s.enable_auto_intro = False
        save_settings(s)
        # Reload
        s2 = load_settings()
        self.assertFalse(s2.enable_auto_intro)

    def test_third_party_no_rename(self):
        from recorder.intro_parser import IntroParser
        parser = IntroParser()
        # "Ее зовут Анна" should be rejected by the negative patterns
        match = parser.parse_intro("А ее зовут Анна.")
        self.assertIsNone(match)


    def test_adjacent_same_speaker_split_intro(self):


        # We simulate the grouping logic inside run_session_diarization manually since it's hard to mock full audio
        merged_segments = [
            {"speaker_id": "speaker_01", "text": "Меня зовут", "from_sec": 0.0},
            {"speaker_id": "speaker_01", "text": "Анна Иванова", "from_sec": 2.0}
        ]

        from recorder.intro_parser import IntroParser
        parser = IntroParser()

        # The logic in diarizer.py:
        grouped = []
        for seg in merged_segments:
            spk = seg.get("speaker_id")
            txt = seg.get("text", "").strip()
            if grouped and grouped[-1] and grouped[-1]["speaker_id"] == spk:
                grouped[-1]["text"] += " " + txt
            else:
                grouped.append({"speaker_id": spk, "text": txt})

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["text"], "Меня зовут Анна Иванова")

        match = parser.parse_intro(grouped[0]["text"])
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Анна Иванова")

    def test_export_json_provenance_with_timestamp(self):
        manifest = MagicMock()
        manifest.speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "auto_intro", "name_evidence": "Меня зовут Анна", "detected_at": 1690000000.0}
        }
        with patch("recorder.export.load_session", return_value=manifest), \
             patch("recorder.export.save_session") as mock_save, \
             patch("recorder.export.get_session_dir", return_value=Path(os.path.join(self.tmp_dir.name, "sessions", "test_session"))), \
             patch("recorder.export.is_safe_regular_file", return_value=True), \
             patch("recorder.export.generate_all_exports", return_value={}):

             update_speaker_names("test_session", {"speaker_01": manifest.speakers["speaker_01"]})

             saved_manifest = mock_save.call_args[0][0]
             spk = saved_manifest.speakers["speaker_01"]
             self.assertEqual(spk["name_source"], "auto_intro")
             self.assertEqual(spk["name_evidence"], "Меня зовут Анна")
             self.assertEqual(spk["detected_at"], 1690000000.0)

if __name__ == "__main__":
    unittest.main()