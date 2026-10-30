"""Screenshot the main window's tabs (Linux + Xvfb, development).

    KONG_HOME=/tmp/k xvfb-run -s "-screen 0 1280x900x24" python tools/preview_window.py OUT_DIR
"""

import sys
import threading
import time
from pathlib import Path

from PIL import ImageGrab

from kong import app as kapp
from kong.config import Config
from tools import fakes

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
pasted: list[str] = []
paste_fn = fakes.install(kapp, pasted)
a = kapp.App(Config(gpu_offer_answered=True))
a.paste_fn = paste_fn
a.history.add("我們明天開會的時候要討論一下這個 implementation，順便看一下 roadmap。")
a.history.add("這個 bug 我前天修了，你再 pull 一次看看。")


def shot(name):
    w = a.main_window.win
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height())).save(out / f"{name}.png")


def on_tk(fn, *args):
    done = threading.Event()
    a.panel.call(lambda: (fn(*args), done.set()))
    done.wait(10)


def script():
    while a.state != "idle":
        time.sleep(0.2)
    time.sleep(2)
    mw = a.main_window
    for i, name in enumerate(("1-home", "2-meeting", "3-glossary", "4-settings")):
        on_tk(mw.nb.select, i)
        time.sleep(0.5)
        on_tk(shot, name)
    a.quit()


threading.Thread(target=script, daemon=True).start()
a.run(show_window=True)
print("saved to", out)
