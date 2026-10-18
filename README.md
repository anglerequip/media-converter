<p align="center"><img src="assets/icon.png" width="88" alt=""></p>

<h1 align="center">Media Converter</h1>

<p align="center">Convert images, GIFs, videos and audio on Windows, and save videos from YouTube, TikTok and other sites.<br>
Free, private (everything runs on your PC) and maximum quality by default.</p>

<p align="center">
  <a href="https://github.com/anglerequip/media-converter/releases/latest/download/MediaConverter-Setup.exe"><img src="docs/download-button.svg" width="286" alt="Download for Windows"></a>
</p>
<p align="center"><sub>Windows 10 and 11 &nbsp;·&nbsp; everything included, nothing else to install &nbsp;·&nbsp; <a href="https://github.com/anglerequip/media-converter/releases/latest">portable version and release notes</a></sub></p>

<p align="center"><img src="docs/screenshot.png" width="760" alt="Media Converter after converting four files to MP4, with the format choices on the right"></p>

## Features

- **All the usual formats:** PNG, JPG, WebP, HEIC, AVIF, GIF, TIFF, ICO, PDF · MP4, MOV, MKV, WebM, AVI · MP3, WAV, FLAC, M4A, OGG, Opus
- **Animations done right:** WebP ↔ GIF ↔ MP4 with exact frame timing and transparency
- **Video links to MP4 or MP3:** YouTube, TikTok, Instagram, X, Reddit, Vimeo and over a thousand more sites
- **Maximum quality by default**, with simple Smaller / Balanced / High / Maximum choices
- **No needless re-encoding:** MKV → MP4 and similar changes are instant and lossless
- **Batches:** drag in files or whole folders and see progress and space saved
- **Light and dark mode**, smooth animations and one-click updates

## Install

1. Click **Download for Windows** above and run `MediaConverter-Setup.exe`.
2. If Windows shows *"Windows protected your PC"*, click **More info → Run anyway**. This appears because the app isn't signed with a paid certificate.
3. Open **Media Converter** from the Start menu or the Desktop.

To update, click the **↻** button in the top-right corner of the app.

## How to use

Drop files in, pick a format and press **Convert Files**. To save a video, paste its link under **Download from Link**. Only download videos you have the right to save.

<details>
<summary><b>Quality settings in detail</b></summary>

| Level | Images | Video | Audio |
|---|---|---|---|
| **Maximum** (default) | JPG 100 (4:4:4), lossless WebP, AVIF/HEIC 100 | H.264 CRF 14 / H.265 CRF 16 / AV1 CRF 20 | MP3 320k, AAC 320k, Opus 256k |
| High | JPG 95, WebP 95 | CRF 18 / 21 / 27 | MP3 V0, AAC 256k |
| Balanced | JPG 88, WebP 85 | CRF 23 / 26 / 34 | MP3 V2, AAC 192k |
| Smaller | JPG 75, WebP 75 | CRF 28 / 30 / 42 | MP3 V5, AAC 128k |
| Lossless (Advanced) | PNG/TIFF/WebP lossless | x264 / x265 / AV1 lossless | ALAC / FLAC |

- Size and frame rate are kept unless you set a maximum; files are never enlarged.
- GIFs use a two-pass palette; at Maximum every frame gets its own 256 colours.
- Photos keep their colour profile and EXIF data and are turned upright.
- 10-bit video stays 10-bit with H.265 and AV1. Album art is kept for MP3, M4A and FLAC.
- Every finished file is opened and checked before it is marked Completed.

</details>

<details>
<summary><b>All supported formats</b></summary>

| You have | You can make |
|---|---|
| **Images:** PNG, JPG, WebP, HEIC, AVIF, TIFF, BMP, ICO, TGA, PSD, JP2, QOI… | PNG, JPG, WebP, AVIF, HEIC, TIFF, BMP, GIF, ICO, TGA, PDF |
| **Animations:** GIF, animated WebP, APNG | MP4, WebM, MOV, MKV, AVI, GIF, WebP, APNG, or the first frame as an image |
| **Video:** MP4, MOV, MKV, WebM, AVI, FLV, WMV, MPG, 3GP, TS, M2TS… | MP4, MKV, MOV, WebM, AVI, GIF, WebP, APNG, and any audio format below |
| **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, Opus, WMA, AIFF, ALAC… | MP3, M4A, WAV, FLAC, OGG, Opus, AAC, AIFF, WMA |
| **A link** from YouTube, TikTok, Instagram, X, Reddit, Facebook, Vimeo, Twitch, SoundCloud… | MP4 (best quality or H.264), MP3 320 kbps, M4A |

</details>

<details>
<summary><b>Downloads: notes and limits</b></summary>

- **Best video** picks the highest resolution offered; on YouTube that is often AV1 or VP9, which Windows 11 plays. Choose **Compatible video** (H.264) for older devices.
- Private, age-restricted, members-only and copy-protected videos are not supported.
- If a site stops working, check for updates: each release ships with the newest yt-dlp.

</details>

<details>
<summary><b>For developers: run from source, build, command line</b></summary>

Run from source (Python 3.10+):

```powershell
git clone https://github.com/anglerequip/media-converter.git
cd media-converter
powershell -ExecutionPolicy Bypass -File install.ps1
```

Build the installer and portable zip:

```powershell
winget install Gyan.FFmpeg.Shared JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

Command line and tests:

```powershell
python -m converter convert clip.webp --to mp4 --quality max
python -m converter download "https://www.youtube.com/watch?v=..." --mode best
python tests/smoke_test.py
```

Code layout: `converter/` holds the engine (Pillow for images, FFmpeg for everything with a timeline), the yt-dlp downloader and the updater; `ui/` holds the interface (CustomTkinter) with its design tokens, components and animations.

</details>

## License

MIT. FFmpeg (GPL), yt-dlp, Pillow and the other libraries keep their own licenses; the FFmpeg licence and source links ship in the app's `ffmpeg` folder.
