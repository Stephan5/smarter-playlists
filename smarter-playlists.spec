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
    # Every extension module is a file macOS scans the first time it runs, so leave out the ones smarter-playlists
    # doesn't use. Those are the ones no command loads, and ones the standard library has pure Python versions of
    # (_json, _decimal, _asyncio etc.) or only uses if it can (bz2, lzma, zstd). Not _ssl or _ctypes, which psycopg and
    # PyObjC need. After changing this, run every command from the built executable, as a missing module only shows
    # when something imports it.
    excludes=['tkinter', 'unittest', 'pydoc', 'doctest', 'xmlrpc', 'multiprocessing', 'curses', 'sqlite3', 'turtle',
              'idlelib', 'lib2to3', 'wsgiref', '_codecs_cn', '_codecs_hk', '_codecs_iso2022', '_codecs_jp',
              '_codecs_kr', '_codecs_tw', '_blake2', '_csv', '_hashlib', '_interpqueues', '_md5', '_multibytecodec',
              '_scproxy', '_sha1', '_sha2', '_sha3', '_statistics', '_uuid', 'resource', 'unicodedata',
              '_lzma', '_bz2', '_zstd', '_asyncio', '_decimal', '_json', '_pickle', '_heapq', '_bisect', '_queue',
              '_zoneinfo', '_elementtree'],
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
