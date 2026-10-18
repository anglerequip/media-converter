"""End-to-end check: builds sample files, converts each one to every format it offers, and verifies the results.

    python tests/smoke_test.py            # every conversion at Maximum quality
    python tests/smoke_test.py --quick    # fewer targets, faster
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from converter.engine import Options, convert  # noqa: E402
from converter.ffmpeg_tools import ffmpeg, probe  # noqa: E402
from converter.formats import AUDIO_OUT, kind_of, targets_for  # noqa: E402


def make_samples(folder: Path) -> list[Path]:
    photo = Image.new("RGB", (801, 601))
    d = ImageDraw.Draw(photo)
    for x in range(0, 801, 4):
        d.line([(x, 0), (x, 600)], fill=(x % 256, (x * 3) % 256, 200))
    exif = photo.getexif()
    exif[0x0112] = 6  # "rotate 90" orientation tag, like a phone photo
    photo.save(folder / "photo.jpg", quality=95, exif=exif.tobytes())

    rgba = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).ellipse([20, 20, 280, 180], fill=(220, 40, 40, 255))
    rgba.save(folder / "transparent.png")
    rgba.save(folder / "photo.heic")

    frames = []
    for i in range(12):
        f = Image.new("RGBA", (161, 121), (0, 0, 0, 0))
        ImageDraw.Draw(f).rectangle([i * 10, 30, i * 10 + 40, 90], fill=(30, 120, 250, 255))
        frames.append(f)
    frames[0].save(folder / "anim.gif", save_all=True, append_images=frames[1:], duration=80, loop=0, disposal=2)
    frames[0].save(folder / "sticker.webp", save_all=True, append_images=frames[1:],
                   duration=[60, 120] * 6, loop=0, lossless=True)

    run = lambda *a: subprocess.run([ffmpeg(), "-v", "error", "-y", *a], check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "testsrc2=s=641x361:d=3:r=30", "-f", "lavfi", "-i", "sine=f=440:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "pad=642:362", "-c:a", "aac", "-shortest", str(folder / "clip.mkv"))
    run("-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25", "-c:v", "libvpx-vp9", "-an", str(folder / "noaudio.webm"))
    run("-f", "lavfi", "-i", "sine=f=330:d=3", "-c:a", "flac", "-sample_fmt", "s32", str(folder / "song.flac"))
    return sorted(p for p in folder.iterdir() if p.is_file())


def verify(path: Path, target: str) -> str:
    if path.stat().st_size == 0:
        raise AssertionError("empty file")
    if target in ("png", "jpg", "webp", "avif", "heic", "tiff", "bmp", "gif", "ico", "tga", "apng"):
        with Image.open(path) as im:
            im.load()
            return f"{im.format} {im.size[0]}x{im.size[1]} {im.mode} frames={getattr(im, 'n_frames', 1)}"
    if target == "pdf":
        return f"pdf {path.stat().st_size // 1024} KB"
    info = probe(path)
    parts = []
    for s in info.get("streams", []):
        if s["codec_type"] == "video":
            parts.append(f"{s['codec_name']} {s.get('width')}x{s.get('height')} {s.get('pix_fmt')}")
        elif s["codec_type"] == "audio":
            parts.append(f"{s['codec_name']} {s.get('sample_rate')}Hz")
    if target not in AUDIO_OUT and not any("x" in p for p in parts):
        raise AssertionError("no video stream in output")
    return ", ".join(parts)


def main() -> int:
    quick = "--quick" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="mediaconv_test_"))
    samples_dir = work / "in"
    samples_dir.mkdir()
    samples = make_samples(samples_dir)
    print(f"Working in {work}\n")

    failures = 0
    for src in samples:
        kind = kind_of(src)
        targets = targets_for(src, kind)
        if quick:
            targets = targets[:4]
        print(f"{src.name}  ({kind})")
        for target in targets:
            for codec in (["h264", "h265", "av1"] if (target == "mp4" and kind == "video" and not quick) else ["h264"]):
                opts = Options(quality="max", video_codec=codec, out_dir=work / "out")
                started = time.time()
                try:
                    out = convert(src, target, opts)
                    detail = verify(out, target)
                    print(f"  ok   {target:<5} {codec if target == 'mp4' else '':<5} {time.time() - started:5.1f}s  {detail}")
                except Exception as exc:  # report and keep going
                    if src.name == "noaudio.webm" and target in AUDIO_OUT and "no audio track" in str(exc):
                        print(f"  ok   {target:<5}        refused as expected: {exc}")
                        continue
                    failures += 1
                    print(f"  FAIL {target:<5} {codec if target == 'mp4' else '':<5} {exc}")
        print()
    print(f"{failures} failure(s). Outputs in {work / 'out'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
