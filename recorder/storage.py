"""Safe filesystem and storage operations with strict symlink and traversal protections."""
from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from .constants import (
    MIN_DISK_FREE_BYTES,
    OUT_DIR,
    SESSION_ID_RE,
    UNSAFE_FILENAME_RE,
)


class StorageError(Exception):
    """Raised when a storage validation or disk check fails."""


def _resolve_root(root: Path | None = None) -> Path:
    from .constants import OUT_DIR
    return root if root is not None else OUT_DIR


def ensure_private_out_dir(target_dir: Path | None = None) -> Path:
    """Ensure directory exists and has 0o700 permissions. Never follow symlinks."""
    out_dir = _resolve_root(target_dir)
    if os.path.islink(out_dir):
        raise StorageError(f"Directory cannot be a symlink: {out_dir}")

    if not out_dir.exists():
        os.makedirs(out_dir, mode=0o700, exist_ok=True)

    st = os.lstat(out_dir)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise StorageError(f"Directory must be a regular directory: {out_dir}")

    try:
        os.chmod(out_dir, 0o700)
    except OSError:
        pass

    return out_dir


def validate_session_id(session_id: str) -> str:
    """Validate that session_id contains only safe characters and no traversals."""
    if not session_id or not isinstance(session_id, str):
        raise StorageError("Session ID cannot be empty")
    cleaned = session_id.strip()
    if not SESSION_ID_RE.fullmatch(cleaned):
        raise StorageError(f"Invalid session ID format: {cleaned!r}")
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise StorageError(f"Path traversal characters detected in session ID: {cleaned!r}")
    return cleaned


def verify_path_components_safe(target_path: Path, root: Path | None = None) -> None:
    """Verify that every directory component from root to target_path is not a symlink.
    Crucially does NOT call .resolve() on target_path to avoid canonicalizing symlinks away.
    """
    active_root = _resolve_root(root)
    from .constants import STATIC_DIR
    if active_root != STATIC_DIR:
        ensure_private_out_dir(active_root)

    # Compute relative path string without resolving symlinks in target_path
    target_abs = os.path.abspath(target_path)
    root_abs = os.path.abspath(active_root)

    try:
        rel_str = os.path.relpath(target_abs, root_abs)
    except ValueError as exc:
        raise StorageError(f"Path traversal escape outside root: {target_path}") from exc

    if rel_str == ".." or rel_str.startswith(".." + os.sep):
        raise StorageError(f"Path traversal escape: {target_path} escapes root {active_root}")

    # Walk each component from root without following symlinks
    current = Path(root_abs)
    parts = Path(rel_str).parts
    for part in parts:
        if part in (".", "..") or "/" in part or "\\" in part:
            raise StorageError(f"Unsafe component in path: {part!r}")
        current = current / part
        if os.path.islink(current):
            raise StorageError(f"Symlink detected in path component: {current}")
        if current.exists():
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                raise StorageError(f"Symlink detected via lstat: {current}")


def get_session_dir(
    session_id: str,
    create_exclusive: bool = False,
    create_if_missing: bool = True,
    root: Path | None = None,
) -> Path:
    """Return the absolute Path to a session directory, enforcing containment and no-symlinks."""
    valid_id = validate_session_id(session_id)
    active_root = _resolve_root(root)
    from .constants import STATIC_DIR
    if active_root != STATIC_DIR:
        ensure_private_out_dir(active_root)
    target_path = active_root / valid_id

    if os.path.islink(target_path):
        raise StorageError(f"Session path is a symlink: {target_path}")

    if target_path.exists():
        st = os.lstat(target_path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise StorageError(f"Session path is not a directory: {target_path}")
        if create_exclusive:
            raise StorageError(f"Session directory already exists: {valid_id}")
    elif create_exclusive or create_if_missing:
        try:
            os.mkdir(target_path, 0o700)
        except FileExistsError as exc:
            raise StorageError(f"Session directory already exists: {valid_id}") from exc

    verify_path_components_safe(target_path, root=active_root)
    return target_path


def safe_make_dir(path: Path, mode: int = 0o700, root: Path | None = None) -> None:
    """Create directory strictly inside root with restricted private permissions (0o700)."""
    active_root = _resolve_root(root)
    from .constants import STATIC_DIR
    if active_root != STATIC_DIR:
        ensure_private_out_dir(active_root)

    target_abs = os.path.abspath(path)
    root_abs = os.path.abspath(active_root)

    try:
        rel_str = os.path.relpath(target_abs, root_abs)
    except ValueError as exc:
        raise StorageError(f"Refusing to create directory outside root: {path}") from exc

    if rel_str == ".." or rel_str.startswith(".." + os.sep):
        raise StorageError(f"Refusing to create directory outside root: {path}")

    current = Path(root_abs)
    for part in Path(rel_str).parts:
        current = current / part
        if os.path.islink(current):
            raise StorageError(f"Cannot create directory through symlink: {current}")
        if not current.exists():
            os.mkdir(current, mode)
        else:
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                raise StorageError(f"Symlink detected at: {current}")


def is_safe_regular_file(path: Path) -> bool:
    """Check if path is an existing regular file, strictly single link (st_nlink == 1), and NOT a symlink."""
    try:
        if os.path.islink(path):
            return False
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return False
        if not stat.S_ISREG(st.st_mode):
            return False
        if st.st_nlink != 1:
            return False
        return True
    except (OSError, ValueError):
        return False


def safe_read_file(path: Path, root: Path | None = None) -> bytes:
    """Read a regular file strictly inside root without following symlinks and rejecting hardlinks."""
    active_root = _resolve_root(root)
    verify_path_components_safe(path, root=active_root)

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise StorageError(f"Cannot open file safely: {path} ({exc})") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StorageError(f"Opened file descriptor is not a regular file: {path}")
        if st.st_nlink != 1:
            raise StorageError(f"Opened file descriptor has hard links (st_nlink={st.st_nlink}): {path}")
        with open(fd, "rb", closefd=True) as f:
            return f.read()
    except Exception as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise StorageError(f"Failed safe read for {path}: {exc}") from exc


def safe_read_text(path: Path, root: Path | None = None, encoding: str = "utf-8") -> str:
    """Read regular file text safely with nofollow and single-link enforcement."""
    raw = safe_read_file(path, root=root)
    return raw.decode(encoding)


def atomic_write_bytes(target: Path, data: bytes, mode: int = 0o600, root: Path | None = None) -> None:
    """Atomically write data to target path inside root using a temporary file and fsync."""
    active_root = _resolve_root(root)
    verify_path_components_safe(target.parent, root=active_root)
    if os.path.islink(target):
        raise StorageError(f"Target file is a symlink: {target}")

    safe_make_dir(target.parent, root=active_root)
    temp_name = f".tmp_{uuid.uuid4().hex}_{target.name}"
    temp_path = target.parent / temp_name

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(temp_path, flags, mode)
    try:
        with open(fd, "wb", closefd=True) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        if os.path.islink(target):
            raise StorageError(f"Target became a symlink: {target}")

        os.replace(temp_path, target)
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError(f"Failed atomic write to {target}: {exc}") from exc


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8", mode: int = 0o600, root: Path | None = None) -> None:
    """Atomically write string content to target path."""
    atomic_write_bytes(target, text.encode(encoding), mode=mode, root=root)


def check_disk_space(target_path: Path | None = None) -> dict[str, Any]:
    path = target_path or OUT_DIR
    if not path.exists():
        try:
            ensure_private_out_dir()
        except Exception:
            pass

    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        free_bytes = usage.free
        total_bytes = usage.total
        used_bytes = usage.used
        pct_used = (used_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0

        est_bytes_per_sec = 48000 * 2 * 2
        hours_remaining = max(0.0, (free_bytes - MIN_DISK_FREE_BYTES) / (est_bytes_per_sec * 3600.0))

        return {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "percent_used": round(pct_used, 1),
            "is_low": free_bytes < MIN_DISK_FREE_BYTES,
            "hours_remaining": round(hours_remaining, 1),
            "min_required_bytes": MIN_DISK_FREE_BYTES,
        }
    except Exception as exc:
        return {
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "percent_used": 0.0,
            "is_low": True,
            "hours_remaining": 0.0,
            "min_required_bytes": MIN_DISK_FREE_BYTES,
            "error": str(exc),
        }


def safe_upload_filename(filename: str) -> str:
    import re
    name = Path(filename or "audio").name
    stem = Path(name).stem[:40] or "audio"
    suffix = Path(name).suffix[:10] or ".audio"
    clean_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip(" ._") or "audio"
    clean_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)
    unique_suffix = uuid.uuid4().hex[:8]
    return f"{clean_stem}_{unique_suffix}{clean_suffix}"
