"""Export transcript into multiple standard formats: TXT, Markdown, SRT, VTT, JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diarization_merge import format_speaker_name
from .session import SessionManifest, load_session, save_session
from .storage import (
    OUT_DIR,
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_read_text,
)


def format_srt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS,mmm for SubRip SRT with integer millisecond arithmetic."""
    total_ms = max(0, int(round(seconds * 1000.0)))
    millis = total_ms % 1000
    total_secs = total_ms // 1000
    secs = total_secs % 60
    mins = (total_secs // 60) % 60
    hrs = total_secs // 3600
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def format_vtt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS.mmm for WebVTT with integer millisecond arithmetic."""
    total_ms = max(0, int(round(seconds * 1000.0)))
    millis = total_ms % 1000
    total_secs = total_ms // 1000
    secs = total_secs % 60
    mins = (total_secs // 60) % 60
    hrs = total_secs // 3600
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def export_srt(segments: list[dict[str, Any]], speakers: dict[str, Any] | None = None) -> str:
    """Generate SRT subtitle text from segments with optional speaker prefix."""
    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        start = format_srt_time(seg.get("from_sec", 0.0))
        end = format_srt_time(seg.get("to_sec", 0.0))
        text = seg.get("text", "").strip()
        spk_id = seg.get("speaker_id")
        spk_name = format_speaker_name(spk_id, speakers) if spk_id and spk_id != "speaker_unknown" else None

        prefix = f"{spk_name}: " if spk_name else ""
        if seg.get("overlap"):
            prefix = f"[Наложение] {prefix}"

        lines.append(f"{idx}\n{start} --> {end}\n{prefix}{text}\n")
    return "\n".join(lines)


def export_vtt(segments: list[dict[str, Any]], speakers: dict[str, Any] | None = None) -> str:
    """Generate WebVTT subtitle text from segments with optional <v Voice> tags."""
    lines: list[str] = ["WEBVTT\n"]
    for idx, seg in enumerate(segments, start=1):
        start = format_vtt_time(seg.get("from_sec", 0.0))
        end = format_vtt_time(seg.get("to_sec", 0.0))
        text = seg.get("text", "").strip()
        spk_id = seg.get("speaker_id")
        spk_name = format_speaker_name(spk_id, speakers) if spk_id and spk_id != "speaker_unknown" else None

        if spk_name:
            line_body = f"<v {spk_name}>{text}</v>"
        else:
            line_body = text
        if seg.get("overlap"):
            line_body = f"[Наложение] {line_body}"

        lines.append(f"{idx}\n{start} --> {end}\n{line_body}\n")
    return "\n".join(lines)


def export_markdown(
    manifest: SessionManifest,
    segments: list[dict[str, Any]],
    full_text: str,
    speakers: dict[str, Any] | None = None,
) -> str:
    """Generate clean Markdown document with headers, metadata, and timestamped speech blocks."""
    lines: list[str] = [
        f"# Транскрипт: {manifest.title}",
        "",
        f"- **Сессия**: `{manifest.session_id}`",
        f"- **Дата создания**: {manifest.created_at}",
        f"- **Длительность**: {manifest.total_duration_sec} сек",
        f"- **Слов**: {manifest.word_count}",
        f"- **Язык**: {manifest.language}",
        "",
        "---",
        "",
    ]

    has_speakers = any(seg.get("speaker_id") and seg.get("speaker_id") != "speaker_unknown" for seg in segments)

    if segments:
        for seg in segments:
            t_start = format_vtt_time(seg.get("from_sec", 0.0))
            text = seg.get("text", "").strip()
            spk_id = seg.get("speaker_id")
            spk_name = format_speaker_name(spk_id, speakers) if spk_id and spk_id != "speaker_unknown" else None
            overlap_badge = " *(наложение речи)*" if seg.get("overlap") else ""

            if has_speakers and spk_name:
                lines.append(f"### [{t_start}] {spk_name}{overlap_badge}\n\n{text}\n")
            elif spk_name:
                lines.append(f"> `[{t_start}]` **{spk_name}**: {text}{overlap_badge}\n")
            else:
                lines.append(f"> `[{t_start}]` {text}{overlap_badge}\n")
    else:
        lines.append(full_text)

    return "\n".join(lines)


def export_txt(
    segments: list[dict[str, Any]],
    full_text: str,
    speakers: dict[str, Any] | None = None,
) -> str:
    """Generate structured plain text transcript with speaker names if available."""
    if not segments:
        return full_text

    has_speakers = any(seg.get("speaker_id") and seg.get("speaker_id") != "speaker_unknown" for seg in segments)
    if not has_speakers:
        return full_text

    lines: list[str] = []
    for seg in segments:
        t_start = format_vtt_time(seg.get("from_sec", 0.0))
        text = seg.get("text", "").strip()
        spk_id = seg.get("speaker_id")
        spk_name = format_speaker_name(spk_id, speakers) if spk_id and spk_id != "speaker_unknown" else None

        prefix = f"[{t_start}] {spk_name}: " if spk_name else f"[{t_start}] "
        if seg.get("overlap"):
            prefix = f"[Наложение] {prefix}"
        lines.append(f"{prefix}{text}")

    return "\n\n".join(lines) + "\n"


def generate_all_exports(session_id: str) -> dict[str, str]:
    """Generate all supported export files for a session and save them atomically."""
    session_dir = get_session_dir(session_id)
    manifest = load_session(session_id)
    if not manifest:
        raise StorageError(f"Session {session_id} not found")

    transcript_txt = session_dir / "transcript.txt"
    full_text = ""
    if is_safe_regular_file(transcript_txt):
        full_text = safe_read_text(transcript_txt, root=OUT_DIR)

    segments: list[dict[str, Any]] = []
    speakers: dict[str, Any] = getattr(manifest, "speakers", {}) or {}

    transcript_json = session_dir / "transcript.json"
    if is_safe_regular_file(transcript_json):
        try:
            payload = json.loads(safe_read_text(transcript_json, root=OUT_DIR))
            segments = payload.get("segments", [])
            if not speakers and payload.get("diarization", {}).get("speakers"):
                speakers = payload["diarization"]["speakers"]
        except Exception:
            pass

    if not full_text:
        if segments:
            full_text = " ".join(seg.get("text", "").strip() for seg in segments if seg.get("text"))
        else:
            raise StorageError(f"No transcript.txt found for {session_id}")

    # Generate files
    srt_content = export_srt(segments, speakers=speakers)
    vtt_content = export_vtt(segments, speakers=speakers)
    md_content = export_markdown(manifest, segments, full_text, speakers=speakers)
    structured_txt = export_txt(segments, full_text, speakers=speakers)

    atomic_write_text(session_dir / "transcript.srt", srt_content)
    atomic_write_text(session_dir / "transcript.vtt", vtt_content)
    atomic_write_text(session_dir / "transcript.md", md_content)
    if structured_txt != full_text:
        atomic_write_text(session_dir / "transcript.txt", structured_txt)

    return {
        "txt": str(transcript_txt),
        "srt": str(session_dir / "transcript.srt"),
        "vtt": str(session_dir / "transcript.vtt"),
        "md": str(session_dir / "transcript.md"),
        "json": str(transcript_json),
    }


def update_speaker_names(session_id: str, new_speakers: dict[str, Any]) -> dict[str, Any]:
    """Update speaker display names in session manifest and transcript.json, and re-export all files."""
    session_dir = get_session_dir(session_id)
    manifest = load_session(session_id)
    if not manifest:
        raise StorageError(f"Session {session_id} not found")

    current_speakers = dict(getattr(manifest, "speakers", {}) or {})

    for spk_id, val in new_speakers.items():
        if isinstance(val, str):
            disp = val.strip()
            if spk_id not in current_speakers:
                current_speakers[spk_id] = {"display_name": disp}
            else:
                if isinstance(current_speakers[spk_id], dict):
                    current_speakers[spk_id]["display_name"] = disp
                else:
                    current_speakers[spk_id] = {"display_name": disp}
        elif isinstance(val, dict):
            if spk_id not in current_speakers:
                current_speakers[spk_id] = val
            elif isinstance(current_speakers[spk_id], dict):
                current_speakers[spk_id].update(val)

    manifest.speakers = current_speakers
    save_session(manifest)

    # Update transcript.json if present
    transcript_json = session_dir / "transcript.json"
    if is_safe_regular_file(transcript_json):
        try:
            payload = json.loads(safe_read_text(transcript_json, root=OUT_DIR))
            if "diarization" in payload and isinstance(payload["diarization"], dict):
                payload["diarization"]["speakers"] = current_speakers
            else:
                payload["diarization"] = {"speakers": current_speakers}
            atomic_write_text(transcript_json, json.dumps(payload, indent=2, ensure_ascii=False))
        except Exception:
            pass

    # Atomic re-export of all files
    exports = generate_all_exports(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "speakers": current_speakers,
        "exports": exports,
    }
