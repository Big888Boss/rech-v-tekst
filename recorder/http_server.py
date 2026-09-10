"""Hardened HTTP server exposing REST APIs, static files, and audio/transcript streaming."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .capture import CAPTURE_MANAGER
from .constants import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LOG_LIMIT,
    MAX_JSON_BODY_BYTES,
    MAX_UPLOAD_BODY_BYTES,
    MIN_DISK_FREE_BYTES,
    OUT_DIR,
    STATE_PROCESSING,
    STATE_RECORDING,
    STATIC_DIR,
    UPLOADS_DIR,
)
from .device import preflight_audio
from .export import generate_all_exports
from .lock import GLOBAL_LOCK, LockBusyError
from .media_tools import (
    get_media_tools_status,
    resolve_ffmpeg_bin,
    resolve_ffprobe_bin,
    resolve_model_path,
    resolve_whisper_bin,
)
from .normalize import import_media_file as import_user_media_file
from .session import (
    get_latest_session,
    list_sessions,
    load_session,
    remove_from_queue,
    restore_to_queue,
    retry_session,
    save_session,
)
from .storage import (
    StorageError,
    atomic_write_bytes,
    check_disk_space,
    get_session_dir,
    is_safe_regular_file,
    safe_make_dir,
    safe_read_file,
    safe_read_text,
    safe_upload_filename,
    validate_session_id,
)
from .summary import generate_summary
from .transcribe import TRANSCRIBE_MANAGER


class UnsupportedMediaTypeError(Exception):
    """Raised when Content-Type is not supported."""



class ServerState:
    """Central server operational state, log ring buffer, and CSRF secret."""

    def __init__(self) -> None:
        self.csrf_token: str = secrets.token_hex(24)
        self.logs: deque[str] = deque(maxlen=LOG_LIMIT)
        self.last_preflight: dict | None = None
        self.started_at: float = time.time()
        self.active_workers: dict[str, dict[str, Any]] = {}
        self.active_diarizers: dict[str, Any] = {}
        self.active_diarizer_progress: dict[str, Any] = {}
        self._workers_lock = threading.RLock()

    def log(self, line: str) -> None:
        clean = line.strip()
        if not clean:
            return
        stamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {clean}")

    def register_worker(self, worker_id: str, record: dict[str, Any]) -> None:
        with self._workers_lock:
            self.active_workers[worker_id] = record

    def unregister_worker(self, worker_id: str) -> None:
        with self._workers_lock:
            self.active_workers.pop(worker_id, None)

    def stop_all_workers(self, timeout: float = 5.0) -> None:
        """Cancel active operations and join worker threads before shutdown."""
        try:
            if CAPTURE_MANAGER.is_active():
                CAPTURE_MANAGER.stop_capture()
        except Exception:
            pass
        try:
            if TRANSCRIBE_MANAGER.progress["is_running"]:
                TRANSCRIBE_MANAGER.cancel_transcription()
        except Exception:
            pass
        try:
            from .installer import INSTALLER
            if INSTALLER.is_running():
                INSTALLER.cancel_install()
        except Exception:
            pass

        with self._workers_lock:
            diarizers = list(self.active_diarizers.values())
        for d in diarizers:
            try:
                d.cancel()
            except Exception:
                pass

        with self._workers_lock:
            workers = list(self.active_workers.values())

        for w in workers:
            try:
                if w.get("cancel_event"):
                    w["cancel_event"].set()
                proc = w.get("proc")
                if proc and proc.poll() is None:
                    from .capture import stop_and_reap_process_group
                    stop_and_reap_process_group(proc, timeout_sec=1.5)
                th = w.get("thread")
                if th and th.is_alive() and th != threading.current_thread():
                    th.join(timeout=timeout)
            except Exception:
                pass

        with self._workers_lock:
            self.active_workers.clear()
            self.active_diarizers.clear()


STATE = ServerState()
CAPTURE_MANAGER.set_log_callback(STATE.log)
TRANSCRIBE_MANAGER.set_log_callback(STATE.log)


class HardenedHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing security controls and API routes."""

    server_version = "WebinarRecorder/2.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def get_server_port(self) -> int:
        if hasattr(self.server, "server_port"):
            return self.server.server_port
        return DEFAULT_PORT

    def validate_host(self) -> bool:
        """Validate that Host header matches actual host and port (DNS rebinding protection)."""
        host = self.headers.get("Host", "").strip().lower()
        if not host:
            return False
        port = self.get_server_port()
        valid_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            "127.0.0.1",
            "localhost",
        }
        return host in valid_hosts

    def validate_origin(self) -> bool:
        """Verify Origin header for mutative POST requests (CSRF protection)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.get_server_port()
        valid_origins = {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }
        return origin.lower() in valid_origins

    def validate_csrf(self, parsed_body: dict | None = None) -> bool:
        """Validate CSRF token from header or JSON body."""
        token = self.headers.get("X-CSRF-Token")
        if token and secrets.compare_digest(token, STATE.csrf_token):
            return True
        if parsed_body and isinstance(parsed_body, dict):
            body_token = parsed_body.get("csrf_token")
            if body_token and secrets.compare_digest(str(body_token), STATE.csrf_token):
                return True
        return False

    def do_GET(self) -> None:
        if not self.validate_host():
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return

        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/" or path == "/index.html":
                self.serve_static_file("index.html", "text/html")
            elif path.startswith("/static/"):
                rel_path = path[len("/static/"):]
                self.serve_static_file(rel_path)
            elif path == "/api/status":
                self.handle_api_status()
            elif path == "/api/sessions":
                self.handle_api_sessions()
            elif path.startswith("/api/session/"):
                session_id = path[len("/api/session/"):]
                self.handle_api_session(session_id)
            elif path == "/api/preflight":
                self.handle_api_preflight(parsed.query)
            elif path == "/api/settings":
                self.handle_api_settings_get(parsed.query)
            elif path == "/api/install/status":
                self.handle_api_install_status()
            elif path == "/api/diarization/status":
                self.handle_api_diarization_status()
            elif path.startswith("/files/"):
                self.handle_file_download(path[len("/files/"):])
            elif path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except StorageError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except PermissionError as exc:
            self.send_error_json(HTTPStatus.FORBIDDEN, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"Internal error: {exc}")

    def do_POST(self) -> None:
        if not self.validate_host():
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return

        if not self.validate_origin():
            self.send_error_json(HTTPStatus.FORBIDDEN, "Cross-origin requests are forbidden")
            return

        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/upload":
                if not self.validate_csrf():
                    self.send_error_json(HTTPStatus.FORBIDDEN, "CSRF token missing or invalid")
                    return
                self.handle_upload(parsed.query)
                return

            body = self.read_json_body()
            if not self.validate_csrf(body):
                self.send_error_json(HTTPStatus.FORBIDDEN, "CSRF token missing or invalid")
                return

            if path == "/api/preflight/test":
                self.handle_preflight_test(body)
            elif path == "/api/record/start":
                self.handle_record_start(body)
            elif path == "/api/record/stop":
                self.handle_record_stop(body)
            elif path == "/api/process":
                self.handle_process(body)
            elif path == "/api/process/cancel":
                self.handle_process_cancel(body)
            elif path == "/api/session/retry":
                self.handle_session_retry(body)
            elif path == "/api/session/queue/remove":
                self.handle_queue_remove(body)
            elif path == "/api/session/queue/restore":
                self.handle_queue_restore(body)
            elif path == "/api/summary":
                self.handle_summary(body)
            elif path == "/api/export":
                self.handle_export(body)
            elif path == "/api/settings":
                self.handle_api_settings_post(body)
            elif path == "/api/install/start":
                self.handle_api_install_start(body)
            elif path == "/api/installer/diarize":
                self.handle_api_installer_diarize(body)
            elif path == "/api/session/diarize":
                self.handle_session_diarize(body)
            elif path == "/api/session/diarize/cancel":
                self.handle_session_diarize_cancel(body)
            elif path == "/api/session/speakers":
                self.handle_session_speakers(body)
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")

        except UnsupportedMediaTypeError as exc:
            self.send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
        except StorageError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except PermissionError as exc:
            self.send_error_json(HTTPStatus.FORBIDDEN, str(exc))
        except TimeoutError as exc:
            self.send_error_json(HTTPStatus.REQUEST_TIMEOUT, str(exc))
        except (RuntimeError, LockBusyError) as exc:
            self.send_error_json(HTTPStatus.CONFLICT, str(exc))
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"Internal error: {exc}")

    def read_json_body(self) -> dict[str, Any]:
        """Read and strictly parse JSON object body with content type and length checks (R5 / SEC-08)."""
        ctype = self.headers.get("Content-Type", "")
        media_type = ctype.split(";")[0].strip().lower()
        if media_type != "application/json":
            raise UnsupportedMediaTypeError("Content-Type must be application/json")

        cl_header = self.headers.get("Content-Length")
        if cl_header is None:
            raise ValueError("Missing Content-Length header")
        try:
            content_length = int(cl_header)
        except ValueError:
            raise ValueError("Invalid Content-Length header")

        if content_length <= 0:
            raise ValueError("Content-Length must be greater than 0")
        if content_length > MAX_JSON_BODY_BYTES:
            raise ValueError(f"Request body too large ({content_length} bytes, max {MAX_JSON_BODY_BYTES})")

        # Set read timeout on socket
        try:
            if hasattr(self.connection, "settimeout"):
                self.connection.settimeout(10.0)
        except Exception:
            pass

        data = self.rfile.read(content_length)
        if len(data) != content_length:
            raise ValueError(f"Truncated body: expected {content_length} bytes, received {len(data)}")

        try:
            parsed = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON payload: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"JSON root must be an object (dict), received {type(parsed).__name__}")

        return parsed

    def send_html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def serve_static_file(self, rel_path: str, forced_mime: str | None = None) -> None:
        if not STATIC_DIR.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Static directory missing")
            return

        clean_rel = Path(unquote(rel_path)).name
        file_path = (STATIC_DIR / clean_rel).resolve()
        if not file_path.is_relative_to(STATIC_DIR.resolve()):
            self.send_error_json(HTTPStatus.FORBIDDEN, "Path traversal forbidden")
            return

        if not is_safe_regular_file(file_path):
            self.send_error_json(HTTPStatus.NOT_FOUND, "File not found")
            return

        content_type = forced_mime or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        try:
            data = safe_read_file(file_path, root=STATIC_DIR)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if "text/" in content_type else content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except StorageError as exc:
            self.send_error_json(HTTPStatus.FORBIDDEN, str(exc))

    def handle_api_status(self) -> None:
        disk = check_disk_space()
        current_lock = GLOBAL_LOCK.get_current_holder()
        active_session = CAPTURE_MANAGER.active_session_id
        is_recording = CAPTURE_MANAGER.is_active()
        all_sessions = [s.to_dict() for s in list_sessions()]
        all_logs = list(STATE.logs)

        # Unified active model matching frontend contract
        active = None
        if is_recording and active_session:
            start_time = None
            marker_file = OUT_DIR / ".active_capture.json"
            if marker_file.exists():
                try:
                    m = json.loads(safe_read_text(marker_file))
                    start_time = m.get("start_time")
                except Exception:
                    pass
            active = {
                "kind": "recording",
                "session_id": active_session,
                "start_time": start_time,
            }
        elif TRANSCRIBE_MANAGER.progress.get("is_running"):
            active = {
                "kind": "transcribing",
                "session_id": TRANSCRIBE_MANAGER.progress.get("session_id") or TRANSCRIBE_MANAGER.active_session_id,
                "progress": TRANSCRIBE_MANAGER.progress,
            }
        elif current_lock and current_lock.get("action") == "summary":
            active = {
                "kind": "summary",
                "session_id": current_lock.get("session_id"),
                "status": "running",
            }
        else:
            run_summ = next((s for s in all_sessions if s.get("summary_status") == "running"), None)
            if run_summ:
                active = {
                    "kind": "summary",
                    "session_id": run_summ.get("session_id"),
                    "status": "running",
                }

        if active is None:
            with STATE._workers_lock:
                active_diar_id = next(iter(STATE.active_diarizers.keys()), None)
            if active_diar_id is not None:
                active = {
                    "kind": "diarizing",
                    "session_id": active_diar_id,
                    "status": "running",
                    "diarization_progress": STATE.active_diarizer_progress.get(active_diar_id),
                }

        from .installer import INSTALLER
        installer_status = INSTALLER.get_status()
        if active is None and installer_status.get("status") == "running":
            active = {
                "kind": "install",
                "session_id": "installer_job",
                "status": "running",
            }

        tools_status = get_media_tools_status()

        payload = {
            "ok": True,
            "active": active,
            "sessions": all_sessions,
            "logs": all_logs,
            "is_recording": is_recording,
            "active_session": active_session,
            "lock": current_lock,
            "disk": disk,
            "transcription": TRANSCRIBE_MANAGER.progress,
            "installer": installer_status,
            "last_preflight": STATE.last_preflight,
            "tools": tools_status.to_dict() if hasattr(tools_status, "to_dict") else tools_status,
            "recent_logs": all_logs,
            "csrf_token": STATE.csrf_token,
            "uptime_sec": round(time.time() - STATE.started_at, 1),
        }
        self.send_json(payload)

    def handle_api_sessions(self) -> None:
        sessions = list_sessions()
        self.send_json({
            "ok": True,
            "sessions": [s.to_dict() for s in sessions],
            "total": len(sessions),
        })

    def handle_api_preflight(self, query_str: str) -> None:
        params = parse_qs(query_str)
        kind = params.get("kind", ["blackhole"])[0]
        idx_param = params.get("device_index", [None])[0] or params.get("index", [None])[0]
        idx = int(idx_param) if idx_param is not None and str(idx_param).isdigit() else None

        # GET is strictly read-only enumeration; test_volume is always False
        result = preflight_audio(kind=kind, index=idx, test_volume=False)
        STATE.last_preflight = result
        self.send_json({"ok": True, "preflight": result})

    def handle_preflight_test(self, body: dict) -> None:
        kind = body.get("kind", "blackhole")
        idx_val = body.get("device_index") if "device_index" in body else body.get("index")
        idx = int(idx_val) if idx_val is not None and str(idx_val).isdigit() else None
        result = preflight_audio(kind=kind, index=idx, test_volume=True)
        STATE.last_preflight = result
        self.send_json({"ok": True, "preflight": result})

    def handle_api_session(self, session_id: str) -> None:
        valid_id = validate_session_id(session_id)
        manifest = load_session(valid_id)
        if not manifest:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"Session {session_id} not found")
            return

        session_dir = get_session_dir(valid_id)
        transcript_text = ""
        summary_text = ""
        segments = []

        t_path = session_dir / "transcript.txt"
        if is_safe_regular_file(t_path):
            transcript_text = safe_read_text(t_path, root=OUT_DIR)

        s_path = session_dir / "summary.md"
        if is_safe_regular_file(s_path):
            summary_text = safe_read_text(s_path, root=OUT_DIR)

        json_path = session_dir / "transcript.json"
        diarization_info = None
        if is_safe_regular_file(json_path):
            try:
                j_data = json.loads(safe_read_text(json_path, root=OUT_DIR))
                if isinstance(j_data, dict):
                    segments = j_data.get("segments", [])
                    diarization_info = j_data.get("diarization")
            except Exception:
                pass

        self.send_json({
            "ok": True,
            "manifest": manifest.to_dict(),
            "transcript": transcript_text,
            "summary": summary_text,
            "segments": segments,
            "diarization": diarization_info,
            "diarization_progress": STATE.active_diarizer_progress.get(valid_id),
        })

    def handle_file_download(self, subpath: str) -> None:
        parts = Path(subpath).parts
        if len(parts) < 2:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid file path format")
            return

        session_id = parts[0]
        filename = parts[-1]
        valid_id = validate_session_id(session_id)
        session_dir = get_session_dir(valid_id)

        allowed_files = {
            "transcript.txt",
            "transcript.json",
            "transcript.srt",
            "transcript.vtt",
            "transcript.md",
            "summary.md",
            "full_export.md",
            "session.json",
        }

        is_chunk = filename.startswith("chunk_") and filename.endswith(".wav")
        if filename not in allowed_files and not is_chunk:
            self.send_error_json(HTTPStatus.FORBIDDEN, f"File {filename} is not allowed for download")
            return

        target = session_dir / filename
        if not target.exists() and filename in ("transcript.srt", "transcript.vtt", "transcript.md"):
            try:
                from .export import generate_all_exports
                generate_all_exports(valid_id)
            except Exception:
                pass

        if not target.exists() and is_chunk:
            target = session_dir / "normalized" / filename

        if not target.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, f"File {filename} not found")
            return

        if not is_safe_regular_file(target):
            self.send_error_json(HTTPStatus.FORBIDDEN, "Access to non-regular file or symlink is forbidden")
            return

        data = safe_read_file(target, root=OUT_DIR)
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def handle_upload(self, query_str: str) -> None:
        cl_header = self.headers.get("Content-Length")
        if not cl_header:
            raise ValueError("Missing Content-Length header for upload")
        try:
            content_length = int(cl_header)
        except ValueError:
            raise ValueError("Invalid Content-Length header")

        if content_length <= 0:
            raise ValueError("Empty upload body")
        if content_length > MAX_UPLOAD_BODY_BYTES:
            raise ValueError(f"Upload exceeds maximum allowed size ({MAX_UPLOAD_BODY_BYTES} bytes)")

        # Check required dependencies before accepting upload
        tools_status = get_media_tools_status()
        if not tools_status.ready:
            missing_str = ", ".join(tools_status.missing)
            err_msg = f"Отсутствуют необходимые зависимости для импорта: {missing_str}. Установите их перед загрузкой."
            if tools_status.remediation:
                err_msg += f" {tools_status.remediation}"
            STATE.log(f"Upload rejected: {err_msg}")
            raise RuntimeError(err_msg)

        # Verify disk space before taking lock
        disk = check_disk_space()
        required_bytes = content_length + MIN_DISK_FREE_BYTES
        if disk["free_bytes"] < required_bytes:
            avail_mb = disk["free_bytes"] // (1024 * 1024)
            need_mb = required_bytes // (1024 * 1024)
            raise StorageError(
                f"Insufficient disk space for upload: need {need_mb} MB (including reserve), "
                f"available {avail_mb} MB"
            )

        # Acquire global operation lock for upload
        try:
            GLOBAL_LOCK.acquire("upload", "staging_upload")
        except LockBusyError as exc:
            raise RuntimeError(str(exc)) from exc

        # R3 / SEC-08: Complete protection wrapping everything after acquire in try/finally
        temp_path: Path | None = None
        try:
            params = parse_qs(query_str)
            raw_name = params.get("name", ["audio.m4a"])[0]
            safe_name = safe_upload_filename(raw_name)

            safe_make_dir(UPLOADS_DIR)
            temp_path = UPLOADS_DIR / f".tmp_{uuid.uuid4().hex[:8]}_{safe_name}"

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            upload_start = time.monotonic()
            timeout_sec = 300.0  # 5 minutes maximum upload duration

            fd = os.open(temp_path, flags, 0o600)
            remaining = content_length
            try:
                with open(fd, "wb", closefd=True) as f:
                    while remaining > 0:
                        remaining_time = timeout_sec - (time.monotonic() - upload_start)
                        if remaining_time <= 0:
                            raise TimeoutError("Upload exceeded maximum allowed duration (300s)")

                        if hasattr(self.connection, "settimeout"):
                            self.connection.settimeout(max(0.1, min(30.0, remaining_time)))

                        read_size = min(65536, remaining)
                        if hasattr(self.rfile, "read1"):
                            chunk = self.rfile.read1(read_size)
                        else:
                            chunk = self.rfile.read(read_size)

                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)

                        if time.monotonic() - upload_start > timeout_sec:
                            raise TimeoutError("Upload exceeded maximum allowed duration (300s)")
                    f.flush()
                    os.fsync(f.fileno())

                if remaining > 0:
                    raise ValueError("Upload connection closed prematurely")

                # Deadline check before promotion
                if time.monotonic() - upload_start > timeout_sec:
                    raise TimeoutError("Upload exceeded maximum allowed duration (300s)")

                final_dest = UPLOADS_DIR / safe_name
                os.replace(temp_path, final_dest)
                temp_path = None
            except Exception as exc:
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                STATE.log(f"Upload storage error for {raw_name}: {exc}")
                raise

            session_id = f"upload_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:12]}"
            STATE.log(f"Received upload: {raw_name} -> session {session_id}")

            cancel_ev = threading.Event()
            worker_id = f"import_{session_id}_{uuid.uuid4().hex[:6]}"
            worker_rec: dict[str, Any] = {
                "type": "import",
                "session_id": session_id,
                "thread": threading.current_thread(),
                "cancel_event": cancel_ev,
                "proc": None,
            }
            STATE.register_worker(worker_id, worker_rec)

            def on_import_proc(p: subprocess.Popen[str]) -> None:
                worker_rec["proc"] = p

            try:
                manifest = import_user_media_file(
                    file_path=final_dest,
                    session_id=session_id,
                    title=raw_name.strip()[:100] or session_id,
                    log_fn=STATE.log,
                    cancel_event=cancel_ev,
                    on_proc_start=on_import_proc,
                )
                if "enable_diarize" in params or "enable_diarization" in params:
                    en_val = params.get("enable_diarize", params.get("enable_diarization", ["0"]))[0]
                    en = en_val in ("1", "true", "True")
                    nspk_val = params.get("num_speakers", [None])[0]
                    nspk = int(nspk_val) if nspk_val and nspk_val.isdigit() else None
                    manifest.diarization_params = {
                        "enabled": en,
                        "num_speakers": nspk,
                    }
                    manifest.save()
            except Exception as exc:
                STATE.log(f"Import failed for session {session_id} ({raw_name}): {exc}")
                raise
            finally:
                STATE.unregister_worker(worker_id)

            self.send_json({"ok": True, "session_id": session_id, "manifest": manifest.to_dict()})
        finally:
            try:
                if temp_path and temp_path.exists():
                    temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            finally:
                GLOBAL_LOCK.release()

    def handle_record_start(self, body: dict) -> None:
        source_kind = body.get("source_kind", "zoom")
        dev_idx = body.get("device_index") if "device_index" in body else body.get("index")
        title = body.get("title")

        stamp = time.strftime("%Y%m%d_%H%M%S")
        prefix = "zoom" if source_kind == "zoom" else "mic"
        session_id = f"{prefix}_{stamp}"

        manifest = CAPTURE_MANAGER.start_capture(
            session_id=session_id,
            source_kind=source_kind,
            device_index=dev_idx,
        )
        language = body.get("language") or body.get("lang")
        if language:
            manifest.language = language
        if title:
            manifest.title = title

        if "enable_diarize" in body or "enable_diarization" in body:
            en = bool(body.get("enable_diarize") or body.get("enable_diarization"))
            nspk = body.get("num_speakers")
            manifest.diarization_params = {
                "enabled": en,
                "num_speakers": int(nspk) if (nspk is not None and str(nspk).isdigit()) else None,
            }

        save_session(manifest)

        self.send_json({"ok": True, "session_id": session_id, "manifest": manifest.to_dict()})

    def handle_record_stop(self, body: dict) -> None:
        session_id = body.get("session_id")
        manifest = CAPTURE_MANAGER.stop_capture(session_id=session_id)
        self.send_json({"ok": True, "manifest": manifest.to_dict()})

    def handle_process(self, body: dict) -> None:
        raw_id = body.get("session_id")
        if not raw_id:
            latest = get_latest_session()
            if not latest:
                raise ValueError("Сессия для обработки не указана и не найдена")
            session_id = latest.session_id
        else:
            session_id = validate_session_id(str(raw_id).strip())

        # 1. Check session existence
        s = load_session(session_id)
        if not s:
            raise StorageError(f"Сессия не найдена: {session_id}")

        # 2. Check queue state (must not be removed from queue)
        if not getattr(s, "in_queue", True):
            raise RuntimeError("Запись убрана из очереди. Верните её в очередь перед запуском распознавания.")

        # 3. Check session status: only ready, failed, or interrupted can be processed
        if s.status in (STATE_RECORDING, STATE_PROCESSING):
            raise RuntimeError(f"Сессия {session_id} уже выполняется (статус: {s.status})")

        # 4. Check media tools (ffmpeg/ffprobe/whisper/model) via unified get_media_tools_status
        tools_status = get_media_tools_status()
        tools_ready = tools_status.ready if hasattr(tools_status, "ready") else tools_status.get("ready", False)
        tools_missing = tools_status.missing if hasattr(tools_status, "missing") else tools_status.get("missing", [])
        if not tools_ready:
            missing_str = ", ".join(tools_missing)
            raise ValueError(f"Отсутствуют необходимые медиа-утилиты: {missing_str}. Установите FFmpeg.")

        # 5. Check Whisper engine and model upfront using unified verification
        if not tools_status.whisper:
            err = tools_status.whisper_error or "Движок распознавания (whisper-cli) не найден или повреждён. Установите whisper.cpp."
            raise ValueError(err)

        if not tools_status.model:
            err = tools_status.model_error or "Файл модели Whisper не найден или повреждён. Установите модель перед запуском."
            raise ValueError(err)

        w_bin = tools_status.whisper_path
        m_path = tools_status.model_path

        # 7. Check if session has chunks to process
        has_chunks = bool(s.raw_chunks or s.normalized_chunks)
        if not has_chunks:
            s_dir = get_session_dir(session_id, create_if_missing=False)
            has_raw = (s_dir / "raw").is_dir() and any((s_dir / "raw").iterdir())
            has_norm = (s_dir / "normalized").is_dir() and any((s_dir / "normalized").iterdir())
            if not (has_raw or has_norm):
                raise ValueError(f"В сессии {session_id} отсутствуют аудиосегменты для распознавания")

        # 8. Check active worker collision
        with STATE._workers_lock:
            for wid, w in STATE.active_workers.items():
                if w.get("type") == "transcribe" and w.get("session_id") == session_id:
                    raise RuntimeError(f"Обработка сессии {session_id} уже запущена в фоновом потоке")

        # 9. Concurrency / lock check: acquire lock before background thread starts
        try:
            GLOBAL_LOCK.acquire("process", session_id)
        except LockBusyError as exc:
            raise RuntimeError(str(exc)) from exc

        lock_held_by_handler = True
        worker_registered = False
        worker_id = f"transcribe_{session_id}_{uuid.uuid4().hex[:6]}"
        try:
            # Re-read session manifest under lock to eliminate TOCTOU race with handle_queue_remove
            s = load_session(session_id)
            if not s:
                raise StorageError(f"Сессия не найдена: {session_id}")
            if not getattr(s, "in_queue", True):
                raise RuntimeError("Запись убрана из очереди. Верните её в очередь перед запуском распознавания.")
            if s.status in (STATE_RECORDING, STATE_PROCESSING):
                raise RuntimeError(f"Сессия {session_id} уже выполняется (статус: {s.status})")

            lang = body.get("lang") or body.get("language")
            if not lang:
                lang = s.language or "ru"
            elif s.language != lang:
                s.language = lang
                save_session(s)

            if "enable_diarize" in body or "enable_diarization" in body:
                en = bool(body.get("enable_diarize") or body.get("enable_diarization"))
                nspk = body.get("num_speakers")
                s.diarization_params = {
                    "enabled": en,
                    "num_speakers": int(nspk) if (nspk is not None and str(nspk).isdigit()) else None,
                }
                save_session(s)

            def _run_bg() -> None:
                try:
                    TRANSCRIBE_MANAGER.transcribe_session(
                        session_id,
                        lang=lang,
                        whisper_bin=w_bin,
                        model_path=m_path,
                        lock_already_acquired=True,
                    )
                    generate_all_exports(session_id)
                except Exception as e:
                    STATE.log(f"Transcription failed: {e}")
                finally:
                    try:
                        GLOBAL_LOCK.release(expected_session_id=session_id)
                    except Exception:
                        pass
                    STATE.unregister_worker(worker_id)

            th = threading.Thread(target=_run_bg, daemon=True)
            STATE.register_worker(worker_id, {"type": "transcribe", "session_id": session_id, "thread": th})
            worker_registered = True
            th.start()
            lock_held_by_handler = False  # Lock ownership successfully transferred to worker thread
        except Exception:
            if worker_registered:
                STATE.unregister_worker(worker_id)
            if lock_held_by_handler:
                try:
                    GLOBAL_LOCK.release(expected_session_id=session_id)
                except Exception:
                    pass
            raise

        self.send_json({"ok": True, "session_id": session_id, "status": "processing_started"})

    def handle_process_cancel(self, body: dict) -> None:
        session_id = body.get("session_id")
        TRANSCRIBE_MANAGER.cancel_transcription()
        with STATE._workers_lock:
            for wid, w in list(STATE.active_workers.items()):
                if not session_id or w.get("session_id") == session_id:
                    if w.get("cancel_event"):
                        w["cancel_event"].set()
                    proc = w.get("proc")
                    if proc and proc.poll() is None:
                        from .capture import stop_and_reap_process_group
                        stop_and_reap_process_group(proc, timeout_sec=1.5)
        self.send_json({"ok": True, "message": "Cancellation requested"})

    def handle_session_retry(self, body: dict) -> None:
        session_id_raw = body.get("session_id")
        if not session_id_raw:
            raise ValueError("session_id required")
        session_id = validate_session_id(str(session_id_raw).strip())

        GLOBAL_LOCK.acquire("retry", session_id)
        try:
            with STATE._workers_lock:
                for wid, w in STATE.active_workers.items():
                    if w.get("session_id") == session_id:
                        raise RuntimeError(
                            f"Невозможно сбросить статус: сессия занята активным процессом ({w.get('type')})"
                        )

            if TRANSCRIBE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно сбросить статус во время активного распознавания")

            if CAPTURE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно сбросить статус во время активной записи")

            manifest = retry_session(session_id)
            STATE.log(f"Session {session_id} reset to ready for retry")
            self.send_json({"ok": True, "manifest": manifest.to_dict()})
        finally:
            GLOBAL_LOCK.release(expected_session_id=session_id)

    def handle_queue_remove(self, body: dict) -> None:
        session_id_raw = body.get("session_id")
        if not session_id_raw:
            raise ValueError("session_id обязателен для исключения из очереди")
        session_id = validate_session_id(str(session_id_raw).strip())

        GLOBAL_LOCK.acquire("queue_remove", session_id)
        try:
            with STATE._workers_lock:
                for wid, w in STATE.active_workers.items():
                    if w.get("session_id") == session_id:
                        raise RuntimeError(
                            f"Невозможно убрать запись из очереди: сессия занята активным процессом ({w.get('type')})"
                        )

            if TRANSCRIBE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно убрать запись из очереди во время активного распознавания")

            if CAPTURE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно убрать запись из очереди во время активной записи")

            manifest = remove_from_queue(session_id)
            STATE.log(f"Session {session_id} removed from queue (in_queue=False)")
            self.send_json({
                "ok": True,
                "session_id": session_id,
                "in_queue": False,
                "manifest": manifest.to_dict(),
            })
        finally:
            GLOBAL_LOCK.release(expected_session_id=session_id)

    def handle_queue_restore(self, body: dict) -> None:
        session_id_raw = body.get("session_id")
        if not session_id_raw:
            raise ValueError("session_id обязателен для возврата в очередь")
        session_id = validate_session_id(str(session_id_raw).strip())

        GLOBAL_LOCK.acquire("queue_restore", session_id)
        try:
            with STATE._workers_lock:
                for wid, w in STATE.active_workers.items():
                    if w.get("session_id") == session_id:
                        raise RuntimeError(
                            f"Невозможно вернуть запись в очередь: сессия занята активным процессом ({w.get('type')})"
                        )

            if TRANSCRIBE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно вернуть запись в очередь во время активного распознавания")

            if CAPTURE_MANAGER.active_session_id == session_id:
                raise RuntimeError("Невозможно вернуть запись в очередь во время активной записи")

            manifest = restore_to_queue(session_id)
            STATE.log(f"Session {session_id} restored to queue (in_queue=True)")
            self.send_json({
                "ok": True,
                "session_id": session_id,
                "in_queue": True,
                "manifest": manifest.to_dict(),
            })
        finally:
            GLOBAL_LOCK.release(expected_session_id=session_id)

    def handle_summary(self, body: dict) -> None:
        session_id = body.get("session_id")
        if not session_id:
            raise ValueError("session_id required")
        provider = body.get("provider", "claude")
        template = body.get("template", "meeting")

        # Provider 'none' means local processing only (no external AI transmission)
        if provider == "none":
            self.send_json({
                "ok": True,
                "session_id": session_id,
                "summary": None,
                "message": "Local mode: external summary skipped to preserve local privacy",
            })
            return

        # Strict identity check for boolean True for external transmission
        opt_in = body.get("opt_in")
        if opt_in is not True:
            raise PermissionError("External summary requires explicit boolean true opt-in")

        cancel_ev = threading.Event()
        worker_id = f"summary_{session_id}_{uuid.uuid4().hex[:6]}"
        worker_rec: dict[str, Any] = {
            "type": "summary",
            "session_id": session_id,
            "thread": threading.current_thread(),
            "cancel_event": cancel_ev,
            "proc": None,
        }
        STATE.register_worker(worker_id, worker_rec)

        def on_proc(p: subprocess.Popen[str]) -> None:
            worker_rec["proc"] = p

        try:
            summary_text = generate_summary(
                session_id=session_id,
                provider=provider,
                template_name=template,
                user_opt_in=True,
                log_fn=STATE.log,
                cancel_event=cancel_ev,
                on_proc_start=on_proc,
            )
            self.send_json({"ok": True, "session_id": session_id, "summary": summary_text})
        finally:
            STATE.unregister_worker(worker_id)

    def handle_export(self, body: dict) -> None:
        session_id = body.get("session_id")
        if not session_id:
            raise ValueError("session_id required")
        results = generate_all_exports(session_id)
        self.send_json({"ok": True, "session_id": session_id, "exports": results})

    def handle_api_settings_get(self, query_str: str = "") -> None:
        params = parse_qs(query_str) if query_str else {}
        force = params.get("force_recheck", ["0"])[0].lower() in ("1", "true", "yes")
        from .config import get_effective_settings
        self.send_json(get_effective_settings(force_recheck=force))

    def handle_api_settings_post(self, body: dict) -> None:
        if CAPTURE_MANAGER.active_session_id or CAPTURE_MANAGER.is_active():
            raise RuntimeError("Невозможно изменить настройки во время активной записи")
        if TRANSCRIBE_MANAGER.active_session_id or TRANSCRIBE_MANAGER.progress.get("is_running"):
            raise RuntimeError("Невозможно изменить настройки во время активного распознавания")
        from .installer import INSTALLER
        if INSTALLER.is_running():
            raise RuntimeError("Невозможно изменить настройки во время установки компонентов")

        # Acquire cross-process GLOBAL_LOCK to eliminate race with record/process/upload/install
        try:
            GLOBAL_LOCK.acquire("settings", "save_settings")
        except LockBusyError as exc:
            raise RuntimeError(str(exc)) from exc

        try:
            from .config import get_effective_settings, save_settings
            save_settings(body)
            self.send_json({"ok": True, "settings": get_effective_settings()})
        finally:
            GLOBAL_LOCK.release(expected_session_id="save_settings")

    def handle_api_install_status(self) -> None:
        from .installer import INSTALLER
        self.send_json(INSTALLER.get_status())

    def handle_api_install_start(self, body: dict) -> None:
        if CAPTURE_MANAGER.active_session_id or CAPTURE_MANAGER.is_active():
            raise RuntimeError("Невозможно запустить установку компонентов во время активной записи")
        if TRANSCRIBE_MANAGER.active_session_id or TRANSCRIBE_MANAGER.progress.get("is_running"):
            raise RuntimeError("Невозможно запустить установку компонентов во время активного распознавания")

        from .installer import INSTALLER
        force = bool(body.get("force", False))

        def _on_before_start(th: threading.Thread) -> None:
            STATE.register_worker("installer_job", {
                "type": "install",
                "thread": th,
            })

        def _on_rollback() -> None:
            STATE.unregister_worker("installer_job")

        def _on_finish() -> None:
            STATE.unregister_worker("installer_job")

        status = INSTALLER.start_install(
            force=force,
            on_before_start=_on_before_start,
            on_rollback=_on_rollback,
            on_finish=_on_finish,
        )
        self.send_json({"ok": True, "installer": status})

    def handle_api_diarization_status(self) -> None:
        from .installer import get_diarization_install_status
        st = get_diarization_install_status()
        self.send_json({"ok": True, **st})

    def handle_api_installer_diarize(self, body: dict) -> None:
        from .installer import install_diarization_components
        force = bool(body.get("force", False))
        res = install_diarization_components(force=force)
        self.send_json({"ok": True, **res})

    def handle_session_diarize(self, body: dict) -> None:
        session_id = body.get("session_id")
        if not session_id:
            raise ValueError("session_id required")
        valid_id = validate_session_id(session_id)
        manifest = load_session(valid_id)
        if not manifest:
            raise StorageError(f"Session {valid_id} not found")

        num_speakers = body.get("num_speakers")
        if num_speakers is not None:
            try:
                num_speakers = int(num_speakers)
                if num_speakers < 2 or num_speakers > 20:
                    raise ValueError("Number of speakers must be between 2 and 20")
            except (ValueError, TypeError):
                raise ValueError("num_speakers must be an integer between 2 and 20 or null")

        from .diarizer import Diarizer, DiarizationConfig
        cfg = DiarizationConfig(num_speakers=num_speakers)
        diarizer = Diarizer(config=cfg)

        with STATE._workers_lock:
            if valid_id in STATE.active_diarizers:
                raise RuntimeError(f"Diarization is already running for session {valid_id}")
            STATE.active_diarizers[valid_id] = diarizer

        def _diarize_worker() -> None:
            try:
                def on_prog(p_data: dict[str, Any]) -> None:
                    STATE.active_diarizer_progress[valid_id] = p_data

                diarizer.run_session_diarization(valid_id, on_progress=on_prog)
            except Exception as exc:
                STATE.log(f"Diarization error for {valid_id}: {exc}")
            finally:
                with STATE._workers_lock:
                    STATE.active_diarizers.pop(valid_id, None)

        th = threading.Thread(target=_diarize_worker, daemon=True, name=f"diarize_{valid_id}")
        th.start()
        self.send_json({"ok": True, "session_id": valid_id, "status": "started"})

    def handle_session_diarize_cancel(self, body: dict) -> None:
        session_id = body.get("session_id")
        if not session_id:
            raise ValueError("session_id required")
        valid_id = validate_session_id(session_id)
        with STATE._workers_lock:
            diarizer = STATE.active_diarizers.get(valid_id)
        if diarizer:
            diarizer.cancel()
            self.send_json({"ok": True, "session_id": valid_id, "status": "cancelled"})
        else:
            self.send_json({"ok": True, "session_id": valid_id, "status": "idle"})

    def handle_session_speakers(self, body: dict) -> None:
        session_id = body.get("session_id")
        if not session_id:
            raise ValueError("session_id required")
        valid_id = validate_session_id(session_id)
        speakers = body.get("speakers")
        if not isinstance(speakers, dict):
            raise ValueError("speakers dictionary required")

        from .export import update_speaker_names
        res = update_speaker_names(valid_id, speakers)
        self.send_json({"ok": True, **res})


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> tuple[ThreadingHTTPServer, threading.Thread]:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError(f"Refusing to bind to non-loopback host: {host}")

    server = ThreadingHTTPServer((host, port), HardenedHTTPHandler)
    server.server_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
