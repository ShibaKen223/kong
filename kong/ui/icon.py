"""Kong's mark, drawn with Pillow so the app ships no image files.

Four slanted bars on a black tile: a voice waveform cut like the speed stripes on a cassette
label. The tile and bar colours carry the state, so it reads at 16 px in the tray.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from . import theme

INK = theme.rgb(theme.INK)
ORANGE = theme.rgb(theme.ORANGE)
PAPER = theme.rgb(theme.PAPER)
GREY = (64, 64, 62)
MUTED = (150, 150, 145)

# (tile, bars) per state
STATES = {
    "idle": (INK, ORANGE),
    "recording": (ORANGE, INK),
    "busy": (INK, PAPER),
    "loading": (GREY, MUTED),
    "error": (GREY, MUTED),
}
HEIGHTS = (18, 36, 46, 26)  # out of 64
SLANT = 5  # how far the top of a bar leans right, out of 64


def kong_icon(state: str = "idle", size: int = 64) -> Image.Image:
    ss = 8 if size < 64 else 4  # draw big and scale down: crisp edges at every size
    n = size * ss
    u = n / 64
    tile, bar = STATES.get(state, STATES["idle"])
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=14 * u, fill=tile + (255,))
    for i, h in enumerate(HEIGHTS):
        x = (12 + i * 11) * u
        top, bot = (32 - h / 2) * u, (32 + h / 2) * u
        lean = SLANT * u * h / max(HEIGHTS)
        d.polygon([(x, bot), (x + 7.5 * u, bot), (x + 7.5 * u + lean, top), (x + lean, top)], fill=bar + (255,))
    if state == "error":
        r = 7 * u
        d.ellipse((n - 6 * u - 2 * r, 6 * u, n - 6 * u, 6 * u + 2 * r), fill=ORANGE + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: str) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    # Each size drawn on its own (not one image scaled), so small sizes stay sharp.
    imgs = [kong_icon("idle", s) for s in sizes]
    imgs[-1].save(path, sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
