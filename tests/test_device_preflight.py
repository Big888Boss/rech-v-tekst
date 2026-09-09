"""Unit tests for audio device enumeration, volume parsing, and read-only preflight."""
from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock, patch

from recorder.device import (
    parse_volume_level,
    preflight_audio,
)
from tests.isolated_test import IsolatedTestCase


class TestDevicePreflight(IsolatedTestCase):
    def test_parse_volume_level(self) -> None:
        self.assertEqual(parse_volume_level("-12.5"), -12.5)
        self.assertEqual(parse_volume_level("0.0"), 0.0)
        self.assertEqual(parse_volume_level("-85.2"), -85.2)
        # -inf handling (fix B12)
        self.assertEqual(parse_volume_level("-inf"), -math.inf)
        self.assertEqual(parse_volume_level("-Inf"), -math.inf)
        self.assertEqual(parse_volume_level(None), -math.inf)
        self.assertEqual(parse_volume_level("invalid"), -math.inf)

    @patch("recorder.device.list_audio_devices")
    @patch("recorder.device.probe_device_stream_info")
    @patch("recorder.device.run_volume_check")
    def test_read_only_preflight_does_not_capture(self, mock_vol, mock_probe, mock_list) -> None:
        mock_list.return_value = ([{"index": 1, "name": "BlackHole 2ch", "kind": "blackhole"}], "")

        # Call with test_volume=False (read-only)
        res = preflight_audio(kind="blackhole", test_volume=False)

        # Must NOT call probe or volume check!
        mock_probe.assert_not_called()
        mock_vol.assert_not_called()

        # Permissions and stream rate must remain untested/unknown, not assumed 48000
        self.assertEqual(res["permissions"], "enumerated_capture_untested")
        self.assertIsNone(res["native_stream"]["sample_rate"])
        self.assertFalse(res["native_stream"]["probed"])
        self.assertIsNone(res["volume_check"])
        self.assertTrue(res["ready"])

    @patch("recorder.device.list_audio_devices")
    @patch("recorder.device.probe_device_stream_info")
    @patch("recorder.device.run_volume_check")
    def test_explicit_test_action_runs_checks(self, mock_vol, mock_probe, mock_list) -> None:
        mock_list.return_value = ([{"index": 2, "name": "USB Mic", "kind": "external_mic"}], "")
        mock_probe.return_value = {"sample_rate": 44100, "channels": 1, "format": "pcm_s16le", "probed": True}
        mock_vol.return_value = {"status": "ok", "max_volume_db": -14.2, "mean_volume_db": -26.0}

        # Call with test_volume=True (explicit test action)
        res = preflight_audio(kind="mic", test_volume=True)

        mock_probe.assert_called_once()
        mock_vol.assert_called_once()

        self.assertEqual(res["permissions"], "granted")
        self.assertEqual(res["native_stream"]["sample_rate"], 44100)
        self.assertEqual(res["volume_check"]["max_volume_db"], -14.2)
        self.assertTrue(res["ready"])

    def test_find_device_zoom_selects_blackhole_and_not_mic0(self) -> None:
        from recorder.device import find_device
        fake_devs = [
            {"index": 0, "name": "Built-in Microphone", "kind": "builtin_mic"},
            {"index": 7, "name": "BlackHole 2ch", "kind": "blackhole"},
        ]
        # CLI03: zoom must select blackhole (index 7), NEVER fallback to mic (index 0)
        target = find_device(kind="zoom", devices=fake_devs)
        self.assertIsNotNone(target)
        self.assertEqual(target["index"], 7)
        self.assertEqual(target["kind"], "blackhole")

        target_bh = find_device(kind="blackhole", devices=fake_devs)
        self.assertIsNotNone(target_bh)
        self.assertEqual(target_bh["index"], 7)

        target_mic = find_device(kind="mic", devices=fake_devs)
        self.assertIsNotNone(target_mic)
        self.assertEqual(target_mic["index"], 0)

        # Unknown kind must return None, not fallback to devices[0]
        self.assertIsNone(find_device(kind="unknown_xyz", devices=fake_devs))

    @patch("recorder.device.list_audio_devices")
    @patch("recorder.cli.run_volume_check")
    def test_cli_record_test_zoom_selects_blackhole(self, mock_vol, mock_list) -> None:
        import argparse
        from recorder.cli import cli_record
        fake_devs = [
            {"index": 0, "name": "Built-in Microphone", "kind": "builtin_mic"},
            {"index": 7, "name": "BlackHole 2ch", "kind": "blackhole"},
        ]
        mock_list.return_value = (fake_devs, "")
        mock_vol.return_value = {"status": "ok", "max_volume_db": -10.0, "mean_volume_db": -20.0}

        args = argparse.Namespace(
            kind="zoom",
            test=True,
            mic=None,
            list=False,
            session=None,
            session_flag=None,
        )
        exit_code = cli_record(args)
        self.assertEqual(exit_code, 0)
        # Verify run_volume_check was invoked with index 7 (BlackHole), NOT index 0!
        mock_vol.assert_called_once_with(7, duration_sec=3.0)

    def test_device_command_construction_uses_resolved_binary_and_fails_closed(self) -> None:
        """Verify device.py resolves ffmpeg via media_tools and fails closed on nonexistent explicit bin."""
        self._patch_list_devices.stop()
        self._patch_probe.stop()
        self._patch_volume.stop()
        try:
            import recorder.device as dev_mod

            # 1. Non-existent explicit binary must fail closed without fallback
            devs, err = dev_mod.list_audio_devices(ffmpeg_bin="nonexistent_fake_ffmpeg")
            self.assertEqual(devs, [])
            self.assertIn("ffmpeg binary not found", err)

            probe_res = dev_mod.probe_device_stream_info(device_index=0, ffmpeg_bin="nonexistent_fake_ffmpeg")
            self.assertFalse(probe_res["probed"])
            self.assertEqual(probe_res["error"], "ffmpeg not found")

            vol_res = dev_mod.run_volume_check(device_index=0, ffmpeg_bin="nonexistent_fake_ffmpeg")
            self.assertEqual(vol_res["status"], "error")
            self.assertIn("nonexistent_fake_ffmpeg binary not found", vol_res["message"])

            # 2. Existing fake binary is resolved and placed directly in command argv[0]
            fake_bin = self.data_root / "fake_ffmpeg_bin"
            fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_bin.chmod(0o755)
            resolved_str = str(fake_bin.resolve())
            self.register_allowed_fake_executable(fake_bin)

            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.stderr = "AVFoundation audio devices:\n[0] Fake Mic"
                mock_proc.returncode = 0
                mock_run.return_value = mock_proc

                dev_mod.list_audio_devices(ffmpeg_bin=str(fake_bin))
                mock_run.assert_called_once()
                self.assertEqual(mock_run.call_args[0][0][0], resolved_str)

            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.stderr = "Audio: pcm_s16le, 44100 Hz, stereo"
                mock_proc.returncode = 0
                mock_run.return_value = mock_proc

                dev_mod.probe_device_stream_info(device_index=0, ffmpeg_bin=str(fake_bin))
                mock_run.assert_called_once()
                self.assertEqual(mock_run.call_args[0][0][0], resolved_str)

            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.stderr = "max_volume: -12.0 dB\nmean_volume: -24.0 dB"
                mock_proc.returncode = 0
                mock_run.return_value = mock_proc

                dev_mod.run_volume_check(device_index=0, duration_sec=1.0, ffmpeg_bin=str(fake_bin))
                mock_run.assert_called_once()
                self.assertEqual(mock_run.call_args[0][0][0], resolved_str)
        finally:
            self._patch_list_devices.start()
            self._patch_probe.start()
            self._patch_volume.start()

    def test_capture_command_construction_uses_resolved_binary_and_fails_closed(self) -> None:
        """Verify capture.py resolves ffmpeg via media_tools and fails closed on nonexistent explicit bin."""
        from recorder.capture import CAPTURE_MANAGER

        # 1. Non-existent explicit binary raises RuntimeError fail-closed without silent fallback
        with self.assertRaises(RuntimeError) as ctx:
            CAPTURE_MANAGER.start_capture("sess_nonexistent_bin", ffmpeg_bin="nonexistent_custom_ffmpeg")
        self.assertIn("FFmpeg binary not found ('nonexistent_custom_ffmpeg')", str(ctx.exception))

        # 2. Existing fake binary is placed in the constructed capture command
        fake_bin = self.data_root / "fake_capture_ffmpeg"
        fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_bin.chmod(0o755)
        resolved_str = str(fake_bin.resolve())
        self.register_allowed_fake_executable(fake_bin)

        with patch("recorder.capture.launch_guarded_process") as mock_launch:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_proc.returncode = 0
            mock_launch.return_value = mock_proc

            with patch("recorder.capture.find_device", return_value={"index": 0, "name": "Fake Device", "kind": "mic"}):
                with patch(
                    "recorder.capture.probe_device_stream_info",
                    return_value={"sample_rate": 48000, "channels": 2, "format": "pcm_s16le", "probed": True},
                ):
                    manifest = CAPTURE_MANAGER.start_capture(
                        "sess_resolved_bin_test",
                        ffmpeg_bin=str(fake_bin),
                    )
                    self.assertIsNotNone(manifest)
                    mock_launch.assert_called_once()
                    cmd_called = mock_launch.call_args[0][0]
                    if cmd_called[0] == "caffeinate":
                        self.assertEqual(cmd_called[2], resolved_str)
                    else:
                        self.assertEqual(cmd_called[0], resolved_str)

                    # Clean stop
                    with patch("recorder.capture.stop_and_reap_process_group"):
                        with patch("recorder.capture.is_process_alive_and_ours", return_value=False):
                            CAPTURE_MANAGER.stop_capture("sess_resolved_bin_test")

    def test_find_device_builtin_mic_russian_name_and_missing_blackhole(self) -> None:
        """Verify device selection with only builtin_mic (Russian name, no second mic)."""
        from recorder.device import find_device

        fake_devs = [
            {"index": 1, "name": "Микрофон MacBook Pro", "kind": "builtin_mic"}
        ]

        # 1. Zoom/BlackHole must return None (not fall back to mic or index 1)
        bh_dev = find_device(kind="zoom", devices=fake_devs)
        self.assertIsNone(bh_dev)

        bh_explicit = find_device(kind="blackhole", devices=fake_devs)
        self.assertIsNone(bh_explicit)

        # 2. Mic must find builtin_mic with index 1
        mic_dev = find_device(kind="mic", devices=fake_devs)
        self.assertIsNotNone(mic_dev)
        self.assertEqual(mic_dev["index"], 1)
        self.assertEqual(mic_dev["kind"], "builtin_mic")
        self.assertEqual(mic_dev["name"], "Микрофон MacBook Pro")

        # 3. Explicit index matching
        explicit_dev = find_device(index=1, devices=fake_devs)
        self.assertIsNotNone(explicit_dev)
        self.assertEqual(explicit_dev["index"], 1)

        explicit_missing = find_device(index=7, devices=fake_devs)
        self.assertIsNone(explicit_missing)


if __name__ == "__main__":
    unittest.main()
