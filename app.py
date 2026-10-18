"""Media Converter: desktop window."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is optional; the "choose files" button always works
    TkinterDnD = None

from converter import __version__
from converter.downloader import MODES, download
from converter.engine import QUALITY_LABELS, VIDEO_CODECS, Options, convert
from converter.ffmpeg_tools import Cancelled, ConversionError, find_tool
from converter.formats import AUDIO_IN, DESCRIPTIONS, IMAGE_IN, VIDEO_IN, kind_of, targets_for

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = Path(os.environ.get("APPDATA", Path.home())) / "MediaConverter" / "settings.json"
KIND_LABELS = {"image": "Image", "animation": "Animation", "video": "Video", "audio": "Audio", None: "Not supported"}
SAME_FOLDER, OTHER_FOLDER = "Same folder as each file", "Choose a folder"
DOWNLOADS = Path.home() / "Downloads"

GREEN, RED, GREY, BLUE = ("#1a7f37", "#4ac26b"), ("#cf222e", "#ff7b72"), ("gray40", "gray65"), ("#0969da", "#58a6ff")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def reveal(path: Path) -> None:
    """Open Explorer with the file selected (or the folder opened)."""
    if sys.platform == "win32":
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path if path.is_dir() else path.parent)])


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class FileRow:
    """One file in the conversion list."""

    def __init__(self, app: "App", parent, path: Path) -> None:
        self.app = app
        self.path = path
        self.kind = kind_of(path)
        self.state = "queued" if self.kind else "unsupported"
        self.output: Path | None = None
        self.error = ""

        self.frame = ctk.CTkFrame(parent, corner_radius=8, fg_color=("gray90", "gray20"))
        self.frame.grid_columnconfigure(0, weight=1)
        self.name = ctk.CTkLabel(self.frame, text=shorten(path.name, 60), anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        self.name.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(8, 0))
        self.kind_label = ctk.CTkLabel(self.frame, text=KIND_LABELS[self.kind], text_color=GREY, width=90, anchor="e")
        self.kind_label.grid(row=0, column=1, padx=6, pady=(8, 0))
        self.remove = ctk.CTkButton(self.frame, text="✕", width=28, height=24, fg_color="transparent",
                                    text_color=GREY, hover_color=("gray80", "gray30"), command=lambda: app.remove_row(self))
        self.remove.grid(row=0, column=2, padx=(0, 8), pady=(8, 0))
        self.status = ctk.CTkLabel(self.frame, text="", anchor="w", text_color=GREY, cursor="hand2")
        self.status.grid(row=1, column=0, columnspan=2, sticky="w", padx=12)
        self.status.bind("<Button-1>", lambda _e: self.on_status_click())
        self.bar = ctk.CTkProgressBar(self.frame, height=4)
        self.bar.set(0)
        self.bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 10))
        self.set_state(self.state)

    def set_state(self, state: str, text: str = "", fraction: float | None = None) -> None:
        self.state = state
        colour = {"done": GREEN, "failed": RED, "working": BLUE}.get(state, GREY)
        default = {
            "queued": "Ready",
            "unsupported": "This file type is not supported",
            "working": "Working…",
            "done": f"Done: {self.output.name if self.output else ''}  (click to show)",
            "failed": "Failed (click for details)",
            "skipped": "Skipped",
            "cancelled": "Cancelled",
        }[state]
        self.status.configure(text=shorten(text or default, 95), text_color=colour)
        if fraction is not None:
            self.bar.set(fraction)
        elif state == "done":
            self.bar.set(1)
        elif state in ("queued", "failed", "skipped", "cancelled", "unsupported"):
            self.bar.set(0)

    def on_status_click(self) -> None:
        if self.state == "done" and self.output:
            reveal(self.output)
        elif self.state == "failed":
            messagebox.showerror("Conversion failed", f"{self.path.name}\n\n{self.error}", parent=self.app)


class App(ctk.CTk):
    def __init__(self, initial_files: list[str]) -> None:
        super().__init__()
        self.dnd = False
        if TkinterDnD is not None:
            try:
                TkinterDnD._require(self)
                self.dnd = True
            except Exception:
                pass

        self.title("Media Converter")
        self.geometry("900x760")
        self.minsize(780, 640)
        icon = APP_DIR / "assets" / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

        self.settings = load_settings()
        self.events: queue.Queue = queue.Queue()
        self.rows: list[FileRow] = []
        self.cancel_event = threading.Event()
        self.busy = False
        self.target_lookup: dict[str, str] = {}
        self.out_dir = Path(self.settings.get("out_dir") or DOWNLOADS)
        self.dl_dir = Path(self.settings.get("dl_dir") or DOWNLOADS)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self.tabs = ctk.CTkTabview(self, corner_radius=10)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._build_convert_tab(self.tabs.add("Convert files"))
        self._build_download_tab(self.tabs.add("Download from link"))
        if self.settings.get("tab") in ("Convert files", "Download from link"):
            self.tabs.set(self.settings["tab"])

        self.add_paths([Path(p) for p in initial_files])
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self.poll_events)

    # ------------------------------------------------------------------ layout

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Media Converter", font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head,
            text="Images, GIFs, video, audio and video links. Everything is processed on this computer.",
            text_color=GREY,
        ).grid(row=1, column=0, sticky="w")
        self.appearance = ctk.CTkSegmentedButton(head, values=["System", "Light", "Dark"], command=self.set_appearance, width=180)
        self.appearance.grid(row=0, column=1, rowspan=2, sticky="e")
        mode = self.settings.get("appearance", "System")
        self.appearance.set(mode)
        ctk.set_appearance_mode(mode)
        if not find_tool("ffmpeg"):
            ctk.CTkLabel(
                head,
                text="FFmpeg is missing, so only still images will convert. Install it with:  winget install Gyan.FFmpeg",
                text_color=RED,
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_convert_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        drop = ctk.CTkFrame(tab, height=96, corner_radius=10, border_width=2, border_color=("gray70", "gray35"))
        drop.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        drop.grid_propagate(False)
        drop.grid_columnconfigure(0, weight=1)
        drop.grid_rowconfigure((0, 1), weight=1)
        hint = "Drop files or folders here, or click to choose" if self.dnd else "Click to choose files"
        title = ctk.CTkLabel(drop, text=hint, font=ctk.CTkFont(size=16, weight="bold"))
        title.grid(row=0, column=0, sticky="s")
        sub = ctk.CTkLabel(drop, text="PNG · JPG · WebP · GIF · HEIC · AVIF · TIFF · MP4 · MOV · MKV · WebM · MP3 · WAV · FLAC · and more",
                           text_color=GREY)
        sub.grid(row=1, column=0, sticky="n")
        for w in (drop, title, sub):
            w.bind("<Button-1>", lambda _e: self.choose_files())
            w.configure(cursor="hand2")

        self.file_list = ctk.CTkScrollableFrame(tab, corner_radius=10, fg_color=("gray95", "gray14"))
        self.file_list.grid(row=1, column=0, sticky="nsew", padx=4)
        self.file_list.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.file_list, text="No files yet.", text_color=GREY)
        self.empty_label.grid(row=0, column=0, pady=30)

        if self.dnd:
            for w in (drop, title, sub, self.file_list, self.file_list._parent_canvas):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self.on_drop)

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=4, pady=(10, 0))
        for col in range(4):
            opts.grid_columnconfigure(col, weight=1, uniform="opts")

        ctk.CTkLabel(opts, text="Convert to", anchor="w").grid(row=0, column=0, sticky="w")
        self.target_menu = ctk.CTkOptionMenu(opts, values=["Add files first"], dynamic_resizing=False)
        self.target_menu.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkLabel(opts, text="Quality", anchor="w").grid(row=0, column=1, sticky="w")
        self.quality_menu = ctk.CTkOptionMenu(opts, values=list(QUALITY_LABELS.values()), dynamic_resizing=False)
        self.quality_menu.set(QUALITY_LABELS.get(self.settings.get("quality", "max"), QUALITY_LABELS["max"]))
        self.quality_menu.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ctk.CTkLabel(opts, text="Video codec (MP4 / MKV / MOV)", anchor="w").grid(row=0, column=2, sticky="w")
        self.codec_menu = ctk.CTkOptionMenu(opts, values=list(VIDEO_CODECS.values()), dynamic_resizing=False)
        self.codec_menu.set(VIDEO_CODECS.get(self.settings.get("codec", "h264"), VIDEO_CODECS["h264"]))
        self.codec_menu.grid(row=1, column=2, sticky="ew", padx=(0, 8))

        ctk.CTkLabel(opts, text="Save to", anchor="w").grid(row=0, column=3, sticky="w")
        self.out_menu = ctk.CTkOptionMenu(opts, values=[SAME_FOLDER, OTHER_FOLDER], command=self.on_out_mode, dynamic_resizing=False)
        self.out_menu.set(self.settings.get("out_mode", SAME_FOLDER))
        self.out_menu.grid(row=1, column=3, sticky="ew")

        self.keep_streams = ctk.CTkCheckBox(
            opts, text="Copy video/audio unchanged when the new format allows it (fastest, zero quality loss)"
        )
        if self.settings.get("keep_streams", True):
            self.keep_streams.select()
        self.keep_streams.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.out_label = ctk.CTkLabel(opts, text="", text_color=GREY, anchor="e", cursor="hand2")
        self.out_label.grid(row=2, column=3, sticky="e", pady=(10, 0))
        self.out_label.bind("<Button-1>", lambda _e: self.pick_out_dir())
        self.update_out_label()

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=4, pady=(12, 4))
        bottom.grid_columnconfigure(0, weight=1)
        self.total_bar = ctk.CTkProgressBar(bottom, height=8)
        self.total_bar.set(0)
        self.total_bar.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))
        self.convert_status = ctk.CTkLabel(bottom, text="Add files to begin.", anchor="w", text_color=GREY)
        self.convert_status.grid(row=1, column=0, sticky="w")
        ctk.CTkButton(bottom, text="Clear list", width=100, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self.clear_rows).grid(row=1, column=1, padx=4)
        ctk.CTkButton(bottom, text="Open folder", width=110, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self.open_convert_folder).grid(row=1, column=2, padx=4)
        self.cancel_btn = ctk.CTkButton(bottom, text="Cancel", width=90, fg_color=("gray75", "gray30"), hover_color=("#a40e26", "#da3633"),
                                        text_color_disabled=("gray45", "gray60"),
                                        command=self.cancel, state="disabled")
        self.cancel_btn.grid(row=1, column=3, padx=4)
        self.convert_btn = ctk.CTkButton(bottom, text="Convert", width=140, height=36,
                                         font=ctk.CTkFont(size=14, weight="bold"), command=self.start_convert)
        self.convert_btn.grid(row=1, column=4, padx=(4, 0))

    def _build_download_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            tab,
            text="Paste one or more links, one per line. Works with YouTube, TikTok, Instagram, X / Twitter, Reddit,\n"
                 "Facebook, Vimeo, Twitch clips, SoundCloud and well over a thousand other sites.",
            justify="left", anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(4, 6))

        self.url_box = ctk.CTkTextbox(tab, height=120, corner_radius=10, wrap="none")
        self.url_box.grid(row=1, column=0, sticky="ew", padx=4)

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=4, pady=(8, 0))
        row.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(row, text="Paste", width=80, command=self.paste_links).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(row, text="Clear", width=80, fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
                      command=lambda: self.url_box.delete("1.0", "end")).grid(row=0, column=1)

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=3, column=0, sticky="ew", padx=4, pady=(12, 0))
        opts.grid_columnconfigure((0, 1), weight=1, uniform="dl")
        ctk.CTkLabel(opts, text="Save as", anchor="w").grid(row=0, column=0, sticky="w")
        self.mode_menu = ctk.CTkOptionMenu(opts, values=list(MODES.values()), dynamic_resizing=False)
        self.mode_menu.set(MODES.get(self.settings.get("dl_mode", "best"), MODES["best"]))
        self.mode_menu.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(opts, text="Save to", anchor="w").grid(row=0, column=1, sticky="w")
        self.dl_dir_btn = ctk.CTkButton(opts, text="", anchor="w", fg_color="transparent", border_width=1,
                                        text_color=("gray10", "gray90"), command=self.pick_dl_dir)
        self.dl_dir_btn.grid(row=1, column=1, sticky="ew")
        self.playlist = ctk.CTkCheckBox(opts, text="If the link is a playlist, download every video in it")
        if self.settings.get("playlist"):
            self.playlist.select()
        self.playlist.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.update_dl_label()

        prog = ctk.CTkFrame(tab, fg_color="transparent")
        prog.grid(row=4, column=0, sticky="ew", padx=4, pady=(12, 6))
        prog.grid_columnconfigure(0, weight=1)
        self.dl_bar = ctk.CTkProgressBar(prog, height=8)
        self.dl_bar.set(0)
        self.dl_bar.grid(row=0, column=0, sticky="ew")
        self.dl_status = ctk.CTkLabel(prog, text="", anchor="w", text_color=GREY)
        self.dl_status.grid(row=1, column=0, sticky="w")

        self.dl_log = ctk.CTkTextbox(tab, corner_radius=10, state="disabled", fg_color=("gray95", "gray14"))
        self.dl_log.grid(row=5, column=0, sticky="nsew", padx=4)

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.grid(row=6, column=0, sticky="ew", padx=4, pady=(10, 4))
        bottom.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bottom, text="Only download videos you have the right to save.", text_color=GREY,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.update_btn = ctk.CTkButton(bottom, text="Update downloader", width=140, fg_color="transparent", border_width=1,
                                        text_color=("gray10", "gray90"), command=self.update_downloader)
        self.update_btn.grid(row=0, column=1, padx=4)
        ctk.CTkButton(bottom, text="Open folder", width=110, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=lambda: reveal(self.dl_dir)).grid(row=0, column=2, padx=4)
        self.dl_cancel_btn = ctk.CTkButton(bottom, text="Cancel", width=90, fg_color=("gray75", "gray30"), hover_color=("#a40e26", "#da3633"),
                                        text_color_disabled=("gray45", "gray60"),
                                           command=self.cancel, state="disabled")
        self.dl_cancel_btn.grid(row=0, column=3, padx=4)
        self.dl_btn = ctk.CTkButton(bottom, text="Download", width=140, height=36, font=ctk.CTkFont(size=14, weight="bold"),
                                    command=self.start_download)
        self.dl_btn.grid(row=0, column=4, padx=(4, 0))

    # ------------------------------------------------------------------ file list

    def choose_files(self) -> None:
        if self.busy:
            return
        pattern = lambda exts: " ".join(f"*.{e}" for e in sorted(exts))  # noqa: E731
        files = filedialog.askopenfilenames(
            parent=self,
            title="Choose files to convert",
            filetypes=[
                ("All supported files", pattern(IMAGE_IN | VIDEO_IN | AUDIO_IN)),
                ("Images and GIFs", pattern(IMAGE_IN)),
                ("Video", pattern(VIDEO_IN)),
                ("Audio", pattern(AUDIO_IN)),
                ("All files", "*.*"),
            ],
        )
        self.add_paths([Path(f) for f in files])

    def on_drop(self, event) -> None:
        if not self.busy:
            self.tabs.set("Convert files")
            self.add_paths([Path(p) for p in self.tk.splitlist(event.data)])

    def add_paths(self, paths: list[Path]) -> None:
        supported = IMAGE_IN | VIDEO_IN | AUDIO_IN
        files: list[Path] = []
        for p in paths:
            if p.is_dir():
                files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower().lstrip(".") in supported)
            elif p.is_file():
                files.append(p)
        known = {r.path.resolve() for r in self.rows}
        for f in files:
            if f.resolve() in known:
                continue
            known.add(f.resolve())
            self.rows.append(FileRow(self, self.file_list, f))
        self.layout_rows()

    def remove_row(self, row: FileRow) -> None:
        if self.busy:
            return
        row.frame.destroy()
        self.rows.remove(row)
        self.layout_rows()

    def clear_rows(self) -> None:
        if self.busy:
            return
        for r in self.rows:
            r.frame.destroy()
        self.rows.clear()
        self.total_bar.set(0)
        self.layout_rows()

    def layout_rows(self) -> None:
        if self.rows:
            self.empty_label.grid_remove()
        else:
            self.empty_label.grid()
        for i, r in enumerate(self.rows):
            r.frame.grid(row=i + 1, column=0, sticky="ew", padx=4, pady=3)
        self.refresh_targets()
        usable = sum(1 for r in self.rows if r.kind)
        if not self.busy:
            self.convert_status.configure(
                text=f"{usable} file{'s' if usable != 1 else ''} ready." if usable else "Add files to begin.",
                text_color=GREY,
            )

    def refresh_targets(self) -> None:
        option_sets = [targets_for(r.path, r.kind) for r in self.rows if r.kind]
        current = self.target_lookup.get(self.target_menu.get())
        if not option_sets:
            self.target_lookup = {}
            self.target_menu.configure(values=["Add files first"])
            self.target_menu.set("Add files first")
            return
        common = [t for t in option_sets[0] if all(t in s for s in option_sets[1:])]
        if not common:  # mixed files (e.g. photos and songs): offer everything; files that can't do it are skipped
            common = []
            for s in option_sets:
                common += [t for t in s if t not in common]
        self.target_lookup = {f"{t.upper()}  –  {DESCRIPTIONS.get(t, '')}": t for t in common}
        labels = list(self.target_lookup)
        self.target_menu.configure(values=labels)
        preferred = current or self.settings.get("target")
        choice = next((lbl for lbl, t in self.target_lookup.items() if t == preferred), labels[0])
        self.target_menu.set(choice)

    # ------------------------------------------------------------------ options

    def set_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)

    def on_out_mode(self, value: str) -> None:
        if value == OTHER_FOLDER and not self.settings.get("out_dir"):
            self.pick_out_dir()
        self.update_out_label()

    def pick_out_dir(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Save converted files to", initialdir=str(self.out_dir))
        if folder:
            self.out_dir = Path(folder)
            self.settings["out_dir"] = folder
            self.out_menu.set(OTHER_FOLDER)
        self.update_out_label()

    def update_out_label(self) -> None:
        if self.out_menu.get() == OTHER_FOLDER:
            self.out_label.configure(text=shorten(str(self.out_dir), 34) + "  (change)")
        else:
            self.out_label.configure(text="")

    def pick_dl_dir(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Save downloads to", initialdir=str(self.dl_dir))
        if folder:
            self.dl_dir = Path(folder)
        self.update_dl_label()

    def update_dl_label(self) -> None:
        self.dl_dir_btn.configure(text=shorten(str(self.dl_dir), 48))

    def open_convert_folder(self) -> None:
        done = [r.output for r in self.rows if r.output]
        if self.out_menu.get() == OTHER_FOLDER:
            reveal(self.out_dir)
        elif done:
            reveal(done[-1])
        elif self.rows:
            reveal(self.rows[0].path.parent)

    def current_options(self) -> Options:
        quality = next(k for k, v in QUALITY_LABELS.items() if v == self.quality_menu.get())
        codec = next(k for k, v in VIDEO_CODECS.items() if v == self.codec_menu.get())
        return Options(
            quality=quality,
            video_codec=codec,
            keep_streams=bool(self.keep_streams.get()),
            out_dir=self.out_dir if self.out_menu.get() == OTHER_FOLDER else None,
        )

    # ------------------------------------------------------------------ background jobs

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.cancel_event.clear()
        normal, disabled = ("disabled", "normal") if busy else ("normal", "disabled")
        self.convert_btn.configure(state=normal)
        self.dl_btn.configure(state=normal)
        self.update_btn.configure(state=normal)
        cancel_colour = RED if busy else ("gray75", "gray30")
        self.cancel_btn.configure(state=disabled, fg_color=cancel_colour)
        self.dl_cancel_btn.configure(state=disabled, fg_color=cancel_colour)
        for r in self.rows:
            r.remove.configure(state=normal)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.convert_status.configure(text="Cancelling…")
        self.dl_status.configure(text="Cancelling…")

    def start_convert(self) -> None:
        if self.busy:
            return
        target = self.target_lookup.get(self.target_menu.get())
        if not target:
            self.choose_files()
            return
        rows = [r for r in self.rows if r.kind and r.state != "done"] or [r for r in self.rows if r.kind]
        if not rows:
            return
        self.set_busy(True)
        self.total_bar.set(0)
        for r in rows:
            r.set_state("queued")
        threading.Thread(target=self._convert_worker, args=(rows, target, self.current_options()), daemon=True).start()

    def _convert_worker(self, rows: list[FileRow], target: str, opts: Options) -> None:
        done = failed = skipped = 0
        total = len(rows)
        for i, row in enumerate(rows):
            if self.cancel_event.is_set():
                self.events.put(("row", row, "cancelled", "", None))
                continue
            if target not in targets_for(row.path, row.kind):
                skipped += 1
                self.events.put(("row", row, "skipped", f"Skipped: {KIND_LABELS[row.kind].lower()} files can't become {target.upper()}", None))
                continue

            def progress(fraction: float, text: str, row=row, i=i) -> None:
                self.events.put(("row", row, "working", f"{text}  {fraction * 100:.0f}%", fraction))
                self.events.put(("total", (i + fraction) / total, f"Converting {i + 1} of {total}: {row.path.name}"))

            try:
                row.output = convert(row.path, target, opts, progress, self.cancel_event)
                done += 1
                self.events.put(("row", row, "done", "", None))
            except Cancelled:
                self.events.put(("row", row, "cancelled", "", None))
            except ConversionError as exc:
                failed += 1
                row.error = str(exc)
                self.events.put(("row", row, "failed", "", None))
            except Exception as exc:  # never let one bad file kill the batch
                failed += 1
                row.error = f"{type(exc).__name__}: {exc}"
                self.events.put(("row", row, "failed", "", None))
        parts = [f"{done} converted"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        if self.cancel_event.is_set():
            parts.append("cancelled")
        self.events.put(("convert_finished", ", ".join(parts) + ".", failed == 0 and not self.cancel_event.is_set()))

    def paste_links(self) -> None:
        try:
            text = self.clipboard_get()
        except Exception:
            return
        existing = self.url_box.get("1.0", "end").strip()
        self.url_box.insert("end", ("\n" if existing else "") + text.strip())

    def log(self, text: str) -> None:
        self.dl_log.configure(state="normal")
        self.dl_log.insert("end", text + "\n")
        self.dl_log.see("end")
        self.dl_log.configure(state="disabled")

    def start_download(self) -> None:
        if self.busy:
            return
        urls = [u.strip() for u in self.url_box.get("1.0", "end").splitlines() if u.strip()]
        urls = [u if u.startswith(("http://", "https://")) else "https://" + u for u in urls]
        if not urls:
            self.dl_status.configure(text="Paste a link first.", text_color=RED)
            return
        mode = next(k for k, v in MODES.items() if v == self.mode_menu.get())
        self.set_busy(True)
        self.dl_bar.set(0)
        threading.Thread(target=self._download_worker, args=(urls, mode, bool(self.playlist.get())), daemon=True).start()

    def _download_worker(self, urls: list[str], mode: str, playlist: bool) -> None:
        ok = failed = 0
        for i, url in enumerate(urls):
            if self.cancel_event.is_set():
                break
            self.events.put(("log", f"↓ {url}"))

            def progress(fraction: float, text: str, i=i) -> None:
                self.events.put(("dl", fraction, f"Link {i + 1} of {len(urls)}: {text}  {fraction * 100:.0f}%"))

            try:
                files = download(url, self.dl_dir, mode, playlist, progress, self.cancel_event)
                ok += 1
                for f in files:
                    self.events.put(("log", f"   ✓ saved {f.name}"))
            except Cancelled:
                self.events.put(("log", "   cancelled"))
                break
            except ConversionError as exc:
                failed += 1
                self.events.put(("log", f"   ✗ {exc}"))
            except Exception as exc:
                failed += 1
                self.events.put(("log", f"   ✗ {type(exc).__name__}: {exc}"))
        summary = f"{ok} downloaded" + (f", {failed} failed" if failed else "") + ("" if not self.cancel_event.is_set() else ", cancelled")
        self.events.put(("dl_finished", summary + ".", failed == 0))

    def update_downloader(self) -> None:
        if self.busy:
            return
        self.set_busy(True)
        self.log("Updating yt-dlp (the part that reads video sites)…")

        def work() -> None:
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe" and (python.parent / "python.exe").exists():
                python = python.parent / "python.exe"
            result = subprocess.run(
                [str(python), "-m", "pip", "install", "-U", "--pre", "yt-dlp[default,curl-cffi]"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                self.events.put(("log", "   ✓ Updated. Restart the app to use the new version."))
            else:
                self.events.put(("log", "   ✗ Update failed: " + (result.stderr.strip().splitlines() or ["unknown error"])[-1]))
            self.events.put(("dl_finished", "", True))

        threading.Thread(target=work, daemon=True).start()

    def poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "row":
                    _, row, state, text, fraction = event
                    if row.frame.winfo_exists():
                        row.set_state(state, text, fraction)
                elif kind == "total":
                    self.total_bar.set(event[1])
                    self.convert_status.configure(text=shorten(event[2], 70), text_color=BLUE)
                elif kind == "convert_finished":
                    self.set_busy(False)
                    self.total_bar.set(1 if event[2] else self.total_bar.get())
                    self.convert_status.configure(text=event[1], text_color=GREEN if event[2] else RED)
                elif kind == "dl":
                    self.dl_bar.set(event[1])
                    self.dl_status.configure(text=event[2], text_color=BLUE)
                elif kind == "log":
                    self.log(event[1])
                elif kind == "dl_finished":
                    self.set_busy(False)
                    if event[1]:
                        self.dl_bar.set(1 if event[2] else self.dl_bar.get())
                        self.dl_status.configure(text=event[1], text_color=GREEN if event[2] else RED)
        except queue.Empty:
            pass
        self.after(80, self.poll_events)

    def on_close(self) -> None:
        if self.busy and not messagebox.askyesno("Still working", "A job is still running. Stop it and quit?", parent=self):
            return
        self.cancel_event.set()
        self.settings.update(
            tab=self.tabs.get(),
            appearance=self.appearance.get(),
            target=self.target_lookup.get(self.target_menu.get(), self.settings.get("target")),
            quality=self.current_options().quality,
            codec=self.current_options().video_codec,
            keep_streams=bool(self.keep_streams.get()),
            out_mode=self.out_menu.get(),
            dl_mode=next(k for k, v in MODES.items() if v == self.mode_menu.get()),
            dl_dir=str(self.dl_dir),
            playlist=bool(self.playlist.get()),
        )
        save_settings(self.settings)
        self.destroy()


def main() -> None:
    ctk.set_default_color_theme("blue")
    if sys.platform == "win32":
        try:  # sharp text on high-DPI screens and our own taskbar icon
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MediaConverter.App")
        except Exception:
            pass
    app = App(sys.argv[1:])
    app.title(f"Media Converter {__version__}")
    app.mainloop()


if __name__ == "__main__":
    main()
