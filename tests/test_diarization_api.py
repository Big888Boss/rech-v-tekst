"""Comprehensive HTTP API tests for diarization endpoints, queue removal, and speaker renaming."""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from recorder.constants import (
    DIARIZATION_CANCELLED,
    DIARIZATION_COMPLETED,
    DIARIZATION_IDLE,
    DIARIZATION_RUNNING,
    STATE_READY,
)
from recorder.http_server import (
    GLOBAL_LOCK,
    STATE,
    HardenedHTTPHandler,
)
from recorder.session import SessionManifest, load_session, save_session
from recorder.storage import get_session_dir
from tests.isolated_test import IsolatedTestCase


class TestDiarizationAPI(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_api_diar_sess"
        self.sess_dir = get_session_dir(self.session_id)
        (self.sess_dir / "normalized").mkdir(parents=True, exist_ok=True)

        self.manifest = SessionManifest(
            session_id=self.session_id,
            title="API Test Session",
            source_kind="mic",
            normalized_chunks=[{
                "index": 0,
                "filename": "chunk_000.wav",
                "duration_sec": 5.0,
            }],
            total_duration_sec=5.0,
            status=STATE_READY,
            in_queue=True,
        )
        save_session(self.manifest)
        (self.sess_dir / "transcript.txt").write_text("Тестовый текст", encoding="utf-8")
        (self.sess_dir / "transcript.json").write_text(json.dumps({
            "schema_version": 2,
            "session_id": self.session_id,
            "segments": [{"from_sec": 0.0, "to_sec": 5.0, "text": "Тестовый текст"}]
        }), encoding="utf-8")

        STATE.csrf_token = "api_test_csrf_token_12345"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), HardenedHTTPHandler)
        self.port = self.server.server_port
        self.server.server_port = self.port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            GLOBAL_LOCK.release(force_unlock=True)
        except Exception:
            pass
        super().tearDown()

    def _get(self, path: str) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = Request(url, headers={"User-Agent": "TestClient"})
        try:
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except HTTPError as e:
            data = json.loads(e.read().decode("utf-8")) if e.fp else {}
            return e.code, data

    def _post(self, path: str, body: dict, csrf: str | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        b = dict(body)
        token = csrf if csrf is not None else STATE.csrf_token
        b["csrf_token"] = token
        payload = json.dumps(b).encode("utf-8")
        req = Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "TestClient",
        })
        try:
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except HTTPError as e:
            data = json.loads(e.read().decode("utf-8")) if e.fp else {}
            return e.code, data

    def test_diarization_status_get(self):
        code, data = self._get("/api/diarization/status")
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("ready", data)
        self.assertIn("arch", data)

    def test_diarize_num_speakers_validation(self):
        # 1. Invalid speaker count < 2 (must be rejected)
        code, err1 = self._post("/api/session/diarize", {
            "session_id": self.session_id,
            "num_speakers": 1,
        })
        self.assertIn(code, (400, 500))

        # 2. Invalid speaker count > 20 (must be rejected)
        code, err2 = self._post("/api/session/diarize", {
            "session_id": self.session_id,
            "num_speakers": 21,
        })
        self.assertIn(code, (400, 500))

        # 3. Non-numeric invalid value (must be rejected)
        code, err3 = self._post("/api/session/diarize", {
            "session_id": self.session_id,
            "num_speakers": "many",
        })
        self.assertIn(code, (400, 500))

        # 4. Missing CSRF token (must be rejected with 403)
        code, err4 = self._post("/api/session/diarize", {
            "session_id": self.session_id,
            "num_speakers": 4,
        }, csrf="invalid_csrf_token")
        self.assertEqual(code, 403)

    def test_speaker_rename_api_endpoint(self):
        code, res = self._post("/api/session/speakers", {
            "session_id": self.session_id,
            "speakers": {
                "speaker_01": "Екатерина",
                "speaker_02": {"display_name": "Константин"},
            },
        })
        self.assertEqual(code, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res["speakers"]["speaker_01"]["display_name"], "Екатерина")
        self.assertEqual(res["speakers"]["speaker_02"]["display_name"], "Константин")

        # Verify inspection endpoint returns updated speakers
        code_insp, insp = self._get(f"/api/session/{self.session_id}")
        self.assertEqual(code_insp, 200)
        self.assertEqual(insp["manifest"]["speakers"]["speaker_01"]["display_name"], "Екатерина")

    def test_queue_remove_and_restore_api(self):
        # 1. Remove from queue
        code, rem = self._post("/api/session/queue/remove", {"session_id": self.session_id})
        self.assertEqual(code, 200)
        self.assertFalse(rem["in_queue"])
        loaded = load_session(self.session_id)
        self.assertFalse(loaded.in_queue)
        # Verify session audio files remain safely preserved on disk
        self.assertTrue((self.sess_dir / "transcript.txt").exists())

        # 2. Restore to queue
        code, rest = self._post("/api/session/queue/restore", {"session_id": self.session_id})
        self.assertEqual(code, 200)
        self.assertTrue(rest["in_queue"])
        loaded = load_session(self.session_id)
        self.assertTrue(loaded.in_queue)


if __name__ == "__main__":
    unittest.main()
