<p align="center"><img src="assets/icon.png" width="96" alt=""></p>

<h1 align="center">Media Converter</h1>

<p align="center">A desktop app for Windows that converts images, GIFs, video and audio between all the common formats, and downloads videos from YouTube, TikTok and other links. Everything runs on your own computer.</p>

<p align="center"><img src="docs/screenshot.png" width="760" alt="Media Converter after converting four files to MP4, with the format choices on the right"></p>

## What it does

| You have | You can make |
|---|---|
| **Images:** PNG, JPG, WebP, HEIC, AVIF, TIFF, BMP, ICO, TGA, PSD, JP2, QOI, … | PNG, JPG, WebP, AVIF, HEIC, TIFF, BMP, GIF, ICO, TGA, PDF |
| **Animations:** animated GIF, animated WebP, APNG | MP4, WebM, MOV, MKV, AVI, GIF, animated WebP, APNG, or the first frame as any image format |
| **Video:** MP4, MOV, MKV, WebM, AVI, FLV, WMV, MPG, 3GP, TS, M2TS, … | MP4, MKV, MOV, WebM, AVI, GIF, animated WebP, APNG, and audio (MP3, M4A, WAV, FLAC, OGG, Opus, AAC, AIFF, WMA) |
| **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, Opus, WMA, AIFF, ALAC, … | MP3, M4A, WAV, FLAC, OGG, Opus, AAC, AIFF, WMA |
| **A link:** YouTube, TikTok, Instagram, X / Twitter, Reddit, Facebook, Vimeo, Twitch, SoundCloud and 1,000+ other sites | MP4 (best quality, or H.264 for maximum compatibility), MP3 320 kbps, M4A |

Examples: `webp → png`, `webp → mp4`, `gif → mp4`, `mp4 → gif`, `heic → jpg`, `mkv → mp4`, `mov → mp4`, `mp4 → mp3`, `flac → mp3`, `png → ico`, `jpg → pdf`, a YouTube or TikTok link → MP4.

## Using it

1. **Add files.** Drag files or whole folders into the window, or press **Choose Files** (Ctrl+O).
2. **Pick a format.** The app detects what each file is and suggests the best formats first. Only formats it can really produce are shown.
3. **Press Convert Files** (Ctrl+Enter). Each file shows its own progress, and when the batch finishes you see how many succeeded and how much space was saved.

Mixed batches are fine: files that can't become the chosen format are skipped, and the app tells you which ones and why before you start. Every finished file is opened and checked before it is marked **Completed**.

The **Download from Link** tab checks a link as you type and tells you whether the site is supported. Downloaded files can be sent straight to the converter.

The window follows the Windows light or dark theme, or you can pick one in the top-right corner. All settings are remembered between sessions.

Interactions are animated with short, restrained transitions: buttons and selections fade, new files slide into the list, panels open and close smoothly, and the two screens glide in when you switch. Animations follow Windows' "Animation effects" setting and can be turned off in Settings.

## Quality

The default is **Maximum**. The settings panel explains each level in plain words and shows the exact technical setting underneath.

| Level | Images | Video | Audio |
|---|---|---|---|
| **Maximum** | JPG 100 (4:4:4), lossless WebP, AVIF/HEIC 100 | H.264 CRF 14 / H.265 CRF 16 / AV1 CRF 20, slow preset | MP3 320k, AAC 320k, Vorbis q10, Opus 256k |
| High | JPG 95, WebP 95 | CRF 18 / 21 / 27 | MP3 V0, AAC 256k |
| Balanced | JPG 88, WebP 85 | CRF 23 / 26 / 34 | MP3 V2, AAC 192k |
| Smaller | JPG 75, WebP 75 | CRF 28 / 30 / 42 | MP3 V5, AAC 128k |
| Lossless (Advanced settings) | PNG/TIFF always lossless; WebP lossless | x264/x265/AV1 lossless | ALAC / FLAC where the container allows |

Size and frame rate are kept as they are unless you choose a maximum width or frame rate. Files are never enlarged.

Details that protect quality:

- **No needless re-encoding.** With "Keep original streams" switched on (Advanced settings), `mkv → mp4`, `mp4 → m4a` and similar conversions copy the streams bit for bit: instant and lossless.
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
- `app.py` and `ui/`: the window, built with CustomTkinter. `ui/theme.py` holds the colours, type and icons (Windows' own Segoe Fluent Icons); `ui/motion.py` the animations; `ui/widgets.py` the shared components; `convert_view.py` and `download_view.py` the two screens. Drag and drop uses tkinterdnd2.

## License

MIT. FFmpeg, yt-dlp, Pillow and the other libraries keep their own licenses.
