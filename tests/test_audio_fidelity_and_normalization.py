"""Unit and integration tests for audio fidelity, duration preservation, and offline normalization."""
from __future__ import annotations

import shutil
import struct
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.capture import validate_and_probe_wav
from recorder.constants import OUT_DIR, TARGET_WHISPER_CHANNELS, TARGET_WHISPER_RATE
from recorder.normalize import (
    get_audio_metadata,
    normalize_chunk,
)
from recorder.storage import get_session_dir, safe_make_dir
from tests.isolated_test import IsolatedTestCase


def create_synthetic_wav(
    target_path: Path,
    duration_sec: float = 2.0,
    sample_rate: int = 48000,
    channels: int = 2,
) -> Path:
    """Generate a clean uncompressed PCM WAV file using standard wave module."""
    num_frames = int(duration_sec * sample_rate)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        sample_val = struct.pack("<h", 1200)
        frame_val = sample_val * channels
        wf.writeframes(frame_val * num_frames)
    return target_path


class TestAudioFidelityAndNormalization(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_audio_fidelity_session"
        self.root = self.data_root / self.session_id
        safe_make_dir(self.root)

    def test_wav_validation_and_header_parsing(self) -> None:
        wav_path = self.root / "test_48k.wav"
        create_synthetic_wav(wav_path, duration_sec=3.5, sample_rate=48000, channels=2)

        info = validate_and_probe_wav(wav_path)
        self.assertIsNotNone(info)
        self.assertEqual(info["sample_rate"], 48000)
        self.assertEqual(info["channels"], 2)
        self.assertAlmostEqual(info["duration_sec"], 3.5, places=2)

    def test_corrupted_wav_rejection(self) -> None:
        corrupt_path = self.root / "corrupted.wav"
        corrupt_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt truncated_garbage")

        info = validate_and_probe_wav(corrupt_path)
        self.assertIsNone(info)

    def test_truncated_claimed_header_wav_rejection(self) -> None:
        # Create a valid wav header claiming 100,000 frames (400,000 bytes) but truncate payload to 100 bytes
        trunc_path = self.root / "truncated_crash_tail.wav"
        # 44-byte canonical WAV header:
        # Channels: 2, SampleRate: 48000, BitsPerSample: 16, BlockAlign: 4, ByteRate: 192000
        # data chunk claimed size: 400000 bytes
        header = (
            b"RIFF"
            + struct.pack("<I", 400036)  # ChunkSize
            + b"WAVE"
            + b"fmt "
            + struct.pack("<I", 16)       # Subchunk1Size
            + struct.pack("<H", 1)        # AudioFormat (PCM)
            + struct.pack("<H", 2)        # NumChannels
            + struct.pack("<I", 48000)    # SampleRate
            + struct.pack("<I", 192000)   # ByteRate
            + struct.pack("<H", 4)        # BlockAlign
            + struct.pack("<H", 16)       # BitsPerSample
            + b"data"
            + struct.pack("<I", 400000)   # Subchunk2Size (Claimed 400,000 bytes)
            + b"\x00" * 60               # Only 60 bytes of audio payload! Total size: 104 bytes!
        )
        trunc_path.write_bytes(header)
        self.assertEqual(trunc_path.stat().st_size, 104)

        # Must be rejected because file is physically truncated before claimed frames
        info = validate_and_probe_wav(trunc_path)
        self.assertIsNone(info)

    @patch("recorder.normalize.run_guarded_command")
    def test_normalization_duration_preservation(self, mock_guarded) -> None:
        raw_wav = self.root / "raw_chunk_000.wav"
        create_synthetic_wav(raw_wav, duration_sec=4.0, sample_rate=48000, channels=2)

        norm_wav = self.root / "chunk_000.wav"

        # Mock ffmpeg creating normalized file at temp_out (cmd[-1])
        def fake_ffmpeg(cmd, **kwargs):
            out_file = Path(cmd[-1])
            create_synthetic_wav(out_file, duration_sec=4.0, sample_rate=16000, channels=1)
            return 0, "", ""

        mock_guarded.side_effect = fake_ffmpeg

        rec = normalize_chunk(raw_wav, norm_wav)

        self.assertTrue(norm_wav.exists())
        self.assertEqual(rec["sample_rate"], TARGET_WHISPER_RATE)
        self.assertEqual(rec["channels"], TARGET_WHISPER_CHANNELS)
        self.assertAlmostEqual(rec["duration_sec"], 4.0, places=2)

    @patch("recorder.normalize.run_guarded_command")
    def test_normalization_mismatch_raises_error_and_preserves_old(self, mock_guarded) -> None:
        raw_wav = self.root / "raw_chunk_001.wav"
        create_synthetic_wav(raw_wav, duration_sec=5.0, sample_rate=48000, channels=2)

        norm_wav = self.root / "chunk_001.wav"
        create_synthetic_wav(norm_wav, duration_sec=5.0, sample_rate=16000, channels=1)
        original_mtime = norm_wav.stat().st_mtime

        # Mock ffmpeg returning a shortened file (2.0s vs 5.0s)
        def fake_ffmpeg_short(cmd, **kwargs):
            out_file = Path(cmd[-1])
            create_synthetic_wav(out_file, duration_sec=2.0, sample_rate=16000, channels=1)
            return 0, "", ""

        mock_guarded.side_effect = fake_ffmpeg_short

        with self.assertRaises(RuntimeError) as ctx:
            normalize_chunk(raw_wav, norm_wav)

        self.assertIn("Duration mismatch", str(ctx.exception))
        self.assertEqual(norm_wav.stat().st_mtime, original_mtime)


if __name__ == "__main__":
    unittest.main()
