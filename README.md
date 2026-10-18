<p align="center"><img src="assets/icon.png" width="96" alt=""></p>

<h1 align="center">Media Converter</h1>

<p align="center">A desktop app for Windows that converts images, GIFs, video and audio between all the common formats, and downloads videos from YouTube, TikTok and other links. Everything runs on your own computer.</p>

<p align="center"><img src="docs/screenshot.png" width="720" alt="Media Converter window"></p>

## What it does

| You have | You can make |
|---|---|
| **Images:** PNG, JPG, WebP, HEIC, AVIF, TIFF, BMP, ICO, TGA, PSD, JP2, QOI, … | PNG, JPG, WebP, AVIF, HEIC, TIFF, BMP, GIF, ICO, TGA, PDF |
| **Animations:** animated GIF, animated WebP, APNG | MP4, WebM, MOV, MKV, AVI, GIF, animated WebP, APNG, or the first frame as any image format |
| **Video:** MP4, MOV, MKV, WebM, AVI, FLV, WMV, MPG, 3GP, TS, M2TS, … | MP4, MKV, MOV, WebM, AVI, GIF, animated WebP, APNG, and audio (MP3, M4A, WAV, FLAC, OGG, Opus, AAC, AIFF, WMA) |
| **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, Opus, WMA, AIFF, ALAC, … | MP3, M4A, WAV, FLAC, OGG, Opus, AAC, AIFF, WMA |
| **A link:** YouTube, TikTok, Instagram, X / Twitter, Reddit, Facebook, Vimeo, Twitch, SoundCloud and 1,000+ other sites | MP4 (best quality, or H.264 for maximum compatibility), MP3 320 kbps, M4A |

Examples: `webp → png`, `webp → mp4`, `gif → mp4`, `mp4 → gif`, `heic → jpg`, `mkv → mp4`, `mov → mp4`, `mp4 → mp3`, `flac → mp3`, `png → ico`, `jpg → pdf`, a YouTube or TikTok link → MP4.

Drag files or whole folders into the window, pick a format and press **Convert**. Mixed batches are fine: files that can't become the chosen format are skipped and marked.

## Quality

The default is **Maximum**. You can pick from five levels:

| Level | Images | Video | Audio |
|---|---|---|---|
| Lossless | PNG/TIFF always lossless; WebP lossless; JPG 100 with full colour | x264/x265/AV1 lossless | ALAC / FLAC where the container allows |
| **Maximum** | JPG 100 (4:4:4), lossless WebP, AVIF/HEIC 100 | H.264 CRF 14 / H.265 CRF 16 / AV1 CRF 20, slow preset | MP3 320k, AAC 320k, Vorbis q10, Opus 256k |
| High | JPG 95, WebP 95 | CRF 18 / 21 / 27 | MP3 V0, AAC 256k |
| Balanced | JPG 88, WebP 85 | CRF 23 / 26 / 34 | MP3 V2, AAC 192k |
| Small file | JPG 75, WebP 75 | CRF 28 / 30 / 42 | MP3 V5, AAC 128k |

Details that protect quality:

- **No needless re-encoding.** With "Copy video/audio unchanged" ticked, `mkv → mp4`, `mp4 → m4a` and similar conversions copy the streams bit for bit: instant and lossless.
- **GIFs use FFmpeg's two-pass palette** (`palettegen` + `paletteuse`). At Maximum every frame gets its own 256-colour palette.
- **Animated GIF/WebP timing is frame-exact.** Frames keep their individual delays, and transparency is kept where the target supports it (WebP, APNG, GIF, WebM).
- **Photos keep their colour profile and EXIF data**, and phone rotation is applied so the picture is upright everywhere.
- **10-bit video stays 10-bit** when encoding to H.265 or AV1.
- **Album art is carried over** when converting to MP3, M4A and FLAC.

## Install (Windows)

You need [Python 3.10+](https://www.python.org/downloads/) (`winget install Python.Python.3.12`).

```powershell
git clone https://github.com/anglerequip/media-converter.git
cd media-converter
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer adds the Python packages, installs FFmpeg with winget if it is missing, and creates **Media Converter** shortcuts on the Desktop and in the Start menu.

To start it by hand: `pythonw MediaConverter.pyw`, or `python app.py` to see errors in the console.

## Command line

The same engine works from a terminal:

```powershell
python -m converter convert photo.webp --to png
python -m converter convert sticker.webp clip.gif --to mp4 --quality max
python -m converter convert movie.mkv --to mp4 --codec h265 --out D:\Converted
python -m converter download "https://www.youtube.com/watch?v=..." --mode best
python -m converter download "https://www.tiktok.com/@user/video/..." --mode compatible
python -m converter formats song.flac       # what can this file become?
```

## When a site stops working

Video sites change often. If downloads from one start failing, press **Update downloader** in the app (or run `pip install -U --pre "yt-dlp[default,curl-cffi]"`) and restart.

Notes on downloads:

- **Best quality** picks the highest resolution available. On YouTube that is often AV1 or VP9 video. Windows 11 plays these; on older devices use **plays everywhere (H.264)**.
- Private, age-restricted or members-only videos need a signed-in browser session and are not supported.
- Only download content you own or have permission to save, and follow each site's terms of service.

## Tests

```powershell
python tests/smoke_test.py
```

This creates sample images, animations, video and audio, converts each to every format it offers, and checks every result opens correctly.

## How it works

- `converter/formats.py`: which formats are read, and what each kind of file can become.
- `converter/engine.py`: still images go through Pillow; video, audio and animations through FFmpeg. Animated GIF/WebP frames are decoded by Pillow (correct frame disposal and per-frame timing) and passed to FFmpeg.
- `converter/downloader.py`: link downloads through yt-dlp.
- `app.py`: the window (CustomTkinter, drag and drop through tkinterdnd2).

## License

MIT. FFmpeg, yt-dlp, Pillow and the other libraries keep their own licenses.
