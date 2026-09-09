"""Integration tests for HTTP server security: Host/Origin, CSRF, upload reservation, and downloads."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from recorder.constants import BASE_DIR, OUT_DIR, get_data_root, set_data_root
from recorder.http_server import STATE, HardenedHTTPHandler
from recorder.session import create_session
from recorder.storage import atomic_write_text, get_session_dir


class TestHTTPServerSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._prev_root = get_data_root()
        cls.addClassCleanup(set_data_root, cls._prev_root)

        cls._orig_env = dict(os.environ)
        def _restore_env():
            os.environ.clear()
            os.environ.update(cls._orig_env)
        cls.addClassCleanup(_restore_env)

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_dir.cleanup)

        cls.data_root = Path(cls.temp_dir.name)
        set_data_root(cls.data_root)
        os.environ["WEBINAR_OUT_DIR"] = str(cls.data_root)
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"

        # Device & backend isolation barrier
        cls._patch_list = patch(
            "recorder.device.list_audio_devices",
            return_value=([{"index": 0, "name": "Fake Mock Mic", "kind": "mic"}], ""),
        )
        cls._patch_probe = patch(
            "recorder.device.probe_device_stream_info",
            return_value={"sample_rate": 48000, "channels": 2, "format": "pcm_s16le", "probed": True},
        )
        cls._patch_volume = patch(
            "recorder.device.run_volume_check",
            return_value={"status": "ok", "max_volume_db": -12.0, "mean_volume_db": -24.0},
        )
        cls._patch_start_capture = patch("recorder.capture.CAPTURE_MANAGER.start_capture")

        cls._patch_list.start()
        cls.addClassCleanup(cls._patch_list.stop)

        cls._patch_probe.start()
        cls.addClassCleanup(cls._patch_probe.stop)

        cls._patch_volume.start()
        cls.addClassCleanup(cls._patch_volume.stop)

        cls.mock_capture_start = cls._patch_start_capture.start()
        cls.addClassCleanup(cls._patch_start_capture.stop)

        # Start server on dynamic port on 127.0.0.1
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), HardenedHTTPHandler)
        def _stop_server():
            try:
                cls.server.shutdown()
                cls.server.server_close()
                cls.thread.join(timeout=2.0)
            except Exception:
                pass
        cls.addClassCleanup(_stop_server)

        cls.port = cls.server.server_port
        cls.server.server_port = cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        pass

    def setUp(self) -> None:
        self.mock_capture_start.reset_mock()

    def test_invalid_host_header_blocked(self) -> None:
        req = urllib.request.Request(f"{self.base_url}/api/status", headers={"Host": "attacker.com"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 400)
        self.mock_capture_start.assert_not_called()

    def test_cross_origin_post_blocked(self) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/api/record/start",
            data=b"{}",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": "http://evil-site.com",
                "Content-Type": "application/json",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)
        self.mock_capture_start.assert_not_called()

    def test_csrf_token_required_for_mutations(self) -> None:
        # POST without CSRF token must fail 403
        req = urllib.request.Request(
            f"{self.base_url}/api/record/start",
            data=b'{"source_kind":"zoom"}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)
        self.mock_capture_start.assert_not_called()

    def test_x_requested_with_without_token_is_rejected(self) -> None:
        # Verify that X-Requested-With alone does NOT bypass CSRF!
        req = urllib.request.Request(
            f"{self.base_url}/api/record/start",
            data=b'{"source_kind":"zoom"}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)
        self.mock_capture_start.assert_not_called()

    def test_valid_csrf_token_accepted(self) -> None:
        # Fetch status to get current CSRF token
        req_status = urllib.request.Request(
            f"{self.base_url}/api/status",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        with urllib.request.urlopen(req_status, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data["csrf_token"]

        self.assertTrue(token)
        self.assertEqual(token, STATE.csrf_token)

    def test_disallowed_file_download_blocked(self) -> None:
        # Attempt to download non-whitelisted file
        req = urllib.request.Request(
            f"{self.base_url}/files/some_session/id_rsa",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)

    def test_read_only_preflight_does_not_execute_volume_test(self) -> None:
        # GET /api/preflight is strictly read-only and never runs audio capture
        req = urllib.request.Request(
            f"{self.base_url}/api/preflight?kind=mic&test_volume=1",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            # Volume check must NOT be present in read-only preflight
            self.assertIsNone(data["preflight"].get("volume_check"))

    def test_preflight_test_requires_csrf_token(self) -> None:
        # POST /api/preflight/test without CSRF token must fail with 403
        req = urllib.request.Request(
            f"{self.base_url}/api/preflight/test",
            data=b'{"kind":"mic"}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)

    def test_summary_endpoint_requires_strict_opt_in_boolean(self) -> None:
        token = STATE.csrf_token
        # Try with string "false"
        req = urllib.request.Request(
            f"{self.base_url}/api/summary",
            data=b'{"session_id":"test","opt_in":"false"}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
                "X-CSRF-Token": token,
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertEqual(ctx.exception.code, 403)

    def test_download_hardlink_or_symlink_rejected(self) -> None:
        sess_dir = get_session_dir("test_download_links")
        transcript_file = sess_dir / "transcript.txt"
        target_secret = self.data_root / "secret.txt"
        target_secret.write_text("very secret")

        # 1. Symlink rejection
        if transcript_file.exists() or transcript_file.is_symlink():
            transcript_file.unlink()
        os.symlink(target_secret, transcript_file)

        req = urllib.request.Request(
            f"{self.base_url}/files/test_download_links/transcript.txt",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertIn(ctx.exception.code, [403, 404])

        # 2. Hardlink rejection
        transcript_file.unlink()
        os.link(target_secret, transcript_file)
        self.assertEqual(transcript_file.stat().st_nlink, 2)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5.0)
        self.assertIn(ctx.exception.code, [403, 404])


if __name__ == "__main__":
    unittest.main()
