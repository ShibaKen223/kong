import threading
import time

import numpy as np

from kong import audio
from kong.segmenter import Segmenter, find_pause

SR = audio.SR


def speech(seconds, amp=0.3):
    t = np.arange(int(seconds * SR))
    return (amp * np.sin(t * 0.07) * (1 + 0.3 * np.sin(t * 0.0013))).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), np.float32)


def test_find_pause_picks_the_last_pause():
    x = np.concatenate([speech(3), silence(0.6), speech(3), silence(0.8), speech(2)])
    pos = find_pause(x, 0, len(x))
    assert 6.6 * SR < pos < 7.4 * SR  # inside the second pause


def test_short_gaps_are_not_pauses():
    x = np.concatenate([speech(3), silence(0.1), speech(3)])
    assert find_pause(x, 0, len(x)) is None


class FakeEngine:
    id = "fake"
    incremental = True

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def transcribe(self, clip, glossary=None):
        with self.lock:
            self.calls.append(len(clip) / SR)
            time.sleep(0.05)
            return f"段{len(self.calls)}。"


def test_segments_are_cut_at_pauses_and_joined_in_order():
    e = FakeEngine()
    seg = Segmenter(e)
    x = np.concatenate([speech(7), silence(0.6), speech(7), silence(0.6), speech(3)])
    for i in range(SR, len(x) + 1, SR):
        seg.feed(x[:i])
    out = seg.finish(x)
    assert seg.submitted == 3
    assert out == "段1。段2。段3。"
    assert all(c > 2 for c in e.calls)


def test_cancel_stops_work():
    e = FakeEngine()
    seg = Segmenter(e)
    seg.cancel()
    seg.feed(np.concatenate([speech(8), silence(0.6), speech(2)]))
    assert seg.submitted == 0
