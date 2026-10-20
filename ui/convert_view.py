"""The "Convert Files" screen: drop zone, file queue, settings panel and action bar."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from converter.engine import Options, convert, describe_settings
from converter.ffmpeg_tools import Cancelled, ConversionError
from converter.formats import (
    AUDIO_IN, GROUP_ORDER, IMAGE_IN, VIDEO_IN, group_of, kind_of, recommended_for, targets_for,
)

from . import motion as M
from . import theme as T
from .widgets import (
    DashedPanel, FileItem, Segmented, VirtualFileList, autohide_scrollbar, icon_circle, make_focusable, scaling,
    style_switch, thumb_tile, SmoothProgress, Tooltip, chip, fit_text, ghost_button, human_size,
    primary_button, secondary_button, set_primary_enabled, style_chip,
)

FORMAT_INFO = {
    "png": "Lossless image with transparency. Ideal for graphics and screenshots.",
    "jpg": "Small, universal photo format. Doesn't support transparency.",
    "webp": "Modern format with small files. Supports transparency and animation.",
    "avif": "Newest image format with excellent compression. Needs up-to-date apps.",
    "heic": "iPhone photo format. Small files, but limited support on Windows.",
    "tiff": "Lossless format used for printing and editing.",
    "bmp": "Uncompressed Windows bitmap. Large files.",
    "gif": "Plays everywhere, including chats and forums. Limited to 256 colours.",
    "ico": "Windows icon containing every size up to 256 px.",
    "tga": "Lossless format used by games and 3D tools.",
    "pdf": "A PDF document with the image on a page.",
    "apng": "Animated PNG: full colour and transparency, larger than GIF.",
    "mp4": "Video that plays on almost every phone, computer, TV and website.",
    "mkv": "Flexible video file that keeps every audio track.",
    "mov": "QuickTime video for iPhone, Mac and video editors.",
    "webm": "Open video format made for websites (VP9).",
    "avi": "Older video format for legacy players.",
    "mp3": "Audio that plays everywhere.",
    "m4a": "AAC audio: better than MP3 at the same size. Apple's default.",
    "wav": "Uncompressed audio. Exact quality, large files.",
    "flac": "Lossless audio at about half the size of WAV.",
    "ogg": "Open audio format (Vorbis).",
    "opus": "Modern, very efficient audio. Best quality for its size.",
    "aac": "Raw AAC audio stream.",
    "aiff": "Uncompressed audio, common on Mac.",
    "wma": "Windows Media Audio for older devices.",
}
STILL_GIF_INFO = "Simple image with up to 256 colours."
ORDER = ["png", "jpg", "webp", "avif", "heic", "tiff", "gif", "bmp", "ico", "tga", "pdf", "apng",
         "mp4", "webm", "mov", "mkv", "avi", "mp3", "m4a", "wav", "flac", "ogg", "opus", "aac", "aiff", "wma"]
LOSSLESS_ONLY = {"png", "tiff", "bmp", "tga", "ico", "apng", "flac", "wav", "aiff"}
CODEC_TARGETS = {"mp4", "mkv", "mov"}
MOVING_TARGETS = {"mp4", "webm", "mov", "mkv", "avi", "gif", "webp", "apng"}
QUALITY_TEXT = {
    "small": "Smallest files. Some quality loss is visible up close.",
    "balanced": "Good quality at a moderate size. Fine for sharing.",
    "high": "Very close to the original, with larger files.",
    "max": "Highest quality, with no visible loss. Files are the largest; Lossless mode (Advanced) goes further.",
}
CODEC_TEXT = {
    "h264": "H.264 plays on every phone, TV, browser and editor.",
    "h265": "H.265 (HEVC) makes files about 40% smaller. Needs a device from roughly 2016 or newer.",
    "av1": "AV1 makes the smallest files but encodes slowly. Needs a recent device.",
}
SIZES = [("Original size", None), ("3840 px wide (4K)", 3840), ("2560 px wide", 2560), ("1920 px wide (1080p)", 1920),
         ("1280 px wide (720p)", 1280), ("854 px wide (480p)", 854), ("640 px wide", 640), ("480 px wide", 480),
         ("320 px wide", 320)]
RATES = [("Original frame rate", None), ("60 fps", 60.0), ("30 fps", 30.0), ("24 fps", 24.0), ("15 fps", 15.0),
         ("10 fps", 10.0)]
SUPPORTED = IMAGE_IN | VIDEO_IN | AUDIO_IN


def option_menu(parent, values, command, value) -> ctk.CTkOptionMenu:
    m = ctk.CTkOptionMenu(
        parent, values=values, command=command, height=32, corner_radius=T.RADIUS_SMALL, dynamic_resizing=False,
        fg_color=T.SURFACE_ALT, button_color=T.SURFACE_ALT, button_hover_color=T.SURFACE_HOVER,
        text_color=T.TEXT, font=T.font(12), dropdown_font=T.font(12),
        dropdown_fg_color=T.SURFACE, dropdown_hover_color=T.ACCENT_SOFT, dropdown_text_color=T.TEXT,
    )
    m.set(value)
    make_focusable(m)
    return m


def switch(parent, text: str, value: bool, command) -> ctk.CTkSwitch:
    def changed() -> None:
        style_switch(s)
        command()

    # Sized to its label (CTkSwitch is 100 px wide otherwise), so the whole label is part of the switch.
    width = round(T.font(12, "semibold").measure(text)) + 38 + 24
    s = ctk.CTkSwitch(parent, text=text, command=changed, font=T.font(12, "semibold"), text_color=T.TEXT,
                      switch_width=38, switch_height=20, width=width)
    if value:
        s.select()
    style_switch(s)
    make_focusable(s)
    return s


def help_text(parent, text: str = "", colour=T.MUTED, indent: int = 0, wrap: int | None = None) -> ctk.CTkLabel:
    """A muted explanation that wraps to the width its container actually has (minus `indent`, the label's own left
    and right padding), so it never runs past the edge of the panel at any window size. A fixed `wrap` width is for
    labels whose container takes its size from them (a centred message), where following it would only shrink."""
    label = ctk.CTkLabel(parent, text=text, font=T.font(11), text_color=colour, justify="left", anchor="w",
                         wraplength=wrap or 260)
    label._mc_wrap = wrap or 260
    if wrap:
        return label

    def rewrap(_e=None) -> None:
        width = round(parent.winfo_width() / scaling(parent)) - indent - 4
        if width > 80 and abs(width - label._mc_wrap) > 2:
            label._mc_wrap = width
            label.configure(wraplength=width)

    parent.bind("<Configure>", rewrap, add="+")
    return label


def section_title(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=T.font(13, "semibold"), text_color=T.TEXT, anchor="w")


class DropZone(DashedPanel):
    """Big call to action while the list is empty; a slim "add more" strip afterwards."""

    def __init__(self, parent, on_files, on_folder) -> None:
        super().__init__(parent, fill=T.SURFACE, line=T.BORDER_STRONG)
        self.compact = None
        self.hover = False
        b = self.body
        b.grid_columnconfigure(0, weight=1)

        # full layout
        self.full = ctk.CTkFrame(b, fg_color=T.SURFACE)
        self.full_icon = icon_circle(self.full, "upload", 64, 28)
        self.full_icon.grid(row=0, column=0, columnspan=2, pady=(0, 14))
        ctk.CTkLabel(self.full, text="Drop your files here", font=T.font(18, "semibold"), text_color=T.TEXT).grid(
            row=1, column=0, columnspan=2)
        ctk.CTkLabel(self.full, text="or browse files from your computer", font=T.font(12), text_color=T.MUTED).grid(
            row=2, column=0, columnspan=2, pady=(2, 16))
        self.choose_btn = primary_button(self.full, "Choose Files", on_files, icon_name="add", width=150)
        self.choose_btn.grid(row=3, column=0, padx=(0, 6), sticky="e")
        self.folder_btn = secondary_button(self.full, "Choose Folder", on_folder, icon_name="folder", height=36, width=150)
        self.folder_btn.grid(row=3, column=1, padx=(6, 0), sticky="w")
        ctk.CTkLabel(
            self.full,
            text="PNG, JPG, WebP, GIF, HEIC, AVIF, TIFF, MP4, MOV, MKV, WebM, MP3, WAV, FLAC and more",
            font=T.font(11), text_color=T.FAINT,
        ).grid(row=4, column=0, columnspan=2, pady=(18, 0))

        # compact layout
        self.slim = ctk.CTkFrame(b, fg_color=T.SURFACE)
        self.slim.grid_columnconfigure(1, weight=1)
        self.slim_icon = icon_circle(self.slim, "upload", 34, 17)
        self.slim_icon.grid(row=0, column=0, rowspan=2, padx=(8, 10))
        ctk.CTkLabel(self.slim, text="Add more files", font=T.font(13, "semibold"), text_color=T.TEXT, anchor="w",
                     height=18).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(self.slim, text="Drop them here, or use the buttons", font=T.font(11), text_color=T.MUTED,
                     anchor="w", height=16).grid(row=1, column=1, sticky="nw")
        self.slim_files = secondary_button(self.slim, "Add Files", on_files, icon_name="add", height=32)
        self.slim_files.grid(row=0, column=2, rowspan=2, padx=4)
        self.slim_folder = ghost_button(self.slim, "Add Folder", on_folder, icon_name="folder", height=32, colour=T.TEXT)
        self.slim_folder.grid(row=0, column=3, rowspan=2, padx=(0, 6))

        self.clickables = [self.full, self.full_icon, self.slim_icon]
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            self.full.place_forget()
            self.slim.place(relx=0, rely=0.5, relwidth=1, anchor="w")
            self.configure(height=70)
        else:
            self.slim.place_forget()
            self.full.place(relx=0.5, rely=0.5, anchor="center")

    def set_hover(self, hover: bool) -> None:
        if hover == self.hover:
            return
        self.hover = hover
        fill = T.ACCENT_SOFT if hover else T.SURFACE
        self.full.configure(fg_color=fill)
        self.slim.configure(fg_color=fill)
        M.fade(self.full_icon, M.FAST, fg_color=T.SURFACE if hover else T.ACCENT_SOFT)
        M.fade(self.slim_icon, M.FAST, fg_color=T.SURFACE if hover else T.ACCENT_SOFT)
        self.set_colours(fill, T.ACCENT if hover else T.BORDER_STRONG)


class ConvertView(ctk.CTkFrame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=T.BG)
        self.app = app
        self.s = app.settings
        self.cards: list[FileItem] = []  # the queue; VirtualFileList draws only the visible part
        self.target: str | None = None
        self.busy = False
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.last_output: Path | None = None
        self.chips: dict[str, list] = {}
        self._signature = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=340)
        self.grid_rowconfigure(0, weight=1)
        self._build_left()
        self._build_settings()
        self._build_action_bar()
        self.layout_cards()
        self.refresh()
        self.after(80, self._drain)

    # ================================================================== layout

    def _build_left(self) -> None:
        left = self.left = ctk.CTkFrame(self, fg_color=T.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_columnconfigure(0, weight=1)

        self.banner = ctk.CTkFrame(left, corner_radius=T.RADIUS, border_width=1)
        self.banner.grid_columnconfigure(1, weight=1)
        self.banner_icon = ctk.CTkLabel(self.banner, text="", width=36, height=36)
        self.banner_icon.grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=12)
        self.banner_title = ctk.CTkLabel(self.banner, text="", font=T.font(14, "semibold"), anchor="w", height=20)
        self.banner_title.grid(row=0, column=1, sticky="sw", pady=(12, 0))
        self.banner_detail = ctk.CTkLabel(self.banner, text="", font=T.font(12), text_color=T.MUTED, anchor="w", height=18,
                                          justify="left")
        self.banner_detail.grid(row=1, column=1, sticky="nw", pady=(0, 12))
        self.banner_open = secondary_button(self.banner, "Open Output Folder", self.open_output, icon_name="folder_open")
        self.banner_open.grid(row=0, column=2, rowspan=2, padx=4)
        self.banner_more = secondary_button(self.banner, "Convert More Files", self.convert_more, icon_name="add")
        self.banner_more.grid(row=0, column=3, rowspan=2, padx=(4, 6))
        self.banner_close = ghost_button(self.banner, icon_name="close", command=self.hide_banner)
        self.banner_close.grid(row=0, column=4, rowspan=2, padx=(0, 8))
        self._banner_title_text = ""
        self.banner.bind("<Configure>", self._fit_banner_text, add="+")

        self.drop = DropZone(left, self.choose_files, self.choose_folder)
        self.drop.grid(row=1, column=0, sticky="nsew")

        self.queue_head = ctk.CTkFrame(left, fg_color=T.BG, height=30)
        self.queue_head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.queue_head, text="Files", font=T.font(14, "semibold"), text_color=T.TEXT).grid(row=0, column=0, padx=(2, 8))
        self.queue_count = ctk.CTkLabel(self.queue_head, text="", font=T.font(12), text_color=T.MUTED)
        self.queue_count.grid(row=0, column=1, sticky="w")

        self.list = VirtualFileList(left, self.remove, self.app.reveal, self.show_error, self._request_details)

    def _build_settings(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=T.RADIUS_LARGE, border_width=1, border_color=T.BORDER,
                             width=340)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        sp = self.settings_panel = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", corner_radius=0, scrollbar_button_color=T.BORDER,
            scrollbar_button_hover_color=T.BORDER_STRONG)
        sp.grid(row=0, column=0, sticky="nsew", padx=(6, 2), pady=6)
        sp.grid_columnconfigure(0, weight=1)
        autohide_scrollbar(sp)
        px = (10, 8)

        # -- output format
        self.sec_format = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_format.grid(row=0, column=0, sticky="ew", padx=px, pady=(8, 0))
        self.sec_format.grid_columnconfigure(0, weight=1)
        section_title(self.sec_format, "Convert to").grid(row=0, column=0, sticky="w")
        self.detected = help_text(self.sec_format, "Add files and the best formats will be suggested.")
        self.detected.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.chip_area = ctk.CTkFrame(self.sec_format, fg_color="transparent", height=1)
        self.chip_area.grid(row=2, column=0, sticky="ew")
        self.format_card = ctk.CTkFrame(self.sec_format, fg_color=T.SURFACE_ALT, corner_radius=T.RADIUS_SMALL)
        self.format_card.grid_columnconfigure(0, weight=1)
        self.format_name = ctk.CTkLabel(self.format_card, text="", font=T.font(12, "semibold"), text_color=T.TEXT, anchor="w")
        self.format_name.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
        self.format_desc = help_text(self.format_card, indent=20)
        self.format_desc.grid(row=1, column=0, sticky="w", padx=10)
        self.format_cover = help_text(self.format_card, indent=20)
        self.format_cover.grid(row=2, column=0, sticky="w", padx=10, pady=(2, 8))

        # -- quality
        self.sec_quality = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_quality.grid_columnconfigure(0, weight=1)
        section_title(self.sec_quality, "Quality").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.quality = Segmented(
            self.sec_quality,
            [("small", "Smaller", "Smallest files, some visible quality loss", None),
             ("balanced", "Balanced", "Good quality at a moderate size", None),
             ("high", "High", "Very close to the original", None),
             ("max", "Maximum", "Highest quality, largest files", None)],
            command=lambda v: self._set("quality", v), value=self.s.get("quality", "max"), font_size=11,
        )
        self.quality.grid(row=1, column=0, sticky="ew")
        self.quality_text = help_text(self.sec_quality)
        self.quality_text.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.quality_tech = help_text(self.sec_quality, colour=T.FAINT)
        self.quality_tech.grid(row=3, column=0, sticky="w")

        # -- video codec
        self.sec_codec = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_codec.grid_columnconfigure(0, weight=1)
        section_title(self.sec_codec, "Video compression").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.codec = Segmented(
            self.sec_codec,
            [("h264", "Compatible", "H.264 / AVC: plays on every device", None),
             ("h265", "Smaller", "H.265 / HEVC: about 40% smaller, newer devices", None),
             ("av1", "Smallest", "AV1: smallest files, slow to encode, newest devices", None)],
            command=lambda v: self._set("codec", v), value=self.s.get("codec", "h264"), font_size=11,
        )
        self.codec.grid(row=1, column=0, sticky="ew")
        self.codec_text = help_text(self.sec_codec)
        self.codec_text.grid(row=2, column=0, sticky="w", pady=(6, 0))

        # -- size and frame rate
        self.sec_size = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_size.grid_columnconfigure(0, weight=1)
        self.size_title = section_title(self.sec_size, "Size")
        self.size_title.grid(row=0, column=0, sticky="w", pady=(0, 6))
        size_label = next((lbl for lbl, v in SIZES if v == self.s.get("max_width")), SIZES[0][0])
        self.size_menu = option_menu(self.sec_size, [lbl for lbl, _ in SIZES], lambda v: self._set("max_width", dict(SIZES)[v]), size_label)
        self.size_menu.grid(row=1, column=0, sticky="ew")
        rate_label = next((lbl for lbl, v in RATES if v == self.s.get("fps")), RATES[0][0])
        self.rate_menu = option_menu(self.sec_size, [lbl for lbl, _ in RATES], lambda v: self._set("fps", dict(RATES)[v]), rate_label)
        self.rate_menu.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.size_text = help_text(self.sec_size, "Files are only ever made smaller, never enlarged.")
        self.size_text.grid(row=3, column=0, sticky="w", pady=(6, 0))

        # -- save to
        self.sec_save = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_save.grid_columnconfigure(0, weight=1)
        section_title(self.sec_save, "Save to").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.save = Segmented(
            self.sec_save,
            [("same", "Same folder", "Next to each original file", None),
             ("converted", "Converted", "In a “Converted” folder next to each original", None),
             ("custom", "Custom", "One folder you choose", None)],
            command=self._on_save_mode, value=self.s.get("save_mode", "same"), font_size=11,
        )
        self.save.grid(row=1, column=0, sticky="ew")
        self.save_row = ctk.CTkFrame(self.sec_save, fg_color=T.SURFACE_ALT, corner_radius=T.RADIUS_SMALL)
        self.save_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.save_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.save_row, text="", image=T.icon("folder", 15, T.MUTED), width=20).grid(row=0, column=0, padx=(10, 6), pady=7)
        self.save_path = ctk.CTkLabel(self.save_row, text="", font=T.font(11), text_color=T.MUTED, anchor="w", height=18)
        self.save_path.grid(row=0, column=1, sticky="ew")
        self.save_tip = Tooltip(self.save_path, "")
        self.save_change = ghost_button(self.save_row, "Change", self.pick_custom_dir, colour=T.ACCENT_TEXT, height=26)
        self.save_change.grid(row=0, column=2, padx=4)

        # -- advanced
        self.sec_adv = ctk.CTkFrame(sp, fg_color="transparent")
        self.sec_adv.grid_columnconfigure(0, weight=1)
        self.adv_toggle = ctk.CTkButton(
            self.sec_adv, text="Advanced settings", anchor="w", height=32, corner_radius=T.RADIUS_SMALL,
            fg_color="transparent", hover_color=T.SURFACE_HOVER, text_color=T.TEXT, font=T.font(13, "semibold"),
            compound="right", command=self.toggle_advanced, border_spacing=2,
        )
        self.adv_toggle.grid(row=0, column=0, sticky="ew")
        make_focusable(self.adv_toggle)
        self.adv_body = ctk.CTkFrame(self.sec_adv, fg_color="transparent")
        self.adv_body.grid_columnconfigure(0, weight=1)
        self.sw_copy = switch(self.adv_body, "Keep original streams", self.s.get("keep_streams", True),
                              lambda: self._set("keep_streams", bool(self.sw_copy.get())))
        self.sw_copy.grid(row=0, column=0, sticky="w", pady=(6, 0))
        help_text(self.adv_body, "Copies video and audio without re-encoding when the new format can hold them "
                                 "(for example MKV to MP4). Instant and lossless.", indent=46).grid(row=1, column=0, sticky="w", padx=(46, 0))
        self.sw_lossless = switch(self.adv_body, "Lossless mode", self.s.get("lossless", False),
                                  lambda: self._set("lossless", bool(self.sw_lossless.get())))
        self.sw_lossless.grid(row=2, column=0, sticky="w", pady=(12, 0))
        help_text(self.adv_body, "No quality loss at all where the format allows it. Files get very large. "
                                 "Overrides the quality setting.", indent=46).grid(row=3, column=0, sticky="w", padx=(46, 0))
        ctk.CTkLabel(self.adv_body, text="Fill for transparent areas", font=T.font(12, "semibold"), text_color=T.TEXT,
                     anchor="w").grid(row=4, column=0, sticky="w", pady=(12, 4))
        self.bg = Segmented(self.adv_body, [("white", "White", None, None), ("black", "Black", None, None)],
                            command=lambda v: self._set("background", v), value=self.s.get("background", "white"), font_size=11)
        self.bg.grid(row=5, column=0, sticky="ew")
        help_text(self.adv_body, "Used when the new format can't store transparency, such as JPG or MP4.").grid(
            row=6, column=0, sticky="w", pady=(4, 4))
        self._show_advanced(self.s.get("advanced_open", False))

    def _build_action_bar(self) -> None:
        bar = self.bar = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=T.RADIUS_LARGE, border_width=1, border_color=T.BORDER)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        bar.grid_columnconfigure(0, weight=1)
        self.status_title = ctk.CTkLabel(bar, text="", font=T.font(14, "semibold"), text_color=T.TEXT, anchor="w", height=20)
        self.status_title.grid(row=0, column=0, sticky="sw", padx=18, pady=(14, 0))
        self.status_detail = ctk.CTkLabel(bar, text="", font=T.font(12), text_color=T.MUTED, anchor="w", height=18)
        self.status_detail.grid(row=1, column=0, sticky="nw", padx=18, pady=(0, 14))
        self.total = SmoothProgress(bar, height=6)
        self.total.grid(row=2, column=0, columnspan=6, sticky="ew", padx=18, pady=(0, 14))
        self.total.grid_remove()

        self.clear_btn = ghost_button(bar, "Clear List", self.clear, icon_name="delete", height=36, colour=T.MUTED)
        self.clear_btn.grid(row=0, column=1, rowspan=2, padx=4)
        self.open_btn = secondary_button(bar, "Open Output Folder", self.open_output, icon_name="folder_open", height=38)
        self.open_btn.grid(row=0, column=2, rowspan=2, padx=4)
        self.cancel_btn = secondary_button(bar, "Cancel", self.cancel, icon_name="stop", height=38)
        self.cancel_btn.configure(text_color=T.ERROR, image=T.icon("stop", 15, T.ERROR))
        self.cancel_btn.grid(row=0, column=3, rowspan=2, padx=4)
        self.convert_btn = primary_button(bar, "Convert Files", self.start, icon_name="convert", height=42, width=176)
        self.convert_btn.configure(font=T.font(14, "semibold"))
        self.convert_btn.grid(row=0, column=4, rowspan=2, padx=(6, 14), pady=12)
        self.convert_tip = Tooltip(self.convert_btn, "")

    # ================================================================== settings helpers

    def _set(self, key: str, value) -> None:
        self.s[key] = value
        self.refresh()

    def _show_advanced(self, show: bool, animate: bool = False) -> None:
        self.s["advanced_open"] = show
        self.adv_toggle.configure(image=T.icon("chevron_up" if show else "chevron_down", 14, T.MUTED))
        if show:
            if animate and M.enabled():
                target = M.natural_height(self.adv_body)
                M.collapse_now(self.adv_body)
                self.adv_body.grid(row=1, column=0, sticky="ew", padx=(9, 0))
                M.expand(self.adv_body, target, done=lambda: self.settings_panel._parent_canvas.yview_moveto(1.0))
            else:
                self.adv_body.grid(row=1, column=0, sticky="ew", padx=(9, 0))
        elif animate and self.adv_body.winfo_ismapped():
            M.collapse(self.adv_body, done=self.adv_body.grid_remove)
        else:
            self.adv_body.grid_remove()

    def toggle_advanced(self) -> None:
        self._show_advanced(not self.s.get("advanced_open", False), animate=True)
        if self.s["advanced_open"] and not M.enabled():
            self.after(50, lambda: self.settings_panel._parent_canvas.yview_moveto(1.0))

    def _on_save_mode(self, mode: str) -> None:
        if mode == "custom" and not self.s.get("custom_dir"):
            if not self.pick_custom_dir():
                self.save.set(self.s.get("save_mode", "same"))
                return
        self._set("save_mode", mode)

    def pick_custom_dir(self) -> bool:
        folder = filedialog.askdirectory(parent=self, title="Save converted files to",
                                         initialdir=self.s.get("custom_dir") or str(Path.home()))
        if folder:
            self.s["custom_dir"] = folder
            self.s["save_mode"] = "custom"
            self.save.set("custom")
            self.refresh()
            return True
        return False

    def options(self) -> Options:
        mode = self.s.get("save_mode", "same")
        return Options(
            quality="lossless" if self.s.get("lossless") else self.s.get("quality", "max"),
            video_codec=self.s.get("codec", "h264"),
            keep_streams=self.s.get("keep_streams", True),
            out_dir=Path(self.s["custom_dir"]) if mode == "custom" and self.s.get("custom_dir") else None,
            subfolder="Converted" if mode == "converted" else None,
            background=(0, 0, 0) if self.s.get("background") == "black" else (255, 255, 255),
            max_width=self.s.get("max_width"),
            fps=self.s.get("fps"),
        )

    # ================================================================== files

    def choose_files(self) -> None:
        if self.busy:
            return
        pattern = lambda exts: " ".join(f"*.{e}" for e in sorted(exts))  # noqa: E731
        files = filedialog.askopenfilenames(
            parent=self, title="Choose files to convert",
            filetypes=[("All supported files", pattern(SUPPORTED)), ("Images and GIFs", pattern(IMAGE_IN)),
                       ("Video", pattern(VIDEO_IN)), ("Audio", pattern(AUDIO_IN)), ("All files", "*.*")],
        )
        self.add_paths([Path(f) for f in files])

    def choose_folder(self) -> None:
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self, title="Choose a folder to convert")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> int:
        if self.busy:
            self.app.toast("Wait for the current conversion to finish before adding files.", "warning")
            return 0
        files: list[Path] = []
        for p in paths:
            if p.is_dir():
                files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower().lstrip(".") in SUPPORTED)
            elif p.is_file():
                files.append(p)
        known = {c.path.resolve() for c in self.cards}
        new_items: list[FileItem] = []
        for f in files:
            resolved = f.resolve()
            if resolved in known:
                continue
            known.add(resolved)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            new_items.append(FileItem(f, kind_of(f), size_bytes=size))
        # A short highlight for small batches; a folder of hundreds of files should simply appear.
        if len(new_items) <= 12:
            for item in new_items:
                item.flash = True
        self.cards.extend(new_items)
        if new_items:
            self.hide_banner()
        self.layout_cards()
        self.refresh()
        return len(new_items)

    def _request_details(self, item: FileItem) -> None:
        """Thumbnails and media details load in the background, only for files that come into view."""
        self.app.details.request(item.path, item.kind, lambda thumb, details: self._details_ready(item, thumb, details))

    def _details_ready(self, item: FileItem, thumb, details: dict) -> None:
        item.details = details
        if details.get("error") and item.state == "ready" and not self.busy:
            # Damaged or mislabelled: say so now, and leave it out, rather than let it fail during the conversion.
            item.state, item.error = "unreadable", details["error"]
            self.refresh()
        if thumb is not None:
            item.tile = thumb_tile(thumb, item.kind)
            self.list.remember_tile(item)
        self.list.refresh_item(item)

    def remove(self, item: FileItem) -> None:
        if self.busy or item not in self.cards:
            return
        self.list.remove(item)  # the list shares self.cards, so this removes it from the queue too
        self.refresh()
        self._update_count()
        if not self.cards:
            self.layout_cards()

    def clear(self) -> None:
        if self.busy:
            return
        self.cards = []
        self.hide_banner()
        self.total.jump(0)
        self.layout_cards()
        self.refresh()

    def convert_more(self) -> None:
        self.clear()
        self.choose_files()

    def layout_cards(self) -> None:
        has = bool(self.cards)
        self.drop.set_compact(has)
        if has:
            self.left.grid_rowconfigure(1, weight=0)
            self.left.grid_rowconfigure(3, weight=1)
            self.drop.grid(row=1, column=0, sticky="ew")
            self.queue_head.grid(row=2, column=0, sticky="ew", pady=(14, 6))
            self.list.grid(row=3, column=0, sticky="nsew")
        else:
            self.left.grid_rowconfigure(1, weight=1)
            self.left.grid_rowconfigure(3, weight=0)
            self.drop.grid(row=1, column=0, sticky="nsew")
            self.queue_head.grid_remove()
            self.list.grid_remove()
        self.list.set_items(self.cards)
        self._update_count()

    def _update_count(self) -> None:
        total = sum(c.size_bytes for c in self.cards)
        n = len(self.cards)
        self.queue_count.configure(text=f"{n} file{'s' if n != 1 else ''}  ·  {human_size(total)}" if n else "")

    def show_error(self, card: FileItem) -> None:
        title = "This file can't be read" if card.state == "unreadable" else "Conversion failed"
        messagebox.showerror(title, f"{card.path.name}\n\n{card.error}", parent=self)

    # ================================================================== refresh (format choices + status)

    def _available(self):
        cards = [c for c in self.cards if c.kind and c.state != "unreadable"]
        union: list[str] = []
        for c in cards:
            for t in targets_for(c.path, c.kind):
                if t not in union:
                    union.append(t)
        union.sort(key=lambda t: ORDER.index(t) if t in ORDER else 99)
        moving = any(c.kind in ("animation", "video") for c in cards)
        return cards, union, moving

    def refresh(self) -> None:
        cards, union, moving = self._available()

        # choose a target: keep the current one if possible, else the remembered one, else a recommendation
        kinds = [c.kind for c in cards]
        dominant = max(set(kinds), key=kinds.count) if kinds else None
        suggested = [t for t in recommended_for(dominant, cards[0].path.suffix if cards else "") if t in union][:3]
        if self.target not in union:
            remembered = self.s.get("target")
            self.target = remembered if remembered in union else (suggested[0] if suggested else (union[0] if union else None))
        if self.target:
            self.s["target"] = self.target

        signature = (tuple(union), moving, tuple(suggested))
        if signature != self._signature:
            self._signature = signature
            self._build_chips(union, moving, suggested)
            # New choices: bring them into view, wherever the panel was scrolled to before.
            self.after_idle(lambda: self.settings_panel._parent_canvas.yview_moveto(0.0))
        else:
            for t, buttons in self.chips.items():
                for b in buttons:
                    style_chip(b, t == self.target, t in suggested)

        counts = {}
        for k in kinds:
            counts[k] = counts.get(k, 0) + 1
        if cards:
            names = {"image": "image", "animation": "animation", "video": "video", "audio": "audio file"}
            parts = [f"{n} {names[k]}{'s' if n != 1 else ''}" for k, n in counts.items()]
            self.detected.configure(text="Detected " + ", ".join(parts) + ".")
        else:
            self.detected.configure(text="Add files and the best formats will be suggested.")

        self._refresh_sections(cards, moving)
        self._refresh_status()

    def _build_chips(self, union, moving, suggested) -> None:
        for w in self.chip_area.winfo_children():
            w.destroy()
        self.chips = {}
        self.chip_area.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="chips")
        row = 0

        def heading(text):
            nonlocal row
            ctk.CTkLabel(self.chip_area, text=text, font=T.font(10, "semibold"), text_color=T.FAINT, anchor="w",
                         height=16).grid(row=row, column=0, columnspan=4, sticky="w", pady=(8 if row else 0, 4))
            row += 1

        def place(targets):
            nonlocal row
            for i, t in enumerate(targets):
                b = chip(self.chip_area, t.upper(), lambda t=t: self.select(t), t == self.target, t in suggested)
                b.grid(row=row + i // 4, column=i % 4, sticky="ew", padx=2, pady=2)
                Tooltip(b, FORMAT_INFO.get(t, ""))
                self.chips.setdefault(t, []).append(b)
            row += (len(targets) + 3) // 4

        if suggested:
            heading("SUGGESTED")
            place(suggested)
        groups = {g: [] for g in GROUP_ORDER}
        for t in union:
            groups[group_of(t, moving)].append(t)
        for g in GROUP_ORDER:
            if groups[g]:
                heading(g.upper())
                place(groups[g])

    def select(self, target: str) -> None:
        if self.busy:
            return
        self.target = target
        self.s["target"] = target
        self.refresh()

    def _refresh_sections(self, cards, moving) -> None:
        t = self.target
        row = 1
        # format card
        if t:
            self.format_card.grid(row=3, column=0, sticky="ew", pady=(10, 0))
            self.format_name.configure(text=f"{t.upper()}")
            info = STILL_GIF_INFO if (t == "gif" and not moving) else FORMAT_INFO.get(t, "")
            self.format_desc.configure(text=info)
            able = [c for c in cards if t in targets_for(c.path, c.kind)]
            unsupported = len(self.cards) - len(cards)
            if len(able) == len(self.cards):
                self.format_cover.configure(text=f"All {len(able)} file{'s' if len(able) != 1 else ''} can be converted.",
                                            text_color=T.SUCCESS)
            else:
                skip = len(self.cards) - len(able)
                why = " (unsupported type)" if unsupported == skip else ""
                self.format_cover.configure(
                    text=f"{len(able)} of {len(self.cards)} files can become {t.upper()}. "
                         f"{skip} will be skipped{why}.",
                    text_color=T.WARNING)
        else:
            self.format_card.grid_remove()

        def show(frame, visible):
            nonlocal row
            if visible:
                row += 1
                frame.grid(row=row, column=0, sticky="ew", padx=(10, 8), pady=(22, 0))
            else:
                frame.grid_remove()

        # quality
        show(self.sec_quality, bool(t))
        if t:
            lossless_mode = self.s.get("lossless")
            if t in LOSSLESS_ONLY:
                self.quality.grid_remove()
                self.quality_text.configure(text=f"{t.upper()} is lossless, so the picture or sound is kept exactly. "
                                                 "There is nothing to choose here.")
                self.quality_tech.configure(text="")
            else:
                self.quality.grid()
                q = self.s.get("quality", "max")
                self.quality.set(q)
                if lossless_mode:
                    self.quality_text.configure(text="Lossless mode is on (see Advanced settings), so this choice is ignored.")
                    self.quality_tech.configure(text=describe_settings(t, "lossless", self.s.get("codec", "h264")))
                else:
                    self.quality_text.configure(text=QUALITY_TEXT[q])
                    self.quality_tech.configure(text=describe_settings(t, q, self.s.get("codec", "h264")))

        # codec
        show_codec = bool(t in CODEC_TARGETS and moving)
        show(self.sec_codec, show_codec)
        if show_codec:
            c = self.s.get("codec", "h264")
            self.codec.set(c)
            text = CODEC_TEXT[c]
            if t == "mov" and c == "av1":
                text = "MOV files can't hold AV1, so H.265 will be used instead."
            if self.s.get("keep_streams", True):
                text += " Videos that already use this compression are copied without re-encoding."
            self.codec_text.configure(text=text)

        # size / frame rate
        visual = bool(t) and t not in {"mp3", "m4a", "wav", "flac", "ogg", "opus", "aac", "aiff", "wma"}
        show(self.sec_size, visual)
        if visual:
            moving_out = moving and t in MOVING_TARGETS
            self.size_title.configure(text="Size and frame rate" if moving_out else "Size")
            if moving_out:
                self.rate_menu.grid()
            else:
                self.rate_menu.grid_remove()
            extra = " Changing either one means the video is re-encoded." if moving_out and t in CODEC_TARGETS else ""
            self.size_text.configure(text="Original settings are kept unless you change them. Files are never enlarged." + extra)

        # save to
        show(self.sec_save, True)
        mode = self.s.get("save_mode", "same")
        self.save.set(mode)
        if mode == "same":
            self.save_path.configure(text="Next to each original file")
            self.save_change.grid_remove()
        elif mode == "converted":
            self.save_path.configure(text="In a “Converted” folder next to each original")
            self.save_change.grid_remove()
        else:
            path = self.s.get("custom_dir", "")
            self.save_path.configure(text=fit_text(path, T.font(11), 200))
            self.save_tip.set(path)
            self.save_change.grid()

        show(self.sec_adv, True)
        # The toggle is a button whose text sits 9 px in from its edge: shift the section so its heading lines up
        # with the other headings, and give its contents those 9 px back.
        self.sec_adv.grid_configure(pady=(18, 12), padx=(1, 8))

    def _lock_settings(self, locked: bool) -> None:
        """While files convert, the settings can't change: they wouldn't apply to the running job, which is confusing."""
        if locked:
            self._locked_states = {}

            def walk(widget) -> None:
                for w in widget.winfo_children():
                    if isinstance(w, Segmented):
                        self._locked_states[w] = None
                        w.set_locked(True)
                        continue
                    if isinstance(w, (ctk.CTkButton, ctk.CTkSwitch, ctk.CTkOptionMenu)):
                        self._locked_states[w] = w.cget("state")
                        w.configure(state="disabled")
                    walk(w)

            walk(self.settings_panel)
            self.detected.configure(text="Settings are locked until the conversion finishes.")
        else:
            for w, state in getattr(self, "_locked_states", {}).items():
                if w.winfo_exists():
                    w.set_locked(False) if isinstance(w, Segmented) else w.configure(state=state)
            self._locked_states = {}  # refresh() puts the "Detected ..." line back

    def _refresh_status(self) -> None:
        if self.busy:
            return
        cards = [c for c in self.cards if c.kind]
        t = self.target
        able = [c for c in cards if t and t in targets_for(c.path, c.kind)]
        self.open_btn.grid() if self.last_output else self.open_btn.grid_remove()
        self.cancel_btn.grid_remove()
        self.clear_btn.grid() if self.cards else self.clear_btn.grid_remove()
        reason = ""
        if not self.cards:
            title, detail, reason = "No files yet", f"Add files to get started. Drag them in or press {T.KEY}O.", "Add files first"
        elif not cards:
            title, detail, reason = "Nothing to convert", "None of these file types are supported.", "No supported files"
        elif not t:
            title, detail, reason = "Choose a format", "Pick what to convert to on the right.", "Choose a format first"
        elif not able:
            title = "Nothing to convert"
            detail = f"None of these files can become {t.upper()}. Pick another format."
            reason = detail
        else:
            size = sum(c.size_bytes for c in able)
            title = f"{len(able)} file{'s' if len(able) != 1 else ''} ready  ·  {human_size(size)}"
            qname = "Lossless" if self.s.get("lossless") else {"small": "Smaller file", "balanced": "Balanced",
                                                                "high": "High quality", "max": "Maximum quality"}[self.s.get("quality", "max")]
            detail = f"Converting to {t.upper()}  ·  {qname if t not in LOSSLESS_ONLY else 'Lossless'}"
            skipped = len(self.cards) - len(able)
            if skipped:
                detail += f"  ·  {skipped} will be skipped"
        self.status_title.configure(text=title, text_color=T.TEXT)
        self.status_detail.configure(text=detail, text_color=T.MUTED)
        set_primary_enabled(self.convert_btn, not reason)
        self.convert_tip.set(reason)

    # ================================================================== running

    def start(self) -> None:
        if self.busy or not getattr(self.convert_btn, "_mc_enabled", True):
            return
        t = self.target
        usable = [c for c in self.cards if c.kind and c.state != "unreadable"]
        todo = [c for c in usable if c.state != "done"] or usable
        if not t or not any(t in targets_for(c.path, c.kind) for c in todo):
            self._refresh_status()
            return
        self.busy = True
        self.app.set_busy(True)
        self.cancel_event.clear()
        self.hide_banner()
        for c in todo:
            c.output, c.out_size, c.error, c.detail, c.fraction, c.state = None, 0, "", "", None, "waiting"
        self.list.set_locked(True)
        self._lock_settings(True)
        set_primary_enabled(self.convert_btn, False)
        self.clear_btn.grid_remove()
        self.open_btn.grid_remove()
        self.cancel_btn.grid()
        self.cancel_btn.configure(state="normal", text="Cancel", image=T.icon("stop", 15, T.ERROR))
        self.total.grid()
        self.total.jump(0)
        threading.Thread(target=self._worker, args=(todo, t, self.options()), daemon=True).start()

    def cancel(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.cancel_btn.configure(state="disabled", text="Cancelling…", image=T.blank(15))
            self.status_detail.configure(text="Stopping safely. The unfinished file will be removed.")

    def _worker(self, cards: list[FileItem], target: str, opts: Options) -> None:
        put = self.events.put
        total = len(cards)
        results = {"done": [], "failed": 0, "skipped": 0, "cancelled": False}
        for i, card in enumerate(cards):
            if self.cancel_event.is_set():
                put(("card", card, "cancelled", None, ""))
                continue
            if target not in targets_for(card.path, card.kind):
                results["skipped"] += 1
                put(("card", card, "skipped", None, f"{card.kind.capitalize()} files can't become {target.upper()}"))
                continue

            def progress(fraction: float, text: str, card=card, i=i) -> None:
                put(("card", card, "working", fraction, text))
                put(("total", (i + fraction) / total, i + 1, total, card.path.name, text))

            put(("card", card, "working", 0.0, "Starting"))
            put(("total", i / total, i + 1, total, card.path.name, "Starting"))
            try:
                out = convert(card.path, target, opts, progress, self.cancel_event)
                card.output, card.out_size = out, out.stat().st_size
                results["done"].append((card.size_bytes, card.out_size))
                put(("card", card, "done", None, ""))
                put(("output", out))
            except Cancelled:
                results["cancelled"] = True
                put(("card", card, "cancelled", None, ""))
            except ConversionError as exc:
                results["failed"] += 1
                card.error = str(exc)
                put(("card", card, "failed", None, ""))
            except Exception as exc:  # one bad file never stops the batch
                results["failed"] += 1
                card.error = f"{type(exc).__name__}: {exc}"
                put(("card", card, "failed", None, ""))
        results["cancelled"] = results["cancelled"] or self.cancel_event.is_set()
        put(("finished", results, target))

    def _drain(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "card":
                    _, item, state, fraction, detail = ev
                    item.state, item.fraction, item.detail = state, fraction, detail
                    self.list.refresh_item(item)
                elif kind == "total":
                    _, fraction, n, total, name, text = ev
                    self.total.glide(fraction)
                    self.status_title.configure(text=f"Converting {n} of {total}  ·  {fraction * 100:.0f}%", text_color=T.TEXT)
                    step = f"  ·  {text}"  # shorten only the file name, so the current step stays readable
                    name = fit_text(name, T.font(12), 420 - T.font(12).measure(step))
                    self.status_detail.configure(text=name + step, text_color=T.MUTED)
                elif kind == "output":
                    self.last_output = ev[1]
                elif kind == "finished":
                    self._finish(ev[1], ev[2])
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def _finish(self, results: dict, target: str) -> None:
        self.busy = False
        self.app.set_busy(False)
        self.list.set_locked(False)
        self._lock_settings(False)
        self.total.glide(1.0)
        self.after(600, self.total.grid_remove)
        done, failed, skipped, cancelled = results["done"], results["failed"], results["skipped"], results["cancelled"]
        before = sum(a for a, _ in done)
        after = sum(b for _, b in done)
        if not done and not failed and cancelled:
            self.show_banner("cancelled", "Conversion cancelled", "No files were changed.")
        else:
            parts = []
            if failed:
                parts.append(f"{failed} failed")
            if skipped:
                parts.append(f"{skipped} skipped")
            if cancelled:
                parts.append("cancelled before the end")
            if done and before:
                saved = before - after
                if saved > 0:
                    parts.append(f"saved {human_size(saved)} ({saved / before * 100:.0f}%)")
                else:
                    parts.append(f"output is {human_size(-saved)} larger than the originals")
            title = f"{len(done)} file{'s' if len(done) != 1 else ''} converted to {target.upper()}"
            tone = "success" if done and not failed and not cancelled else ("warning" if done else "error")
            if not done:
                title = "No files were converted"
            detail = "  ·  ".join(parts)
            self.show_banner(tone, title, (detail[:1].upper() + detail[1:]) if detail else "Everything went smoothly.")
        self.refresh()
        self.app.notify_done(len(done), failed)

    # ================================================================== banner

    def show_banner(self, tone: str, title: str, detail: str) -> None:
        styles = {
            "success": (T.SUCCESS_SOFT, T.SUCCESS, "check_circle"),
            "warning": (T.WARNING_SOFT, T.WARNING, "warning"),
            "error": (T.ERROR_SOFT, T.ERROR, "error_circle"),
            "cancelled": (T.SURFACE_ALT, T.MUTED, "info"),
        }
        bg, fg, icon_name = styles[tone]
        self.banner.configure(fg_color=bg, border_color=bg)
        self.banner_icon.configure(image=T.icon(icon_name, 26, fg))
        self._banner_title_text = title
        self.banner_title.configure(text=title, text_color=T.TEXT)
        self.banner_detail.configure(text=detail)
        for b in (self.banner_open, self.banner_more):
            M.paint(b, 0, fg_color=T.SURFACE)
        self.banner_open.grid() if self.last_output else self.banner_open.grid_remove()
        self.after_idle(self._fit_banner_text)
        if self.banner.winfo_ismapped():  # possibly mid-collapse: stop that and show it fully
            M.cancel(self.banner, "height")
            self.banner.grid_propagate(True)
            return
        if M.enabled():
            target = M.natural_height(self.banner)
            M.collapse_now(self.banner)
            self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))
            M.expand(self.banner, target)
        else:
            self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))

    def _fit_banner_text(self, _e=None) -> None:
        """Fit the banner's title, and wrap its detail line, into the room left beside the buttons, so neither is
        cut off at small window sizes."""
        k = scaling(self)
        buttons = sum(b.winfo_reqwidth() for b in (self.banner_open, self.banner_more, self.banner_close)
                      if b.winfo_manager()) / k
        room = self.banner.winfo_width() / k - buttons - (14 + 36 + 10) - (8 + 10 + 8) - 12
        if room > 80:
            self.banner_detail.configure(wraplength=room)
            self.banner_title.configure(text=fit_text(self._banner_title_text, T.font(14, "semibold"), room, end=True))

    def hide_banner(self) -> None:
        if self.banner.winfo_ismapped() and M.enabled():
            M.collapse(self.banner, duration=M.NORMAL, done=self.banner.grid_remove)
        else:
            self.banner.grid_remove()

    def open_output(self) -> None:
        if self.last_output:
            self.app.reveal(self.last_output)
