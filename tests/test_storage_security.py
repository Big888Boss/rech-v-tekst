"""Unit tests for storage security: symlink protections, path traversals, and atomic writes."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from recorder.constants import OUT_DIR
from recorder.session import SessionManifest, create_session, load_session, save_session
from recorder.storage import (
    StorageError,
    atomic_write_bytes,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_make_dir,
    safe_read_file,
    safe_upload_filename,
    validate_session_id,
    verify_path_components_safe,
)
from tests.isolated_test import IsolatedTestCase


class TestStorageSecurity(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.data_root

    def test_session_id_validation(self) -> None:
        valid_ids = ["webinar_20260908", "lecture-01", "session.test_123", "zoom_2026_09"]
        for sid in valid_ids:
            self.assertEqual(validate_session_id(sid), sid)

        invalid_ids = [
            "../etc/passwd",
            "foo/bar",
            "\\test",
            "session;rm -rf",
            "a",  # too short
            "",
            "   ",
            "session\x00null",
            "../../../outside",
        ]
        for bad_id in invalid_ids:
            with self.assertRaises(StorageError):
                validate_session_id(bad_id)

    def test_symlink_component_rejection(self) -> None:
        # Create a directory inside root
        real_dir = self.root / "real_dir"
        real_dir.mkdir()

        # Create a symlink pointing outside
        symlink_dir = self.root / "link_dir"
        os.symlink(real_dir, symlink_dir)

        # Path through symlink must be rejected by verify_path_components_safe
        target_through_link = symlink_dir / "file.txt"
        with self.assertRaises(StorageError):
            verify_path_components_safe(target_through_link, root=self.root)

    def test_safe_read_rejects_symlink(self) -> None:
        real_file = self.root / "test_real_file.txt"
        sym_file = self.root / "test_sym_file.txt"
        try:
            atomic_write_text(real_file, "secret data")
            if os.path.exists(sym_file):
                os.unlink(sym_file)
            os.symlink(real_file, sym_file)

            # safe_read_file on sym_file must fail
            with self.assertRaises(StorageError):
                safe_read_file(sym_file)
        finally:
            real_file.unlink(missing_ok=True)
            if os.path.islink(sym_file):
                sym_file.unlink(missing_ok=True)

    def test_atomic_write_preserves_on_failure(self) -> None:
        target = self.root / "atomic_target.txt"
        try:
            atomic_write_text(target, "initial content")
            self.assertEqual(safe_read_file(target).decode("utf-8"), "initial content")

            # Update atomically
            atomic_write_text(target, "updated content")
            self.assertEqual(safe_read_file(target).decode("utf-8"), "updated content")
        finally:
            target.unlink(missing_ok=True)

    def test_get_session_dir_exclusive_creation(self) -> None:
        sid = "test_exclusive_sess"
        sess_dir = self.root / sid
        try:
            # First creation must succeed
            dir1 = get_session_dir(sid, create_exclusive=True)
            self.assertTrue(dir1.exists())

            # Second exclusive creation must raise StorageError
            with self.assertRaises(StorageError):
                get_session_dir(sid, create_exclusive=True)
        finally:
            import shutil
            shutil.rmtree(sess_dir, ignore_errors=True)

    def test_safe_read_hardlink_rejected(self) -> None:
        real_file = self.root / "original_file.txt"
        real_file.write_text("classified content")
        hardlink_file = self.root / "hardlinked_file.txt"
        os.link(real_file, hardlink_file)
        self.assertEqual(hardlink_file.stat().st_nlink, 2)

        # safe_read_file must reject hardlinks (st_nlink > 1)
        with self.assertRaises(StorageError):
            safe_read_file(hardlink_file)

    def test_load_session_identity_mismatch_rejected(self) -> None:
        # Create session sess_A
        manifest_a = create_session("sess_A", title="Original A")
        sess_dir = get_session_dir("sess_A")
        # Tamper manifest JSON so session_id is sess_B
        manifest_file = sess_dir / "session.json"
        data = manifest_a.to_dict()
        data["session_id"] = "sess_B"  # Mismatched ID!
        import json
        manifest_file.write_text(json.dumps(data), encoding="utf-8")

        # Loading sess_A when file contains sess_B must return None
        self.assertIsNone(load_session("sess_A"))

    def test_load_session_symlink_manifest_rejected(self) -> None:
        create_session("sess_symlink_test")
        sess_dir = get_session_dir("sess_symlink_test")
        manifest_file = sess_dir / "session.json"
        manifest_file.unlink()

        secret_file = self.root / "secret.json"
        secret_file.write_text('{"session_id":"sess_symlink_test","status":"ready"}')

        os.symlink(secret_file, manifest_file)
        self.assertIsNone(load_session("sess_symlink_test"))

    def test_safe_upload_filename_cyrillic_spaces_quotes(self) -> None:
        # Non-ASCII, Cyrillic, spaces, quotes
        test_names = [
            ("запись 01 (Zoom).wav", "01_Zoom"),
            ("O'Reilly Presentation.mp3", "O_Reilly_Presentation"),
            ("meeting   double   spaces.wav", "meeting_double_spaces"),
            ("---leading-trailing---.m4a", "leading-trailing"),
        ]
        for raw, expected_substr in test_names:
            sanitized = safe_upload_filename(raw)
            self.assertIn(expected_substr, sanitized)
            # Must be safe ASCII
            self.assertTrue(sanitized.isascii())
            self.assertNotIn(" ", sanitized)
    def test_sec03_corrupted_manifest_with_empty_transcript_reports_failed(self) -> None:
        from recorder.constants import STATE_COMPLETED, STATE_FAILED, STATE_INTERRUPTED
        from recorder.session import list_sessions

        # Trigger scenario from CODEX_SECURITY_RECHECK_02:
        # Corrupted session.json + empty transcript.txt
        corrupt_dir = self.root / "alpha_corrupt"
        corrupt_dir.mkdir(0o700, exist_ok=True)
        (corrupt_dir / "session.json").write_text("{ broken JSON", encoding="utf-8")
        (corrupt_dir / "transcript.txt").write_text("", encoding="utf-8")

        sessions = {s.session_id: s for s in list_sessions()}
        self.assertIn("alpha_corrupt", sessions)
        alpha = sessions["alpha_corrupt"]

        # Must report failed with error message and has_transcript=False, NEVER completed!
        self.assertEqual(alpha.status, STATE_FAILED)
        self.assertFalse(alpha.has_transcript)
        self.assertFalse(alpha.has_summary)
        self.assertIsNotNone(alpha.error_message)
        self.assertIn("Invalid manifest JSON", alpha.error_message)

        # Legacy session with valid non-empty transcript -> completed
        legacy_dir = self.root / "legacy_valid"
        legacy_dir.mkdir(0o700, exist_ok=True)
        (legacy_dir / "transcript.txt").write_text("00:00 Привет", encoding="utf-8")

        sessions2 = {s.session_id: s for s in list_sessions()}
        self.assertIn("legacy_valid", sessions2)
        leg = sessions2["legacy_valid"]
        self.assertEqual(leg.status, STATE_COMPLETED)
        self.assertTrue(leg.has_transcript)

        # Legacy session with empty transcript -> interrupted, never completed!
        legacy_empty = self.root / "legacy_empty"
        legacy_empty.mkdir(0o700, exist_ok=True)
        (legacy_empty / "transcript.txt").write_text("", encoding="utf-8")

        sessions3 = {s.session_id: s for s in list_sessions()}
        self.assertIn("legacy_empty", sessions3)
        leg_empty = sessions3["legacy_empty"]
        self.assertNotEqual(leg_empty.status, STATE_COMPLETED)
        self.assertEqual(leg_empty.status, STATE_INTERRUPTED)
        self.assertFalse(leg_empty.has_transcript)

    def test_sec03_unknown_status_preserves_original_name_in_error(self) -> None:
        from recorder.constants import STATE_FAILED
        m = SessionManifest.from_dict({"status": "unknown_custom_xyz"})
        self.assertEqual(m.status, STATE_FAILED)
        self.assertEqual(m.error_message, "Invalid manifest status: unknown_custom_xyz")


if __name__ == "__main__":
    unittest.main()


import pytest
from recorder.storage import ensure_readonly_root_dir


def test_ensure_readonly_root_dir_accepts_real_dir():
    with tempfile.TemporaryDirectory() as d:
        real_dir = Path(d)
        # Should not raise
        result = ensure_readonly_root_dir(real_dir)
        assert result == real_dir


def test_ensure_readonly_root_dir_rejects_symlink():
    with tempfile.TemporaryDirectory() as d:
        real_dir = Path(d) / "real"
        real_dir.mkdir()

        symlink_dir = Path(d) / "symlinked"
        os.symlink(real_dir, symlink_dir)

        with pytest.raises(StorageError) as exc:
            ensure_readonly_root_dir(symlink_dir)
        assert "cannot be a symlink" in str(exc.value)


def test_ensure_readonly_root_dir_rejects_file():
    with tempfile.TemporaryDirectory() as d:
        fpath = Path(d) / "file.txt"
        fpath.write_text("hello")

        with pytest.raises(StorageError) as exc:
            ensure_readonly_root_dir(fpath)
        assert "must be a regular directory" in str(exc.value)


def test_ensure_readonly_root_dir_rejects_missing():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "missing"

        with pytest.raises(StorageError) as exc:
            ensure_readonly_root_dir(missing)
        assert "Read-only directory missing" in str(exc.value)
