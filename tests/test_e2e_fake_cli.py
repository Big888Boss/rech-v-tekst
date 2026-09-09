"""End-to-end integration tests using fake CLIs for capture, normalization, whisper, and export."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.capture import CaptureManager
from recorder.cli import cli_process, cli_record
from recorder.constants import OUT_DIR, STATE_COMPLETED, STATE_READY
from recorder.export import generate_all_exports
from recorder.session import SessionManifest, create_session, load_session, save_session
from recorder.storage import get_session_dir, safe_make_dir
from recorder.transcribe import TranscriptionManager
from tests.isolated_test import IsolatedTestCase
from tests.test_audio_fidelity_and_normalization import create_synthetic_wav


class TestE2EFakeCLI(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_e2e_fake_cli_sess"
        self.sess_dir = self.data_root / self.session_id
        self.raw_dir = self.sess_dir / "raw"
        self.norm_dir = self.sess_dir / "normalized"

    def test_full_pipeline_capture_normalize_transcribe_export(self) -> None:
        manifest = create_session(self.session_id, title="E2E Pipeline Test")

        # Step 1: Create session with two closed raw chunks and journal.csv
        raw0 = self.raw_dir / "raw_chunk_000.wav"
        raw1 = self.raw_dir / "raw_chunk_001.wav"
        create_synthetic_wav(raw0, duration_sec=3.0, sample_rate=48000, channels=2)
        create_synthetic_wav(raw1, duration_sec=3.0, sample_rate=48000, channels=2)

        journal = self.raw_dir / "journal.csv"
        journal.write_text("raw_chunk_000.wav,0.0,3.0\nraw_chunk_001.wav,3.0,6.0\n", encoding="utf-8")

        cap_mgr = CaptureManager()
        manifest = cap_mgr._index_raw_chunks(manifest, is_stopped=True)
        save_session(manifest)

        self.assertEqual(len(manifest.raw_chunks), 2)
        self.assertEqual(manifest.raw_chunks[0]["sample_rate"], 48000)
        self.assertAlmostEqual(manifest.total_duration_sec, 6.0, places=1)

        # Step 2: Create a fake whisper CLI script
        fake_bin_dir = self.sess_dir / "fake_bin"
        fake_bin_dir.mkdir()
        fake_whisper = fake_bin_dir / "fake_whisper"
        fake_whisper.write_text("""#!/bin/sh
out_base=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-of" ]; then
    out_base="$2"
    shift 2
  else
    shift
  fi
done
if [ -n "$out_base" ]; then
  echo "Тестовый сегмент речи" > "${out_base}.txt"
  echo '{"transcription":[{"offsets":{"from":0,"to":3000},"text":"Тестовый сегмент речи"}]}' > "${out_base}.json"
fi
exit 0
""", encoding="utf-8")
        fake_whisper.chmod(0o755)
        self.register_allowed_fake_executable(fake_whisper)

        dummy_model = self.sess_dir / "model.bin"
        dummy_model.write_bytes(b"MODEL")

        # Mock normalization to create synthetic 16kHz WAVs
        for idx in (0, 1):
            create_synthetic_wav(self.norm_dir / f"chunk_{idx:03d}.wav", duration_sec=3.0, sample_rate=16000, channels=1)

        # Step 3: Run transcription manager
        from recorder.media_tools import MediaToolsStatus
        fake_status = MediaToolsStatus(
            ffmpeg_path="/usr/bin/ffmpeg",
            ffprobe_path="/usr/bin/ffprobe",
            ffmpeg=True,
            ffprobe=True,
            ready=True,
            missing=(),
            remediation=None,
            whisper=True,
            model=True,
            whisper_path=str(fake_whisper),
            model_path=str(dummy_model),
            whisper_version="whisper.cpp version: 1.9.3",
            model_size_bytes=len(b"MODEL"),
            model_state="verified",
            model_error=None,
            whisper_error=None,
        )

        tm = TranscriptionManager()
        with patch("recorder.media_tools.get_media_tools_status", return_value=fake_status), \
             patch("recorder.transcribe.normalize_chunk") as mock_norm:
            def fake_norm(raw_f, target_norm, **kw):
                create_synthetic_wav(target_norm, duration_sec=3.0, sample_rate=16000, channels=1)
                return {
                    "filename": target_norm.name,
                    "duration_sec": 3.0,
                    "sample_rate": 16000,
                    "channels": 1,
                    "size_bytes": 96044,
                }
            mock_norm.side_effect = fake_norm
            manifest = tm.transcribe_session(
                session_id=self.session_id,
                model_path=dummy_model,
                whisper_bin=str(fake_whisper),
            )

        self.assertEqual(manifest.status, STATE_COMPLETED)
        self.assertGreater(manifest.word_count, 0)

        # Step 4: Verify generated transcripts and export files
        t_file = self.sess_dir / "transcript.txt"
        self.assertTrue(t_file.exists())
        self.assertIn("Тестовый сегмент речи", t_file.read_text(encoding="utf-8"))

        exports = generate_all_exports(self.session_id)
        self.assertTrue(Path(exports["srt"]).exists())
        self.assertTrue(Path(exports["vtt"]).exists())
        self.assertTrue(Path(exports["md"]).exists())
        self.assertTrue(Path(exports["json"]).exists())

        # Check SRT content format
        srt_text = Path(exports["srt"]).read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:03,000", srt_text)
        self.assertIn("Тестовый сегмент речи", srt_text)

        # Check VTT content format
        vtt_text = Path(exports["vtt"]).read_text(encoding="utf-8")
        self.assertTrue(vtt_text.startswith("WEBVTT"))


class TestCLIEntrypoint(IsolatedTestCase):
    def test_cli_process_import_media_dependencies_and_cancellation(self) -> None:
        """Verify cli_process handles media file import without NameError and wires cancel_event/on_proc_start."""
        dummy_media = self.data_root / "sample_lecture.m4a"
        dummy_media.write_bytes(b"FAKE_AUDIO_DATA_FOR_IMPORT")

        called_on_proc = []
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        def fake_import(path, session_id=None, title=None, cancel_event=None, on_proc_start=None):
            self.assertIsInstance(cancel_event, threading.Event)
            self.assertFalse(cancel_event.is_set())
            if on_proc_start:
                on_proc_start(fake_proc)
                called_on_proc.append(fake_proc)
            m = SessionManifest(
                session_id=session_id or "import_test",
                title=title or "sample_lecture.m4a",
                status=STATE_READY,
            )
            save_session(m)
            return m

        args = argparse.Namespace(
            target=str(dummy_media),
            lang="ru",
            summary_provider="none",
            template="meeting",
        )

        with patch("recorder.cli.import_user_media_file", side_effect=fake_import):
            with patch("recorder.cli.TRANSCRIBE_MANAGER.transcribe_session") as mock_trans:
                def fake_transcribe(sess_id, **kw):
                    m = SessionManifest(session_id=sess_id, status=STATE_COMPLETED, word_count=42)
                    save_session(m)
                    return m
                mock_trans.side_effect = fake_transcribe

                with patch("recorder.cli.generate_all_exports", return_value={"txt": "out/test/t.txt"}):
                    rc = cli_process(args)
                    self.assertEqual(rc, 0)
                    self.assertEqual(len(called_on_proc), 1)

    def test_cli_process_import_signal_handling(self) -> None:
        """Verify SIGINT/SIGTERM during media import sets cancel_event and invokes stop_and_reap."""
        dummy_media = self.data_root / "sample_test.wav"
        dummy_media.write_bytes(b"FAKE_WAV")

        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        reaped = []

        def fake_stop(proc, timeout_sec=1.5):
            reaped.append(proc)

        def fake_import_raising(path, session_id=None, title=None, cancel_event=None, on_proc_start=None):
            on_proc_start(fake_proc)
            handler = signal.getsignal(signal.SIGINT)
            self.assertTrue(callable(handler))
            handler(signal.SIGINT, None)
            self.assertTrue(cancel_event.is_set())
            raise KeyboardInterrupt("Simulated Ctrl+C")

        args = argparse.Namespace(
            target=str(dummy_media),
            lang="ru",
            summary_provider="none",
            template="meeting",
        )

        with patch("recorder.cli.import_user_media_file", side_effect=fake_import_raising):
            with patch("recorder.capture.stop_and_reap_process_group", side_effect=fake_stop):
                rc = cli_process(args)
                self.assertEqual(rc, 1)
                self.assertIn(fake_proc, reaped)

    def test_cli_record_test_flag_duration(self) -> None:
        """Verify cli_record --test calls volume check for 3 seconds."""
        args = argparse.Namespace(
            kind="mic",
            mic=1,
            list=False,
            test=True,
        )
        with patch("recorder.cli.find_device", return_value={"index": 1, "name": "Fake Mic", "kind": "mic"}):
            with patch("recorder.cli.run_volume_check", return_value={"status": "ok", "mean_volume_db": -20.0, "max_volume_db": -5.0}) as mock_vc:
                rc = cli_record(args)
                self.assertEqual(rc, 0)
                mock_vc.assert_called_once_with(1, duration_sec=3.0)


if __name__ == "__main__":
    unittest.main()
