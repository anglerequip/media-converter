# Builds the Windows release: dist\MediaConverter\ (the app), MediaConverter-Setup.exe and MediaConverter-portable.zip.
# The file names carry no version, so github.com/.../releases/latest/download/<name> always gets the newest one.
#
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
#
# Needs: Python 3.10+, the shared FFmpeg build (winget install Gyan.FFmpeg.Shared) and
# Inno Setup 6 (winget install JRSoftware.InnoSetup). Set FFMPEG_SHARED_DIR to use another FFmpeg folder.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($message) { Write-Host "BUILD FAILED: $message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }

# ---------------------------------------------------------------- tools
Step "Checking tools"
$ffmpegDir = $env:FFMPEG_SHARED_DIR
if (-not $ffmpegDir) {
    $ffmpegDir = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter "Gyan.FFmpeg.Shared*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Directory } | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ffmpegDir -or -not (Test-Path "$ffmpegDir\bin\avcodec-*.dll")) { Fail "Shared FFmpeg not found. Run: winget install Gyan.FFmpeg.Shared" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "C:\Program Files (x86)\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Fail "Inno Setup 6 not found. Run: winget install JRSoftware.InnoSetup" }
Write-Host "FFmpeg:     $ffmpegDir"
Write-Host "Inno Setup: $iscc"

# ---------------------------------------------------------------- clean build environment
Step "Preparing a clean Python environment (.venv-build)"
if (-not (Test-Path ".venv-build\Scripts\python.exe")) { python -m venv .venv-build; if ($LASTEXITCODE) { Fail "could not create venv" } }
$py = Resolve-Path ".venv-build\Scripts\python.exe"
& $py -m pip install --disable-pip-version-check -q --upgrade pip
& $py -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
& $py -m pip install --disable-pip-version-check -q --upgrade --pre "yt-dlp[default,curl-cffi]"  # newest site fixes
if ($LASTEXITCODE) { Fail "pip install failed" }
$version = (& $py -c "from converter import __version__; print(__version__)").Trim()
Write-Host "Version:    $version"

# ---------------------------------------------------------------- version resource for the .exe
$parts = ($version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'anglerequip'),
      StringStruct('FileDescription', 'Media Converter'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'MediaConverter'),
      StringStruct('OriginalFilename', 'MediaConverter.exe'),
      StringStruct('ProductName', 'Media Converter'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding utf8 "build\version_info.txt"

# ---------------------------------------------------------------- PyInstaller
Step "Building MediaConverter.exe"
if (Test-Path "dist\MediaConverter") { Remove-Item -Recurse -Force "dist\MediaConverter" }
& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath "build\work" "build\MediaConverter.spec"
if ($LASTEXITCODE -or -not (Test-Path "dist\MediaConverter\MediaConverter.exe")) { Fail "PyInstaller failed" }

# ---------------------------------------------------------------- FFmpeg + licences
Step "Adding FFmpeg"
$ff = New-Item -ItemType Directory -Force "dist\MediaConverter\ffmpeg"
Copy-Item "$ffmpegDir\bin\*.dll", "$ffmpegDir\bin\ffmpeg.exe", "$ffmpegDir\bin\ffprobe.exe" $ff
Copy-Item "$ffmpegDir\LICENSE" "$ff\LICENSE.txt"
@"
FFmpeg is included as separate programs (ffmpeg.exe, ffprobe.exe) under the GNU GPL v3; see LICENSE.txt.
Build: $(Split-Path -Leaf $ffmpegDir) from https://www.gyan.dev/ffmpeg/builds/
Source code: https://ffmpeg.org/download.html and https://github.com/GyanD/codexffmpeg
"@ | Set-Content -Encoding utf8 "$ff\README.txt"
Copy-Item "LICENSE" "dist\MediaConverter\LICENSE.txt"

# ---------------------------------------------------------------- installer + portable zip
Step "Building the installer"
& $iscc /Q "/DAppVersion=$version" "build\installer.iss"
if ($LASTEXITCODE) { Fail "Inno Setup failed" }

Step "Building the portable zip"
$zip = "dist\MediaConverter-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\MediaConverter" -DestinationPath $zip -CompressionLevel Optimal

Step "Done"
Get-ChildItem "dist\MediaConverter-Setup.exe", $zip | ForEach-Object {
    "{0,-45} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)
}
