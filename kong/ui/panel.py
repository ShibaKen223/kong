"""The strip at the bottom of the screen: 聽寫中 → 辨識中 → 整理中 → 已貼上.

Every public method is safe to call from any thread: work is queued for the Tk thread.
The window never takes focus (WS_EX_NOACTIVATE) and is never unmapped: hiding makes it fully
transparent and moves it off-screen, because showing a window again can activate it and pull
the cursor out of the app the user is typing in.
"""

from __future__ import annotations

import collections
import logging
import queue
import sys
import time
import tkinter as tk
from typing import Callable

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

BG = "#1c1c1e"
FG = "#f2f2f7"
SUB = "#a1a1aa"
BORDER = "#3a3a3c"
COLORS = {
    "listen": "#ff453a",
    "work": "#ffb340",
    "ok": "#32d74b",
    "warn": "#ffb340",
    "error": "#ff453a",
    "info": "#0a84ff",
}
FONT = "Microsoft JhengHei UI" if IS_WIN else "Noto Sans CJK TC"
MAX_CHARS = 160
WIDTH = 580


class Panel:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._hide_at: float | None = None
        self._listen_since: float | None = None
        self._level: Callable[[], float] = lambda: 0.0
        self._levels: collections.deque[float] = collections.deque([0.0] * 18, maxlen=18)
        self._visible = False
        self._scale = 1.0
        self._hwnd = None
        self._build()
        root.after(30, self._pump)

    # --- construction (Tk thread) --------------------------------------------------------

    def _build(self) -> None:
        r = self.root
        r.withdraw()
        r.overrideredirect(True)
        r.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        try:
            self._scale = max(1.0, r.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            pass
        px = lambda v: int(v * self._scale)  # noqa: E731

        outer = tk.Frame(r, bg=BG, padx=px(16), pady=px(12))
        outer.pack(fill="both", expand=True)
        top = tk.Frame(outer, bg=BG)
        top.pack(fill="x")
        self._dot = tk.Canvas(top, width=px(12), height=px(12), bg=BG, highlightthickness=0)
        self._dot.pack(side="left", padx=(0, px(8)))
        self._dot_item = self._dot.create_oval(px(1), px(1), px(11), px(11), fill=COLORS["info"], outline="")
        self._title = tk.Label(top, text="", fg=FG, bg=BG, font=(FONT, 11, "bold"))
        self._title.pack(side="left")
        self._clock = tk.Label(top, text="", fg=SUB, bg=BG, font=(FONT, 10))
        self._clock.pack(side="left", padx=(px(8), 0))
        self._meter = tk.Canvas(top, width=px(110), height=px(18), bg=BG, highlightthickness=0)
        self._meter.pack(side="right")

        self._text = tk.Label(
            outer,
            text="",
            fg=FG,
            bg=BG,
            font=(FONT, 12),
            justify="left",
            anchor="w",
            wraplength=px(WIDTH - 40),
        )
        self._text.pack(fill="x", pady=(px(6), 0))
        self._hint = tk.Label(outer, text="", fg=SUB, bg=BG, font=(FONT, 9), anchor="w")
        self._hint_pad = (px(6), 0)

        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.0)
        r.geometry(f"{px(WIDTH)}x{px(90)}+-20000+-20000")
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
            win32.make_overlay(self._hwnd)

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
        if self._listen_since is not None:
            secs = int(now - self._listen_since)
            self._clock.configure(text=f"{secs // 60}:{secs % 60:02d}")
            self._levels.append(self._level())
            self._draw_meter()
        if self._hide_at is not None and now >= self._hide_at:
            self._do_hide()
        self.root.after(40, self._pump)

    def _draw_meter(self) -> None:
        c = self._meter
        c.delete("all")
        w, h = int(c["width"]), int(c["height"])
        n = len(self._levels)
        bw = w / n
        for i, lv in enumerate(self._levels):
            v = min(1.0, (lv * 12) ** 0.6)
            bh = max(2, v * h)
            x = i * bw
            c.create_rectangle(x + 1, (h - bh) / 2, x + bw - 2, (h + bh) / 2, fill=COLORS["listen"], outline="")

    def _place(self) -> None:
        r = self.root
        r.update_idletasks()
        w = int(WIDTH * self._scale)
        h = r.winfo_reqheight()
        area = None
        if IS_WIN:
            from .. import win32

            area = win32.work_area(win32.GetForegroundWindow())
        if area is None:
            area = (0, 0, r.winfo_screenwidth(), r.winfo_screenheight())
        left, top, right, bottom = area
        x = left + (right - left - w) // 2
        y = bottom - h - int(28 * self._scale)
        r.geometry(f"{w}x{h}+{x}+{y}")
        if not self._visible:
            r.attributes("-alpha", 0.97)
            self._visible = True
        if self._hwnd:
            from .. import win32

            # Re-assert: Tk rewrites the extended style when it changes the alpha.
            win32.make_overlay(self._hwnd)

    def _set_hint(self, hint: str) -> None:
        self._hint.configure(text=hint)
        if hint:
            self._hint.pack(fill="x", pady=self._hint_pad)
        else:
            self._hint.pack_forget()

    def _set_dot(self, kind: str) -> None:
        self._dot.itemconfigure(self._dot_item, fill=COLORS.get(kind, COLORS["info"]))

    def _do_listening(self, level, hint) -> None:
        self._level = level
        self._levels.extend([0.0] * self._levels.maxlen)
        self._listen_since = time.monotonic()
        self._hide_at = None
        self._set_dot("listen")
        self._title.configure(text="聽寫中")
        self._clock.configure(text="0:00")
        self._text.configure(text="", fg=SUB)
        self._set_hint(hint)
        self._meter.pack(side="right")
        self._place()

    def _do_text(self, text: str) -> None:
        if len(text) > MAX_CHARS:
            text = "…" + text[-MAX_CHARS:]
        self._text.configure(text=text, fg=FG)
        if self._visible:
            self._place()

    def _do_status(self, kind, title, text, hint, hide_after) -> None:
        self._listen_since = None
        self._clock.configure(text="")
        self._meter.delete("all")
        self._set_dot(kind)
        self._title.configure(text=title)
        if text is not None:
            if len(text) > MAX_CHARS:
                text = "…" + text[-MAX_CHARS:]
            self._text.configure(text=text, fg=FG if kind != "error" else COLORS["error"])
        self._set_hint(hint)
        self._hide_at = time.monotonic() + hide_after if hide_after else None
        self._place()

    def _do_hide(self) -> None:
        self._hide_at = None
        self._listen_since = None
        self._visible = False
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("+-20000+-20000")
