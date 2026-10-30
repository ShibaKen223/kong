"""The card at the bottom of the screen: 聽寫中 → 辨識中 → 整理中 → 已貼上.

Every public method is safe to call from any thread: work is queued for the Tk thread.
The window never takes focus (WS_EX_NOACTIVATE) and is never unmapped: hiding fades it to
fully transparent, because showing a window again can activate it and pull the cursor out of
the app the user is typing in.

The whole card is drawn with Pillow (`Painter`), so it can have a soft shadow, rounded
corners and an animated graphic. On Windows the image becomes the window itself through
UpdateLayeredWindow (per-pixel alpha); elsewhere it is shown in a plain Tk label, for previews.

Look: a cassette label. Warm grey paper, heavy black type, hairline rules, and a block of
signal orange and black on the right whose geometry shows the state: a dot meter of your
voice, sliding stripes while it works, arcs when it is done.
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import re
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import theme
from .theme import rgb

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

PAPER_TOP = (219, 219, 215)
PAPER_BOTTOM = (205, 205, 201)
INK = rgb(theme.INK)
INK_SOFT = rgb(theme.INK_SOFT)
INK_FAINT = rgb(theme.INK_FAINT)
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
WIDTH = 660  # the card; the window adds MARGIN on every side for the shadow
BLOCK_W = 150  # the graphic on the right
MARGIN = 30
RADIUS = 14
PAD_X = 22
PAD_TOP = 14
META_H = 22
LINE_H = 31
TEXT_PX = 21
SMALL_PX = 12
FOOTER_H = 34
CELL = 19  # dot meter grid
BOTTOM_GAP = 24  # card to the bottom of the work area
HISTORY = 12  # voice level samples kept for the dot meter

FADE_IN_S = 0.14
FADE_OUT_S = 0.18

_TOKEN = re.compile(r"[A-Za-z0-9_\-.'/:@#%&+]+|\s+|.", re.S)
_NO_LINE_START = set("，。、．・：；！？）」』】〉》…,.!?:;)]}%")


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


def _font(kind: str, px: int):
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
        self.f_text = _font("bold", self.px(TEXT_PX))
        self.f_small = _font("regular", self.px(SMALL_PX))
        self.f_meta = _font("bold", self.px(SMALL_PX))
        self.f_mark = _font("heavy", self.px(14))
        self._base_cache: dict[tuple[int, int], tuple[Image.Image, Image.Image]] = {}

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

    def _base(self, w: int, h: int) -> tuple[Image.Image, Image.Image]:
        """(shadow + paper card, card shape mask) for a card of w×h; cached per size."""
        key = (w, h)
        if key in self._base_cache:
            return self._base_cache[key]
        m, r = self.px(MARGIN), self.px(RADIUS)
        full = (w + 2 * m, h + 2 * m)

        shadow = Image.new("L", full, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (m + self.px(6), m + self.px(10), m + w - self.px(6), m + h + self.px(6)), radius=r, fill=170
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self.px(14)))
        img = Image.new("RGBA", full, (0, 0, 0, 0))
        img.putalpha(shadow)

        ss = 3  # supersample the rounded corners for smooth edges
        outer = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(outer).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
        inner = Image.new("L", (w * ss, h * ss), 0)
        bw = max(1, round(self.s)) * ss
        ImageDraw.Draw(inner).rounded_rectangle(
            (bw, bw, w * ss - 1 - bw, h * ss - 1 - bw), radius=r * ss - bw, fill=255
        )
        outer = outer.resize((w, h), Image.LANCZOS)
        inner = inner.resize((w, h), Image.LANCZOS)

        rows = np.linspace(0.0, 1.0, h)[:, None, None]
        grad = np.array(PAPER_TOP) * (1 - rows) + np.array(PAPER_BOTTOM) * rows
        # A little paper grain, fixed per size so it does not crawl between frames.
        grain = np.random.default_rng(7).normal(0.0, 1.6, (h, w, 1))
        paper = (np.repeat(grad, w, axis=1) + grain).clip(0, 255).astype(np.uint8)
        card = Image.fromarray(paper, "RGB").convert("RGBA")
        card.putalpha(outer)
        rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        rim.putalpha(ImageChops.subtract(outer, inner).point(lambda a: a * 40 // 255))
        card.alpha_composite(rim)
        img.alpha_composite(card, (m, m))
        self._base_cache[key] = (img, outer)
        return img, outer

    # --- the block -----------------------------------------------------------------------

    def block(self, v: View, w: int, h: int) -> Image.Image:
        """The graphic on the right edge, drawn 3× and scaled down for clean edges."""
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
        """A grid of dots: each column is a moment of your voice, newest on the right."""
        cell = CELL * u
        pad = 12 * u
        cols = max(3, int((W - 2 * pad) // cell))
        rows = max(3, int((H - 2 * pad) // cell))
        gx = (W - cols * cell) / 2
        gy = (H - rows * cell) / 2
        hist = (list(v.history) + [v.level])[-cols:]
        hist = [0.0] * (cols - len(hist)) + hist
        rmax = cell * 0.42
        for ci, lv in enumerate(hist):
            lit = lv * rows
            for ri in range(rows):
                cx = gx + ci * cell + cell / 2
                cy = gy + (rows - 1 - ri) * cell + cell / 2  # from the bottom up
                f = max(0.0, min(1.0, lit - ri))
                if f > 0.05:
                    r = rmax * (0.35 + 0.65 * f)
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
                else:
                    r = rmax * 0.28
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DIM_DOT)

    def _bar(self, width: int, height: int, color, alpha: float) -> Image.Image:
        """A bar that fades out at both ends."""
        width, height = max(1, width), max(1, height)
        ax = np.sin(np.pi * np.linspace(0.0, 1.0, width)) ** 0.5
        a = (np.ones((height, 1)) * ax[None, :] * 255 * alpha).clip(0, 255).astype(np.uint8)
        bar = Image.new("RGBA", (width, height), color + (0,))
        bar.putalpha(Image.fromarray(a, "L"))
        return bar

    # --- one frame -----------------------------------------------------------------------

    def render(self, v: View) -> Image.Image:
        px = self.px
        w, m = px(WIDTH), px(MARGIN)
        bw = px(BLOCK_W)
        col_x0, col_x1 = px(PAD_X), w - bw - px(PAD_X)
        text_w = col_x1 - col_x0

        main, fg = v.text, INK
        status = v.title
        if not main:
            if v.kind == "listen":
                main, fg = PLACEHOLDER, INK_FAINT
            else:
                main, status = v.title, ""
        if len(main) > MAX_CHARS:
            main = "…" + main[-MAX_CHARS:]
        lines = self.tail_lines(main, text_w)

        text_y = px(PAD_TOP) + px(META_H) + px(4)
        rule_y = text_y + len(lines) * px(LINE_H) + px(10)
        h = rule_y + px(FOOTER_H if v.hint else 18)

        base, shape = self._base(w, h)
        img = base.copy()
        d = ImageDraw.Draw(img)
        x0, y0 = m, m

        # The block, clipped to the card's rounded right corners.
        block = self.block(v, bw, h)
        block.putalpha(ImageChops.multiply(block.getchannel("A"), shape.crop((w - bw, 0, w, h))))
        img.alpha_composite(block, (x0 + w - bw, y0))

        # Meta row: what is happening on the left, the wordmark on the right.
        my = y0 + px(PAD_TOP) + px(META_H) // 2
        mark = "KONG"
        mw = int(self.f_mark.getlength(mark))
        d.text((x0 + col_x1 - mw, my), mark, font=self.f_mark, fill=INK, anchor="lm")
        if status:
            sq = px(7)
            sx = x0 + col_x0
            d.rectangle((sx, my - sq // 2, sx + sq, my - sq // 2 + sq), fill=ORANGE if v.kind != "ok" else INK)
            label = f"[ {status} ]" + (f"  {v.clock}" if v.clock else "")
            label = self.ellipsize(label, self.f_meta, col_x1 - col_x0 - mw - px(30))
            d.text((sx + sq + px(8), my), label, font=self.f_meta, fill=INK, anchor="lm")

        tx = x0 + col_x0
        for i, line in enumerate(lines):
            d.text((tx, y0 + text_y + i * px(LINE_H) + px(LINE_H) // 2), line, font=self.f_text, fill=fg, anchor="lm")

        # A hairline rule; orange runs along it with your voice or the work.
        rx0, rx1, ry = x0 + col_x0, x0 + col_x1, y0 + rule_y
        d.line((rx0, ry, rx1, ry), fill=INK + (210,), width=max(1, round(self.s)))
        rw, th = rx1 - rx0, px(3)
        seg = None
        if v.kind == "listen":
            sw = int(rw * (0.03 + 0.97 * v.level))
            seg = (rx0, sw)
        elif v.kind == "work":
            sw = int(rw * 0.28)
            p = (v.t / 1.4) % 1.0
            seg = (rx0 - sw + int(p * (rw + sw)), sw)
        elif v.kind in ("ok", "warn", "error"):
            seg = (rx0, int(rw * _ease(v.t / 0.45)))
        if seg and seg[1] > 0:
            sx, sw = seg
            layer = Image.new("RGBA", (rw, th), (0, 0, 0, 0))
            layer.paste(Image.new("RGBA", (sw, th), ORANGE + (255,)) if v.kind != "work" else self._bar(sw, th, ORANGE, 1.0),
                        (sx - rx0, 0))
            img.alpha_composite(layer, (rx0, ry - th // 2))

        if v.hint:
            fy = ry + px(FOOTER_H) // 2
            hint = self.ellipsize(v.hint, self.f_small, rw)
            d.text((rx0, fy), hint, font=self.f_small, fill=INK_SOFT, anchor="lm")
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
            if now - self._sampled >= 0.11:
                self._sampled = now
                v.history = (v.history + [v.level])[-HISTORY:]
            animating = True
        elif v.kind == "work" or now - self._since < 0.6:  # stripes run; arcs and rule sweep in
            animating = True
        if self._alpha != self._target:
            step = dt / (FADE_IN_S if self._target > self._alpha else FADE_OUT_S)
            self._alpha = min(self._target, self._alpha + step) if self._target > self._alpha else max(self._target, self._alpha - step)
            animating = True
        v.t = now - self._since
        if (animating or self._dirty) and (self._alpha > 0 or self._target > 0):
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
