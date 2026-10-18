"""The "Download from Link" screen."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog
from urllib.parse import urlparse

import customtkinter as ctk

from converter.downloader import download
from converter.ffmpeg_tools import Cancelled, ConversionError

from . import theme as T
from .convert_view import help_text, section_title, switch
from .widgets import (
    Segmented, SmoothProgress, Tooltip, fit_text, ghost_button, human_size, primary_button,
    secondary_button, set_primary_enabled,
)

SITE_NAMES = {
    "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "twitter": "X / Twitter",
    "reddit": "Reddit", "facebook": "Facebook", "vimeo": "Vimeo", "twitch": "Twitch", "soundcloud": "SoundCloud",
    "dailymotion": "Dailymotion", "bilibili": "Bilibili", "pinterest": "Pinterest", "streamable": "Streamable",
    "imgur": "Imgur", "tumblr": "Tumblr", "bandcamp": "Bandcamp", "kick": "Kick", "rumble": "Rumble",
    "linkedin": "LinkedIn", "snapchat": "Snapchat", "threads": "Threads", "bluesky": "Bluesky",
}
MEDIA_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".mp3", ".m4a", ".ogg", ".wav", ".flac", ".m3u8", ".gif")
MODE_TEXT = {
    "best": "The highest resolution and quality the site offers, saved as MP4. On YouTube this is often AV1 or VP9 "
            "video, which Windows 11 plays.",
    "compatible": "MP4 with H.264 video and AAC audio, which plays on every phone, TV and editor. "
                  "Sometimes a little lower resolution than Best.",
    "mp3": "Audio only, as MP3 at 320 kbps.",
    "m4a": "Audio only, kept in the site's original AAC quality where possible.",
}


def friendly_error(message: str) -> str:
    m = message.lower()
    if "unsupported url" in m:
        return "This site or page isn't supported."
    if "private" in m:
        return "This video is private."
    if "sign in" in m or "login" in m or "log in" in m or "age" in m and "restrict" in m or "members" in m:
        return "This video needs a signed-in account (private, age-restricted or members-only). That isn't supported."
    if "404" in m or "not found" in m or "does not exist" in m or "unavailable" in m or "removed" in m:
        return "The video couldn't be found. It may have been removed or the link may be wrong."
    if "429" in m or "too many requests" in m or "rate" in m and "limit" in m:
        return "The site is limiting downloads right now. Try again in a few minutes."
    if "getaddrinfo" in m or "unable to download webpage" in m or "timed out" in m or "connection" in m:
        return "Couldn't reach the site. Check your internet connection."
    if "drm" in m:
        return "This video is copy-protected (DRM) and can't be downloaded."
    first = message.strip().splitlines()[0] if message.strip() else "Unknown error"
    return f"{first[:180]} If this keeps happening, try Settings → Update downloader."


class DownloadView(ctk.CTkFrame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=T.BG)
        self.app = app
        self.s = app.settings
        self.busy = False
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.extractors = None
        self.url_ok = False
        self.dl_dir = Path(self.s.get("dl_dir") or (Path.home() / "Downloads"))
        self._job = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=340)
        self.grid_rowconfigure(0, weight=1)
        self._build_main()
        self._build_history()
        threading.Thread(target=self._load_extractors, daemon=True).start()
        self.after(80, self._drain)
        self._validate()

    # ------------------------------------------------------------------ layout

    def _build_main(self) -> None:
        card = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=T.RADIUS_LARGE, border_width=1, border_color=T.BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=24, pady=22)
        inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(inner, text="Download a video from a link", font=T.font(18, "semibold"), text_color=T.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        help_text(inner, "Works with YouTube, TikTok, Instagram, X, Reddit, Vimeo, SoundCloud and many other sites. "
                         "The link is checked as you type.").grid(row=1, column=0, sticky="w", pady=(2, 18))
        inner.grid_rowconfigure(1, minsize=0)
        for w in inner.grid_slaves(row=1):
            w.configure(wraplength=560)

        section_title(inner, "Video link").grid(row=2, column=0, sticky="w", pady=(0, 6))
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            row, height=40, corner_radius=T.RADIUS_SMALL, border_width=1, border_color=T.BORDER_STRONG,
            fg_color=T.SURFACE_ALT, text_color=T.TEXT, placeholder_text="https://www.youtube.com/watch?v=…",
            placeholder_text_color=T.FAINT, font=T.font(13),
        )
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<KeyRelease>", lambda _e: self._schedule_validate())
        self.entry.bind("<<Paste>>", lambda _e: self.after(10, self._validate))
        self.entry.bind("<Return>", lambda _e: self.start())
        self.entry.bind("<FocusIn>", lambda _e: self.entry.configure(border_color=T.ACCENT))
        self.entry.bind("<FocusOut>", lambda _e: self.entry.configure(border_color=T.BORDER_STRONG))
        self.paste_btn = secondary_button(row, "Paste", self.paste, icon_name="paste", height=40, width=96)
        self.paste_btn.grid(row=0, column=1, padx=(8, 0))

        self.check_row = ctk.CTkFrame(inner, fg_color="transparent", height=24)
        self.check_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.check_icon = ctk.CTkLabel(self.check_row, text="", width=18)
        self.check_icon.grid(row=0, column=0, padx=(0, 6))
        self.check_text = ctk.CTkLabel(self.check_row, text="", font=T.font(12), anchor="w", justify="left", wraplength=540)
        self.check_text.grid(row=0, column=1, sticky="w")

        self.playlist = switch(inner, "Download the whole playlist", self.s.get("playlist", False),
                               lambda: self.s.__setitem__("playlist", bool(self.playlist.get())))
        self.playlist_row = 5

        section_title(inner, "Save as").grid(row=6, column=0, sticky="w", pady=(18, 6))
        self.mode = Segmented(
            inner,
            [("best", "Best video", "Highest quality MP4", "video"),
             ("compatible", "Compatible video", "H.264 MP4 that plays everywhere", "video"),
             ("mp3", "MP3 audio", "Audio only, 320 kbps", "audio"),
             ("m4a", "M4A audio", "Audio only, original quality", "audio")],
            command=self._on_mode, value=self.s.get("dl_mode", "best"), height=36,
        )
        self.mode.grid(row=7, column=0, sticky="ew")
        self.mode_text = help_text(inner)
        self.mode_text.configure(wraplength=560)
        self.mode_text.grid(row=8, column=0, sticky="w", pady=(6, 0))

        section_title(inner, "Save to").grid(row=9, column=0, sticky="w", pady=(18, 6))
        dest = ctk.CTkFrame(inner, fg_color=T.SURFACE_ALT, corner_radius=T.RADIUS_SMALL)
        dest.grid(row=10, column=0, sticky="ew")
        dest.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(dest, text="", image=T.icon("folder", 16, T.MUTED), width=22).grid(row=0, column=0, padx=(12, 6), pady=8)
        self.dest_label = ctk.CTkLabel(dest, text="", font=T.font(12), text_color=T.TEXT, anchor="w")
        self.dest_label.grid(row=0, column=1, sticky="ew")
        self.dest_tip = Tooltip(self.dest_label, "")
        ghost_button(dest, "Open", lambda: self.app.reveal(self.dl_dir), colour=T.MUTED, height=28).grid(row=0, column=2)
        ghost_button(dest, "Change", self.pick_dir, colour=T.ACCENT_TEXT, height=28).grid(row=0, column=3, padx=(0, 6))
        self._update_dest()

        inner.grid_rowconfigure(11, weight=1)
        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.grid(row=12, column=0, sticky="ew", pady=(24, 0))
        actions.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(actions, text="", font=T.font(12), text_color=T.MUTED, anchor="w")
        self.status.grid(row=0, column=0, sticky="w")
        self.bar = SmoothProgress(actions, height=6)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.bar.grid_remove()
        self.cancel_btn = secondary_button(actions, "Cancel", self.cancel, icon_name="stop", height=40)
        self.cancel_btn.configure(text_color=T.ERROR, image=T.icon("stop", 15, T.ERROR))
        self.cancel_btn.grid(row=0, column=1, rowspan=2, padx=(12, 6))
        self.cancel_btn.grid_remove()
        self.dl_btn = primary_button(actions, "Download", self.start, icon_name="download", height=42, width=160)
        self.dl_btn.configure(font=T.font(14, "semibold"))
        self.dl_btn.grid(row=0, column=2, rowspan=2, padx=(6, 0))

        help_text(inner, "Only download videos you own or have permission to save, and follow each site's terms. "
                         "Private, paid and copy-protected videos are not supported.", colour=T.FAINT).grid(
            row=13, column=0, sticky="w", pady=(18, 0))
        self._on_mode(self.mode.get(), save=False)

    def _build_history(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=T.RADIUS_LARGE, border_width=1, border_color=T.BORDER, width=340)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 6))
        section_title(head, "Downloaded").grid(row=0, column=0, sticky="w")
        self.hist_count = ctk.CTkLabel(head, text="", font=T.font(12), text_color=T.MUTED)
        self.hist_count.grid(row=0, column=1, padx=8)
        self.history = ctk.CTkScrollableFrame(panel, fg_color="transparent", scrollbar_button_color=T.BORDER,
                                              scrollbar_button_hover_color=T.BORDER_STRONG)
        self.history.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 10))
        self.history.grid_columnconfigure(0, weight=1)
        self.hist_empty = ctk.CTkFrame(self.history, fg_color="transparent")
        self.hist_empty.grid(row=0, column=0, pady=40)
        ctk.CTkLabel(self.hist_empty, text="", image=T.icon("download", 30, T.FAINT)).grid(row=0, column=0)
        help_text(self.hist_empty, "Downloads from this session appear here.\nSend any of them straight to the converter.",
                  colour=T.MUTED).grid(row=1, column=0, pady=(8, 0))
        self.hist_empty.winfo_children()[1].configure(justify="center")
        self.hist_rows = 0
        self.hist_items: list[ctk.CTkFrame] = []

    # ------------------------------------------------------------------ link checking

    def _load_extractors(self) -> None:
        try:
            from yt_dlp.extractor import gen_extractor_classes

            classes = [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]
            for ie in classes:  # compile every pattern now, so checking a link later is instant
                try:
                    ie.suitable("https://example.invalid/")
                except Exception:
                    pass
            self.extractors = classes
        except Exception:
            self.extractors = []
        self.events.put(("extractors_ready",))

    def _site_for(self, url: str):
        for ie in self.extractors or []:
            try:
                if ie.suitable(url):
                    return ie
            except Exception:
                continue
        return None

    def _schedule_validate(self) -> None:
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self._validate)

    def _clean_url(self) -> str:
        text = self.entry.get().strip()
        if text and "://" not in text and "." in text.split("/")[0]:
            text = "https://" + text
        return text

    def _show_check(self, tone: str | None, text: str) -> None:
        colours = {"ok": (T.SUCCESS, "check_circle"), "warn": (T.WARNING, "warning"), "error": (T.ERROR, "error_circle"),
                   "info": (T.MUTED, "info")}
        if not tone:
            self.check_icon.configure(image=T.blank(15))
            self.check_text.configure(text="")
            return
        colour, icon_name = colours[tone]
        self.check_icon.configure(image=T.icon(icon_name, 15, colour))
        self.check_text.configure(text=text, text_color=colour)

    def _validate(self) -> None:
        self._job = None
        url = self._clean_url()
        self.url_ok = False
        show_playlist = False
        if not url:
            self._show_check(None, "")
        else:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or "." not in parsed.netloc or " " in url:
                self._show_check("error", "That doesn't look like a web address. Links start with https://")
            elif self.extractors is None:
                self.url_ok = True
                self._show_check("info", "Checking the link…")
            else:
                ie = self._site_for(url)
                self.url_ok = True
                if ie is not None:
                    key = ie.IE_NAME.split(":")[0].lower()
                    name = SITE_NAMES.get(key, ie.IE_NAME.split(":")[0].replace("_", " ").title())
                    if not ie.working():
                        self._show_check("warn", f"{name} link. This site's downloader is currently marked as broken, "
                                                 "so it may fail. Try Settings → Update downloader.")
                    else:
                        self._show_check("ok", f"{name} link recognised.")
                elif parsed.path.lower().endswith(MEDIA_EXTS):
                    self._show_check("ok", "Direct link to a media file.")
                else:
                    self._show_check("warn", "This site isn't on the supported list. You can try, but the download "
                                             "will probably not work.")
                q = parsed.query.lower()
                show_playlist = "list=" in q or "/playlist" in parsed.path.lower() or "/sets/" in parsed.path.lower()
        if show_playlist:
            self.playlist.grid(row=self.playlist_row, column=0, sticky="w", pady=(10, 0))
        else:
            self.playlist.grid_remove()
        self._update_button()

    def _update_button(self) -> None:
        set_primary_enabled(self.dl_btn, self.url_ok and not self.busy)
        if not self.busy:
            self.status.configure(text="" if self.url_ok else "Paste a link to begin.", text_color=T.MUTED)

    # ------------------------------------------------------------------ actions

    def paste(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except Exception:
            self.app.toast("The clipboard is empty.", "warning")
            return
        self.entry.delete(0, "end")
        self.entry.insert(0, text.splitlines()[0] if text else "")
        self._validate()

    def _on_mode(self, mode: str, save: bool = True) -> None:
        if save:
            self.s["dl_mode"] = mode
        self.mode_text.configure(text=MODE_TEXT[mode])

    def pick_dir(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Save downloads to", initialdir=str(self.dl_dir))
        if folder:
            self.dl_dir = Path(folder)
            self.s["dl_dir"] = folder
            self._update_dest()

    def _update_dest(self) -> None:
        text = str(self.dl_dir)
        self.dest_label.configure(text=fit_text(text, T.font(12), 380))
        self.dest_tip.set(text)

    def start(self) -> None:
        if self.busy or not self.url_ok:
            return
        url = self._clean_url()
        self.busy = True
        self.app.set_busy(True)
        self.cancel_event.clear()
        set_primary_enabled(self.dl_btn, False)
        self.cancel_btn.grid()
        self.cancel_btn.configure(state="normal", text="Cancel", image=T.icon("stop", 15, T.ERROR))
        self.bar.grid()
        self.bar.jump(0)
        self.status.configure(text="Connecting…", text_color=T.MUTED)
        mode = self.mode.get()
        playlist = bool(self.playlist.get()) and bool(self.playlist.winfo_ismapped())
        threading.Thread(target=self._worker, args=(url, mode, playlist), daemon=True).start()

    def cancel(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.cancel_btn.configure(state="disabled", text="Cancelling…", image=T.blank(15))

    def _worker(self, url: str, mode: str, playlist: bool) -> None:
        def progress(fraction: float, text: str) -> None:
            self.events.put(("progress", fraction, text))

        try:
            files = download(url, self.dl_dir, mode, playlist, progress, self.cancel_event)
            files = [f for f in files if f.exists()]
            if not files:
                raise ConversionError("The site returned nothing to save.")
            self.events.put(("done", files))
        except Cancelled:
            self.events.put(("cancelled",))
        except ConversionError as exc:
            self.events.put(("failed", friendly_error(str(exc))))
        except Exception as exc:
            self.events.put(("failed", friendly_error(f"{type(exc).__name__}: {exc}")))

    def _drain(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "extractors_ready":
                    self._validate()
                elif kind == "progress":
                    self.bar.glide(ev[1])
                    self.status.configure(text=f"{ev[2]}  ·  {ev[1] * 100:.0f}%", text_color=T.MUTED)
                elif kind in ("done", "failed", "cancelled"):
                    self.busy = False
                    self.app.set_busy(False)
                    self.cancel_btn.grid_remove()
                    if kind == "done":
                        self.bar.glide(1.0)
                        for f in ev[1]:
                            self._add_history(f)
                        n = len(ev[1])
                        self.status.configure(text=f"Saved {n} file{'s' if n != 1 else ''} to {self.dl_dir.name}.",
                                              text_color=T.SUCCESS)
                        self.entry.delete(0, "end")
                        self._show_check(None, "")
                        self.app.notify_done(n, 0)
                    elif kind == "failed":
                        self.bar.jump(0)
                        self.status.configure(text="Download failed.", text_color=T.ERROR)
                        self._show_check("error", ev[1])
                    else:
                        self.bar.jump(0)
                        self.status.configure(text="Download cancelled. Partial files were removed.", text_color=T.MUTED)
                    self.after(800, self.bar.grid_remove)
                    self.url_ok = bool(self._clean_url())
                    self._update_button() if kind != "done" else set_primary_enabled(self.dl_btn, False)
                    if kind != "done":
                        self._validate()
        except queue.Empty:
            pass
        self.after(80, self._drain)

    # ------------------------------------------------------------------ history

    def _add_history(self, path: Path) -> None:
        self.hist_empty.grid_remove()
        audio = path.suffix.lower() in (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac")
        row = ctk.CTkFrame(self.history, fg_color=T.SURFACE_ALT, corner_radius=T.RADIUS)
        row.grid_columnconfigure(1, weight=1)
        self.hist_items.insert(0, row)  # newest first
        for i, item in enumerate(self.hist_items):
            item.grid(row=i + 1, column=0, sticky="ew", pady=(0, 8), padx=(4, 6))
        ctk.CTkLabel(row, text="", width=34, height=34, corner_radius=T.RADIUS_SMALL, fg_color=T.SURFACE,
                     image=T.icon("audio" if audio else "video", 17, T.ACCENT_TEXT)).grid(row=0, column=0, rowspan=2, padx=(10, 10), pady=10)
        name = ctk.CTkLabel(row, text=fit_text(path.name, T.font(12, "semibold"), 160), font=T.font(12, "semibold"),
                            text_color=T.TEXT, anchor="w", height=18)
        name.grid(row=0, column=1, sticky="ew", pady=(10, 0))
        Tooltip(name, path.name)
        size = path.stat().st_size if path.exists() else 0
        ctk.CTkLabel(row, text=f"{path.suffix.lstrip('.').upper()}  ·  {human_size(size)}", font=T.font(11),
                     text_color=T.MUTED, anchor="w", height=16).grid(row=1, column=1, sticky="ew", pady=(0, 10))
        conv = ghost_button(row, icon_name="convert", command=lambda: self.app.send_to_convert(path), colour=T.ACCENT_TEXT)
        conv.grid(row=0, column=2, rowspan=2)
        Tooltip(conv, "Convert this file")
        folder = ghost_button(row, icon_name="folder_open", command=lambda: self.app.reveal(path))
        folder.grid(row=0, column=3, rowspan=2, padx=(0, 6))
        Tooltip(folder, "Show in folder")
        self.hist_rows += 1
        self.hist_count.configure(text=str(self.hist_rows))
