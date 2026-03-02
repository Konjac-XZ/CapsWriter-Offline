# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for CapsWriter-Offline GUI client.

Build command:
    pyinstaller start_client_gui.spec

The resulting executable will be in dist/start_client_gui/start_client_gui.exe
with all runtime dependencies in the same directory (onedir mode).
"""

block_cipher = None

a = Analysis(
    ['start_client_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('config', 'config'),
        ('config.toml', '.'),
        ('util/client_gui_theme_custom.css', 'util'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'qt_material',
        'win32api',
        'win32con',
        'win32gui',
        'win32print',
        'pywintypes',
        'win32com',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Explicitly exclude PyQt5 to avoid mixed Qt bindings when PyQt5 is present
    excludes=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.QtNetwork',
        'PyQt5.QtPrintSupport',
    ],
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
    [],
    exclude_binaries=True,
    name='start_client_gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/client-icon.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='start_client_gui',
)
