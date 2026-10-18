"""Check GitHub for a newer version of Media Converter and install it.

The version on GitHub is read from converter/__init__.py on the main branch. Installing uses
`git pull` when the app folder is a git clone, and otherwise downloads the latest source as a
zip and copies it over the app folder. Either way the Python packages are refreshed afterwards.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "anglerequip/media-converter"
BRANCH = "main"
APP_DIR = Path(__file__).resolve().parent.parent
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_HEADERS = {"User-Agent": f"MediaConverter/{__version__}"}


class UpdateError(Exception):
    """Something went wrong; the message is meant for the user."""


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3]) or (0,)


def _get(url: str, timeout: int = 15, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url, headers={**_HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise UpdateError("Couldn't reach GitHub. Check your internet connection and try again.") from exc


def latest_version() -> tuple[str, list[str]]:
    """Return (newest version on GitHub, short list of recent changes)."""
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


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")


def _python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def install_update(progress: Callable[[str], None], app_dir: Path = APP_DIR) -> str:
    """Bring the app folder up to date. Returns a sentence describing what was done."""
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
    exe = Path(sys.executable)
    pythonw = exe.parent / "pythonw.exe"
    runner = str(pythonw if pythonw.exists() else exe)
    subprocess.Popen([runner, str(app_dir / "MediaConverter.pyw")], cwd=str(app_dir), creationflags=_NO_WINDOW)
