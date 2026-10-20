"""Download videos from links (YouTube, TikTok, Instagram, X, Reddit, Vimeo, and ~1800 other sites) with yt-dlp."""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from glob import escape as glob_escape

from .ffmpeg_tools import Cancelled, ConversionError, ProgressFn, find_tool

MODES = {
    "best": "Video: best quality (MP4)",
    "compatible": "Video: plays everywhere (H.264 MP4)",
    "mp3": "Audio only: MP3 320 kbps",
    "m4a": "Audio only: M4A (original quality)",
}


LOCAL_NAMES = ("localhost", ".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


class UnsafeLink(ConversionError):
    """A link the app refuses to open, with a message meant for the user."""


def _is_internal(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def check_url(url: str, resolve: bool = False) -> None:
    """Refuse anything but ordinary web links: other schemes (file:, ftp:...), links with a password in them,
    and links to this computer or the local network, such as a router's settings page. With resolve=True the
    name is also looked up, which catches numeric forms like http://2130706433/ and names that point inside."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        parts.port  # noqa: B018  (raises for a port that isn't a number)
    except ValueError as exc:
        raise UnsafeLink("That link isn't a valid web address.") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeLink("Only web links (starting with https:// or http://) can be downloaded.")
    if parts.username or parts.password:
        raise UnsafeLink("Links with a user name or password in them aren't supported.")
    if not host:
        raise UnsafeLink("That link has no website name in it.")
    local = "Links to this computer or your local network aren't supported, only public websites."
    if host == "localhost" or host.endswith(LOCAL_NAMES) or _is_internal(host):
        raise UnsafeLink(local)
    if resolve:
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
        except OSError:
            return  # offline or unknown name: the download itself reports that
        if any(_is_internal(info[4][0]) for info in infos):
            raise UnsafeLink(local)


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
    """Download one link. Returns the saved file(s). If it is cancelled or fails, everything this download
    wrote (finished files and partial ones) is removed again; files that were already there are left alone."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise ConversionError("yt-dlp is not installed. Run:  pip install yt-dlp") from exc

    check_url(url, resolve=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    partial: set[Path] = set()
    started = time.time()

    def on_progress(d: dict) -> None:
        for key in ("tmpfilename", "filename"):
            if d.get(key):
                partial.add(Path(d[key]))
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
        # Fetching in 10 MB pieces avoids most of YouTube's occasional "403 Forbidden" refusals.
        "http_chunk_size": 10 * 1024 * 1024,
        "updatetime": False,  # a new file is dated today, not with the site's upload date
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

    def clean_up() -> None:
        for p in partial | set(saved):
            candidates = [p, Path(f"{p}.part"), Path(f"{p}.ytdl"), *p.parent.glob(f"{glob_escape(p.name)}.part-Frag*")]
            for c in candidates:
                try:
                    # Only what this download wrote: partial files always, finished ones only if new.
                    if c.name.endswith((".part", ".ytdl")) or ".part-Frag" in c.name or c.stat().st_mtime >= started - 2:
                        c.unlink(missing_ok=True)
                except OSError:
                    pass

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadCancelled as exc:
        clean_up()
        raise Cancelled() from exc
    except yt_dlp.utils.DownloadError as exc:
        clean_up()
        message = str(exc).replace("ERROR: ", "").strip()
        raise ConversionError(message) from exc
    except BaseException:
        clean_up()
        raise
    if progress:
        progress(1.0, "Done")
    return saved
