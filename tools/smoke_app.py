"""Drive the whole app without a microphone or Windows (development smoke test).

A wav file stands in for the mic, the hotkey is simulated, and "paste" is captured.

    KONG_HOME=/tmp/k xvfb-run -s "-screen 0 1280x720x24" python tools/smoke_app.py a.wav [b.wav ...]
"""

import sys
import threading
import time
import wave

import numpy as np

from kong import app as kapp
from kong import audio
from kong.config import Config
from kong.paste import Result
from kong.ui import panel as P

P.FONT = "WenQuanYi Zen Hei"
WAVS = sys.argv[1:]
pasted: list[str] = []


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype("float32") / 32768.0
    return audio.resample(a, sr)


class FakeRecorder(audio.Recorder):
    """Feeds the next wav in real time, the way a microphone would."""

    queue: list[np.ndarray] = []

    def start(self):
        self._chunks, self._n, self._sr = [], 0, audio.SR
        clip = self.queue.pop(0)
        self._stream = object()

        def feed():
            for i in range(0, len(clip), 1600):
                if self._stream is None:
                    return
                part = clip[i:i + 1600]
                with self._lock:
                    self._chunks.append(part)
                    self._n += len(part)
                self.level = float(np.sqrt(np.mean(part * part)))
                time.sleep(0.1)

        threading.Thread(target=feed, daemon=True).start()

    def _close(self):
        self._stream = None


def fake_paste(text, target, method, restore):
    pasted.append(text)
    return Result.PASTED


class FakeTray:
    state = "loading"

    def __init__(self, app):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def refresh(self):
        pass

    def set_state(self, state, tooltip=None):
        self.state = state


kapp.Tray = FakeTray
kapp.audio.Recorder = FakeRecorder

FakeRecorder.queue = [np.concatenate([np.zeros(8000, np.float32), read_wav(w), np.zeros(8000, np.float32)]) for w in WAVS]
durations = [len(c) / audio.SR for c in FakeRecorder.queue]
a = kapp.App(Config(gpu_offer_answered=True))
a.paste_fn = fake_paste


def script():
    while a.state != "idle":
        time.sleep(0.2)
    time.sleep(4)  # Claude Code warm-up
    for d in durations:
        a._events.put("double")
        time.sleep(d + 0.3)
        a._events.put("tap")
        t = time.monotonic()
        while a.state != "idle" and time.monotonic() - t < 40:
            time.sleep(0.05)
        print(f"done in {time.monotonic() - t:.2f}s after the stop tap -> {pasted[-1] if pasted else None!r}", flush=True)
        time.sleep(3)
    a.quit()


threading.Thread(target=script, daemon=True).start()
a.run()
print("pasted:", pasted)
