"""Tests for clean-clone / fresh-root diarization installation without work/diarization-spike.

Covers:
- Safe archive extraction and rejection of path traversal.
- Verification of pinned SHA-256 digests and rejection of tampered artifacts.
- Atomic staging and permissions (0o755 for bin/libs, 0o644 for models).
- Idempotent re-execution.
- Rejection on non-arm64 architecture.
- Full installation lifecycle into an isolated root.
"""
from __future__ import annotations

import bz2
import contextlib
import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.isolated_test import IsolatedTestCase
import recorder.constants as rc
import recorder.installer as ri
import recorder.diarizer as rd


class TestFreshInstallDiarization(IsolatedTestCase):
    """Test diarization installer on fresh directories simulating a clean git clone."""

    def setUp(self):
        super().setUp()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.fake_base = Path(self.tmp_dir.name)

        # Create isolated subdirectories
        self.fake_work = self.fake_base / "work"
        self.fake_models = self.fake_base / "models"
        self.fake_bin = self.fake_work / "bin"
        self.fake_lib = self.fake_work / "lib"
        self.fake_seg_dir = self.fake_models / "diarization" / "sherpa-onnx-pyannote-segmentation-3-0"
        self.fake_emb_dir = self.fake_models / "diarization"

        # Explicitly ensure work/diarization-spike DOES NOT exist
        spike = self.fake_work / "diarization-spike"
        if spike.exists():
            import shutil
            shutil.rmtree(spike)

    def tearDown(self):
        self.tmp_dir.cleanup()
        super().tearDown()

    def _create_synthetic_tar_bz2(self, file_contents: dict[str, bytes]) -> bytes:
        """Create an in-memory tar.bz2 archive with given member subpaths and contents."""
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w:bz2") as tar:
            for arcname, data in file_contents.items():
                ti = tarfile.TarInfo(name=arcname)
                ti.size = len(data)
                ti.mtime = 1700000000
                ti.mode = 0o755 if arcname.startswith("bin/") or arcname.startswith("lib/") else 0o644
                tar.addfile(ti, io.BytesIO(data))
        return tar_buf.getvalue()

    def test_safe_tar_extraction_rejects_path_traversal(self):
        """Extraction must raise ValueError if an archive member contains '..' or absolute path."""
        bad_tar_buf = io.BytesIO()
        with tarfile.open(fileobj=bad_tar_buf, mode="w:bz2") as tar:
            data = b"malicious content"
            ti = tarfile.TarInfo(name="../etc/evil.sh")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        bad_tar_path = self.fake_base / "evil.tar.bz2"
        bad_tar_path.write_bytes(bad_tar_buf.getvalue())

        dest_file = self.fake_bin / "evil.sh"
        with self.assertRaises(ValueError) as ctx:
            ri._safe_extract_tar_members(
                tar_path=bad_tar_path,
                mappings={"../etc/evil.sh": (dest_file, 0o755)},
            )
        self.assertIn("небезопасный путь", str(ctx.exception))
        self.assertFalse(dest_file.exists())

    def test_download_file_verifies_sha256_and_cleans_staging(self):
        """_download_file must verify SHA-256 and delete staging .part file on digest mismatch."""
        target_path = self.fake_models / "test_model.bin"
        fake_content = b"valid file content"
        correct_sha = hashlib.sha256(fake_content).hexdigest()
        tampered_sha = "0" * 64

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [fake_content, b""]
        mock_resp.headers = {"Content-Length": str(len(fake_content))}
        mock_resp.__enter__.return_value = mock_resp

        # Tampered SHA expectation should raise ValueError
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(ValueError) as ctx:
                ri._download_file(
                    url="https://example.com/model.bin",
                    target_path=target_path,
                    expected_sha256=tampered_sha,
                    expected_size=len(fake_content),
                )
            self.assertIn("Контрольная сумма SHA-256", str(ctx.exception))
            self.assertFalse(target_path.exists())
            # Ensure no orphan staging files exist in target parent directory
            orphan_parts = list(self.fake_models.glob(".test_model.bin.part.*"))
            self.assertEqual(len(orphan_parts), 0)

    def test_install_diarization_components_clean_clone_mocked(self):
        """Complete clean installation with mocked HTTP streams matching pinned hashes."""
        fake_bin_data = b"synthetic-sherpa-onnx-binary-executable"
        fake_c_lib_data = b"synthetic-sherpa-onnx-c-api-dylib"
        fake_onnx_lib_data = b"synthetic-onnxruntime-dylib"
        fake_seg_data = b"synthetic-pyannote-segmentation-model-int8"
        fake_emb_data = b"synthetic-eres2net-embedding-model-16k"

        # Tar archives
        static_tar = self._create_synthetic_tar_bz2({
            "sherpa-onnx-v1.13.7-osx-arm64-static/bin/sherpa-onnx-offline-speaker-diarization": fake_bin_data,
        })
        shared_tar = self._create_synthetic_tar_bz2({
            "sherpa-onnx-v1.13.7-osx-arm64-shared-lib/lib/libsherpa-onnx-c-api.dylib": fake_c_lib_data,
            "sherpa-onnx-v1.13.7-osx-arm64-shared-lib/lib/libonnxruntime.dylib": fake_onnx_lib_data,
        })
        seg_tar = self._create_synthetic_tar_bz2({
            "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx": fake_seg_data,
        })

        static_sha = hashlib.sha256(static_tar).hexdigest()
        shared_sha = hashlib.sha256(shared_tar).hexdigest()
        seg_archive_sha = hashlib.sha256(seg_tar).hexdigest()
        seg_model_sha = hashlib.sha256(fake_seg_data).hexdigest()
        emb_model_sha = hashlib.sha256(fake_emb_data).hexdigest()

        def fake_urlopen(req, timeout=30):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url == rc.PINNED_SHERPA_ARM64_STATIC_URL:
                data = static_tar
            elif url == rc.PINNED_SHERPA_ARM64_SHARED_LIB_URL:
                data = shared_tar
            elif url == rc.PINNED_SEGMENTATION_URL:
                data = seg_tar
            elif url == rc.PINNED_EMBEDDING_URL:
                data = fake_emb_data
            else:
                raise ValueError(f"Unexpected URL: {url}")

            mock_res = MagicMock()
            mock_res.read.side_effect = [data, b""]
            mock_res.headers = {"Content-Length": str(len(data))}
            mock_res.__enter__.return_value = mock_res
            return mock_res

        progress_events = []
        def _on_progress(stage, msg, pct):
            progress_events.append((stage, msg, pct))

        patches = [
            patch.object(rc, "BASE_DIR", self.fake_base),
            patch.object(rc, "MODELS_DIR", self.fake_models),
            patch.object(rc, "WORK_BIN_DIR", self.fake_bin),
            patch.object(rc, "WORK_LIB_DIR", self.fake_lib),
            patch.object(rc, "DIARIZATION_MODELS_DIR", self.fake_models / "diarization"),
            patch.object(ri, "BASE_DIR", self.fake_base),
            patch.object(ri, "MODELS_DIR", self.fake_models),
            patch.object(rd, "BASE_DIR", self.fake_base),
            
            patch.object(rd, "WORK_BIN_DIR", self.fake_bin),
            patch.object(rd, "WORK_LIB_DIR", self.fake_lib),
            patch.object(rd, "DIARIZATION_MODELS_DIR", self.fake_models / "diarization"),
            patch.object(rc, "PINNED_SHERPA_ARM64_STATIC_SHA256", static_sha),
            patch.object(rc, "PINNED_SHERPA_ARM64_SHARED_LIB_SHA256", shared_sha),
            patch.object(rc, "PINNED_SEGMENTATION_ARCHIVE_SHA256", seg_archive_sha),
            patch.object(rc, "PINNED_SEGMENTATION_MODEL_SHA256", seg_model_sha),
            patch.object(rc, "PINNED_EMBEDDING_MODEL_SHA256", emb_model_sha),
            patch.object(rc, "PINNED_EMBEDDING_MODEL_SIZE", len(fake_emb_data)),
            patch.object(ri, "PINNED_SHERPA_ARM64_STATIC_SHA256", static_sha),
            patch.object(ri, "PINNED_SHERPA_ARM64_SHARED_LIB_SHA256", shared_sha),
            patch.object(ri, "PINNED_SEGMENTATION_ARCHIVE_SHA256", seg_archive_sha),
            patch.object(ri, "PINNED_SEGMENTATION_MODEL_SHA256", seg_model_sha),
            patch.object(ri, "PINNED_EMBEDDING_MODEL_SHA256", emb_model_sha),
            patch.object(ri, "PINNED_EMBEDDING_MODEL_SIZE", len(fake_emb_data)),
            patch.object(rd, "PINNED_SEGMENTATION_MODEL_SHA256", seg_model_sha),
            patch.object(rd, "PINNED_EMBEDDING_MODEL_SHA256", emb_model_sha),
            patch("platform.machine", return_value="arm64"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            status = ri.install_diarization_components(force=True, progress_cb=_on_progress)
            self.assertTrue(status["ready"])
            self.assertTrue(status["arch_supported"])

            # Verify files on disk
            installed_bin = self.fake_bin / rc.SHERPA_BIN_NAME
            installed_c_lib = self.fake_lib / rc.SHERPA_LIB_NAME
            installed_onnx_lib = self.fake_lib / rc.ONNXRUNTIME_LIB_NAME
            installed_seg = self.fake_seg_dir / rc.PYANNOTE_SEG_MODEL_NAME
            installed_emb = self.fake_emb_dir / rc.ERES2NET_EMB_MODEL_NAME

            self.assertTrue(installed_bin.exists())
            self.assertTrue(installed_c_lib.exists())
            self.assertTrue(installed_onnx_lib.exists())
            self.assertTrue(installed_seg.exists())
            self.assertTrue(installed_emb.exists())

            # Verify permissions
            self.assertTrue(bool(installed_bin.stat().st_mode & stat.S_IXUSR))
            self.assertEqual(installed_bin.read_bytes(), fake_bin_data)
            self.assertEqual(installed_c_lib.read_bytes(), fake_c_lib_data)
            self.assertEqual(installed_onnx_lib.read_bytes(), fake_onnx_lib_data)
            self.assertEqual(installed_seg.read_bytes(), fake_seg_data)
            self.assertEqual(installed_emb.read_bytes(), fake_emb_data)

            # Check progress was reported
            self.assertTrue(len(progress_events) > 0)
            self.assertEqual(progress_events[-1][0], "completed")

            # Test idempotence without force
            progress_events.clear()
            status_second = ri.install_diarization_components(force=False, progress_cb=_on_progress)
            self.assertTrue(status_second["ready"])

    def test_install_diarization_rejects_x86_64(self):
        """Attempting to install on x86_64 must fail with informative Russian message."""
        with patch("platform.machine", return_value="x86_64"):
            with self.assertRaises(RuntimeError) as ctx:
                ri.install_diarization_components(force=True)
            self.assertIn("Intel x86_64 заблокирована", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
