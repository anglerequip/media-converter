"""Check GitHub for a newer version of Media Converter and install it.

Packaged app (MediaConverter.exe): the newest version is the latest GitHub Release. Installed copies
download that release's Setup.exe, check it against the fingerprint GitHub publishes, and run it
silently; it replaces the app and starts it again. Portable copies open the download page instead,
because a running program can't overwrite itself.

Running from source: the version is read from converter/__init__.py on the main branch, and the
update uses `git pull` (or a fresh zip of the source when the folder isn't a git clone), followed by
refreshing the Python packages.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "anglerequip/media-converter"
BRANCH = "main"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_HEADERS = {"User-Agent": f"MediaConverter/{__version__}"}

# Returned by install_update when an installer has been started: the app must now close itself.
INSTALLER_STARTED = "installer-started"

_release: dict = {}  # the release found by the last check


class UpdateError(Exception):
    """Something went wrong; the message is meant for the user."""


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def is_installed_copy() -> bool:
    """True for a copy installed with Setup.exe (it ships an uninstaller), False for the portable zip."""
    return FROZEN and any(APP_DIR.glob("unins*.exe"))


def _get(url: str, timeout: int = 15, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url, headers={**_HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise UpdateError("Couldn't reach GitHub. Check your internet connection and try again.") from exc


# ----------------------------------------------------------------------------- checking


def latest_version() -> tuple[str, list[str]]:
    """Return (newest version, short list of recent changes)."""
    return _latest_release() if FROZEN else _latest_source()


def _latest_release() -> tuple[str, list[str]]:
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    except UpdateError:
        raise
    except ValueError as exc:
        raise UpdateError("GitHub answered, but the release information couldn't be read.") from exc
    if not data.get("tag_name"):
        raise UpdateError("No released version was found on GitHub yet.")
    _release.clear()
    _release.update(data)
    notes = [line.strip().lstrip("-*• ").strip() for line in (data.get("body") or "").splitlines()]
    notes = [n for n in notes if n and not n.startswith("#")]
    return data["tag_name"].lstrip("vV"), notes[:5]


def _latest_source() -> tuple[str, list[str]]:
    try:  # the API is up to date the moment a change is pushed
        source = _get(f"https://api.github.com/repos/{REPO}/contents/converter/__init__.py?ref={BRANCH}",
                      headers={"Accept": "application/vnd.github.raw"}).decode("utf-8", "replace")
    except UpdateError:  # e.g. the hourly API limit: fall back to the raw file, which can lag a few minutes
        source = _get(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/converter/__init__.py").decode("utf-8", "replace")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    if not match:
        raise UpdateError("GitHub answered, but the version number couldn't be read.")
    notes: list[str] = []
    try:  # recent change titles are a nice-to-have; never fail the check over them
        commits = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits?sha={BRANCH}&per_page=5"))
        notes = [c["commit"]["message"].splitlines()[0] for c in commits][:5]
    except Exception:
        pass
    return match.group(1), notes


# ----------------------------------------------------------------------------- installing


def install_update(progress: Callable[[str], None], app_dir: Path = APP_DIR) -> str:
    """Install the newest version. Returns a sentence for the user, or INSTALLER_STARTED."""
    if FROZEN:
        return _install_release(progress)
    return _install_source(progress, app_dir)


def _install_release(progress: Callable[[str], None]) -> str:
    if not _release:
        latest_version()
    if not is_installed_copy():
        webbrowser.open(_release.get("html_url") or RELEASES_PAGE)
        return ("This is the portable version, so it can't replace itself. The download page is now open in "
                "your browser: download the new portable zip and use it instead of this folder.")
    asset = next((a for a in _release.get("assets", []) if a.get("name", "").lower().endswith("setup.exe")), None)
    if not asset:
        raise UpdateError("The newest release has no installer to download yet. Try again later.")

    target = Path(tempfile.gettempdir()) / asset["name"]
    request = urllib.request.Request(asset["browser_download_url"], headers=_HEADERS)
    digest = hashlib.sha256()
    total = int(asset.get("size") or 0)
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as fh:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    progress(f"Downloading the update… {done * 100 // total}%")
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise UpdateError("The download was interrupted. Check your internet connection and try again.") from exc

    if total and done != total:
        target.unlink(missing_ok=True)
        raise UpdateError("The download was incomplete, so nothing was installed. Please try again.")
    expected = str(asset.get("digest") or "")
    if expected.startswith("sha256:") and expected.split(":", 1)[1].lower() != digest.hexdigest():
        target.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer didn't match GitHub's fingerprint, so it was deleted "
                          "and nothing was installed.")

    progress("Starting the installer…")
    # Silent install over the current copy; the installer closes this app if needed and starts it again.
    subprocess.Popen([str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=_NO_WINDOW)
    return INSTALLER_STARTED


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")


def _python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def _install_source(progress: Callable[[str], None], app_dir: Path) -> str:
    git = shutil.which("git")
    if (app_dir / ".git").is_dir() and git:
        progress("Checking the app folder…")
        dirty = _run([git, "status", "--porcelain", "--untracked-files=no"], app_dir)
        if dirty.stdout.strip():
            raise UpdateError("Files in the app folder have been changed by hand, so the update was stopped to "
                              f"avoid overwriting them. Folder: {app_dir}")
        progress("Downloading the update…")
        pull = _run([git, "pull", "--ff-only", "origin", BRANCH], app_dir)
        if pull.returncode != 0:
            detail = (pull.stderr or pull.stdout).strip().splitlines()[-1:] or ["unknown error"]
            raise UpdateError(f"The update couldn't be downloaded: {detail[0]}")
        method = "Updated with git."
    else:
        progress("Downloading the update…")
        data = _get(f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}", timeout=60)
        progress("Installing the new files…")
        with tempfile.TemporaryDirectory(prefix="mediaconv_update_") as tmp:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extractall(tmp)
            roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
            if len(roots) != 1 or not (roots[0] / "app.py").exists():
                raise UpdateError("The downloaded update didn't look right, so nothing was changed.")
            for src in roots[0].rglob("*"):
                if src.is_file():
                    dest = app_dir / src.relative_to(roots[0])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
        method = "Downloaded and installed the latest version."

    progress("Updating the parts the app depends on…")
    deps = _run([_python(), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                 str(app_dir / "requirements.txt")], app_dir)
    if deps.returncode != 0:
        method += " Some supporting packages could not be updated; the app should still work."
    return method


def restart(app_dir: Path = APP_DIR) -> None:
    """Start a fresh copy of the app (without a console window). The caller then closes this one."""
    if FROZEN:
        subprocess.Popen([sys.executable], cwd=str(app_dir), creationflags=_NO_WINDOW)
        return
    exe = Path(sys.executable)
    pythonw = exe.parent / "pythonw.exe"
    runner = str(pythonw if pythonw.exists() else exe)
    subprocess.Popen([runner, str(app_dir / "MediaConverter.pyw")], cwd=str(app_dir), creationflags=_NO_WINDOW)
