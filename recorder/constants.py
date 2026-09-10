"""Constants and configuration parameters for webinar-recorder."""
from __future__ import annotations

import os
import re
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
_env_out = os.environ.get("WEBINAR_OUT_DIR")
OUT_DIR = Path(_env_out).resolve() if _env_out else BASE_DIR / "out"
MODELS_DIR = BASE_DIR / "models"
DEFAULT_MODEL_PATH = MODELS_DIR / "ggml-large-v3-turbo.bin"
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = OUT_DIR / "uploads"
LOCK_FILE = OUT_DIR / ".recorder.lock"
TASK_LOCAL_BIN_DIR = BASE_DIR / "work" / "task_local_ffmpeg"
WORK_BIN_DIR = BASE_DIR / "work" / "bin"
WORK_LIB_DIR = BASE_DIR / "work" / "lib"
DIARIZATION_MODELS_DIR = MODELS_DIR / "diarization"

# Diarization Pinned Binaries and Models (ADR-001)
SHERPA_BIN_NAME = "sherpa-onnx-offline-speaker-diarization"
SHERPA_LIB_NAME = "libsherpa-onnx-c-api.dylib"
ONNXRUNTIME_LIB_NAME = "libonnxruntime.dylib"
PYANNOTE_SEG_MODEL_NAME = "model.int8.onnx"
ERES2NET_EMB_MODEL_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

PINNED_SHERPA_ARM64_STATIC_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.7/sherpa-onnx-v1.13.7-osx-arm64-static.tar.bz2"
PINNED_SHERPA_ARM64_STATIC_SHA256 = "4392c74b9d6138d15219d1113b4a54ef7edb2eb34b710d3a2ef0ffb7b5910b1a"
PINNED_SHERPA_ARM64_BINARY_SHA256 = "7cba57f66d1b039c886716778345b1ab2802cd144beddbaffe7590b5768e0e43"

PINNED_SHERPA_ARM64_SHARED_LIB_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.7/sherpa-onnx-v1.13.7-osx-arm64-shared-lib.tar.bz2"
PINNED_SHERPA_ARM64_SHARED_LIB_SHA256 = "c51e220217f2ce5d3de211887dc13ad49bf22346a2025a4159591d892008242d"

PINNED_SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
PINNED_SEGMENTATION_ARCHIVE_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
PINNED_SEGMENTATION_MODEL_SHA256 = "d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d"
PINNED_SEGMENTATION_MODEL_SIZE = 1540506

PINNED_EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
PINNED_EMBEDDING_MODEL_SHA256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
PINNED_EMBEDDING_MODEL_SIZE = 39593761

# Diarization window parameters
DEFAULT_DIARIZATION_WINDOW_SEC = 600.0
DEFAULT_DIARIZATION_OVERLAP_SEC = 60.0
DEFAULT_DIARIZATION_CLUSTER_THRESHOLD = 0.85
DEFAULT_DIARIZATION_MATCH_THRESHOLD = 0.52
DEFAULT_DIARIZATION_NEW_THRESHOLD = 0.35
DEFAULT_DIARIZATION_ALPHA = 0.80

# Diarization states
DIARIZATION_IDLE = "idle"
DIARIZATION_RUNNING = "running"
DIARIZATION_COMPLETED = "completed"
DIARIZATION_FAILED = "failed"
DIARIZATION_CANCELLED = "cancelled"


def get_data_root() -> Path:
    """Return the current active OUT_DIR."""
    return OUT_DIR


def set_data_root(new_root: Path) -> None:
    """Dynamically point OUT_DIR, UPLOADS_DIR, and LOCK_FILE to an isolated test root."""
    global OUT_DIR, UPLOADS_DIR, LOCK_FILE
    OUT_DIR = new_root
    UPLOADS_DIR = new_root / "uploads"
    LOCK_FILE = new_root / ".recorder.lock"
    import sys
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("recorder"):
            mod = sys.modules[mod_name]
            if hasattr(mod, "OUT_DIR"):
                setattr(mod, "OUT_DIR", OUT_DIR)
            if hasattr(mod, "UPLOADS_DIR"):
                setattr(mod, "UPLOADS_DIR", UPLOADS_DIR)
            if hasattr(mod, "LOCK_FILE"):
                setattr(mod, "LOCK_FILE", LOCK_FILE)

# Networking & Server Defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_JSON_BODY_BYTES = 64 * 1024  # 64 KB
MAX_UPLOAD_BODY_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
DEFAULT_POLL_INTERVAL_MS = 2000

# Audio & Capture Settings:
# Short 120-second atomic segments to minimize loss window on crash
DEFAULT_SEGMENT_TIME_SEC = 120
TARGET_WHISPER_RATE = 16000
TARGET_WHISPER_CHANNELS = 1
MIN_DISK_FREE_BYTES = 500 * 1024 * 1024  # 500 MB minimum reserve

# Validation regex
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")
UNSAFE_FILENAME_RE = re.compile(r"[/\\:\x00-\x1f\x7f]+")

# Durable Session States
STATE_RECORDING = "recording"
STATE_READY = "ready"
STATE_PROCESSING = "processing"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_INTERRUPTED = "interrupted"

ALL_STATES = (
    STATE_RECORDING,
    STATE_READY,
    STATE_PROCESSING,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
)

# Source kinds
SOURCE_ZOOM = "zoom"
SOURCE_MIC = "mic"
SOURCE_UPLOAD = "upload"

# Summary providers
SUMMARY_PROVIDER_NONE = "none"
SUMMARY_PROVIDER_AUTO = "auto"
SUMMARY_PROVIDER_CLAUDE = "claude"
SUMMARY_PROVIDER_CODEX = "codex"  # Disabled / roadmap by default

VALID_SUMMARY_PROVIDERS = (
    SUMMARY_PROVIDER_NONE,
    SUMMARY_PROVIDER_AUTO,
    SUMMARY_PROVIDER_CLAUDE,
    SUMMARY_PROVIDER_CODEX,
)

# Summary templates
TEMPLATE_MEETING = "meeting"
TEMPLATE_LECTURE = "lecture"
TEMPLATE_INTERVIEW = "interview"
TEMPLATE_ACTION_ITEMS = "action_items"

VALID_TEMPLATES = (
    TEMPLATE_MEETING,
    TEMPLATE_LECTURE,
    TEMPLATE_INTERVIEW,
    TEMPLATE_ACTION_ITEMS,
)

ALLOWED_EXPORT_FILES = {
    "transcript.txt",
    "transcript.md",
    "transcript.json",
    "transcript.srt",
    "transcript.vtt",
    "summary.md",
    "session.json",
    "diarization_checkpoint.json",
}

LOG_LIMIT = 2000
