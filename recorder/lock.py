"""Cross-process and cross-thread locking with symlink protection and operation reservation."""
from __future__ import annotations

import atexit
import fcntl
import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .constants import LOCK_FILE, OUT_DIR
from .storage import StorageError, ensure_private_out_dir


class LockBusyError(Exception):
    """Raised when an operation cannot acquire the global process lock or reservation."""


class ProcessLock:
    """Inter-process and inter-thread mutual exclusion lock backed by fcntl.flock on a protected file."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path
        self._thread_lock = threading.RLock()
        self._fd: int | None = None
        self._action: str | None = None
        self._session_id: str | None = None
        self._acquired_by_thread: int | None = None
        self._inheritable: bool = False

    @property
    def lock_path(self) -> Path:
        from .constants import LOCK_FILE
        return self._lock_path if self._lock_path is not None else LOCK_FILE

    @property
    def fd(self) -> int | None:
        """Return the underlying open lock file descriptor, if currently held."""
        return self._fd

    def prepare_inheritable_fd(self) -> int | None:
        """Mark the active lock file descriptor as inheritable for child workers/guardians."""
        with self._thread_lock:
            if self._fd is not None:
                os.set_inheritable(self._fd, True)
                self._inheritable = True
                return self._fd
            return None

    def transfer_ownership(self) -> int | None:
        """Transfer ownership of the kernel flock to child/guardian process.
        Closes the parent's file descriptor WITHOUT calling flock(LOCK_UN) or
        truncating metadata, so that the child retains kernel flock protection."""
        with self._thread_lock:
            if self._fd is not None:
                fd = self._fd
                try:
                    os.close(fd)
                except OSError:
                    pass
                self._fd = None
                self._action = None
                self._session_id = None
                self._acquired_by_thread = None
                self._inheritable = False
                return fd
            return None

    def acquire(self, action: str, session_id: str) -> None:
        """Acquire exclusive lock across processes and threads, with strict symlink protections."""
        with self._thread_lock:
            if self._fd is not None:
                raise LockBusyError(
                    f"Lock already held in this process for action={self._action!r}, session={self._session_id!r}"
                )

            ensure_private_out_dir()

            # Reject if lock path is a symlink
            if os.path.islink(self.lock_path):
                raise StorageError(f"Refusing to open lock file: {self.lock_path} is a symlink!")

            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            try:
                fd = os.open(self.lock_path, flags, 0o600)
            except OSError as exc:
                raise StorageError(f"Failed to open lock file: {exc}") from exc

            # Verify descriptor itself is a regular file and strictly single-link (no hardlinks)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    os.close(fd)
                    raise StorageError(f"Lock file descriptor is not a regular file: mode={oct(st.st_mode)}")
                if st.st_nlink != 1:
                    os.close(fd)
                    raise StorageError(f"Lock file has multiple links (st_nlink={st.st_nlink}); hard links forbidden")
            except OSError as exc:
                os.close(fd)
                raise StorageError(f"Failed to stat lock file: {exc}") from exc

            # Non-blocking lock acquisition BEFORE any truncate or write
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                owner_info = self._read_owner_info(fd)
                os.close(fd)
                msg = (
                    f"Operation busy: action={owner_info.get('action')!r}, "
                    f"session={owner_info.get('session_id')!r}, "
                    f"pid={owner_info.get('pid')}"
                )
                raise LockBusyError(msg) from exc

            # Lock acquired: now safe to truncate and write metadata
            self._fd = fd
            self._action = action
            self._session_id = session_id
            self._acquired_by_thread = threading.get_ident()
            self._inheritable = False

            meta = {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "action": action,
                "session_id": session_id,
                "acquired_at": time.time(),
            }
            try:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, json.dumps(meta).encode("utf-8"))
                os.fsync(fd)
            except OSError:
                pass

    def release(self, force_unlock: bool = False, expected_session_id: str | None = None) -> None:
        """Release the acquired lock safely.
        If expected_session_id is provided and does not match the active session_id,
        the release is skipped to prevent releasing a lock acquired by another operation.
        If the FD was prepared for child inheritance, performs close-only release
        without flock(LOCK_UN) and without truncating metadata, allowing the child
        worker or guardian to retain kernel flock until its process exits or closes the FD."""
        with self._thread_lock:
            if self._fd is not None:
                if expected_session_id is not None and self._session_id != expected_session_id:
                    return
                try:
                    if not self._inheritable or force_unlock:
                        st = os.fstat(self._fd)
                        if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                            os.ftruncate(self._fd, 0)
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    os.close(self._fd)
                except OSError:
                    pass
                finally:
                    self._fd = None
                    self._action = None
                    self._session_id = None
                    self._acquired_by_thread = None
                    self._inheritable = False

    def _read_owner_info(self, fd: int) -> dict[str, Any]:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 1024)
            if raw:
                return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
        return {}

    def get_current_holder(self) -> dict[str, Any] | None:
        """Check if lock is actively held and return owner info if active, else None."""
        with self._thread_lock:
            if self._fd is not None:
                return {
                    "pid": os.getpid(),
                    "action": self._action,
                    "session_id": self._session_id,
                    "local": True,
                }

            if not self.lock_path.exists() or os.path.islink(self.lock_path):
                return None

            flags = os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            try:
                fd = os.open(self.lock_path, flags)
            except OSError:
                return None

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Unheld
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                return None
            except (BlockingIOError, OSError):
                info = self._read_owner_info(fd)
                os.close(fd)
                return info


GLOBAL_LOCK = ProcessLock()


@contextmanager
def locked_operation(action: str, session_id: str) -> Generator[None, None, None]:
    """Context manager for acquiring global lock during record, process, or upload."""
    GLOBAL_LOCK.acquire(action, session_id)
    try:
        yield
    finally:
        GLOBAL_LOCK.release()


def check_global_lock() -> dict[str, Any] | None:
    return GLOBAL_LOCK.get_current_holder()


atexit.register(GLOBAL_LOCK.release)
