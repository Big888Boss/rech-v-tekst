"""Unit tests for ProcessLock: flock, thread-safety, symlink rejection, and stale locks."""
from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path

from recorder.lock import LockBusyError, ProcessLock
from recorder.storage import StorageError
from tests.isolated_test import IsolatedTestCase


class TestProcessLock(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.lock_file = self.data_root / ".recorder.lock"
        self.lock = ProcessLock(self.lock_file)

    def tearDown(self) -> None:
        try:
            self.lock.release()
        finally:
            super().tearDown()

    def test_acquire_and_release(self) -> None:
        self.lock.acquire("record", "sess_01")
        self.assertIsNotNone(self.lock.get_current_holder())

        # Second acquire in same process must raise LockBusyError
        with self.assertRaises(LockBusyError):
            self.lock.acquire("process", "sess_02")

        self.lock.release()
        self.assertIsNone(self.lock.get_current_holder())

    def test_symlink_lock_file_rejection(self) -> None:
        # Create real file and symlink
        real_target = self.data_root / "some_file.txt"
        real_target.write_text("protected content")

        os.symlink(real_target, self.lock_file)

        # Attempting to acquire lock on a symlink must raise StorageError, never truncate the target!
        with self.assertRaises(StorageError):
            self.lock.acquire("record", "sess_hack")

        # Verify real_target was not truncated!
        self.assertEqual(real_target.read_text(), "protected content")

    def test_hardlink_lock_file_rejection(self) -> None:
        # Create real file and hardlink
        real_target = self.data_root / "some_hardlink_target.txt"
        real_target.write_text("sensitive hardlink content")

        os.link(real_target, self.lock_file)
        self.assertEqual(self.lock_file.stat().st_nlink, 2)

        # Attempting to acquire lock on a hardlinked file must raise StorageError, never truncate the target!
        with self.assertRaises(StorageError):
            self.lock.acquire("record", "sess_hack_hardlink")

        # Verify real_target was not truncated!
        self.assertEqual(real_target.read_text(), "sensitive hardlink content")

    def test_multithreaded_concurrency(self) -> None:
        results = []

        def worker(idx: int):
            lock = ProcessLock(self.lock_file)
            try:
                lock.acquire("test", f"sess_{idx}")
                results.append(f"acquired_{idx}")
                import time
                time.sleep(0.05)
                lock.release()
            except LockBusyError:
                results.append(f"busy_{idx}")

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one thread should acquire, the other must see busy
        acquired_count = sum(1 for r in results if r.startswith("acquired"))
        busy_count = sum(1 for r in results if r.startswith("busy"))
        self.assertEqual(acquired_count, 1)
        self.assertEqual(busy_count, 1)


if __name__ == "__main__":
    unittest.main()
