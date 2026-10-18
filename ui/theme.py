"""Design tokens: colours, type, radii and icons. Every colour is a (light, dark) pair."""

from __future__ import annotations

import os
import sys
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------- colour

BG = ("#F3F4F6", "#17181B")
SURFACE = ("#FFFFFF", "#212327")
SURFACE_ALT = ("#F1F3F6", "#2A2C31")
SURFACE_HOVER = ("#E7EAEE", "#33363C")
BORDER = ("#E0E3E8", "#32353B")
BORDER_STRONG = ("#C6CBD3", "#4A4E57")
TEXT = ("#1D2127", "#ECEEF1")
MUTED = ("#5E6570", "#A1A7B0")
FAINT = ("#878D96", "#7B818A")

ACCENT = ("#3987D7", "#3987D7")
ACCENT_HOVER = ("#2E75BF", "#4C97E3")
ACCENT_SOFT = ("#EAF2FC", "#1C2A3A")
ACCENT_TEXT = ("#2B6CB0", "#78B1EC")  # accent used as text: darker in light mode for contrast
ON_ACCENT = ("#FFFFFF", "#FFFFFF")
ACCENT_DISABLED = ("#A8C7E9", "#2A3D52")
FOCUS = ("#3987D7", "#78B1EC")

SUCCESS = ("#1E7F45", "#4CC07A")
SUCCESS_SOFT = ("#E6F4EB", "#1B3024")
WARNING = ("#A35F08", "#E3A84A")
WARNING_SOFT = ("#FCF2E1", "#382D1B")
ERROR = ("#BF3A3A", "#F07A7A")
ERROR_SOFT = ("#FBECEC", "#3A2324")

# ----------------------------------------------------------------------------- shape

RADIUS = 10
RADIUS_SMALL = 8
RADIUS_LARGE = 14
PAD = 16

# ----------------------------------------------------------------------------- type

_FONT_CACHE: dict = {}


def font(size: int = 12, weight: str = "normal") -> ctk.CTkFont:
    """Segoe UI in three weights. "semibold" uses the real Semibold face rather than faux bold."""
    key = (size, weight)
    if key not in _FONT_CACHE:
        if weight == "semibold":
            _FONT_CACHE[key] = ctk.CTkFont(family="Segoe UI Semibold", size=size)
        else:
            _FONT_CACHE[key] = ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if weight == "bold" else "normal")
    return _FONT_CACHE[key]


# ----------------------------------------------------------------------------- icons
# Glyphs from Windows' own icon font (Segoe Fluent Icons on Windows 11, MDL2 Assets on Windows 10),
# rendered to images so they can sit inside buttons next to text and follow the theme.

GLYPHS = {
    "upload": "\ue898", "download": "\ue896", "folder": "\ue8b7", "folder_open": "\ue838",
    "settings": "\ue713", "close": "\ue711", "delete": "\ue74d", "photo": "\ue91b", "video": "\ue714",
    "audio": "\ue8d6", "check": "\ue73e", "check_circle": "\ue930", "error": "\ue783",
    "error_circle": "\uea39", "warning": "\ue7ba", "link": "\ue71b", "paste": "\ue77f",
    "refresh": "\ue72c", "play": "\ue768", "chevron_down": "\ue70d", "chevron_up": "\ue70e",
    "info": "\ue946", "gif": "\uf4a9", "sun": "\ue706", "moon": "\ue708", "pc": "\ue7f8",
    "convert": "\ue8ab", "add": "\ue710", "stop": "\ue71a", "doc": "\ue8a5", "skip": "\ue893",
}

_ICON_FONTS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "SegoeIcons.ttf"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts", "SegoeIcons.ttf"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segmdl2.ttf"),
]


@lru_cache(maxsize=None)
def _icon_font_path() -> str | None:
    for path in _ICON_FONTS:
        if os.path.isfile(path):
            return path
    return None


def _render_glyph(char: str, px: int, colour: str) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    path = _icon_font_path()
    draw = ImageDraw.Draw(img)
    if path:
        f = ImageFont.truetype(path, int(px * 0.92))
        box = draw.textbbox((0, 0), char, font=f)
        x = (px - (box[2] - box[0])) / 2 - box[0]
        y = (px - (box[3] - box[1])) / 2 - box[1]
        draw.text((x, y), char, font=f, fill=colour)
    else:  # no icon font (not Windows): a simple dot keeps layouts intact
        r = px // 6
        draw.ellipse([px / 2 - r, px / 2 - r, px / 2 + r, px / 2 + r], fill=colour)
    return img


@lru_cache(maxsize=None)
def icon(name: str, size: int = 16, colour: tuple[str, str] = TEXT) -> ctk.CTkImage:
    """A theme-aware icon image. Rendered at 3x and scaled down so it stays crisp at any DPI."""
    char = GLYPHS[name]
    light = _render_glyph(char, size * 3, colour[0])
    dark = _render_glyph(char, size * 3, colour[1])
    return ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))


@lru_cache(maxsize=None)
def blank(size: int = 16) -> ctk.CTkImage:
    """A transparent image. CTk ignores image=None on labels, so this is how an icon is cleared."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def resolve(colour: tuple[str, str] | str) -> str:
    """Pick the light or dark half of a colour pair for the current appearance."""
    if isinstance(colour, str):
        return colour
    return colour[1] if ctk.get_appearance_mode() == "Dark" else colour[0]


def is_windows() -> bool:
    return sys.platform == "win32"
