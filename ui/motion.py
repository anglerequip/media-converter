"""Small, restrained animations: easing, colour fades, expand/collapse and slides.

Everything runs on Tk's event loop with `after`, timed by the clock rather than by frame count, so a busy
moment shortens an animation instead of dragging it out. When animations are off (the in-app switch, or
Windows' "Animation effects" setting), every helper jumps straight to the end state.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from typing import Callable

import customtkinter as ctk

FRAME_MS = 15
FAST, NORMAL, SLOW = 120, 180, 260

_enabled = True
_jobs: dict[tuple[str, str], str] = {}


# ----------------------------------------------------------------------------- switches


def system_allows_animation() -> bool:
    """Windows' Settings > Accessibility > Visual effects > Animation effects, or macOS's Accessibility > Display >
    Reduce motion."""
    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(["/usr/bin/defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            return out != "1"
        except Exception:
            return True
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


def set_enabled(flag: bool) -> None:
    global _enabled
    _enabled = bool(flag)


def enabled() -> bool:
    return _enabled


# ----------------------------------------------------------------------------- core


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def cancel(widget, key: str) -> None:
    job = _jobs.pop((str(widget), key), None)
    if job:
        try:
            widget.after_cancel(job)
        except tk.TclError:
            pass


def animate(widget, key: str, duration: int, step: Callable[[float], None],
            done: Callable[[], None] | None = None, ease: Callable[[float], float] = ease_out) -> None:
    """Call step(progress) with eased progress from 0 to 1 over `duration` ms. A new animation with the
    same widget and key replaces the running one."""
    cancel(widget, key)
    ident = (str(widget), key)
    if not _enabled or duration <= 0:
        step(1.0)
        if done:
            done()
        return
    start = time.perf_counter()

    def tick() -> None:
        try:
            if not widget.winfo_exists():
                _jobs.pop(ident, None)
                return
            t = min(1.0, (time.perf_counter() - start) * 1000 / duration)
            step(ease(t))
            if t < 1.0:
                _jobs[ident] = widget.after(FRAME_MS, tick)
            else:
                _jobs.pop(ident, None)
                if done:
                    done()
        except tk.TclError:  # the widget went away mid-animation
            _jobs.pop(ident, None)

    tick()


# ----------------------------------------------------------------------------- colour


def _rgb(widget, colour: str) -> tuple[int, int, int]:
    r, g, b = widget.winfo_rgb(colour)
    return r // 257, g // 257, b // 257


def _pair(widget, colour, fallback) -> tuple[str, str]:
    if colour == "transparent" or colour is None:
        colour = fallback
    if colour == "transparent" or colour is None:
        colour = ("#FFFFFF", "#000000")
    if isinstance(colour, str):
        return colour, colour
    return tuple(colour)  # type: ignore[return-value]


def _mix(widget, a: str, b: str, t: float) -> str:
    ra, ga, ba = _rgb(widget, a)
    rb, gb, bb = _rgb(widget, b)
    return "#%02X%02X%02X" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def fade(widget, duration: int = NORMAL, key: str = "colour", **targets) -> None:
    """Fade colour options (fg_color, text_color, border_color, progress_color…) to new values.
    Both halves of a (light, dark) pair are blended, so a theme switch mid-fade still looks right."""
    behind = getattr(widget, "_bg_color", None)
    starts, ends = {}, {}
    try:
        for prop, target in targets.items():
            s = _pair(widget, widget.cget(prop), behind)
            e = _pair(widget, target, behind)
            if tuple(_rgb(widget, c) for c in s) != tuple(_rgb(widget, c) for c in e):
                starts[prop], ends[prop] = s, e
    except tk.TclError:
        widget.configure(**targets)
        return
    if not starts:
        widget.configure(**targets)  # nothing visible changes; still store the exact values (e.g. "transparent")
        return

    def step(t: float) -> None:
        if t >= 1.0:
            widget.configure(**targets)
            return
        widget.configure(**{p: (_mix(widget, starts[p][0], ends[p][0], t), _mix(widget, starts[p][1], ends[p][1], t))
                            for p in starts})

    animate(widget, key, duration, step)


# ----------------------------------------------------------------------------- smooth hover for buttons

_COLOUR_KEYS = ("fg_color", "text_color", "border_color")


def smooth_hover(button: ctk.CTkButton, hover_colour) -> None:
    """Replace CTk's instant hover with a short fade. While hovered, the pointer is watched so that moving
    onto the button's icon or text never counts as leaving it."""
    button.configure(hover=False)
    button._mc_base = button.cget("fg_color")
    button._mc_hover = hover_colour
    button._mc_hovering = False

    def inside() -> bool:
        try:
            x, y = button.winfo_pointerxy()
            w = button.winfo_containing(x, y)
            return w is not None and str(w).startswith(str(button))
        except (tk.TclError, KeyError):
            return False

    def watch() -> None:
        if not button._mc_hovering:
            return
        if inside():
            button.after(80, watch)
        else:
            button._mc_hovering = False
            fade(button, NORMAL, key="hover", fg_color=button._mc_base)

    def enter(_e=None) -> None:
        if button._mc_hovering or str(button.cget("state")) == "disabled":
            return
        button._mc_hovering = True
        cancel(button, "colour")
        fade(button, FAST, key="hover", fg_color=button._mc_hover)
        button.after(80, watch)

    for w in (button, *button.winfo_children()):
        w.bind("<Enter>", enter, add="+")


def paint(button: ctk.CTkButton, duration: int = NORMAL, **props) -> None:
    """Restyle a button with a fade. Keeps smooth_hover's resting and hover colours in sync, so a later
    mouse-out returns to the new colour rather than an old one."""
    if "hover_color" in props:
        hover = props.pop("hover_color")
        if hasattr(button, "_mc_hover"):
            button._mc_hover = hover
        else:
            button.configure(hover_color=hover)
    if "fg_color" in props and hasattr(button, "_mc_base"):
        button._mc_base = props["fg_color"]
        if getattr(button, "_mc_hovering", False):
            props["fg_color"] = button._mc_hover
    colours = {k: v for k, v in props.items() if k in _COLOUR_KEYS}
    others = {k: v for k, v in props.items() if k not in _COLOUR_KEYS}
    if others:
        button.configure(**others)
    if colours:
        cancel(button, "hover")
        fade(button, duration, **colours)


# ----------------------------------------------------------------------------- size


def _scaling(widget) -> float:
    try:
        return ctk.ScalingTracker.get_widget_scaling(widget)
    except Exception:
        return 1.0


def natural_height(frame) -> int:
    """Height in pixels the frame wants for its contents. Stops any running height animation first."""
    cancel(frame, "height")
    frame.grid_propagate(True)
    frame.update_idletasks()
    return max(1, frame.winfo_reqheight())


def collapse_now(frame) -> None:
    """Shrink a frame to 1 px before it is shown, ready for expand()."""
    frame.grid_propagate(False)
    frame.configure(height=1)


def expand(frame, target_px: int | None = None, duration: int = SLOW, done=None) -> None:
    """Grow a frame from its current height to its natural height, then let it size itself again."""
    target = target_px or natural_height(frame)
    s = _scaling(frame)
    frame.grid_propagate(False)
    start = max(1, frame.winfo_height())

    def step(t: float) -> None:
        frame.configure(height=max(1, (start + (target - start) * t) / s))

    def finish() -> None:
        try:
            frame.grid_propagate(True)
        except tk.TclError:
            return
        if done:
            done()

    animate(frame, "height", duration, step, finish)


def collapse(frame, duration: int = NORMAL, done=None) -> None:
    """Shrink a frame to nothing, then call done (typically grid_remove or destroy)."""
    s = _scaling(frame)
    start = max(1, frame.winfo_height())
    frame.grid_propagate(False)

    def step(t: float) -> None:
        frame.configure(height=max(1, start * (1 - t) / s))

    def finish() -> None:
        if done:
            done()
        try:
            if frame.winfo_exists():
                frame.grid_propagate(True)
        except tk.TclError:
            pass

    animate(frame, "height", duration, step, finish, ease=ease_in_out)


# ----------------------------------------------------------------------------- position and opacity


def slide_in(widget, dx: int = 28, duration: int = SLOW, **place_kw) -> None:
    """Place a widget a little to one side and glide it into position."""
    def step(t: float) -> None:
        widget.place(x=round(dx * (1 - t)), **place_kw)

    animate(widget, "slide", duration, step)


def window_fade_in(window, duration: int = 220) -> None:
    try:
        window.attributes("-alpha", 0.0)
    except tk.TclError:
        return

    def step(t: float) -> None:
        window.attributes("-alpha", t)

    # Start once the window has finished building and drawing, so the fade isn't starved of frames.
    window.after_idle(lambda: window.after(60, lambda: animate(window, "alpha", duration, step)))
