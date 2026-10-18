"""Built-in check that the packaged app really works: `MediaConverter.exe --self-test results.json [url ...]`.

Creates small sample files with Pillow and the bundled FFmpeg, runs one conversion for each code path
(still images, HEIC/AVIF, animations, every video codec, audio, GIF), optionally resolves video links
with yt-dlp, and writes the outcome as JSON. Nothing is written outside a temporary folder.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PIL import Image, ImageDraw

from . import __version__
from .engine import Options, convert
from .ffmpeg_tools import _NO_WINDOW, ffmpeg, find_tool

CASES = [
    ("photo.jpg", "png", {}), ("photo.jpg", "webp", {}), ("photo.jpg", "avif", {}), ("photo.jpg", "heic", {}),
    ("photo.jpg", "pdf", {}), ("logo.png", "ico", {}), ("anim.gif", "mp4", {}), ("anim.gif", "webp", {}),
    ("sticker.webp", "gif", {}), ("clip.mkv", "mp4", {"video_codec": "h264", "keep_streams": False}),
    ("clip.mkv", "mp4", {"video_codec": "h265", "keep_streams": False}),
    ("clip.mkv", "mp4", {"video_codec": "av1", "keep_streams": False}), ("clip.mkv", "webm", {}),
    ("clip.mkv", "gif", {}), ("clip.mkv", "mp3", {}), ("song.flac", "opus", {}), ("song.flac", "m4a", {}),
]


def _samples(folder: Path) -> None:
    photo = Image.new("RGB", (320, 240))
    ImageDraw.Draw(photo).ellipse([40, 30, 280, 210], fill=(57, 135, 215))
    photo.save(folder / "photo.jpg", quality=95)
    logo = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle([20, 20, 108, 108], fill=(220, 40, 40, 255))
    logo.save(folder / "logo.png")
    frames = []
    for i in range(8):
        f = Image.new("RGBA", (96, 64), (0, 0, 0, 0))
        ImageDraw.Draw(f).rectangle([i * 8, 16, i * 8 + 24, 48], fill=(30, 120, 250, 255))
        frames.append(f)
    frames[0].save(folder / "anim.gif", save_all=True, append_images=frames[1:], duration=80, loop=0, disposal=2)
    frames[0].save(folder / "sticker.webp", save_all=True, append_images=frames[1:], duration=60, loop=0, lossless=True)
    run = lambda *a: subprocess.run([ffmpeg(), "-v", "error", "-y", *a], check=True,  # noqa: E731
                                    stdin=subprocess.DEVNULL, capture_output=True, creationflags=_NO_WINDOW)
    run("-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25", "-f", "lavfi", "-i", "sine=f=440:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(folder / "clip.mkv"))
    run("-f", "lavfi", "-i", "sine=f=330:d=2", "-c:a", "flac", str(folder / "song.flac"))


def run(out_file: str, urls: list[str]) -> int:
    report: dict = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                    "ffmpeg": find_tool("ffmpeg"), "ffprobe": find_tool("ffprobe"), "conversions": [], "links": []}
    failures = 0
    with tempfile.TemporaryDirectory(prefix="mediaconv_selftest_") as tmp:
        folder = Path(tmp)
        try:
            _samples(folder)
        except Exception:
            report["sample_error"] = traceback.format_exc()
            Path(out_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 1
        for name, target, extra in CASES:
            started = time.time()
            entry = {"input": name, "target": target, **{k: v for k, v in extra.items() if k == "video_codec"}}
            try:
                result = convert(folder / name, target, Options(out_dir=folder / "out", **extra))
                entry.update(ok=True, bytes=result.stat().st_size, seconds=round(time.time() - started, 2))
            except Exception as exc:
                failures += 1
                entry.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:400])
            report["conversions"].append(entry)

    if urls:
        import yt_dlp

        report["yt_dlp"] = yt_dlp.version.__version__
        from . import spotify

        for url in urls:
            if spotify.is_spotify(url):  # read Spotify and find the YouTube Music match, without downloading
                try:
                    collection = spotify.read(url)
                    match = spotify.find_match(collection.tracks[0])
                    report["links"].append({"url": url, "ok": True, "title": collection.tracks[0].title,
                                            "match": f"{match.get('track') or match.get('title')} / "
                                                     f"{', '.join(match.get('artists') or [])}",
                                            "songs": len(collection.tracks)})
                except Exception as exc:
                    failures += 1
                    report["links"].append({"url": url, "ok": False, "error": str(exc)[:300]})
                continue
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "simulate": True, "format": "bv*+ba/b",
                                       "logger": _Quiet()}) as ydl:
                    info = ydl.extract_info(url, download=False)
                report["links"].append({"url": url, "ok": True, "title": info.get("title"),
                                        "height": info.get("height"), "formats": len(info.get("formats") or [])})
            except Exception as exc:
                failures += 1
                report["links"].append({"url": url, "ok": False, "error": str(exc)[:300]})

    report["failures"] = failures
    Path(out_file).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failures else 0


class _Quiet:
    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug
