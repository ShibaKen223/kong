"""A microphone glyph drawn with Pillow, so the app ships no image files."""

from __future__ import annotations

from PIL import Image, ImageDraw

COLORS = {
    "idle": (10, 132, 255),
    "recording": (255, 69, 58),
    "busy": (255, 179, 64),
    "loading": (142, 142, 147),
    "error": (142, 142, 147),
}


def mic_icon(state: str = "idle", size: int = 64) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2 * s, 2 * s, 62 * s, 62 * s), radius=14 * s, fill=COLORS.get(state, COLORS["idle"]) + (255,))
    white = (255, 255, 255, 255)
    d.rounded_rectangle((24 * s, 11 * s, 40 * s, 38 * s), radius=8 * s, fill=white)
    d.arc((16 * s, 20 * s, 48 * s, 46 * s), start=0, end=180, fill=white, width=max(2, int(4 * s)))
    d.line((32 * s, 46 * s, 32 * s, 53 * s), fill=white, width=max(2, int(4 * s)))
    d.line((24 * s, 54 * s, 40 * s, 54 * s), fill=white, width=max(2, int(4 * s)))
    if state == "error":
        d.ellipse((42 * s, 40 * s, 60 * s, 58 * s), fill=(255, 69, 58, 255))
    return img


def write_ico(path: str) -> None:
    mic_icon("idle", 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
