"""Download videos from links (YouTube, TikTok, Instagram, X, Reddit, Vimeo, and ~1800 other sites) with yt-dlp."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .ffmpeg_tools import Cancelled, ConversionError, ProgressFn, find_tool

MODES = {
    "best": "Video: best quality (MP4)",
    "compatible": "Video: plays everywhere (H.264 MP4)",
    "mp3": "Audio only: MP3 320 kbps",
    "m4a": "Audio only: M4A (original quality)",
}


class _SilentLogger:
    """yt-dlp prints to the console by default; the app reports errors itself."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def _options(mode: str) -> dict:
    if mode == "best":
        # Highest resolution and bitrate available, merged into an MP4 file.
        return {"format": "bv*+ba/b", "merge_output_format": "mp4"}
    if mode == "compatible":
        # Prefer H.264 + AAC, which every phone, TV and editor can open.
        return {
            "format": "bv*+ba/b",
            "format_sort": ["vcodec:h264", "lang", "quality", "res", "fps", "acodec:aac"],
            "merge_output_format": "mp4",
        }
    if mode == "mp3":
        return {
            "format": "ba/b",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
        }
    if mode == "m4a":
        return {
            "format": "ba[ext=m4a]/ba/b",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        }
    raise ValueError(f"Unknown download mode: {mode}")


def _js_runtimes() -> dict:
    # YouTube needs a JavaScript runtime to unlock all formats. Use whichever is installed.
    found = {}
    for name in ("deno", "node", "bun"):
        path = find_tool(name)
        if path:
            found[name] = {"path": path}
    return found


def download(
    url: str,
    out_dir: str | Path,
    mode: str = "best",
    playlist: bool = False,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> list[Path]:
    """Download one link. Returns the saved file(s)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise ConversionError("yt-dlp is not installed. Run:  pip install yt-dlp") from exc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    def on_progress(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if not progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            part = "audio" if (d.get("info_dict") or {}).get("vcodec") == "none" else "video"
            speed = d.get("speed") or 0
            text = f"Downloading {part}" + (f" ({speed / 1_048_576:.1f} MB/s)" if speed else "")
            progress(min(done / total, 0.999) if total else 0.0, text)
        elif d.get("status") == "finished":
            progress(0.999, "Processing")

    def on_postprocess(d: dict) -> None:
        if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
            path = (d.get("info_dict") or {}).get("filepath")
            if path:
                saved.append(Path(path))

    opts = {
        "outtmpl": {"default": str(out_dir / "%(title).150B [%(id)s].%(ext)s")},
        "windowsfilenames": True,
        "noplaylist": not playlist,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
        **_options(mode),
    }
    ffmpeg_path = find_tool("ffmpeg")
    if ffmpeg_path:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadCancelled as exc:
        for p in saved:
            p.unlink(missing_ok=True)
        raise Cancelled() from exc
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).replace("ERROR: ", "").strip()
        raise ConversionError(message) from exc
    if progress:
        progress(1.0, "Done")
    return saved
