"""Render the panel in each state and save screenshots (Linux + Xvfb, for development).

    xvfb-run -s "-screen 0 1280x720x24" python tools/preview_panel.py OUT_DIR
"""

import math
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

from kong.ui import panel as P

P.FONT = "WenQuanYi Zen Hei"
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

root = tk.Tk()
p = P.Panel(root)
t0 = time.monotonic()
level = lambda: abs(math.sin((time.monotonic() - t0) * 5)) * 0.06  # noqa: E731

steps = [
    (0.0, lambda: p.show_listening(level, "講完按一下「右 Ctrl」・Esc 取消")),
    (1.2, lambda: p.set_text("那那個我們明天開會的時候要討論一下這個 implementation")),
    (2.4, lambda: None),
    (2.6, lambda: p.show_working("整理中", "那那個我們明天開會的時候要討論一下這個 implementation，然後順便看一下 roadmap。")),
    (3.6, lambda: None),
    (3.8, lambda: p.show("ok", "已貼上", "我們明天開會的時候要討論一下這個 implementation，順便看一下 roadmap。", hide_after=None)),
    (4.8, lambda: None),
    (5.0, lambda: p.show("warn", "已貼上原稿（沒有整理）", "這個 bug 我前天修了。", hint="Claude Code 還沒登入：打開終端機執行 claude，登入你的帳號後再試", hide_after=None)),
    (6.0, lambda: None),
]
shots = {2.4: "1-listening", 3.6: "2-working", 4.8: "3-done", 6.0: "4-warn"}


def tick(i=0):
    if i >= len(steps):
        root.quit()
        return
    at, fn = steps[i]
    if time.monotonic() - t0 < at:
        root.after(30, tick, i)
        return
    fn()
    if at in shots:
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        ImageGrab.grab(bbox=(x - 20, y - 20, x + w + 20, y + h + 20)).save(out / f"{shots[at]}.png")
    root.after(30, tick, i + 1)


root.after(100, tick)
root.mainloop()
print("saved to", out)
