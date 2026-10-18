# Installs everything Media Converter needs and adds Desktop + Start menu shortcuts.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "1/3  Python packages" -ForegroundColor Cyan
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host "Python 3.10+ is required. Install it with:  winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
& $python -m pip install --disable-pip-version-check --upgrade --quiet -r (Join-Path $root "requirements.txt")
& $python -m pip install --disable-pip-version-check --upgrade --quiet --pre "yt-dlp[default,curl-cffi]"   # newest site fixes

Write-Host "2/3  FFmpeg" -ForegroundColor Cyan
$hasFfmpeg = & $python -c "import sys; sys.path.insert(0, r'$root'); from converter.ffmpeg_tools import find_tool; print(bool(find_tool('ffmpeg') and find_tool('ffprobe')))"
if ($hasFfmpeg -ne "True") {
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent
} else {
    Write-Host "     already installed"
}

Write-Host "3/3  Shortcuts" -ForegroundColor Cyan
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Media Converter.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Media Converter.lnk")
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = "`"$(Join-Path $root 'MediaConverter.pyw')`""
    $s.WorkingDirectory = $root
    $s.IconLocation = (Join-Path $root "assets\icon.ico")
    $s.Description = "Convert images, GIFs, video and audio; download video links"
    $s.Save()
    Write-Host "     $lnk"
}
Write-Host "`nDone. Start Media Converter from the Desktop or Start menu." -ForegroundColor Green
