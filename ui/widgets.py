"""Reusable components built on CustomTkinter, styled with the tokens in theme.py."""

from __future__ import annotations

import io
import queue
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageColor, ImageDraw, ImageOps, ImageTk

from . import motion as M
from . import theme as T

# ----------------------------------------------------------------------------- small helpers


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def scaling(widget) -> float:
    try:
        return ctk.ScalingTracker.get_widget_scaling(widget)
    except Exception:
        return 1.0


def fit_text(text: str, fnt: ctk.CTkFont, max_px: float, end: bool = False) -> str:
    """Shorten text to fit. File names get a middle ellipsis so the extension stays visible;
    sentences (end=True) are cut at the end."""
    if max_px <= 0 or fnt.measure(text) <= max_px:
        return text
    if end:
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fnt.measure(text[:mid].rstrip() + "…") <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + "…"
    stem, dot, ext = text.rpartition(".")
    tail = (stem[-4:] + dot + ext) if dot and len(ext) <= 5 else text[-6:]
    head = text[: len(text) - len(tail)]
    lo, hi = 0, len(head)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.measure(head[:mid] + "…" + tail) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return head[:lo].rstrip() + "…" + tail


def make_focusable(button, ring: tuple[str, str] = T.FOCUS) -> None:
    """Let Tab reach a CTk button, switch, check box or drop-down menu, show a focus ring, and work it with Enter or
    Space. Only keyboard focus shows the ring: clicking these widgets never gives them focus."""
    canvas = button._canvas  # CTk draws these on a canvas; it is the widget that can take focus

    def enabled() -> bool:
        return button.cget("state") != "disabled" and getattr(button, "_mc_enabled", True)

    # Tab skips disabled and hidden controls.
    canvas.configure(takefocus=lambda _name: 1 if enabled() and canvas.winfo_viewable() else 0)
    menu = isinstance(button, ctk.CTkOptionMenu)  # has no border, so its text turns the ring colour instead
    key = "text_color" if menu else "border_color"
    state = {"width": None, "colour": None}

    def focus_in(_e):
        state["colour"] = button.cget(key)
        if menu:
            button.configure(text_color=ring)
        else:
            state["width"] = button.cget("border_width")
            button.configure(border_width=2, border_color=ring)

    def focus_out(_e):
        if state["colour"] is None:
            return
        changes = {"border_width": state["width"]} if not menu else {}
        if button.cget(key) == ring:  # restore the colour only if nothing restyled the widget meanwhile
            changes[key] = state["colour"]
        if changes:
            button.configure(**changes)
        state["colour"] = None

    def activate(_e):
        if enabled():
            if menu:
                button._open_dropdown_menu()
            elif isinstance(button, (ctk.CTkSwitch, ctk.CTkCheckBox)):
                button.toggle()
            else:
                button.invoke()
        return "break"

    canvas.bind("<FocusIn>", focus_in, add="+")
    canvas.bind("<FocusOut>", focus_out, add="+")
    canvas.bind("<Return>", activate, add="+")
    canvas.bind("<space>", activate, add="+")


# Switch colours as (light, dark). CTk draws the knob larger than the track, so in light mode a white knob vanished
# against the white panel and the switch looked cut off: there the knob takes the accent colour when on and a mid
# grey when off. Dark mode keeps a white knob, which already stands out.
_SWITCH_ON_KNOB, _SWITCH_OFF_KNOB = (T.ACCENT[0], "#FFFFFF"), ("#7C838D", "#C9CED6")
_SWITCH_ON_TRACK, _SWITCH_OFF_TRACK = ("#B9D5F2", T.ACCENT[1]), ("#D9DDE3", "#4A505A")


def style_switch(s) -> None:
    """Colour a CTkSwitch for its current state; call again after it changes."""
    on = bool(s.get())
    s.configure(progress_color=_SWITCH_ON_TRACK, fg_color=_SWITCH_OFF_TRACK,
                button_color=_SWITCH_ON_KNOB if on else _SWITCH_OFF_KNOB,
                button_hover_color=_SWITCH_ON_KNOB if on else _SWITCH_OFF_KNOB)


def autohide_scrollbar(frame: ctk.CTkScrollableFrame, colour=T.BORDER, hover=T.BORDER_STRONG) -> None:
    """Show a scrollable panel's scrollbar only when its content is taller than the panel. The bar keeps its place
    and just turns invisible, so the content never shifts sideways when it comes and goes."""
    canvas, bar = frame._parent_canvas, frame._scrollbar
    state = {"shown": True}

    def check(_e=None) -> None:
        try:
            needed = frame.winfo_reqheight() > canvas.winfo_height() + 1
        except tk.TclError:
            return
        if needed != state["shown"]:
            state["shown"] = needed
            behind = frame._parent_frame.cget("fg_color")
            if behind == "transparent":
                behind = frame._parent_frame.cget("bg_color")
            bar.configure(button_color=colour if needed else behind, button_hover_color=hover if needed else behind)

    frame.bind("<Configure>", check, add="+")
    canvas.bind("<Configure>", check, add="+")
    frame.after(300, check)


def use_app_icon(window) -> None:
    """Give a dialog the app's icon. CTkToplevel sets its own default icon about 200 ms after it opens, so ours goes
    on after that. (macOS dialogs have no icon in their title bar.)"""
    if sys.platform != "win32":
        return
    ico = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    if ico.exists():
        window.after(250, lambda: window.winfo_exists() and window.iconbitmap(str(ico)))


def bind_tree(widget, sequence: str, func, add: str = "+") -> None:
    widget.bind(sequence, func, add=add)
    for child in widget.winfo_children():
        bind_tree(child, sequence, func, add)


def icon_circle(parent, icon_name: str, size: int, icon_size: int, bg=T.ACCENT_SOFT, fg=T.ACCENT_TEXT) -> ctk.CTkFrame:
    """An icon on a round background. (A rounded CTkLabel pads its sides and turns into an oval.)"""
    circle = ctk.CTkFrame(parent, width=size, height=size, corner_radius=size // 2, fg_color=bg)
    circle.grid_propagate(False)
    label = ctk.CTkLabel(circle, text="", image=T.icon(icon_name, icon_size, fg), fg_color="transparent")
    label.place(relx=0.5, rely=0.5, anchor="center")
    circle.icon_label = label
    return circle


# ----------------------------------------------------------------------------- buttons


def primary_button(parent, text: str, command=None, icon_name: str | None = None, height: int = 36, **kw) -> ctk.CTkButton:
    b = ctk.CTkButton(
        parent, text=text, command=command, height=height, corner_radius=T.RADIUS_SMALL,
        fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
        text_color_disabled=("#F4F7FB", "#9DB4CC"), font=T.font(13, "semibold"),
        image=T.icon(icon_name, 16, T.ON_ACCENT) if icon_name else None, compound="left", border_spacing=8, **kw,
    )
    make_focusable(b, ring=("#1D4F86", "#BFDDFB"))
    M.smooth_hover(b, T.ACCENT_HOVER)
    return b


def set_primary_enabled(button: ctk.CTkButton, enabled: bool) -> None:
    """CTk keeps the normal colour on disabled buttons; give primary buttons a clearly inactive look."""
    button._mc_enabled = enabled  # handlers check this; Tk's real "disabled" state turns icons into grey boxes
    M.paint(button, fg_color=T.ACCENT if enabled else T.ACCENT_DISABLED,
            hover_color=T.ACCENT_HOVER if enabled else T.ACCENT_DISABLED,
            cursor="hand2" if enabled else "arrow")


def secondary_button(parent, text: str, command=None, icon_name: str | None = None, height: int = 34, **kw) -> ctk.CTkButton:
    b = ctk.CTkButton(
        parent, text=text, command=command, height=height, corner_radius=T.RADIUS_SMALL,
        fg_color=T.SURFACE, hover_color=T.SURFACE_HOVER, border_width=1, border_color=T.BORDER_STRONG,
        text_color=T.TEXT, text_color_disabled=T.FAINT, font=T.font(12, "semibold"),
        image=T.icon(icon_name, 15, T.TEXT) if icon_name else None, compound="left", border_spacing=6, **kw,
    )
    make_focusable(b)
    M.smooth_hover(b, T.SURFACE_HOVER)
    return b


def ghost_button(parent, text: str = "", command=None, icon_name: str | None = None, colour=T.MUTED,
                 width: int = 0, height: int = 30, bg="transparent", **kw) -> ctk.CTkButton:
    b = ctk.CTkButton(
        parent, text=text, command=command, height=height, width=width or (height if not text else 28),
        corner_radius=T.RADIUS_SMALL, fg_color=bg, hover_color=T.SURFACE_HOVER,
        text_color=colour, text_color_disabled=T.FAINT, font=T.font(12, "semibold"),
        image=T.icon(icon_name, 15, colour) if icon_name else None, compound="left",
        border_spacing=4 if text else 0, **kw,
    )
    make_focusable(b)
    M.smooth_hover(b, T.SURFACE_HOVER)
    return b


# ----------------------------------------------------------------------------- tooltip


class Tooltip:
    """A small themed hint shown after hovering for half a second."""

    def __init__(self, widget, text: str | Callable[[], str], delay: int = 450) -> None:
        self.widget, self.text, self.delay = widget, text, delay
        self.tip: tk.Toplevel | None = None
        self.job = None
        self.x_offset: Callable[[], int] | None = None  # where the hint starts, from the widget's left edge
        bind_tree(widget, "<Enter>", self._schedule)
        bind_tree(widget, "<Leave>", self._hide)
        bind_tree(widget, "<ButtonPress>", self._hide)

    def set(self, text) -> None:
        self.text = text

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self) -> None:
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip is not None:
            return
        x = self.widget.winfo_rootx() + (self.x_offset() if self.x_offset else 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        border = T.resolve(T.BORDER_STRONG)
        tw.configure(bg=border)
        label = tk.Label(tw, text=text, justify="left", wraplength=320, padx=10, pady=6,
                         bg=T.resolve(T.SURFACE), fg=T.resolve(T.TEXT), font=("Segoe UI", 9))
        label.pack(padx=1, pady=1)
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


# ----------------------------------------------------------------------------- segmented control


@lru_cache(maxsize=512)
def _rounded_image(w: int, h: int, r: int, fill: str, border: str | None, bw: int) -> ImageTk.PhotoImage:
    """An anti-aliased rounded rectangle for a canvas, drawn at 4x and scaled down."""
    k, edge = 4, border or fill
    img = Image.new("RGBA", (w * k, h * k), ImageColor.getrgb(edge)[:3] + (0,))  # edge colour, so no dark fringe
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w * k - 1, h * k - 1), r * k, fill=edge)
    if border and bw:
        draw.rounded_rectangle((bw * k, bw * k, w * k - 1 - bw * k, h * k - 1 - bw * k), max(0, r - bw) * k, fill=fill)
    return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))


@lru_cache(maxsize=512)
def _glyph_image(name: str, px: int, colour: str) -> ImageTk.PhotoImage:
    char = T._icon_source()[1][name]
    return ImageTk.PhotoImage(T._render_glyph(char, px * 3, colour).resize((px, px), Image.LANCZOS))


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = ImageColor.getrgb(a), ImageColor.getrgb(b)
    return "#%02X%02X%02X" % tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))


class Segmented(ctk.CTkFrame):
    """A segmented control: a grey track with a raised pill that slides to the chosen option.

    Everything is drawn on one canvas, so the pill can glide between the options and their labels can
    brighten as it arrives (with a widget per option, each one's background would hide the pill).
    Once Tab reaches it, Left and Right (or Home and End) choose an option.

    options: list of (value, label, tooltip or None, icon name or None)
    """

    INSET, GAP, ICON = 3, 2, 15

    def __init__(self, parent, options, command: Callable[[str], None] | None = None, value: str | None = None,
                 height: int = 32, font_size: int = 12, equal: bool = True, min_segment: int = 0, **kw) -> None:
        self.command = command
        self.values = [o[0] for o in options]
        self.labels = {o[0]: o[1] for o in options}
        self.tips = {o[0]: o[2] for o in options}
        self.icons = {o[0]: o[3] for o in options}
        self.disabled: set[str] = set()
        self.locked = False
        self.value = value if value in self.values else self.values[0]
        self.font = T.font(font_size, "semibold")
        self.equal = equal
        natural = []
        for v in self.values:
            if self.labels[v]:
                w = self.font.measure(self.labels[v]) + 24 + (self.ICON + 6 if self.icons[v] else 0)
            else:
                w = height  # icon only: a little wider than tall
            natural.append(max(w, min_segment))
        if equal:
            natural = [max(natural)] * len(natural)
        self._natural = natural
        width = sum(natural) + 2 * self.INSET + self.GAP * (len(natural) - 1)
        super().__init__(parent, fg_color="transparent", width=width, height=height, corner_radius=0, border_width=0, **kw)

        self._rects: dict[str, tuple[int, int, int, int]] = {}  # canvas pixels: x0, y0, x1, y1
        self._layout: dict[str, tuple[int, int, int, str]] = {}  # icon x, text x, centre y, fitted label
        self._pill: list[float] | None = None  # the pill's left and right edge, None until first laid out
        self._hover: str | None = None
        self._lit = {v: 0.0 for v in self.values}  # hover highlight, 0..1
        self._scale = 0.0
        self._size = (0, 0)
        self._cfont = None
        self._shown: dict = {}  # last value given to each canvas item, to skip redundant redraws

        c = self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, takefocus=self._can_focus)
        c.place(x=0, y=0, relwidth=1, relheight=1)
        self._track = c.create_image(0, 0, anchor="nw")
        self._pill_item = c.create_image(0, 0, anchor="nw")
        self._items = {v: (c.create_image(0, 0, anchor="w"), c.create_text(0, 0, anchor="w")) for v in self.values}
        c.bind("<Configure>", self._on_configure)
        c.bind("<Motion>", lambda e: self._set_hover(self._value_at(e.x)))
        c.bind("<Leave>", lambda _e: self._set_hover(None))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<FocusIn>", lambda _e: self._paint())
        c.bind("<FocusOut>", lambda _e: self._paint())
        c.bind("<Left>", lambda _e: self._step(-1))
        c.bind("<Right>", lambda _e: self._step(1))
        c.bind("<Home>", lambda _e: self._step(-len(self.values)))
        c.bind("<End>", lambda _e: self._step(len(self.values)))
        self.tooltip = Tooltip(c, lambda: self.tips.get(self._hover) if self._hover else None)
        self.tooltip.x_offset = lambda: self._rects[self._hover][0] if self._hover in self._rects else 8

    # -- public

    def get(self) -> str | None:
        return self.value

    def set(self, value: str, notify: bool = False) -> None:
        """Choose an option; the pill glides there. notify=True also runs the command (when the choice changed)."""
        if value not in self.values or value == self.value:
            return
        self.value = value
        self._update_cursor()
        if notify and self.command:
            self.command(value)
        # Glide once whatever the choice changed has been drawn (a whole screen, after a switch), so the pill
        # gets the event loop to itself instead of stepping slowly while that repaint is going on.
        self.after_idle(self._glide)

    def set_disabled(self, values) -> None:
        """Grey out options that don't apply right now (they can't be chosen with the mouse or keyboard)."""
        values = set(values)
        if values != self.disabled:
            self.disabled = values
            self._update_cursor()
            self._paint()

    def set_locked(self, locked: bool) -> None:
        """Show the choice but don't let it change (while a conversion runs, for example)."""
        if locked != self.locked:
            self.locked = locked
            self._update_cursor()
            self._paint()

    # -- layout and drawing

    def _on_configure(self, e) -> None:
        s = scaling(self)
        if s != self._scale or self._cfont is None:
            self._scale = s
            self._cfont = tkfont.Font(root=self, family=self.font.cget("family"), weight=self.font.cget("weight"),
                                      size=-round(abs(self.font.cget("size")) * s))
        W, H = self._size = (e.width, e.height)
        inset, gap = round(self.INSET * s), round(self.GAP * s)
        avail = W - 2 * inset - gap * (len(self.values) - 1)
        if self.equal:
            widths = [avail / len(self.values)] * len(self.values)
        else:
            widths = [avail * w / sum(self._natural) for w in self._natural]
        icon_px, icon_gap, pad = round(self.ICON * s), round(6 * s), round(6 * s)  # pad: least room beside a label
        x = float(inset)
        for v, w in zip(self.values, widths):
            x0, x1 = round(x), round(x + w)
            self._rects[v] = (x0, inset, x1, H - inset)
            label, icon_name = self.labels[v], self.icons[v]
            if label:
                room = x1 - x0 - 2 * pad - (icon_px + icon_gap if icon_name else 0)
                text = fit_text(label, self._cfont, room, end=True)
                group = self._cfont.measure(text) + (icon_px + icon_gap if icon_name else 0)
                gx = x0 + (x1 - x0 - group) / 2
                self._layout[v] = (round(gx), round(gx + (icon_px + icon_gap if icon_name else 0)), H // 2, text)
            else:
                self._layout[v] = (round(x0 + (x1 - x0 - icon_px) / 2), 0, H // 2, "")
            x += w + gap
        if self._pill is None or not M.running(self, "pill"):
            r = self._rects[self.value]
            self._pill = [r[0], r[2]]
        self._shown.clear()
        self._paint()
        self.after(400, self._prewarm)

    def _prewarm(self) -> None:
        """Draw each icon's in-between colours ahead of time, while nothing is happening, so the first glide
        doesn't stall to make them."""
        if not self.winfo_exists():
            return
        res, px = T.resolve, round(self.ICON * self._scale)
        muted, text, accent = res(T.MUTED), res(T.TEXT), res(T.ACCENT_TEXT)
        for name in {n for n in self.icons.values() if n}:
            for base in (muted, text):
                for i in range(9):
                    _glyph_image(name, px, _mix(base, accent, i / 8))

    def _glide(self) -> None:
        goal = self._rects.get(self.value)
        if goal is None or self._pill is None:
            return
        start = tuple(self._pill)
        distance = abs(goal[0] - start[0]) / max(self._scale, 0.5)
        duration = int(min(M.SLOW, max(M.NORMAL, 140 + distance * 0.5)))  # longer trips take a little longer

        def step(t: float) -> None:
            r = self._rects.get(self.value, goal)  # follow the target if the window is resized mid-glide
            self._pill = [start[0] + (r[0] - start[0]) * t, start[1] + (r[2] - start[1]) * t]
            self._paint()

        M.animate(self, "pill", duration, step, ease=M.ease_out_quart)

    def _put(self, item, kind: str, **opts) -> None:
        key = (item, kind)
        if self._shown.get(key) != opts:
            self._shown[key] = opts
            if kind == "coords":
                self.canvas.coords(item, opts["x"], opts["y"])
            else:
                self.canvas.itemconfigure(item, **opts)

    def _paint(self) -> None:
        if self._pill is None or not self._rects:
            return
        c, s, res = self.canvas, self._scale, T.resolve
        W, H = self._size
        line = max(1, round(s))
        bg = self._apply_appearance_mode(self._bg_color)
        if self._shown.get("bg") != bg:
            self._shown["bg"] = bg
            c.configure(bg=bg)
        self._put(self._track, "image", image=_rounded_image(W, H, round(T.RADIUS * s), res(T.SURFACE_ALT), res(T.BORDER), line))

        x0, x1 = self._pill
        r = self._rects[self.value]
        focused = str(c.tk.call("focus")) == str(c)
        border, bw = (res(T.FOCUS), max(2, round(2 * s))) if focused else (res(T.SEGMENT_BORDER), line)
        self._put(self._pill_item, "image", image=_rounded_image(max(2, round(x1 - x0)), r[3] - r[1], round(T.RADIUS_SMALL * s),
                                                                 res(T.SEGMENT), border, bw))
        self._put(self._pill_item, "coords", x=round(x0), y=r[1])

        icon_px = round(self.ICON * s)
        muted, text, faint, accent = res(T.MUTED), res(T.TEXT), res(T.FAINT), res(T.ACCENT_TEXT)
        for v in self.values:
            rx0, _, rx1, _ = self._rects[v]
            icon_item, text_item = self._items[v]
            ix, tx, cy, label = self._layout[v]
            covered = max(0.0, min(x1, rx1) - max(x0, rx0)) / max(1, rx1 - rx0)  # how much of this option the pill covers
            if v in self.disabled or self.locked:
                fg = icon_fg = faint
            else:
                lit = round(max(covered, self._lit[v]) * 8) / 8  # 8 steps are smooth enough and keep the cache small
                fg = _mix(muted, text, lit)
                icon_fg = _mix(_mix(muted, text, round(self._lit[v] * 8) / 8), accent, round(covered * 8) / 8)
            self._put(text_item, "text", text=label, font=self._cfont, fill=fg)
            self._put(text_item, "coords", x=tx, y=cy)
            if self.icons[v]:
                self._put(icon_item, "image", image=_glyph_image(self.icons[v], icon_px, icon_fg))
                self._put(icon_item, "coords", x=ix, y=cy + (round(s) if label else 0))  # optical centre beside text

    def _draw(self, no_color_updates: bool = False) -> None:
        super()._draw(no_color_updates)
        if hasattr(self, "canvas"):  # theme or scaling changed
            self._shown.clear()
            self._paint()

    # -- input

    def _value_at(self, x: int) -> str | None:
        for v in self.values:
            x0, _, x1, _ = self._rects.get(v, (0, 0, 0, 0))
            if x0 - 2 <= x <= x1 + 2:
                return v
        return None

    def _on_press(self, e) -> None:
        v = self._value_at(e.x)
        if v is not None and v not in self.disabled and not self.locked:
            self.set(v, notify=True)

    def _step(self, direction: int) -> str:
        usable = [v for v in self.values if v not in self.disabled]
        if usable and not self.locked:
            i = self.values.index(self.value)
            if abs(direction) > 1:
                target = usable[0] if direction < 0 else usable[-1]
            else:
                later = [v for v in self.values[i + 1:] if v in usable] if direction > 0 else \
                    [v for v in reversed(self.values[:i]) if v in usable]
                target = later[0] if later else self.value
            self.set(target, notify=True)
        return "break"

    def _can_focus(self, _name=None) -> int:
        return 1 if not self.locked and len(self.disabled) < len(self.values) and self.canvas.winfo_viewable() else 0

    def _set_hover(self, value: str | None) -> None:
        if value == self._hover:
            return
        self._hover = value
        self._update_cursor()
        self.tooltip._hide()
        if value is not None and self.tips.get(value):
            self.tooltip._schedule()
        for v in self.values:
            goal = 1.0 if v == value and v not in self.disabled and not self.locked else 0.0
            start = self._lit[v]
            if start == goal:
                continue

            def step(t: float, v=v, start=start, goal=goal) -> None:
                self._lit[v] = start + (goal - start) * t
                self._paint()

            M.animate(self, f"hover-{v}", M.FAST, step)

    def _update_cursor(self) -> None:
        h = self._hover
        usable = h is not None and h != self.value and h not in self.disabled and not self.locked
        self.canvas.configure(cursor="hand2" if usable else "")


# ----------------------------------------------------------------------------- format chip


def chip(parent, text: str, command, selected: bool = False, recommended: bool = False) -> ctk.CTkButton:
    b = ctk.CTkButton(parent, text=text, command=command, height=30, width=62, corner_radius=T.RADIUS_SMALL,
                      font=T.font(12, "semibold"), border_width=1)
    style_chip(b, selected, recommended, animate=False)
    make_focusable(b)
    M.smooth_hover(b, T.ACCENT_HOVER if selected else T.ACCENT_SOFT)
    return b


def style_chip(b: ctk.CTkButton, selected: bool, recommended: bool = False, animate: bool = True) -> None:
    d = M.NORMAL if animate else 0
    if selected:
        M.paint(b, d, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT, border_color=T.ACCENT)
    else:
        M.paint(b, d, fg_color=T.SURFACE, hover_color=T.ACCENT_SOFT,
                text_color=T.ACCENT_TEXT if recommended else T.TEXT,
                border_color=T.ACCENT if recommended else T.BORDER_STRONG)


# ----------------------------------------------------------------------------- smooth progress bar


class SmoothProgress(ctk.CTkProgressBar):
    """A progress bar that glides to new values instead of jumping."""

    def __init__(self, parent, **kw) -> None:
        kw.setdefault("height", 6)
        kw.setdefault("corner_radius", 3)
        kw.setdefault("fg_color", T.SURFACE_ALT)
        kw.setdefault("progress_color", T.ACCENT)
        super().__init__(parent, **kw)
        self._value = 0.0
        self._target = 0.0
        self._job = None
        self._last = 0.0
        super().set(0)

    def glide(self, value: float) -> None:
        if not M.enabled():
            self.jump(max(0.0, min(1.0, value)))
            return
        self._target = max(0.0, min(1.0, value))
        if value < self._value:  # going backwards (a new job): jump
            self._value = self._target
            super().set(self._value)
        if self._job is None:
            self._last = time.perf_counter()
            M.hold_precise_timer(self, True)
            self._step()

    def jump(self, value: float) -> None:
        self._value = self._target = value
        super().set(value)

    def _step(self) -> None:
        diff = self._target - self._value
        if abs(diff) < 0.002 or not self.winfo_exists():
            self._value = self._target
            super().set(self._value)
            self._job = None
            M.hold_precise_timer(self, False)
            return
        now = time.perf_counter()
        dt, self._last = min(now - self._last, 0.05), now
        self._value += diff * (1 - 0.75 ** (dt / 0.016))  # the same glide at any step rate
        super().set(self._value)
        self._job = self.after(M.next_delay(now), self._step)


# ----------------------------------------------------------------------------- drop zone


def _dashed_box(w: int, h: int, radius: int, fill: str, line: str, line_w: float, dash: float, gap: float,
                ss: int = 2) -> Image.Image:
    """A rounded rectangle with a dashed outline, drawn at ss x size and scaled down for smooth edges."""
    W, H, R, LW = w * ss, h * ss, radius * ss, line_w * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = LW / 2 + ss
    d.rounded_rectangle([inset, inset, W - inset, H - inset], radius=R, fill=fill)
    # Walk the outline as a polyline and draw every other stretch.
    import math

    pts: list[tuple[float, float]] = []
    x0, y0, x1, y1 = inset, inset, W - inset, H - inset

    def arc(cx, cy, a0, a1):
        steps = 12
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))

    pts.append((x0 + R, y0))
    pts.append((x1 - R, y0))
    arc(x1 - R, y0 + R, -90, 0)
    pts.append((x1, y1 - R))
    arc(x1 - R, y1 - R, 0, 90)
    pts.append((x0 + R, y1))
    arc(x0 + R, y1 - R, 90, 180)
    pts.append((x0, y0 + R))
    arc(x0 + R, y0 + R, 180, 270)

    on, remaining = True, dash * ss
    segment = [pts[0]]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        length = math.hypot(bx - ax, by - ay)
        pos = 0.0
        while length - pos > remaining:
            pos += remaining
            t = pos / length
            p = (ax + (bx - ax) * t, ay + (by - ay) * t)
            if on:
                segment.append(p)
                d.line(segment, fill=line, width=int(LW), joint="curve")
            segment = [p]
            on = not on
            remaining = (dash if on else gap) * ss
        remaining -= length - pos
        segment.append((bx, by))
    if on and len(segment) > 1:
        d.line(segment, fill=line, width=int(LW), joint="curve")
    return img.resize((w, h), Image.LANCZOS)


class DashedPanel(ctk.CTkFrame):
    """A panel with a dashed rounded border. Child widgets go in .body, which matches the fill colour."""

    def __init__(self, parent, fill=T.SURFACE, line=T.BORDER_STRONG, radius: int = T.RADIUS_LARGE, **kw) -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0, border_width=0, **kw)
        self.fill, self.line, self.radius = fill, line, radius
        self.bg_label = ctk.CTkLabel(self, text="")
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = ctk.CTkFrame(self, fg_color=fill, corner_radius=0)
        self._job = None
        self._size = (0, 0)
        self.bind("<Configure>", self._on_configure)

    def set_colours(self, fill, line) -> None:
        self.fill, self.line = fill, line
        self.body.configure(fg_color=fill)
        for child in self.body.winfo_children():
            if isinstance(child, (ctk.CTkFrame,)) and getattr(child, "_follows_fill", False):
                child.configure(fg_color=fill)
        self._render()

    def _on_configure(self, event) -> None:
        if (event.width, event.height) != self._size:
            self._size = (event.width, event.height)
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(30, self._render)

    def _render(self) -> None:
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        s = scaling(self)
        lw, lh = w / s, h / s
        light = _dashed_box(w, h, int(self.radius * s), self.fill[0], self.line[0], 1.6 * s, 7 * s, 5 * s)
        dark = _dashed_box(w, h, int(self.radius * s), self.fill[1], self.line[1], 1.6 * s, 7 * s, 5 * s)
        self.bg_label.configure(image=ctk.CTkImage(light_image=light, dark_image=dark, size=(lw, lh)))
        inset = int(4 * s) + int(self.radius * s * 0.3)
        # Plain Tk placement: CustomTkinter's own place() rejects the negative size offset used for the inset.
        tk.Place.place_configure(self.body, x=inset, y=inset, relwidth=1, relheight=1,
                                 width=-2 * inset, height=-2 * inset)


# ----------------------------------------------------------------------------- media details + thumbnails


KIND_ICON = {"image": "photo", "animation": "gif", "video": "video", "audio": "audio", None: "doc"}
KIND_TINT = {
    "image": (("#E8F1FB", "#1C2A3A"), ("#2B6CB0", "#78B1EC")),
    "animation": (("#F3ECFB", "#2A2238"), ("#7B4BC2", "#B697EA")),
    "video": (("#FCEEE6", "#3A261D"), ("#B45A22", "#EE9E6C")),
    "audio": (("#E6F4EB", "#1B3024"), ("#1E7F45", "#4CC07A")),
    None: ((T.SURFACE_ALT[0], T.SURFACE_ALT[1]), (T.MUTED[0], T.MUTED[1])),
}


TILE_PX, TILE_RADIUS = 96, 17  # drawn at about 2x the 44 px display size


def _tile_base(colour: str) -> Image.Image:
    img = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, TILE_PX - 1, TILE_PX - 1], radius=TILE_RADIUS, fill=colour)
    return img


@lru_cache(maxsize=None)
def kind_tile(kind: str | None) -> ctk.CTkImage:
    """A tinted rounded square with the file-type icon, for files without a preview."""
    bg, fg = KIND_TINT[kind]
    out = []
    for mode in (0, 1):
        tile = _tile_base(bg[mode])
        glyph = T._render_glyph(T._icon_source()[1][KIND_ICON[kind]], 44, fg[mode])
        tile.alpha_composite(glyph, ((TILE_PX - 44) // 2, (TILE_PX - 44) // 2))
        out.append(tile)
    return ctk.CTkImage(light_image=out[0], dark_image=out[1], size=(44, 44))


def thumb_tile(img: Image.Image, kind: str | None) -> ctk.CTkImage:
    """A preview cropped to a rounded square. Transparent areas show the file-type tint."""
    bg, _ = KIND_TINT[kind]
    fitted = ImageOps.fit(img.convert("RGBA"), (TILE_PX, TILE_PX), Image.LANCZOS)
    mask = Image.new("L", (TILE_PX, TILE_PX), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, TILE_PX - 1, TILE_PX - 1], radius=TILE_RADIUS, fill=255)
    out = []
    for mode in (0, 1):
        tile = _tile_base(bg[mode])
        tile.alpha_composite(fitted)
        clipped = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
        clipped.paste(tile, (0, 0), mask)
        out.append(clipped)
    return ctk.CTkImage(light_image=out[0], dark_image=out[1], size=(44, 44))


def _video_frame(path: Path, at: float, cover: bool = False) -> Image.Image | None:
    from converter.ffmpeg_tools import _NO_WINDOW, find_tool

    ff = find_tool("ffmpeg")
    if not ff:
        return None
    args = [ff, "-v", "error"]
    if not cover:
        args += ["-ss", f"{at:.2f}"]
    args += ["-i", str(path)]
    if cover:
        args += ["-an", "-map", "0:v:0"]
    args += ["-frames:v", "1", "-vf", "scale=192:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=20, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout:
            return Image.open(io.BytesIO(r.stdout))
    except Exception:
        pass
    return None


def load_details(path: Path, kind: str | None) -> tuple[Image.Image | None, dict]:
    """Runs on a worker thread. Returns (thumbnail, details) for a file card. If the file can't be read at all,
    details["error"] says why, so the list can flag it before anyone tries to convert it."""
    details: dict = {}
    thumb = None
    try:
        if kind in ("image", "animation"):
            with Image.open(path) as im:
                details["size"] = im.size
                if kind == "animation":
                    n = getattr(im, "n_frames", 1)
                    total = 0
                    for i in range(n):
                        im.seek(i)
                        d = im.info.get("duration", 100) or 100
                        total += 100 if d <= 10 else d
                    details["frames"] = n
                    details["duration"] = total / 1000
                    im.seek(0)
                im.draft("RGB", (192, 192))
                first = ImageOps.exif_transpose(im.convert("RGBA"))
                thumb = first
        elif kind in ("video", "audio"):
            from converter.ffmpeg_tools import ConversionError, duration_of, probe

            try:
                info = probe(path)
            except ConversionError as exc:
                details["error"] = str(exc).split("\n\nTechnical detail")[0]
                return None, details
            details["duration"] = duration_of(info)
            video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
                          and not s.get("disposition", {}).get("attached_pic")), None)
            cover = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
                          and s.get("disposition", {}).get("attached_pic")), None)
            if video:
                details["size"] = (video.get("width"), video.get("height"))
                thumb = _video_frame(path, min(1.0, details["duration"] / 3))
            elif cover:
                thumb = _video_frame(path, 0, cover=True)
    except Image.DecompressionBombError:
        details["error"] = "This image is far larger than any real photo (over 500 megapixels), so it isn't opened."
    except Exception:
        if kind in ("image", "animation"):  # Pillow couldn't decode it (a video thumbnail failing is harmless)
            details["error"] = "This image is damaged, or it isn't really the type its extension says."
    return thumb, details


class DetailsLoader:
    """Loads thumbnails and media details in the background and hands them back on the UI thread."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.results: queue.Queue = queue.Queue()
        self.root.after(100, self._drain)

    def request(self, path: Path, kind: str | None, callback: Callable[[Image.Image | None, dict], None]) -> None:
        def work():
            self.results.put((callback, *load_details(path, kind)))

        self.pool.submit(work)

    def _drain(self) -> None:
        try:
            while True:
                callback, thumb, details = self.results.get_nowait()
                try:
                    callback(thumb, details)
                except tk.TclError:
                    pass  # the card was removed meanwhile
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------------- file list (virtualised)
#
# Each file is a FileItem (plain data). The list shows them with a small pool of FileRow widgets, only as many as
# fit on screen plus two, and re-binds those rows to whichever items are in view as you scroll. Adding 500 files
# therefore creates no extra widgets, and scrolling moves a dozen rows instead of re-laying out hundreds.


STATUS_STYLE = {
    # state: (label, text colour, background)
    "ready": ("Ready", T.MUTED, T.SURFACE_ALT),
    "waiting": ("Waiting", T.MUTED, T.SURFACE_ALT),
    "working": ("Converting", T.ACCENT_TEXT, T.ACCENT_SOFT),
    "done": ("Completed", T.SUCCESS, T.SUCCESS_SOFT),
    "failed": ("Failed", T.ERROR, T.ERROR_SOFT),
    "skipped": ("Skipped", T.WARNING, T.WARNING_SOFT),
    "cancelled": ("Cancelled", T.MUTED, T.SURFACE_ALT),
    "unsupported": ("Not supported", T.WARNING, T.WARNING_SOFT),
    "unreadable": ("Can't be read", T.ERROR, T.ERROR_SOFT),
}
KIND_NAMES = {"image": "Image", "animation": "Animation", "video": "Video", "audio": "Audio", None: "Unknown"}


@dataclass(eq=False)
class FileItem:
    """One file in the queue: everything the list needs to draw it, nothing tied to a widget."""

    path: Path
    kind: str | None
    size_bytes: int = 0
    state: str = "ready"
    fraction: float | None = None
    detail: str = ""
    output: Path | None = None
    out_size: int = 0
    error: str = ""
    details: dict = field(default_factory=dict)
    details_requested: bool = False
    tile: object | None = None  # CTkImage of the thumbnail, kept in a bounded cache
    flash: bool = False  # highlight once when it first comes into view

    def __post_init__(self) -> None:
        if self.kind is None:
            self.state = "unsupported"

    def meta_text(self) -> str:
        parts = [KIND_NAMES[self.kind], self.path.suffix.lstrip(".").upper() or "?", human_size(self.size_bytes)]
        d = self.details
        if d.get("size") and all(d["size"]):
            parts.append(f"{d['size'][0]}×{d['size'][1]}")
        if d.get("duration"):
            parts.append(human_duration(d["duration"]))
        if d.get("frames"):
            parts.append(f"{d['frames']} frames")
        return "  ·  ".join(parts)


class FileRow(ctk.CTkFrame):
    """A reusable row. bind(item) makes it show that item; it never stores state of its own."""

    HEIGHT = 64

    def __init__(self, parent, on_remove, on_reveal, on_error) -> None:
        super().__init__(parent, fg_color=T.SURFACE, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER,
                         height=self.HEIGHT, bg_color=T.BG)
        self.grid_propagate(False)  # fixed height: a progress bar appearing never moves anything
        self.item: FileItem | None = None
        self._shown_state: tuple | None = None
        self.on_remove, self.on_reveal, self.on_error = on_remove, on_reveal, on_error
        self.locked = False
        self._tinted = False  # True while the "just added" highlight colours are on

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure((0, 1), weight=1)
        self.thumb = ctk.CTkLabel(self, text="", width=44, height=44)
        self.thumb.grid(row=0, column=0, rowspan=2, padx=(10, 12))
        self.name = ctk.CTkLabel(self, text="", anchor="w", font=T.font(13, "semibold"), text_color=T.TEXT, height=20)
        self.name.grid(row=0, column=1, sticky="sew")
        self.meta = ctk.CTkLabel(self, text="", anchor="w", font=T.font(11), text_color=T.MUTED, height=18)
        self.meta.grid(row=1, column=1, sticky="new")
        self.bar = SmoothProgress(self, height=4)
        self.status = ctk.CTkLabel(self, text="", height=22, corner_radius=11, font=T.font(11, "semibold"))
        self.status.grid(row=0, column=2, sticky="se", padx=(8, 4))
        self.status_tip = Tooltip(self.status, "")
        self.result = ctk.CTkLabel(self, text="", anchor="e", font=T.font(11), text_color=T.MUTED, height=18)
        self.result.grid(row=1, column=2, sticky="ne", padx=(8, 4))
        for w in (self.status, self.result):
            w.bind("<Button-1>", lambda _e: self._status_click())
        self.action = ghost_button(self, icon_name="close", command=self._action, height=30)
        self.action.grid(row=0, column=3, rowspan=2, padx=(0, 8))
        self.action_tip = Tooltip(self.action, "")
        self._name_width = 0
        self.bind("<Configure>", lambda _e: self._fit_name(), add="+")
        # Very subtle hover: the border lifts a little.
        bind_tree(self, "<Enter>", lambda _e: self._hover(True))
        bind_tree(self, "<Leave>", lambda _e: self._hover(False))

    # -- binding

    def bind_item(self, item: FileItem, lock: bool) -> None:
        same = item is self.item
        self.item, self.locked = item, lock
        if not same:
            self.thumb.configure(image=item.tile or kind_tile(item.kind))
            self._name_width = 0
            self._fit_name()
            if item.flash and M.enabled():  # a newly added file: short highlight
                item.flash = False
                self._tinted = True
                self.configure(fg_color=T.ACCENT_SOFT, border_color=T.ACCENT)
                M.fade(self, 650, key="flash", fg_color=T.SURFACE, border_color=T.BORDER)
            else:
                item.flash = False
                if self._tinted or M._jobs.get((str(self), "flash")):  # repaint only if it isn't plain already
                    self._tinted = False
                    M.cancel(self, "flash")
                    self.configure(fg_color=T.SURFACE, border_color=T.BORDER)
        elif item.tile is not None:
            self.thumb.configure(image=item.tile)
        self.meta.configure(text=item.meta_text())
        self._show_state(animate=same)

    def _fit_name(self) -> None:
        """Shorten the file name to the room left beside the status badge and its result line, measured from the
        text itself, so the name never runs under them, whatever state the row is in."""
        if self.item is None:
            return
        k = scaling(self)
        row = self.winfo_width() / k
        if row <= 50:  # not laid out yet
            self.name.configure(text=self.item.path.name)
            return
        side = max(T.font(11, "semibold").measure(self.status.cget("text")) + 28,  # + the badge's rounded ends, air
                   T.font(11).measure(self.result.cget("text")))
        # thumbnail column (10 + 44 + 12), status column padding (8 + 4), action button and its padding, some air
        reserved = 66 + 12 + self.action.winfo_reqwidth() / k + 8 + 10
        avail = row - reserved - side
        key = (round(avail), self.item.path.name)
        if key != self._name_width:
            self._name_width = key
            self.name.configure(text=fit_text(self.item.path.name, T.font(13, "semibold"), avail))

    def _show_state(self, animate: bool) -> None:
        item = self.item
        key = (item.state, None if item.fraction is None else round(item.fraction, 2), item.detail, item.error,
               str(item.output), self.locked)
        if key == self._shown_state:  # the key covers everything this part shows, so nothing would change
            return
        label, fg, bg = STATUS_STYLE[item.state]
        if item.state == "working" and item.fraction is not None:
            label = f"{label} {item.fraction * 100:.0f}%"
        state_changed = self._shown_state is None or self._shown_state[0] != item.state
        if item.state == "ready":  # nothing has happened to this file yet: no badge, less clutter
            M.cancel(self.status, "colour")
            self.status.configure(text="", fg_color="transparent", cursor="")
        else:
            self.status.configure(text=f"  ●  {label}  ", cursor="hand2" if item.state in ("failed", "done") else "")
            from_nothing = self._shown_state is None or self._shown_state[0] == "ready"
            M.fade(self.status, M.NORMAL if (animate and state_changed and not from_nothing) else 0,
                   text_color=fg, fg_color=bg)
        if item.state == "working":
            if not self.bar.winfo_manager():  # placed already? (it may not be drawn yet, so not winfo_ismapped)
                self.bar.place(in_=self.meta, relx=0, rely=1.0, y=4, relwidth=1.0, anchor="nw")
                self.bar.jump(0)
            if item.fraction is not None:
                self.bar.glide(item.fraction) if animate else self.bar.jump(item.fraction)
        elif self.bar.winfo_manager():
            self.bar.place_forget()
        tips = {"failed": f"{item.error}\n\nClick for full details.", "skipped": item.detail,
                "unreadable": f"{item.error}\n\nIt will be skipped. Click for details.",
                "unsupported": "This file type can't be converted.", "done": "Click to show the file in its folder"}
        self.status_tip.set(tips.get(item.state, ""))
        if item.state == "done" and item.output:
            change = ""
            if item.size_bytes and item.out_size:
                pct = (1 - item.out_size / item.size_bytes) * 100
                change = f"  ·  {pct:.0f}% smaller" if pct >= 1 else (f"  ·  {-pct:.0f}% larger" if pct <= -1 else "")
            self.result.configure(text=f"{item.output.suffix.lstrip('.').upper()}  ·  {human_size(item.out_size)}{change}",
                                  text_color=T.SUCCESS if "smaller" in change else T.MUTED, cursor="hand2")
            self.action.configure(image=T.icon("folder_open", 15, T.MUTED), state="normal")
            self.action_tip.set("Show in folder")
        else:
            if item.state == "failed":
                first = (item.error.strip().splitlines() or ["Unknown error"])[0]
                self.result.configure(text=fit_text(first, T.font(11), 260, end=True), text_color=T.ERROR, cursor="hand2")
            elif item.state == "skipped":
                self.result.configure(text=fit_text(item.detail, T.font(11), 260, end=True), text_color=T.WARNING, cursor="")
            else:
                self.result.configure(text="", cursor="")
            self.action.configure(image=T.icon("close", 15, T.FAINT if self.locked else T.MUTED),  # dimmed while locked
                                  state="disabled" if self.locked else "normal")
            self.action_tip.set("Remove from list")
        self._shown_state = key
        self._fit_name()  # the badge and result line may have grown or shrunk

    # -- interaction

    def _hover(self, on: bool) -> None:
        if self.item is not None and not M._jobs.get((str(self), "flash")):
            M.fade(self, M.FAST, key="hover", border_color=T.BORDER_STRONG if on else T.BORDER)

    def _action(self) -> None:
        if self.item is None:
            return
        if self.item.state == "done" and self.item.output:
            self.on_reveal(self.item.output)
        elif not self.locked:
            self.on_remove(self.item)

    def _status_click(self) -> None:
        if self.item is None:
            return
        if self.item.state == "done" and self.item.output:
            self.on_reveal(self.item.output)
        elif self.item.state in ("failed", "unreadable"):
            self.on_error(self.item)


class VirtualFileList(ctk.CTkFrame):
    """A scrolling list that only creates as many rows as fit on screen, and reuses them as you scroll."""

    GAP = 8
    TILE_CACHE = 150  # thumbnails kept in memory; others are re-made if they scroll back into view

    def __init__(self, parent, on_remove, on_reveal, on_error, request_details) -> None:
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.on_remove, self.on_reveal, self.on_error = on_remove, on_reveal, on_error
        self.request_details = request_details
        self.items: list[FileItem] = []
        self.rows: list[tuple[FileRow, int]] = []
        self.locked = False
        self.top = 0.0  # pixels scrolled from the top
        self._target = 0.0
        self._gap_at: int | None = None  # index where a removal animation is closing a gap
        self._gap = 0.0
        self._tiles: list[FileItem] = []  # most recently made thumbnails, newest last

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=T.resolve(T.BG))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ctk.CTkScrollbar(self, command=self._on_scrollbar, button_color=T.BORDER_STRONG,
                                          button_hover_color=T.FAINT, fg_color=T.BG)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        # Reserve the scrollbar's column even while it's hidden, so it appearing doesn't narrow and redraw every row.
        self.grid_columnconfigure(1, minsize=self.scrollbar.winfo_reqwidth())
        self._win_state: dict[int, tuple] = {}  # last (width, state) given to each row window, to skip no-op reconfigures
        self.canvas.bind("<Configure>", lambda _e: self.layout())
        self._bind_wheel(self.canvas)

    # -- data

    def set_items(self, items: list[FileItem]) -> None:
        self.items = items
        self.layout()

    def refresh_item(self, item: FileItem) -> None:
        for row, _ in self.rows:
            if row.item is item:
                row.bind_item(item, self.locked)
                return

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        for row, _ in self.rows:
            if row.item is not None:
                row.bind_item(row.item, locked)

    def remove(self, item: FileItem) -> None:
        if item not in self.items:
            return
        idx = self.items.index(item)
        self.items.remove(item)
        if M.enabled():  # the rows below glide up into the freed space
            row_h = self._row_h()
            self._gap_at, self._gap = idx, row_h

            def step(t: float) -> None:
                self._gap = row_h * (1 - t)
                self.layout()

            M.animate(self, "gap", M.NORMAL, step, done=self._end_gap, ease=M.ease_in_out)
        else:
            self.layout()

    def _end_gap(self) -> None:
        self._gap_at, self._gap = None, 0.0
        self.layout()

    def remember_tile(self, item: FileItem) -> None:
        """Keep a bounded number of thumbnails in memory."""
        if item in self._tiles:
            self._tiles.remove(item)
        self._tiles.append(item)
        while len(self._tiles) > self.TILE_CACHE:
            old = self._tiles.pop(0)
            if not any(row.item is old for row, _ in self.rows):
                old.tile, old.details_requested = None, False

    # -- layout

    def _row_h(self) -> float:
        return (FileRow.HEIGHT + self.GAP) * scaling(self)

    def _max_top(self) -> float:
        return max(0.0, len(self.items) * self._row_h() - self.canvas.winfo_height())

    def layout(self) -> None:
        height, width = self.canvas.winfo_height(), self.canvas.winfo_width()
        if height < 10 or width < 10:
            return
        row_h = self._row_h()
        self.top = min(max(self.top, 0.0), self._max_top())
        need = int(height // row_h) + 3
        while len(self.rows) < need:
            row = FileRow(self.canvas, self.on_remove, self.on_reveal, self.on_error)
            self._bind_wheel(row)
            win = self.canvas.create_window(0, 0, window=row, anchor="nw", state="hidden")
            self.rows.append((row, win))
        first = int(self.top // row_h)
        pad = int(6 * scaling(self))
        n = len(self.rows)
        for k in range(n):
            # File number idx always uses row idx % n, so scrolling past one row re-uses just one row, not all of them.
            idx = first + k
            row, win = self.rows[idx % n]
            if idx < len(self.items):
                item = self.items[idx]
                y = idx * row_h - self.top + (self._gap if self._gap_at is not None and idx >= self._gap_at else 0)
                self.canvas.coords(win, 0, round(y))
                self._configure_win(win, width - pad, "normal")
                if row.item is not item:
                    row.bind_item(item, self.locked)
                    if item.kind and not item.details_requested:
                        item.details_requested = True
                        self.request_details(item)
            else:
                self._configure_win(win, None, "hidden")
                row.item = None
        total = len(self.items) * row_h
        if total <= height:
            self.scrollbar.grid_remove()
        else:
            self.scrollbar.grid()
            self.scrollbar.set(self.top / total, min(1.0, (self.top + height) / total))

    def _configure_win(self, win: int, width: int | None, state: str) -> None:
        old_width, old_state = self._win_state.get(win, (None, None))
        width = old_width if width is None else width
        if (width, state) != (old_width, old_state):
            self._win_state[win] = (width, state)
            self.canvas.itemconfigure(win, **({"width": width} if width else {}), state=state)

    # -- scrolling (eased, so a wheel notch glides instead of jumping)

    def _scroll_to(self, target: float, animate: bool = True) -> None:
        self._target = min(max(target, 0.0), self._max_top())
        if not animate or not M.enabled():
            self.top = self._target
            self.layout()
            return
        start = self.top

        def step(t: float) -> None:
            self.top = start + (self._target - start) * t
            self.layout()

        M.animate(self, "scroll", M.FAST, step)

    def _on_scrollbar(self, *args) -> None:
        total = len(self.items) * self._row_h()
        if args and args[0] == "moveto":
            self._scroll_to(float(args[1]) * total, animate=False)
        elif args and args[0] == "scroll":
            amount = int(args[1]) * (self._row_h() if args[2] == "units" else self.canvas.winfo_height())
            self._scroll_to(self._target + amount)

    def _bind_wheel(self, widget) -> None:
        def wheel(event) -> str:
            if sys.platform == "darwin":
                delta = -event.delta * 6
            elif getattr(event, "num", None) in (4, 5):
                delta = -120 if event.num == 4 else 120
            else:
                delta = -event.delta
            base = self._target if M._jobs.get((str(self), "scroll")) else self.top
            self._scroll_to(base + delta / 120 * self._row_h() * 1.5)
            return "break"

        for w in (widget, *widget.winfo_children()):
            w.bind("<MouseWheel>", wheel, add="+")
            w.bind("<Button-4>", wheel, add="+")
            w.bind("<Button-5>", wheel, add="+")
            for child in w.winfo_children():
                child.bind("<MouseWheel>", wheel, add="+")

    def _set_appearance_mode(self, mode_string) -> None:
        super()._set_appearance_mode(mode_string)
        self.canvas.configure(bg=T.resolve(T.BG))
