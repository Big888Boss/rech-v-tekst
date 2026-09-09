"""Whisper transcription orchestration, source-bound checkpoints, and atomic results."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .capture import stop_and_reap_process_group, validate_and_probe_wav
from .constants import (
    DEFAULT_MODEL_PATH,
    OUT_DIR,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    TARGET_WHISPER_CHANNELS,
    TARGET_WHISPER_RATE,
)
from .lock import GLOBAL_LOCK, LockBusyError
from .normalize import get_audio_metadata, normalize_chunk
from .session import SessionManifest, load_session, save_session
from .storage import (
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_make_dir,
    safe_read_text,
)


def format_timestamp(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


class TranscriptionManager:
    """Manages Whisper transcription pipeline with source-bound checkpoints and ETA."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.active_session_id: str | None = None
        self.active_operation_token: str | None = None
        self._cancel_requested = False
        self._cancel_event = threading.Event()
        self._active_proc: subprocess.Popen[str] | None = None
        self.progress: dict[str, Any] = {
            "session_id": None,
            "total_chunks": 0,
            "completed_chunks": 0,
            "current_chunk": 0,
            "elapsed_sec": 0.0,
            "eta_sec": None,
            "is_running": False,
        }
        self.log_callback: Callable[[str], None] | None = None

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self.log_callback = cb

    def _log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)

    def cancel_transcription(self) -> None:
        """Request cancellation of running transcription via control channel and bounded escalation."""
        with self._lock:
            self._cancel_requested = True
            self._cancel_event.set()
            proc = self._active_proc

        if proc and proc.poll() is None:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.write("cancel\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                stop_and_reap_process_group(proc, timeout_sec=2.0)

    def _clean_processing_marker(self, expected_token: str | None = None) -> None:
        marker = OUT_DIR / ".active_processing.json"
        if not marker.exists():
            return
        try:
            data = json.loads(safe_read_text(marker))
            if expected_token is None or data.get("token") == expected_token:
                marker.unlink(missing_ok=True)
        except Exception:
            if expected_token is None:
                marker.unlink(missing_ok=True)

    def _run_process_isolated(self, cmd: list[str], timeout: float) -> tuple[int, str, str]:
        """Run process under guardian supervision with bounded escalation, lock inheritance,
        and continuous stream draining to prevent pipe deadlocks (R01, R02).
        """
        if self._cancel_requested or self._cancel_event.is_set():
            raise InterruptedError("Transcription was cancelled by operator")

        session_id = self.active_session_id or "transcribe"
        token = self.active_operation_token or uuid.uuid4().hex[:8]
        session_dir = get_session_dir(session_id) if session_id else None

        lock_fd = GLOBAL_LOCK.prepare_inheritable_fd()
        # Retain parent lock FD across chunks so lock does not drop between pipeline steps

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as out_f, \
             tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as err_f:
            stdout_path = Path(out_f.name)
            stderr_path = Path(err_f.name)

        proc = None
        try:
            from .guardian import launch_guarded_process
            proc = launch_guarded_process(
                cmd,
                action="transcribe",
                session_id=session_id,
                token=token,
                cwd=str(session_dir) if session_dir and session_dir.exists() else str(Path.cwd()),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                session_dir=str(session_dir) if session_dir else None,
                lock_fd=lock_fd,
            )

            with self._lock:
                self._active_proc = proc

            if session_id:
                try:
                    m = load_session(session_id)
                    if m and m.status == STATE_PROCESSING:
                        m.processing_pid = proc.pid
                        save_session(m)
                except Exception:
                    pass

            start_time = time.monotonic()
            while True:
                ret = proc.poll()
                if ret is not None:
                    break

                if self._cancel_requested or self._cancel_event.is_set():
                    if proc.stdin and not proc.stdin.closed:
                        try:
                            proc.stdin.write("cancel\n")
                            proc.stdin.flush()
                            proc.stdin.close()
                        except (BrokenPipeError, OSError):
                            pass
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        from .capture import stop_and_reap_process_group
                        stop_and_reap_process_group(proc, timeout_sec=2.0)
                    raise InterruptedError("Transcription was cancelled by operator")

                if time.monotonic() - start_time > timeout:
                    if proc.stdin and not proc.stdin.closed:
                        try:
                            proc.stdin.write("cancel\n")
                            proc.stdin.flush()
                            proc.stdin.close()
                        except (BrokenPipeError, OSError):
                            pass
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        from .capture import stop_and_reap_process_group
                        stop_and_reap_process_group(proc, timeout_sec=2.0)
                    raise TimeoutError(f"Transcription process timed out after {timeout}s: {cmd[0]}")

                time.sleep(0.05)

            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
            return proc.returncode, stdout, stderr
        finally:
            with self._lock:
                if self._active_proc is proc:
                    self._active_proc = None
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)

    def transcribe_session(
        self,
        session_id: str,
        lang: str = "ru",
        model_path: Path | None = None,
        whisper_bin: str | None = None,
        ffmpeg_bin: str = "ffmpeg",
        lock_already_acquired: bool = False,
    ) -> SessionManifest:
        """Run complete transcription pipeline for a session with source-bound normalization."""
        with self._lock:
            if self.progress["is_running"]:
                raise RuntimeError("Another transcription is already running")

            if not lock_already_acquired:
                try:
                    GLOBAL_LOCK.acquire("process", session_id)
                except LockBusyError as exc:
                    raise RuntimeError(str(exc)) from exc

            op_token = uuid.uuid4().hex
            self._cancel_requested = False
            self._cancel_event.clear()
            self.active_session_id = session_id
            self.active_operation_token = op_token
            self.progress = {
                "session_id": session_id,
                "total_chunks": 0,
                "completed_chunks": 0,
                "current_chunk": 0,
                "elapsed_sec": 0.0,
                "eta_sec": None,
                "is_running": True,
            }

        lock_held = True
        manifest = None
        start_time = time.time()
        try:
            manifest = load_session(session_id)
            if not manifest:
                raise StorageError(f"Session {session_id} not found")

            manifest.status = STATE_PROCESSING
            manifest.language = lang
            manifest.operation_token = op_token
            save_session(manifest)

            # Write active processing marker
            try:
                marker_path = OUT_DIR / ".active_processing.json"
                atomic_write_text(
                    marker_path,
                    json.dumps({
                        "session_id": session_id,
                        "token": op_token,
                        "start_time": time.time(),
                    }, indent=2),
                )
            except OSError:
                pass

            session_dir = get_session_dir(session_id)
            raw_dir = session_dir / "raw"
            norm_dir = session_dir / "normalized"
            safe_make_dir(norm_dir)

            # SOURCE-BOUND NORMALIZATION CHECK (R04):
            if manifest.raw_chunks:
                raw_filenames = [c["filename"] for c in manifest.raw_chunks if c.get("closed")]
                raw_files = []
                for fn in raw_filenames:
                    p = raw_dir / fn
                    if not is_safe_regular_file(p):
                        raise StorageError(f"Declared source chunk missing or corrupted: {fn}")
                    raw_files.append(p)
            else:
                raw_files = []

            if raw_files:
                self._log(f"Verifying normalization for {len(raw_files)} raw chunk(s)...")
                normalized_records: list[dict[str, Any]] = []
                total_duration = 0.0
                total_frames = 0
                existing_norm = {rec.get("filename"): rec for rec in (manifest.normalized_chunks or [])}

                def _on_norm_proc(p: subprocess.Popen[str]) -> None:
                    with self._lock:
                        self._active_proc = p

                for idx, raw_f in enumerate(raw_files):
                    if self._cancel_requested or self._cancel_event.is_set():
                        raise InterruptedError("Transcription was cancelled by operator")

                    target_chunk_name = f"chunk_{idx:03d}.wav"
                    target_norm_path = norm_dir / target_chunk_name
                    legacy_path = session_dir / target_chunk_name

                    rec = normalize_chunk(
                        raw_f,
                        target_norm_path,
                        ffmpeg_bin=ffmpeg_bin,
                        log_fn=self._log,
                        cancel_event=self._cancel_event,
                        on_proc_start=_on_norm_proc,
                    )
                    rec["index"] = idx
                    normalized_records.append(rec)
                    total_frames += rec.get("frames", 0)
                    total_duration += rec["duration_sec"]

                    if not legacy_path.exists():
                        try:
                            shutil.copy2(target_norm_path, legacy_path)
                        except OSError:
                            pass

                with self._lock:
                    if self._active_proc is not None and self._active_proc.poll() is not None:
                        self._active_proc = None

                manifest.normalized_chunks = normalized_records
                if total_frames > 0:
                    manifest.total_duration_sec = round(total_frames / TARGET_WHISPER_RATE, 3)
                else:
                    manifest.total_duration_sec = round(total_duration, 3)
                save_session(manifest)
                chunk_files = [norm_dir / rec["filename"] for rec in normalized_records]
            elif manifest.normalized_chunks:
                chunk_files = []
                for rec in manifest.normalized_chunks:
                    fname = rec["filename"]
                    p = norm_dir / fname
                    if not p.exists():
                        legacy_p = session_dir / fname
                        if is_safe_regular_file(legacy_p):
                            safe_make_dir(norm_dir)
                            try:
                                shutil.copy2(legacy_p, p)
                            except OSError:
                                pass
                    if not is_safe_regular_file(p):
                        raise StorageError(f"Declared normalized chunk missing: {fname}")
                    chunk_files.append(p)
            else:
                # Neither closed raw chunks nor declared normalized chunks exist!
                # Do NOT glob unknown files; fail gracefully per R04.
                raise RuntimeError(f"Session {session_id} has no closed chunks available for processing")

            # Validate external Whisper binary and model using unified preflight
            from .media_tools import get_media_tools_status
            tools_status = get_media_tools_status(whisper_bin=whisper_bin, model_path=model_path)
            if not tools_status.whisper:
                raise RuntimeError(tools_status.whisper_error or "Whisper executable (whisper-cli) не найден или повреждён. Установите whisper.cpp.")
            if not tools_status.model:
                raise RuntimeError(tools_status.model_error or f"Whisper model file not ready: {tools_status.model_path}")

            w_bin = tools_status.whisper_path
            assert w_bin is not None
            m_path = Path(tools_status.model_path)

            total_chunks = len(chunk_files)
            self.progress["total_chunks"] = total_chunks
            transcripts_dir = session_dir / "transcripts"
            safe_make_dir(transcripts_dir)

            all_segments: list[dict[str, Any]] = []
            consolidated_text_lines: list[str] = []
            chunk_durations: list[float] = []
            cumulative_offset_sec = 0.0

            for idx, chunk_file in enumerate(chunk_files):
                if self._cancel_requested:
                    raise InterruptedError("Transcription cancelled by operator")

                self.progress["current_chunk"] = idx + 1
                self.progress["elapsed_sec"] = round(time.time() - start_time, 1)

                chunk_meta = get_audio_metadata(chunk_file)
                chunk_audio_dur = chunk_meta["duration_sec"]

                self._log(f"Transcribing chunk {idx+1}/{total_chunks} ({chunk_file.name}, {chunk_audio_dur:.1f}s)...")
                t0 = time.time()
                chunk_txt, chunk_segs = self._run_whisper_chunk(
                    w_bin=w_bin,
                    m_path=m_path,
                    lang=lang,
                    chunk_file=chunk_file,
                    transcripts_dir=transcripts_dir,
                    chunk_index=idx,
                )
                chunk_time = time.time() - t0
                chunk_durations.append(chunk_time)

                self.progress["completed_chunks"] = idx + 1
                self.progress["elapsed_sec"] = round(time.time() - start_time, 1)

                remaining = total_chunks - (idx + 1)
                avg_time_per_chunk = sum(chunk_durations) / len(chunk_durations)
                self.progress["eta_sec"] = round(avg_time_per_chunk * remaining, 1) if remaining > 0 else 0.0

                if chunk_txt:
                    consolidated_text_lines.append(chunk_txt.strip())
                if chunk_segs:
                    for seg in chunk_segs:
                        seg_offset = dict(seg)
                        seg_offset["from_sec"] = round(seg.get("from_sec", 0.0) + cumulative_offset_sec, 2)
                        seg_offset["to_sec"] = round(seg.get("to_sec", 0.0) + cumulative_offset_sec, 2)
                        all_segments.append(seg_offset)

                cumulative_offset_sec += chunk_audio_dur

            if self._cancel_requested:
                raise InterruptedError("Transcription cancelled by operator")

            # Atomic write of final consolidated transcripts
            full_transcript_text = "\n\n".join(consolidated_text_lines)
            transcript_txt_path = session_dir / "transcript.txt"
            atomic_write_text(transcript_txt_path, full_transcript_text)

            transcript_json_path = session_dir / "transcript.json"
            json_payload = {
                "session_id": session_id,
                "language": lang,
                "model": m_path.name,
                "segments": all_segments,
                "full_text": full_transcript_text,
            }
            atomic_write_text(transcript_json_path, json.dumps(json_payload, indent=2, ensure_ascii=False))

            word_count = len(full_transcript_text.split())
            manifest.status = STATE_COMPLETED
            manifest.has_transcript = True
            manifest.word_count = word_count
            manifest.error_message = None
            manifest.processing_pid = None
            save_session(manifest)
            self._log(f"Transcription completed: {word_count} words across {len(chunk_files)} chunk(s).")
            return manifest

        except InterruptedError as exc:
            if manifest:
                manifest.status = STATE_INTERRUPTED
                manifest.error_message = str(exc)
                manifest.processing_pid = None
                save_session(manifest)
            self._log(f"Transcription interrupted: {exc}")
            return manifest
        except Exception as exc:
            if manifest:
                try:
                    manifest.status = STATE_FAILED
                    manifest.error_message = f"Transcription error: {exc}"
                    manifest.processing_pid = None
                    save_session(manifest)
                except Exception:
                    pass
            self._log(f"Transcription failed: {exc}")
            raise
        finally:
            with self._lock:
                self.progress["is_running"] = False
                self.active_session_id = None
                self.active_operation_token = None
                if lock_held and not lock_already_acquired:
                    try:
                        GLOBAL_LOCK.release(expected_session_id=session_id)
                    except Exception:
                        pass
            self._clean_processing_marker(op_token)

    def _run_whisper_chunk(
        self,
        w_bin: str,
        m_path: Path,
        lang: str,
        chunk_file: Path,
        transcripts_dir: Path,
        chunk_index: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        if self._cancel_requested:
            raise InterruptedError("Transcription cancelled by operator")

        # Unique staging directory per attempt (R10)
        staging_dir = transcripts_dir / f".staging_{chunk_index:03d}_{uuid.uuid4().hex[:8]}"
        safe_make_dir(staging_dir)
        gpu_out_base = staging_dir / f"gpu_chunk_{chunk_index:03d}"
        cpu_out_base = staging_dir / f"cpu_chunk_{chunk_index:03d}"

        from .config import load_settings
        saved_settings = load_settings()

        cpu_nice = os.environ.get("WHISPER_CPU_NICE", "10")
        env_cpu_threads = os.environ.get("WHISPER_CPU_THREADS")
        cpu_threads = env_cpu_threads if (env_cpu_threads and env_cpu_threads.isdigit()) else str(saved_settings.cpu_threads)

        env_gpu_threads = os.environ.get("WHISPER_THREADS")
        gpu_threads = env_gpu_threads if (env_gpu_threads and env_gpu_threads.isdigit()) else str(saved_settings.threads)

        env_no_gpu = os.environ.get("WHISPER_NO_GPU")
        if env_no_gpu is not None:
            effective_no_gpu = env_no_gpu.strip().lower() in ("1", "true", "yes")
        else:
            effective_no_gpu = saved_settings.no_gpu
        use_gpu = not effective_no_gpu
        success_provider = None
        text_content = ""
        segments: list[dict[str, Any]] = []

        try:
            if use_gpu:
                cmd = [
                    w_bin,
                    "-m",
                    str(m_path),
                    "-l",
                    lang,
                    "-f",
                    str(chunk_file),
                    "-otxt",
                    "-oj",
                    "-of",
                    str(gpu_out_base),
                    "-t",
                    gpu_threads,
                    "-pp",
                ]
                try:
                    ret, _, stderr = self._run_process_isolated(cmd, timeout=600)
                    if ret == 0:
                        gpu_txt = staging_dir / f"gpu_chunk_{chunk_index:03d}.txt"
                        gpu_json = staging_dir / f"gpu_chunk_{chunk_index:03d}.json"
                        if is_safe_regular_file(gpu_txt) and is_safe_regular_file(gpu_json):
                            success_provider = "gpu"
                        else:
                            self._log("GPU whisper succeeded exit code but outputs were missing; falling back to CPU...")
                    else:
                        if self._cancel_requested:
                            raise InterruptedError("Transcription cancelled by operator")
                        self._log(f"GPU whisper returned code {ret}, falling back to CPU...")
                except (TimeoutError, Exception) as e:
                    if self._cancel_requested:
                        raise InterruptedError("Transcription cancelled by operator")
                    self._log(f"GPU whisper error ({e}), falling back to CPU...")

            if self._cancel_requested:
                raise InterruptedError("Transcription cancelled by operator")

            if success_provider != "gpu":
                # Clean up any leftover partial GPU files before CPU run
                for f in staging_dir.glob(f"gpu_chunk_{chunk_index:03d}.*"):
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass

                cmd_cpu = [
                    "nice",
                    "-n",
                    cpu_nice,
                    w_bin,
                    "-ng",
                    "-m",
                    str(m_path),
                    "-l",
                    lang,
                    "-f",
                    str(chunk_file),
                    "-otxt",
                    "-oj",
                    "-of",
                    str(cpu_out_base),
                    "-t",
                    cpu_threads,
                    "-pp",
                ]
                ret, _, stderr = self._run_process_isolated(cmd_cpu, timeout=900)
                if ret != 0:
                    if self._cancel_requested:
                        raise InterruptedError("Transcription cancelled by operator")
                    raise RuntimeError(f"Whisper failed on both GPU and CPU: {stderr.strip()}")
                success_provider = "cpu"

            chosen_prefix = "gpu" if success_provider == "gpu" else "cpu"
            staged_txt = staging_dir / f"{chosen_prefix}_chunk_{chunk_index:03d}.txt"
            staged_json = staging_dir / f"{chosen_prefix}_chunk_{chunk_index:03d}.json"

            # R10: Strict requirement for BOTH text and JSON timestamp outputs
            if not is_safe_regular_file(staged_txt):
                raise RuntimeError(
                    f"Whisper completed with exit 0 but failed to produce text file for chunk {chunk_index}"
                )
            if not is_safe_regular_file(staged_json):
                raise RuntimeError(
                    f"Whisper completed with exit 0 but failed to produce required JSON timestamp output for chunk {chunk_index}"
                )

            text_content = staged_txt.read_text(encoding="utf-8")

            # Parse and strictly validate JSON schema and timestamps
            try:
                raw_json = staged_json.read_text(encoding="utf-8")
                data = json.loads(raw_json)
                if not isinstance(data, dict):
                    raise ValueError("JSON root must be an object")
                transcription = data.get("transcription")
                if not isinstance(transcription, list):
                    raise ValueError("JSON 'transcription' field must be a list")

                for item in transcription:
                    offsets = item.get("offsets", {})
                    from_ms = offsets.get("from")
                    to_ms = offsets.get("to")
                    if from_ms is None or to_ms is None:
                        raise ValueError("Missing 'from' or 'to' offset")
                    if to_ms < from_ms or from_ms < 0:
                        raise ValueError(f"Invalid timestamp interval: {from_ms} -> {to_ms}")
                    segments.append({
                        "chunk_index": chunk_index,
                        "from_sec": round(from_ms / 1000.0, 2),
                        "to_sec": round(to_ms / 1000.0, 2),
                        "text": str(item.get("text", "")).strip(),
                    })

                if text_content.strip() and not segments:
                    raise ValueError("Whisper produced non-empty text but transcription segments list is empty")
            except Exception as exc:
                raise RuntimeError(f"Whisper JSON output is invalid for chunk {chunk_index}: {exc}") from exc

            # Promote staged files to final destination atomically
            final_txt = transcripts_dir / f"chunk_{chunk_index:03d}.txt"
            final_json = transcripts_dir / f"chunk_{chunk_index:03d}.json"
            os.replace(staged_txt, final_txt)
            os.replace(staged_json, final_json)

            return text_content, segments

        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


TRANSCRIBE_MANAGER = TranscriptionManager()
