# Third-party components in MediaConverter.exe

Media Converter's own code is MIT-licensed (see `LICENSE`). The Windows release bundles the components below, unchanged,
at the exact versions in `build/requirements-lock.txt` and `build/build.ps1`. Each keeps its own licence.

Because FFmpeg, x265 (inside pillow-heif) and mutagen are licensed under the GNU GPL, the release as a whole is
distributed in a way that complies with the GPL: Media Converter's complete source code, including the build scripts,
is public in this repository, and the source of every component is available from the links below.

| Component | Version | Licence | Used for | Source |
|---|---|---|---|---|
| FFmpeg (full shared build by Gyan Doshi) | 9.0.2 | GPL-3.0 | Video, audio and animation conversion | https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz · build scripts: https://github.com/GyanD/codexffmpeg |
| Python | 3.12 | PSF-2.0 | Runs the app | https://www.python.org/downloads/source/ |
| Tcl/Tk | 8.6 | Tcl/Tk licence (BSD-style) | Window toolkit | https://www.tcl-lang.org/software/tcltk/ |
| Pillow | 13.0.0 | MIT-CMU | Still images | https://github.com/python-pillow/Pillow |
| pillow-heif | 1.8.1 | BSD-3-Clause | HEIC/HEIF images | https://github.com/bigcat88/pillow_heif |
| · libheif | bundled in pillow-heif | LGPL-3.0 | HEIC container | https://github.com/strukturag/libheif |
| · libde265 | bundled in pillow-heif | LGPL-3.0 | HEIC decoding | https://github.com/strukturag/libde265 |
| · x265 | bundled in pillow-heif | GPL-2.0-or-later | HEIC encoding | https://bitbucket.org/multicoreware/x265_git |
| CustomTkinter | 6.0.0 | CC0-1.0 | Window components | https://github.com/TomSchimansky/CustomTkinter |
| darkdetect | 0.8.0 | BSD-3-Clause | Follow the Windows theme | https://github.com/albertosottile/darkdetect |
| tkinterdnd2 (with tkdnd) | 0.6.4 | MIT (tkdnd: BSD-style) | Drag and drop | https://github.com/Eliav2/tkinterdnd2 |
| yt-dlp | 2026.10.10.233141.dev0 | Unlicense | Downloading from links | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | 0.8.0 | Unlicense, MIT, ISC | YouTube helper scripts for yt-dlp | https://github.com/yt-dlp/ejs |
| mutagen | 1.48.1 | GPL-2.0-or-later | Audio tags (used by yt-dlp) | https://github.com/quodlibet/mutagen |
| curl_cffi (with curl-impersonate) | 0.16.3 | MIT (curl: curl licence; BoringSSL: OpenSSL/ISC) | Browser-like connections for sites such as TikTok | https://github.com/lexiforest/curl_cffi |
| cffi | 2.1.1 | MIT-0 | Used by curl_cffi | https://github.com/python-cffi/cffi |
| pycparser | 3.11 | BSD-3-Clause | Used by cffi | https://github.com/eliben/pycparser |
| requests | 2.34.2 | Apache-2.0 | Used by yt-dlp | https://github.com/psf/requests |
| urllib3 | 2.8.0 | MIT | Used by requests | https://github.com/urllib3/urllib3 |
| certifi | see lock file | MPL-2.0 | Trusted certificate list for HTTPS | https://github.com/certifi/python-certifi |
| idna | 3.20 | BSD-3-Clause | International domain names | https://github.com/kjd/idna |
| charset-normalizer | 3.5.2 | MIT | Text encoding detection | https://github.com/jawah/charset_normalizer |
| brotli | 1.2.0 | MIT | Compressed web responses | https://github.com/google/brotli |
| websockets | 17.2 | BSD-3-Clause | Used by yt-dlp for some sites | https://github.com/python-websockets/websockets |
| pycryptodomex | 3.24.1 | BSD-2-Clause and Public Domain | Used by yt-dlp for some sites | https://github.com/Legrandin/pycryptodome |
| PyInstaller bootloader | 6.22.3 | GPL-2.0 with an exception that allows any licence for the bundled app | Starts the app | https://github.com/pyinstaller/pyinstaller |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | Used by PyInstaller | https://github.com/enthought/pywin32-ctypes |

Build-only tools, not included in the app: PyInstaller (packaging), Inno Setup 6.7.3 (installer, its own licence:
https://jrsoftware.org/files/is/license.txt), pip-tools (lock file).

This list is provided for transparency and is not legal advice.
