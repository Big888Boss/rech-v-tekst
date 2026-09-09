"""Continuous native audio capture with segment journal, atomic closure, and tail recovery."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable

from .constants import (
    DEFAULT_SEGMENT_TIME_SEC,
    OUT_DIR,
    SOURCE_MIC,
    SOURCE_ZOOM,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_READY,
    STATE_RECORDING,
)
from .device import find_device, probe_device_stream_info
from .guardian import launch_guarded_process
from .lock import GLOBAL_LOCK, LockBusyError
from .media_tools import resolve_ffmpeg_bin
from .session import (
    SessionManifest,
    create_session,
    get_manifest_path,
    load_session,
    save_session,
    session_exists,
)
from .storage import (
    StorageError,
    atomic_write_text,
    check_disk_space,
    get_session_dir,
    is_safe_regular_file,
    safe_read_text,
)


def _matches_session(cmd_str: str, session_id: str) -> bool:
    """Verify that session_id appears as an exact path component or argument token in command line."""
    try:
        tokens = shlex.split(cmd_str)
    except Exception:
        tokens = cmd_str.split()
    for t in tokens:
        p = Path(t)
        if session_id in p.parts or t == session_id:
            return True
        if "=" in t:
            k, v = t.split("=", 1)
            vp = Path(v)
            if v == session_id or session_id in vp.parts:
                return True
    return False


def _extract_tokens_from_cmd(cmd_str: str) -> set[str]:
    """Extract exact --token argument values from command line string.
    Prevents prefix/suffix collision (e.g. old-token matching old-token-other) (F04).
    """
    found: set[str] = set()
    try:
        tokens = shlex.split(cmd_str)
    except Exception:
        tokens = cmd_str.split()

    it = iter(tokens)
    for t in it:
        if t == "--token":
            try:
                found.add(next(it))
            except StopIteration:
                pass
        elif t.startswith("--token="):
            found.add(t.split("=", 1)[1])
    return found


def _is_valid_guardian_cmd(cmd_str: str, expected_session_id: str) -> bool:
    """Validate that cmd_str has the authentic supervisor structure:
    executable is a python binary, invokes -m recorder.guardian, and matches expected_session_id.
    Rejects unrelated binaries (e.g. printf) or unrelated modules (e.g. python -m unrelated) (F04).
    """
    try:
        argv = shlex.split(cmd_str)
    except Exception:
        argv = cmd_str.split()
    if not argv:
        return False

    # 1. Executable must be a python binary
    exe_name = Path(argv[0]).name.lower()
    if not (exe_name.startswith("python") or exe_name == Path(sys.executable).name.lower()):
        return False

    # 2. Must invoke module recorder.guardian: python [flags] -m recorder.guardian ...
    has_guardian_module = False
    for i in range(1, len(argv) - 1):
        if argv[i] == "-m":
            if argv[i + 1] == "recorder.guardian":
                has_guardian_module = True
            break
        elif not argv[i].startswith("-"):
            break
    if not has_guardian_module:
        return False

    # 3. Must match session id exactly
    session_matched = False
    for i, arg in enumerate(argv):
        if arg in ("--session-id", "--session"):
            if i + 1 < len(argv) and argv[i + 1] == expected_session_id:
                session_matched = True
                break
        elif arg.startswith(("--session-id=", "--session=")):
            val = arg.split("=", 1)[1]
            if val == expected_session_id:
                session_matched = True
                break
    return session_matched


def is_process_alive_and_ours(
    pid: int,
    expected_session_id: str | None = None,
    expected_token: str | None = None,
    substrings: tuple[str, ...] = ("recorder.guardian", "ffmpeg", "caffeinate", "whisper"),
) -> bool:
    """Check if process with given PID exists and is authoritatively proven to belong to our application.
    Requires matching guardian token, active marker registration, or manifest record.
    Unrelated processes with matching session path substrings are strictly rejected (F04 / R03).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return False
        cmd = proc.stdout.strip()
    except Exception:
        return False

    cmd_lower = cmd.lower()
    if not any(sub in cmd_lower for sub in substrings):
        return False

    if not expected_session_id:
        return False

    # 1. Authoritative check for recorder.guardian supervisor argv identity (F04)
    if not _is_valid_guardian_cmd(cmd, expected_session_id):
        return False

    cmd_tokens = _extract_tokens_from_cmd(cmd)

    if expected_token:
        return expected_token in cmd_tokens

    # Discover known tokens from manifest, markers, and lock
    known_tokens: set[str] = set()
    try:
        m = load_session(expected_session_id)
        if m and m.operation_token:
            known_tokens.add(m.operation_token)
    except Exception:
        pass
    for m_name in (".active_capture.json", ".active_processing.json"):
        try:
            m_path = OUT_DIR / m_name
            if m_path.exists():
                d = json.loads(safe_read_text(m_path))
                if d.get("session_id") == expected_session_id and d.get("token"):
                    known_tokens.add(d["token"])
        except Exception:
            pass
    holder = GLOBAL_LOCK.get_current_holder()
    if holder and holder.get("session_id") == expected_session_id and holder.get("token"):
        known_tokens.add(holder["token"])

    if known_tokens:
        return bool(known_tokens.intersection(cmd_tokens))
    return False


def is_group_alive(pgid: int) -> bool:
    """Check whether any processes in the given process group are alive."""
    if pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def stop_and_reap_process_group(
    proc: subprocess.Popen[Any] | int,
    timeout_sec: float = 5.0,
    expected_session_id: str | None = None,
) -> bool:
    """Bounded stop and reap of a process group with SIGINT -> SIGTERM -> SIGKILL escalation.
    Never signals an unknown process. Ensures all descendants in pgid are reaped,
    even if the process leader has already exited.
    Returns True if the entire group is confirmed dead, False otherwise.
    """
    if isinstance(proc, int):
        pid = proc
        p = None
    else:
        pid = proc.pid
        p = proc

    if pid <= 0:
        return True

    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid

    # Check if process is alive and actually ours before sending any signals!
    if p is None and not is_process_alive_and_ours(pid, expected_session_id=expected_session_id):
        # Leader might have exited, but check if group has descendants
        if not is_group_alive(pgid):
            return True
        # If group is alive but we can't confirm it's ours, do not signal arbitrary processes
        if not is_process_alive_and_ours(pgid, expected_session_id=expected_session_id):
            return False

    # 1. Graceful SIGINT
    try:
        os.killpg(pgid, signal.SIGINT)
    except OSError:
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass

    start_wait = time.time()
    while time.time() - start_wait < timeout_sec:
        if not is_group_alive(pgid) and (p is None or p.poll() is not None):
            return True
        time.sleep(0.05)

    # 2. Escalate to SIGTERM
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    start_term = time.time()
    while time.time() - start_term < 1.5:
        if not is_group_alive(pgid) and (p is None or p.poll() is not None):
            return True
        time.sleep(0.05)

    # 3. Escalate to SIGKILL
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    start_kill = time.time()
    while time.time() - start_kill < 1.5:
        if p and p.poll() is None:
            try:
                p.wait(timeout=0.1)
            except Exception:
                pass
        if not is_group_alive(pgid):
            return True
        time.sleep(0.05)

    return not is_group_alive(pgid)


def validate_and_probe_wav(file_path: Path) -> dict[str, Any] | None:
    """Validate WAV RIFF structure, verify physical data payload matches declared frames,
    and extract exact frame count, sample rate, channels, and bits per sample.
    Returns metadata dict if valid and non-empty, None if corrupted or truncated.
    """
    if not is_safe_regular_file(file_path):
        return None

    try:
        st = file_path.stat()
        if st.st_size < 44:
            return None

        # Check RIFF/WAVE header bytes
        with open(file_path, "rb") as f:
            magic = f.read(12)
            if len(magic) < 12 or magic[:4] != b"RIFF" or magic[8:12] != b"WAVE":
                return None

        with wave.open(str(file_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            frames = wf.getnframes()
            sampwidth = wf.getsampwidth()
            comptype = wf.getcomptype()

            if comptype != "NONE" or rate <= 0 or frames <= 0 or channels <= 0 or sampwidth <= 0:
                return None

            bytes_per_frame = channels * sampwidth
            expected_data_bytes = frames * bytes_per_frame

            # Reject physically truncated payloads (e.g. 100-byte file claiming 48000 frames)
            if st.st_size < expected_data_bytes + 44:
                return None

            # Verify that final frame actually exists and can be read from disk
            try:
                wf.setpos(frames - 1)
                last_frame = wf.readframes(1)
                if len(last_frame) != bytes_per_frame:
                    return None
            except Exception:
                return None

            dur = float(frames) / float(rate)
            return {
                "channels": channels,
                "sample_rate": rate,
                "frames": frames,
                "sampwidth": sampwidth,
                "bits_per_sample": sampwidth * 8,
                "duration_sec": round(dur, 3),
            }
    except Exception:
        return None


def read_segment_journal(journal_path: Path) -> set[str]:
    """Read filenames of completed segments written to ffmpeg segment_list journal."""
    closed_names: set[str] = set()
    if not journal_path.exists() or not is_safe_regular_file(journal_path):
        return closed_names

    try:
        with open(journal_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0].strip():
                    filename = Path(row[0].strip()).name
                    closed_names.add(filename)
    except Exception:
        pass
    return closed_names


class CaptureManager:
    """Manages continuous native-rate audio recording processes with journaled atomic closure."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.active_session_id: str | None = None
        self.active_process: subprocess.Popen[str] | None = None
        self.active_operation_token: str | None = None
        self._stop_event = threading.Event()
        self._completion_events: dict[str, threading.Event] = {}
        self._watcher_thread: threading.Thread | None = None
        self.log_callback: Callable[[str], None] | None = None

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self.log_callback = cb

    def _log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)

    def is_active(self, session_id: str | None = None) -> bool:
        with self._lock:
            if not self.active_session_id or not self.active_process:
                return False
            if session_id and self.active_session_id != session_id:
                return False
            return self.active_process.poll() is None

    def get_completion_event(self, session_id: str) -> threading.Event:
        with self._lock:
            if session_id not in self._completion_events:
                self._completion_events[session_id] = threading.Event()
            return self._completion_events[session_id]

    def wait_for_completion(self, session_id: str, timeout: float = 5.0) -> bool:
        event = self.get_completion_event(session_id)
        return event.wait(timeout=timeout)

    def _clean_active_marker(self, expected_token: str | None = None) -> None:
        active_marker = OUT_DIR / ".active_capture.json"
        if not active_marker.exists():
            return
        try:
            data = json.loads(safe_read_text(active_marker))
            if expected_token is None or data.get("token") == expected_token:
                active_marker.unlink(missing_ok=True)
        except Exception:
            if expected_token is None:
                active_marker.unlink(missing_ok=True)

    def start_capture(
        self,
        session_id: str,
        title: str | None = None,
        source_kind: str = SOURCE_ZOOM,
        device_index: int | None = None,
        segment_time_sec: int = DEFAULT_SEGMENT_TIME_SEC,
        ffmpeg_bin: str = "ffmpeg",
    ) -> SessionManifest:
        """Start atomic segmented audio recording in native hardware format."""
        with self._lock:
            if self.is_active():
                raise RuntimeError(
                    f"Another recording session ({self.active_session_id}) is actively capturing"
                )

            # Check if session already exists BEFORE doing any hardware device probe!
            if session_exists(session_id):
                raise StorageError(f"Session already exists: {session_id}")

            active_marker = OUT_DIR / ".active_capture.json"
            if active_marker.exists():
                try:
                    m_data = json.loads(safe_read_text(active_marker))
                    m_pid = m_data.get("pid")
                    m_sid = m_data.get("session_id")
                    if m_pid and is_process_alive_and_ours(m_pid, expected_session_id=m_sid):
                        raise LockBusyError(
                            f"Another capture is actively recording for session {m_sid} (PID {m_pid})"
                        )
                    else:
                        active_marker.unlink(missing_ok=True)
                except (json.JSONDecodeError, OSError):
                    active_marker.unlink(missing_ok=True)

            disk = check_disk_space()
            if disk["is_low"]:
                raise RuntimeError(
                    f"Insufficient disk space ({disk['free_bytes'] // (1024*1024)} MB available). "
                    f"Minimum 500 MB required."
                )

            try:
                GLOBAL_LOCK.acquire("record", session_id)
            except LockBusyError as exc:
                raise RuntimeError(str(exc)) from exc

            lock_held = True
            proc = None
            log_file = None
            manifest = None
            op_token = uuid.uuid4().hex

            comp_event = threading.Event()
            self._completion_events[session_id] = comp_event

            try:
                dev = find_device(kind=source_kind, index=device_index, ffmpeg_bin=ffmpeg_bin)
                dev_idx = dev["index"] if dev else (0 if device_index is None else device_index)
                dev_name = dev["name"] if dev else f"Device {dev_idx}"

                # Probe device native rate on capture start
                stream_info = probe_device_stream_info(dev_idx, ffmpeg_bin=ffmpeg_bin)
                native_rate = stream_info.get("sample_rate")
                native_channels = stream_info.get("channels")

                manifest = create_session(
                    session_id=session_id,
                    title=title.strip() if title and title.strip() else session_id,
                    source_kind=source_kind,
                    device_info={"index": dev_idx, "name": dev_name, "probed_stream": stream_info},
                    native_sample_rate=native_rate,
                    native_channels=native_channels,
                )

                session_dir = get_session_dir(session_id)
                raw_dir = session_dir / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                segment_pattern = str(raw_dir / "raw_chunk_%03d.wav")
                journal_path = str(raw_dir / "journal.csv")

                try:
                    resolved_ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
                except FileNotFoundError as exc:
                    raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}'). Required for capture.") from exc
                if not resolved_ffmpeg:
                    raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}'). Required for capture.")

                cmd = [
                    resolved_ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-f",
                    "avfoundation",
                    "-i",
                    f":{dev_idx}",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "segment",
                    "-segment_time",
                    str(segment_time_sec),
                    "-segment_list",
                    journal_path,
                    "-segment_list_type",
                    "csv",
                    "-segment_list_size",
                    "0",
                    "-reset_timestamps",
                    "1",
                    segment_pattern,
                ]

                # Prevent macOS sleep while recording
                if shutil.which("caffeinate"):
                    cmd = ["caffeinate", "-i"] + cmd

                log_path = session_dir / "capture.log"
                log_file = open(log_path, "a", encoding="utf-8")

                child_env = dict(os.environ)
                child_env["WEBINAR_SESSION_ID"] = session_id
                child_env["WEBINAR_OPERATION_TOKEN"] = op_token

                # Publish durable recording state and token prior to child spawn (F01c)
                manifest.status = STATE_RECORDING
                manifest.operation_token = op_token
                manifest.error_message = None
                save_session(manifest)

                lock_fd = GLOBAL_LOCK.prepare_inheritable_fd()

                proc = launch_guarded_process(
                    cmd,
                    action="record",
                    session_id=session_id,
                    token=op_token,
                    cwd=str(session_dir),
                    log_path=str(log_path),
                    session_dir=str(session_dir),
                    lock_fd=lock_fd,
                )
                time.sleep(0.05)
                if proc.poll() is not None:
                    manifest.status = STATE_FAILED
                    manifest.error_message = f"Capture process failed to start (exit code {proc.returncode})"
                    save_session(manifest)
                    raise RuntimeError(f"Capture process failed to start (exit code {proc.returncode})")

                # Lock inheritance confirmed: transfer ownership to guardian via close-only
                GLOBAL_LOCK.transfer_ownership()

                pgid = proc.pid
                proc.saved_pgid = pgid

                self.active_session_id = session_id
                self.active_process = proc
                self.active_pgid = pgid
                self.active_operation_token = op_token
                manifest.pid = proc.pid
                save_session(manifest)

                # Write active capture marker atomically
                try:
                    atomic_write_text(
                        active_marker,
                        json.dumps({
                            "session_id": session_id,
                            "pid": proc.pid,
                            "pgid": pgid,
                            "token": op_token,
                            "start_time": time.time(),
                        }, indent=2),
                    )
                except OSError:
                    pass

                self._stop_event.clear()
                self._watcher_thread = threading.Thread(
                    target=self._watch_capture,
                    args=(proc, session_id, raw_dir, log_file, op_token, comp_event),
                    daemon=True,
                )
                self._watcher_thread.start()
                return manifest

            except Exception as exc:
                if proc is not None:
                    stop_and_reap_process_group(proc, timeout_sec=2.0, expected_session_id=session_id)
                if log_file and not log_file.closed:
                    try:
                        log_file.close()
                    except Exception:
                        pass
                if lock_held:
                    try:
                        GLOBAL_LOCK.release()
                    except Exception:
                        pass
                self.active_process = None
                self.active_session_id = None
                self.active_operation_token = None
                self._clean_active_marker(op_token)
                comp_event.set()
                if manifest is not None:
                    try:
                        manifest.status = STATE_FAILED
                        manifest.error_message = f"Startup failure: {exc}"
                        save_session(manifest)
                    except Exception:
                        pass
                raise

    def stop_capture(self, session_id: str | None = None) -> SessionManifest:
        """Stop active capture with graceful SIGINT, bounded wait, and escalation."""
        with self._lock:
            target_id = session_id or self.active_session_id
            if not target_id:
                raise RuntimeError("No active recording session")

            manifest = load_session(target_id)
            if not manifest:
                raise RuntimeError(f"Session {target_id} not found")

            # Check if target session is already stopped
            if manifest.status != STATE_RECORDING and target_id != self.active_session_id:
                return manifest

            if self.active_session_id is not None and target_id != self.active_session_id:
                raise RuntimeError(
                    f"Cannot stop session {target_id}: another session ({self.active_session_id}) is currently recording"
                )

            proc = self.active_process
            pid = proc.pid if proc else manifest.pid
            token = self.active_operation_token or manifest.operation_token

            # Only send signals if process is PROVEN alive and ours!
            stopped_ok = True
            if proc and proc.poll() is None:
                self._log(f"Stopping recording for session {target_id} via guardian (PID {proc.pid})...")
                if proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.write("stop\n")
                        proc.stdin.flush()
                        proc.stdin.close()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=5.0)
                    stopped_ok = (proc.returncode in (0, 130))
                except subprocess.TimeoutExpired:
                    stopped_ok = stop_and_reap_process_group(
                        proc,
                        timeout_sec=2.0,
                        expected_session_id=target_id,
                    )
            elif pid and is_process_alive_and_ours(pid, expected_session_id=target_id):
                self._log(f"Stopping orphaned process group (PID {pid})...")
                stopped_ok = stop_and_reap_process_group(
                    pid,
                    timeout_sec=5.0,
                    expected_session_id=target_id,
                )

            if not stopped_ok:
                manifest.status = STATE_FAILED
                manifest.error_message = f"Failed to stop capture process group (PID {pid})"
                save_session(manifest)
                raise RuntimeError(manifest.error_message)

            self._clean_active_marker(token)
            self._stop_event.set()
            self.active_process = None
            self.active_session_id = None
            self.active_operation_token = None
            try:
                GLOBAL_LOCK.release()
            except Exception:
                pass

            manifest = self._index_raw_chunks(manifest, is_stopped=True)
            manifest.status = STATE_READY if manifest.raw_chunks else STATE_INTERRUPTED
            manifest.pid = None
            save_session(manifest)

            event = self.get_completion_event(target_id)
            event.set()

            self._log(f"Recording stopped for session {target_id}. {len(manifest.raw_chunks)} valid chunk(s) indexed.")
            return manifest

    def _index_raw_chunks(self, manifest: SessionManifest, is_stopped: bool = False) -> SessionManifest:
        """Index only closed and validated WAV chunks with bound source hashes."""
        session_dir = get_session_dir(manifest.session_id)
        raw_dir = session_dir / "raw"
        if not raw_dir.exists():
            return manifest

        journal_path = raw_dir / "journal.csv"
        closed_in_journal = read_segment_journal(journal_path)

        valid_chunks: list[dict[str, Any]] = []
        raw_files = sorted(raw_dir.glob("raw_chunk_*.wav"))
        total_duration = 0.0
        total_frames = 0
        target_sample_rate = 0

        for idx, f in enumerate(raw_files):
            wav_info = validate_and_probe_wav(f)
            if wav_info is None:
                continue

            is_closed = f.name in closed_in_journal
            if is_stopped and idx == len(raw_files) - 1 and wav_info["frames"] > 0:
                is_closed = True

            if not is_closed:
                continue

            target_sample_rate = wav_info["sample_rate"]
            total_frames += wav_info["frames"]
            dur = wav_info["duration_sec"]
            total_duration += dur

            try:
                raw_bytes = f.read_bytes()
                source_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
            except Exception:
                source_hash = ""

            valid_chunks.append({
                "index": idx,
                "filename": f.name,
                "duration_sec": dur,
                "sample_rate": wav_info["sample_rate"],
                "channels": wav_info["channels"],
                "frames": wav_info["frames"],
                "bits_per_sample": wav_info["bits_per_sample"],
                "source_hash": source_hash,
                "closed": True,
                "size_bytes": f.stat().st_size,
            })

        manifest.raw_chunks = valid_chunks
        if target_sample_rate > 0:
            manifest.total_duration_sec = round(total_frames / target_sample_rate, 3)
        else:
            manifest.total_duration_sec = round(total_duration, 3)
        return manifest

    def _watch_capture(
        self,
        proc: subprocess.Popen[str],
        session_id: str,
        raw_dir: Path,
        log_file: Any,
        op_token: str,
        comp_event: threading.Event,
    ) -> None:
        journal_path = raw_dir / "journal.csv"
        last_chunk_count = 0

        try:
            while proc.poll() is None and not self._stop_event.is_set():
                disk = check_disk_space()
                if disk["is_low"]:
                    self._log("WARNING: Disk space critically low (<500MB)! Stopping recording automatically...")
                    stop_and_reap_process_group(proc, timeout_sec=5.0, expected_session_id=session_id)
                    break

                closed = read_segment_journal(journal_path)
                if len(closed) > last_chunk_count:
                    last_chunk_count = len(closed)
                    manifest = load_session(session_id)
                    if manifest and manifest.status == STATE_RECORDING:
                        manifest = self._index_raw_chunks(manifest, is_stopped=False)
                        save_session(manifest)
                        self._log(f"Indexed {len(manifest.raw_chunks)} closed chunk(s) so far...")

                time.sleep(1.0)
        finally:
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass
            if log_file and not log_file.closed:
                try:
                    log_file.close()
                except Exception:
                    pass

        returncode = proc.returncode

        with self._lock:
            if self.active_session_id == session_id:
                self.active_process = None
                self.active_session_id = None
                self.active_operation_token = None
                try:
                    GLOBAL_LOCK.release()
                except Exception:
                    pass

            self._clean_active_marker(op_token)

            manifest = load_session(session_id)
            if manifest:
                manifest = self._index_raw_chunks(manifest, is_stopped=True)
                if returncode not in (0, 255, -2, -15):
                    if not manifest.raw_chunks:
                        manifest.status = STATE_FAILED
                        manifest.error_message = f"Capture process failed (exit code {returncode})"
                    else:
                        manifest.status = STATE_INTERRUPTED
                        manifest.error_message = f"Capture interrupted unexpectedly (exit code {returncode})"
                elif manifest.status == STATE_RECORDING:
                    manifest.status = STATE_READY if manifest.raw_chunks else STATE_INTERRUPTED
                    manifest.error_message = None if manifest.raw_chunks else "No audio data was captured"
                manifest.pid = None
                save_session(manifest)
                self._log(f"Capture process terminated for {session_id} with status {manifest.status}")

            comp_event.set()

    def reconcile_on_startup(self) -> list[str]:
        """Scan sessions and reconcile orphaned recording and processing sessions (L01, L07, F01, F04)."""
        reconciled = []
        sessions = OUT_DIR.glob("*/session.json") if OUT_DIR.exists() else []

        # Check current lock holder to avoid declaring live owner session interrupted (F01, F04)
        holder = GLOBAL_LOCK.get_current_holder()
        active_holder_sid = None
        if holder:
            h_pid = holder.get("pid")
            h_sid = holder.get("session_id")
            if h_pid and h_sid:
                try:
                    os.kill(h_pid, 0)
                    active_holder_sid = h_sid
                except OSError:
                    pass

        for path in sessions:
            try:
                sid = path.parent.name
                if sid == active_holder_sid:
                    # Session is actively protected by a living guardian holding the lock!
                    # Do not interrupt or corrupt its state (F01).
                    continue

                manifest = load_session(sid)
                if not manifest:
                    continue

                if manifest.status == STATE_RECORDING:
                    pid = manifest.pid
                    if not pid or not is_process_alive_and_ours(pid, expected_session_id=sid):
                        manifest = self._index_raw_chunks(manifest, is_stopped=False)
                        manifest.status = STATE_READY if manifest.raw_chunks else STATE_INTERRUPTED
                        manifest.pid = None
                        manifest.error_message = "Session interrupted by previous shutdown"
                        save_session(manifest)
                        reconciled.append(sid)

                elif manifest.status == "processing":
                    pid = manifest.processing_pid or manifest.pid
                    if not pid or not is_process_alive_and_ours(pid, expected_session_id=sid, substrings=("recorder.guardian", "whisper", "ffmpeg")):
                        manifest.status = STATE_INTERRUPTED
                        manifest.error_message = "Processing interrupted by previous shutdown"
                        manifest.pid = None
                        manifest.processing_pid = None
                        save_session(manifest)
                        reconciled.append(sid)

                if manifest.summary_status == "running":
                    manifest.summary_status = "interrupted"
                    save_session(manifest)
                    if sid not in reconciled:
                        reconciled.append(sid)
            except Exception:
                pass

        for m_name in (".active_capture.json", ".active_processing.json"):
            marker = OUT_DIR / m_name
            if marker.exists():
                try:
                    marker_data = json.loads(safe_read_text(marker))
                    m_pid = marker_data.get("pid")
                    m_gpid = marker_data.get("guardian_pid")
                    m_sid = marker_data.get("session_id")
                    alive_ours = False
                    for check_p in (m_gpid, m_pid):
                        if check_p and is_process_alive_and_ours(check_p, expected_session_id=m_sid):
                            alive_ours = True
                            break
                    if not alive_ours:
                        marker.unlink(missing_ok=True)
                except Exception:
                    marker.unlink(missing_ok=True)

        return reconciled


CAPTURE_MANAGER = CaptureManager()
