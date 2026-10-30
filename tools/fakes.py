"""Stand-ins for the microphone, the tray and pasting, for driving the app on Linux."""

import threading
import time
import wave

import numpy as np

from kong import audio
from kong.paste import Result


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return audio.resample(a, sr)


class FakeRecorder(audio.Recorder):
    """Feeds the next queued clip in real time, the way a microphone would."""

    queue: list = []

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


def install(kapp, pasted: list):
    """Patch kong.app for a headless run; returns a paste function that records into `pasted`."""
    from kong.ui import main_window

    main_window.FONT = "WenQuanYi Zen Hei"
    kapp.Tray = FakeTray
    kapp.audio.Recorder = FakeRecorder

    def fake_paste(text, target, method, restore):
        pasted.append(text)
        return Result.PASTED

    return fake_paste
