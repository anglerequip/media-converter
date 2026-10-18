"""Draws assets/icon.ico and assets/icon.png. Run once after changing the design; the results are committed.

Every size in the .ico is drawn separately at 8x and scaled down, rather than shrinking one big picture:
small icons get thicker strokes and bigger arrowheads and drop the play triangle, so they stay sharp in
the taskbar, Explorer and on the Desktop at every Windows display scale.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 72, 96, 128, 256]
TOP, BOTTOM = (47, 123, 232), (109, 62, 234)  # blue to violet
SS = 8  # supersampling factor


def draw(size: int) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Rounded square with a vertical gradient; almost full bleed, like Windows 11 app icons.
    margin = round(s * (0.02 if size <= 24 else 0.04))
    radius = round(s * 0.22)
    gradient = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, s - margin - 1, s - margin - 1], radius=radius, fill=255)
    img.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = cy = s / 2
    # Two circular arrows. Lines get relatively thicker as the icon gets smaller so they never blur away.
    stroke = s * (0.13 if size <= 20 else 0.11 if size <= 24 else 0.095 if size <= 32 else 0.08 if size <= 48 else 0.07)
    r_mid = s * 0.29  # radius of the middle of the line
    head_len = stroke * (1.55 if size <= 32 else 1.45)
    head_half = stroke * (1.25 if size <= 32 else 1.15)

    def arc_with_head(start: float, end: float) -> None:
        outer = r_mid + stroke / 2
        d.arc([cx - outer, cy - outer, cx + outer, cy + outer], start=start, end=end, fill=white, width=round(stroke))
        a = math.radians(end)
        bx, by = cx + r_mid * math.cos(a), cy + r_mid * math.sin(a)  # end of the line, centre of the arrow's base
        tx, ty = -math.sin(a), math.cos(a)  # direction of travel (clockwise on screen)
        nx, ny = math.cos(a), math.sin(a)  # pointing away from the centre
        d.polygon([(bx + tx * head_len, by + ty * head_len),
                   (bx + nx * head_half, by + ny * head_half),
                   (bx - nx * head_half, by - ny * head_half)], fill=white)

    arc_with_head(200, 322)  # top arrow, heading clockwise towards the right
    arc_with_head(20, 142)  # bottom arrow, heading clockwise towards the left

    if size >= 24:  # "play" in the middle means media. Too small to read below 24 px.
        h = s * (0.19 if size <= 32 else 0.17)
        w = h * 0.88
        x0 = cx - w * 0.36  # a triangle looks centred when its body sits slightly right of centre
        d.polygon([(x0, cy - h / 2), (x0, cy + h / 2), (x0 + w, cy)], fill=white)

    out = img.resize((size, size), Image.LANCZOS)
    if size <= 32:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=70, threshold=0))
    return out


if __name__ == "__main__":
    frames = {size: draw(size) for size in ICO_SIZES}
    big = frames[256]
    big.save(ROOT / "assets" / "icon.ico", sizes=[(s, s) for s in ICO_SIZES],
             append_images=[frames[s] for s in ICO_SIZES if s != 256])
    draw(512).save(ROOT / "assets" / "icon.png")
    print("wrote assets/icon.ico (" + ", ".join(map(str, ICO_SIZES)) + " px) and assets/icon.png")
