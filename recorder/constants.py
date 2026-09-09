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
}

LOG_LIMIT = 2000
