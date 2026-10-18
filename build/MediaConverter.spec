# PyInstaller recipe for MediaConverter.exe (Windows) and Media Converter.app (macOS).
# Run through build\build.ps1 or build/build_mac.sh, not directly.
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # noqa: F821 (provided by PyInstaller)
MAC = sys.platform == "darwin"

datas = [(str(ROOT / "assets"), "assets")]
binaries = []
hiddenimports = []
# Packages that carry data files, native libraries or lazily imported modules.
for package in ("customtkinter", "tkinterdnd2", "pillow_heif", "curl_cffi", "yt_dlp", "yt_dlp_ejs"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

if MAC:
    # On macOS FFmpeg goes inside the app bundle (Contents/Frameworks/ffmpeg) so the bundle's signature covers it.
    # build_mac.sh downloads the pinned, checksummed build and passes its folder here.
    ffmpeg_dir = Path(os.environ["MC_FFMPEG_DIR"])
    binaries += [(str(ffmpeg_dir / name), "ffmpeg") for name in ("ffmpeg", "ffprobe")]
    datas += [(str(ffmpeg_dir / "README.txt"), "ffmpeg")]

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
    icon=str(ROOT / "assets" / ("icon.icns" if MAC else "icon.ico")),
    version=None if MAC else str(ROOT / "build" / "version_info.txt"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MediaConverter", upx=False)  # noqa: F821

if MAC:
    version = os.environ.get("MC_VERSION", "0.0.0")
    BUNDLE(  # noqa: F821
        coll,
        name="Media Converter.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="io.github.anglerequip.mediaconverter",
        version=version,
        info_plist={
            "CFBundleName": "Media Converter",
            "CFBundleDisplayName": "Media Converter",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow the system's light/dark mode
            "NSHumanReadableCopyright": "MIT License",
        },
    )
