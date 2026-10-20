# Security and privacy

## What the app does on your computer

- **Converting files happens entirely offline.** Files are read from where you pick them and new files are written
  next to them, in a "Converted" folder, or in a folder you choose. Existing files are never overwritten: a new name
  such as `photo (1).png` is used instead. Every finished file is opened and checked before it is reported as done.
- **The app never connects to the internet on its own.** There is no update check, no telemetry, no crash reporting
  and no account. It goes online only when you press **Download** on the Download from Link screen:
  - to the site in the link, through yt-dlp;
  - for Spotify links, to `open.spotify.com` and Spotify's image servers (`*.spotifycdn.com`, `*.scdn.co`) for song
    details and cover art, and to YouTube Music to find and download the matching audio.
  Nothing about your files is ever sent anywhere.
- **What it stores:** your settings in `%APPDATA%\MediaConverter\settings.json` on Windows or
  `~/Library/Application Support/MediaConverter/settings.json` on a Mac and, in the packaged app only, error details in
  `log.txt` in the same folder (kept under 0.5 MB). Both stay on your computer.
- **Programs it starts:** only the bundled `ffmpeg` and `ffprobe`, and Explorer or Finder for "Show in folder".
  They are always started with a fixed list of arguments and full file paths, never through a command shell.
- **Protections built in:** images claiming more than 500 megapixels are refused (a known way to exhaust memory);
  downloaded file and folder names are cleaned so they can't point outside the chosen folder or use reserved Windows
  names; only `https` addresses on Spotify's own servers are contacted for Spotify data.
- **Links:** only `https://` and `http://` links are accepted, never `file:` or other kinds, and not links with a user
  name or password in them. Links to this computer or the local network (such as a router's settings page) are refused:
  the name is checked as you type, and the address it looks up to is checked again just before connecting. A public
  site that redirects into a local network is not caught, because yt-dlp follows redirects itself. Downloaded files
  are never opened or run by the app, and a cancelled or failed download removes what it had written.

## How the release is built, and how to check it

The published files are built by [GitHub Actions](.github/workflows/release.yml) from the tagged commit in this
repository, on clean GitHub-hosted machines: Windows with [build/build.ps1](build/build.ps1), and a Mac with Apple
silicon plus an Intel Mac with [build/build_mac.sh](build/build_mac.sh). Each build runs the app's self-test before
anything is published, and the run logs are public on the Actions tab. Every input is pinned:

| Input | Pinned by |
|---|---|
| Media Converter source | the git tag (e.g. `v3.2.0`) |
| Python 3.12.10 | the workflow |
| All Python libraries | `build/requirements-lock.txt`: exact versions and SHA-256 hashes, installed with `pip --require-hashes` |
| FFmpeg 9.0.2 | download addresses and SHA-256 in `build/build.ps1` (Windows) and `build/build_mac.sh` (both Macs); the build stops if a file differs |
| Lucide icon font (Mac icons) | committed in `assets/fonts` with its npm integrity hash in `assets/fonts/README.txt` |
| Inno Setup 6.7.3 | download address and SHA-256 in `build/build.ps1` |
| GitHub Actions used by the workflow | full commit SHAs |

Each release includes `SHA256SUMS.txt` and a `BUILD-INFO` file per platform (the commit, every input and its fingerprint).

To check a download:

1. **Fingerprint:** in PowerShell, `Get-FileHash .\MediaConverter-Setup.exe`, or on a Mac in Terminal,
   `shasum -a 256 MediaConverter-mac-apple-silicon.dmg`, and compare with `SHA256SUMS.txt` on the release page.
2. **Built by this repository:** while the repository is public, each release carries a signed build provenance
   record from GitHub. With the [GitHub CLI](https://cli.github.com/):
   `gh attestation verify MediaConverter-Setup.exe --repo anglerequip/media-converter`.
   It confirms the file was produced by this repository's workflow from a specific commit.
3. **Rebuild it yourself:** check out the tag and run `powershell -ExecutionPolicy Bypass -File build\build.ps1`
   with Python 3.12 (on a Mac: `bash build/build_mac.sh`). You get the same app from the same pinned inputs. The files are not byte-for-byte identical to the
   release (PyInstaller and Inno Setup record build times), so compare the contents rather than the fingerprints.

The apps are not signed with a paid certificate: Windows SmartScreen shows a warning the first time, and macOS asks
you to allow the app in Privacy & Security (the Mac app carries only a free "ad-hoc" signature). The checks above are
the way to confirm a file is genuine.

## Reporting a vulnerability

Please report security problems privately through GitHub: **Security → Report a vulnerability** on this repository.
Please don't open a public issue for them.
