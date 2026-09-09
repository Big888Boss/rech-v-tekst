"""Export transcript into multiple standard formats: TXT, Markdown, SRT, VTT, JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .session import SessionManifest, load_session
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


def export_srt(segments: list[dict[str, Any]]) -> str:
    """Generate SRT subtitle text from segments."""
    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        start = format_srt_time(seg.get("from_sec", 0.0))
        end = format_srt_time(seg.get("to_sec", 0.0))
        text = seg.get("text", "").strip()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def export_vtt(segments: list[dict[str, Any]]) -> str:
    """Generate WebVTT subtitle text from segments."""
    lines: list[str] = ["WEBVTT\n"]
    for idx, seg in enumerate(segments, start=1):
        start = format_vtt_time(seg.get("from_sec", 0.0))
        end = format_vtt_time(seg.get("to_sec", 0.0))
        text = seg.get("text", "").strip()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def export_markdown(manifest: SessionManifest, segments: list[dict[str, Any]], full_text: str) -> str:
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

    if segments:
        for seg in segments:
            t_start = format_vtt_time(seg.get("from_sec", 0.0))
            text = seg.get("text", "").strip()
            lines.append(f"> `[{t_start}]` {text}\n")
    else:
        lines.append(full_text)

    return "\n".join(lines)


def generate_all_exports(session_id: str) -> dict[str, str]:
    """Generate all supported export files for a session and save them atomically."""
    session_dir = get_session_dir(session_id)
    manifest = load_session(session_id)
    if not manifest:
        raise StorageError(f"Session {session_id} not found")

    transcript_txt = session_dir / "transcript.txt"
    if not is_safe_regular_file(transcript_txt):
        raise StorageError(f"No transcript.txt found for {session_id}")

    full_text = safe_read_text(transcript_txt, root=OUT_DIR)
    segments: list[dict[str, Any]] = []

    transcript_json = session_dir / "transcript.json"
    if is_safe_regular_file(transcript_json):
        try:
            payload = json.loads(safe_read_text(transcript_json, root=OUT_DIR))
            segments = payload.get("segments", [])
        except Exception:
            pass

    # Generate files
    srt_content = export_srt(segments)
    vtt_content = export_vtt(segments)
    md_content = export_markdown(manifest, segments, full_text)

    atomic_write_text(session_dir / "transcript.srt", srt_content)
    atomic_write_text(session_dir / "transcript.vtt", vtt_content)
    atomic_write_text(session_dir / "transcript.md", md_content)

    return {
        "txt": str(transcript_txt),
        "srt": str(session_dir / "transcript.srt"),
        "vtt": str(session_dir / "transcript.vtt"),
        "md": str(session_dir / "transcript.md"),
        "json": str(transcript_json),
    }
