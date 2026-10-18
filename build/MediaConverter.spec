# PyInstaller recipe for MediaConverter.exe. Run through build\build.ps1, not directly.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # noqa: F821 (provided by PyInstaller)

datas = [(str(ROOT / "assets"), "assets")]
binaries = []
hiddenimports = []
# Packages that carry data files, native libraries or lazily imported modules.
for package in ("customtkinter", "tkinterdnd2", "pillow_heif", "curl_cffi", "yt_dlp", "yt_dlp_ejs"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [str(ROOT / "MediaConverter.pyw")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["numpy", "matplotlib", "pandas", "scipy", "IPython", "PyQt5", "PyQt6", "PySide6", "tkinter.test"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaConverter",
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
    console=False,
    upx=False,
)
COLLECT(exe, a.binaries, a.datas, name="MediaConverter", upx=False)  # noqa: F821
