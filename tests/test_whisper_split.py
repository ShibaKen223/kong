import threading
from types import SimpleNamespace

import numpy as np

from kong.audio import SR
from kong.stt.whisper_gpu import WhisperGPU


class FakeModel:
    def __init__(self):
        self.lengths = []

    def transcribe(self, audio, **kw):
        self.lengths.append(len(audio) / SR)
        return [SimpleNamespace(text=f"第{len(self.lengths)}段")], None


def make():
    w = WhisperGPU.__new__(WhisperGPU)  # no GPU or model here: only the splitting is tested
    w._model, w._lock = FakeModel(), threading.Lock()
    return w


def test_long_dictation_is_cut_before_whisper_30_second_window():
    w = make()
    t = np.arange(70 * SR)
    clip = (0.3 * np.sin(t * 0.07)).astype(np.float32)
    clip[25 * SR : 25 * SR + SR // 2] = 0  # a pause to cut at
    assert w.transcribe(clip) == "第1段第2段第3段"
    assert max(w._model.lengths) <= 28.0 and abs(sum(w._model.lengths) - 70) < 0.01
    assert abs(w._model.lengths[0] - 25.25) < 0.3  # cut inside the pause


def test_short_dictation_goes_in_whole():
    w = make()
    assert w.transcribe(np.ones(5 * SR, np.float32) * 0.1) == "第1段"
    assert w._model.lengths == [5.0]
