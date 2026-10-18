"""Spotify links: save songs, albums and playlists as tagged audio files.

Spotify's own audio is copy-protected (DRM) and is never touched. Instead, the song details are read
from Spotify's public embed page (title, artists, length, cover art), the same recording is found on
YouTube Music, and that audio is saved with Spotify's details written into the file. A match must agree
on length, artist and title; songs without a confident match are skipped rather than guessed.

Podcast episodes and shows are not copy-protected in most cases, so those go straight to yt-dlp.
"""

from __future__ import annotations

import glob
import json
import re
import subprocess
import threading
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import _NO_WINDOW, Cancelled, ConversionError, ProgressFn, find_tool

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/130.0 Safari/537.36", "Accept-Language": "en"}
_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?|spotify:)"
                   r"(track|album|playlist|episode|show|artist)[/:]([A-Za-z0-9]{22})", re.I)
PLAYLIST_LIMIT = 100  # Spotify's embed page lists at most this many songs
_EXTRA_WORDS = ("instrumental", "karaoke", "cover", "remix", "live", "sped up", "slowed", "reverb", "8d",
                "nightcore", "acoustic", "demo", "edit", "version", "lyrics", "1 hour", "loop")


class NoMatch(Exception):
    pass


@dataclass
class Track:
    title: str
    artists: list[str]
    duration: float  # seconds
    spotify_id: str = ""
    album: str = ""
    number: int = 0
    year: str = ""
    cover_url: str = ""


@dataclass
class Collection:
    kind: str  # track | album | playlist
    name: str
    tracks: list[Track] = field(default_factory=list)
    truncated: bool = False


# ----------------------------------------------------------------------------- reading Spotify


def parse_link(url: str) -> tuple[str, str] | None:
    """('track' | 'album' | 'playlist' | 'episode' | 'show' | 'artist', id) or None."""
    m = _LINK.search(url or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def is_spotify(url: str) -> bool:
    return parse_link(url) is not None


def _get(url: str, timeout: int = 20) -> bytes:
    # Only Spotify's own pages and image servers, and only over https: nothing else can be fetched from here.
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "open.spotify.com" or host.endswith(_COVER_HOSTS)):
        raise ConversionError("Refused to contact an unexpected address.")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:  # nosec B310
            return r.read()
    except Exception as exc:
        raise ConversionError("Couldn't reach Spotify. Check your internet connection and try again.") from exc


def _entity(kind: str, ident: str) -> dict:
    html = _get(f"https://open.spotify.com/embed/{kind}/{ident}").decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    try:
        return json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except Exception as exc:
        raise ConversionError("Spotify didn't return the details for this link. It may be private, "
                              "removed, or not available in your country.") from exc


def _cover(entity: dict) -> str:
    images = (entity.get("visualIdentity") or {}).get("image") or entity.get("coverArt", {}).get("sources") or []
    images = [i for i in images if _cover_allowed(i.get("url", ""))]
    return max(images, key=lambda i: i.get("maxWidth") or i.get("width") or 0)["url"] if images else ""


_COVER_HOSTS = (".spotifycdn.com", ".scdn.co")


def _cover_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    return parsed.scheme == "https" and any((parsed.hostname or "").endswith(h) for h in _COVER_HOSTS)


def _artists(text: str) -> list[str]:
    return [a.strip() for a in re.split(r",", (text or "").replace(" ", " ")) if a.strip()]


def _year(entity: dict) -> str:
    iso = (entity.get("releaseDate") or {}).get("isoString") or ""
    return iso[:4]


def read(url: str) -> Collection:
    parsed = parse_link(url)
    if not parsed:
        raise ConversionError("That isn't a Spotify song, album or playlist link.")
    kind, ident = parsed
    if kind == "artist":
        raise ConversionError("Artist pages aren't supported. Open one of the artist's albums or songs and use that link.")
    entity = _entity(kind, ident)
    if kind == "track":
        t = Track(entity.get("title") or entity.get("name") or "", [a["name"] for a in entity.get("artists") or []],
                  (entity.get("duration") or 0) / 1000, ident, year=_year(entity), cover_url=_cover(entity))
        return Collection("track", t.title, [t])
    tracks = []
    cover, year = _cover(entity), _year(entity)
    for i, item in enumerate(entity.get("trackList") or [], 1):
        sid = (item.get("uri") or "").rsplit(":", 1)[-1]
        tracks.append(Track(item.get("title") or "", _artists(item.get("subtitle")), (item.get("duration") or 0) / 1000,
                            sid, album=entity.get("name", "") if kind == "album" else "", number=i if kind == "album" else 0,
                            year=year if kind == "album" else "", cover_url=cover if kind == "album" else ""))
    name = entity.get("name") or entity.get("title") or "Spotify"
    return Collection(kind, name, tracks, truncated=kind == "playlist" and len(tracks) >= PLAYLIST_LIMIT)


def _fill_track_details(t: Track) -> None:
    """Playlists list songs without cover art or year; fetch them from the song's own page."""
    if t.cover_url or not t.spotify_id:
        return
    try:
        entity = _entity("track", t.spotify_id)
        t.cover_url, t.year = _cover(entity), t.year or _year(entity)
    except ConversionError:
        pass


# ----------------------------------------------------------------------------- matching on YouTube Music


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)|\[(?:feat|ft|with)\.?[^\]]*\]", " ", text)
    text = re.sub(r"\s-\s.*(remaster|version|edit|mix|mono|stereo).*$", " ", text)
    text = re.sub(r"\((?:[^)]*remaster[^)]*|[^)]*version[^)]*)\)", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _score(track: Track, info: dict) -> tuple[float, str]:
    """Higher is better. Returns (score, reason it would be rejected or '')."""
    yt_title = info.get("track") or info.get("title") or ""
    yt_artists = [a for a in (info.get("artists") or []) if a] or [info.get("channel") or info.get("uploader") or ""]
    duration = info.get("duration") or 0
    diff = abs(duration - track.duration) if duration and track.duration else 999

    if diff > 15:
        return -1, f"length differs by {diff:.0f}s"
    score = 40 if diff <= 2 else 30 if diff <= 5 else 15 if diff <= 10 else 0

    wanted = {_norm(a) for a in track.artists if a}
    found = {_norm(a) for a in yt_artists if a}
    channel = _norm(info.get("channel") or "")
    if wanted & found or (channel and any(w and w in channel for w in wanted)):
        score += 30
    elif any(w and w in _norm(yt_title + " " + " ".join(yt_artists)) for w in wanted):
        score += 15
    else:
        return -1, "different artist"

    similarity = SequenceMatcher(None, _norm(track.title), _norm(yt_title)).ratio()
    score += similarity * 30
    original = _norm(track.title)
    for word in _EXTRA_WORDS:
        if word in _norm(yt_title) and word not in original:
            return -1, f'"{word}" version'
    if similarity < 0.6:
        return -1, "different title"
    return score, ""


class _Quiet:
    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def find_match(track: Track, cancel: threading.Event | None = None) -> dict:
    """Return yt-dlp's details for the best matching recording, or raise NoMatch."""
    import yt_dlp

    query = f"{' '.join(track.artists[:2])} {track.title}"
    searches = [
        "https://music.youtube.com/search?q=" + urllib.parse.quote(query) + "#songs",
        f"ytsearch4:{query} audio",
    ]
    best: tuple[float, dict] | None = None
    reasons: list[str] = []
    base = {"quiet": True, "no_warnings": True, "logger": _Quiet()}
    for search in searches:
        try:
            with yt_dlp.YoutubeDL({**base, "extract_flat": True, "playlistend": 4}) as ydl:
                results = ydl.extract_info(search, download=False).get("entries") or []
        except Exception:
            continue
        for entry in results[:3]:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            vid = entry.get("id")
            if not vid:
                continue
            try:
                with yt_dlp.YoutubeDL({**base, "skip_download": True}) as ydl:
                    info = ydl.extract_info(f"https://music.youtube.com/watch?v={vid}", download=False)
            except Exception:
                continue
            score, why = _score(track, info)
            if why:
                reasons.append(why)
            elif best is None or score > best[0]:
                best = (score, info)
            if best and best[0] >= 90:  # same length, artist and title: no need to look further
                return best[1]
        if best and best[0] >= 70:  # a strong YouTube Music result: no need for the general search
            break
    if not best:
        detail = f" (closest results: {', '.join(dict.fromkeys(reasons))})" if reasons else ""
        raise NoMatch(f"No confident match found{detail}.")
    return best[1]


# ----------------------------------------------------------------------------- saving


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe(name: str) -> str:
    """A file or folder name that is valid on Windows and can't point outside the chosen folder."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")[:150].strip(" .")
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name or "Untitled"


def _tag(path: Path, track: Track, cover: bytes | None) -> None:
    """Write Spotify's title, artists, album, number, year and cover into the file (no re-encoding)."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return
    tmp = path.with_name(path.stem + ".tagging" + path.suffix)
    args = [ffmpeg, "-v", "error", "-y", "-i", str(path)]
    cover_file = None
    if cover:
        cover_file = path.with_name(path.stem + ".cover.jpg")
        cover_file.write_bytes(cover)
        args += ["-i", str(cover_file), "-map", "0:a", "-map", "1:v", "-c", "copy", "-disposition:v:0", "attached_pic",
                 "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    else:
        args += ["-map", "0:a", "-c", "copy"]
    meta = {"title": track.title, "artist": ", ".join(track.artists), "album_artist": track.artists[0] if track.artists else "",
            "album": track.album, "track": str(track.number) if track.number else "", "date": track.year,
            "comment": "Audio matched from YouTube Music; details from Spotify"}
    for key, value in meta.items():
        if value:
            args += ["-metadata", f"{key}={value}"]
    if path.suffix.lower() == ".mp3":
        args += ["-id3v2_version", "3"]
    args.append(str(tmp))
    result = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    if cover_file:
        cover_file.unlink(missing_ok=True)
    if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)  # tags are a bonus; keep the untagged audio rather than fail


def _download_audio(video: dict, folder: Path, filename: str, fmt: str, progress: ProgressFn,
                    cancel: threading.Event | None) -> Path:
    import yt_dlp

    def hook(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min((d.get("downloaded_bytes") or 0) / total, 0.99), "Downloading")

    post = ({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"} if fmt == "mp3"
            else {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"})
    from .downloader import _js_runtimes

    opts = {"quiet": True, "no_warnings": True, "logger": _Quiet(), "format": "ba[ext=m4a]/ba/b",
            "outtmpl": {"default": str(folder / (filename + ".%(ext)s"))}, "progress_hooks": [hook],
            "postprocessors": [post], "noplaylist": True, "retries": 5, "fragment_retries": 5,
            # Fetching in 10 MB pieces avoids most of YouTube's occasional "403 Forbidden" refusals.
            "http_chunk_size": 10 * 1024 * 1024}
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg:
        opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    url = video.get("webpage_url") or f"https://music.youtube.com/watch?v={video['id']}"
    last_error = ""
    for attempt in range(3):  # YouTube refuses a request now and then; a fresh attempt usually works
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except yt_dlp.utils.DownloadCancelled as exc:
            raise Cancelled() from exc
        except yt_dlp.utils.DownloadError as exc:
            last_error = str(exc).replace("ERROR: ", "")
            for leftover in folder.glob(glob.escape(filename) + ".*"):
                leftover.unlink(missing_ok=True)
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            progress(0.0, f"Retrying ({attempt + 2} of 3)")
    else:
        raise ConversionError(last_error or "The download failed three times.")
    out = folder / f"{filename}.{fmt}"
    if not out.exists():
        raise ConversionError("The audio was downloaded but the file couldn't be found afterwards.")
    return out


@dataclass
class Result:
    saved: list[Path] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    no_match: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    truncated: bool = False
    name: str = ""


def target_folder(collection: Collection, out_dir: str | Path) -> Path:
    """Albums and playlists get their own folder; single songs go straight into the chosen folder."""
    folder = Path(out_dir)
    if collection.kind in ("album", "playlist"):
        folder = folder / _safe(collection.name)
    return folder


def file_name(track: Track) -> str:
    return _safe(f"{track.artists[0] if track.artists else 'Unknown'} - {track.title}")


_THUMB_HOSTS = (".ytimg.com", ".googleusercontent.com", ".ggpht.com")


def thumbnail_bytes(video: dict) -> bytes | None:
    """The video's thumbnail for the match card, only from YouTube's own image servers over https."""
    candidates = [t.get("url") for t in sorted(video.get("thumbnails") or [], key=lambda t: t.get("width") or 0)
                  if (t.get("width") or 0) >= 300] + [video.get("thumbnail")]
    for url in candidates:
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme == "https" and (parsed.hostname or "").endswith(_THUMB_HOSTS):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=15) as r:  # nosec B310
                    return r.read(5_000_000)
            except Exception:
                continue
    return None


def save_match(track: Track, video: dict, folder: Path, fmt: str, progress: ProgressFn,
               cancel: threading.Event | None = None) -> Path:
    """Download a confirmed match and write Spotify's details and cover into it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = _download_audio(video, folder, file_name(track), fmt, progress, cancel)
    progress(0.995, "Adding title, artist and cover")
    _fill_track_details(track)
    if not track.album:
        track.album = video.get("album") or ""
    cover = None
    if track.cover_url and _cover_allowed(track.cover_url):
        try:
            cover = _get(track.cover_url)
        except ConversionError:
            cover = None
    _tag(path, track, cover)
    return path


def download(url: str, out_dir: str | Path, fmt: str = "m4a", progress: ProgressFn | None = None,
             cancel: threading.Event | None = None, on_saved: Callable[[Path], None] | None = None) -> Result:
    """Search and save a whole Spotify link without asking (used by the command line)."""
    progress = progress or (lambda f, t: None)
    progress(0.0, "Reading the Spotify link")
    collection = read(url)
    if not collection.tracks:
        raise ConversionError("This Spotify link has no songs in it.")
    folder = target_folder(collection, out_dir)
    result = Result(truncated=collection.truncated, name=collection.name)
    total = len(collection.tracks)
    for i, track in enumerate(collection.tracks):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        label = f"{', '.join(track.artists[:2])} – {track.title}"
        prefix = f"Song {i + 1} of {total}: " if total > 1 else ""
        step = lambda f, text, i=i: progress((i + f) / total, f"{prefix}{label} · {text}")  # noqa: E731
        if (folder / f"{file_name(track)}.{fmt}").exists():
            result.skipped_existing.append(label)
            continue
        step(0.02, "Finding the song")
        try:
            video = find_match(track, cancel)
        except NoMatch as exc:
            result.no_match.append(f"{label}: {exc}")
            continue
        try:
            path = save_match(track, video, folder, fmt, step, cancel)
            result.saved.append(path)
            if on_saved:
                on_saved(path)
        except Cancelled:
            raise
        except ConversionError as exc:
            result.failed.append(f"{label}: {exc}")
    progress(1.0, "Done")
    return result
