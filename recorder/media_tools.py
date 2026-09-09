from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import DEFAULT_MODEL_PATH, TASK_LOCAL_BIN_DIR
from .storage import is_safe_regular_file

DEFAULT_FFMPEG_NAMES = (None, "", "ffmpeg")
DEFAULT_FFPROBE_NAMES = (None, "", "ffprobe")

# Pinned catalog model specifications
CATALOG_MODEL_NAME = "ggml-large-v3-turbo.bin"
CATALOG_MODEL_SIZE = 1624555275
CATALOG_MODEL_SHA256 = "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
VALID_GGML_MAGICS = (b"lmgg", b"fmgg", b"tjgg", b"GGUF")

MAX_CACHE_ENTRIES = 128
# Caches keyed by (path, st_size, st_mtime_ns, st_ctime_ns, st_ino, st_dev)
_WHISPER_PROBE_CACHE: dict[tuple[str, int, int, int, int, int], dict[str, Any]] = {}
_MODEL_VERIFY_CACHE: dict[tuple[str, int, int, int, int, int], dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def invalidate_media_tools_cache() -> None:
    """Explicitly invalidate external tool and model verification caches."""
    with _CACHE_LOCK:
        _WHISPER_PROBE_CACHE.clear()
        _MODEL_VERIFY_CACHE.clear()


def _put_whisper_cache(key: tuple[str, int, int, int, int, int], val: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        if len(_WHISPER_PROBE_CACHE) >= MAX_CACHE_ENTRIES:
            _WHISPER_PROBE_CACHE.clear()
        _WHISPER_PROBE_CACHE[key] = val


def _put_model_cache(key: tuple[str, int, int, int, int, int], val: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        if len(_MODEL_VERIFY_CACHE) >= MAX_CACHE_ENTRIES:
            _MODEL_VERIFY_CACHE.clear()
        _MODEL_VERIFY_CACHE[key] = val


@dataclass(frozen=True)
class MediaToolsStatus:
    ffmpeg_path: str | None
    ffprobe_path: str | None
    ffmpeg: bool
    ffprobe: bool
    ready: bool
    missing: tuple[str, ...]
    remediation: str | None
    whisper: bool = False
    model: bool = False
    whisper_path: str | None = None
    model_path: str | None = None
    whisper_version: str | None = None
    model_size_bytes: int = 0
    model_state: str = "missing"  # "verified", "unverified", "corrupt", "invalid_format", "missing", "unsafe"
    model_error: str | None = None
    whisper_error: str | None = None
    model_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            # Backward-compatible booleans for existing API clients:
            "ffmpeg": self.ffmpeg,
            "ffprobe": self.ffprobe,
            # Structured preflight fields:
            "ready": self.ready,
            "missing": list(self.missing),
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "remediation": self.remediation,
            "whisper": self.whisper,
            "model": self.model,
            "whisper_path": self.whisper_path,
            "model_path": self.model_path,
            "whisper_version": self.whisper_version,
            "model_size_bytes": self.model_size_bytes,
            "model_state": self.model_state,
            "model_error": self.model_error,
            "whisper_error": self.whisper_error,
            "model_sha256": self.model_sha256,
        }


def _is_executable_file(path_str: str) -> bool:
    """Check if a path string points to an existing executable file."""
    try:
        p = Path(path_str)
        return p.is_file() and os.access(p, os.X_OK)
    except (OSError, ValueError):
        return False


def resolve_ffmpeg_bin(explicit_bin: str | None = None) -> str | None:
    """Resolve ffmpeg binary path using strict priority:
    1. Explicit caller override (when not matching default 'ffmpeg').
       If provided and invalid, raises FileNotFoundError (fail-closed, no silent fallback!).
    2. FFMPEG_BIN environment variable. If set and invalid, raises FileNotFoundError.
    3. Active PATH lookup via shutil.which("ffmpeg").
    4. Task-local fallback at TASK_LOCAL_BIN_DIR / "ffmpeg" via shutil.which.
    Does NOT mutate os.environ["PATH"].
    """
    is_default = explicit_bin in DEFAULT_FFMPEG_NAMES

    # 1. Real explicit override (must fail-closed if invalid)
    if not is_default:
        assert explicit_bin is not None
        if _is_executable_file(explicit_bin):
            return str(Path(explicit_bin).resolve())
        w = shutil.which(explicit_bin)
        if w:
            return w
        raise FileNotFoundError(
            f"Explicitly configured FFmpeg binary not found or not executable: {explicit_bin}"
        )

    # 2. Environment variable override
    env_override = os.environ.get("FFMPEG_BIN")
    if env_override:
        if _is_executable_file(env_override):
            return str(Path(env_override).resolve())
        w = shutil.which(env_override)
        if w:
            return w
        raise FileNotFoundError(
            f"FFMPEG_BIN environment variable points to non-existent or non-executable binary: {env_override}"
        )

    # 3. Active PATH lookup
    path_bin = shutil.which("ffmpeg")
    if path_bin:
        return path_bin

    # 4. Task-local fallback (resolved through shutil.which so mocks and permissions apply)
    local_bin = TASK_LOCAL_BIN_DIR / "ffmpeg"
    if local_bin.is_file():
        return shutil.which(str(local_bin))

    return None


def resolve_ffprobe_bin(explicit_bin: str | None = None) -> str | None:
    """Resolve ffprobe binary path using strict priority:
    1. Explicit caller override (when not matching default 'ffprobe').
       If provided and invalid, raises FileNotFoundError (fail-closed, no silent fallback!).
    2. FFPROBE_BIN environment variable. If set and invalid, raises FileNotFoundError.
    3. Active PATH lookup via shutil.which("ffprobe").
    4. Task-local fallback at TASK_LOCAL_BIN_DIR / "ffprobe" via shutil.which.
    Does NOT mutate os.environ["PATH"].
    """
    is_default = explicit_bin in DEFAULT_FFPROBE_NAMES

    # 1. Real explicit override (must fail-closed if invalid)
    if not is_default:
        assert explicit_bin is not None
        if _is_executable_file(explicit_bin):
            return str(Path(explicit_bin).resolve())
        w = shutil.which(explicit_bin)
        if w:
            return w
        raise FileNotFoundError(
            f"Explicitly configured ffprobe binary not found or not executable: {explicit_bin}"
        )

    # 2. Environment variable override
    env_override = os.environ.get("FFPROBE_BIN")
    if env_override:
        if _is_executable_file(env_override):
            return str(Path(env_override).resolve())
        w = shutil.which(env_override)
        if w:
            return w
        raise FileNotFoundError(
            f"FFPROBE_BIN environment variable points to non-existent or non-executable binary: {env_override}"
        )

    # 3. Active PATH lookup
    path_bin = shutil.which("ffprobe")
    if path_bin:
        return path_bin

    # 4. Task-local fallback (resolved through shutil.which so mocks and permissions apply)
    local_bin = TASK_LOCAL_BIN_DIR / "ffprobe"
    if local_bin.is_file():
        return shutil.which(str(local_bin))

    return None


def resolve_whisper_bin(explicit_bin: str | None = None) -> str | None:
    """Resolve whisper binary path using strict priority:
    1. Explicit caller override.
    2. WHISPER_BIN environment variable.
    3. Persistent settings.json configured path (if valid).
    4. Isolated project runtime bins (work/bin/whisper-cli, TASK_LOCAL_BIN_DIR / "whisper-cli").
    5. Active PATH lookup for whisper-cli, whisper-cpp, whisper.
    Does NOT mutate os.environ["PATH"] and avoids .resolve() to preserve symlink checks.
    """
    if explicit_bin:
        if _is_executable_file(explicit_bin):
            return str(Path(explicit_bin).absolute())
        w = shutil.which(explicit_bin)
        if w:
            return w
        return None

    env_override = os.environ.get("WHISPER_BIN")
    if env_override:
        if _is_executable_file(env_override):
            return str(Path(env_override).absolute())
        w = shutil.which(env_override)
        if w:
            return w
        return None

    # 3. Persistent settings.json override
    try:
        from .config import load_settings
        saved_bin = load_settings().whisper_bin
        if saved_bin:
            if _is_executable_file(saved_bin):
                return str(Path(saved_bin).absolute())
            w = shutil.which(saved_bin)
            if w:
                return w
            return None
    except Exception:
        pass

    # 4. Isolated project runtime bins
    from .constants import BASE_DIR
    local_candidates = [
        BASE_DIR / "work" / "bin" / "whisper-cli",
        TASK_LOCAL_BIN_DIR / "whisper-cli",
        TASK_LOCAL_BIN_DIR / "whisper",
    ]
    for local_bin in local_candidates:
        if local_bin.is_file():
            w = shutil.which(str(local_bin))
            if w:
                return w

    # 5. System PATH lookup
    for candidate in ("whisper-cli", "whisper-cpp", "whisper"):
        w = shutil.which(candidate)
        if w:
            return w

    return None


def resolve_model_path(explicit_path: Path | None = None) -> Path:
    """Resolve Whisper model path using strict priority:
    1. Explicit caller override.
    2. WHISPER_MODEL_PATH environment variable.
    3. Persistent settings.json configured path.
    4. DEFAULT_MODEL_PATH (models/ggml-large-v3-turbo.bin).
    Preserves symlink identity by avoiding .resolve().
    """
    if explicit_path is not None:
        return Path(explicit_path).absolute()
    env_override = os.environ.get("WHISPER_MODEL_PATH")
    if env_override:
        return Path(env_override).absolute()

    # 3. Persistent settings.json
    try:
        from .config import load_settings
        saved_model = load_settings().whisper_model_path
        if saved_model:
            return Path(saved_model).absolute()
    except Exception:
        pass

    from .constants import DEFAULT_MODEL_PATH
    return DEFAULT_MODEL_PATH


def verify_whisper_engine(
    whisper_bin: str | None = None,
    force_recheck: bool = False,
) -> tuple[bool, str | None, str | None, str | None]:
    """Check availability and functionality of whisper executable with caching.
    Verifies actual whisper.cpp identity and command-line capabilities (-m, -f, -oj/-otxt).
    Returns: (ready: bool, path: str | None, version: str | None, error: str | None)
    """
    w_path = resolve_whisper_bin(whisper_bin)
    if not w_path:
        return False, None, None, "Исполняемый файл whisper-cli не найден"

    p = Path(w_path)
    if not p.is_file() or not os.access(p, os.X_OK):
        return False, w_path, None, f"Файл {w_path} не существует или не является исполняемым"

    if not is_safe_regular_file(p):
        return False, w_path, None, f"Файл {w_path} не является безопасным регулярным файлом (символические ссылки запрещены)"

    try:
        st = p.stat()
        cache_key = (
            str(p.absolute()),
            st.st_size,
            st.st_mtime_ns,
            getattr(st, "st_ctime_ns", 0),
            st.st_ino,
            st.st_dev,
        )
    except OSError as exc:
        return False, w_path, None, f"Не удалось прочитать атрибуты файла {w_path}: {exc}"

    if not force_recheck:
        with _CACHE_LOCK:
            cached = _WHISPER_PROBE_CACHE.get(cache_key)
            if cached is not None:
                return cached["ready"], cached["path"], cached["version"], cached["error"]

    # Execute probe with strict timeout
    probe_ready = False
    probe_version: str | None = None
    probe_error: str | None = None

    try:
        # Step 1: probe --version
        res_ver = subprocess.run(
            [w_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        ver_out = (res_ver.stdout or "").strip()
        ver_err = (res_ver.stderr or "").strip()
        combined_ver = ver_out + " " + ver_err

        # Step 2: probe -h for required whisper CLI flags (-m, -f, -oj/-otxt)
        res_help = subprocess.run(
            [w_path, "-h"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        help_out = (res_help.stdout or "") + " " + (res_help.stderr or "")

        # Verify identity: version output must contain whisper.cpp, or help must identify whisper.cpp
        has_whisper_identity = (
            "whisper.cpp" in combined_ver.lower()
            or "whisper.cpp" in help_out.lower()
            or ("whisper" in combined_ver.lower() and "whisper-cli" in help_out.lower())
        )

        # Verify CLI capability flags: MUST have -m / --model, -f / --file, and output options
        has_m_flag = ("-m " in help_out or "--model" in help_out)
        has_f_flag = ("-f " in help_out or "--file" in help_out)
        has_out_flag = ("-oj" in help_out or "-otxt" in help_out or "--output-json" in help_out)
        has_capabilities = has_m_flag and has_f_flag and has_out_flag

        if res_ver.returncode == 0 and has_whisper_identity and has_capabilities:
            probe_ready = True
            first_line = ver_out.splitlines()[0] if ver_out else ""
            if "whisper.cpp" in first_line:
                probe_version = first_line
            else:
                probe_version = f"whisper.cpp ({first_line or '1.9.3'})"
        elif has_whisper_identity and has_capabilities:
            # Fallback if --version exit code was non-zero on some build, but capabilities match
            probe_ready = True
            probe_version = "whisper.cpp (compatible)"
        else:
            probe_ready = False
            probe_version = None
            if not has_whisper_identity or not has_capabilities:
                probe_error = (
                    f"Исполняемый файл {w_path} не опознан как whisper.cpp "
                    f"(отсутствует идентификатор whisper.cpp или параметры -m/-f/-oj)"
                )
            else:
                probe_error = f"Ошибка запуска whisper-cli (код {res_ver.returncode}): {ver_err or 'exec failed'}"
    except subprocess.TimeoutExpired:
        probe_error = "Таймаут выполнения проверки whisper-cli (3 сек)"
    except Exception as exc:
        probe_error = f"Сбой запуска whisper-cli: {exc}"

    _put_whisper_cache(cache_key, {
        "ready": probe_ready,
        "path": w_path,
        "version": probe_version,
        "error": probe_error,
    })

    return probe_ready, w_path, probe_version, probe_error


def verify_whisper_model(
    model_path: Path | None = None,
    force_recheck: bool = False,
) -> dict[str, Any]:
    """Check availability, format, and checksum integrity of whisper model with caching.
    Supported model: official large-v3-turbo verified by SHA-256 (even if renamed).
    Non-matching/custom models without verified SHA-256 are marked unverified with ready=False.
    Returns dictionary with:
      ready: bool
      path: str | None
      state: str ("verified", "unverified", "corrupt", "missing", "unsafe")
      size_bytes: int
      sha256: str | None
      error: str | None
      is_catalog: bool
    """
    m_path = resolve_model_path(model_path)
    is_catalog_name = (m_path.name == CATALOG_MODEL_NAME or m_path == DEFAULT_MODEL_PATH)

    if not m_path.exists():
        return {
            "ready": False,
            "path": str(m_path),
            "state": "missing",
            "size_bytes": 0,
            "sha256": None,
            "error": f"Файл модели не найден: {m_path}",
            "is_catalog": is_catalog_name,
        }

    if not is_safe_regular_file(m_path):
        return {
            "ready": False,
            "path": str(m_path),
            "state": "unsafe",
            "size_bytes": 0,
            "sha256": None,
            "error": f"Файл модели не является безопасным регулярным файлом (символические ссылки запрещены): {m_path}",
            "is_catalog": is_catalog_name,
        }

    try:
        st = m_path.stat()
        cache_key = (
            str(m_path.absolute()),
            st.st_size,
            st.st_mtime_ns,
            getattr(st, "st_ctime_ns", 0),
            st.st_ino,
            st.st_dev,
        )
    except OSError as exc:
        return {
            "ready": False,
            "path": str(m_path),
            "state": "corrupt",
            "size_bytes": 0,
            "sha256": None,
            "error": f"Не удалось прочитать атрибуты файла модели: {exc}",
            "is_catalog": is_catalog_name,
        }

    if not force_recheck:
        with _CACHE_LOCK:
            cached = _MODEL_VERIFY_CACHE.get(cache_key)
            if cached is not None:
                return dict(cached)

    res: dict[str, Any]

    # Check size match for large-v3-turbo (supports catalog model even if renamed or in custom path)
    if st.st_size == CATALOG_MODEL_SIZE:
        # Check SHA-256
        h = hashlib.sha256()
        try:
            with open(m_path, "rb") as f:
                while chunk := f.read(4 * 1024 * 1024):
                    h.update(chunk)
            digest = h.hexdigest()
            if digest == CATALOG_MODEL_SHA256:
                res = {
                    "ready": True,
                    "path": str(m_path),
                    "state": "verified",
                    "size_bytes": st.st_size,
                    "sha256": digest,
                    "error": None,
                    "is_catalog": True,
                }
            else:
                res = {
                    "ready": False,
                    "path": str(m_path),
                    "state": "corrupt",
                    "size_bytes": st.st_size,
                    "sha256": digest,
                    "error": "Контрольная сумма SHA-256 модели не совпадает с официальной large-v3-turbo",
                    "is_catalog": is_catalog_name,
                }
        except Exception as exc:
            res = {
                "ready": False,
                "path": str(m_path),
                "state": "corrupt",
                "size_bytes": st.st_size,
                "sha256": None,
                "error": f"Ошибка чтения файла модели: {exc}",
                "is_catalog": is_catalog_name,
            }
    elif is_catalog_name:
        # Catalog named model with wrong size is definitely corrupt
        res = {
            "ready": False,
            "path": str(m_path),
            "state": "corrupt",
            "size_bytes": st.st_size,
            "sha256": None,
            "error": (
                f"Размер каталожной модели {m_path.name} ({st.st_size} байт) "
                f"не совпадает с официальным ({CATALOG_MODEL_SIZE} байт)"
            ),
            "is_catalog": True,
        }
    else:
        # Custom model with non-catalog size (e.g. junk file, another model)
        # MUST NOT be ready=True without full verified load smoke!
        res = {
            "ready": False,
            "path": str(m_path),
            "state": "unverified",
            "size_bytes": st.st_size,
            "sha256": None,
            "error": "Модель не проверена: поддерживается проверенная модель large-v3-turbo с подтверждённым SHA-256 (нестандартные модели требуют проверочного запуска)",
            "is_catalog": False,
        }

    _put_model_cache(cache_key, dict(res))
    return dict(res)


def get_media_tools_status(
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
    whisper_bin: str | None = None,
    model_path: Path | None = None,
    force_recheck: bool = False,
) -> MediaToolsStatus:
    """Check availability of required external media tools and return immutable status."""
    ff_path: str | None = None
    fp_path: str | None = None
    missing: list[str] = []

    try:
        ff_path = resolve_ffmpeg_bin(ffmpeg_bin)
    except FileNotFoundError:
        ff_path = None
    if not ff_path:
        missing.append("ffmpeg")

    try:
        fp_path = resolve_ffprobe_bin(ffprobe_bin)
    except FileNotFoundError:
        fp_path = None
    if not fp_path:
        missing.append("ffprobe")

    is_ready = len(missing) == 0
    remediation = None
    if not is_ready:
        remediation = (
            "Установите FFmpeg (например, `brew install ffmpeg`) или поместите исполняемые файлы "
            "ffmpeg и ffprobe в каталог work/task_local_ffmpeg."
        )

    w_ready, w_path, w_ver, w_err = verify_whisper_engine(whisper_bin, force_recheck=force_recheck)
    m_info = verify_whisper_model(model_path, force_recheck=force_recheck)

    return MediaToolsStatus(
        ffmpeg_path=ff_path,
        ffprobe_path=fp_path,
        ffmpeg=bool(ff_path),
        ffprobe=bool(fp_path),
        ready=is_ready,
        missing=tuple(missing),
        remediation=remediation,
        whisper=w_ready,
        model=m_info["ready"],
        whisper_path=w_path,
        model_path=m_info["path"],
        whisper_version=w_ver,
        model_size_bytes=m_info["size_bytes"],
        model_state=m_info["state"],
        model_error=m_info["error"],
        whisper_error=w_err,
        model_sha256=m_info["sha256"],
    )
