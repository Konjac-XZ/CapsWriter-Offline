# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for CapsWriter-Offline GUI client.

Build command:
    pyinstaller start_client_gui.spec

The resulting executable will be in dist/start_client_gui.exe
Copy it to the repository root for distribution.
"""

block_cipher = None

a = Analysis(
    ['start_client_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('config', 'config'),
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='start_client_gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
