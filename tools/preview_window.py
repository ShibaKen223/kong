"""Screenshot the main window's tabs, including a test dictation (Linux + Xvfb, development).

    KONG_HOME=/tmp/k xvfb-run -s "-screen 0 1280x800x24" python tools/preview_window.py OUT_DIR a.wav
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import ImageGrab

from kong import app as kapp
from kong.config import Config
from tools import fakes

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
pasted: list[str] = []
paste_fn = fakes.install(kapp, pasted)
clip = np.concatenate([np.zeros(8000, np.float32), fakes.read_wav(sys.argv[2]), np.zeros(8000, np.float32)])
fakes.FakeRecorder.queue = [clip]
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
    time.sleep(3)
    mw = a.main_window
    on_tk(mw._toggle_test)
    time.sleep(len(clip) / 16000 + 0.5)
    on_tk(mw._toggle_test)
    t = time.monotonic()
    while a.state != "idle" and time.monotonic() - t < 40:
        time.sleep(0.2)
    time.sleep(1.5)
    on_tk(shot, "1-home")
    for i, name in ((1, "2-meeting"), (2, "3-settings"), (3, "4-history"), (4, "5-glossary")):
        on_tk(mw.nb.select, i)
        time.sleep(0.5)
        on_tk(shot, name)
    a.quit()


threading.Thread(target=script, daemon=True).start()
a.run(show_window=True)
print("saved to", out)
