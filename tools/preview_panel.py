"""Render the panel in each state and save PNGs (any OS; no window, no screen grab).

    python tools/preview_panel.py OUT_DIR [SCALE]

Each state is drawn over a light and a dark backdrop, the way it sits over real apps.
"""

import sys
from pathlib import Path

from PIL import Image

from kong.ui.panel import Painter, View

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
p = Painter(float(sys.argv[2]) if len(sys.argv) > 2 else 1.5)

LIVE = "那那個我們明天開會的時候要討論一下這個 implementation"
VOICE = [0.1, 0.3, 0.6, 0.85, 0.5, 0.7, 0.95, 0.4, 0.2, 0.55, 0.8]
states = {
    "1-listening-empty": View("listen", "聽寫中", "", "講完按一下「右 Ctrl」・Esc 取消", "0:01", 0.05, 0.3),
    "2-listening": View("listen", "聽寫中", LIVE, "講完按一下「右 Ctrl」・Esc 取消", "0:04", 0.7, 2.1, VOICE),
    "3-working": View("work", "整理中", LIVE + "，然後順便看一下 roadmap。", "Esc 取消", t=0.6),
    "4-done": View("ok", "已貼上", "我們明天開會的時候要討論一下這個 implementation，順便看一下 roadmap。", t=2),
    "5-warn": View(
        "warn", "已貼上原稿（沒有整理）", "這個 bug 我前天修了。",
        "Claude Code 還沒登入：打開終端機執行 claude，登入你的帳號後再試", t=2,
    ),
    "6-long": View("listen", "聽寫中", LIVE * 4, "講完按一下「右 Ctrl」・Esc 取消", "1:12", 0.4, 5.0, VOICE),
    "7-info": View("info", "已取消", t=2),
    "8-error": View("error", "麥克風打不開", "找不到可用的錄音裝置", "檢查 Windows 設定 → 隱私權與安全性 → 麥克風", t=2),
}
for name, v in states.items():
    card = p.render(v)
    w, h = card.size
    sheet = Image.new("RGBA", (w, h * 2), (0, 0, 0, 255))
    sheet.paste((246, 246, 244, 255), (0, 0, w, h))
    sheet.paste((30, 30, 32, 255), (0, h, w, h * 2))
    sheet.alpha_composite(card, (0, 0))
    sheet.alpha_composite(card, (0, h))
    sheet.convert("RGB").save(out / f"{name}.png")
print("saved to", out)
