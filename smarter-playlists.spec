# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_smarter_playlists.py'],
    pathex=[],
    binaries=[],
    datas=[('smarter_playlists/*.sql', 'smarter_playlists'), ('smarter_playlists/migrations/*.sql', 'smarter_playlists/migrations'), ('smarter_playlists/*.js', 'smarter_playlists')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='smarter-playlists',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
