"""Alignment and merge algorithm for combining Whisper transcription segments with diarization turns."""
from __future__ import annotations

from typing import Any


def merge_diarization_with_segments(
    segments: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    speakers: dict[str, dict[str, Any]] | None = None,
    min_overlap_ratio: float = 0.20,
    min_overlap_speech_sec: float = 0.30,
) -> list[dict[str, Any]]:
    """Merge diarization speaker turns into Whisper ASR segments based on maximal temporal overlap.

    Args:
        segments: List of Whisper segments with 'from_sec', 'to_sec', 'text'.
        turns: List of diarization turns with 'from_sec', 'to_sec', 'speaker_id'.
        speakers: Optional dictionary mapping speaker_id to speaker metadata.
        min_overlap_ratio: Minimum ratio of segment duration required to assign speaker.
        min_overlap_speech_sec: Threshold in seconds to flag simultaneous overlapping speech.

    Returns:
        New list of segments with 'speaker_id' and 'overlap' fields attached.
    """
    if not segments:
        return []

    # Sort turns by start time for determinism
    sorted_turns = sorted(turns, key=lambda t: (float(t.get("from_sec", 0.0)), float(t.get("to_sec", 0.0))))

    merged: list[dict[str, Any]] = []

    for seg in segments:
        seg_copy = dict(seg)
        s_from = float(seg.get("from_sec", 0.0))
        s_to = float(seg.get("to_sec", 0.0))
        dur = max(0.0, s_to - s_from)

        if dur <= 0.0 or not sorted_turns:
            seg_copy["speaker_id"] = "speaker_unknown"
            seg_copy["speaker"] = "speaker_unknown"
            seg_copy["overlap"] = False
            merged.append(seg_copy)
            continue

        # Accumulate overlap per speaker
        speaker_durations: dict[str, float] = {}
        turn_has_overlap_flag = False

        for t in sorted_turns:
            t_from = float(t.get("from_sec", 0.0))
            t_to = float(t.get("to_sec", 0.0))

            # Early break if turn starts after segment ends
            if t_from >= s_to:
                break
            # Skip if turn ends before segment starts
            if t_to <= s_from:
                continue

            # Compute intersection
            overlap_start = max(s_from, t_from)
            overlap_end = min(s_to, t_to)
            intersection = max(0.0, overlap_end - overlap_start)

            if intersection > 0.0:
                spk = str(t.get("speaker_id", "speaker_unknown"))
                speaker_durations[spk] = speaker_durations.get(spk, 0.0) + intersection
                if bool(t.get("overlap", False)) and intersection >= min_overlap_speech_sec:
                    turn_has_overlap_flag = True

        if not speaker_durations:
            seg_copy["speaker_id"] = "speaker_unknown"
            seg_copy["speaker"] = "speaker_unknown"
            seg_copy["overlap"] = False
            merged.append(seg_copy)
            continue

        # Find speaker with maximum overlap
        best_spk, max_overlap = max(speaker_durations.items(), key=lambda item: item[1])

        # Verify minimum coverage threshold
        if max_overlap < (dur * min_overlap_ratio):
            seg_copy["speaker_id"] = "speaker_unknown"
            seg_copy["speaker"] = "speaker_unknown"
        else:
            seg_copy["speaker_id"] = best_spk
            seg_copy["speaker"] = best_spk

        # Detect overlapping speech: >= 2 speakers with significant speech inside segment
        significant_speakers = [s for s, d in speaker_durations.items() if d >= min_overlap_speech_sec and s != "speaker_unknown"]
        is_overlapping = len(significant_speakers) >= 2 or turn_has_overlap_flag

        seg_copy["overlap"] = is_overlapping
        merged.append(seg_copy)

    return merged


def format_speaker_name(speaker_id: str | None, speakers: dict[str, Any] | None = None) -> str:
    """Resolve human-readable display name for a speaker_id from metadata mapping."""
    if not speaker_id or speaker_id == "speaker_unknown":
        return "Неизвестный"

    if speakers and speaker_id in speakers:
        meta = speakers[speaker_id]
        if isinstance(meta, dict) and meta.get("display_name"):
            return str(meta["display_name"]).strip()
        if isinstance(meta, str) and meta.strip():
            return meta.strip()

    # Default localized speaker naming (speaker_01 -> Говорящий 1, speaker_02 -> Говорящий 2)
    if speaker_id.startswith("speaker_"):
        suffix = speaker_id[len("speaker_"):]
        if suffix.isdigit():
            num = int(suffix)
            if num == 0:
                num = 1
            return f"Говорящий {num}"

    return speaker_id
