import os
import sys
import stat
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from recorder.storage import ensure_readonly_root_dir, StorageError

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
