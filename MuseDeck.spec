# -*- mode: python ; coding: utf-8 -*-
# MuseDeck 1.0 打包配置：单文件轻量化原生 WebView2 桌面端（~14MB）

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('assets/icon.png', 'assets'), ('assets/cacert.pem', 'assets'), ('musedeck/web', 'musedeck/web')],
    hiddenimports=['webview', 'clr', 'pythonnet', 'musedeck.core', 'musedeck.web_app', 'musedeck.api_server'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy', 'PyQt6', 'qfluentwidgets'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MuseDeck',
    icon='assets/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
