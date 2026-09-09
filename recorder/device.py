"""Audio device enumeration, non-intrusive preflight, and explicit test capture."""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from typing import Any

from .media_tools import resolve_ffmpeg_bin

DEVICE_INDEX_RE = re.compile(r"\[([0-9]+)\]\s*(.+)")
AUDIO_STREAM_RE = re.compile(r"Audio:\s*([a-zA-Z0-9_]+),\s*([0-9]+)\s*Hz,\s*([a-zA-Z0-9_]+)")
MAX_VOL_RE = re.compile(r"max_volume:\s*(-?[0-9.]+|-[iI]nf)\s*dB")
MEAN_VOL_RE = re.compile(r"mean_volume:\s*(-?[0-9.]+|-[iI]nf)\s*dB")


def list_audio_devices(ffmpeg_bin: str = "ffmpeg") -> tuple[list[dict[str, Any]], str]:
    """List avfoundation audio input devices via read-only device listing. Returns (devices, raw_stderr)."""
    try:
        resolved = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError:
        resolved = None
    if not resolved:
        return [], "ffmpeg binary not found"

    try:
        proc = subprocess.run(
            [resolved, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = proc.stderr
    except Exception as exc:
        return [], str(exc)

    devices: list[dict[str, Any]] = []
    in_audio = False
    for line in output.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if "AVFoundation video devices" in line:
            in_audio = False
            continue

        if in_audio:
            match = re.search(r"\[([0-9]+)\]\s*(.+)", line)
            if match:
                idx = int(match.group(1))
                name = match.group(2).strip()
                kind = "mic"
                lower_name = name.lower()
                if "blackhole" in lower_name:
                    kind = "blackhole"
                elif any(k in lower_name for k in ("macbook", "internal", "built-in")):
                    kind = "builtin_mic"
                elif any(k in lower_name for k in ("usb", "external", "airpods", "headphone")):
                    kind = "external_mic"

                devices.append({
                    "index": idx,
                    "name": name,
                    "kind": kind,
                })

    return devices, output


def find_device(
    kind: str = "blackhole",
    index: int | None = None,
    ffmpeg_bin: str = "ffmpeg",
    devices: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Find audio device by explicit index or preferred kind."""
    if devices is None:
        devices, _ = list_audio_devices(ffmpeg_bin=ffmpeg_bin)
    if not devices:
        return None

    if index is not None:
        for dev in devices:
            if dev["index"] == index:
                return dev
        return None

    if kind in ("blackhole", "zoom"):
        for dev in devices:
            if dev["kind"] == "blackhole":
                return dev
        return None

    if kind == "mic":
        # Prefer external mic, then built-in mic, then first non-blackhole device
        for dev in devices:
            if dev["kind"] == "external_mic":
                return dev
        for dev in devices:
            if dev["kind"] == "builtin_mic":
                return dev
        for dev in devices:
            if dev["kind"] != "blackhole":
                return dev
        return None

    if kind is None:
        return devices[0]

    return None


def probe_device_stream_info(device_index: int, ffmpeg_bin: str = "ffmpeg", timeout: float = 4.0) -> dict[str, Any]:
    """Probe the native audio sample rate, channels, and format by testing device stream.
    Executed ONLY on explicit test or capture initialization, NEVER during read-only preflight.
    """
    try:
        resolved = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError:
        resolved = None
    if not resolved:
        return {
            "sample_rate": None,
            "channels": None,
            "format": None,
            "probed": False,
            "error": "ffmpeg not found",
        }

    try:
        proc = subprocess.run(
            [
                resolved,
                "-hide_banner",
                "-f",
                "avfoundation",
                "-i",
                f":{device_index}",
                "-t",
                "0.2",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stderr = proc.stderr
    except Exception as exc:
        return {
            "sample_rate": None,
            "channels": None,
            "format": None,
            "probed": False,
            "error": str(exc),
        }

    sample_rate = None
    channels = None
    fmt = None
    probed = False

    match = AUDIO_STREAM_RE.search(stderr)
    if match:
        fmt = match.group(1)
        sample_rate = int(match.group(2))
        chan_str = match.group(3)
        if "mono" in chan_str:
            channels = 1
        elif "stereo" in chan_str:
            channels = 2
        probed = True

    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "format": fmt,
        "probed": probed,
    }


def parse_volume_level(val_str: str | None) -> float:
    """Safely parse decibel string including -inf into float."""
    if not val_str:
        return -math.inf
    cleaned = val_str.strip().lower()
    if "-inf" in cleaned or cleaned == "-inf":
        return -math.inf
    try:
        return float(cleaned)
    except ValueError:
        return -math.inf


def run_volume_check(device_index: int, duration_sec: float = 3.0, ffmpeg_bin: str = "ffmpeg") -> dict[str, Any]:
    """Run an audio level test on the specified device. Only executed on explicit user action."""
    try:
        resolved = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError:
        resolved = None
    if not resolved:
        return {
            "max_volume_db": -math.inf,
            "mean_volume_db": -math.inf,
            "status": "error",
            "message": f"{ffmpeg_bin} binary not found",
        }

    try:
        proc = subprocess.run(
            [
                resolved,
                "-hide_banner",
                "-f",
                "avfoundation",
                "-i",
                f":{device_index}",
                "-t",
                str(duration_sec),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=duration_sec + 4.0,
        )
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        return {
            "max_volume_db": -math.inf,
            "mean_volume_db": -math.inf,
            "status": "error",
            "message": "Volume check timed out",
        }
    except Exception as exc:
        return {
            "max_volume_db": -math.inf,
            "mean_volume_db": -math.inf,
            "status": "error",
            "message": str(exc),
        }

    max_match = MAX_VOL_RE.search(stderr)
    mean_match = MEAN_VOL_RE.search(stderr)

    max_vol = parse_volume_level(max_match.group(1) if max_match else None)
    mean_vol = parse_volume_level(mean_match.group(1) if mean_match else None)

    if max_vol == -math.inf or max_vol < -80.0:
        return {
            "max_volume_db": max_vol if max_vol != -math.inf else -99.0,
            "mean_volume_db": mean_vol if mean_vol != -math.inf else -99.0,
            "status": "silence",
            "message": "Silence detected. Audio signal is not reaching the device.",
        }

    return {
        "max_volume_db": round(max_vol, 1),
        "mean_volume_db": round(mean_vol, 1),
        "status": "ok",
        "message": f"Sound detected (peak {max_vol:.1f} dB).",
    }


def preflight_audio(
    kind: str = "blackhole",
    index: int | None = None,
    test_volume: bool = False,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Perform preflight check.
    If test_volume is False: STRICTLY READ-ONLY (only device list enumeration).
    Does NOT capture sound, does NOT probe stream, leaves rate and capture permissions unknown.
    """
    devices, raw_output = list_audio_devices(ffmpeg_bin=ffmpeg_bin)
    device = find_device(kind=kind, index=index, ffmpeg_bin=ffmpeg_bin)

    if not devices:
        has_perm_error = "Input/output error" in raw_output or "permission" in raw_output.lower()
        return {
            "ready": False,
            "devices": [],
            "selected_device": None,
            "permissions": "denied" if has_perm_error else "unknown",
            "native_stream": {"sample_rate": None, "channels": None, "format": None, "probed": False},
            "volume_check": None,
            "error": "No audio input devices found. Check macOS Microphone permissions in System Settings.",
        }

    if not device:
        return {
            "ready": False,
            "devices": devices,
            "selected_device": None,
            "permissions": "enumerated_capture_untested",
            "native_stream": {"sample_rate": None, "channels": None, "format": None, "probed": False},
            "volume_check": None,
            "error": f"Audio device of kind {kind!r} (index {index}) not found in available devices.",
        }

    # READ-ONLY PREFLIGHT (No capture!):
    if not test_volume:
        return {
            "ready": True,
            "devices": devices,
            "selected_device": device,
            "permissions": "enumerated_capture_untested",
            "native_stream": {"sample_rate": None, "channels": None, "format": None, "probed": False},
            "volume_check": None,
            "error": None,
        }

    # EXPLICIT TEST ACTION (only when test_volume=True):
    stream_info = probe_device_stream_info(device["index"], ffmpeg_bin=ffmpeg_bin)
    vol_check = run_volume_check(device["index"], duration_sec=2.0, ffmpeg_bin=ffmpeg_bin)

    perm_status = "granted" if vol_check["status"] != "error" else "error"
    is_ready = vol_check["status"] == "ok"

    return {
        "ready": is_ready,
        "devices": devices,
        "selected_device": device,
        "permissions": perm_status,
        "native_stream": stream_info,
        "volume_check": vol_check,
        "error": None if is_ready else vol_check.get("message"),
    }
