"""Media Converter: the main window."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from tkinter import messagebox

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is optional; the buttons always work
    TkinterDnD = None

from converter import __version__
from converter.ffmpeg_tools import find_tool, install_hint
from ui import motion as M
from ui import theme as T
from ui.convert_view import ConvertView
from ui.download_view import DownloadView
from ui.widgets import (DetailsLoader, Segmented, Tooltip, fit_text, ghost_button, make_focusable, style_switch,
                        use_app_icon)

APP_DIR = Path(__file__).resolve().parent
if sys.platform == "win32":
    SETTINGS_FILE = Path(os.environ.get("APPDATA", Path.home())) / "MediaConverter" / "settings.json"
elif sys.platform == "darwin":
    SETTINGS_FILE = Path.home() / "Library" / "Application Support" / "MediaConverter" / "settings.json"
else:
    SETTINGS_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "MediaConverter" / "settings.json"
REPO_URL = "https://github.com/anglerequip/media-converter"


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


class App(ctk.CTk):
    def __init__(self, initial_files: list[str]) -> None:
        super().__init__(fg_color=T.BG)
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings.get("appearance", "System"))
        self.system_motion = M.system_allows_animation()
        M.set_enabled(self.settings.get("animations", True) and self.system_motion)
        M.window_fade_in(self)

        self.dnd = False
        if TkinterDnD is not None:
            try:
                TkinterDnD._require(self)
                self.dnd = True
            except Exception:
                pass

        self.title("Media Converter")
        self._fit_to_screen(self.settings.get("geometry", "1180x800"))
        icon = APP_DIR / "assets" / "icon.ico"
        if icon.exists():
            try:
                if sys.platform == "win32":
                    self.iconbitmap(str(icon))
                else:
                    self._icon_photo = ImageTk.PhotoImage(Image.open(APP_DIR / "assets" / "icon.png").resize((256, 256)))
                    self.iconphoto(True, self._icon_photo)
            except Exception:
                pass

        self.busy_count = 0
        self.details = DetailsLoader(self)
        self._toast_job = None
        self._leave_job = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        body = ctk.CTkFrame(self, fg_color=T.BG)
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(4, 20))
        self.current_view: str | None = None
        self._body = body
        self.convert_view = ConvertView(body, self)
        self._download_view: DownloadView | None = None  # built the first time it's opened (saves ~0.2 s at launch)
        self.convert_view.place(x=0, y=0, relwidth=1, relheight=1)
        self.views = {"convert": self.convert_view}
        self.show_view(self.settings.get("view", "convert"))

        self._build_toast()
        if self.dnd:
            self.register_drop(self)
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if initial_files:
            self.after(200, lambda: self.convert_view.add_paths([Path(p) for p in initial_files]))
        if not find_tool("ffmpeg"):
            self.after(800, lambda: self.toast("FFmpeg is missing, so only still images will convert. "
                                               "Open Settings for how to install it.", "error", 9000))
        self.after(300, self.refresh_icons)

    def refresh_icons(self, widget=None) -> None:
        """Re-apply every button icon. On Windows, Tk sometimes skips painting an image label that was
        created before the window first appeared, leaving an empty patch where the icon should be."""
        widget = widget or self
        if isinstance(widget, ctk.CTkButton) and getattr(widget, "_image_label", None) is not None:
            try:
                widget._update_image()
            except Exception:
                pass
        for child in widget.winfo_children():
            self.refresh_icons(child)

    def _fit_to_screen(self, geometry: str) -> None:
        """Open at the remembered (or default) size, but never larger than the screen or off its edges."""
        import re

        m = re.match(r"(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?", geometry or "")
        w, h = (int(m.group(1)), int(m.group(2))) if m else (1180, 800)
        scale = self._get_window_scaling() if hasattr(self, "_get_window_scaling") else 1.0
        screen_w = self.winfo_screenwidth() / scale  # CTk sizes windows in unscaled units
        screen_h = self.winfo_screenheight() / scale
        w, h = int(min(w, screen_w - 40)), int(min(h, screen_h - 90))  # room for the taskbar, Dock or menu bar
        self.minsize(min(1000, w), min(700, h))
        x = int(m.group(3)) if m and m.group(3) else None
        y = int(m.group(4)) if m and m.group(4) else None
        if x is None or y is None or not (0 <= x <= screen_w - 100 and 0 <= y <= screen_h - 100):
            x, y = int((screen_w - w) / 2), int(max(20, (screen_h - h) / 3))
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------ header

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color=T.BG, height=64)
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 10))
        head.grid_columnconfigure(1, weight=1)
        head.grid_columnconfigure(3, weight=1)

        brand = ctk.CTkFrame(head, fg_color=T.BG)
        brand.grid(row=0, column=0, sticky="w")
        logo_path = APP_DIR / "assets" / "icon.png"
        if logo_path.exists():
            logo = Image.open(logo_path)
            ctk.CTkLabel(brand, text="", image=ctk.CTkImage(logo, logo, size=(36, 36))).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        ctk.CTkLabel(brand, text="Media Converter", font=T.font(17, "semibold"), text_color=T.TEXT, height=22,
                     anchor="w").grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(brand, text="Convert images, videos, GIFs, and audio in seconds.", font=T.font(11),
                     text_color=T.MUTED, height=16, anchor="w").grid(row=1, column=1, sticky="nw")

        self.tabs = Segmented(
            head,
            [("convert", "Convert Files", f"{T.KEY}1", "convert"), ("download", "Download from Link", f"{T.KEY}2", "link")],
            command=self.show_view, height=40, font_size=13, min_segment=190,
        )
        self.tabs.grid(row=0, column=2)

        right = ctk.CTkFrame(head, fg_color=T.BG)
        right.grid(row=0, column=4, sticky="e")
        self.theme_switch = Segmented(
            right,
            [("System", "", "Follow macOS" if sys.platform == "darwin" else "Follow Windows", "pc"), ("Light", "", "Light theme", "sun"), ("Dark", "", "Dark theme", "moon")],
            command=self.set_theme, value=self.settings.get("appearance", "System"), height=34, equal=True,
        )
        self.theme_switch.grid(row=0, column=0, padx=(0, 8))
        # Styled like the theme switcher's track beside it.
        gear = ghost_button(right, icon_name="settings", command=self.open_settings, height=34, colour=T.MUTED,
                            bg=T.SURFACE_ALT, width=38)
        gear.configure(border_width=1, border_color=T.BORDER, corner_radius=T.RADIUS)
        gear.grid(row=0, column=1)
        Tooltip(gear, "Settings")

    @property
    def download_view(self) -> DownloadView:
        if self._download_view is None:
            self._download_view = DownloadView(self._body, self)
            self._download_view.lower()  # placed under the current screen until it is chosen
            self._download_view.place(x=0, y=0, relwidth=1, relheight=1)
            self.views["download"] = self._download_view
        return self._download_view

    def show_view(self, name: str) -> None:
        """Switch screens. Both stay alive, so files, links, results and running jobs are all kept."""
        if name not in ("convert", "download"):
            name = "convert"
        if name == "download":
            self.download_view  # noqa: B018  (build it now if this is the first visit)
        previous, self.current_view = self.current_view, name
        focus = str(self.tk.call("focus"))
        keyboard_on_tabs = focus == str(self.tabs.canvas)
        view = self.views[name]
        if previous != name:
            # Both screens share one layout (cards, bottom bar and main button in the same places) and stay
            # placed on top of each other; switching raises one over the other in a single repaint, so it reads as
            # the content changing while the shell stays still, and the switcher's pill carries the motion.
            # Measured on a mid-range PC: raising takes 50-75 ms; unmapping and mapping the screens took 155-165 ms,
            # and sliding a whole screen cost 16-140 ms per frame, which stuttered instead of gliding.
            view.lift()
            for key, other in self.views.items():
                if key != name and focus.startswith(str(other) + "."):
                    self.focus_set()  # don't leave typing going into the screen underneath
        self.tabs.set(name)
        self.settings["view"] = name
        if name == "download" and not keyboard_on_tabs and previous != name:
            # Ready for pasting, unless the switcher itself has keyboard focus (arrow keys keep working there).
            self.after_idle(self.download_view.entry.focus_set)

    def _tab(self, event, forward: bool) -> str:
        hidden = tuple(str(v) + "." for k, v in self.views.items() if k != self.current_view)
        widget = event.widget
        try:
            for _ in range(500):
                widget = widget.tk_focusNext() if forward else widget.tk_focusPrev()
                if widget is None or not str(widget).startswith(hidden):
                    break
            if widget is not None:
                self.tk.call("tk::TabToWindow", str(widget))  # Tk's own step: also selects an entry's text
        except (KeyError, AttributeError, tk.TclError):
            pass
        return "break"

    def current_screen(self):
        return self.views.get(self.current_view or "convert", self.convert_view)

    def set_theme(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.settings["appearance"] = mode
        if hasattr(self, "theme_switch"):
            self.theme_switch.set(mode)
            self.after(50, self.refresh_icons)

    # ------------------------------------------------------------------ drag and drop

    def register_drop(self, widget=None) -> None:
        """Accept dropped files anywhere in the window. Only the window itself is registered: Windows and macOS
        look up from the widget under the pointer to the first registered window, so registering every widget
        (hundreds, about 6 ms each) only slowed startup and adding files, and made the drop zone flicker."""
        if not self.dnd or getattr(self, "_drop_registered", False):
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<DropEnter>>", self._drop_enter)
            self.dnd_bind("<<DropLeave>>", self._drop_leave)
            self.dnd_bind("<<Drop>>", self._drop)
            self._drop_registered = True
        except Exception:
            pass

    def _drop_enter(self, event):
        if self._leave_job:
            self.after_cancel(self._leave_job)
            self._leave_job = None
        if self.tabs.get() == "convert" and not self.convert_view.busy:
            self.convert_view.drop.set_hover(True)
        return event.action

    def _drop_leave(self, event):
        if self._leave_job:
            self.after_cancel(self._leave_job)
        self._leave_job = self.after(120, lambda: self.convert_view.drop.set_hover(False))
        return event.action

    def _drop(self, event):
        self.convert_view.drop.set_hover(False)
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        if self.tabs.get() != "convert":
            self.show_view("convert")
        added = self.convert_view.add_paths(paths)
        if paths and not added and not self.convert_view.busy:
            self.toast("Those files are already in the list, or aren't a supported type.", "warning")
        return event.action

    # ------------------------------------------------------------------ shared services

    def reveal(self, path: Path) -> None:
        path = Path(path)
        if sys.platform == "win32":
            if path.is_file():  # full path to Explorer, so no other "explorer.exe" on the search path can run
                explorer = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                subprocess.Popen([explorer, "/select,", str(path)])
            elif path.exists():
                os.startfile(path)  # noqa: S606
        else:
            if sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", str(path)] if path.is_file() else ["/usr/bin/open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])

    def send_to_convert(self, path: Path) -> None:
        self.show_view("convert")
        if self.convert_view.add_paths([Path(path)]):
            self.toast(f"Added {Path(path).name} to the converter.", "success")

    def set_busy(self, busy: bool) -> None:
        self.busy_count = max(0, self.busy_count + (1 if busy else -1))

    def notify_done(self, ok: int, failed: int) -> None:
        """Flash the taskbar button when a job finishes while the window is in the background."""
        if sys.platform == "win32" and self.focus_displayof() is None:
            try:
                import ctypes

                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                ctypes.windll.user32.FlashWindow(hwnd, True)
            except Exception:
                pass

    # ------------------------------------------------------------------ toast

    def _build_toast(self) -> None:
        self.toast_frame = ctk.CTkFrame(self, corner_radius=T.RADIUS, border_width=1, fg_color=T.SURFACE,
                                        border_color=T.BORDER_STRONG)
        self.toast_icon = ctk.CTkLabel(self.toast_frame, text="", width=20)
        self.toast_icon.grid(row=0, column=0, padx=(12, 8), pady=10)
        self.toast_text = ctk.CTkLabel(self.toast_frame, text="", font=T.font(12), text_color=T.TEXT, wraplength=520,
                                       justify="left")
        self.toast_text.grid(row=0, column=1, padx=(0, 14), pady=10)

    def toast(self, text: str, tone: str = "info", ms: int = 4000) -> None:
        colour, icon_name = {"info": (T.ACCENT_TEXT, "info"), "success": (T.SUCCESS, "check_circle"),
                             "warning": (T.WARNING, "warning"), "error": (T.ERROR, "error_circle")}[tone]
        self.toast_icon.configure(image=T.icon(icon_name, 16, colour))
        self.toast_text.configure(text=text)
        self.toast_frame.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)

        def place(t: float) -> None:
            self.toast_frame.place(relx=0.5, rely=1.0, y=round(-24 + 60 * (1 - t)), anchor="s")

        M.animate(self.toast_frame, "slide", M.SLOW, place)
        self._toast_job = self.after(ms, self._hide_toast)

    def _hide_toast(self) -> None:
        def place(t: float) -> None:
            self.toast_frame.place(relx=0.5, rely=1.0, y=round(-24 + 80 * t), anchor="s")

        M.animate(self.toast_frame, "slide", M.NORMAL, place, done=self.toast_frame.place_forget, ease=M.ease_in_out)

    # ------------------------------------------------------------------ keyboard

    def _bind_keys(self) -> None:
        mod = "Command" if sys.platform == "darwin" else "Control"
        self.bind(f"<{mod}-o>", lambda _e: (self.show_view("convert"), self.convert_view.choose_files()))
        self.bind(f"<{mod}-Return>", lambda _e: self.current_screen().start())
        self.bind("<Escape>", lambda _e: self.current_screen().cancel())  # only the job on the screen you're looking at
        # Tab and Shift+Tab skip the screen underneath, which stays mapped so switching is quick.
        self.bind_all("<<NextWindow>>", lambda e: self._tab(e, True))
        self.bind_all("<<PrevWindow>>", lambda e: self._tab(e, False))
        self.bind(f"<{mod}-Key-1>", lambda _e: self.show_view("convert"))
        self.bind(f"<{mod}-Key-2>", lambda _e: self.show_view("download"))

    # ------------------------------------------------------------------ settings dialog

    def open_settings(self) -> None:
        if getattr(self, "_settings_win", None) is not None and self._settings_win.winfo_exists():
            self._settings_win.focus()
            return
        win = self._settings_win = ctk.CTkToplevel(self, fg_color=T.BG)
        win.title("Settings")
        win.geometry(f"460x600+{self.winfo_rootx() + self.winfo_width() - 500}+{self.winfo_rooty() + 70}")
        win.resizable(False, False)
        win.transient(self)
        M.window_fade_in(win, 160)
        use_app_icon(win)
        win.grid_columnconfigure(0, weight=1)

        def card(row: int, title: str) -> ctk.CTkFrame:
            c = ctk.CTkFrame(win, fg_color=T.SURFACE, corner_radius=T.RADIUS_LARGE, border_width=1, border_color=T.BORDER)
            c.grid(row=row, column=0, sticky="ew", padx=16, pady=(16 if row == 0 else 0, 12))
            c.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(c, text=title, font=T.font(13, "semibold"), text_color=T.TEXT, anchor="w").grid(
                row=0, column=0, sticky="w", padx=16, pady=(14, 6))
            return c

        def note(parent, text, colour=T.MUTED, row=1):
            lbl = ctk.CTkLabel(parent, text=text, font=T.font(11), text_color=colour, anchor="w", justify="left", wraplength=390)
            lbl.grid(row=row, column=0, sticky="w", padx=16, pady=(0, 6))
            return lbl

        c = card(0, "Appearance")
        theme = Segmented(c, [("System", "System", None, "pc"), ("Light", "Light", None, "sun"), ("Dark", "Dark", None, "moon")],
                          command=lambda v: (self.set_theme(v), self.theme_switch.set(v)),
                          value=self.settings.get("appearance", "System"))
        theme.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        anim = ctk.CTkSwitch(c, text="Animations", font=T.font(12, "semibold"), text_color=T.TEXT,
                             switch_width=38, switch_height=20)
        if self.settings.get("animations", True):
            anim.select()
        style_switch(anim)
        anim.grid(row=2, column=0, sticky="w", padx=16)
        make_focusable(anim)
        anim_note = note(c, "", row=3)

        def describe_motion() -> None:
            if not self.system_motion:
                setting = ("macOS's Reduce motion setting is on" if sys.platform == "darwin"
                           else "Windows' Animation effects setting is off")
                anim_note.configure(text=f"{setting}, so the app stays still too.", text_color=T.MUTED)
            else:
                anim_note.configure(text="Smooth fades and slides. Turn off for a completely still interface.",
                                    text_color=T.MUTED)

        def toggle_motion() -> None:
            style_switch(anim)
            self.settings["animations"] = bool(anim.get())
            M.set_enabled(self.settings["animations"] and self.system_motion)
            describe_motion()

        anim.configure(command=toggle_motion)
        describe_motion()
        anim_note.grid_configure(pady=(4, 14))

        c = card(1, "Video downloader")
        try:
            import yt_dlp

            version = yt_dlp.version.__version__
        except Exception:
            version = "not installed"
        note(c, f"yt-dlp {version} is built in. Media Converter never updates itself or anything else; if downloads "
                "from a site stop working, download the newest Media Converter from the project page.").grid_configure(
            pady=(0, 14))

        c = card(2, "FFmpeg")
        ff = find_tool("ffmpeg")
        if ff:
            note(c, "Installed. Used for video, audio and animations.", T.SUCCESS)
            path_lbl = note(c, fit_text(ff, T.font(11), 380), T.FAINT, row=2)
            path_lbl.grid_configure(pady=(0, 14))
            Tooltip(path_lbl, ff)
        else:
            note(c, f"Not found. {install_hint()}", T.ERROR).grid_configure(pady=(0, 14))

        c = card(3, "About")
        note(c, f"Media Converter {__version__}. Works offline: files are converted on this computer and never "
                "uploaded, and the app only goes online when you download from a link.")
        links = ctk.CTkFrame(c, fg_color="transparent")
        links.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 12))
        ghost_button(links, "Project page", lambda: webbrowser.open(REPO_URL), colour=T.ACCENT_TEXT).grid(row=0, column=0)

        def reset() -> None:
            if messagebox.askyesno("Reset settings", "Reset every setting to its default? The app will close.", parent=win):
                self.settings.clear()
                self._skip_save = True
                save_settings({})
                self.destroy()

        ghost_button(links, "Reset all settings", reset, colour=T.ERROR).grid(row=0, column=1, padx=6)
        win.after(100, win.lift)

    # ------------------------------------------------------------------ closing

    def _save_and_close(self) -> None:
        self.convert_view.cancel_event.set()
        if self._download_view is not None:
            self._download_view.cancel_event.set()
        if not getattr(self, "_skip_save", False):
            if self.state() == "normal":
                self.settings["geometry"] = self.geometry()
            save_settings(self.settings)
        self.details.shutdown()
        self.destroy()

    def on_close(self) -> None:
        if self.busy_count and not messagebox.askyesno(
                "Still working", "A conversion or download is still running. Stop it and quit?", parent=self):
            return
        self._save_and_close()


def _log_file() -> Path:
    return SETTINGS_FILE.parent / "log.txt"


def _setup_logging() -> None:
    """The packaged app has no console: send stray output and crash reports to a small log file."""
    if not getattr(sys, "frozen", False):
        return
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 512_000:
            path.unlink()
        log = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    sys.stdout = sys.stderr = log


def main() -> None:
    _setup_logging()
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":  # MediaConverter.exe --self-test results.json [url ...]
        from converter.selftest import run

        raise SystemExit(run(sys.argv[2], sys.argv[3:]))
    if sys.platform == "win32":
        try:  # own taskbar group and icon instead of Python's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MediaConverter.App")
        except Exception:
            pass
    app = App(sys.argv[1:])

    def report(exc, value, tb) -> None:
        import traceback

        print("".join(traceback.format_exception(exc, value, tb)), file=sys.stderr)
        try:
            app.toast(f"Something went wrong ({exc.__name__}). Details were saved to {_log_file()}", "error", 8000)
        except Exception:
            pass

    app.report_callback_exception = report
    app.mainloop()


if __name__ == "__main__":
    main()
