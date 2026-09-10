import sys
import os
import importlib
from pathlib import Path
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_frozen_paths_macos_bundle(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', '/FakeBundle/Contents/Frameworks', raising=False)

    import recorder.constants
    importlib.reload(recorder.constants)

    try:
        assert str(recorder.constants.RESOURCE_BASE_DIR) == '/FakeBundle/Contents/Resources'
        assert str(recorder.constants.STATIC_DIR) == '/FakeBundle/Contents/Resources/static'
        assert 'Application Support' in str(recorder.constants.BASE_DIR)
        assert str(recorder.constants.BASE_DIR).endswith('rech-v-tekst')
        assert str(recorder.constants.MODELS_DIR).endswith('models')
    finally:
        monkeypatch.delattr(sys, 'frozen', raising=False)
        monkeypatch.delattr(sys, '_MEIPASS', raising=False)
        importlib.reload(recorder.constants)

def test_frozen_paths_flat_bundle(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', '/private/tmp/_MEI12345', raising=False)

    import recorder.constants
    importlib.reload(recorder.constants)

    try:
        assert str(recorder.constants.RESOURCE_BASE_DIR) == '/private/tmp/_MEI12345'
        assert str(recorder.constants.STATIC_DIR) == '/private/tmp/_MEI12345/static'
        assert 'Application Support' in str(recorder.constants.BASE_DIR)
        assert str(recorder.constants.BASE_DIR).endswith('rech-v-tekst')
    finally:
        monkeypatch.delattr(sys, 'frozen', raising=False)
        monkeypatch.delattr(sys, '_MEIPASS', raising=False)
        importlib.reload(recorder.constants)
