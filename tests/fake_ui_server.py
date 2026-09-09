#!/usr/bin/env python3
"""Isolated Fake UI Server for E2E Browser Testing.
Provides deterministic, synthetic capture, transcription, preflight, and summary backends.
Enforces a strict fail-closed safety barrier blocking real audio hardware (avfoundation)
and external AI providers (claude, etc.).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import uuid
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure project root in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# --- Fail-Closed Deny-All Subprocess Safety Barrier ---
import subprocess

def _deny_all_subprocess(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError(f"FAIL-CLOSED BARRIER: Subprocess execution is strictly forbidden in fake UI server. Invoked: {args}")

class DenyAllPopen:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _deny_all_subprocess(*args, **kwargs)

subprocess.Popen = DenyAllPopen  # type: ignore[misc]
subprocess.run = _deny_all_subprocess  # type: ignore[misc]

# --- Data Root Setup ---
from recorder.constants import set_data_root, get_data_root
out_env = os.environ.get("WEBINAR_OUT_DIR")
if out_env:
    out_dir = Path(out_env).resolve()
else:
    out_dir = BASE_DIR / "work" / "qa" / "fake_server_out"

out_dir.mkdir(parents=True, exist_ok=True)
os.chmod(out_dir, 0o700)
set_data_root(out_dir)

from recorder.session import SessionManifest, save_session, load_session, list_sessions, get_latest_session
from recorder.constants import (
    STATE_RECORDING,
    STATE_READY,
    STATE_INTERRUPTED,
    STATE_COMPLETED,
    STATE_FAILED,
)

def create_synthetic_wav(path: Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        n_frames = int(duration_sec * sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)

# --- Synthetic Managers ---

class FakeCaptureManager:
    def __init__(self) -> None:
        self.active_session_id: str | None = None
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        return self.active_session_id is not None

    def start_capture(self, session_id: str, source_kind: str = "zoom", device_index: int | None = None) -> SessionManifest:
        with self._lock:
            self.active_session_id = session_id
            s_dir = out_dir / session_id
            s_dir.mkdir(parents=True, exist_ok=True)
            manifest = SessionManifest(
                session_id=session_id,
                source_kind=source_kind,
                device_info={"index": device_index} if device_index is not None else {},
                status=STATE_RECORDING,
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            save_session(manifest)
            marker = out_dir / ".active_capture.json"
            marker.write_text(json.dumps({"session_id": session_id, "start_time": time.time()}), encoding="utf-8")
            return manifest

    def stop_capture(self, session_id: str | None = None) -> SessionManifest:
        with self._lock:
            sid = session_id or self.active_session_id or "unknown_session"
            self.active_session_id = None
            marker = out_dir / ".active_capture.json"
            if marker.exists():
                marker.unlink(missing_ok=True)

            s_dir = out_dir / sid
            s_dir.mkdir(parents=True, exist_ok=True)
            chunk_file = s_dir / "chunk_000.wav"
            create_synthetic_wav(chunk_file, duration_sec=2.0)

            manifest = load_session(sid) or SessionManifest(session_id=sid)
            manifest.status = STATE_READY
            manifest.total_duration_sec = 2.0
            manifest.raw_chunks = [{"filename": "chunk_000.wav", "duration_sec": 2.0}]
            manifest.normalized_chunks = [{"filename": "chunk_000.wav", "duration_sec": 2.0}]
            manifest.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
            save_session(manifest)
            return manifest

    def reconcile_on_startup(self) -> list[str]:
        return []

class FakeTranscribeManager:
    def __init__(self) -> None:
        self.progress: dict[str, Any] = {"is_running": False}
        self.active_session_id: str | None = None
        self._cancelled = False
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

    def transcribe_session(self, session_id: str, lang: str = "ru", *args: Any, **kwargs: Any) -> SessionManifest:
        with self._lock:
            self.active_session_id = session_id
            self._cancelled = False
            self._cancel_event.clear()
            self.progress = {
                "is_running": True,
                "session_id": session_id,
                "completed_chunks": 1,
                "total_chunks": 2,
                "elapsed_sec": 1,
                "eta_sec": 1,
            }

        # Allow brief window for UI to observe active processing / cancel, or wake immediately on cancel
        self._cancel_event.wait(timeout=0.6)

        with self._lock:
            if self._cancelled:
                self.progress = {"is_running": False}
                self.active_session_id = None
                manifest = load_session(session_id) or SessionManifest(session_id=session_id)
                manifest.status = STATE_INTERRUPTED
                manifest.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
                save_session(manifest)
                raise InterruptedError("Transcription cancelled by operator")

            s_dir = out_dir / session_id
            s_dir.mkdir(parents=True, exist_ok=True)

            txt_file = s_dir / "transcript.txt"
            txt_file.write_text(
                "00:00:00 Доброе утро, коллеги. Начинаем общее собрание инженеров.\n"
                "00:01:30 Обсуждаем архитектурные изменения и надежность записи звука.",
                encoding="utf-8",
            )

            json_file = s_dir / "transcript.json"
            json_file.write_text(
                json.dumps({
                    "segments": [
                        {"from_sec": 0.0, "to_sec": 4.5, "text": "Доброе утро, коллеги. Начинаем общее собрание инженеров."},
                        {"from_sec": 90.0, "to_sec": 95.0, "text": "Обсуждаем архитектурные изменения и надежность записи звука."},
                    ]
                }, indent=2),
                encoding="utf-8",
            )

            md_file = s_dir / "transcript.md"
            md_file.write_text(
                f"# Transcript: {session_id}\n\n"
                "**00:00:00** Доброе утро, коллеги. Начинаем общее собрание инженеров.\n\n"
                "**00:01:30** Обсуждаем архитектурные изменения и надежность записи звука.",
                encoding="utf-8",
            )

            manifest = load_session(session_id) or SessionManifest(session_id=session_id)
            manifest.status = STATE_COMPLETED
            manifest.has_transcript = True
            manifest.word_count = 1420
            manifest.language = lang
            manifest.total_duration_sec = 2.0
            manifest.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
            save_session(manifest)

            self.progress = {"is_running": False}
            self.active_session_id = None
            return manifest

    def cancel_transcription(self) -> None:
        self._cancel_event.set()
        with self._lock:
            self._cancelled = True
            sid = self.active_session_id or (self.progress.get("session_id"))
            self.progress = {"is_running": False}
            self.active_session_id = None
            if sid:
                m = load_session(sid)
                if m:
                    m.status = STATE_INTERRUPTED
                    m.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    save_session(m)

def fake_preflight_audio(kind: str = "blackhole", index: int | None = None, test_volume: bool = False) -> dict[str, Any]:
    if kind == "denied_mic":
        return {
            "devices": [{"index": 0, "name": "Built-in Microphone", "kind": "mic"}],
            "selected_device": {"index": 0, "name": "Built-in Microphone", "kind": "mic"},
            "permissions": "denied",
            "volume_check": None,
            "native_stream": None,
        }

    devs = [
        {"index": 0, "name": "BlackHole 2ch", "kind": "blackhole"},
        {"index": 1, "name": "Built-in Microphone", "kind": "mic"},
    ]
    sel_idx = index if index is not None else (0 if kind == "blackhole" else 1)
    sel = next((d for d in devs if d["index"] == sel_idx), devs[0])

    res: dict[str, Any] = {
        "devices": devs,
        "selected_device": sel,
        "permissions": "granted",
        "native_stream": {"sample_rate": 48000, "channels": 2},
    }
    if test_volume:
        res["volume_check"] = {
            "status": "ok",
            "max_volume_db": -10.0,
            "mean_volume_db": -22.0,
            "message": "Audio signal detected successfully (peak: -10.0 dB). Ready to record.",
        }
    else:
        res["volume_check"] = None
    return res

def fake_generate_summary(
    session_id: str,
    provider: str = "claude",
    template_name: str = "meeting",
    user_opt_in: bool = False,
    *args: Any,
    **kwargs: Any,
) -> str:
    summary_text = (
        "# Итоги собрания\n\n"
        "- Обсудили нативный захват звука без потерь.\n"
        "- Защитили файловую систему от symlink и race conditions."
    )
    s_dir = out_dir / session_id
    s_dir.mkdir(parents=True, exist_ok=True)
    (s_dir / "summary.md").write_text(summary_text, encoding="utf-8")

    m = load_session(session_id)
    if m:
        m.has_summary = True
        m.summary_status = "completed"
        save_session(m)
    return summary_text

def fake_retry_session(session_id: str) -> SessionManifest:
    """Production retry contract: resets status to STATE_READY, clears error message, does NOT auto-transcribe."""
    m = load_session(session_id)
    if not m:
        m = SessionManifest(session_id=session_id)
    m.status = STATE_READY
    m.error_message = None
    m.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    save_session(m)
    return m

def fake_generate_all_exports(session_id: str) -> None:
    s_dir = out_dir / session_id
    s_dir.mkdir(parents=True, exist_ok=True)
    txt_path = s_dir / "transcript.txt"
    if not txt_path.exists():
        txt_path.write_text("00:00:00 Доброе утро, коллеги.\n", encoding="utf-8")
    md_path = s_dir / "transcript.md"
    if not md_path.exists():
        md_path.write_text(f"# Transcript: {session_id}\n\n**00:00:00** Доброе утро, коллеги.\n", encoding="utf-8")
    json_path = s_dir / "transcript.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({"segments": [{"from_sec": 0.0, "to_sec": 4.0, "text": "Доброе утро, коллеги."}]}, indent=2), encoding="utf-8")

# --- Patch recorder.http_server with Synthetic Backends ---
import recorder.http_server as srv_mod
fake_cap = FakeCaptureManager()
fake_trans = FakeTranscribeManager()

srv_mod.CAPTURE_MANAGER = fake_cap  # type: ignore[misc]
srv_mod.TRANSCRIBE_MANAGER = fake_trans  # type: ignore[misc]
srv_mod.preflight_audio = fake_preflight_audio  # type: ignore[misc]
srv_mod.generate_summary = fake_generate_summary  # type: ignore[misc]
srv_mod.retry_session = fake_retry_session  # type: ignore[misc]
srv_mod.generate_all_exports = fake_generate_all_exports  # type: ignore[misc]

from recorder.media_tools import MediaToolsStatus

def fake_resolve_model_path(*args: Any, **kwargs: Any) -> Path:
    p = get_data_root() / "fake_model.bin"
    if not p.exists():
        p.write_bytes(b"FAKE_WHISPER_MODEL")
    return p

def fake_get_media_tools_status() -> MediaToolsStatus:
    m_path = fake_resolve_model_path()
    return MediaToolsStatus(
        ffmpeg=True,
        ffprobe=True,
        whisper=True,
        model=True,
        ready=True,
        missing=[],
        ffmpeg_path="/fake/ffmpeg",
        ffprobe_path="/fake/ffprobe",
        whisper_path=sys.executable,
        model_path=str(m_path),
        remediation=None,
    )

srv_mod.get_media_tools_status = fake_get_media_tools_status  # type: ignore[misc]
srv_mod.resolve_whisper_bin = lambda *a, **kw: sys.executable  # type: ignore[misc]
srv_mod.resolve_model_path = fake_resolve_model_path  # type: ignore[misc]

from recorder.http_server import HardenedHTTPHandler

FAKE_SERVER_IDENTITY = os.environ.get("UI_IDENTITY") or f"fake_server_{uuid.uuid4().hex[:12]}"

class FakeServerHandler(HardenedHTTPHandler):
    def do_GET(self) -> None:
        from urllib.parse import urlparse
        pr = urlparse(self.path)
        if pr.path == "/api/identity":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "identity": FAKE_SERVER_IDENTITY}).encode("utf-8"))
            return
        super().do_GET()

def seed_fixtures(target_dir: Path) -> None:
    """Create initial fixtures: one failed session to verify error state."""
    s_fail = target_dir / "session_20260908_failed"
    s_fail.mkdir(parents=True, exist_ok=True)
    m_fail = SessionManifest(
        session_id="session_20260908_failed",
        title="Тестовый сбой оборудования",
        status=STATE_FAILED,
        source_kind="mic",
        total_duration_sec=12.0,
        error_message="Audio hardware capture device disconnected unexpectedly during recording",
        created_at="2026-09-08 08:00:00",
        updated_at="2026-09-08 08:02:00",
    )
    save_session(m_fail)

def run_server(port: int = 0) -> None:
    seed_fixtures(out_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeServerHandler)
    actual_port = server.server_address[1]
    server.server_port = actual_port

    # Save identity metadata file
    info_file = out_dir / ".fake_server_info.json"
    info_file.write_text(json.dumps({
        "port": actual_port,
        "identity": FAKE_SERVER_IDENTITY,
        "pid": os.getpid(),
    }), encoding="utf-8")

    print(f"=== Isolated Fake UI Server Online ===")
    print(f"ServerInfo: port={actual_port} identity={FAKE_SERVER_IDENTITY}")
    print(f"URL: http://127.0.0.1:{actual_port}")
    print(f"OutDir: {out_dir}")
    sys.stdout.flush()

    def handle_signal(sig: int, frame: Any) -> None:
        print("\nShutting down fake UI server...")
        try:
            srv_mod.STATE.stop_all_workers(timeout=2.0)
            srv_mod.GLOBAL_LOCK.release()
        except Exception:
            pass
        finally:
            try:
                server.server_close()
            except Exception:
                pass
            sys.exit(0)

    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except (ValueError, AttributeError):
        pass

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        handle_signal(signal.SIGINT, None)

if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("UI_PORT", "8799"))
    run_server(p)
