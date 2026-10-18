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
    # A copy placed next to the app (e.g. tools/ffmpeg.exe) also works.
    app_dir = Path(__file__).resolve().parent.parent
    dirs += [str(app_dir / "tools"), str(app_dir / "tools" / "bin")]
    return dirs


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if sys.platform == "win32" else "")
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


def ffmpeg() -> str:
    path = find_tool("ffmpeg")
    if not path:
        raise ConversionError(
            "FFmpeg was not found. Install it with:  winget install Gyan.FFmpeg  and restart the app."
        )
    return path


def ffprobe() -> str:
    path = find_tool("ffprobe")
    if not path:
        raise ConversionError(
            "FFprobe was not found. Install FFmpeg with:  winget install Gyan.FFmpeg  and restart the app."
        )
    return path


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
        raise ConversionError(f"Could not read this file: {result.stderr.strip() or 'unknown format'}")
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
        raise ConversionError(detail)
