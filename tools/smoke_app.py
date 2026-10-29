"""Drive the whole app without a microphone or Windows (development smoke test).

A wav file stands in for the mic, the hotkey is simulated, and "paste" is captured.

    KONG_HOME=/tmp/k xvfb-run -s "-screen 0 1280x720x24" python tools/smoke_app.py [--engine qwen3] a.wav ...
"""

import sys
import threading
import time

import numpy as np

from kong import app as kapp
from kong import audio
from kong.config import Config
from tools import fakes

args = sys.argv[1:]
engine = "sensevoice"
if args[:1] == ["--engine"]:
    engine, args = args[1], args[2:]
pasted: list[str] = []
paste_fn = fakes.install(kapp, pasted)
fakes.FakeRecorder.queue = [np.concatenate([np.zeros(8000, np.float32), fakes.read_wav(w), np.zeros(8000, np.float32)]) for w in args]
durations = [len(c) / audio.SR for c in fakes.FakeRecorder.queue]
a = kapp.App(Config(gpu_offer_answered=True, engine=engine))
a.paste_fn = paste_fn


def script():
    while a.state != "idle" or a.engine is None:
        time.sleep(0.2)
    time.sleep(4)  # Claude Code warm-up
    print("engine:", a.engine.label, flush=True)
    for d in durations:
        a._events.put("double")
        time.sleep(d + 0.3)
        a._events.put("tap")
        t = time.monotonic()
        while a.state != "idle" and time.monotonic() - t < 60:
            time.sleep(0.05)
        print(f"done in {time.monotonic() - t:.2f}s after the stop tap -> {pasted[-1] if pasted else None!r}", flush=True)
        time.sleep(3)
    a.quit()


threading.Thread(target=script, daemon=True).start()
a.run(show_window=False)
