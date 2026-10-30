"""Kong's mark and small glyphs, drawn with Pillow so the app ships no image files.

The mark is the key visual in miniature: four slanted pillars standing on a line, short,
tall, tallest, medium, on a graphite tile. The second one is lit: an orange core in an
aluminium frame (just orange at tray sizes). What is lit carries the state.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import theme

ORANGE = theme.rgb(theme.ORANGE)
ALU = theme.rgb(theme.ALU)
TILE_TOP = (40, 45, 47)
TILE_BOTTOM = (16, 19, 20)
STEEL = (112, 120, 123)  # an unlit pillar, light enough to read at 16 px
DIM = (70, 76, 78)

LIT = 1  # which pillar glows, as in the key visual
# (unlit pillars, the lit one, tile brightness) per state
STATES = {
    "idle": (STEEL, ORANGE, 1.0),
    "recording": (ORANGE, ORANGE, 1.0),  # everything lit
    "busy": (STEEL, ALU, 1.0),  # the glass is dark while it thinks
    "loading": (DIM, DIM, 0.8),
    "error": (DIM, DIM, 0.8),
}
HEIGHTS = (18, 36, 46, 26)  # out of 64
SLANT = 5  # how far the top of a pillar leans right, out of 64


def _tile(n: int, k: float) -> Image.Image:
    rows = np.linspace(0.0, 1.0, n)[:, None, None]
    grad = (np.array(TILE_TOP) * (1 - rows) + np.array(TILE_BOTTOM) * rows) * k
    return Image.fromarray(np.repeat(grad.astype(np.uint8), n, axis=1), "RGB").convert("RGBA")


def kong_icon(state: str = "idle", size: int = 64) -> Image.Image:
    ss = 8 if size < 64 else 4  # draw big and scale down: crisp edges at every size
    n = size * ss
    u = n / 64
    plain, lit, k = STATES.get(state, STATES["idle"])
    img = _tile(n, k)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=14 * u, fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    base = 50 * u
    scale = 38 / max(HEIGHTS)
    for i, h in enumerate(HEIGHTS):
        h = h * scale * u
        x = (12 + i * 11) * u
        lean = SLANT * u * h / (max(HEIGHTS) * scale * u)
        w = 7.5 * u
        poly = [(x, base), (x + w, base), (x + w + lean, base - h), (x + lean, base - h)]
        if i == LIT and size >= 48 and lit == ORANGE:
            # Big enough to show the aluminium frame around the glowing glass.
            d.polygon(poly, fill=ALU + (255,))
            f, t = 1.6 * u, 2.4 * u
            d.polygon([(x + f, base - t), (x + w - f, base - t), (x + w - f + lean * (h - t) / h, base - h + t),
                       (x + f + lean * (h - t) / h, base - h + t)], fill=ORANGE + (255,))
        else:
            d.polygon(poly, fill=(lit if i == LIT else plain) + (255,))
    if state == "error":
        r = 7 * u
        d.ellipse((n - 6 * u - 2 * r, 6 * u, n - 6 * u, 6 * u + 2 * r), fill=ORANGE + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: str) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    # Each size drawn on its own (not one image scaled), so small sizes stay sharp.
    imgs = [kong_icon("idle", s) for s in sizes]
    imgs[-1].save(path, sizes=[(s, s) for s in sizes], append_images=imgs[:-1])


# --- section glyphs: thin orange line drawings on a 24-unit grid -----------------------------


def glyph(name: str, size: int, color=ORANGE, bg=None) -> Image.Image:
    ss = 8
    n = size * ss
    u = n / 24
    img = Image.new("RGBA", (n, n), (bg or (0, 0, 0)) + ((255,) if bg else (0,)))
    d = ImageDraw.Draw(img)
    c = color + (255,)
    sw = max(1, round(1.8 * u))

    def P(*pts):
        return [(x * u, y * u) for x, y in pts]

    if name == "mic":
        d.rounded_rectangle(P((9, 2.5), (15, 14)), radius=3 * u, outline=c, width=sw)
        d.arc(P((5.5, 6), (18.5, 18)), 0, 180, fill=c, width=sw)
        d.line(P((12, 18), (12, 21.5)), fill=c, width=sw)
        d.line(P((8.5, 21.5), (15.5, 21.5)), fill=c, width=sw)
    elif name == "spark":
        d.polygon(P((10, 3), (12, 10), (19, 12), (12, 14), (10, 21), (8, 14), (1.5, 12), (8, 10)), fill=c)
        d.polygon(P((18.5, 2), (19.5, 4.5), (22, 5.5), (19.5, 6.5), (18.5, 9), (17.5, 6.5), (15, 5.5), (17.5, 4.5)), fill=c)
    elif name == "key":
        d.rounded_rectangle(P((2.5, 5), (21.5, 19)), radius=3.5 * u, outline=c, width=sw)
        d.line(P((8, 14.5), (16, 14.5)), fill=c, width=sw)
    elif name == "paste":
        d.rounded_rectangle(P((4.5, 5), (19.5, 22)), radius=2.5 * u, outline=c, width=sw)
        d.rounded_rectangle(P((8.5, 2.5), (15.5, 7.5)), radius=1.5 * u, fill=c)
        d.line(P((8.5, 13), (15.5, 13)), fill=c, width=sw)
        d.line(P((8.5, 17), (13.5, 17)), fill=c, width=sw)
    elif name == "sliders":
        for y, kx in ((7, 15), (17, 8)):
            d.line(P((2.5, y), (21.5, y)), fill=c, width=sw)
            r = 3 * u
            d.ellipse((kx * u - r, y * u - r, kx * u + r, y * u + r), fill=c)
    return img.resize((size, size), Image.LANCZOS)
