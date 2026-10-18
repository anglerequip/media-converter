#!/usr/bin/env bash
# Builds the macOS release from source, for the Mac this runs on (Apple silicon or Intel):
#   dist/Media Converter.app
#   dist/MediaConverter-mac-<apple-silicon|intel>.dmg
#   dist/SHA256SUMS-mac-<arch>.txt and dist/BUILD-INFO-mac-<arch>.txt
#
#   bash build/build_mac.sh
#
# Like build.ps1 on Windows, nothing is taken from the build machine except Python 3.12:
#   - Python libraries come only from build/requirements-lock.txt (exact versions, SHA-256 checked by pip)
#   - FFmpeg is downloaded from the pinned addresses below and checked against SHA-256
# The same script runs in GitHub Actions (.github/workflows/release.yml) to produce the published release.
set -euo pipefail
cd "$(dirname "$0")/.."

# ---------------------------------------------------------------- pinned inputs
PYTHON_VERSION="3.12"
FFMPEG_VERSION="9.0.2"   # static builds by Martin Riedl, https://ffmpeg.martin-riedl.de (GPL)
case "$(uname -m)" in
  arm64)
    ARCH_NAME="apple-silicon"
    FF_BASE="https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2"
    FFMPEG_SHA="c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"
    FFPROBE_SHA="fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6" ;;
  x86_64)
    ARCH_NAME="intel"
    FF_BASE="https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2"
    FFMPEG_SHA="7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4"
    FFPROBE_SHA="2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd" ;;
  *) echo "BUILD FAILED: unsupported Mac architecture $(uname -m)"; exit 1 ;;
esac

fail() { echo "BUILD FAILED: $*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

fetch_pinned() {  # url sha256 destination
  if [ ! -f "$3" ]; then curl -fsSL --retry 3 -o "$3" "$1"; fi
  actual=$(shasum -a 256 "$3" | cut -d' ' -f1)
  if [ "$actual" != "$2" ]; then rm -f "$3"; fail "$(basename "$3") does not match its pinned SHA-256 (expected $2, got $actual). Deleted."; fi
  echo "$(basename "$3"): SHA-256 verified"
}

# ---------------------------------------------------------------- Python
step "Checking Python"
PY_FOUND=$(python3 -c 'import platform; print(platform.python_version())')
case "$PY_FOUND" in "$PYTHON_VERSION".*) echo "Python $PY_FOUND" ;; *) fail "Python $PYTHON_VERSION.x is required, found $PY_FOUND" ;; esac

# ---------------------------------------------------------------- FFmpeg
step "Fetching pinned FFmpeg $FFMPEG_VERSION ($ARCH_NAME)"
mkdir -p build/cache
FF_DIR="build/cache/ffmpeg-mac-$ARCH_NAME-$FFMPEG_VERSION"
fetch_pinned "$FF_BASE/ffmpeg.zip" "$FFMPEG_SHA" "build/cache/ffmpeg-mac-$ARCH_NAME.zip"
fetch_pinned "$FF_BASE/ffprobe.zip" "$FFPROBE_SHA" "build/cache/ffprobe-mac-$ARCH_NAME.zip"
rm -rf "$FF_DIR" && mkdir -p "$FF_DIR"
unzip -q -o "build/cache/ffmpeg-mac-$ARCH_NAME.zip" ffmpeg -d "$FF_DIR"
unzip -q -o "build/cache/ffprobe-mac-$ARCH_NAME.zip" ffprobe -d "$FF_DIR"
chmod +x "$FF_DIR/ffmpeg" "$FF_DIR/ffprobe"
xattr -c "$FF_DIR/ffmpeg" "$FF_DIR/ffprobe" 2>/dev/null || true
"$FF_DIR/ffmpeg" -hide_banner -version | head -1
cat > "$FF_DIR/README.txt" <<EOF
FFmpeg $FFMPEG_VERSION is included as separate programs (ffmpeg, ffprobe) under the GNU GPL v3.
Binary build (static, $ARCH_NAME): $FF_BASE/ffmpeg.zip and ffprobe.zip by Martin Riedl, https://ffmpeg.martin-riedl.de
SHA-256 of those downloads: ffmpeg.zip $FFMPEG_SHA, ffprobe.zip $FFPROBE_SHA
Source code: https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz and the build scripts at https://git.martin-riedl.de/ffmpeg/build-script
EOF

# ---------------------------------------------------------------- clean Python environment
step "Creating a fresh Python environment from the lock file"
rm -rf .venv-build
python3 -m venv .venv-build
PY=.venv-build/bin/python
"$PY" -m pip install --disable-pip-version-check -q --require-hashes --no-deps -r build/requirements-lock.txt
VERSION=$("$PY" -c 'from converter import __version__; print(__version__)')
COMMIT=$(git rev-parse HEAD 2>/dev/null || echo unknown)
DIRTY=$([ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] && echo " (with uncommitted changes)" || true)
echo "Version $VERSION from commit $COMMIT$DIRTY"
export PYTHONHASHSEED=0
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct 2>/dev/null || date +%s); export SOURCE_DATE_EPOCH

# ---------------------------------------------------------------- PyInstaller
step "Building Media Converter.app"
rm -rf dist build/work
MC_FFMPEG_DIR="$PWD/$FF_DIR" MC_VERSION="$VERSION" "$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath dist --workpath build/work build/MediaConverter.spec
APP="dist/Media Converter.app"
[ -x "$APP/Contents/MacOS/MediaConverter" ] || fail "PyInstaller did not produce the app"
cp LICENSE "$APP/Contents/Resources/LICENSE.txt"
cp THIRD-PARTY-NOTICES.md "$APP/Contents/Resources/THIRD-PARTY-NOTICES.md"
cp "$FF_DIR/README.txt" "$APP/Contents/Resources/FFMPEG-README.txt"
# Ad-hoc signature over the whole bundle (required to run on Apple silicon; not an Apple Developer ID).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "bundle signature: valid (ad-hoc)"

# ---------------------------------------------------------------- disk image
step "Building the disk image"
DMG="dist/MediaConverter-mac-$ARCH_NAME.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Media Converter" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
rm -rf "$STAGE"

# ---------------------------------------------------------------- fingerprints + build record
step "Writing SHA256SUMS and BUILD-INFO"
(cd dist && shasum -a 256 "$(basename "$DMG")") > "dist/SHA256SUMS-mac-$ARCH_NAME.txt"
LOCK_SHA=$(shasum -a 256 build/requirements-lock.txt | cut -d' ' -f1)
EXE_SHA=$(shasum -a 256 "$APP/Contents/MacOS/MediaConverter" | cut -d' ' -f1)
cat > "dist/BUILD-INFO-mac-$ARCH_NAME.txt" <<EOF
Media Converter $VERSION for macOS ($ARCH_NAME)
Source commit:      $COMMIT$DIRTY
Built:              $(date -u '+%Y-%m-%d %H:%M UTC') on macOS $(sw_vers -productVersion) $(uname -m)
Python:             $("$PY" -c 'import platform; print(platform.python_version())')
Python libraries:   build/requirements-lock.txt (SHA-256 $LOCK_SHA), installed with --require-hashes
FFmpeg:             $FFMPEG_VERSION  $FF_BASE  ffmpeg.zip SHA-256 $FFMPEG_SHA  ffprobe.zip SHA-256 $FFPROBE_SHA
App executable:     SHA-256 $EXE_SHA
Signature:          ad-hoc (no Apple Developer ID; see README for opening it the first time)

$(cat "dist/SHA256SUMS-mac-$ARCH_NAME.txt")
EOF

step "Done"
cat "dist/SHA256SUMS-mac-$ARCH_NAME.txt"
du -h "$DMG" | cut -f1
