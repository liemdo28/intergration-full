# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPECPATH)

datas = [
    (str(project_dir / "Map"), "Map"),
    (str(project_dir / "qb-mapping.json"), "."),
    (str(project_dir / ".env.qb.example"), "."),
    (str(project_dir / "local-config.example.json"), "."),
    (str(project_dir / "README.md"), "."),
]

hiddenimports = [
    "win32timezone",
]

a = Analysis(
    ["app.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ToastPOSManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ToastPOSManager",
)
