"""Draws assets/icon.ico (and a PNG copy for the README). Run once; the result is committed."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Rounded square with a vertical blue-to-violet gradient.
    grad = Image.new("RGBA", (S, S))
    top, bottom = (37, 99, 235), (124, 58, 237)
    for y in range(S):
        t = y / (S - 1)
        grad.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,), (0, y, S, y + 1))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    w = 70
    # Two arrows chasing each other: "convert".
    d.arc([230, 230, 794, 794], start=200, end=340, fill=white, width=w)
    d.arc([230, 230, 794, 794], start=20, end=160, fill=white, width=w)
    d.polygon([(760, 300), (850, 470), (650, 450)], fill=white)
    d.polygon([(264, 724), (174, 554), (374, 574)], fill=white)
    # A small "play" triangle in the middle for media.
    d.polygon([(450, 400), (450, 624), (630, 512)], fill=white)
    return img


if __name__ == "__main__":
    icon = draw()
    (ROOT / "assets").mkdir(exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "icon.png")
    icon.save(ROOT / "assets" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote assets/icon.ico and assets/icon.png")
