# Builds the Windows release from source:
#   dist\MediaConverter\            the app folder (MediaConverter.exe + libraries + FFmpeg)
#   dist\MediaConverter-Setup.exe   installer
#   dist\MediaConverter-portable.zip
#   dist\SHA256SUMS.txt             fingerprints of the two downloads
#   dist\BUILD-INFO.txt             every input that went into this build
#
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
#
# Nothing is taken from what happens to be installed on the build machine except Python itself:
#   - Python libraries come only from build\requirements-lock.txt (exact versions, SHA-256 checked by pip)
#   - FFmpeg and Inno Setup are downloaded from the pinned addresses below and checked against SHA-256
# The same script runs in GitHub Actions (.github\workflows\release.yml) to produce the published release.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---------------------------------------------------------------- pinned inputs
$PythonVersion = "3.12"   # major.minor; CI uses 3.12.10
$FFmpeg = @{
    Version = "9.0.2"
    Url     = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-full_build-shared.zip"
    Sha256  = "8d31e162f1616e37aab3fa2db991b97e1b8dbeb1c8465fd81a81be16ff91b328"
}
$InnoSetup = @{
    Version = "6.7.3"
    Url     = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
    Sha256  = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
}

function Fail($message) { Write-Host "BUILD FAILED: $message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }

function Get-Pinned($item, $name) {
    # Download once into build\cache and refuse anything whose fingerprint differs from the pinned one.
    $cache = New-Item -ItemType Directory -Force "build\cache"
    $file = Join-Path $cache (Split-Path -Leaf $item.Url)
    if (-not (Test-Path $file)) {
        Write-Host "Downloading $name $($item.Version)"
        Invoke-WebRequest -Uri $item.Url -OutFile $file -UseBasicParsing
    }
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $item.Sha256) {
        Remove-Item $file
        Fail "$name download does not match its pinned SHA-256 (expected $($item.Sha256), got $actual). Deleted."
    }
    Write-Host "$name $($item.Version): SHA-256 verified"
    return $file
}

# ---------------------------------------------------------------- Python
Step "Checking Python"
$hostPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $hostPython) { Fail "Python $PythonVersion is required (winget install Python.Python.3.12)" }
$pyVersion = (& python -c "import platform; print(platform.python_version())").Trim()
if (-not $pyVersion.StartsWith("$PythonVersion.")) { Fail "Python $PythonVersion.x is required, found $pyVersion" }
Write-Host "Python $pyVersion"

# ---------------------------------------------------------------- FFmpeg + Inno Setup
Step "Fetching pinned tools"
$ffZip = Get-Pinned $FFmpeg "FFmpeg"
$ffDir = "build\cache\ffmpeg-$($FFmpeg.Version)"
if (-not (Test-Path "$ffDir\bin\ffmpeg.exe")) {
    if (Test-Path $ffDir) { Remove-Item -Recurse -Force $ffDir }
    Expand-Archive $ffZip "build\cache\ffmpeg-extract" -Force
    Move-Item (Get-ChildItem "build\cache\ffmpeg-extract" -Directory | Select-Object -First 1).FullName $ffDir
    Remove-Item -Recurse -Force "build\cache\ffmpeg-extract"
}
$isExe = Get-Pinned $InnoSetup "Inno Setup"
$isDir = Join-Path $root "build\cache\innosetup-$($InnoSetup.Version)"
if (-not (Test-Path "$isDir\ISCC.exe")) {
    # Portable install into the build folder: nothing is registered on the machine.
    $p = Start-Process $isExe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/PORTABLE=1", "/DIR=`"$isDir`"" -PassThru
    $p.WaitForExit()
    if (-not (Test-Path "$isDir\ISCC.exe")) { Fail "Inno Setup could not be unpacked" }
}

# ---------------------------------------------------------------- clean Python environment
Step "Creating a fresh Python environment from the lock file"
if (Test-Path ".venv-build") { Remove-Item -Recurse -Force ".venv-build" }
python -m venv .venv-build
if ($LASTEXITCODE) { Fail "could not create venv" }
$py = Resolve-Path ".venv-build\Scripts\python.exe"
& $py -m pip install --disable-pip-version-check -q --require-hashes --no-deps -r build\requirements-lock.txt
if ($LASTEXITCODE) { Fail "pip install from the lock file failed" }
$version = (& $py -c "from converter import __version__; print(__version__)").Trim()
$commit = (git rev-parse HEAD 2>$null)
if (-not $commit) { $commit = "unknown" }
$dirty = if (git status --porcelain --untracked-files=no 2>$null) { " (with uncommitted changes)" } else { "" }
Write-Host "Version $version from commit $commit$dirty"

# Same input, same output: fixed hash seed and file times taken from the commit.
$env:PYTHONHASHSEED = "0"
$epoch = (git log -1 --format=%ct 2>$null)
if ($epoch) { $env:SOURCE_DATE_EPOCH = $epoch }

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
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath "build\work" "build\MediaConverter.spec"
if ($LASTEXITCODE -or -not (Test-Path "dist\MediaConverter\MediaConverter.exe")) { Fail "PyInstaller failed" }

# ---------------------------------------------------------------- FFmpeg + licences
Step "Adding FFmpeg"
$ff = New-Item -ItemType Directory -Force "dist\MediaConverter\ffmpeg"
Copy-Item "$ffDir\bin\*.dll", "$ffDir\bin\ffmpeg.exe", "$ffDir\bin\ffprobe.exe" $ff
Copy-Item "$ffDir\LICENSE" "$ff\LICENSE.txt"
@"
FFmpeg $($FFmpeg.Version) is included as separate programs (ffmpeg.exe, ffprobe.exe and their DLLs) under the GNU GPL v3; see LICENSE.txt.
Binary build: $($FFmpeg.Url)
SHA-256 of that download: $($FFmpeg.Sha256)
Source code: https://ffmpeg.org/releases/ffmpeg-$($FFmpeg.Version).tar.xz and the build scripts at https://github.com/GyanD/codexffmpeg
"@ | Set-Content -Encoding utf8 "$ff\README.txt"
Copy-Item "LICENSE" "dist\MediaConverter\LICENSE.txt"
Copy-Item "THIRD-PARTY-NOTICES.md" "dist\MediaConverter\THIRD-PARTY-NOTICES.md"

# ---------------------------------------------------------------- installer + portable zip
Step "Building the installer"
& "$isDir\ISCC.exe" /Q "/DAppVersion=$version" "build\installer.iss"
if ($LASTEXITCODE) { Fail "Inno Setup failed" }

Step "Building the portable zip"
Compress-Archive -Path "dist\MediaConverter" -DestinationPath "dist\MediaConverter-portable.zip" -CompressionLevel Optimal

# ---------------------------------------------------------------- fingerprints + build record
Step "Writing SHA256SUMS.txt and BUILD-INFO.txt"
$outputs = Get-Item "dist\MediaConverter-Setup.exe", "dist\MediaConverter-portable.zip"
$outputs | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name } |
    Set-Content -Encoding ascii "dist\SHA256SUMS.txt"
$lockHash = (Get-FileHash "build\requirements-lock.txt" -Algorithm SHA256).Hash.ToLower()
$exeHash = (Get-FileHash "dist\MediaConverter\MediaConverter.exe" -Algorithm SHA256).Hash.ToLower()
@"
Media Converter $version
Source commit:      $commit$dirty
Built:              $((Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm 'UTC'")) on $([System.Environment]::OSVersion.VersionString)
Python:             $((& $py -c "import platform; print(platform.python_version())").Trim())
Python libraries:   build/requirements-lock.txt (SHA-256 $lockHash), installed with --require-hashes
FFmpeg:             $($FFmpeg.Version)  $($FFmpeg.Url)  SHA-256 $($FFmpeg.Sha256)
Inno Setup:         $($InnoSetup.Version)  $($InnoSetup.Url)  SHA-256 $($InnoSetup.Sha256)
MediaConverter.exe: SHA-256 $exeHash

$(Get-Content "dist\SHA256SUMS.txt" -Raw)
"@ | Set-Content -Encoding utf8 "dist\BUILD-INFO.txt"

Step "Done"
Get-Content "dist\SHA256SUMS.txt"
$outputs | ForEach-Object { "{0,-32} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB) }
