# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

# Include statically served UI files
datas = [
    ('static', 'static')
]

a = Analysis(
    ['macos_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'webview', 'AppKit', 'objc', 'recorder'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='macos_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='macos_app',
)
app = BUNDLE(
    coll,
    name='Речь в текст.app',
    icon=None,
    bundle_identifier='com.pavelboss888.rech-v-tekst',
    info_plist={
        'NSMicrophoneUsageDescription': 'Приложению необходим доступ к микрофону для записи речи.',
        'NSHighResolutionCapable': 'True',
        'CFBundleName': 'Речь в текст',
        'CFBundleDisplayName': 'Речь в текст',
        'CFBundleVersion': '1.2.0',
        'CFBundleShortVersionString': '1.2.0',
    },
)
