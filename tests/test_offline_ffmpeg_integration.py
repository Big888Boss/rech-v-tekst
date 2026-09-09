"""Offline synthetic integration test using task-local ffmpeg from verified PyPI wheel.
Tests normalization and import slicing on synthetic PCM audio with frame and duration validation.
Strictly offline: zero audio devices, zero network, zero avfoundation.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

from recorder.constants import (
    STATE_READY,
    TARGET_WHISPER_CHANNELS,
    TARGET_WHISPER_RATE,
)
from recorder.normalize import (
    get_audio_metadata,
    import_media_file,
    normalize_chunk,
    validate_and_probe_wav,
)
from tests.isolated_test import IsolatedTestCase

REAL_FFMPEG_PATH = Path("work/task_local_ffmpeg/ffmpeg").resolve()


def generate_synthetic_pcm_wav(file_path: Path, sample_rate: int = 48000, channels: int = 2, duration_sec: float = 3.0) -> None:
    """Generate a clean synthetic sine wave WAV file without hardware audio capture."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        # Generate simple low-frequency alternating samples
        samples = []
        for i in range(num_samples):
            val = int(3000 * ((i % 100) / 50.0 - 1.0))
            for _ in range(channels):
                samples.append(val)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class TestOfflineFFmpegIntegration(IsolatedTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if REAL_FFMPEG_PATH.exists():
            cls.register_allowed_fake_executable(REAL_FFMPEG_PATH)

    def setUp(self) -> None:
        super().setUp()
        if not REAL_FFMPEG_PATH.exists():
            self.skipTest(f"Task-local ffmpeg not found at {REAL_FFMPEG_PATH}")
        self.register_allowed_fake_executable(REAL_FFMPEG_PATH)

    def test_real_ffmpeg_normalize_chunk_synthetic_wav(self) -> None:
        """Verify normalize_chunk converts a 48kHz stereo WAV to 16kHz mono PCM16 with real task-local ffmpeg."""
        raw_wav = self.data_root / "raw_synth_48k.wav"
        norm_wav = self.data_root / "normalized" / "chunk_000.wav"

        # Generate 48kHz stereo, 2.5s duration
        generate_synthetic_pcm_wav(raw_wav, sample_rate=48000, channels=2, duration_sec=2.5)

        # Run normalize_chunk with real ffmpeg binary
        rec = normalize_chunk(
            raw_path=raw_wav,
            out_path=norm_wav,
            ffmpeg_bin=str(REAL_FFMPEG_PATH),
        )

        self.assertEqual(rec["sample_rate"], TARGET_WHISPER_RATE)
        self.assertEqual(rec["channels"], TARGET_WHISPER_CHANNELS)
        self.assertAlmostEqual(rec["duration_sec"], 2.5, places=2)

        # Deep inspect resulting WAV file
        meta = validate_and_probe_wav(norm_wav)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["sample_rate"], 16000)
        self.assertEqual(meta["channels"], 1)
        self.assertEqual(meta["bits_per_sample"], 16)
        self.assertAlmostEqual(meta["duration_sec"], 2.5, places=2)

    def test_real_ffmpeg_import_media_file_synthetic_wav(self) -> None:
        """Verify import_media_file slices and normalizes synthetic media into 1s chunks using real ffmpeg."""
        import_src = self.data_root / "source_audio.wav"
        # Generate 44.1kHz stereo, 3.5s duration
        generate_synthetic_pcm_wav(import_src, sample_rate=44100, channels=2, duration_sec=3.5)

        manifest = import_media_file(
            file_path=import_src,
            session_id="sess_offline_import",
            title="Synthetic Audio Import",
            segment_time_sec=1,
            ffmpeg_bin=str(REAL_FFMPEG_PATH),
        )

        self.assertEqual(manifest.status, STATE_READY)
        self.assertGreaterEqual(len(manifest.normalized_chunks), 3)
        self.assertAlmostEqual(manifest.total_duration_sec, 3.5, places=1)

        # Verify each generated chunk has exact 16kHz mono PCM16 properties
        sess_dir = self.data_root / "sess_offline_import"
        for chunk_rec in manifest.normalized_chunks:
            p = sess_dir / "normalized" / chunk_rec["filename"]
            self.assertTrue(p.exists())
            meta = validate_and_probe_wav(p)
            self.assertIsNotNone(meta)
            assert meta is not None
            self.assertEqual(meta["sample_rate"], 16000)
            self.assertEqual(meta["channels"], 1)
            self.assertEqual(meta["bits_per_sample"], 16)


if __name__ == "__main__":
    unittest.main()
