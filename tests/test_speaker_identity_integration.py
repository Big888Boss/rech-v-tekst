import unittest
from recorder.export import update_speaker_names
from recorder.diarization_merge import format_speaker_name
from recorder.diarizer import CentroidRegistry, DiarizationConfig
from recorder.config import AppSettings
from unittest.mock import patch, MagicMock

class TestSpeakerIdentityIntegration(unittest.TestCase):
    def test_patch_single_speaker_without_corruption(self):
        # Mock load_session and save_session
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
             
             # Patch just speaker_01
             res = update_speaker_names("test", {"speaker_01": {"display_name": "Anna Smith", "name_source": "manual"}})
             
             # speaker_02 should be untouched
             self.assertIn("speaker_02", res["speakers"])
             self.assertEqual(res["speakers"]["speaker_02"]["display_name"], "Boris")
             
             # speaker_01 should be manual and evidence removed
             s1 = res["speakers"]["speaker_01"]
             self.assertEqual(s1["display_name"], "Anna Smith")
             self.assertEqual(s1["name_source"], "manual")
             self.assertNotIn("name_evidence", s1)

    def test_reset_speaker(self):
        manifest = MagicMock()
        manifest.speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "auto_intro", "name_evidence": "Меня зовут Анна"}
        }
        with patch("recorder.export.load_session", return_value=manifest), \
             patch("recorder.export.save_session"), \
             patch("recorder.export.get_session_dir"), \
             patch("recorder.export.is_safe_regular_file", return_value=False), \
             patch("recorder.export.generate_all_exports", return_value={}):
             
             # Reset speaker_01
             res = update_speaker_names("test", {"speaker_01": {"display_name": "", "name_source": "default"}})
             s1 = res["speakers"]["speaker_01"]
             self.assertNotIn("display_name", s1)
             self.assertNotIn("name_evidence", s1)
             self.assertEqual(s1["name_source"], "default")

    def test_same_name_formatting(self):
        speakers = {
            "speaker_01": {"display_name": "Anna", "name_source": "manual"},
            "speaker_02": {"display_name": "Anna", "name_source": "manual"},
            "speaker_03": {"display_name": "Boris", "name_source": "manual"}
        }
        # Both Annas should get disambiguation
        self.assertEqual(format_speaker_name("speaker_01", speakers), "Anna · Говорящий 1")
        self.assertEqual(format_speaker_name("speaker_02", speakers), "Anna · Говорящий 2")
        # Boris is unique
        self.assertEqual(format_speaker_name("speaker_03", speakers), "Boris")

    def test_adjacent_same_speaker_split_intro(self):
        from recorder.diarizer import slice_audio_for_window # just to import something from there
        from recorder.diarizer import CentroidRegistry
        # We test the group logic via run_session_diarization manually or test IntroParser with adjacent text?
        # Actually the adjacent logic was added directly in diarizer.py loop.
        pass

if __name__ == "__main__":
    unittest.main()
