"""Locating and running FFmpeg / FFprobe."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[float, str], None]

# Hide the console window that would otherwise flash up for every FFmpeg call on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class ConversionError(Exception):
    """A conversion failed. The message is meant to be shown to the user."""


class Cancelled(Exception):
    """The user cancelled the running job."""


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    # PATH as stored in the registry: picks up tools installed after this process started.
    if sys.platform == "win32":
        try:
            import winreg

            for root, key in (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    with winreg.OpenKey(root, key) as k:
                        value, _ = winreg.QueryValueEx(k, "Path")
                        dirs += [os.path.expandvars(p) for p in value.split(";") if p]
                except OSError:
                    pass
        except ImportError:
            pass
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
            dirs += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin"))
        dirs += [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]
    elif sys.platform == "darwin":
        # Apps started from Finder don't see the shell's PATH, so look where Homebrew and MacPorts install.
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]
    # A copy placed next to the app (e.g. tools/ffmpeg.exe) also works.
    app_dir = Path(__file__).resolve().parent.parent
    dirs += [str(app_dir / "tools"), str(app_dir / "tools" / "bin")]
    return dirs


FROZEN = bool(getattr(sys, "frozen", False))  # running as the packaged MediaConverter.exe


def _bundled_dirs() -> list[str]:
    """In the packaged app, FFmpeg ships in an "ffmpeg" folder next to the program."""
    if not FROZEN:
        return []
    base = Path(sys.executable).resolve().parent
    internal = Path(getattr(sys, "_MEIPASS", base))
    return [str(base / "ffmpeg"), str(internal / "ffmpeg")]


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    exe = name + (".exe" if sys.platform == "win32" else "")
    # The packaged app uses its own tested FFmpeg first, whatever else is installed.
    for d in _bundled_dirs():
        path = os.path.join(d, exe)
        if os.path.isfile(path):
            return path
    found = shutil.which(name)
    if found:
        return found
    for d in _candidate_dirs():
        path = os.path.join(d, exe)
        if os.path.isfile(path):
            return path
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


def install_hint() -> str:
    """How to get FFmpeg on this computer, in one sentence."""
    if FROZEN:
        return "It ships with Media Converter, so this copy is incomplete: download and install it again from the project page."
    if sys.platform == "win32":
        return "Install it by running  winget install Gyan.FFmpeg  in a terminal, then restart the app."
    if sys.platform == "darwin":
        return "Install it by running  brew install ffmpeg  in Terminal, then restart the app."
    return "Install the ffmpeg package with your system's package manager, then restart the app."


def ffmpeg() -> str:
    path = find_tool("ffmpeg")
    if not path:
        raise ConversionError(f"FFmpeg was not found. {install_hint()}")
    return path


def ffprobe() -> str:
    path = find_tool("ffprobe")
    if not path:
        raise ConversionError(f"FFprobe (part of FFmpeg) was not found. {install_hint()}")
    return path


_KNOWN_ERRORS = [
    ("moov atom not found", "This file is incomplete or damaged. It may not have finished downloading or copying."),
    ("invalid data found when processing input", "This file is damaged, or it isn't really the type its extension says."),
    ("no such file or directory", "The file could not be found. It may have been moved or deleted."),
    ("permission denied", "Access to the file was refused. Close any program using it, or choose another output folder."),
    ("no space left on device", "The disk is full. Free up some space or choose another output folder."),
    ("could not find tag for codec", "This combination of format and codec isn't possible. Try another output format."),
    ("does not contain any stream", "This file contains no audio or video."),
    ("end of file", "The file ends unexpectedly. It may be damaged or incomplete."),
]


def plain_error(stderr: str) -> str:
    """Turn FFmpeg's console output into a sentence a person can act on, with the technical detail below it."""
    text = "\n".join(line for line in stderr.strip().splitlines() if line.strip())
    lower = text.lower()
    for needle, message in _KNOWN_ERRORS:
        if needle in lower:
            return f"{message}\n\nTechnical detail: {text[-400:]}"
    return text or "FFmpeg stopped without saying why."


def probe(path: Path) -> dict:
    """Return FFprobe's JSON description of a media file."""
    result = subprocess.run(
        [ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise ConversionError(plain_error(result.stderr) if result.stderr.strip() else "This file could not be read.")
    return json.loads(result.stdout or "{}")


def duration_of(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        return 0.0


def run_ffmpeg(
    args: list[str],
    output: Path,
    duration: float = 0.0,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    label: str = "Converting",
) -> None:
    """Run FFmpeg with progress reporting. Deletes the partial output on failure or cancel."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )

    stderr_lines: list[str] = []

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    t = threading.Thread(target=read_stderr, daemon=True)
    t.start()

    cancelled = False
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            cancelled = True
            proc.kill()
            break
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and progress and duration > 0:
            try:
                done = int(value) / 1_000_000
            except ValueError:
                continue
            progress(max(0.0, min(done / duration, 0.999)), label)
    proc.wait()
    t.join(timeout=2)

    if cancelled or (cancel is not None and cancel.is_set()):
        output.unlink(missing_ok=True)
        raise Cancelled()
    if proc.returncode != 0:
        output.unlink(missing_ok=True)
        detail = "\n".join(stderr_lines[-6:]) or f"FFmpeg exited with code {proc.returncode}"
        raise ConversionError(plain_error(detail))
