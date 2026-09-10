"""Tests for all 5 export formats with speaker diarization labels (TXT, MD, SRT, VTT, JSON)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from recorder.constants import get_data_root, set_data_root
from recorder.export import (
    export_markdown,
    export_srt,
    export_txt,
    export_vtt,
    generate_all_exports,
    update_speaker_names,
)
from recorder.session import SessionManifest, save_session


class TestDiarizationExports(unittest.TestCase):
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

    def test_export_srt_with_speakers_and_overlap(self):
        segments = [
            {"from_sec": 1.0, "to_sec": 4.5, "text": "Первая реплика", "speaker_id": "speaker_01", "overlap": False},
            {"from_sec": 5.0, "to_sec": 8.2, "text": "Вторая реплика", "speaker_id": "speaker_02", "overlap": True},
        ]
        speakers = {
            "speaker_01": {"display_name": "Алексей"},
            "speaker_02": {"display_name": "Мария"},
        }
        srt = export_srt(segments, speakers=speakers)
        self.assertIn("00:00:01,000 --> 00:00:04,500", srt)
        self.assertIn("Алексей: Первая реплика", srt)
        self.assertIn("[Наложение] Мария: Вторая реплика", srt)

    def test_export_vtt_with_speakers_and_overlap(self):
        segments = [
            {"from_sec": 0.0, "to_sec": 3.0, "text": "Начало", "speaker_id": "speaker_01", "overlap": False},
            {"from_sec": 3.5, "to_sec": 6.0, "text": "Перебивание", "speaker_id": "speaker_02", "overlap": True},
        ]
        speakers = {
            "speaker_01": "Иван",
            "speaker_02": "Ольга",
        }
        vtt = export_vtt(segments, speakers=speakers)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("<v Иван>Начало</v>", vtt)
        self.assertIn("[Наложение] <v Ольга>Перебивание</v>", vtt)

    def test_export_markdown_with_speakers(self):
        manifest = SessionManifest(session_id="test_sess", title="Совещание команды")
        segments = [
            {"from_sec": 10.0, "to_sec": 15.0, "text": "Обсуждаем релиз", "speaker_id": "speaker_01"},
            {"from_sec": 16.0, "to_sec": 22.0, "text": "Тесты пройдены", "speaker_id": "speaker_02"},
        ]
        speakers = {
            "speaker_01": {"display_name": "Тимлид"},
            "speaker_02": {"display_name": "QA Инженер"},
        }
        md = export_markdown(manifest, segments, full_text="Обсуждаем релиз Тесты пройдены", speakers=speakers)
        self.assertIn("# Транскрипт: Совещание команды", md)
        self.assertIn("### [00:00:10.000] Тимлид", md)
        self.assertIn("Обсуждаем релиз", md)
        self.assertIn("### [00:00:16.000] QA Инженер", md)
        self.assertIn("Тесты пройдены", md)

    def test_export_txt_structured_with_speakers(self):
        segments = [
            {"from_sec": 0.0, "to_sec": 5.0, "text": "Привет", "speaker_id": "speaker_01"},
        ]
        speakers = {"speaker_01": {"display_name": "Спикер"}}
        txt = export_txt(segments, full_text="Привет", speakers=speakers)
        self.assertIn("[00:00:00.000] Спикер: Привет", txt)

    def test_generate_all_exports_and_update_speaker_names(self):
        sid = "sess_export_001"
        s_dir = self.test_dir / sid
        s_dir.mkdir(parents=True)

        manifest = SessionManifest(
            session_id=sid,
            title="Тестовая сессия экспорта",
            speakers={"speaker_01": {"display_name": "Говорящий 1"}},
        )
        save_session(manifest)

        (s_dir / "transcript.txt").write_text("Привет мир. Вторая фраза.", encoding="utf-8")
        t_json = {
            "schema_version": 2,
            "session_id": sid,
            "diarization": {
                "status": "completed",
                "speakers": {"speaker_01": {"display_name": "Говорящий 1"}},
            },
            "segments": [
                {"from_sec": 0.0, "to_sec": 3.0, "text": "Привет мир.", "speaker_id": "speaker_01", "speaker": "speaker_01"},
                {"from_sec": 3.5, "to_sec": 6.0, "text": "Вторая фраза.", "speaker_id": "speaker_01", "speaker": "speaker_01"},
            ],
        }
        (s_dir / "transcript.json").write_text(json.dumps(t_json, ensure_ascii=False), encoding="utf-8")

        # 1. Generate all initial exports
        exp_paths = generate_all_exports(sid)
        for ext in ("txt", "srt", "vtt", "md"):
            self.assertTrue(Path(exp_paths[ext]).exists())
            content = Path(exp_paths[ext]).read_text(encoding="utf-8")
            self.assertIn("Говорящий 1", content)

        # 2. Update speaker name to custom name
        res = update_speaker_names(sid, {"speaker_01": "Дмитрий"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["speakers"]["speaker_01"]["display_name"], "Дмитрий")

        # 3. Verify that exports were atomically regenerated with new name
        srt_updated = (s_dir / "transcript.srt").read_text(encoding="utf-8")
        self.assertIn("Дмитрий: Привет мир.", srt_updated)

        vtt_updated = (s_dir / "transcript.vtt").read_text(encoding="utf-8")
        self.assertIn("<v Дмитрий>Привет мир.</v>", vtt_updated)

        md_updated = (s_dir / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("Дмитрий", md_updated)

        json_updated = json.loads((s_dir / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(json_updated["diarization"]["speakers"]["speaker_01"]["display_name"], "Дмитрий")


if __name__ == "__main__":
    unittest.main()
