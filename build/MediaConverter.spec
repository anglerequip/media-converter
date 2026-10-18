# PyInstaller recipe for MediaConverter.exe (Windows) and Media Converter.app (macOS).
# Run through build\build.ps1 or build/build_mac.sh, not directly.
# -*- mode: python ; coding: utf-8 -*-
import os
import platform
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
    d, b, h = collect_all(package, include_py_files=False)  # the code itself is already compiled into the app
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
    # sqlite3: only used by yt-dlp to read browser cookies, which this app never does (yt-dlp works without it).
    excludes=["numpy", "matplotlib", "pandas", "scipy", "IPython", "PyQt5", "PyQt6", "PySide6", "tkinter.test", "sqlite3"],
)

# Leave out files this build can never use (about 4.5 MB and 700 files):
#  - tkinterdnd2's drag-and-drop libraries for the other ten systems and CPUs
#  - Tcl's time-zone database (the app never uses Tcl's clock command; Python handles dates)
_arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
_dnd = ("osx-" if MAC else "win-") + _arch


def _keep(dest: str) -> bool:
    parts = dest.replace("\\", "/").split("/")
    if parts[:2] == ["_tcl_data", "tzdata"]:
        return False
    if parts[:2] == ["tkinterdnd2", "tkdnd"] and len(parts) > 3:
        return parts[2] in (_dnd, _dnd + "-tcl9")
    return True


a.datas = [entry for entry in a.datas if _keep(entry[0])]
a.binaries = [entry for entry in a.binaries if _keep(entry[0])]

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
