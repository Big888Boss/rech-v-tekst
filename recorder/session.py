"""Session model, atomic manifest persistence, and lifecycle transitions."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    ALL_STATES,
    OUT_DIR,
    SOURCE_MIC,
    SOURCE_UPLOAD,
    SOURCE_ZOOM,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_RECORDING,
)
from .storage import (
    StorageError,
    atomic_write_text,
    get_session_dir,
    safe_make_dir,
    validate_session_id,
)


@dataclass
class SessionManifest:
    """Durable state manifest stored as session.json in each session directory."""

    session_id: str
    title: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    status: str = STATE_READY
    source_kind: str = SOURCE_ZOOM
    device_info: dict[str, Any] = field(default_factory=dict)
    native_sample_rate: int | None = None
    native_channels: int | None = None
    raw_chunks: list[dict[str, Any]] = field(default_factory=list)
    normalized_chunks: list[dict[str, Any]] = field(default_factory=list)
    total_duration_sec: float = 0.0
    language: str = "ru"
    word_count: int = 0
    error_message: str | None = None
    pid: int | None = None
    operation_token: str | None = None
    processing_pid: int | None = None
    summary_status: str | None = None
    has_transcript: bool = False
    has_summary: bool = False
    in_queue: bool = True
    schema_version: int = 2
    diarization_status: str | None = None
    diarization_params: dict[str, Any] = field(default_factory=dict)
    speakers: dict[str, dict[str, Any]] = field(default_factory=dict)
    has_diarization: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionManifest:
        if "status" in data and data["status"] not in ALL_STATES:
            raw_status = str(data["status"])
            data["status"] = STATE_FAILED
            data["error_message"] = f"Invalid manifest status: {raw_status}"

        if "in_queue" in data:
            data["in_queue"] = bool(data["in_queue"])
        else:
            data["in_queue"] = True

        if "schema_version" not in data:
            data["schema_version"] = 1
        if "speakers" not in data or not isinstance(data.get("speakers"), dict):
            data["speakers"] = {}
        if "diarization_params" not in data or not isinstance(data.get("diarization_params"), dict):
            data["diarization_params"] = {}
        if "has_diarization" in data:
            data["has_diarization"] = bool(data["has_diarization"])
        else:
            data["has_diarization"] = False

        if not isinstance(data.get("raw_chunks"), list):
            data["raw_chunks"] = []
        if not isinstance(data.get("normalized_chunks"), list):
            data["normalized_chunks"] = []
        if not isinstance(data.get("device_info"), dict):
            data["device_info"] = {}
        if not isinstance(data.get("title"), str):
            data["title"] = str(data.get("session_id", "session"))
        if not data.get("session_id") or not isinstance(data.get("session_id"), str):
            data["session_id"] = "unknown_session"

        known_keys = set(cls.__annotations__.keys())
        filtered = {k: v for k, v in data.items() if k in known_keys}
        return cls(**filtered)


def get_manifest_path(session_id: str) -> Path:
    session_dir = get_session_dir(session_id, create_if_missing=False)
    return session_dir / "session.json"


def session_exists(session_id: str) -> bool:
    """Check if session directory already exists on disk."""
    try:
        valid_id = validate_session_id(session_id)
        s_dir = get_session_dir(valid_id, create_if_missing=False)
        return s_dir.exists()
    except Exception:
        return False


def save_session(manifest: SessionManifest) -> None:
    """Save session manifest to disk atomically."""
    manifest.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    session_dir = get_session_dir(manifest.session_id)
    manifest_path = session_dir / "session.json"
    content = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False)
    atomic_write_text(manifest_path, content)


def migrate_legacy_session(session_id: str) -> SessionManifest | None:
    """Safely migrate an unmanifested baseline legacy session folder into a standard SessionManifest."""
    from .capture import validate_and_probe_wav
    from .storage import is_safe_regular_file

    try:
        valid_id = validate_session_id(session_id)
        sess_dir = get_session_dir(valid_id, create_if_missing=False)
        if not sess_dir.exists() or not sess_dir.is_dir():
            return None

        manifest_path = sess_dir / "session.json"
        if is_safe_regular_file(manifest_path):
            return load_session(valid_id)

        # Look for existing audio files
        norm_dir = sess_dir / "normalized"
        raw_dir = sess_dir / "raw"

        norm_files = sorted(norm_dir.glob("chunk_*.wav")) if norm_dir.exists() else []
        if not norm_files:
            legacy_chunks = sorted(sess_dir.glob("chunk_*.wav"))
            if legacy_chunks:
                safe_make_dir(norm_dir)
                norm_files = []
                for lc in legacy_chunks:
                    dest = norm_dir / lc.name
                    if not dest.exists():
                        try:
                            shutil.copy2(lc, dest)
                        except OSError:
                            pass
                    if dest.exists():
                        norm_files.append(dest)
                    elif lc.exists():
                        norm_files.append(lc)

        raw_files = sorted(raw_dir.glob("raw_chunk_*.wav")) if raw_dir.exists() else []

        valid_norm_records = []
        total_dur = 0.0
        for idx, nf in enumerate(norm_files):
            wav_info = validate_and_probe_wav(nf)
            if wav_info:
                dur = wav_info["duration_sec"]
                total_dur += dur
                valid_norm_records.append({
                    "index": idx,
                    "filename": nf.name,
                    "duration_sec": dur,
                    "sample_rate": wav_info["sample_rate"],
                    "channels": wav_info["channels"],
                    "size_bytes": nf.stat().st_size,
                })

        valid_raw_records = []
        for idx, rf in enumerate(raw_files):
            wav_info = validate_and_probe_wav(rf)
            if wav_info:
                valid_raw_records.append({
                    "index": idx,
                    "filename": rf.name,
                    "duration_sec": wav_info["duration_sec"],
                    "sample_rate": wav_info["sample_rate"],
                    "channels": wav_info["channels"],
                    "closed": True,
                    "size_bytes": rf.stat().st_size,
                })

        t_file = sess_dir / "transcript.txt"
        s_file = sess_dir / "summary.md"
        has_transcript = is_safe_regular_file(t_file) and t_file.stat().st_size > 0
        has_summary = is_safe_regular_file(s_file) and s_file.stat().st_size > 0

        if has_transcript:
            status = STATE_COMPLETED
        elif valid_norm_records or valid_raw_records:
            status = STATE_READY
        else:
            status = STATE_INTERRUPTED

        manifest = SessionManifest(
            session_id=valid_id,
            title=valid_id,
            source_kind=SOURCE_MIC if not valid_raw_records else SOURCE_ZOOM,
            raw_chunks=valid_raw_records,
            normalized_chunks=valid_norm_records,
            total_duration_sec=round(total_dur, 3),
            status=status,
            has_transcript=has_transcript,
            has_summary=has_summary,
        )
        save_session(manifest)
        return manifest
    except Exception:
        return None


def load_session(session_id: str) -> SessionManifest | None:
    """Load session manifest from disk with strict identity and schema validation."""
    try:
        from .storage import is_safe_regular_file, safe_read_text

        valid_id = validate_session_id(session_id)
        manifest_path = get_manifest_path(valid_id)
        if not is_safe_regular_file(manifest_path):
            sess_dir = get_session_dir(valid_id, create_if_missing=False)
            if sess_dir.exists() and not manifest_path.exists():
                return migrate_legacy_session(valid_id)
            return None
        content = safe_read_text(manifest_path)
        data = json.loads(content)
        if not isinstance(data, dict):
            return None

        # Strict identity check: manifest session_id MUST match requested session_id
        if data.get("session_id") != valid_id:
            return None

        manifest = SessionManifest.from_dict(data)

        sess_dir = manifest_path.parent
        t_file = sess_dir / "transcript.txt"
        s_file = sess_dir / "summary.md"
        manifest.has_transcript = is_safe_regular_file(t_file) and t_file.stat().st_size > 0
        manifest.has_summary = is_safe_regular_file(s_file) and s_file.stat().st_size > 0

        t_json = sess_dir / "transcript.json"
        if is_safe_regular_file(t_json):
            try:
                t_data = json.loads(safe_read_text(t_json))
                if isinstance(t_data, dict) and t_data.get("diarization"):
                    manifest.has_diarization = True
                    if not manifest.diarization_status:
                        manifest.diarization_status = t_data["diarization"].get("status", "completed")
                    if not manifest.speakers and t_data["diarization"].get("speakers"):
                        manifest.speakers = t_data["diarization"]["speakers"]
            except Exception:
                pass

        return manifest
    except Exception:
        return None


def create_session(
    session_id: str,
    title: str | None = None,
    source_kind: str = SOURCE_ZOOM,
    device_info: dict[str, Any] | None = None,
    native_sample_rate: int | None = None,
    native_channels: int | None = None,
) -> SessionManifest:
    """Initialize a new session with EXCLUSIVE creation.
    Raises StorageError if session already exists, preventing overwrite.
    """
    valid_id = validate_session_id(session_id)
    if session_exists(valid_id):
        raise StorageError(f"Session already exists: {valid_id}")

    # create_exclusive=True will raise StorageError if directory already exists
    session_dir = get_session_dir(valid_id, create_exclusive=True)

    safe_make_dir(session_dir / "raw")
    safe_make_dir(session_dir / "normalized")
    safe_make_dir(session_dir / "transcripts")

    manifest = SessionManifest(
        session_id=valid_id,
        title=title or valid_id,
        source_kind=source_kind,
        device_info=device_info or {},
        native_sample_rate=native_sample_rate,
        native_channels=native_channels,
        status=STATE_READY,
    )
    save_session(manifest)
    return manifest


def retry_session(session_id: str) -> SessionManifest:
    """Explicitly retry a failed or interrupted session without overwriting raw chunks."""
    manifest = load_session(session_id)
    if not manifest:
        raise StorageError(f"Session not found: {session_id}")

    if manifest.status in (STATE_RECORDING, STATE_PROCESSING):
        raise StorageError(f"Cannot retry session while {manifest.status}")

    manifest.status = STATE_READY
    manifest.error_message = None
    save_session(manifest)
    return manifest


def remove_from_queue(session_id: str) -> SessionManifest:
    """Reversibly remove a session from the active processing queue.
    Preserves all audio files, chunks, transcripts, and summaries on disk."""
    valid_id = validate_session_id(session_id)
    manifest = load_session(valid_id)
    if not manifest:
        raise StorageError(f"Session not found: {valid_id}")

    if manifest.status in (STATE_RECORDING, STATE_PROCESSING):
        raise RuntimeError(f"Cannot remove session from queue while {manifest.status}")

    manifest.in_queue = False
    save_session(manifest)
    return manifest


def restore_to_queue(session_id: str) -> SessionManifest:
    """Restore a previously removed session back into the active processing queue."""
    valid_id = validate_session_id(session_id)
    manifest = load_session(valid_id)
    if not manifest:
        raise StorageError(f"Session not found: {valid_id}")

    if manifest.status in (STATE_RECORDING, STATE_PROCESSING):
        raise RuntimeError(f"Cannot restore session to queue while {manifest.status}")

    manifest.in_queue = True
    save_session(manifest)
    return manifest


def list_sessions(include_removed: bool = True) -> list[SessionManifest]:
    """List all sessions ordered by creation time (newest first)."""
    if not OUT_DIR.exists():
        return []

    sessions: list[SessionManifest] = []
    try:
        for entry in OUT_DIR.iterdir():
            if entry.is_dir() and entry.name != "uploads" and not entry.name.startswith("."):
                manifest = load_session(entry.name)
                if manifest:
                    sessions.append(manifest)
                else:
                    try:
                        valid_id = validate_session_id(entry.name)
                        sess_dir = OUT_DIR / valid_id
                        manifest_file = sess_dir / "session.json"

                        from .storage import is_safe_regular_file, safe_read_text

                        if manifest_file.exists():
                            # Corrupted or unsafe manifest file exists!
                            error_detail = "Corrupted session manifest"
                            if not is_safe_regular_file(manifest_file):
                                error_detail = "Manifest file is unsafe (symlink or hardlink detected)"
                            else:
                                try:
                                    raw_text = safe_read_text(manifest_file)
                                    parsed = json.loads(raw_text)
                                    if not isinstance(parsed, dict):
                                        error_detail = "Manifest JSON is not an object"
                                    elif parsed.get("session_id") != valid_id:
                                        error_detail = f"Manifest identity mismatch: expected {valid_id}, got {parsed.get('session_id')}"
                                except json.JSONDecodeError as jde:
                                    error_detail = f"Invalid manifest JSON: {jde}"
                                except Exception as exc:
                                    error_detail = f"Failed to parse manifest: {exc}"

                            synth = SessionManifest(
                                session_id=valid_id,
                                title=valid_id,
                                status=STATE_FAILED,
                                error_message=error_detail,
                                has_transcript=False,
                                has_summary=False,
                            )
                            sessions.append(synth)
                    except StorageError:
                        continue
    except OSError:
        pass

    if not include_removed:
        sessions = [s for s in sessions if getattr(s, "in_queue", True)]

    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions


def get_latest_session() -> SessionManifest | None:
    """Return the most recently created or updated session."""
    sessions = list_sessions()
    return sessions[0] if sessions else None
