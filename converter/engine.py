"""The conversion engine.

Still images go through Pillow. Anything with a timeline (video, audio, animations)
goes through FFmpeg. Animated GIF/WebP/APNG frames are decoded by Pillow, because it
handles frame disposal and per-frame timing correctly, then handed to FFmpeg.
"""

from __future__ import annotations

import io
import math
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, ImageSequence

from .ffmpeg_tools import Cancelled, ConversionError, ProgressFn, duration_of, probe, run_ffmpeg
from .formats import ANIMATION_OUT, AUDIO_OUT, STILL_OUT, kind_of, normalise_ext, targets_for

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False

Image.MAX_IMAGE_PIXELS = None  # allow very large photos and scans

QUALITIES = ["lossless", "max", "high", "balanced", "small"]
QUALITY_LABELS = {
    "lossless": "Lossless (largest files)",
    "max": "Maximum",
    "high": "High",
    "balanced": "Balanced",
    "small": "Small file",
}
VIDEO_CODECS = {"h264": "H.264 (plays everywhere)", "h265": "H.265 / HEVC (smaller)", "av1": "AV1 (smallest, slower)"}


@dataclass
class Options:
    quality: str = "max"
    video_codec: str = "h264"
    keep_streams: bool = True  # copy audio/video streams untouched when the target allows it
    out_dir: Path | None = None  # None = next to the source file
    background: tuple[int, int, int] = (255, 255, 255)  # used where transparency is not supported


def _noop(_f: float, _t: str) -> None:
    pass


def output_path(src: Path, ext: str, out_dir: Path | None) -> Path:
    folder = Path(out_dir) if out_dir else src.parent
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{src.stem}.{ext}"
    n = 1
    while candidate.exists():
        candidate = folder / f"{src.stem} ({n}).{ext}"
        n += 1
    return candidate


def convert(
    src: str | Path,
    target: str,
    opts: Options | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Convert one file. Returns the path of the new file."""
    src = Path(src)
    opts = opts or Options()
    progress = progress or _noop
    target = normalise_ext(target)
    if not src.is_file():
        raise ConversionError(f"File not found: {src}")
    kind = kind_of(src)
    if kind is None:
        raise ConversionError(f"Unsupported file type: {src.suffix or 'no extension'}")
    if target not in targets_for(src, kind):
        raise ConversionError(f"A {src.suffix} file cannot be converted to {target.upper()}.")

    out = output_path(src, target, opts.out_dir)
    progress(0.0, "Starting")
    try:
        if kind == "image" or (kind == "animation" and target not in ANIMATION_OUT):
            _image_to_image(src, out, target, opts)
        elif kind == "animation" and target in ("webp", "apng"):
            _animation_with_pillow(src, out, target, opts, cancel)
        elif kind == "animation":
            _animation_with_ffmpeg(src, out, target, opts, progress, cancel)
        elif target in AUDIO_OUT:
            _to_audio(src, out, target, opts, progress, cancel)
        elif target == "gif":
            _video_to_gif(src, out, opts, progress, cancel)
        elif target in ("webp", "apng"):
            _video_to_animated_image(src, out, target, opts, progress, cancel)
        else:
            _video_to_video(src, out, target, opts, progress, cancel)
    except (Cancelled, ConversionError):
        out.unlink(missing_ok=True)
        raise
    except Exception as exc:  # Pillow and OS errors: report them in plain words
        out.unlink(missing_ok=True)
        raise ConversionError(f"{type(exc).__name__}: {exc}") from exc
    progress(1.0, "Done")
    return out


# --------------------------------------------------------------------------- still images

ALPHA_TARGETS = {"png", "webp", "avif", "heic", "tiff", "gif", "ico", "tga"}


def _flatten(img: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    rgba = img.convert("RGBA")
    base = Image.new("RGB", rgba.size, bg)
    base.paste(rgba, mask=rgba.getchannel("A"))
    return base


def _prepare(img: Image.Image, target: str, bg: tuple[int, int, int]) -> tuple[Image.Image, bytes | None]:
    """Bring an image into a mode the target format can store. Returns (image, icc_profile)."""
    icc = img.info.get("icc_profile")
    mode = img.mode

    if mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        if target in ("png", "tiff") and mode.startswith("I;16"):
            return img, icc  # keep 16-bit depth where the format allows it
        img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L") if mode != "F" else img.convert("L")
    elif mode == "CMYK":
        converted = None
        if icc:
            try:
                converted = ImageCms.profileToProfile(
                    img, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB"
                )
            except Exception:
                converted = None
        img = converted or img.convert("RGB")
        icc = None  # the CMYK profile no longer describes the pixels
    elif mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif mode == "PA":
        img = img.convert("RGBA")
    elif mode == "1":
        img = img.convert("L")
    elif mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in mode else "RGB")

    if img.mode in ("RGBA", "LA") and target not in ALPHA_TARGETS:
        img = _flatten(img, bg)
    if target in ("webp", "avif", "heic", "ico") and img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if img.mode == "LA" else "RGB")
    if target == "tga" and img.mode == "LA":
        img = img.convert("RGBA")
    return img, icc


def _extra(icc: bytes | None, exif: bytes | None) -> dict:
    kw = {}
    if icc:
        kw["icc_profile"] = icc
    if exif:
        kw["exif"] = exif
    return kw


def _image_to_image(src: Path, out: Path, target: str, opts: Options) -> None:
    q = opts.quality
    with Image.open(src) as opened:
        opened.seek(0)
        img = ImageOps.exif_transpose(opened)  # bake in phone rotation, drop the orientation tag
        img.load()
        exif_obj = img.getexif()
        exif = exif_obj.tobytes() if len(exif_obj) else None
        img, icc = _prepare(img, target, opts.background)

    if target == "jpg":
        quality, sub = {"lossless": (100, 0), "max": (100, 0), "high": (95, 0), "balanced": (88, 2), "small": (75, 2)}[q]
        img.save(out, "JPEG", quality=quality, subsampling=sub, optimize=True, **_extra(icc, exif))
    elif target == "png":
        img.save(out, "PNG", compress_level=9 if q == "small" else 6, **_extra(icc, exif))
    elif target == "webp":
        if q in ("lossless", "max"):
            img.save(out, "WEBP", lossless=True, quality=100, method=5, exact=True, **_extra(icc, exif))
        else:
            quality = {"high": 95, "balanced": 85, "small": 75}[q]
            img.save(out, "WEBP", quality=quality, method=6, **_extra(icc, exif))
    elif target == "avif":
        quality = {"lossless": 100, "max": 100, "high": 90, "balanced": 75, "small": 60}[q]
        sub = "4:4:4" if q in ("lossless", "max", "high") else "4:2:0"
        img.save(out, "AVIF", quality=quality, subsampling=sub, speed=4, **_extra(icc, exif))
    elif target == "heic":
        if not HEIF_OK:
            raise ConversionError("HEIC support is missing. Run:  pip install pillow-heif")
        quality = {"lossless": -1, "max": 100, "high": 92, "balanced": 80, "small": 65}[q]
        kw = {"chroma": 444} if q in ("lossless", "max") else {}
        img.save(out, "HEIF", quality=quality, **kw, **_extra(icc, exif))
    elif target == "tiff":
        img.save(out, "TIFF", compression="tiff_lzw", **_extra(icc, None))
    elif target == "bmp":
        img.save(out, "BMP")
    elif target == "gif":
        img.save(out, "GIF", optimize=True)
    elif target == "tga":
        img.save(out, "TGA", rle=True)
    elif target == "ico":
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img.convert("RGBA"), ((side - img.width) // 2, (side - img.height) // 2))
        sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= side] or [(side, side)]
        square.save(out, "ICO", sizes=sizes)
    elif target == "pdf":
        dpi = img.info.get("dpi", (72, 72))[0] or 72
        img.save(out, "PDF", resolution=float(dpi), quality=100 if q in ("lossless", "max") else 95, subsampling=0)
    else:
        raise ConversionError(f"Unknown image format: {target}")


# --------------------------------------------------------------------------- animations (GIF / WebP / APNG input)


def _frame_duration(frame: Image.Image, fallback: int) -> int:
    d = frame.info.get("duration", fallback) or 100
    # Browsers show frames of 10 ms or less for 100 ms; match that so playback speed looks the same.
    return 100 if d <= 10 else int(d)


def _animation_with_pillow(src: Path, out: Path, target: str, opts: Options, cancel: threading.Event | None) -> None:
    """Animated input to animated WebP or APNG, frame-exact, with transparency."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    with Image.open(src) as im:
        loop = im.info.get("loop", 0)
        fallback = im.info.get("duration", 100)
        for frame in ImageSequence.Iterator(im):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            frames.append(frame.convert("RGBA"))
            durations.append(_frame_duration(frame, fallback))
    first, rest = frames[0], frames[1:]
    q = opts.quality
    if target == "webp":
        if q in ("lossless", "max"):
            first.save(out, "WEBP", save_all=True, append_images=rest, duration=durations, loop=loop,
                       lossless=True, quality=100, method=4)
        else:
            quality = {"high": 95, "balanced": 85, "small": 75}[q]
            first.save(out, "WEBP", save_all=True, append_images=rest, duration=durations, loop=loop,
                       quality=quality, method=4)
    else:
        from PIL.PngImagePlugin import Blend, Disposal

        first.save(out, "PNG", save_all=True, append_images=rest, duration=durations, loop=loop,
                   disposal=Disposal.OP_NONE, blend=Blend.OP_SOURCE, compress_level=6)


def _animation_with_ffmpeg(
    src: Path, out: Path, target: str, opts: Options, progress: ProgressFn, cancel: threading.Event | None
) -> None:
    """Animated input to a video file or GIF. Frames go through a temp folder so long animations don't fill RAM."""
    keeps_alpha = target in ("webm", "gif")
    with tempfile.TemporaryDirectory(prefix="mediaconv_") as tmp_name:
        tmp = Path(tmp_name)
        entries: list[str] = []
        durations: list[int] = []
        has_alpha = False
        name = ""
        with Image.open(src) as im:
            total_frames = getattr(im, "n_frames", 1)
            fallback = im.info.get("duration", 100)
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                rgba = frame.convert("RGBA")
                if rgba.getchannel("A").getextrema()[0] < 255:
                    has_alpha = True
                pic = rgba if keeps_alpha else _flatten(rgba, opts.background)
                name = f"f{i:06d}.png"
                pic.save(tmp / name, compress_level=1)
                d = _frame_duration(frame, fallback)
                durations.append(d)
                # "framerate 1000" gives millisecond timestamps; the default (25 fps) rounds delays to 40 ms.
                entries.append(f"file '{name}'\noption framerate 1000\nduration {d / 1000:.3f}")
                progress(0.4 * (i + 1) / total_frames, "Reading frames")
        total = sum(durations) / 1000
        stage = lambda f, t: progress(0.4 + 0.6 * f, "Encoding")  # noqa: E731
        listing = tmp / "frames.txt"

        if target == "gif":
            # The GIF muxer takes each frame's delay from the "duration" lines, including the last one.
            listing.write_text("\n".join(entries), encoding="utf-8")
            vf = _gif_filtergraph(None, None, opts.quality, has_alpha)
            args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-filter_complex", vf, "-loop", "0"]
            run_ffmpeg([*args, str(out)], out, total, stage, cancel)
            _set_last_gif_delay(out, durations[-1])
            return

        # Video encoders need the last frame repeated for its duration to count; "-t" then trims the
        # repeat off so the output is exactly as long as the source.
        entries.append(f"file '{name}'\noption framerate 1000")
        listing.write_text("\n".join(entries), encoding="utf-8")
        input_args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-t", f"{total:.3f}"]

        # Video players want a constant frame rate. Use one that every frame duration divides into
        # (e.g. 60 ms + 120 ms frames -> 1000/60 fps), capped at 60 fps.
        step = 0
        for d in durations:
            step = math.gcd(step, d)
        fps = f"1000/{step}" if 1000 / step <= 60 else "60"
        filters = [f"fps={fps}", "crop=trunc(iw/2)*2:trunc(ih/2)*2"]
        codec = _pick_codec(target, opts.video_codec)
        args = [*input_args, "-vf", ",".join(filters), *_video_encoder(codec, opts.quality, target, None, has_alpha and keeps_alpha), "-an"]
        if target in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
        run_ffmpeg([*args, str(out)], out, total, stage, cancel)


def _set_last_gif_delay(path: Path, delay_ms: int) -> None:
    """FFmpeg's GIF muxer reuses the previous delay for the final frame. Patch in the real one."""
    data = bytearray(path.read_bytes())

    def skip_sub_blocks(pos: int) -> int:
        while pos < len(data) and data[pos] != 0:
            pos += data[pos] + 1
        return pos + 1

    pos = 13
    if data[10] & 0x80:  # global colour table
        pos += 3 * (2 ** ((data[10] & 0x07) + 1))
    last_gce = None
    while pos < len(data):
        block = data[pos]
        if block == 0x21:  # extension
            if data[pos + 1] == 0xF9:
                last_gce = pos
            pos = skip_sub_blocks(pos + 2)
        elif block == 0x2C:  # image descriptor
            packed = data[pos + 9]
            pos += 10
            if packed & 0x80:  # local colour table
                pos += 3 * (2 ** ((packed & 0x07) + 1))
            pos = skip_sub_blocks(pos + 1)
        else:  # 0x3B trailer, or something unexpected: stop
            break
    if last_gce is not None:
        centis = max(2, round(delay_ms / 10))
        data[last_gce + 4] = centis & 0xFF
        data[last_gce + 5] = (centis >> 8) & 0xFF
        path.write_bytes(data)


# --------------------------------------------------------------------------- video


def _streams(info: dict, kind: str) -> list[dict]:
    result = []
    for s in info.get("streams", []):
        if s.get("codec_type") != kind:
            continue
        if kind == "video" and s.get("disposition", {}).get("attached_pic"):
            continue
        result.append(s)
    return result


def _cover_art(info: dict) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") and s.get("codec_name") in ("mjpeg", "png"):
            return s
    return None


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            value = float(Fraction(stream.get(key, "0/1")))
            if 0 < value < 1000:
                return value
        except (ValueError, ZeroDivisionError):
            pass
    return 30.0


def _pick_codec(target: str, choice: str) -> str:
    if target == "webm":
        return "av1" if choice == "av1" else "vp9"
    if target == "avi":
        return "mpeg4"
    if target == "mov" and choice == "av1":
        return "h265"
    return choice


_CRF = {
    "h264": {"max": 14, "high": 18, "balanced": 23, "small": 28},
    "h265": {"max": 16, "high": 21, "balanced": 26, "small": 30},
    "av1": {"max": 20, "high": 27, "balanced": 34, "small": 42},
    "vp9": {"max": 18, "high": 24, "balanced": 31, "small": 38},
}
_FFPROBE_NAME = {"h264": "h264", "h265": "hevc", "av1": "av1", "vp9": "vp9", "mpeg4": "mpeg4"}
_CONTAINER_VIDEO = {
    "mp4": {"h264", "hevc", "av1", "mpeg4", "vp9"},
    "mov": {"h264", "hevc", "mpeg4", "prores"},
    "mkv": None,  # anything
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"mpeg4", "h264", "mjpeg"},
}
_CONTAINER_AUDIO = {
    "mp4": {"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac"},
    "mov": {"aac", "mp3", "alac", "ac3", "pcm_s16le", "pcm_s24le"},
    "mkv": None,
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "ac3", "pcm_s16le"},
}


def _video_encoder(codec: str, q: str, target: str, src_pix_fmt: str | None, alpha: bool = False) -> list[str]:
    high_bit = bool(src_pix_fmt) and any(x in src_pix_fmt for x in ("10", "12"))
    slow = q in ("lossless", "max", "high")
    if codec == "h264":
        if q == "lossless":
            return ["-c:v", "libx264", "-preset", "slow", "-qp", "0", "-pix_fmt", "yuv444p"]
        return ["-c:v", "libx264", "-preset", "slow" if slow else "medium", "-crf", str(_CRF["h264"][q]), "-pix_fmt", "yuv420p"]
    if codec == "h265":
        tag = ["-tag:v", "hvc1"] if target in ("mp4", "mov") else []  # lets Apple devices play it
        if q == "lossless":
            return ["-c:v", "libx265", "-preset", "slow", "-x265-params", "lossless=1:log-level=error", "-pix_fmt", "yuv444p", *tag]
        pix = "yuv420p10le" if high_bit else "yuv420p"
        return ["-c:v", "libx265", "-preset", "slow" if slow else "medium", "-crf", str(_CRF["h265"][q]),
                "-x265-params", "log-level=error", "-pix_fmt", pix, *tag]
    if codec == "av1":
        if q == "lossless":
            return ["-c:v", "libaom-av1", "-aom-params", "lossless=1", "-cpu-used", "6", "-row-mt", "1", "-pix_fmt", "yuv444p"]
        preset = {"max": "4", "high": "5", "balanced": "7", "small": "8"}[q]
        pix = "yuv420p10le" if high_bit else "yuv420p"
        return ["-c:v", "libsvtav1", "-preset", preset, "-crf", str(_CRF["av1"][q]), "-pix_fmt", pix]
    if codec == "vp9":
        pix = "yuva420p" if alpha else "yuv420p"
        if q == "lossless":
            return ["-c:v", "libvpx-vp9", "-lossless", "1", "-row-mt", "1", "-pix_fmt", pix]
        return ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(_CRF["vp9"][q]), "-row-mt", "1",
                "-deadline", "good", "-cpu-used", "2" if slow else "4", "-pix_fmt", pix]
    if codec == "mpeg4":
        qv = {"lossless": "1", "max": "2", "high": "3", "balanced": "5", "small": "8"}[q]
        return ["-c:v", "mpeg4", "-vtag", "xvid", "-q:v", qv]
    raise ConversionError(f"Unknown video codec: {codec}")


def _container_audio(target: str, q: str) -> list[str]:
    rate = {"lossless": "320k", "max": "320k", "high": "256k", "balanced": "192k", "small": "128k"}[q]
    if target in ("mp4", "mov"):
        return ["-c:a", "alac"] if q == "lossless" else ["-c:a", "aac", "-b:a", rate]
    if target == "mkv":
        return ["-c:a", "flac"] if q == "lossless" else ["-c:a", "aac", "-b:a", rate]
    if target == "webm":
        opus = {"lossless": "256k", "max": "256k", "high": "192k", "balanced": "128k", "small": "96k"}[q]
        return ["-c:a", "libopus", "-b:a", opus]
    if target == "avi":
        return ["-c:a", "libmp3lame", "-b:a", rate]
    return []


def _video_to_video(src: Path, out: Path, target: str, opts: Options, progress: ProgressFn, cancel) -> None:
    info = probe(src)
    videos, audios = _streams(info, "video"), _streams(info, "audio")
    if not videos:
        raise ConversionError("This file contains no video track. Try converting it to an audio format instead.")
    v = videos[0]
    same_container = normalise_ext(src.suffix) == target
    codec = _pick_codec(target, opts.video_codec)

    args = ["-i", str(src), "-map", f"0:{v['index']}"]
    if audios:
        args += ["-map", "0:a"]
    args += ["-map_metadata", "0", "-sn", "-dn"]

    allowed_v = _CONTAINER_VIDEO[target]
    copy_video = (
        opts.keep_streams
        and not same_container
        and v.get("codec_name") == _FFPROBE_NAME[codec]
        and (allowed_v is None or v.get("codec_name") in allowed_v)
    )
    if copy_video:
        args += ["-c:v", "copy"]
        if v.get("codec_name") == "hevc" and target in ("mp4", "mov"):
            args += ["-tag:v", "hvc1"]
    else:
        args += ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2", *_video_encoder(codec, opts.quality, target, v.get("pix_fmt"))]

    if audios:
        allowed_a = _CONTAINER_AUDIO[target]
        copy_audio = opts.keep_streams and not same_container and (
            allowed_a is None or all(a.get("codec_name") in allowed_a for a in audios)
        )
        args += ["-c:a", "copy"] if copy_audio else _container_audio(target, opts.quality)
    if target in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    run_ffmpeg([*args, str(out)], out, duration_of(info), progress, cancel)


def _gif_filtergraph(fps: float | None, max_width: int | None, q: str, alpha: bool) -> str:
    pre = []
    if fps:
        pre.append(f"fps={fps:g}")
    if max_width:
        pre.append(f"scale='min({max_width},iw)':-1:flags=lanczos")
    # "single" builds a fresh 256-colour palette for every frame: best colours, bigger file.
    stats = {"lossless": "single", "max": "single", "high": "full", "balanced": "diff", "small": "diff"}[q]
    dither = "sierra2_4a" if q in ("lossless", "max", "high") else "bayer:bayer_scale=3"
    use = f"paletteuse=dither={dither}"
    if stats == "single":
        use += ":new=1"
    if stats == "diff":
        use += ":diff_mode=rectangle"
    if alpha:
        use += ":alpha_threshold=128"
    gen = f"palettegen=max_colors=256:stats_mode={stats}:reserve_transparent={1 if alpha else 0}"
    head = ",".join(pre) + "," if pre else ""
    return f"[0:v]{head}split[a][b];[a]{gen}[p];[b][p]{use}"


def _video_to_gif(src: Path, out: Path, opts: Options, progress: ProgressFn, cancel) -> None:
    info = probe(src)
    videos = _streams(info, "video")
    if not videos:
        raise ConversionError("This file contains no video track.")
    q = opts.quality
    fps_cap = {"lossless": 50, "max": 50, "high": 25, "balanced": 20, "small": 12}[q]
    width = {"lossless": None, "max": None, "high": None, "balanced": 720, "small": 480}[q]
    fps = min(_fps(videos[0]), fps_cap)
    graph = _gif_filtergraph(fps, width, q, alpha=False)
    run_ffmpeg(["-i", str(src), "-filter_complex", graph, "-loop", "0", str(out)], out, duration_of(info), progress, cancel)


def _video_to_animated_image(src: Path, out: Path, target: str, opts: Options, progress: ProgressFn, cancel) -> None:
    info = probe(src)
    videos = _streams(info, "video")
    if not videos:
        raise ConversionError("This file contains no video track.")
    q = opts.quality
    fps_cap = {"lossless": 60, "max": 60, "high": 30, "balanced": 20, "small": 15}[q]
    width = {"lossless": None, "max": None, "high": None, "balanced": 720, "small": 480}[q]
    filters = [f"fps={min(_fps(videos[0]), fps_cap):g}"]
    if width:
        filters.append(f"scale='min({width},iw)':-2:flags=lanczos")
    args = ["-i", str(src), "-map", f"0:{videos[0]['index']}", "-vf", ",".join(filters), "-an"]
    if target == "webp":
        quality = {"lossless": "100", "max": "95", "high": "90", "balanced": "80", "small": "70"}[q]
        args += ["-c:v", "libwebp_anim", "-lossless", "1" if q == "lossless" else "0", "-q:v", quality,
                 "-compression_level", "4", "-loop", "0"]
    else:
        args += ["-c:v", "apng", "-plays", "0", "-f", "apng"]
    run_ffmpeg([*args, str(out)], out, duration_of(info), progress, cancel)


# --------------------------------------------------------------------------- audio

_AUDIO_COPYABLE = {
    "mp3": {"mp3"},
    "m4a": {"aac", "alac"},
    "aac": {"aac"},
    "ogg": {"vorbis", "opus"},
    "opus": {"opus"},
    "flac": {"flac"},
    "wma": {"wmav1", "wmav2"},
    "wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8"},
    "aiff": {"pcm_s16be", "pcm_s24be"},
}
_LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape", "tta", "truehd", "mlp"}


def _audio_encoder(target: str, q: str, stream: dict) -> list[str]:
    if target == "mp3":
        return {
            "lossless": ["-c:a", "libmp3lame", "-b:a", "320k"],
            "max": ["-c:a", "libmp3lame", "-b:a", "320k"],
            "high": ["-c:a", "libmp3lame", "-q:a", "0"],
            "balanced": ["-c:a", "libmp3lame", "-q:a", "2"],
            "small": ["-c:a", "libmp3lame", "-q:a", "5"],
        }[q]
    if target in ("m4a", "aac"):
        if target == "m4a" and q == "lossless":
            return ["-c:a", "alac"]
        rate = {"lossless": "320k", "max": "320k", "high": "256k", "balanced": "192k", "small": "128k"}[q]
        return ["-c:a", "aac", "-b:a", rate]
    if target == "ogg":
        return ["-c:a", "libvorbis", "-q:a", {"lossless": "10", "max": "10", "high": "8", "balanced": "6", "small": "4"}[q]]
    if target == "opus":
        return ["-c:a", "libopus", "-b:a", {"lossless": "256k", "max": "256k", "high": "192k", "balanced": "128k", "small": "96k"}[q]]
    if target == "flac":
        return ["-c:a", "flac", "-compression_level", "8"]
    if target in ("wav", "aiff"):
        codec = stream.get("codec_name", "")
        depth = int(stream.get("bits_per_raw_sample") or 0) or int(stream.get("bits_per_sample") or 0)
        deep = depth > 16 and (codec.startswith("pcm_") or codec in _LOSSLESS_CODECS)
        if target == "wav":
            return ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
        return ["-c:a", "pcm_s24be" if deep else "pcm_s16be"]
    if target == "wma":
        return ["-c:a", "wmav2", "-b:a", {"lossless": "192k", "max": "192k", "high": "192k", "balanced": "160k", "small": "128k"}[q]]
    raise ConversionError(f"Unknown audio format: {target}")


def _to_audio(src: Path, out: Path, target: str, opts: Options, progress: ProgressFn, cancel) -> None:
    info = probe(src)
    audios = _streams(info, "audio")
    if not audios:
        raise ConversionError("This file has no audio track.")
    a = audios[0]
    args = ["-i", str(src), "-map", f"0:{a['index']}", "-map_metadata", "0"]

    copy = (
        opts.keep_streams
        and normalise_ext(src.suffix) != target
        and a.get("codec_name") in _AUDIO_COPYABLE[target]
    )
    args += ["-c:a", "copy"] if copy else _audio_encoder(target, opts.quality, a)

    cover = _cover_art(info)
    if cover and target in ("mp3", "m4a", "flac"):
        args += ["-map", f"0:{cover['index']}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    else:
        args += ["-vn"]
    if target == "mp3":
        args += ["-id3v2_version", "3"]
    if target == "aac":
        args += ["-f", "adts"]
    run_ffmpeg([*args, str(out)], out, duration_of(info), progress, cancel)
