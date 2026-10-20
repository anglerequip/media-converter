"""The Spotify part of the Download screen: explain, search, show what was found, and download only what's confirmed.

1. An info box explains that Spotify's own audio can't be downloaded (it is copy-protected), and that the song
   details can be used to look for the same song on YouTube Music instead.
2. Each song is searched in the background. Every match appears as a card that slides in: video thumbnail,
   title, channel, length and a link to the video.
3. Nothing is downloaded until the user confirms: "Download" / "Not this one" for a single song, or tick the
   songs to keep and press "Download selected" for an album or playlist.
"""

from __future__ import annotations

import io
import queue
import threading
import webbrowser
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps

from converter import spotify
from converter.ffmpeg_tools import Cancelled, ConversionError

from . import motion as M
from . import theme as T
from .widgets import (Tooltip, fit_text, ghost_button, human_duration, icon_circle, make_focusable, primary_button,
                      secondary_button)

INFO_TITLE = "Spotify songs can't be downloaded directly"
INFO_TEXT = (
    "Spotify protects its music with copy protection, so downloading it from Spotify isn't allowed and isn't "
    "possible.\n\n"
    "Instead, Media Converter can use the song's details (title, artist and length) to look for the same song "
    "on YouTube Music. You'll see what it found, with a link to check it, and you decide whether to download it."
)
THUMB_W, THUMB_H = 120, 68


# ----------------------------------------------------------------------------- info box


class InfoDialog(ctk.CTkToplevel):
    """A small modal explanation with "Search YouTube Music" / "Cancel" and a "don't show again" option."""

    def __init__(self, parent, on_close: Callable[[bool, bool], None]) -> None:
        super().__init__(parent, fg_color=T.SURFACE)
        self.on_close = on_close
        self.title("About Spotify links")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(False))
        self.grid_columnconfigure(1, weight=1)

        icon_circle(self, "info", 44, 22).grid(row=0, column=0, padx=(22, 14), pady=(22, 0), sticky="n")
        ctk.CTkLabel(self, text=INFO_TITLE, font=T.font(15, "semibold"), text_color=T.TEXT, anchor="w",
                     justify="left").grid(row=0, column=1, sticky="w", padx=(0, 22), pady=(26, 0))
        ctk.CTkLabel(self, text=INFO_TEXT, font=T.font(12), text_color=T.MUTED, anchor="w", justify="left",
                     wraplength=380).grid(row=1, column=1, sticky="w", padx=(0, 22), pady=(8, 0))
        self.dont_show = ctk.CTkCheckBox(self, text="Don't show this again", font=T.font(11), text_color=T.MUTED,
                                         checkbox_width=18, checkbox_height=18, border_width=2, corner_radius=4,
                                         fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, border_color=T.BORDER_STRONG)
        self.dont_show.grid(row=2, column=1, sticky="w", pady=(16, 0))
        make_focusable(self.dont_show)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=22, pady=(18, 20))
        secondary_button(buttons, "Cancel", lambda: self._finish(False), height=36).grid(row=0, column=0, padx=(0, 8))
        go = primary_button(buttons, "Search YouTube Music", lambda: self._finish(True), icon_name="link", height=36)
        go.grid(row=0, column=1)

        self.bind("<Escape>", lambda _e: self._finish(False))
        self.bind("<Return>", lambda _e: self._finish(True))
        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        M.window_fade_in(self, 160)
        self.after(50, lambda: (self.grab_set(), go.focus_set()))

    def _finish(self, proceed: bool) -> None:
        remember = bool(self.dont_show.get())
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.on_close(proceed, remember)


# ----------------------------------------------------------------------------- result cards


def _thumb_image(data: bytes | None) -> ctk.CTkImage | None:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    scale = 3
    img = ImageOps.fit(img, (THUMB_W * scale, THUMB_H * scale), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=8 * scale, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return ctk.CTkImage(light_image=out, dark_image=out, size=(THUMB_W, THUMB_H))


class MatchCard(ctk.CTkFrame):
    """One found song: thumbnail, video title, channel and length, a link, and either a checkbox (albums and
    playlists) or Download / Not this one buttons (a single song)."""

    def __init__(self, parent, track: spotify.Track, video: dict, thumb: bytes | None, single: bool,
                 on_download: Callable[["MatchCard"], None], on_skip: Callable[["MatchCard"], None],
                 already_saved: bool) -> None:
        super().__init__(parent, fg_color=T.SURFACE_ALT, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER)
        self.track, self.video = track, video
        self.grid_columnconfigure(1, weight=1)

        image = _thumb_image(thumb)
        ctk.CTkLabel(self, text="", width=THUMB_W, height=THUMB_H, corner_radius=8, fg_color=T.SURFACE if not image else "transparent",
                     image=image or T.icon("video", 24, T.FAINT)).grid(row=0, column=0, rowspan=4, padx=(10, 12), pady=10)

        title = video.get("title") or video.get("track") or "Untitled"
        channel = ", ".join(video.get("artists") or []) or video.get("channel") or video.get("uploader") or ""
        length = human_duration(video.get("duration") or 0) if video.get("duration") else ""
        url = video.get("webpage_url") or f"https://music.youtube.com/watch?v={video.get('id', '')}"

        ctk.CTkLabel(self, text=fit_text(title, T.font(13, "semibold"), 330), font=T.font(13, "semibold"),
                     text_color=T.TEXT, anchor="w", height=18).grid(row=0, column=1, sticky="sw", pady=(10, 0))
        ctk.CTkLabel(self, text="  ·  ".join(p for p in (channel, length) if p), font=T.font(11), text_color=T.MUTED,
                     anchor="w", height=16).grid(row=1, column=1, sticky="w")
        link = ctk.CTkLabel(self, text=fit_text(url.replace("https://", ""), T.font(11), 330), font=T.font(11),
                            text_color=T.ACCENT_TEXT, anchor="w", cursor="hand2", height=16)
        link.grid(row=2, column=1, sticky="w")
        link.bind("<Button-1>", lambda _e: webbrowser.open(url))
        Tooltip(link, "Open the video in your browser to check it")
        spotify_line = f"For Spotify: {', '.join(track.artists[:2])} – {track.title} ({human_duration(track.duration)})"
        if already_saved:
            spotify_line += "  ·  already in the folder"
        ctk.CTkLabel(self, text=fit_text(spotify_line, T.font(11), 330), font=T.font(11), text_color=T.FAINT,
                     anchor="w", height=16).grid(row=3, column=1, sticky="nw", pady=(0, 10))

        self.selected = ctk.BooleanVar(value=not already_saved)
        if single:
            actions = ctk.CTkFrame(self, fg_color="transparent")
            actions.grid(row=0, column=2, rowspan=4, padx=(8, 12))
            primary_button(actions, "Download", lambda: on_download(self), icon_name="download", height=34,
                           width=118).grid(row=0, column=0, pady=(0, 6))
            secondary_button(actions, "Not this one", lambda: on_skip(self), height=32, width=118).grid(row=1, column=0)
        else:
            box = ctk.CTkCheckBox(self, text="", variable=self.selected, width=24, checkbox_width=20, checkbox_height=20,
                                  border_width=2, corner_radius=5, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                  border_color=T.BORDER_STRONG)
            box.grid(row=0, column=2, rowspan=4, padx=(8, 12))
            make_focusable(box)
            Tooltip(box, "Include this song")


class MissingRow(ctk.CTkFrame):
    def __init__(self, parent, track: spotify.Track, reason: str) -> None:
        super().__init__(parent, fg_color="transparent")
        ctk.CTkLabel(self, text="", image=T.icon("warning", 14, T.WARNING), width=18).grid(row=0, column=0, padx=(6, 6))
        text = f"Not found: {', '.join(track.artists[:2])} – {track.title}"
        lbl = ctk.CTkLabel(self, text=fit_text(text, T.font(11), 430), font=T.font(11), text_color=T.MUTED, anchor="w")
        lbl.grid(row=0, column=1, sticky="w")
        Tooltip(lbl, f"{text}\n{reason}\nNothing was guessed: only songs whose length, artist and title match are offered.")


# ----------------------------------------------------------------------------- the whole flow


class SpotifyFlow:
    """Runs the Spotify search and confirmation inside the Download screen's results area."""

    def __init__(self, view) -> None:
        self.view = view  # DownloadView
        self.events: queue.Queue = queue.Queue()
        self.cards: list[MatchCard] = []
        self.rows = 0
        self.collection: spotify.Collection | None = None
        self.folder: Path | None = None
        self.fmt = "m4a"
        self.searching = False
        self.cancel_event = threading.Event()
        self._build()
        view.after(80, self._drain)

    # -- layout: a panel that appears between the options and the Download button

    def _build(self) -> None:
        v = self.view
        self.panel = ctk.CTkFrame(v.inner, fg_color=T.SURFACE, corner_radius=0)
        self.panel.grid_columnconfigure(0, weight=1)
        self.panel.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self.panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="", image=T.icon("link", 16, T.ACCENT_TEXT), width=20).grid(row=0, column=0, padx=(0, 8))
        self.heading = ctk.CTkLabel(head, text="", font=T.font(13, "semibold"), text_color=T.TEXT, anchor="w")
        self.heading.grid(row=0, column=1, sticky="w")
        self.close_btn = ghost_button(head, icon_name="close", command=self.close, height=28)
        self.close_btn.grid(row=0, column=2)
        Tooltip(self.close_btn, "Close the results")
        self.list = ctk.CTkScrollableFrame(self.panel, fg_color="transparent", height=230,
                                           scrollbar_button_color=T.BORDER, scrollbar_button_hover_color=T.BORDER_STRONG)
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        self.footer = ctk.CTkFrame(self.panel, fg_color="transparent")
        self.footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.footer.grid_columnconfigure(0, weight=1)
        self.footer_text = ctk.CTkLabel(self.footer, text="", font=T.font(11), text_color=T.MUTED, anchor="w")
        self.footer_text.grid(row=0, column=0, sticky="w")
        self.select_all = ghost_button(self.footer, "Select all", self._toggle_all, colour=T.ACCENT_TEXT, height=30)
        self.select_all.grid(row=0, column=1, padx=4)
        self.download_btn = primary_button(self.footer, "Download selected", self._download_selected,
                                           icon_name="download", height=36)
        self.download_btn.grid(row=0, column=2)

    def visible(self) -> bool:
        return self.panel.winfo_ismapped()

    def _show_panel(self) -> None:
        if not self.visible():
            target = M.natural_height(self.panel)
            M.collapse_now(self.panel)
            self.panel.grid(row=11, column=0, sticky="nsew", pady=(16, 0))
            self.panel.update_idletasks()
            M.expand(self.panel, max(target, 120))
        self.view.compact(True)  # after the panel is placed, so the main Download button hides

    def close(self) -> None:
        if self.searching:
            self.cancel_event.set()
        if self.visible():
            M.collapse(self.panel, done=lambda: (self.panel.grid_remove(), self.view.compact(False)))
        self._clear()

    def _clear(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        self.cards, self.rows = [], 0

    # -- step 1: explain

    def begin(self, url: str, fmt: str) -> None:
        self.fmt = fmt
        if self.view.s.get("spotify_info_dismissed"):
            self._search(url)
            return

        def closed(proceed: bool, remember: bool) -> None:
            if remember:
                self.view.s["spotify_info_dismissed"] = True
            if proceed:
                self._search(url)
            else:
                self.view.set_status("Nothing was searched or downloaded.", T.MUTED)

        self.dialog = InfoDialog(self.view, closed)

    # -- step 2: search in the background and stream the results in

    def _search(self, url: str) -> None:
        self._clear()
        self.searching = True
        self.cancel_event.clear()
        self.heading.configure(text="Looking for the song on YouTube Music…")
        self.footer_text.configure(text="")
        self.download_btn.grid_remove()
        self.select_all.grid_remove()
        self._show_panel()
        self.view.job_started(cancel=self.cancel_event)
        threading.Thread(target=self._search_worker, args=(url,), daemon=True).start()

    def _search_worker(self, url: str) -> None:
        put = self.events.put
        try:
            collection = spotify.read(url)
            if not collection.tracks:
                raise ConversionError("This Spotify link has no songs in it.")
            folder = spotify.target_folder(collection, self.view.dl_dir)
            put(("start", collection, folder))
            total = len(collection.tracks)
            for i, track in enumerate(collection.tracks):
                if self.cancel_event.is_set():
                    raise Cancelled()
                put(("progress", i / total, f"Searching {i + 1} of {total}: {', '.join(track.artists[:2])} – {track.title}"))
                try:
                    video = spotify.find_match(track, self.cancel_event)
                except spotify.NoMatch as exc:
                    put(("missing", track, str(exc)))
                    continue
                exists = (folder / f"{spotify.file_name(track)}.{self.fmt}").exists()
                put(("found", track, video, spotify.thumbnail_bytes(video), exists))
            put(("searched",))
        except Cancelled:
            put(("cancelled",))
        except ConversionError as exc:
            put(("failed", str(exc)))
        except Exception as exc:  # never leave the screen stuck in "searching"
            put(("failed", f"{type(exc).__name__}: {exc}"))

    # -- step 3: confirm, then download only the chosen songs

    def _toggle_all(self) -> None:
        on = not all(c.selected.get() for c in self.cards)
        for c in self.cards:
            c.selected.set(on)
        self.select_all.configure(text="Select none" if on else "Select all")

    def _download_single(self, card: MatchCard) -> None:
        self._download([card])

    def _skip_single(self, card: MatchCard) -> None:
        self.view.set_status("Not downloaded. You can paste a different link, or search again.", T.MUTED)
        self.close()

    def _download_selected(self) -> None:
        chosen = [c for c in self.cards if c.selected.get()]
        if not chosen:
            self.view.app.toast("Tick at least one song to download.", "warning")
            return
        self._download(chosen)

    def _download(self, cards: list[MatchCard]) -> None:
        jobs = [(c.track, c.video) for c in cards]
        folder, fmt = self.folder, self.fmt
        self.close()
        self.cancel_event.clear()
        self.view.job_started(cancel=self.cancel_event)

        def work() -> None:
            put = self.events.put
            saved, failed = [], []
            for i, (track, video) in enumerate(jobs):
                if self.cancel_event.is_set():
                    put(("dl_cancelled", saved, failed))
                    return
                label = f"{', '.join(track.artists[:2])} – {track.title}"
                step = lambda f, t, i=i, label=label: put(("progress", (i + f) / len(jobs),  # noqa: E731
                                                           f"Song {i + 1} of {len(jobs)}: {label} · {t}"))
                try:
                    path = spotify.save_match(track, video, folder, fmt, step, self.cancel_event)
                    saved.append(path)
                    put(("saved", path))
                except Cancelled:
                    put(("dl_cancelled", saved, failed))
                    return
                except ConversionError as exc:
                    failed.append(f"{label}: {exc}")
                except OSError as exc:  # e.g. the download folder is on a drive that was unplugged
                    failed.append(f"{label}: Couldn't save to {folder}: {exc.strerror or exc}")
                except Exception as exc:  # never leave the screen stuck on "downloading"
                    failed.append(f"{label}: {type(exc).__name__}: {exc}")
            put(("dl_done", saved, failed))

        threading.Thread(target=work, daemon=True).start()

    # -- events from the worker threads

    def _drain(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "start":
                    self.collection, self.folder = ev[1], ev[2]
                    single = len(self.collection.tracks) == 1
                    # One card needs little room; a list scrolls, so keep it short enough for the buttons to show.
                    self.list.configure(height=104 if single else 168)
                    what = "song" if single else f"{self.collection.kind} “{self.collection.name}”"
                    self.heading.configure(text=f"Looking for the {what} on YouTube Music…")
                elif kind == "progress":
                    self.view.set_progress(ev[1], ev[2])
                elif kind == "found":
                    _, track, video, thumb, exists = ev
                    single = self.collection is not None and len(self.collection.tracks) == 1
                    card = MatchCard(self.list, track, video, thumb, single, self._download_single,
                                     self._skip_single, exists)
                    self._add_animated(card)
                    self.cards.append(card)
                elif kind == "missing":
                    self._add_animated(MissingRow(self.list, ev[1], ev[2]), flash=False)
                elif kind == "searched":
                    self._search_finished()
                elif kind == "cancelled":
                    self.searching = False
                    self.view.job_finished("Search cancelled. Nothing was downloaded.", T.MUTED)
                    if not self.cards:
                        self.close()
                elif kind == "failed":
                    self.searching = False
                    self.view.job_finished("Couldn't read the Spotify link.", T.ERROR, check=("error", ev[1]))
                    self.close()
                elif kind == "saved":
                    self.view.add_history(ev[1])
                elif kind in ("dl_done", "dl_cancelled"):
                    _, saved, failed = ev
                    n = len(saved)
                    text = f"Saved {n} song{'s' if n != 1 else ''} to {self.folder.name if self.folder else 'the folder'}."
                    if kind == "dl_cancelled":
                        text = f"Stopped. {text}"
                    if failed:
                        text += f" {len(failed)} couldn't be downloaded."
                    warning = None
                    if failed:
                        names = "; ".join(f.split(": ", 1)[0] for f in failed[:3])
                        reason = failed[0].split(": ", 1)[-1]  # e.g. the folder isn't available
                        warning = ("warn", f"Couldn't download: {names}. {reason}")
                    self.view.job_finished(text, T.SUCCESS if n and not failed else T.WARNING, check=warning)
                    self.view.app.notify_done(n, len(failed))
        except queue.Empty:
            pass
        self.view.after(80, self._drain)

    def _add_animated(self, widget, flash: bool = True) -> None:
        """Slide each result into the list as it arrives, with a short highlight."""
        widget.grid(row=self.rows, column=0, sticky="ew", pady=(0, 8), padx=(0, 6))
        self.rows += 1
        if M.enabled():
            height = M.natural_height(widget)
            M.collapse_now(widget)
            if flash:
                widget.configure(fg_color=T.ACCENT_SOFT, border_color=T.ACCENT)
            M.expand(widget, height, duration=M.SLOW)
            if flash:
                M.fade(widget, 900, key="flash", fg_color=T.SURFACE_ALT, border_color=T.BORDER)
        self.view.after(60, lambda: self.list._parent_canvas.yview_moveto(1.0))

    def _search_finished(self) -> None:
        self.searching = False
        found, total = len(self.cards), len(self.collection.tracks) if self.collection else 0
        missing = total - found
        single = total == 1
        if single and found:
            self.heading.configure(text="Found it. Download this song?")
            self.view.job_finished("Check the match, then choose Download or Not this one.", T.MUTED)
        elif single:
            self.heading.configure(text="This song wasn't found on YouTube Music")
            self.view.job_finished("Nothing was downloaded: no confident match.", T.WARNING)
        else:
            self.heading.configure(text=f"Found {found} of {total} songs. Download them?")
            extra = f" Spotify only shares the first {spotify.PLAYLIST_LIMIT} songs of a playlist." if (
                self.collection and self.collection.truncated) else ""
            self.footer_text.configure(text=(f"{missing} not found and left out. " if missing else "")
                                       + "Untick any you don't want." + extra)
            if found:
                self.select_all.grid()
                self.select_all.configure(text="Select none")
                self.download_btn.grid()
            self.view.job_finished(f"Found {found} of {total}. Choose which to download.", T.MUTED)
