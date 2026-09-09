"""Regression tests for Round 3 security requirements (R1, R3, R4, R5)."""
from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import (
    OUT_DIR,
    STATE_COMPLETED,
    STATE_INTERRUPTED,
    STATE_READY,
)
from recorder.http_server import HardenedHTTPHandler, UnsupportedMediaTypeError
from recorder.lock import GLOBAL_LOCK, LockBusyError
from recorder.session import create_session, load_session, save_session
from recorder.summary import generate_summary, run_claude_summary
from tests.isolated_test import IsolatedTestCase


class DummyHandler:
    """Mock handler for unit testing HTTP methods without running a network socket."""
    def __init__(self, headers: dict[str, str], body_bytes: bytes) -> None:
        self.headers = headers
        self.rfile = io.BytesIO(body_bytes)
        self.connection = MagicMock()


class TestSecurityRound03(IsolatedTestCase):
    def test_r5_content_type_exact_media_type_matching(self) -> None:
        """Verify that application/jsonp is rejected while application/json with params is accepted."""
        # 1. Invalid: application/jsonp
        handler_jsonp = DummyHandler(
            headers={"Content-Type": "application/jsonp", "Content-Length": "14"},
            body_bytes=b'{"key": "val"}',
        )
        with self.assertRaises(UnsupportedMediaTypeError):
            HardenedHTTPHandler.read_json_body(handler_jsonp)  # type: ignore[arg-type]

        # 2. Valid: exact application/json
        handler_exact = DummyHandler(
            headers={"Content-Type": "application/json", "Content-Length": "14"},
            body_bytes=b'{"key": "val"}',
        )
        data = HardenedHTTPHandler.read_json_body(handler_exact)  # type: ignore[arg-type]
        self.assertEqual(data, {"key": "val"})

        # 3. Valid: application/json with parameters
        handler_params = DummyHandler(
            headers={"Content-Type": "application/json; charset=utf-8", "Content-Length": "14"},
            body_bytes=b'{"key": "val"}',
        )
        data = HardenedHTTPHandler.read_json_body(handler_params)  # type: ignore[arg-type]
        self.assertEqual(data, {"key": "val"})

    def test_r1_summary_cancellation_durable_status_and_preservation(self) -> None:
        """Verify that cancelling summary sets summary_status to interrupted and preserves previous summary."""
        sess = create_session("sess_summary_cancel")
        sess_dir = self.data_root / "sess_summary_cancel"
        (sess_dir / "transcript.txt").write_text("Meeting transcript text for summary.", encoding="utf-8")
        (sess_dir / "summary.md").write_text("# Previous Valid Summary", encoding="utf-8")
        sess.summary_status = "completed"
        sess.has_summary = True
        save_session(sess)

        cancel_ev = threading.Event()

        # Simulate a child that gets cancelled while running
        def fake_run_claude(*a, **kw):
            cancel_ev.set()
            # Loop until cancel_event is noticed
            if kw.get("cancel_event") and kw["cancel_event"].is_set():
                raise InterruptedError("Summary cancelled by operator")
            raise RuntimeError("Should have been cancelled")

        with patch("recorder.summary.run_claude_summary", side_effect=fake_run_claude), \
             patch("shutil.which", return_value="/usr/local/bin/claude"):
            with self.assertRaises(InterruptedError):
                generate_summary(
                    "sess_summary_cancel",
                    provider="claude",
                    user_opt_in=True,
                    cancel_event=cancel_ev,
                )

        # Check durable manifest status
        updated = load_session("sess_summary_cancel")
        self.assertEqual(updated.summary_status, "interrupted")
        # Previous summary.md MUST NOT be deleted!
        self.assertEqual((sess_dir / "summary.md").read_text(encoding="utf-8"), "# Previous Valid Summary")

    def test_r3_upload_cleanup_failure_releases_lock(self) -> None:
        """Verify that if temp file unlink raises an exception during upload error cleanup, lock is still released."""
        handler = MagicMock()
        handler.headers = {"Content-Length": "100"}
        handler.connection = MagicMock()
        handler.rfile = io.BytesIO(b"incomplete")
        handler.client_address = ("127.0.0.1", 54321)

        # Simulate unlink failure
        with patch.object(Path, "unlink", side_effect=PermissionError("Mock unlink failed")):
            with self.assertRaises(Exception):
                HardenedHTTPHandler.handle_upload(handler, "")

        # Verify GLOBAL_LOCK was released despite unlink exception!
        GLOBAL_LOCK.acquire("test", "sess_after_cleanup_fail")
        GLOBAL_LOCK.release()

    def test_r4_trickle_read_exceeding_deadline_raises_timeouterror(self) -> None:
        """Verify that a slow trickle stream exceeding the deadline raises TimeoutError without hanging."""
        # Custom stream that simulates trickling: advances monotonic clock on each read
        clock_state = [1000.0]

        def fake_monotonic():
            return clock_state[0]

        class TrickleStream(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, b):
                # Advance clock by 40s per byte
                clock_state[0] += 40.0
                if len(b) > 0:
                    b[0] = ord(b"x")
                    return 1
                return 0

        handler = MagicMock()
        handler.headers = {"Content-Length": "1000"}
        handler.connection = MagicMock()
        handler.rfile = io.BufferedReader(TrickleStream())

        with patch("time.monotonic", side_effect=fake_monotonic):
            with self.assertRaises(TimeoutError) as ctx:
                HardenedHTTPHandler.handle_upload(handler, "")
            self.assertIn("exceeded maximum allowed duration", str(ctx.exception).lower())

        # Lock must be released
        GLOBAL_LOCK.acquire("test", "sess_trickle_after")
        GLOBAL_LOCK.release()


if __name__ == "__main__":
    unittest.main()
