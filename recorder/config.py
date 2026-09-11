"""Application settings model, persistent atomic storage, and resolution."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .constants import DEFAULT_MODEL_PATH, TASK_LOCAL_BIN_DIR, get_data_root
from .storage import (
    StorageError,
    atomic_write_text,
    check_disk_space,
    is_safe_regular_file,
    safe_read_text,
)


@dataclass
class AppSettings:
    whisper_bin: str | None = None
    whisper_model_path: str | None = None
    language: str = "ru"
    threads: int = 4
    cpu_threads: int = 2
    no_gpu: bool = False
    enable_auto_intro: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_bool(val: Any, field_name: str) -> bool:
    if isinstance(val, bool):
        return val
    raise ValueError(f"Поле {field_name} должно быть логическим значением (true/false)")


def get_settings_path() -> Path:
    """Return path to persistent settings.json file without following symlinks."""
    env_path = os.environ.get("WEBINAR_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if os.path.islink(p):
            raise StorageError(f"WEBINAR_CONFIG_PATH cannot be a symlink: {env_path}")
        return p.absolute()
    return get_data_root() / "settings.json"


def load_settings() -> AppSettings:
    """Load settings from persistent JSON file or return defaults on missing/corrupted file."""
    try:
        path = get_settings_path()
    except Exception:
        return AppSettings()

    if not path.is_file():
        return AppSettings()

    try:
        if not is_safe_regular_file(path):
            return AppSettings()
        raw = safe_read_text(path, root=path.parent)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return AppSettings()

        raw_no_gpu = data.get("no_gpu", False)
        if isinstance(raw_no_gpu, str):
            no_gpu_val = raw_no_gpu.strip().lower() in ("true", "1", "yes")
        else:
            no_gpu_val = bool(raw_no_gpu)

        return AppSettings(
            whisper_bin=str(data["whisper_bin"]).strip() if data.get("whisper_bin") else None,
            whisper_model_path=str(data["whisper_model_path"]).strip() if data.get("whisper_model_path") else None,
            language=str(data.get("language", "ru")).strip() or "ru",
            threads=int(data.get("threads", 4)),
            cpu_threads=int(data.get("cpu_threads", 2)),
            no_gpu=no_gpu_val,
            enable_auto_intro=bool(data.get("enable_auto_intro", True)),
        )
    except Exception:
        return AppSettings()


def save_settings(new_settings: AppSettings | dict[str, Any]) -> AppSettings:
    """Validate and atomically persist new settings to disk using unified checks.
    Fails closed with ValueError if configured paths are invalid.
    """
    if isinstance(new_settings, dict):
        w_bin = new_settings.get("whisper_bin")
        m_path = new_settings.get("whisper_model_path")
        no_gpu_val = _parse_bool(new_settings.get("no_gpu", False), "no_gpu")
        auto_intro_val = _parse_bool(new_settings.get("enable_auto_intro", True), "enable_auto_intro")
        settings = AppSettings(
            whisper_bin=str(w_bin).strip() if w_bin else None,
            whisper_model_path=str(m_path).strip() if m_path else None,
            language=str(new_settings.get("language", "ru")).strip() or "ru",
            threads=int(new_settings.get("threads", 4)),
            cpu_threads=int(new_settings.get("cpu_threads", 2)),
            no_gpu=no_gpu_val,
            enable_auto_intro=auto_intro_val,
        )
    else:
        settings = new_settings

    # Unified validation for whisper_bin via media_tools
    from .media_tools import (
        invalidate_media_tools_cache,
        verify_whisper_engine,
        verify_whisper_model,
    )

    if settings.whisper_bin:
        ready, w_path, w_ver, w_err = verify_whisper_engine(settings.whisper_bin, force_recheck=True)
        if not ready:
            raise ValueError(f"Бинарный файл whisper не прошёл проверку: {w_err or 'не удалось выполнить'}")

    # Unified validation for whisper_model_path via media_tools
    if settings.whisper_model_path:
        m_info = verify_whisper_model(Path(settings.whisper_model_path), force_recheck=True)
        if not m_info["ready"]:
            raise ValueError(f"Файл модели whisper не прошёл проверку: {m_info['error'] or 'недопустимый файл'}")

    # Validation: language
    allowed_langs = ("ru", "en", "auto")
    if settings.language not in allowed_langs and len(settings.language) != 2:
        raise ValueError(f"Недопустимый язык распознавания: {settings.language}")

    # Validation: threads
    if not (1 <= settings.threads <= 32):
        raise ValueError("Количество потоков GPU должно быть от 1 до 32")
    if not (1 <= settings.cpu_threads <= 32):
        raise ValueError("Количество потоков CPU должно быть от 1 до 32")

    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False)
    atomic_write_text(path, payload, root=path.parent)
    invalidate_media_tools_cache()
    return settings


def get_effective_settings(force_recheck: bool = False) -> dict[str, Any]:
    """Return dictionary of configured vs effective settings, including active environment overrides."""
    settings = load_settings()
    from .media_tools import get_media_tools_status, resolve_model_path, resolve_whisper_bin

    # External tools & model preflight (single source of truth)
    tools_status = get_media_tools_status(force_recheck=force_recheck)

    # Whisper binary resolution & source attribution
    env_bin = os.environ.get("WHISPER_BIN")
    effective_bin = tools_status.whisper_path
    bin_override = bool(env_bin)
    bin_source = "env" if bin_override else ("config" if settings.whisper_bin else ("task_local" if effective_bin and "work/bin" in effective_bin else ("path" if effective_bin else "none")))

    # Model path resolution & source attribution
    env_model = os.environ.get("WHISPER_MODEL_PATH")
    effective_model = tools_status.model_path
    model_override = bool(env_model)
    model_source = "env" if model_override else ("config" if settings.whisper_model_path else ("default" if effective_model else "none"))

    # Environment priority for WHISPER_NO_GPU (handles 0, 1, true, false cleanly)
    env_no_gpu = os.environ.get("WHISPER_NO_GPU")
    if env_no_gpu is not None:
        effective_no_gpu = env_no_gpu.strip().lower() in ("1", "true", "yes")
        no_gpu_override = True
    else:
        effective_no_gpu = settings.no_gpu
        no_gpu_override = False

    # Environment priority for WHISPER_THREADS
    env_threads = os.environ.get("WHISPER_THREADS")
    if env_threads and env_threads.isdigit():
        effective_threads = int(env_threads)
        threads_override = True
    else:
        effective_threads = settings.threads
        threads_override = False

    # Environment priority for WHISPER_CPU_THREADS
    env_cpu_threads = os.environ.get("WHISPER_CPU_THREADS")
    if env_cpu_threads and env_cpu_threads.isdigit():
        effective_cpu_threads = int(env_cpu_threads)
        cpu_threads_override = True
    else:
        effective_cpu_threads = settings.cpu_threads
        cpu_threads_override = False

    # Environment priority for WHISPER_LANG (canonical CLI contract) with WHISPER_LANGUAGE alias
    env_lang = os.environ.get("WHISPER_LANG")
    env_lang_source = "WHISPER_LANG"
    if not env_lang:
        env_lang = os.environ.get("WHISPER_LANGUAGE")
        if env_lang:
            env_lang_source = "WHISPER_LANGUAGE"

    if env_lang:
        effective_lang = env_lang.strip()
        lang_override = True
    else:
        effective_lang = settings.language
        lang_override = False
        env_lang_source = None

    # Disk space info
    disk_info = check_disk_space()
    model_size = tools_status.model_size_bytes

    return {
        "configured": settings.to_dict(),
        "effective": {
            "whisper_bin": effective_bin,
            "whisper_model_path": effective_model,
            "language": effective_lang,
            "threads": effective_threads,
            "cpu_threads": effective_cpu_threads,
            "no_gpu": effective_no_gpu,
            "whisper_version": tools_status.whisper_version,
        },
        "overrides": {
            "whisper_bin": {
                "active": bin_override,
                "env_var": "WHISPER_BIN" if bin_override else None,
                "value": env_bin,
                "source": bin_source,
            },
            "whisper_model_path": {
                "active": model_override,
                "env_var": "WHISPER_MODEL_PATH" if model_override else None,
                "value": env_model,
                "source": model_source,
            },
            "language": {
                "active": lang_override,
                "env_var": env_lang_source,
                "value": env_lang,
            },
            "no_gpu": {
                "active": no_gpu_override,
                "env_var": "WHISPER_NO_GPU" if no_gpu_override else None,
                "value": env_no_gpu,
            },
            "threads": {
                "active": threads_override,
                "env_var": "WHISPER_THREADS" if threads_override else None,
                "value": env_threads,
            },
            "cpu_threads": {
                "active": cpu_threads_override,
                "env_var": "WHISPER_CPU_THREADS" if cpu_threads_override else None,
                "value": env_cpu_threads,
            },
        },
        "model_status": {
            "valid": tools_status.model,
            "path": effective_model,
            "size_bytes": model_size,
            "size_mb": round(model_size / (1024 * 1024), 1) if model_size else 0,
            "size_str": f"{round(model_size / (1024 * 1024 * 1024), 2)} ГБ" if model_size >= 1024 * 1024 * 1024 else (f"{round(model_size / (1024 * 1024), 1)} МБ" if model_size else "0 МБ"),
            "state": tools_status.model_state,
            "error": tools_status.model_error,
            "sha256": tools_status.model_sha256,
        },
        "engine_status": {
            "valid": tools_status.whisper,
            "path": effective_bin,
            "version": tools_status.whisper_version,
            "error": tools_status.whisper_error,
        },
        "disk": {
            "free_bytes": disk_info.get("free_bytes", 0),
            "free_gb": round(disk_info.get("free_bytes", 0) / (1024 * 1024 * 1024), 1),
            "is_low": disk_info.get("is_low", False),
        },
        "tools": tools_status.to_dict(),
    }
