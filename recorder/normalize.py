"""Offline audio normalization with pre-promotion frame count and duration validation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable

from .capture import stop_and_reap_process_group, validate_and_probe_wav
from .constants import (
    DEFAULT_SEGMENT_TIME_SEC,
    SOURCE_UPLOAD,
    STATE_FAILED,
    STATE_READY,
    TARGET_WHISPER_CHANNELS,
    TARGET_WHISPER_RATE,
)
from .guardian import run_guarded_command
from .lock import GLOBAL_LOCK
from .media_tools import resolve_ffmpeg_bin, resolve_ffprobe_bin
from .session import SessionManifest, create_session, load_session, save_session, session_exists
from .storage import (
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_make_dir,
    safe_read_text,
)

SAFE_PROTOCOLS = "file,pipe"
SAFE_FORMATS = "wav,mp3,mov,mp4,m4a,aac,flac,ogg,matroska"


def _run_ffmpeg_command(
    cmd: list[str],
    timeout_sec: float,
    cancel_event: threading.Event | None = None,
    on_proc_start: Any = None,
    session_id: str | None = None,
) -> tuple[int, str, str]:
    """Run ffmpeg under guardian supervision with kernel flock inheritance,
    continuous stream draining to prevent pipe deadlocks, and responsive cancellation (F01, R01, R02).
    """
    if cancel_event and cancel_event.is_set():
        raise InterruptedError("Operation was cancelled by operator")

    # Execute under guardian supervision (F01)
    lock_fd = GLOBAL_LOCK.prepare_inheritable_fd()
    sess_id = session_id or "normalize"
    token = uuid.uuid4().hex[:8]
    retcode, stdout, stderr = run_guarded_command(
        cmd,
        action="normalize",
        session_id=sess_id,
        token=token,
        cwd=Path.cwd(),
        timeout_sec=timeout_sec,
        cancel_event=cancel_event,
        on_proc_start=on_proc_start,
        lock_fd=lock_fd,
    )
    if cancel_event and cancel_event.is_set():
        raise InterruptedError("Operation was cancelled by operator")
    return retcode, stdout, stderr


def get_audio_metadata(file_path: Path, ffprobe_bin: str = "ffprobe") -> dict[str, Any]:
    """Extract audio duration, sample rate, and channels using verified WAV probe with ffprobe fallback.
    Corrupted .wav files are strictly rejected without falling back to ffprobe.
    """
    if file_path.suffix.lower() == ".wav":
        wav_info = validate_and_probe_wav(file_path)
        if wav_info is not None:
            return wav_info
        return {"duration_sec": 0.0, "sample_rate": 0, "channels": 0, "frames": 0}

    # ffprobe fallback strictly for non-WAV media containers (e.g. mp3, m4a, mp4)
    try:
        resolved_probe = resolve_ffprobe_bin(ffprobe_bin)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"ffprobe binary not found ('{ffprobe_bin}'). "
            f"ffprobe is required to probe metadata for {file_path.suffix or 'non-WAV'} media."
        ) from exc

    if not resolved_probe:
        raise FileNotFoundError(
            f"ffprobe binary not found ('{ffprobe_bin}'). "
            f"ffprobe is required to probe metadata for {file_path.suffix or 'non-WAV'} media."
        )

    try:
        res = subprocess.run(
            [
                resolved_probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=sample_rate,channels",
                "-of",
                "default=noprint_wrappers=1",
                "-protocol_whitelist",
                SAFE_PROTOCOLS,
                "-format_whitelist",
                SAFE_FORMATS,
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = res.stdout
        dur = 0.0
        rate = 0
        channels = 1
        for line in out.splitlines():
            if line.startswith("duration="):
                try:
                    dur = float(line.split("=")[1])
                except ValueError:
                    pass
            elif line.startswith("sample_rate="):
                try:
                    rate = int(line.split("=")[1])
                except ValueError:
                    pass
            elif line.startswith("channels="):
                try:
                    channels = int(line.split("=")[1])
                except ValueError:
                    pass
        if dur > 0:
            return {
                "duration_sec": dur,
                "sample_rate": rate,
                "channels": channels,
                "frames": int(dur * rate) if rate else 0,
            }
    except Exception:
        pass

    return {"duration_sec": 0.0, "sample_rate": 0, "channels": 0, "frames": 0}


def normalize_chunk(
    raw_path: Path,
    out_path: Path,
    ffmpeg_bin: str = "ffmpeg",
    log_fn: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_proc_start: Any = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Convert raw WAV chunk into 16 kHz 16-bit mono PCM with atomic promotion and frame validation."""
    if not is_safe_regular_file(raw_path):
        raise StorageError(f"Raw chunk {raw_path} is missing or unsafe")

    raw_bytes = raw_path.read_bytes()
    source_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]

    raw_meta = validate_and_probe_wav(raw_path)
    if raw_meta is None or raw_meta["duration_sec"] <= 0.0:
        raise RuntimeError(f"Source raw chunk {raw_path.name} is corrupted or truncated")
    raw_dur = raw_meta["duration_sec"]

    norm_dir = out_path.parent
    safe_make_dir(norm_dir)
    meta_path = norm_dir / f"{out_path.stem}.meta.json"

    # Check cache bound to exact source hash and physical format
    if out_path.exists() and meta_path.exists():
        try:
            meta_json = json.loads(safe_read_text(meta_path))
            if meta_json.get("source_hash") == source_hash:
                cached_wav = validate_and_probe_wav(out_path)
                if (
                    cached_wav is not None
                    and cached_wav["sample_rate"] == TARGET_WHISPER_RATE
                    and cached_wav["channels"] == TARGET_WHISPER_CHANNELS
                    and cached_wav.get("bits_per_sample") == 16
                    and abs(cached_wav["duration_sec"] - raw_dur) <= 0.05
                ):
                    return {
                        "filename": out_path.name,
                        "duration_sec": cached_wav["duration_sec"],
                        "sample_rate": cached_wav["sample_rate"],
                        "channels": cached_wav["channels"],
                        "frames": cached_wav.get("frames", int(cached_wav["duration_sec"] * TARGET_WHISPER_RATE)),
                        "source_hash": source_hash,
                        "size_bytes": out_path.stat().st_size,
                    }
        except Exception:
            pass

    staging_name = f".staging_{uuid.uuid4().hex[:8]}_{out_path.name}"
    temp_out = norm_dir / staging_name

    try:
        resolved_ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}').") from exc
    if not resolved_ffmpeg:
        raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}').")

    cmd = [
        resolved_ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-protocol_whitelist",
        SAFE_PROTOCOLS,
        "-format_whitelist",
        SAFE_FORMATS,
        "-i",
        str(raw_path),
        "-vn",
        "-ac",
        str(TARGET_WHISPER_CHANNELS),
        "-ar",
        str(TARGET_WHISPER_RATE),
        "-c:a",
        "pcm_s16le",
        str(temp_out),
    ]

    if log_fn:
        log_fn(f"Normalizing {raw_path.name} ({raw_dur:.3f}s) -> 16kHz mono {out_path.name}")

    try:
        ret, stdout, stderr = _run_ffmpeg_command(
            cmd,
            timeout_sec=180.0,
            cancel_event=cancel_event,
            on_proc_start=on_proc_start,
            session_id=session_id,
        )
    except Exception:
        temp_out.unlink(missing_ok=True)
        raise

    if ret != 0:
        temp_out.unlink(missing_ok=True)
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Operation was cancelled by operator")
        raise RuntimeError(f"Ffmpeg normalization failed for {raw_path.name}: {stderr.strip()}")

    # STRICT PRE-PROMOTION VERIFICATION:
    norm_meta = validate_and_probe_wav(temp_out)
    if norm_meta is None or norm_meta["duration_sec"] <= 0.0:
        temp_out.unlink(missing_ok=True)
        raise RuntimeError(f"Normalized chunk {out_path.name} produced zero or invalid duration")

    # Verify 16kHz, mono, and 16-bit PCM properties
    if norm_meta["sample_rate"] != TARGET_WHISPER_RATE:
        temp_out.unlink(missing_ok=True)
        raise RuntimeError(
            f"Normalized chunk sample rate mismatch: expected {TARGET_WHISPER_RATE}, got {norm_meta['sample_rate']}"
        )
    if norm_meta["channels"] != TARGET_WHISPER_CHANNELS:
        temp_out.unlink(missing_ok=True)
        raise RuntimeError(
            f"Normalized chunk channel count mismatch: expected {TARGET_WHISPER_CHANNELS}, got {norm_meta['channels']}"
        )
    if norm_meta.get("bits_per_sample") != 16:
        temp_out.unlink(missing_ok=True)
        raise RuntimeError(
            f"Normalized chunk sample width mismatch: expected 16-bit, got {norm_meta.get('bits_per_sample')}-bit"
        )

    norm_dur = norm_meta["duration_sec"]
    expected_frames = int(raw_dur * TARGET_WHISPER_RATE)
    frame_drift = abs(norm_meta["frames"] - expected_frames)
    max_frame_drift = int(0.05 * TARGET_WHISPER_RATE)  # 800 frames = 50ms

    if frame_drift > max_frame_drift:
        temp_out.unlink(missing_ok=True)
        raise RuntimeError(
            f"Duration mismatch for {raw_path.name}: raw={raw_dur:.3f}s ({expected_frames} frames), "
            f"normalized={norm_dur:.3f}s ({norm_meta['frames']} frames, drift={frame_drift} frames)"
        )

    if not is_safe_regular_file(temp_out):
        temp_out.unlink(missing_ok=True)
        raise StorageError("Staged normalization file failed security check")

    # Atomic promotion
    os.replace(temp_out, out_path)

    # Save cache metadata bound to source digest
    atomic_write_text(
        meta_path,
        json.dumps({
            "source_hash": source_hash,
            "duration_sec": norm_dur,
            "sample_rate": norm_meta["sample_rate"],
            "channels": norm_meta["channels"],
            "frames": norm_meta["frames"],
        }, indent=2),
    )

    return {
        "filename": out_path.name,
        "duration_sec": round(norm_dur, 3),
        "sample_rate": norm_meta["sample_rate"],
        "channels": norm_meta["channels"],
        "frames": norm_meta["frames"],
        "source_hash": source_hash,
        "size_bytes": out_path.stat().st_size,
    }


def normalize_session_raw_chunks(
    manifest: SessionManifest,
    ffmpeg_bin: str = "ffmpeg",
    log_fn: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_proc_start: Any = None,
) -> SessionManifest:
    """Normalize all raw chunks in a session manifest into target 16 kHz mono WAVs."""
    session_dir = get_session_dir(manifest.session_id)
    raw_dir = session_dir / "raw"
    norm_dir = session_dir / "normalized"
    safe_make_dir(norm_dir)

    raw_filenames = [c["filename"] for c in manifest.raw_chunks if c.get("closed")]
    if not raw_filenames:
        return manifest

    normalized_records: list[dict[str, Any]] = []
    total_duration = 0.0
    total_frames = 0

    for idx, fn in enumerate(raw_filenames):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Normalization was cancelled by operator")

        raw_f = raw_dir / fn
        if not is_safe_regular_file(raw_f):
            raise StorageError(f"Declared source raw chunk missing or corrupt: {fn}")

        out_name = f"chunk_{idx:03d}.wav"
        out_path = norm_dir / out_name

        rec = normalize_chunk(
            raw_f,
            out_path,
            ffmpeg_bin=ffmpeg_bin,
            log_fn=log_fn,
            cancel_event=cancel_event,
            on_proc_start=on_proc_start,
            session_id=manifest.session_id,
        )
        rec["index"] = idx
        normalized_records.append(rec)
        total_frames += rec.get("frames", 0)
        total_duration += rec["duration_sec"]

        legacy_path = session_dir / out_name
        if not legacy_path.exists():
            try:
                shutil.copy2(out_path, legacy_path)
            except OSError:
                pass

    manifest.normalized_chunks = normalized_records
    if total_frames > 0:
        manifest.total_duration_sec = round(total_frames / TARGET_WHISPER_RATE, 3)
    else:
        manifest.total_duration_sec = round(total_duration, 3)
    save_session(manifest)
    return manifest


def import_media_file(
    file_path: Path,
    session_id: str,
    title: str | None = None,
    segment_time_sec: int = DEFAULT_SEGMENT_TIME_SEC,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    log_fn: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_proc_start: Any = None,
) -> SessionManifest:
    """Import an existing audio/video file into a fresh exclusive session, normalizing into 16 kHz mono chunks."""
    if not is_safe_regular_file(file_path):
        raise StorageError(f"Source media file is not a valid regular file: {file_path}")

    disallowed = {".m3u", ".m3u8", ".pls", ".asx", ".xspf", ".txt", ".json", ".xml", ".html", ".sh", ".py", ".bash"}
    if file_path.suffix.lower() in disallowed:
        raise StorageError(f"Playlist, text, or script formats are forbidden for import: {file_path.suffix}")

    # Fresh import requires exclusive new session
    if session_exists(session_id):
        raise StorageError(f"Session already exists: {session_id}")

    # Verify tool dependencies before doing any slicing
    try:
        resolved_ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}'). Required to slice and normalize media.") from exc
    if not resolved_ffmpeg:
        raise RuntimeError(f"FFmpeg binary not found ('{ffmpeg_bin}'). Required to slice and normalize media.")

    resolved_ffprobe: str | None = None
    if file_path.suffix.lower() != ".wav":
        try:
            resolved_ffprobe = resolve_ffprobe_bin(ffprobe_bin)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"ffprobe binary not found ('{ffprobe_bin}'). Required to inspect metadata for {file_path.suffix} files."
            ) from exc
        if not resolved_ffprobe:
            raise RuntimeError(
                f"ffprobe binary not found ('{ffprobe_bin}'). Required to inspect metadata for {file_path.suffix} files."
            )

    # Verify source media duration before doing any slicing
    try:
        source_meta = get_audio_metadata(file_path, ffprobe_bin=resolved_ffprobe or ffprobe_bin)
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc

    source_dur = source_meta.get("duration_sec", 0.0)
    if source_dur <= 0.0:
        raise RuntimeError(f"Source media file {file_path.name} has zero or unknown duration")

    manifest = create_session(session_id=session_id, title=title or file_path.stem, source_kind=SOURCE_UPLOAD)
    session_dir = get_session_dir(session_id, create_exclusive=False)
    norm_dir = session_dir / "normalized"
    safe_make_dir(norm_dir)

    staging_dir = session_dir / f".staging_import_{uuid.uuid4().hex[:8]}"
    safe_make_dir(staging_dir)

    if log_fn:
        log_fn(f"Importing {file_path.name} ({source_dur:.3f}s) into {session_id} as 16 kHz mono chunks...")

    pattern = str(staging_dir / "chunk_%03d.wav")
    cmd = [
        resolved_ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-protocol_whitelist",
        SAFE_PROTOCOLS,
        "-format_whitelist",
        SAFE_FORMATS,
        "-i",
        str(file_path),
        "-vn",
        "-ac",
        str(TARGET_WHISPER_CHANNELS),
        "-ar",
        str(TARGET_WHISPER_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_time",
        str(segment_time_sec),
        "-reset_timestamps",
        "1",
        pattern,
    ]

    try:
        ret, stdout, stderr = _run_ffmpeg_command(
            cmd,
            timeout_sec=600.0,
            cancel_event=cancel_event,
            on_proc_start=on_proc_start,
            session_id=session_id,
        )
    except InterruptedError:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = "Ffmpeg import cancelled by operator"
        save_session(manifest)
        raise
    except TimeoutError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = f"Ffmpeg import timed out: {exc}"
        save_session(manifest)
        raise
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = f"Ffmpeg import failed: {exc}"
        save_session(manifest)
        raise

    if ret != 0:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = f"Ffmpeg import failed: {stderr.strip()}"
        save_session(manifest)
        raise RuntimeError(manifest.error_message)

    staged_files = sorted(staging_dir.glob("chunk_*.wav"))
    if not staged_files:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = "Ffmpeg import produced no output chunks"
        save_session(manifest)
        raise RuntimeError(manifest.error_message)

    chunks: list[dict[str, Any]] = []
    total_dur = 0.0
    total_frames = 0

    for idx, f in enumerate(staged_files):
        wav_meta = validate_and_probe_wav(f)
        if (
            wav_meta is None
            or wav_meta["sample_rate"] != TARGET_WHISPER_RATE
            or wav_meta["channels"] != TARGET_WHISPER_CHANNELS
            or wav_meta.get("bits_per_sample") != 16
            or wav_meta["duration_sec"] <= 0.0
        ):
            shutil.rmtree(staging_dir, ignore_errors=True)
            manifest.status = STATE_FAILED
            manifest.error_message = f"Staged chunk {f.name} failed physical WAV validation"
            save_session(manifest)
            raise RuntimeError(manifest.error_message)

        dur = wav_meta["duration_sec"]
        total_dur += dur
        total_frames += wav_meta.get("frames", 0)
        chunks.append({
            "index": idx,
            "filename": f.name,
            "duration_sec": round(dur, 3),
            "sample_rate": TARGET_WHISPER_RATE,
            "channels": TARGET_WHISPER_CHANNELS,
            "frames": wav_meta.get("frames", 0),
            "size_bytes": f.stat().st_size,
        })

    # Validate total duration match against source media
    max_drift = max(0.5, 0.05 * len(chunks))
    if abs(total_dur - source_dur) > max_drift:
        shutil.rmtree(staging_dir, ignore_errors=True)
        manifest.status = STATE_FAILED
        manifest.error_message = (
            f"Import duration mismatch: source={source_dur:.3f}s, output={total_dur:.3f}s "
            f"(drift {abs(total_dur - source_dur):.3f}s exceeds limit {max_drift:.3f}s)"
        )
        save_session(manifest)
        raise RuntimeError(manifest.error_message)

    # Atomic promotion of all validated chunks
    for f in staged_files:
        dest = norm_dir / f.name
        os.replace(f, dest)
        legacy_dest = session_dir / f.name
        if not legacy_dest.exists():
            try:
                shutil.copy2(dest, legacy_dest)
            except OSError:
                pass

    shutil.rmtree(staging_dir, ignore_errors=True)

    manifest.normalized_chunks = chunks
    if total_frames > 0:
        manifest.total_duration_sec = round(total_frames / TARGET_WHISPER_RATE, 3)
    else:
        manifest.total_duration_sec = round(total_dur, 3)
    manifest.status = STATE_READY
    save_session(manifest)
    return manifest

import_user_media_file = import_media_file
