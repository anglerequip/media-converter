"""Which formats the app reads, and what each kind of file can be converted to."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

IMAGE_IN = {
    "png", "apng", "jpg", "jpeg", "jfif", "jpe", "webp", "gif", "bmp", "dib", "tif", "tiff",
    "ico", "avif", "heic", "heif", "tga", "ppm", "pgm", "pbm", "pnm", "psd", "dds", "pcx", "jp2", "qoi",
}
# Formats that may hold an animation; we open them to find out.
MAYBE_ANIMATED = {"gif", "webp", "png", "apng", "avif"}
VIDEO_IN = {
    "mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "wmv", "mpg", "mpeg", "m2v", "3gp", "3g2",
    "ts", "mts", "m2ts", "ogv", "vob", "asf", "f4v", "divx", "rm", "rmvb", "mxf",
}
AUDIO_IN = {
    "mp3", "wav", "flac", "aac", "m4a", "ogg", "oga", "opus", "wma", "aiff", "aif", "aifc",
    "alac", "amr", "ac3", "eac3", "dts", "ape", "wv", "mka", "caf", "mp2", "m4b", "au",
}

STILL_OUT = ["png", "jpg", "webp", "avif", "heic", "tiff", "bmp", "gif", "ico", "tga", "pdf"]
ANIMATION_OUT = ["mp4", "webm", "mov", "mkv", "gif", "webp", "apng", "avi"]
VIDEO_OUT = ["mp4", "mkv", "mov", "webm", "avi", "gif", "webp", "apng"]
AUDIO_OUT = ["mp3", "m4a", "wav", "flac", "ogg", "opus", "aac", "aiff", "wma"]

DESCRIPTIONS = {
    "png": "lossless image",
    "jpg": "photo",
    "webp": "image or animation",
    "avif": "modern image",
    "heic": "iPhone photo",
    "tiff": "lossless image",
    "bmp": "bitmap",
    "gif": "image or animation",
    "ico": "Windows icon",
    "tga": "Targa image",
    "pdf": "document",
    "apng": "animated PNG",
    "mp4": "video",
    "mkv": "video",
    "mov": "Apple video",
    "webm": "web video",
    "avi": "older video",
    "mp3": "audio",
    "m4a": "AAC audio",
    "wav": "lossless audio",
    "flac": "lossless audio",
    "ogg": "Vorbis audio",
    "opus": "Opus audio",
    "aac": "raw AAC audio",
    "aiff": "lossless audio",
    "wma": "Windows audio",
}

# Extensions that mean the same format, so e.g. a .jpeg is not offered "convert to jpg".
_SAME = {"jpeg": "jpg", "jfif": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff", "aifc": "aiff", "oga": "ogg"}


def normalise_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return _SAME.get(ext, ext)


def is_animated_image(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            return bool(getattr(img, "is_animated", False)) and getattr(img, "n_frames", 1) > 1
    except Exception:
        return False


def kind_of(path: Path) -> str | None:
    """Classify a file as 'image', 'animation', 'video', 'audio' or None (unsupported)."""
    ext = path.suffix.lower().lstrip(".")
    if ext in MAYBE_ANIMATED and is_animated_image(path):
        return "animation"
    if ext in IMAGE_IN:
        return "image"
    if ext in VIDEO_IN:
        return "video"
    if ext in AUDIO_IN:
        return "audio"
    return None


def targets_for(path: Path, kind: str | None = None) -> list[str]:
    kind = kind or kind_of(path)
    src = normalise_ext(path.suffix)
    if kind == "image":
        options = STILL_OUT
    elif kind == "animation":
        # Animated outputs first, then "first frame as a still image".
        options = ANIMATION_OUT + [f for f in STILL_OUT if f not in ANIMATION_OUT]
    elif kind == "video":
        # Re-encoding to the same container is allowed: it is how you shrink a video.
        return VIDEO_OUT + AUDIO_OUT
    elif kind == "audio":
        options = AUDIO_OUT
    else:
        return []
    same = {"png", "apng"} if src in ("png", "apng") else {src}
    return [t for t in options if t not in same]


def all_input_extensions() -> list[str]:
    return sorted(IMAGE_IN | VIDEO_IN | AUDIO_IN)
