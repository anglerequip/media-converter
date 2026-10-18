"""Media Converter: the main window."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is optional; the buttons always work
    TkinterDnD = None

from converter import __version__
from converter.ffmpeg_tools import find_tool
from ui import motion as M
from ui import theme as T
from ui.convert_view import ConvertView
from ui.download_view import DownloadView
from ui.widgets import DetailsLoader, Segmented, Tooltip, fit_text, ghost_button

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = Path(os.environ.get("APPDATA", Path.home())) / "MediaConverter" / "settings.json"
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
        self.geometry(self.settings.get("geometry", "1180x800"))
        self.minsize(1000, 700)
        icon = APP_DIR / "assets" / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
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
        self.convert_view = ConvertView(body, self)
        self.download_view = DownloadView(body, self)
        self.views = {"convert": self.convert_view, "download": self.download_view}
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
            [("convert", "Convert Files", "Ctrl+1", "convert"), ("download", "Download from Link", "Ctrl+2", "link")],
            command=self.show_view, height=40, font_size=13,
        )
        self.tabs.grid(row=0, column=2)
        for b in self.tabs.buttons.values():
            b.configure(width=190)

        right = ctk.CTkFrame(head, fg_color=T.BG)
        right.grid(row=0, column=4, sticky="e")
        self.theme_switch = Segmented(
            right,
            [("System", "", "Follow Windows", "pc"), ("Light", "", "Light theme", "sun"), ("Dark", "", "Dark theme", "moon")],
            command=self.set_theme, value=self.settings.get("appearance", "System"), height=34, equal=True,
        )
        self.theme_switch.grid(row=0, column=0, padx=(0, 8))
        # Styled like the theme switcher's track beside it.
        gear = ghost_button(right, icon_name="settings", command=self.open_settings, height=34, colour=T.MUTED,
                            bg=T.SURFACE_ALT, width=38)
        gear.configure(border_width=1, border_color=T.BORDER, corner_radius=T.RADIUS)
        gear.grid(row=0, column=1)
        Tooltip(gear, "Settings")

    def show_view(self, name: str) -> None:
        if name not in ("convert", "download"):
            name = "convert"
        previous, self.current_view = self.current_view, name
        for key, view in self.views.items():
            if key != name:
                M.cancel(view, "slide")
                view.place_forget()
        view = self.views[name]
        if previous is None or previous == name:
            view.place(x=0, y=0, relwidth=1, relheight=1)
        else:
            # Glide in from the side the tab sits on.
            M.slide_in(view, dx=36 if name == "download" else -36, y=0, relwidth=1, relheight=1)
        view.lift()
        self.tabs.set(name)
        self.settings["view"] = name
        if name == "download":
            self.after(50, self.download_view.entry.focus_set)

    def set_theme(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.settings["appearance"] = mode
        if hasattr(self, "theme_switch"):
            self.theme_switch.set(mode)
            self.after(50, self.refresh_icons)

    # ------------------------------------------------------------------ drag and drop

    def register_drop(self, widget) -> None:
        if not self.dnd:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._drop_enter)
            widget.dnd_bind("<<DropLeave>>", self._drop_leave)
            widget.dnd_bind("<<Drop>>", self._drop)
        except Exception:
            pass
        for child in widget.winfo_children():
            self.register_drop(child)

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
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path if path.is_dir() else path.parent)])

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
        self.bind("<Control-o>", lambda _e: (self.show_view("convert"), self.convert_view.choose_files()))
        self.bind("<Control-Return>", lambda _e: self.convert_view.start() if self.tabs.get() == "convert" else self.download_view.start())
        self.bind("<Escape>", lambda _e: (self.convert_view.cancel(), self.download_view.cancel()))
        self.bind("<Control-Key-1>", lambda _e: self.show_view("convert"))
        self.bind("<Control-Key-2>", lambda _e: self.show_view("download"))

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
        ico = APP_DIR / "assets" / "icon.ico"
        if ico.exists():
            win.after(250, lambda: win.iconbitmap(str(ico)))
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
                             progress_color=T.ACCENT, button_color=("#FFFFFF", "#F1F3F6"),
                             button_hover_color=("#FFFFFF", "#FFFFFF"), fg_color=T.BORDER_STRONG,
                             switch_width=38, switch_height=20)
        if self.settings.get("animations", True):
            anim.select()
        anim.grid(row=2, column=0, sticky="w", padx=16)
        anim_note = note(c, "", row=3)

        def describe_motion() -> None:
            if not self.system_motion:
                anim_note.configure(text="Windows' 'Animation effects' setting is off, so the app stays still too.",
                                    text_color=T.MUTED)
            else:
                anim_note.configure(text="Smooth fades and slides. Turn off for a completely still interface.",
                                    text_color=T.MUTED)

        def toggle_motion() -> None:
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
                "from a site stop working, download the newest Media Converter from the project page.")

        c = card(2, "FFmpeg")
        ff = find_tool("ffmpeg")
        if ff:
            note(c, "Installed. Used for video, audio and animations.", T.SUCCESS)
            path_lbl = note(c, fit_text(ff, T.font(11), 380), T.FAINT, row=2)
            Tooltip(path_lbl, ff)
        else:
            note(c, "Not found. Install it by running this in a terminal, then restart the app:", T.ERROR)
            note(c, "winget install Gyan.FFmpeg", T.TEXT, row=2)

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
        self.download_view.cancel_event.set()
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
