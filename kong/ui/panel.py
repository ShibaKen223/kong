"""The card at the bottom of the screen: 聽寫中 → 辨識中 → 整理中 → 已貼上.

Every public method is safe to call from any thread: work is queued for the Tk thread.
The window never takes focus (WS_EX_NOACTIVATE) and is never unmapped: hiding fades it to
fully transparent, because showing a window again can activate it and pull the cursor out of
the app the user is typing in.

The whole card is drawn with Pillow (`Painter`), so it can have a soft shadow, rounded
corners and a glow. On Windows the image becomes the window itself through
UpdateLayeredWindow (per-pixel alpha); elsewhere it is shown in a plain Tk label, for previews.

Look: a dark, quiet card. Kong's mark glows orange on the left and swells with your voice,
the words are large and white, and one thin line under them carries the live state: a fine
waveform while listening, a sweep while working, solid when done.
"""

from __future__ import annotations

import collections
import logging
import math
import os
import queue
import re
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import theme
from .icon import kong_icon
from .theme import rgb

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

CARD_TOP = (44, 44, 48)
CARD_BOTTOM = (28, 28, 31)
CARD_ALPHA = 244  # a touch see-through, like frosted glass without the blur
FG = (242, 242, 247)
FG_STATUS = (206, 206, 212)
SUB = (146, 146, 154)
FAINT = (112, 112, 120)
ERROR_TEXT = (255, 140, 128)
INK = rgb(theme.INK)
ORANGE = rgb(theme.ORANGE)
DIM_DOT = (52, 52, 52)
_WINDIR = os.environ.get("WINDIR", r"C:\Windows")
_F = os.path.join(_WINDIR, "Fonts")
FONT_FILES = {
    "regular": [
        os.path.join(_F, "msjh.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
    "bold": [
        os.path.join(_F, "msjhbd.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
    "heavy": [  # Latin only: the wordmark
        os.path.join(_F, "seguibl.ttf"),
        os.path.join(_F, "ariblk.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}
MAX_CHARS = 160
MAX_LINES = 3
PLACEHOLDER = "請開始說話…"

# Layout, in pixels at 96 dpi.
WIDTH = 640  # the card; the window adds MARGIN on every side for the shadow
MARGIN = 30
RADIUS = 24
MARK_X = 42  # centre of the glowing mark
MARK = 30
TEXT_X = 78
PAD_R = 30
PAD_TOP = 22
LINE_H = 32
TEXT_PX = 20
SMALL_PX = 12.5
FOOTER_H = 42
WAVE_H = 9  # half-height of the tallest waveform tick
CELL = 10  # dot meter grid (the main window's header graphic)
BOTTOM_GAP = 24  # card to the bottom of the work area
HISTORY = 96  # voice level samples kept: the waveform shows the last few seconds

FADE_IN_S = 0.14
FADE_OUT_S = 0.18

_TOKEN = re.compile(r"[A-Za-z0-9_\-.'/:@#%&+]+|\s+|.", re.S)
_NO_LINE_START = set("，。、．・：；！？）」』】〉》…,.!?:;)]}%")
# Which version of the mark sits in the glow.
_MARK_STATE = {"error": "error", "work": "busy"}


@dataclass
class View:
    kind: str = "info"
    title: str = ""
    text: str = ""
    hint: str = ""
    clock: str = ""
    level: float = 0.0  # smoothed microphone level, 0..1
    t: float = 0.0  # seconds since this state began, for animation
    history: list[float] = field(default_factory=list)  # recent levels, oldest first


def _font(kind: str, px: float):
    for path in FONT_FILES[kind]:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def to_bgra(img: Image.Image) -> bytes:
    """RGBA → premultiplied BGRA, the layout UpdateLayeredWindow wants."""
    a = np.asarray(img, dtype=np.uint16)
    alpha = a[..., 3]
    out = np.empty(a.shape, np.uint8)
    for dst, src in ((0, 2), (1, 1), (2, 0)):
        out[..., dst] = (a[..., src] * alpha + 127) // 255
    out[..., 3] = alpha
    return out.tobytes()


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


class Painter:
    """Draws one frame of the card. Pure Pillow: no Tk, no window, so previews can use it."""

    def __init__(self, scale: float = 1.0) -> None:
        self.s = scale
        self.f_text = _font("regular", self.px(TEXT_PX))
        self.f_small = _font("regular", self.px(SMALL_PX))
        self.f_mark = _font("heavy", self.px(12))
        self._base_cache: dict[tuple[int, int], Image.Image] = {}
        self._glow = self._make_glow()
        self._marks = {k: kong_icon(k, self.px(MARK)) for k in ("idle", "busy", "error")}

    def px(self, v: float) -> int:
        return int(round(v * self.s))

    # --- text ----------------------------------------------------------------------------

    def wrap(self, text: str, font, width: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for tok in _TOKEN.findall(text):
            if "\n" in tok:
                lines.append(cur.rstrip())
                cur = ""
                continue
            if not cur and tok.isspace():
                continue
            if font.getlength(cur + tok) <= width or not cur or tok in _NO_LINE_START:
                cur += tok
                # A single token wider than the line (a long URL): break it by character.
                while font.getlength(cur) > width and len(cur) > 1 and tok not in _NO_LINE_START:
                    cut = len(cur) - 1
                    while cut > 1 and font.getlength(cur[:cut]) > width:
                        cut -= 1
                    lines.append(cur[:cut])
                    cur = cur[cut:]
                continue
            lines.append(cur.rstrip())
            cur = "" if tok.isspace() else tok
        if cur or not lines:
            lines.append(cur.rstrip())
        return lines

    def tail_lines(self, text: str, width: int) -> list[str]:
        """The last MAX_LINES lines; the newest words matter most while speaking."""
        lines = self.wrap(text, self.f_text, width)
        while len(lines) > MAX_LINES:
            text = "…" + "".join(lines[len(lines) - MAX_LINES:]).lstrip("…")
            lines = self.wrap(text, self.f_text, width)
            if len(lines) > MAX_LINES:
                text = "…" + text[2:]
                lines = self.wrap(text, self.f_text, width)
        return lines

    def ellipsize(self, text: str, font, width: int) -> str:
        if width <= 0:
            return ""
        if font.getlength(text) <= width:
            return text
        while text and font.getlength(text + "…") > width:
            text = text[:-1]
        return text + "…" if text else ""

    # --- the card ------------------------------------------------------------------------

    def _base(self, w: int, h: int) -> Image.Image:
        """Shadow + dark card for a card of w×h; cached per size."""
        key = (w, h)
        if key in self._base_cache:
            return self._base_cache[key]
        m, r = self.px(MARGIN), self.px(RADIUS)
        full = (w + 2 * m, h + 2 * m)

        shadow = Image.new("L", full, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (m + self.px(4), m + self.px(10), m + w - self.px(4), m + h + self.px(6)), radius=r, fill=190
        )
        img = Image.new("RGBA", full, (0, 0, 0, 0))
        img.putalpha(shadow.filter(ImageFilter.GaussianBlur(self.px(16))))

        rows = np.linspace(0.0, 1.0, h)[:, None, None]
        grad = (np.array(CARD_TOP) * (1 - rows) + np.array(CARD_BOTTOM) * rows).astype(np.uint8)
        card = Image.fromarray(np.repeat(grad, w, axis=1), "RGB").convert("RGBA")
        ss = 3  # supersample the corners for smooth edges
        shape = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=CARD_ALPHA)
        card.putalpha(shape.resize((w, h), Image.LANCZOS))
        # A hairline rim, brighter along the top like light catching the edge.
        rim = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle(
            (0, 0, w * ss - 1, h * ss - 1), radius=r * ss, outline=(255, 255, 255, 255), width=ss * max(1, round(self.s))
        )
        rim = rim.resize((w, h), Image.LANCZOS)
        fade = (np.linspace(34, 12, h)[:, None] * np.ones((1, w))).astype(np.float32)
        a = (np.asarray(rim.getchannel("A"), np.float32) * fade / 255).astype(np.uint8)
        rim.putalpha(Image.fromarray(a, "L"))
        card.alpha_composite(rim)
        img.alpha_composite(card, (m, m))
        self._base_cache[key] = img
        return img

    def _make_glow(self) -> Image.Image:
        g = self.px(96)
        mask = Image.new("L", (g, g), 0)
        r = self.px(16)
        ImageDraw.Draw(mask).ellipse((g / 2 - r, g / 2 - r, g / 2 + r, g / 2 + r), fill=200)
        glow = Image.new("RGBA", (g, g), ORANGE + (0,))
        glow.putalpha(mask.filter(ImageFilter.GaussianBlur(self.px(14))))
        return glow

    def _bar(self, width: int, height: int, color, alpha: float) -> Image.Image:
        """A bar that fades out at both ends."""
        width, height = max(1, width), max(1, height)
        ax = np.sin(np.pi * np.linspace(0.0, 1.0, width)) ** 0.5
        a = (np.ones((height, 1)) * ax[None, :] * 255 * alpha).clip(0, 255).astype(np.uint8)
        bar = Image.new("RGBA", (width, height), color + (0,))
        bar.putalpha(Image.fromarray(a, "L"))
        return bar

    # --- the main window's header graphic -------------------------------------------------

    def block(self, v: View, w: int, h: int) -> Image.Image:
        """A cassette-label tile whose geometry shows the state, drawn 3× and scaled down."""
        ss = 3
        W, H = w * ss, h * ss
        u = self.s * ss  # one 96-dpi pixel in this drawing
        k = v.kind
        bg = ORANGE if k in ("work", "ok", "warn") else INK
        img = Image.new("RGBA", (W, H), bg + (255,))
        d = ImageDraw.Draw(img)

        if k == "listen":
            self._dot_meter(d, v, W, H, u)
        elif k == "work":
            # Black stripes sliding to the right, like tape running.
            band, period = 10 * u, 22 * u
            off = (v.t * 46 * u) % period
            x = -H - period + off
            while x < W + period:
                d.polygon([(x, H), (x + band, H), (x + band + H * 0.6, 0), (x + H * 0.6, 0)], fill=INK)
                x += period
        elif k == "ok":
            # Quarter rings from the bottom-left corner, sweeping in.
            sweep = 90 * _ease(v.t / 0.45)
            cx, cy = 0, H
            band, gap = 12 * u, 10 * u
            r = 18 * u
            while r - band < max(W, H) * 1.5:
                d.arc((cx - r, cy - r, cx + r, cy + r), 270, 270 + sweep, fill=INK, width=int(band))
                r += band + gap
        elif k in ("warn", "error"):
            fg = INK if k == "warn" else ORANGE
            c = W / 2
            top, bot = H / 2 - 24 * u, H / 2 + 24 * u
            d.rectangle((c - 6 * u, top, c + 6 * u, bot - 18 * u), fill=fg)
            d.rectangle((c - 6 * u, bot - 11 * u, c + 6 * u, bot + 1 * u), fill=fg)
        else:
            # A checker of four orange squares.
            q = 16 * u
            cx, cy = W / 2, H / 2
            for i, j in ((0, 0), (1, 1)):
                d.rectangle((cx - q + i * q, cy - q + j * q, cx + i * q, cy + j * q), fill=ORANGE)
        return img.resize((w, h), Image.LANCZOS)

    def _dot_meter(self, d: ImageDraw.ImageDraw, v: View, W: int, H: int, u: float) -> None:
        """A fine grid of dots: each column is a moment of your voice, newest on the right,
        lit outward from the middle row like a waveform."""
        cell = CELL * u
        pad = 10 * u
        cols = max(3, int((W - 2 * pad) // cell))
        rows = max(3, int((H - 2 * pad) // cell))
        gx = (W - cols * cell) / 2
        gy = (H - rows * cell) / 2
        hist = (list(v.history) + [v.level])[-cols:]
        hist = [0.0] * (cols - len(hist)) + hist
        mid = (rows - 1) / 2
        rmax = cell * 0.36
        for ci, lv in enumerate(hist):
            reach = lv * (mid + 0.5)  # how far from the middle row this moment lights up
            cx = gx + ci * cell + cell / 2
            for ri in range(rows):
                cy = gy + ri * cell + cell / 2
                f = max(0.0, min(1.0, reach - abs(ri - mid) + 0.5))
                if ci == cols - 1 or lv > 0.02:
                    f = max(f, 1.0 if abs(ri - mid) < 0.6 else 0.0)  # the middle row is always on
                if f > 0.05:
                    r = rmax * (0.45 + 0.55 * f)
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
                else:
                    r = rmax * 0.32
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DIM_DOT)

    # --- one frame -----------------------------------------------------------------------

    def render(self, v: View) -> Image.Image:
        px = self.px
        w, m = px(WIDTH), px(MARGIN)
        tx, rx = px(TEXT_X), w - px(PAD_R)

        main, fg = v.text, (ERROR_TEXT if v.kind == "error" else FG)
        status = v.title
        if not main:
            if v.kind == "listen":
                main, fg = PLACEHOLDER, FAINT
            else:
                main, status = v.title, ""
        if len(main) > MAX_CHARS:
            main = "…" + main[-MAX_CHARS:]
        lines = self.tail_lines(main, rx - tx)

        lh = px(LINE_H)
        line_y = px(PAD_TOP) + len(lines) * lh + px(12)
        div_y = line_y + px(WAVE_H) + px(10)
        h = div_y + px(FOOTER_H)

        img = self._base(w, h).copy()
        d = ImageDraw.Draw(img)
        x0, y0 = m, m

        # The mark in its glow: swells with your voice, breathes while working.
        if v.kind == "listen":
            k, bright = 0.75 + 0.75 * v.level, 0.5 + 0.5 * v.level
        elif v.kind == "work":
            wave = 0.5 + 0.5 * math.sin(v.t * 2 * math.pi / 1.3)
            k, bright = 0.9 + 0.2 * wave, 0.55 + 0.35 * wave
        elif v.kind == "error":
            k, bright = 0.9, 0.35
        else:
            k, bright = 1.0, 0.7
        gs = max(2, int(self._glow.width * k))
        glow = self._glow.resize((gs, gs), Image.BILINEAR)
        glow.putalpha(glow.getchannel("A").point(lambda a: int(a * bright)))
        cx, cy = x0 + px(MARK_X), y0 + px(PAD_TOP) + lh // 2
        img.alpha_composite(glow, (cx - gs // 2, cy - gs // 2))
        mark = self._marks[_MARK_STATE.get(v.kind, "idle")]
        img.alpha_composite(mark, (cx - mark.width // 2, cy - mark.height // 2))

        for i, line in enumerate(lines):
            d.text((x0 + tx, y0 + px(PAD_TOP) + i * lh + lh // 2), line, font=self.f_text, fill=fg, anchor="lm")

        # The live line.
        lx0, lx1, ly = x0 + tx, x0 + rx, y0 + line_y
        thin = max(1, round(self.s))
        d.line((lx0, ly, lx1, ly), fill=(255, 255, 255, 20), width=thin)
        if v.kind == "listen":
            # A fine waveform of the last few seconds, newest on the right, older ticks fainter.
            hist = (list(v.history) + [v.level])[-HISTORY:]
            step = (lx1 - lx0) / (HISTORY - 1)
            tick = max(1, round(self.s * 1.4))
            for i, lv in enumerate(hist):
                x = lx1 - (len(hist) - 1 - i) * step
                hh = max(thin, px(WAVE_H) * lv)
                age = (len(hist) - 1 - i) / HISTORY
                d.line((x, ly - hh, x, ly + hh), fill=ORANGE + (int(255 * (1 - 0.6 * age)),), width=tick)
        elif v.kind == "work":
            sw = int((lx1 - lx0) * 0.3)
            p = (v.t / 1.4) % 1.0
            sx = lx0 - sw + int(p * (lx1 - lx0 + sw))
            th = px(2)
            layer = Image.new("RGBA", (lx1 - lx0, th), (0, 0, 0, 0))
            layer.paste(self._bar(sw, th, ORANGE, 1.0), (sx - lx0, 0))
            img.alpha_composite(layer, (lx0, ly - th // 2))
        elif v.kind in ("ok", "warn", "error"):
            # Draws itself across, left to right.
            end = lx0 + int((lx1 - lx0) * _ease(v.t / 0.45))
            if end > lx0:
                d.line((lx0, ly, end, ly), fill=ERROR_TEXT if v.kind == "error" else ORANGE, width=px(2))

        dy = y0 + div_y
        d.line((x0 + px(22), dy, x0 + w - px(22), dy), fill=(255, 255, 255, 16), width=thin)

        # Footer: status and hint on the left, the wordmark on the right.
        fy = dy + px(FOOTER_H) // 2
        mk = "KONG"
        mw = int(self.f_mark.getlength(mk))
        right = x0 + w - px(24)
        d.text((right - mw, fy), mk, font=self.f_mark, fill=FAINT, anchor="lm")
        room = right - mw - px(20)
        fx = x0 + px(24)
        if status:
            label = status + (f" {v.clock}" if v.clock else "")
            label = self.ellipsize(label, self.f_small, room - fx)
            if v.kind in ("listen", "warn", "error"):
                dot = px(3)
                d.ellipse((fx, fy - dot, fx + 2 * dot, fy + dot), fill=ERROR_TEXT if v.kind == "error" else ORANGE)
                fx += 2 * dot + px(8)
            d.text((fx, fy), label, font=self.f_small, fill=FG_STATUS, anchor="lm")
            fx += int(self.f_small.getlength(label))
            if v.hint:
                d.text((fx, fy), "・", font=self.f_small, fill=FAINT, anchor="lm")
                fx += int(self.f_small.getlength("・"))
        if v.hint:
            d.text((fx, fy), self.ellipsize(v.hint, self.f_small, room - fx), font=self.f_small, fill=SUB, anchor="lm")
        return img


class Panel:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._hide_at: float | None = None
        self._listen_since: float | None = None
        self._level: Callable[[], float] = lambda: 0.0
        self._levels: collections.deque[float] = collections.deque([0.0] * 3, maxlen=3)
        self._view = View()
        self._since = time.monotonic()
        self._sampled = 0.0
        self._alpha = 0.0  # what is on screen now; fades toward _target
        self._target = 0.0
        self._last = time.monotonic()
        self._dirty = False
        self._area: tuple[int, int, int, int] | None = None
        self._scale = 1.0
        self._hwnd = None
        self._layered = None
        self._label: tk.Label | None = None
        self._photo = None
        self._build()
        self._painter = Painter(self._scale)
        root.after(30, self._pump)

    @property
    def _visible(self) -> bool:
        return self._target > 0

    # --- construction (Tk thread) --------------------------------------------------------

    def _build(self) -> None:
        r = self.root
        r.withdraw()
        r.overrideredirect(True)
        try:
            self._scale = max(1.0, r.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            pass
        r.attributes("-topmost", True)
        if not IS_WIN:
            r.configure(bg="#3a3a38")
            self._label = tk.Label(r, bd=0, highlightthickness=0, bg="#3a3a38")
            self._label.pack()
            r.attributes("-alpha", 0.0)
        r.geometry("1x1+-20000+-20000")
        # Tk creates the real (wrapper) window only when it is first mapped, so map it now,
        # invisible and off-screen, then give that window its overlay styles.
        r.deiconify()
        r.update()
        if IS_WIN:
            from .. import win32

            try:
                self._hwnd = int(r.wm_frame(), 16) or None
            except (tk.TclError, ValueError):
                self._hwnd = None
            self._hwnd = self._hwnd or win32.toplevel_of(r.winfo_id())
            win32.make_overlay(self._hwnd, round_corners=False)
            self._layered = win32.LayeredWindow(self._hwnd)

    # --- thread-safe API -----------------------------------------------------------------

    def _post(self, fn, *args) -> None:
        self._q.put((fn, args))

    def call(self, fn, *args) -> None:
        """Run `fn` on the Tk thread."""
        self._post(fn, *args)

    def show_listening(self, level: Callable[[], float], hint: str) -> None:
        self._post(self._do_listening, level, hint)

    def set_text(self, text: str) -> None:
        self._post(self._do_text, text)

    def show_working(self, title: str, text: str | None = None, hint: str = "Esc 取消") -> None:
        self._post(self._do_status, "work", title, text, hint, None)

    def show(self, kind: str, title: str, text: str = "", hint: str = "", hide_after: float | None = 2.0) -> None:
        self._post(self._do_status, kind, title, text, hint, hide_after)

    def hide(self) -> None:
        self._post(self._do_hide)

    # --- Tk thread -----------------------------------------------------------------------

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("panel update failed")
        except queue.Empty:
            pass
        now = time.monotonic()
        dt, self._last = now - self._last, now
        if self._hide_at is not None and now >= self._hide_at:
            self._do_hide()

        v = self._view
        animating = False
        if self._listen_since is not None:
            secs = int(now - self._listen_since)
            v.clock = f"{secs // 60}:{secs % 60:02d}"
            self._levels.append(min(1.0, (self._level() * 12) ** 0.6))
            target = sum(self._levels) / len(self._levels)
            # Rise quickly with the voice, settle slowly: reads as alive, not jittery.
            v.level += (target - v.level) * (0.55 if target > v.level else 0.18)
            if now - self._sampled >= 0.07:
                self._sampled = now
                v.history = (v.history + [v.level])[-HISTORY:]
            animating = True
        elif v.kind == "work" or now - self._since < 0.6:  # stripes run; arcs and rule sweep in
            animating = True
        on_screen = self._alpha > 0
        if self._alpha != self._target:
            step = dt / (FADE_IN_S if self._target > self._alpha else FADE_OUT_S)
            self._alpha = min(self._target, self._alpha + step) if self._target > self._alpha else max(self._target, self._alpha - step)
            animating = True
        v.t = now - self._since
        # `on_screen`, not the new alpha: the tick that fades to 0 must still push that last,
        # empty frame, or the window keeps a see-through ghost of the one before.
        if (animating or self._dirty) and (on_screen or self._target > 0):
            try:
                self._frame()
            except Exception:
                log.exception("panel draw failed")
            self._dirty = False
        self.root.after(33, self._pump)

    def _work_area(self) -> tuple[int, int, int, int]:
        if IS_WIN:
            from .. import win32

            area = win32.work_area(win32.GetForegroundWindow())
            if area:
                return area
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _frame(self) -> None:
        img = self._painter.render(self._view)
        w, h = img.size
        m = self._painter.px(MARGIN)
        if self._area is None:
            self._area = self._work_area()
        left, top, right, bottom = self._area
        ease = 1 - (1 - self._alpha) ** 3
        x = left + (right - left - w) // 2
        y = bottom - self._painter.px(BOTTOM_GAP) - (h - m) + int((1 - ease) * self._painter.px(10))
        if self._layered is not None:
            if self._alpha <= 0:
                x, y = -20000, -20000
            self._layered.update(to_bgra(img), w, h, x, y, int(255 * ease))
            return
        from PIL import ImageTk

        flat = Image.new("RGBA", img.size, (58, 58, 56, 255))
        flat.alpha_composite(img)
        self._photo = ImageTk.PhotoImage(flat.convert("RGB"))
        self._label.configure(image=self._photo)
        self.root.geometry(f"{w}x{h}+{x}+{y}" if self._alpha > 0 else "+-20000+-20000")
        self.root.attributes("-alpha", 0.98 * ease)

    def _show_now(self) -> None:
        if self._target == 0:
            self._area = None  # follow the monitor the user is typing on
        self._target = 1.0
        self._dirty = True

    def _do_listening(self, level, hint) -> None:
        self._level = level
        self._levels.extend([0.0] * self._levels.maxlen)
        self._listen_since = time.monotonic()
        self._since = self._listen_since
        self._hide_at = None
        self._view = View(kind="listen", title="聽寫中", clock="0:00", hint=hint)
        self._show_now()

    def _do_text(self, text: str) -> None:
        self._view.text = text
        self._dirty = True

    def _do_status(self, kind, title, text, hint, hide_after) -> None:
        self._listen_since = None
        v = self._view
        if v.kind != kind:
            self._since = time.monotonic()
        v.kind, v.title, v.hint, v.clock, v.level = kind, title, hint, "", 0.0
        if text is not None:
            v.text = text
        self._hide_at = time.monotonic() + hide_after if hide_after else None
        self._show_now()

    def _do_hide(self) -> None:
        self._hide_at = None
        self._listen_since = None
        self._target = 0.0
