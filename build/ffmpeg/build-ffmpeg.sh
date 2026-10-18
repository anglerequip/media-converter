#!/usr/bin/env bash
# Builds the FFmpeg that Media Converter ships, from pinned, checksum-verified sources.
#
#   Windows: run in an MSYS2 UCRT64 shell (packages: see .github/workflows/ffmpeg.yml).
#   macOS:   run in Terminal with the Xcode command line tools and: brew install nasm cmake meson ninja pkgconf
#
#   bash build/ffmpeg/build-ffmpeg.sh
#
# Output in build/ffmpeg/out/:
#   ffmpeg-<version>-mc<rev>-<platform>.zip   bin/ffmpeg + bin/ffprobe, licences, BUILD-INFO.txt
#   ffmpeg-<version>-mc<rev>-sources.tar.gz   every source used, plus this script (the GPL's "corresponding source")
#
# What it contains, and why it's smaller than a "full" FFmpeg build:
#   * All of FFmpeg's own decoders, encoders, formats, filters and protocols (nothing native is removed).
#   * Exactly the external libraries Media Converter uses: x264, x265 (8 and 10 bit), SVT-AV1, libaom (AV1
#     encoder), libvpx (VP8/VP9), libwebp, LAME (MP3), Opus, Vorbis, and dav1d for AV1 decoding.
#   * Left out: hardware encoders, capture devices (libavdevice), ffplay, and the ~80 other optional libraries a
#     full build bundles (subtitle rendering, OpenCL/Vulkan filters, rarely used codecs) that the app never calls.
#   * ffprobe is a second, decode-only build, so it doesn't carry a copy of every encoder.
#   * Third-party libraries are linked statically: two self-contained programs, no DLLs or dylibs to manage.
set -euo pipefail

FFMPEG_VERSION=9.0.2
REV=1  # bump when this script changes the result

# name | url | sha256        (release tarballs; hashes checked against each project's published sums or signature)
TARBALLS=(
  "ffmpeg|https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz|8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
  "zlib|https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz|bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16"
  "x265|https://bitbucket.org/multicoreware/x265_git/downloads/x265_4.2.tar.gz|40b1ea0453e0309f0eba934e0ddf533f8f6295966679e8894e8f1c1c8d5e1210"
  "dav1d|https://downloads.videolan.org/pub/videolan/dav1d/1.5.4/dav1d-1.5.4.tar.xz|686616b7c69eb88d44459391ab25cac13b6647a3b288835c5784e71c1514a5c5"
  "libwebp|https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz|e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564"
  "lame|https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz|ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"
  "opus|https://downloads.xiph.org/releases/opus/opus-1.6.1.tar.gz|6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1"
  "libogg|https://downloads.xiph.org/releases/ogg/libogg-1.3.6.tar.xz|5c8253428e181840cd20d41f3ca16557a9cc04bad4a3d04cce84808677fa1061"
  "libvorbis|https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.xz|b33cc4934322bcbf6efcbacf49e3ca01aadbea4114ec9589d1b1e9d20f72954b"
)
# name | git url | tag or branch | exact commit   (projects without official release tarballs)
GITS=(
  "x264|https://code.videolan.org/videolan/x264.git|stable|b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
  "libvpx|https://chromium.googlesource.com/webm/libvpx|v1.17.0|6df3ec34557879fff673706f4a1d9fbd0f3a6f0e"
  "aom|https://aomedia.googlesource.com/aom|v3.15.2|af3dc9aadc793c1b00edc003e3fc19a44fdeb574"
  "svtav1|https://gitlab.com/AOMediaCodec/SVT-AV1.git|v4.2.0|9292ec8e32bce26f781f277ec8739b53426c4300"
)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/work}"
OUT="$HERE/out"
DL="$WORK/downloads"
SRC="$WORK/src"
PREFIX="$WORK/prefix"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1789000000}"

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  MINGW*|MSYS*)
    [[ "${MSYSTEM:-}" == UCRT64 ]] || die "run this in an MSYS2 UCRT64 shell"
    OS=windows; PLATFORM=windows-x64; EXE=.exe; X86=1; CC="${CC:-gcc}"
    JOBS=$(nproc)
    ;;
  Darwin)
    OS=mac; EXE=; JOBS=$(sysctl -n hw.ncpu); CC="${CC:-cc}"
    export MACOSX_DEPLOYMENT_TARGET=11.0
    case "$(uname -m)" in
      arm64) PLATFORM=macos-arm64; X86=0; VPX_TARGET=arm64-darwin20-gcc; HOST=aarch64-apple-darwin ;;
      x86_64) PLATFORM=macos-x64; X86=1; VPX_TARGET=x86_64-darwin20-gcc; HOST=x86_64-apple-darwin ;;
      *) die "unsupported Mac CPU $(uname -m)" ;;
    esac
    ;;
  *) die "unsupported system $(uname -s)" ;;
esac
NAME="ffmpeg-$FFMPEG_VERSION-mc$REV"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# ---------------------------------------------------------------- sources

fetch_sources() {
  step "Fetching pinned sources"
  mkdir -p "$DL" "$SRC"
  local entry name url sum file got repo ref commit
  for entry in "${TARBALLS[@]}"; do
    IFS='|' read -r name url sum <<<"$entry"
    file="$DL/$(basename "$url")"
    [[ -f "$file" && "$(sha256 "$file")" == "$sum" ]] || curl -fsSL --retry 4 --retry-all-errors -o "$file" "$url"
    got="$(sha256 "$file")"
    [[ "$got" == "$sum" ]] || die "$name: SHA-256 mismatch (expected $sum, got $got)"
    rm -rf "${SRC:?}/$name" && mkdir -p "$SRC/$name"
    tar -xf "$file" -C "$SRC/$name" --strip-components=1
    echo "  $name  $(basename "$url")  sha256 $sum"
  done
  for entry in "${GITS[@]}"; do
    IFS='|' read -r name repo ref commit <<<"$entry"
    rm -rf "${SRC:?}/$name"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$ref" "$repo" "$SRC/$name" 2>/dev/null || true
    if [[ "$(git -C "$SRC/$name" rev-parse HEAD 2>/dev/null)" != "$commit" ]]; then  # the branch moved on: fetch the exact commit
      rm -rf "${SRC:?}/$name" && git init -q "$SRC/$name"
      git -C "$SRC/$name" fetch -q --depth 1 "$repo" "$commit"
      git -C "$SRC/$name" -c advice.detachedHead=false checkout -q FETCH_HEAD
    fi
    got="$(git -C "$SRC/$name" rev-parse HEAD)"
    [[ "$got" == "$commit" ]] || die "$name: expected commit $commit, got $got"
    git -C "$SRC/$name" archive --format=tar.gz --prefix="$name/" -o "$DL/$name-$commit.tar.gz" HEAD
    echo "  $name  $ref  commit $commit"
  done
}

# ---------------------------------------------------------------- libraries (static, into $PREFIX)

CMAKE_COMMON=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib
              -DCMAKE_PREFIX_PATH="$PREFIX" -DBUILD_SHARED_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5
              -DCMAKE_POSITION_INDEPENDENT_CODE=ON)
[[ $OS == mac ]] && CMAKE_COMMON+=(-DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET")

cmake_lib() {  # cmake_lib <source dir> <build dir> [options...]
  local src="$1" build="$2"; shift 2
  cmake -S "$src" -B "$build" "${CMAKE_COMMON[@]}" "$@" >/dev/null
  cmake --build "$build" -j "$JOBS" >/dev/null
  cmake --install "$build" >/dev/null
}

build_libs() {
  rm -rf "$PREFIX" "$WORK/b" && mkdir -p "$PREFIX/lib/pkgconfig" "$PREFIX/include" "$WORK/b"
  export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig" PKG_CONFIG_PATH=
  export CFLAGS="-O2" CXXFLAGS="-O2"
  local nasm=(); [[ $X86 == 1 ]] && nasm=(-DENABLE_NASM=ON)

  if [[ $OS == windows ]]; then
    step "zlib"  # macOS has zlib built in; on Windows a static copy goes into ffmpeg.exe
    (cd "$SRC/zlib" && for f in adler32 compress crc32 deflate gzclose gzlib gzread gzwrite infback inffast inflate inftrees trees uncompr zutil; do
       "$CC" $CFLAGS -c "$f.c" -o "$WORK/b/z_$f.o"; done)
    ar rcs "$PREFIX/lib/libz.a" "$WORK"/b/z_*.o
    cp "$SRC/zlib/zlib.h" "$SRC/zlib/zconf.h" "$PREFIX/include/"
    printf 'prefix=%s\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\nName: zlib\nDescription: zlib\nVersion: 1.3.2\nLibs: -L${libdir} -lz\nCflags: -I${includedir}\n' \
      "$PREFIX" > "$PREFIX/lib/pkgconfig/zlib.pc"
  fi

  step "x264"
  (cd "$SRC/x264" && ./configure --prefix="$PREFIX" --enable-static --enable-pic --disable-cli --disable-opencl >/dev/null \
     && make -j "$JOBS" >/dev/null && make install >/dev/null && make distclean >/dev/null)

  step "x265 (8-bit with 10-bit linked in)"
  local xs="$SRC/x265/source" xb="$WORK/b/x265"
  local x265_opts=(-DENABLE_SHARED=OFF -DENABLE_CLI=OFF -DENABLE_TESTS=OFF)
  cmake -S "$xs" -B "$xb/10" "${CMAKE_COMMON[@]}" "${x265_opts[@]}" -DHIGH_BIT_DEPTH=ON -DEXPORT_C_API=OFF >/dev/null
  cmake --build "$xb/10" -j "$JOBS" >/dev/null
  mkdir -p "$xb/8" && cp "$xb/10/libx265.a" "$xb/8/libx265_main10.a"
  cmake -S "$xs" -B "$xb/8" "${CMAKE_COMMON[@]}" "${x265_opts[@]}" -DLINKED_10BIT=ON -DEXTRA_LIB=x265_main10.a \
    -DEXTRA_LINK_FLAGS=-L. >/dev/null
  cmake --build "$xb/8" -j "$JOBS" >/dev/null
  cmake --install "$xb/8" >/dev/null
  if [[ $OS == mac ]]; then
    libtool -static -o "$PREFIX/lib/libx265.a" "$xb/8/libx265.a" "$xb/8/libx265_main10.a" 2>/dev/null
  else
    # MinGW's ar can't use MSYS-style absolute paths in an MRI script, so merge inside the build folder.
    (cd "$xb/8" && printf 'CREATE libx265_all.a\nADDLIB libx265.a\nADDLIB libx265_main10.a\nSAVE\nEND\n' | ar -M)
    cp "$xb/8/libx265_all.a" "$PREFIX/lib/libx265.a"
  fi
  # A static build must not pull in the shared GCC runtime (Windows) — the C++ runtime is linked in statically below.
  [[ $OS == windows ]] && sed -i -E 's/ -lgcc_s//g; s/ -lgcc( |$)/ /g' "$PREFIX/lib/pkgconfig/x265.pc"

  step "SVT-AV1"
  cmake_lib "$SRC/svtav1" "$WORK/b/svtav1" -DBUILD_APPS=OFF -DBUILD_TESTING=OFF

  step "libaom (AV1 encoder only; dav1d decodes)"
  cmake_lib "$SRC/aom" "$WORK/b/aom" -DENABLE_DOCS=0 -DENABLE_EXAMPLES=0 -DENABLE_TESTDATA=0 -DENABLE_TESTS=0 \
    -DENABLE_TOOLS=0 -DCONFIG_AV1_DECODER=0 ${nasm[@]+"${nasm[@]}"}

  step "libvpx"
  local vpx_target=${VPX_TARGET:-x86_64-win64-gcc} vpx_as=(); [[ $X86 == 1 ]] && vpx_as=(--as=nasm)
  mkdir -p "$WORK/b/vpx"
  (cd "$WORK/b/vpx" && "$SRC/libvpx/configure" --prefix="$PREFIX" --target="$vpx_target" ${vpx_as[@]+"${vpx_as[@]}"} --enable-static \
     --disable-shared --enable-pic --enable-vp9-highbitdepth --disable-examples --disable-tools --disable-docs \
     --disable-unit-tests >/dev/null && make -j "$JOBS" >/dev/null && make install >/dev/null)

  step "dav1d"
  meson setup "$WORK/b/dav1d" "$SRC/dav1d" --prefix="$PREFIX" --libdir=lib --buildtype=release \
    --default-library=static -Denable_tools=false -Denable_tests=false >/dev/null
  ninja -C "$WORK/b/dav1d" install >/dev/null

  step "libwebp"
  cmake_lib "$SRC/libwebp" "$WORK/b/webp" -DWEBP_BUILD_ANIM_UTILS=OFF -DWEBP_BUILD_CWEBP=OFF -DWEBP_BUILD_DWEBP=OFF \
    -DWEBP_BUILD_GIF2WEBP=OFF -DWEBP_BUILD_IMG2WEBP=OFF -DWEBP_BUILD_VWEBP=OFF -DWEBP_BUILD_WEBPINFO=OFF \
    -DWEBP_BUILD_WEBPMUX=OFF -DWEBP_BUILD_EXTRAS=OFF -DWEBP_BUILD_LIBWEBPMUX=ON

  step "LAME"
  local lame_host=(); [[ $OS == mac ]] && lame_host=(--build="$HOST" --host="$HOST")  # its 2017 config.guess predates Apple silicon
  (cd "$SRC/lame" && ./configure --prefix="$PREFIX" ${lame_host[@]+"${lame_host[@]}"} --disable-shared --enable-static --disable-frontend \
     --disable-gtktest --disable-decoder >/dev/null && make -j "$JOBS" >/dev/null && make install >/dev/null)

  step "Opus, Ogg, Vorbis"
  cmake_lib "$SRC/opus" "$WORK/b/opus" -DOPUS_BUILD_PROGRAMS=OFF -DOPUS_BUILD_TESTING=OFF -DOPUS_BUILD_SHARED_LIBRARY=OFF \
    -DOPUS_STACK_PROTECTOR=OFF -DOPUS_FORTIFY_SOURCE=OFF
  cmake_lib "$SRC/libogg" "$WORK/b/ogg" -DBUILD_TESTING=OFF -DINSTALL_DOCS=OFF
  cmake_lib "$SRC/libvorbis" "$WORK/b/vorbis" -DBUILD_TESTING=OFF
}

# ---------------------------------------------------------------- FFmpeg

ffmpeg_configure() {  # ffmpeg_configure <build dir> [extra options...]
  local dir="$1"; shift
  local common=(--prefix="$dir/install" --disable-autodetect --enable-gpl --enable-version3 --disable-debug --disable-doc
                --disable-ffplay --disable-avdevice --enable-zlib --pkg-config-flags=--static
                --extra-cflags="-I$PREFIX/include" --extra-version="mediaconverter-mc$REV")
  if [[ $OS == windows ]]; then
    common+=(--enable-w32threads --enable-schannel --extra-ldflags="-L$PREFIX/lib -static -static-libgcc -static-libstdc++")
  else
    common+=(--enable-pthreads --enable-securetransport --extra-ldflags="-L$PREFIX/lib")
  fi
  mkdir -p "$dir"
  (cd "$dir" && "$SRC/ffmpeg/configure" "${common[@]}" "$@" > configure.log 2>&1) \
    || { tail -40 "$dir/config.log" 2>/dev/null || true; tail -20 "$dir/configure.log"; die "FFmpeg configure failed in $dir"; }
  (cd "$dir" && make -j "$JOBS" >/dev/null && make install >/dev/null)
}

build_ffmpeg() {
  step "FFmpeg $FFMPEG_VERSION: ffmpeg (full codec set)"
  ffmpeg_configure "$WORK/b/ff-full" --disable-ffprobe \
    --enable-libx264 --enable-libx265 --enable-libsvtav1 --enable-libaom --disable-decoder=libaom_av1 \
    --enable-libvpx --enable-libdav1d \
    --enable-libwebp --enable-libmp3lame --enable-libopus --enable-libvorbis
  step "FFmpeg $FFMPEG_VERSION: ffprobe (decode only)"
  ffmpeg_configure "$WORK/b/ff-probe" --disable-ffmpeg --disable-avfilter --disable-swscale --disable-swresample \
    --disable-encoders --disable-muxers --enable-libdav1d
}

# ---------------------------------------------------------------- checks

check_self_contained() {
  local f="$1"
  if [[ $OS == windows ]]; then
    local dll bad=0
    for dll in $(objdump -p "$f" | awk '/DLL Name:/ {print $3}'); do
      if [[ -e "/ucrt64/bin/$dll" ]]; then echo "  $f needs $dll from MSYS2"; bad=1; fi
    done
    [[ $bad == 0 ]] || die "$f is not self-contained"
  else
    if otool -L "$f" | tail -n +2 | grep -vE '^\s+(/usr/lib/|/System/Library/)'; then die "$f links to non-system libraries"; fi
  fi
  echo "  $(basename "$f"): only system libraries"
}

smoke_test() {
  step "Smoke test: every external encoder, plus probing and AV1 decoding"
  local ff="$1/ffmpeg$EXE" fp="$1/ffprobe$EXE" t="$WORK/test" src
  rm -rf "$t" && mkdir -p "$t"
  src=(-f lavfi -i "testsrc2=size=160x96:rate=10:duration=1" -f lavfi -i "sine=frequency=440:duration=1")
  local cases=(
    "h264.mp4|-c:v libx264 -pix_fmt yuv420p -c:a aac"
    "h265.mp4|-c:v libx265 -pix_fmt yuv420p -x265-params log-level=error -c:a aac"
    "h265-10.mkv|-c:v libx265 -pix_fmt yuv420p10le -x265-params log-level=error -an"
    "svtav1.mkv|-c:v libsvtav1 -preset 12 -pix_fmt yuv420p10le -an"
    "aom.mkv|-c:v libaom-av1 -cpu-used 8 -an"
    "vp9.webm|-c:v libvpx-vp9 -deadline realtime -pix_fmt yuva420p -c:a libopus"
    "vp8.webm|-c:v libvpx -deadline realtime -c:a libvorbis"
    "anim.webp|-c:v libwebp_anim -an"
    "audio.mp3|-vn -c:a libmp3lame"
    "audio.flac|-vn -c:a flac"
    "anim.gif|-an -c:v gif"
  )
  local c out args
  for c in "${cases[@]}"; do
    IFS='|' read -r out args <<<"$c"
    # shellcheck disable=SC2086
    "$ff" -v error -y "${src[@]}" $args "$t/$out"
    [[ -s "$t/$out" ]] || die "no output for $out"
    "$fp" -v error -show_entries stream=codec_name,pix_fmt -of csv=p=0 "$t/$out" | tr '\n' ' ' | sed "s|^|  $out: |"; echo
  done
  "$ff" -v error -c:v libdav1d -i "$t/svtav1.mkv" -f null - || die "dav1d could not decode AV1"
  "$fp" -v error -show_entries stream=pix_fmt -of csv=p=0 "$t/h265-10.mkv" | grep -q 10le || die "x265 did not produce 10-bit"
  echo "  all encoders work"
}

# ---------------------------------------------------------------- package

package() {
  step "Packaging"
  local stage="$WORK/stage/$NAME-$PLATFORM"
  rm -rf "$WORK/stage" && mkdir -p "$stage/bin" "$stage/licenses" "$OUT"
  cp "$WORK/b/ff-full/install/bin/ffmpeg$EXE" "$WORK/b/ff-probe/install/bin/ffprobe$EXE" "$stage/bin/"
  check_self_contained "$stage/bin/ffmpeg$EXE"
  check_self_contained "$stage/bin/ffprobe$EXE"
  smoke_test "$stage/bin"

  cp "$SRC/ffmpeg/COPYING.GPLv3" "$stage/LICENSE.txt"
  local lic
  for lic in "ffmpeg:LICENSE.md" "x264:COPYING" "x265:COPYING" "svtav1:LICENSE.md" "svtav1:PATENTS.md" "aom:LICENSE" \
             "aom:PATENTS" "libvpx:LICENSE" "libvpx:PATENTS" "dav1d:COPYING" "libwebp:COPYING" "libwebp:PATENTS" \
             "lame:COPYING" "opus:COPYING" "libogg:COPYING" "libvorbis:COPYING" "zlib:LICENSE"; do
    [[ -f "$SRC/${lic%%:*}/${lic#*:}" ]] && cp "$SRC/${lic%%:*}/${lic#*:}" "$stage/licenses/${lic%%:*}-${lic#*:}"
  done

  {
    echo "FFmpeg $FFMPEG_VERSION for Media Converter, build mc$REV ($PLATFORM)"
    echo
    echo "Built by build/ffmpeg/build-ffmpeg.sh in https://github.com/anglerequip/media-converter from these sources:"
    local entry name url sum repo ref commit
    for entry in "${TARBALLS[@]}"; do IFS='|' read -r name url sum <<<"$entry"; echo "  $name  $url  sha256 $sum"; done
    for entry in "${GITS[@]}"; do IFS='|' read -r name repo ref commit <<<"$entry"; echo "  $name  $repo  $ref  commit $commit"; done
    echo
    echo "Compiler: $("$CC" --version | head -1)"
    echo
    echo "ffmpeg -version:"; "$stage/bin/ffmpeg$EXE" -hide_banner -version | sed 's/^/  /'
    echo; echo "ffprobe -version:"; "$stage/bin/ffprobe$EXE" -hide_banner -version | sed 's/^/  /'
    echo; echo "SHA-256:"
    echo "  ffmpeg$EXE   $(sha256 "$stage/bin/ffmpeg$EXE")"
    echo "  ffprobe$EXE  $(sha256 "$stage/bin/ffprobe$EXE")"
  } > "$stage/BUILD-INFO.txt"
  cat > "$stage/README.txt" <<EOF
FFmpeg $FFMPEG_VERSION (https://ffmpeg.org), built for Media Converter.

License: GNU GPL v3 (see LICENSE.txt). The libraries linked in, and their licences, are listed in licenses/.
Source code for everything in this package: $NAME-sources.tar.gz, published next to this file, or
rebuild it yourself with build/ffmpeg/build-ffmpeg.sh. See BUILD-INFO.txt for exact versions and checksums.
EOF

  find "$stage" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} + 2>/dev/null || true
  rm -f "$OUT/$NAME-$PLATFORM.zip"
  (cd "$WORK/stage" && zip -q -X -r -9 "$OUT/$NAME-$PLATFORM.zip" "$NAME-$PLATFORM")

  # Corresponding source for the GPL: every input archive, plus this script.
  rm -rf "$WORK/srcpkg" && mkdir -p "$WORK/srcpkg/$NAME-sources"
  cp "$DL"/* "$HERE/build-ffmpeg.sh" "$WORK/srcpkg/$NAME-sources/"
  tar -C "$WORK/srcpkg" -czf "$OUT/$NAME-sources.tar.gz" "$NAME-sources"

  ls -l "$stage/bin"
  echo "  -> $OUT/$NAME-$PLATFORM.zip  ($(du -k "$OUT/$NAME-$PLATFORM.zip" | cut -f1) KB)"
}

fetch_sources
build_libs
build_ffmpeg
package
