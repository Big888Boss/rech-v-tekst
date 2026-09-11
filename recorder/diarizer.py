"""Core speaker diarization engine for long calls (>=8 hours) using sherpa-onnx.
Implements bounded sliding windowing, acoustic centroid registry via C-API,
atomic checkpoints for resumption, process isolation, and Whisper segment alignment.
"""
from __future__ import annotations

import array
import ctypes
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .constants import (
    BASE_DIR,
    DEFAULT_DIARIZATION_ALPHA,
    DEFAULT_DIARIZATION_CLUSTER_THRESHOLD,
    DEFAULT_DIARIZATION_MATCH_THRESHOLD,
    DEFAULT_DIARIZATION_NEW_THRESHOLD,
    DEFAULT_DIARIZATION_OVERLAP_SEC,
    DEFAULT_DIARIZATION_WINDOW_SEC,
    DIARIZATION_CANCELLED,
    DIARIZATION_COMPLETED,
    DIARIZATION_FAILED,
    DIARIZATION_IDLE,
    DIARIZATION_MODELS_DIR,
    DIARIZATION_RUNNING,
    ERES2NET_EMB_MODEL_NAME,
    OUT_DIR,
    PINNED_EMBEDDING_MODEL_SHA256,
    PINNED_SEGMENTATION_MODEL_SHA256,
    PINNED_SHERPA_ARM64_BINARY_SHA256,
    PYANNOTE_SEG_MODEL_NAME,
    SHERPA_BIN_NAME,
    SHERPA_LIB_NAME,
    STATE_COMPLETED,
    WORK_BIN_DIR,
    WORK_LIB_DIR,
)
from .diarization_merge import merge_diarization_with_segments
from .export import generate_all_exports
from .lock import GLOBAL_LOCK, LockBusyError
from .session import SessionManifest, load_session, save_session
from .storage import (
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_make_dir,
    safe_read_text,
    validate_session_id,
)

# Ctypes Structures for Sherpa-onnx C-API
class _SherpaEmbeddingConfig(ctypes.Structure):
    _fields_ = [
        ("model", ctypes.c_char_p),
        ("num_threads", ctypes.c_int32),
        ("debug", ctypes.c_int32),
        ("provider", ctypes.c_char_p),
    ]


_C_API_LOCK = threading.Lock()
_EXTRACTOR_CACHE: dict[str, tuple[Any, Any, int]] = {}


def resolve_diarizer_paths() -> dict[str, Path | None]:
    """Locate diarization binary, library, and models within approved project directories."""
    candidates_bin = [
        WORK_BIN_DIR / SHERPA_BIN_NAME,
        BASE_DIR / "work" / "diarization-spike" / "bin" / SHERPA_BIN_NAME,
    ]
    bin_path = next((p for p in candidates_bin if is_safe_regular_file(p) and os.access(p, os.X_OK)), None)

    candidates_lib = [
        WORK_LIB_DIR / SHERPA_LIB_NAME,
        BASE_DIR / "work" / "diarization-spike" / "lib" / SHERPA_LIB_NAME,
    ]
    lib_path = next((p for p in candidates_lib if is_safe_regular_file(p)), None)

    candidates_seg = [
        DIARIZATION_MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / PYANNOTE_SEG_MODEL_NAME,
        DIARIZATION_MODELS_DIR / PYANNOTE_SEG_MODEL_NAME,
        BASE_DIR / "work" / "diarization-spike" / "models" / "sherpa-onnx-pyannote-segmentation-3-0" / PYANNOTE_SEG_MODEL_NAME,
    ]
    seg_path = next((p for p in candidates_seg if is_safe_regular_file(p)), None)

    candidates_emb = [
        DIARIZATION_MODELS_DIR / ERES2NET_EMB_MODEL_NAME,
        BASE_DIR / "work" / "diarization-spike" / "models" / ERES2NET_EMB_MODEL_NAME,
    ]
    emb_path = next((p for p in candidates_emb if is_safe_regular_file(p)), None)

    return {
        "bin": bin_path,
        "lib": lib_path,
        "seg_model": seg_path,
        "emb_model": emb_path,
    }


def verify_diarizer_components(force_hash_check: bool = False) -> dict[str, Any]:
    """Verify availability and integrity of diarization components."""
    paths = resolve_diarizer_paths()
    missing: list[str] = []
    errors: list[str] = []

    if not paths["bin"]:
        missing.append("sherpa-onnx binary")
    if not paths["lib"]:
        missing.append("sherpa-onnx c-api library")
    if not paths["seg_model"]:
        missing.append("segmentation model")
    if not paths["emb_model"]:
        missing.append("embedding model")

    if missing:
        return {
            "ready": False,
            "missing": missing,
            "errors": [f"Отсутствуют компоненты: {', '.join(missing)}"],
            "paths": {k: str(v) if v else None for k, v in paths.items()},
        }

    # Optional cryptographic hash verification
    if force_hash_check:
        try:
            assert paths["seg_model"] is not None
            seg_hash = hashlib.sha256(paths["seg_model"].read_bytes()).hexdigest()
            if seg_hash != PINNED_SEGMENTATION_MODEL_SHA256:
                errors.append(f"Segmentation model SHA-256 mismatch: {seg_hash} != {PINNED_SEGMENTATION_MODEL_SHA256}")

            assert paths["emb_model"] is not None
            emb_hash = hashlib.sha256(paths["emb_model"].read_bytes()).hexdigest()
            if emb_hash != PINNED_EMBEDDING_MODEL_SHA256:
                errors.append(f"Embedding model SHA-256 mismatch: {emb_hash} != {PINNED_EMBEDDING_MODEL_SHA256}")
        except Exception as exc:
            errors.append(f"Hash verification error: {exc}")

    return {
        "ready": len(errors) == 0,
        "missing": missing,
        "errors": errors,
        "paths": {k: str(v) if v else None for k, v in paths.items()},
    }


@dataclass
class DiarizationConfig:
    window_sec: float = DEFAULT_DIARIZATION_WINDOW_SEC
    overlap_sec: float = DEFAULT_DIARIZATION_OVERLAP_SEC
    cluster_threshold: float = DEFAULT_DIARIZATION_CLUSTER_THRESHOLD
    num_speakers: int | None = None
    match_threshold: float = DEFAULT_DIARIZATION_MATCH_THRESHOLD
    new_speaker_threshold: float = DEFAULT_DIARIZATION_NEW_THRESHOLD
    centroid_alpha: float = DEFAULT_DIARIZATION_ALPHA
    min_duration_on: float = 0.3
    min_duration_off: float = 0.5

    @property
    def stride_sec(self) -> float:
        return max(30.0, self.window_sec - self.overlap_sec)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SherpaEmbeddingExtractor:
    """Thread-safe C-API wrapper for 512-dimensional speaker embedding extraction."""

    def __init__(self, lib_path: Path, model_path: Path) -> None:
        self.lib_path = lib_path
        self.model_path = model_path
        self._lock = threading.Lock()
        self._lib, self._extractor, self.dim = self._init_extractor(lib_path, model_path)

    @staticmethod
    def _init_extractor(lib_path: Path, model_path: Path) -> tuple[Any, Any, int]:
        with _C_API_LOCK:
            cache_key = f"{lib_path}:{model_path}"
            if cache_key in _EXTRACTOR_CACHE:
                return _EXTRACTOR_CACHE[cache_key]

            # Try loading libonnxruntime first if present in the same directory
            onnx_dir = lib_path.parent
            onnx_lib = onnx_dir / "libonnxruntime.dylib"
            if onnx_lib.exists():
                try:
                    ctypes.CDLL(str(onnx_lib))
                except Exception:
                    pass

            lib = ctypes.CDLL(str(lib_path))

            lib.SherpaOnnxCreateSpeakerEmbeddingExtractor.argtypes = [ctypes.POINTER(_SherpaEmbeddingConfig)]
            lib.SherpaOnnxCreateSpeakerEmbeddingExtractor.restype = ctypes.c_void_p
            lib.SherpaOnnxDestroySpeakerEmbeddingExtractor.argtypes = [ctypes.c_void_p]
            lib.SherpaOnnxDestroySpeakerEmbeddingExtractor.restype = None

            lib.SherpaOnnxSpeakerEmbeddingExtractorDim.argtypes = [ctypes.c_void_p]
            lib.SherpaOnnxSpeakerEmbeddingExtractorDim.restype = ctypes.c_int32

            lib.SherpaOnnxSpeakerEmbeddingExtractorCreateStream.argtypes = [ctypes.c_void_p]
            lib.SherpaOnnxSpeakerEmbeddingExtractorCreateStream.restype = ctypes.c_void_p
            lib.SherpaOnnxDestroyOnlineStream.argtypes = [ctypes.c_void_p]
            lib.SherpaOnnxDestroyOnlineStream.restype = None

            lib.SherpaOnnxOnlineStreamAcceptWaveform.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_int32,
            ]
            lib.SherpaOnnxOnlineStreamAcceptWaveform.restype = None
            lib.SherpaOnnxOnlineStreamInputFinished.argtypes = [ctypes.c_void_p]
            lib.SherpaOnnxOnlineStreamInputFinished.restype = None

            lib.SherpaOnnxSpeakerEmbeddingExtractorIsReady.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            lib.SherpaOnnxSpeakerEmbeddingExtractorIsReady.restype = ctypes.c_int32

            lib.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            lib.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding.restype = ctypes.POINTER(ctypes.c_float)

            lib.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding.argtypes = [ctypes.POINTER(ctypes.c_float)]
            lib.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding.restype = None

            cfg = _SherpaEmbeddingConfig(
                model=str(model_path).encode("utf-8"),
                num_threads=1,
                debug=0,
                provider=b"cpu",
            )
            extractor = lib.SherpaOnnxCreateSpeakerEmbeddingExtractor(ctypes.byref(cfg))
            if not extractor:
                raise RuntimeError(f"Failed to create SherpaOnnxSpeakerEmbeddingExtractor with {model_path}")

            dim = lib.SherpaOnnxSpeakerEmbeddingExtractorDim(extractor)
            _EXTRACTOR_CACHE[cache_key] = (lib, extractor, dim)
            return lib, extractor, dim

    def compute_embedding(self, samples: array.array, sample_rate: int = 16000) -> list[float] | None:
        """Compute unit-normalized 512-d embedding vector for mono float32 samples."""
        if len(samples) < int(sample_rate * 0.4):
            return None

        with self._lock:
            stream = self._lib.SherpaOnnxSpeakerEmbeddingExtractorCreateStream(self._extractor)
            if not stream:
                return None

            try:
                c_samples = (ctypes.c_float * len(samples)).from_buffer(samples)
                self._lib.SherpaOnnxOnlineStreamAcceptWaveform(stream, sample_rate, c_samples, len(samples))
                self._lib.SherpaOnnxOnlineStreamInputFinished(stream)

                if not self._lib.SherpaOnnxSpeakerEmbeddingExtractorIsReady(self._extractor, stream):
                    return None

                ptr = self._lib.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding(self._extractor, stream)
                if not ptr:
                    return None

                raw_emb = [ptr[i] for i in range(self.dim)]
                self._lib.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding(ptr)

                norm = math.sqrt(sum(x * x for x in raw_emb))
                if norm > 1e-9:
                    return [x / norm for x in raw_emb]
                return raw_emb
            finally:
                self._lib.SherpaOnnxDestroyOnlineStream(stream)


class CentroidRegistry:
    """Registry maintaining global speaker acoustic centroids across sliding windows."""

    def __init__(self, initial_speakers: dict[str, Any] | None = None, initial_centroids: dict[str, list[float]] | None = None) -> None:
        self.centroids: dict[str, list[float]] = {}
        if initial_centroids:
            for spk, vec in initial_centroids.items():
                norm = math.sqrt(sum(x * x for x in vec))
                self.centroids[spk] = [x / norm for x in vec] if norm > 1e-9 else list(vec)

        self.speakers: dict[str, dict[str, Any]] = {}
        if initial_speakers:
            self.speakers = json.loads(json.dumps(initial_speakers))
        else:
            self.speakers["speaker_unknown"] = {"display_name": "Неизвестный", "color_index": -1}

    def _next_speaker_id(self) -> str:
        idx = 1
        while f"speaker_{idx:02d}" in self.speakers or f"speaker_{idx:02d}" in self.centroids:
            idx += 1
        return f"speaker_{idx:02d}"

    def register_speaker(self, speaker_id: str | None = None, centroid: list[float] | None = None) -> str:
        if not speaker_id:
            speaker_id = self._next_speaker_id()

        if speaker_id not in self.speakers:
            num = int(speaker_id.split("_")[-1]) if speaker_id.startswith("speaker_") and speaker_id.split("_")[-1].isdigit() else (len(self.speakers) + 1)
            color_idx = (num - 1) % 20
            self.speakers[speaker_id] = {
                "display_name": f"Говорящий {num}",
                "color_index": color_idx,
            }

        if centroid is not None:
            norm = math.sqrt(sum(x * x for x in centroid))
            self.centroids[speaker_id] = [x / norm for x in centroid] if norm > 1e-9 else list(centroid)

        return speaker_id


    def set_speaker_name(self, speaker_id: str, name: str, source: str = "manual", evidence: str = "", confidence: float = 1.0) -> None:
        if speaker_id not in self.speakers:
            self.register_speaker(speaker_id=speaker_id)

        # Only overwrite if manual, or if current source is not manual (auto_intro overrides default, manual overrides auto_intro)
        current_source = self.speakers[speaker_id].get("name_source", "default")
        if source == "manual" or current_source != "manual":
            self.speakers[speaker_id]["display_name"] = name
            self.speakers[speaker_id]["name_source"] = source
            if evidence:
                self.speakers[speaker_id]["name_evidence"] = evidence
            if confidence is not None:
                self.speakers[speaker_id]["name_confidence"] = confidence

    def reset_speaker_name(self, speaker_id: str) -> None:
        if speaker_id in self.speakers:
            num = int(speaker_id.split("_")[-1]) if speaker_id.startswith("speaker_") and speaker_id.split("_")[-1].isdigit() else (len(self.speakers))
            self.speakers[speaker_id]["display_name"] = f"Говорящий {num}"
            self.speakers[speaker_id]["name_source"] = "default"
            self.speakers[speaker_id].pop("name_evidence", None)
            self.speakers[speaker_id].pop("name_confidence", None)

    def match_cluster(
        self,
        cluster_emb: list[float],
        overlap_speaker_ids: set[str] | None = None,
        config: DiarizationConfig | None = None,
    ) -> tuple[str, float]:
        """Match a local cluster embedding against known centroids using cosine similarity."""
        cfg = config or DiarizationConfig()

        if not self.centroids:
            spk_id = self.register_speaker(centroid=cluster_emb)
            return spk_id, 1.0

        scores: list[tuple[str, float]] = []
        for spk, c_vec in self.centroids.items():
            cos_sim = sum(a * b for a, b in zip(cluster_emb, c_vec))
            scores.append((spk, cos_sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        best_spk, best_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else -1.0

        # Reliable match: score >= match_threshold
        if best_score >= cfg.match_threshold:
            # Check ambiguity: if runner-up is within 0.08, consult overlap boundary
            if (best_score - second_score) < 0.08 and overlap_speaker_ids:
                second_spk = scores[1][0]
                if second_spk in overlap_speaker_ids and best_spk not in overlap_speaker_ids:
                    return second_spk, second_score
            return best_spk, best_score

        # Definitely new speaker: score < new_speaker_threshold
        if best_score < cfg.new_speaker_threshold:
            new_id = self.register_speaker(centroid=cluster_emb)
            return new_id, best_score

        # Ambiguous zone [0.35, 0.52]: use overlap evidence if available
        if overlap_speaker_ids and best_spk in overlap_speaker_ids:
            return best_spk, best_score

        # Fallback: if not confirmed by overlap, register new speaker to avoid false attribution
        new_id = self.register_speaker(centroid=cluster_emb)
        return new_id, best_score

    def update_centroid(self, speaker_id: str, new_emb: list[float], alpha: float = DEFAULT_DIARIZATION_ALPHA) -> None:
        """Update centroid with exponential moving average to prevent drift."""
        if speaker_id not in self.centroids:
            self.centroids[speaker_id] = list(new_emb)
            return

        old_vec = self.centroids[speaker_id]
        updated = [alpha * o + (1.0 - alpha) * n for o, n in zip(old_vec, new_emb)]
        norm = math.sqrt(sum(x * x for x in updated))
        self.centroids[speaker_id] = [x / norm for x in updated] if norm > 1e-9 else updated


def slice_audio_for_window(
    chunk_records: list[dict[str, Any]],
    normalized_dir: Path,
    window_start_sec: float,
    window_end_sec: float,
    out_wav_path: Path,
    sample_rate: int = 16000,
) -> float:
    """Slice time window [window_start_sec, window_end_sec] across normalized chunk files into out_wav_path."""
    curr_time = 0.0
    slices: list[tuple[Path, int, int]] = []  # (file_path, start_frame, num_frames)

    for chunk in chunk_records:
        fn = chunk.get("filename", "")
        dur = float(chunk.get("duration_sec", 0.0))
        c_start = curr_time
        c_end = curr_time + dur
        curr_time = c_end

        # Intersection check
        if c_end <= window_start_sec or c_start >= window_end_sec:
            continue

        s_in = max(c_start, window_start_sec)
        e_in = min(c_end, window_end_sec)
        offset_sec = s_in - c_start
        dur_sec = e_in - s_in

        if dur_sec <= 0.0:
            continue

        c_file = normalized_dir / fn
        if not c_file.exists():
            continue

        start_frame = int(round(offset_sec * sample_rate))
        num_frames = int(round(dur_sec * sample_rate))
        slices.append((c_file, start_frame, num_frames))

    out_wav_path.parent.mkdir(parents=True, exist_ok=True)
    total_written_frames = 0

    with wave.open(str(out_wav_path), "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(sample_rate)

        for src_file, start_frame, num_frames in slices:
            with wave.open(str(src_file), "rb") as in_wf:
                in_wf.setpos(start_frame)
                raw_bytes = in_wf.readframes(num_frames)
                out_wf.writeframes(raw_bytes)
                total_written_frames += len(raw_bytes) // 2

    return total_written_frames / float(sample_rate)


def calculate_chunk_fingerprints(session_dir: Path, chunk_records: list[dict[str, Any]]) -> dict[str, str]:
    """Compute SHA-256 digests of session normalized chunks for checkpoint cache invalidation."""
    norm_dir = session_dir / "normalized"
    fingerprints: dict[str, str] = {}
    for c in chunk_records:
        fn = c.get("filename")
        if not fn:
            continue
        p = norm_dir / fn
        if is_safe_regular_file(p):
            fingerprints[fn] = hashlib.sha256(p.read_bytes()).hexdigest()
    return fingerprints


class Diarizer:
    """Orchestrates windowed speaker diarization with atomic checkpoints and cancellation support."""

    def __init__(self, config: DiarizationConfig | None = None) -> None:
        self.config = config or DiarizationConfig()
        self._cancel_requested = False
        self._active_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._extractor: SherpaEmbeddingExtractor | None = None

    def cancel(self) -> None:
        """Signal cancellation and terminate running sherpa-onnx subprocess."""
        self._cancel_requested = True
        with self._lock:
            proc = self._active_proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    from .capture import stop_and_reap_process_group
                    stop_and_reap_process_group(proc, timeout_sec=2.0)
            except Exception:
                pass

    def run_session_diarization(
        self,
        session_id: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Execute full diarization workflow for a session."""
        valid_id = validate_session_id(session_id)
        session_dir = get_session_dir(valid_id)
        manifest = load_session(valid_id)
        if not manifest:
            raise StorageError(f"Session {valid_id} not found")

        # Verify components
        comp_status = verify_diarizer_components()
        if not comp_status["ready"]:
            err = "; ".join(comp_status["errors"])
            manifest.diarization_status = DIARIZATION_FAILED
            manifest.error_message = err
            save_session(manifest)
            raise RuntimeError(f"Diarizer components not ready: {err}")

        paths = resolve_diarizer_paths()
        sherpa_bin = paths["bin"]
        sherpa_lib = paths["lib"]
        seg_model = paths["seg_model"]
        emb_model = paths["emb_model"]

        assert sherpa_bin and sherpa_lib and seg_model and emb_model

        if not self._extractor:
            self._extractor = SherpaEmbeddingExtractor(sherpa_lib, emb_model)

        norm_dir = session_dir / "normalized"
        chunks = manifest.normalized_chunks
        if not chunks:
            raise RuntimeError(f"Session {valid_id} has no normalized audio chunks to diarize")

        total_audio_sec = float(manifest.total_duration_sec)
        if total_audio_sec <= 0.0:
            total_audio_sec = sum(float(c.get("duration_sec", 0.0)) for c in chunks)

        # Chunk fingerprints
        current_chunk_hashes = calculate_chunk_fingerprints(session_dir, chunks)

        # Compute windows
        w_len = self.config.window_sec
        w_stride = self.config.stride_sec
        if total_audio_sec <= w_len:
            windows = [(0.0, total_audio_sec)]
        else:
            windows = []
            curr_start = 0.0
            while curr_start < total_audio_sec:
                curr_end = min(curr_start + w_len, total_audio_sec)
                windows.append((curr_start, curr_end))
                if curr_end >= total_audio_sec:
                    break
                curr_start += w_stride

        total_windows = len(windows)

        # Checkpoint restoration
        checkpoint_path = session_dir / "diarization_checkpoint.json"
        resume_window_idx = 0
        accumulated_turns: list[dict[str, Any]] = []
        registry = CentroidRegistry(initial_speakers=manifest.speakers)

        if is_safe_regular_file(checkpoint_path):
            try:
                cp_data = json.loads(safe_read_text(checkpoint_path))
                if (
                    cp_data.get("schema_version") == 2
                    and cp_data.get("chunk_fingerprints") == current_chunk_hashes
                    and cp_data.get("model_fingerprints", {}).get("seg") == PINNED_SEGMENTATION_MODEL_SHA256
                    and cp_data.get("model_fingerprints", {}).get("emb") == PINNED_EMBEDDING_MODEL_SHA256
                ):
                    resume_window_idx = cp_data.get("last_processed_window", -1) + 1
                    accumulated_turns = cp_data.get("turns", [])
                    registry = CentroidRegistry(
                        initial_speakers=cp_data.get("speakers"),
                        initial_centroids=cp_data.get("global_centroids"),
                    )
            except Exception:
                # Corrupted checkpoint: discard and start fresh safely
                resume_window_idx = 0
                accumulated_turns = []
                registry = CentroidRegistry(initial_speakers=manifest.speakers)

        manifest.diarization_status = DIARIZATION_RUNNING
        manifest.diarization_params = self.config.to_dict()
        save_session(manifest)

        start_time = time.time()
        temp_dir = session_dir / "diarization_tmp"
        safe_make_dir(temp_dir)

        try:
            for w_idx in range(resume_window_idx, total_windows):
                if self._cancel_requested:
                    manifest.diarization_status = DIARIZATION_CANCELLED
                    save_session(manifest)
                    return {"status": "cancelled", "session_id": valid_id}

                w_start, w_end = windows[w_idx]
                w_dur = max(0.0, w_end - w_start)
                if w_dur < 0.5:
                    continue

                # Report progress
                elapsed = time.time() - start_time
                progress_pct = round((w_idx / total_windows) * 100.0, 1)
                eta = round((elapsed / max(1, w_idx - resume_window_idx)) * (total_windows - w_idx), 1) if (w_idx > resume_window_idx) else None

                progress_payload = {
                    "session_id": valid_id,
                    "window_index": w_idx + 1,
                    "total_windows": total_windows,
                    "progress_percent": progress_pct,
                    "elapsed_sec": round(elapsed, 1),
                    "eta_sec": eta,
                    "status": "running",
                }
                if on_progress:
                    try:
                        on_progress(progress_payload)
                    except Exception:
                        pass

                # 1. Slice audio for window
                window_wav = temp_dir / f"window_{w_idx:04d}.wav"
                slice_audio_for_window(chunks, norm_dir, w_start, w_end, window_wav)

                # 2. Run sherpa-onnx CLI
                cmd = [
                    str(sherpa_bin),
                    f"--segmentation.pyannote-model={seg_model}",
                    f"--embedding.model={emb_model}",
                    f"--min-duration-on={self.config.min_duration_on}",
                    f"--min-duration-off={self.config.min_duration_off}",
                ]
                if self.config.num_speakers and self.config.num_speakers > 0:
                    cmd.append(f"--clustering.num-clusters={self.config.num_speakers}")
                else:
                    cmd.append(f"--clustering.cluster-threshold={self.config.cluster_threshold}")
                cmd.append(str(window_wav))

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                with self._lock:
                    self._active_proc = proc

                stdout, stderr = proc.communicate()
                with self._lock:
                    self._active_proc = None

                if proc.returncode != 0 and not self._cancel_requested:
                    raise RuntimeError(f"sherpa-onnx diarization failed (exit {proc.returncode}): {stderr.strip()[:200]}")

                # 3. Parse turns from stdout
                local_turns: list[tuple[float, float, str]] = []
                for line in stdout.splitlines():
                    m = re.match(r"^\s*([0-9]+\.[0-9]+)\s*--\s*([0-9]+\.[0-9]+)\s+(speaker_[0-9]+)", line)
                    if m:
                        local_turns.append((float(m.group(1)), float(m.group(2)), m.group(3)))

                # 4. Extract samples and compute embeddings for local clusters
                with wave.open(str(window_wav), "rb") as wf:
                    w_frames = wf.readframes(wf.getnframes())
                    raw_ints = array.array("h")
                    raw_ints.frombytes(w_frames)
                    w_samples = array.array("f", (x / 32768.0 for x in raw_ints))

                cluster_audio: dict[str, array.array] = {}
                for s, e, spk in local_turns:
                    if spk not in cluster_audio:
                        cluster_audio[spk] = array.array("f")
                    s_idx = int(s * 16000)
                    e_idx = int(e * 16000)
                    cluster_audio[spk].extend(w_samples[s_idx:e_idx])

                cluster_embs: dict[str, list[float]] = {}
                for spk, c_audio in cluster_audio.items():
                    emb = self._extractor.compute_embedding(c_audio)
                    if emb:
                        cluster_embs[spk] = emb

                # Overlap speakers from previous window turns
                overlap_speaker_ids = {
                    t["speaker_id"]
                    for t in accumulated_turns
                    if float(t.get("to_sec", 0.0)) >= w_start
                }

                # Map local clusters to global speakers
                local_to_global: dict[str, str] = {}
                for spk, emb in cluster_embs.items():
                    g_spk, score = registry.match_cluster(emb, overlap_speaker_ids=overlap_speaker_ids, config=self.config)
                    local_to_global[spk] = g_spk
                    registry.update_centroid(g_spk, emb, alpha=self.config.centroid_alpha)

                # 5. Overlap stitching and turn merging
                if w_idx == 0:
                    for s, e, spk in local_turns:
                        g_id = local_to_global.get(spk) or registry.register_speaker()
                        accumulated_turns.append({
                            "from_sec": round(w_start + s, 3),
                            "to_sec": round(w_start + e, 3),
                            "speaker_id": g_id,
                            "confidence": None,
                            "overlap": False,
                        })
                else:
                    # Handover at midpoint of overlap
                    t_handover = w_start + (self.config.overlap_sec / 2.0)

                    # Truncate / prune previously accumulated turns
                    pruned_turns: list[dict[str, Any]] = []
                    for t in accumulated_turns:
                        t_to = float(t.get("to_sec", 0.0))
                        t_from = float(t.get("from_sec", 0.0))
                        if t_to <= t_handover:
                            pruned_turns.append(t)
                        elif t_from < t_handover:
                            t_copy = dict(t)
                            t_copy["to_sec"] = round(t_handover, 3)
                            pruned_turns.append(t_copy)
                    accumulated_turns = pruned_turns

                    # Add new window turns starting from handover
                    for s, e, spk in local_turns:
                        glob_from = w_start + s
                        glob_to = w_start + e
                        if glob_to <= t_handover:
                            continue
                        adjusted_from = max(t_handover, glob_from)
                        g_id = local_to_global.get(spk) or registry.register_speaker()
                        accumulated_turns.append({
                            "from_sec": round(adjusted_from, 3),
                            "to_sec": round(glob_to, 3),
                            "speaker_id": g_id,
                            "confidence": None,
                            "overlap": False,
                        })

                # Cleanup window audio slice immediately (Bounded memory & disk $O(W)$)
                try:
                    window_wav.unlink(missing_ok=True)
                except Exception:
                    pass

                # 6. Save atomic checkpoint
                checkpoint_data = {
                    "schema_version": 2,
                    "session_id": valid_id,
                    "last_processed_window": w_idx,
                    "total_windows": total_windows,
                    "chunk_fingerprints": current_chunk_hashes,
                    "model_fingerprints": {
                        "seg": PINNED_SEGMENTATION_MODEL_SHA256,
                        "emb": PINNED_EMBEDDING_MODEL_SHA256,
                    },
                    "speakers": registry.speakers,
                    "global_centroids": registry.centroids,
                    "turns": accumulated_turns,
                }
                atomic_write_text(checkpoint_path, json.dumps(checkpoint_data, indent=2, ensure_ascii=False))

            # Sort all turns chronologically
            accumulated_turns.sort(key=lambda t: (float(t.get("from_sec", 0.0)), float(t.get("to_sec", 0.0))))

            # 7. Merge diarization with Whisper segments
            transcript_json = session_dir / "transcript.json"
            segments: list[dict[str, Any]] = []
            if is_safe_regular_file(transcript_json):
                try:
                    p_data = json.loads(safe_read_text(transcript_json))
                    segments = p_data.get("segments", [])
                except Exception:
                    segments = []

            merged_segments = merge_diarization_with_segments(
                segments=segments,
                turns=accumulated_turns,
                speakers=registry.speakers,
            )

            # 7b. Run IntroParser on segments to detect auto_intro
            from .config import load_settings
            app_settings = load_settings()
            if getattr(app_settings, 'enable_auto_intro', True):
                try:
                    from .intro_parser import IntroParser
                    parser = IntroParser()

                    # Group adjacent segments by speaker_id
                    sorted_segs = sorted(merged_segments, key=lambda x: float(x.get("from_sec", 0.0)))

                    grouped = []
                    for seg in sorted_segs:
                        spk = seg.get("speaker_id")
                        if not spk or spk == "speaker_unknown":
                            grouped.append(None) # break continuity
                            continue

                        txt = seg.get("text", "").strip()
                        if not txt:
                            continue

                        if grouped and grouped[-1] and grouped[-1]["speaker_id"] == spk:
                            grouped[-1]["text"] += " " + txt
                        else:
                            grouped.append({"speaker_id": spk, "text": txt})

                    for group in grouped:
                        if not group:
                            continue
                        spk = group["speaker_id"]
                        txt = group["text"]

                        match = parser.parse_intro(txt)
                        if match:
                            registry.set_speaker_name(
                                speaker_id=spk,
                                name=match.name,
                                source="auto_intro",
                                evidence=match.evidence,
                                confidence=match.confidence
                            )
                            # add detected_at timestamp
                            if spk in registry.speakers:
                                registry.speakers[spk]["detected_at"] = time.time()
                                registry.speakers[spk]["segment_time"] = group.get("from_sec", 0.0)
                except Exception as e:
                    import logging
                    logging.error(f"Intro parsing failed: {e}")

            # Re-merge to ensure the newly added names (display_name in speakers dict) are propagated
            # if format_speaker_name was used inside merge_diarization_with_segments.
            # Actually merge_diarization_with_segments sets seg["speaker"] = spk_id, not the display name!
            # The export module uses format_speaker_name(seg["speaker_id"], registry.speakers).
            # So we just need to ensure the updated registry.speakers goes into transcript_payload.

            # Build canonical transcript.json (schema_version: 2)
            transcript_payload = {
                "schema_version": 2,
                "session_id": valid_id,
                "title": manifest.title,
                "language": manifest.language,
                "total_duration_sec": total_audio_sec,
                "diarization": {
                    "status": DIARIZATION_COMPLETED,
                    "backend": "sherpa-onnx",
                    "model_version": "pyannote-3.0-int8+eres2net",
                    "params": self.config.to_dict(),
                    "speakers": registry.speakers,
                    "turns": accumulated_turns,
                },
                "segments": merged_segments,
            }
            atomic_write_text(transcript_json, json.dumps(transcript_payload, indent=2, ensure_ascii=False))

            # 8. Update manifest & re-export all formats
            manifest.diarization_status = DIARIZATION_COMPLETED
            manifest.speakers = registry.speakers
            manifest.has_diarization = True
            save_session(manifest)

            exports = generate_all_exports(valid_id)

            if on_progress:
                try:
                    on_progress({
                        "session_id": valid_id,
                        "window_index": total_windows,
                        "total_windows": total_windows,
                        "progress_percent": 100.0,
                        "elapsed_sec": round(time.time() - start_time, 1),
                        "eta_sec": 0.0,
                        "status": "completed",
                    })
                except Exception:
                    pass

            return {
                "status": "completed",
                "session_id": valid_id,
                "speakers": registry.speakers,
                "turns": accumulated_turns,
                "turns_count": len(accumulated_turns),
                "exports": exports,
            }

        except Exception as exc:
            manifest.diarization_status = DIARIZATION_FAILED
            manifest.error_message = f"Ошибка диаризации: {exc}"
            save_session(manifest)
            raise
        finally:
            # Clean up temp dir
            try:
                for f in temp_dir.glob("*.wav"):
                    f.unlink(missing_ok=True)
                temp_dir.rmdir()
            except Exception:
                pass
