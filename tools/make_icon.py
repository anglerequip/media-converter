"""Generates every app icon from one geometry definition. Run after changing the design; the results are committed.

    python tools/make_icon.py

Outputs (assets/):
    icon.svg       editable vector master
    icon-1024.png  1024 px raster master
    icon.png       512 px, used inside the app and in the README
    icon.ico       Windows: 16, 20, 24, 32, 40, 48, 64, 72, 96, 128 and 256 px
    icon.icns      macOS: 16 to 1024 px, with Apple's transparent margin

Design: a midnight-graphite tile; a luminous-blue file card behind a white document with a folded corner (one file
becoming another); on the document a blue play mark (media), framed by two conversion arrows at larger sizes.
Every raster size is drawn separately at 4x and scaled down, and details that would smear (the fold, the arrows,
the shadow) are left out at the sizes where they can't be read. At 16 px only the two cards and the play mark remain.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 72, 96, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
SS = 4  # supersampling

# ----------------------------------------------------------------------------- the design, in units of the canvas (0..1)

TILE_TOP, TILE_BOTTOM = (28, 42, 66), (13, 21, 36)        # midnight graphite blue
TILE_EDGE = (52, 72, 104)                                   # fine lighter edge so the tile holds on dark taskbars
BLUE_TOP, BLUE_BOTTOM = (90, 170, 246), (47, 123, 216)      # luminous blue card / accent
WHITE = (246, 249, 253)
FOLD = (196, 212, 233)
ACCENT = (57, 135, 215)                                      # #3987D7

TILE_INSET, TILE_RADIUS = 0.035, 0.225
CARD_W, CARD_H, CARD_R = 0.40, 0.50, 0.065
FRONT = (0.215, 0.33)                                        # top-left of the white document
BACK = (FRONT[0] + 0.165, FRONT[1] - 0.125)                  # blue card, up and to the right
FOLD_SIZE = 0.115


def _front_center() -> tuple[float, float]:
    return FRONT[0] + CARD_W / 2, FRONT[1] + CARD_H / 2 + 0.03


def _details(size: int) -> dict:
    """Which details appear at a given pixel size."""
    return {
        "fold": size >= 32,
        "arrows": size >= 64,
        "shadow": size >= 48,
        "edge": True,
        "play_scale": 1.45 if size <= 24 else 1.2 if size < 64 else 1.0,
    }


# ----------------------------------------------------------------------------- raster rendering


def _gradient(w: int, h: int, top, bottom, box) -> Image.Image:
    """Vertical gradient filling `box` (x0, y0, x1, y1) on a w x h transparent image."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    span = max(1, y1 - y0)
    for y in range(int(y0), int(math.ceil(y1)) + 1):
        t = min(1.0, max(0.0, (y - y0) / span))
        d.line([(x0, y), (x1, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return img


def _rounded_mask(w: int, h: int, box, radius: float, cut_corner: float = 0.0) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle(box, radius=radius, fill=255)
    if cut_corner:
        x0, y0, x1, y1 = box
        d.polygon([(x1 - cut_corner, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y0 + cut_corner)], fill=0)
    return mask


def draw(size: int) -> Image.Image:
    s = size * SS
    u = lambda v: v * s  # noqa: E731  (units -> pixels)
    det = _details(size)
    # At taskbar sizes the symbol is enlarged around the tile's centre so it doesn't look lost in the tile.
    k = 1.12 if size <= 24 else 1.0
    g = lambda v, c=0.5: c + (v - c) * k  # noqa: E731  (symbol geometry, scaled about the centre)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Tile with a fine lighter edge.
    inset = u(TILE_INSET if size > 24 else 0.02)
    tile_box = (inset, inset, s - inset, s - inset)
    tile_mask = _rounded_mask(s, s, tile_box, u(TILE_RADIUS))
    img.paste(_gradient(s, s, TILE_TOP, TILE_BOTTOM, tile_box), (0, 0), tile_mask)
    edge = max(SS, u(0.012))
    inner = _rounded_mask(s, s, (tile_box[0] + edge, tile_box[1] + edge, tile_box[2] - edge, tile_box[3] - edge),
                          u(TILE_RADIUS) - edge)
    ring = ImageChops.subtract(tile_mask, inner)
    img.paste(Image.new("RGBA", (s, s), TILE_EDGE + (255,)), (0, 0), ring)

    # Blue card behind.
    cw, ch = u(CARD_W * k), u(CARD_H * k)
    bx, by = u(g(BACK[0])), u(g(BACK[1], 0.51))
    back_box = (bx, by, bx + cw, by + ch)
    img.paste(_gradient(s, s, BLUE_TOP, BLUE_BOTTOM, back_box), (0, 0), _rounded_mask(s, s, back_box, u(CARD_R)))

    # White document in front, with a soft shadow falling on the blue card.
    fx, fy = u(g(FRONT[0])), u(g(FRONT[1], 0.51))
    front_box = (fx, fy, fx + cw, fy + ch)
    fold = u(FOLD_SIZE) if det["fold"] else 0
    front_mask = _rounded_mask(s, s, front_box, u(CARD_R), cut_corner=fold)
    if det["shadow"]:
        shadow = front_mask.filter(ImageFilter.GaussianBlur(u(0.022)))
        shadow = ImageChops.offset(shadow, 0, int(u(0.012)))
        img.paste(Image.new("RGBA", (s, s), (5, 10, 20, 255)), (0, 0), shadow.point(lambda v: int(v * 0.45)))
    img.paste(Image.new("RGBA", (s, s), WHITE + (255,)), (0, 0), front_mask)
    if fold:
        d = ImageDraw.Draw(img)
        x1, y0 = front_box[2], front_box[1]
        d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold * 0.82), (x1 - fold * 0.18, y0 + fold)], fill=FOLD + (255,))

    d = ImageDraw.Draw(img)
    fcx, fcy = _front_center()
    cx, cy = u(g(fcx)), u(g(fcy, 0.51))

    # Conversion arrows around the play mark.
    if det["arrows"]:
        r, w = u(0.105), u(0.026)
        head_len, head_half = w * 1.55, w * 1.35

        def arc(start: float, end: float) -> None:
            outer = r + w / 2
            d.arc([cx - outer, cy - outer, cx + outer, cy + outer], start=start, end=end, fill=ACCENT + (255,), width=round(w))
            a = math.radians(end)
            px, py = cx + r * math.cos(a), cy + r * math.sin(a)
            tx, ty, nx, ny = -math.sin(a), math.cos(a), math.cos(a), math.sin(a)
            d.polygon([(px + tx * head_len, py + ty * head_len), (px + nx * head_half, py + ny * head_half),
                       (px - nx * head_half, py - ny * head_half)], fill=ACCENT + (255,))

        arc(200, 318)
        arc(20, 138)

    # Play mark.
    h = u(0.105) * det["play_scale"] * k
    w_ = h * 0.92
    x0 = cx - w_ * 0.38
    d.polygon([(x0, cy - h / 2), (x0, cy + h / 2), (x0 + w_, cy)], fill=ACCENT + (255,))

    out = img.resize((size, size), Image.LANCZOS)
    if size <= 32:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.5, percent=60, threshold=0))
    return out


def draw_mac(size: int) -> Image.Image:
    """macOS icons sit inside a transparent margin (about 10% per side in Apple's template) with a soft shadow."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body = draw(max(16, round(size * 0.86)))
    pos = ((size - body.width) // 2, (size - body.height) // 2 - max(0, round(size * 0.008)))
    if size >= 64:
        alpha = Image.new("L", (size, size), 0)
        alpha.paste(body.getchannel("A"), pos)
        shadow = ImageChops.offset(alpha.filter(ImageFilter.GaussianBlur(size * 0.018)), 0, round(size * 0.012))
        canvas.paste(Image.new("RGBA", (size, size), (0, 0, 0, 255)), (0, 0), shadow.point(lambda v: int(v * 0.35)))
    canvas.alpha_composite(body, pos)
    return canvas


# ----------------------------------------------------------------------------- vector master


def svg() -> str:
    S = 1024
    u = lambda v: round(v * S, 2)  # noqa: E731
    hexc = lambda c: "#%02X%02X%02X" % c  # noqa: E731
    inset, r = u(TILE_INSET), u(TILE_RADIUS)
    fx, fy, bx, by = u(FRONT[0]), u(FRONT[1]), u(BACK[0]), u(BACK[1])
    cw, ch, cr, fold = u(CARD_W), u(CARD_H), u(CARD_R), u(FOLD_SIZE)
    cx, cy = (u(c) for c in _front_center())
    x1 = fx + cw
    # White document: rounded rectangle with the top-right corner cut for the fold.
    doc = (f"M{fx + cr},{fy} H{x1 - fold} L{x1},{fy + fold} V{fy + ch - cr} "
           f"A{cr},{cr} 0 0 1 {x1 - cr},{fy + ch} H{fx + cr} A{cr},{cr} 0 0 1 {fx},{fy + ch - cr} "
           f"V{fy + cr} A{cr},{cr} 0 0 1 {fx + cr},{fy} Z")
    fold_path = f"M{x1 - fold},{fy} V{fy + fold * 0.82} L{x1 - fold * 0.18},{fy + fold} Z"
    ar, aw = u(0.105), u(0.026)

    def arc_path(start: float, end: float) -> str:
        a0, a1 = math.radians(start), math.radians(end)
        p0 = (cx + ar * math.cos(a0), cy + ar * math.sin(a0))
        p1 = (cx + ar * math.cos(a1), cy + ar * math.sin(a1))
        hl, hh = aw * 1.55, aw * 1.35
        tx, ty, nx, ny = -math.sin(a1), math.cos(a1), math.cos(a1), math.sin(a1)
        head = [(p1[0] + tx * hl, p1[1] + ty * hl), (p1[0] + nx * hh, p1[1] + ny * hh), (p1[0] - nx * hh, p1[1] - ny * hh)]
        return (f'<path d="M{p0[0]:.2f},{p0[1]:.2f} A{ar},{ar} 0 0 1 {p1[0]:.2f},{p1[1]:.2f}" fill="none" '
                f'stroke="{hexc(ACCENT)}" stroke-width="{aw}"/>\n  <path d="M' +
                " L".join(f"{x:.2f},{y:.2f}" for x, y in head) + f' Z" fill="{hexc(ACCENT)}"/>')

    ph = u(0.105)
    pw = ph * 0.92
    px0 = cx - pw * 0.38
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 {S} {S}">
  <!-- Media Converter app icon: vector master. Generated by tools/make_icon.py; edit the geometry there. -->
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{hexc(TILE_TOP)}"/><stop offset="1" stop-color="{hexc(TILE_BOTTOM)}"/>
    </linearGradient>
    <linearGradient id="blue" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{hexc(BLUE_TOP)}"/><stop offset="1" stop-color="{hexc(BLUE_BOTTOM)}"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="{u(0.022) / 2:.1f}"/><feOffset dy="{u(0.012)}"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.45"/></feComponentTransfer>
    </filter>
  </defs>
  <rect x="{inset}" y="{inset}" width="{S - 2 * inset}" height="{S - 2 * inset}" rx="{r}" fill="url(#tile)"
        stroke="{hexc(TILE_EDGE)}" stroke-width="{u(0.012)}"/>
  <rect x="{bx}" y="{by}" width="{cw}" height="{ch}" rx="{cr}" fill="url(#blue)"/>
  <path d="{doc}" fill="#050A14" filter="url(#shadow)"/>
  <path d="{doc}" fill="{hexc(WHITE)}"/>
  <path d="{fold_path}" fill="{hexc(FOLD)}"/>
  {arc_path(200, 318)}
  {arc_path(20, 138)}
  <path d="M{px0:.2f},{cy - ph / 2:.2f} L{px0:.2f},{cy + ph / 2:.2f} L{px0 + pw:.2f},{cy:.2f} Z" fill="{hexc(ACCENT)}"/>
</svg>
'''


if __name__ == "__main__":
    assets = ROOT / "assets"
    frames = {size: draw(size) for size in ICO_SIZES}
    frames[256].save(assets / "icon.ico", sizes=[(s, s) for s in ICO_SIZES],
                     append_images=[frames[s] for s in ICO_SIZES if s != 256])
    draw(1024).save(assets / "icon-1024.png")
    draw(512).save(assets / "icon.png")
    mac = {size: draw_mac(size) for size in ICNS_SIZES}
    mac[1024].save(assets / "icon.icns", append_images=[mac[s] for s in ICNS_SIZES if s != 1024])
    (assets / "icon.svg").write_text(svg(), encoding="utf-8")
    print("wrote assets/icon.svg, icon-1024.png, icon.png, icon.ico (" + ", ".join(map(str, ICO_SIZES)) + " px), icon.icns")
