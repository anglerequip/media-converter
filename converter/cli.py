"""Command-line interface.

    python -m converter convert photo.webp --to png
    python -m converter convert clip.gif sticker.webp --to mp4 --quality max
    python -m converter download "https://www.youtube.com/watch?v=..." --mode best
    python -m converter formats photo.heic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import spotify
from .downloader import MODES, download
from .engine import QUALITIES, VIDEO_CODECS, Options, convert
from .ffmpeg_tools import Cancelled, ConversionError
from .formats import kind_of, targets_for


def _bar(fraction: float, text: str) -> None:
    filled = int(fraction * 30)
    sys.stdout.write(f"\r  [{'#' * filled}{'.' * (30 - filled)}] {fraction * 100:5.1f}%  {text:<32}")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="converter", description="Convert images, GIFs, video and audio.")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="convert files")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--to", required=True, help="target format, e.g. png, mp4, gif, mp3")
    c.add_argument("--quality", choices=QUALITIES, default="max")
    c.add_argument("--codec", choices=list(VIDEO_CODECS), default="h264", help="video codec for mp4/mkv/mov")
    c.add_argument("--reencode", action="store_true", help="always re-encode instead of copying streams")
    c.add_argument("--out", type=Path, help="output folder (default: next to each file)")

    d = sub.add_parser("download", help="download a video from a link")
    d.add_argument("urls", nargs="+")
    d.add_argument("--mode", choices=list(MODES), default="best")
    d.add_argument("--playlist", action="store_true", help="download the whole playlist")
    d.add_argument("--out", type=Path, default=Path.home() / "Downloads")

    f = sub.add_parser("formats", help="list what a file can be converted to")
    f.add_argument("file", type=Path)

    args = parser.parse_args(argv)
    failures = 0
    # Video titles often contain emoji; don't let an old console code page crash the run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if args.command == "formats":
        kind = kind_of(args.file)
        print(f"{args.file.name}: {kind or 'unsupported'}")
        print("  " + ", ".join(targets_for(args.file, kind)))
        return 0 if kind else 1

    if args.command == "convert":
        opts = Options(quality=args.quality, video_codec=args.codec, keep_streams=not args.reencode, out_dir=args.out)
        for file in args.files:
            print(f"{file.name} -> {args.to.upper()}")
            try:
                out = convert(file, args.to, opts, progress=_bar)
                print(f"\n  saved {out}")
            except (ConversionError, Cancelled) as exc:
                failures += 1
                print(f"\n  FAILED: {exc}")
    else:
        for url in args.urls:
            print(url)
            try:
                link = spotify.parse_link(url)
                if link and link[0] in ("track", "album", "playlist"):
                    fmt = args.mode if args.mode in ("mp3", "m4a") else "m4a"
                    result = spotify.download(url, args.out, fmt, progress=_bar)
                    for out in result.saved:
                        print(f"\n  saved {out}")
                    for line in result.no_match + result.failed:
                        failures += 1
                        print(f"\n  skipped {line}")
                    continue
                for out in download(url, args.out, args.mode, args.playlist, progress=_bar):
                    print(f"\n  saved {out}")
            except (ConversionError, Cancelled) as exc:
                failures += 1
                print(f"\n  FAILED: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
