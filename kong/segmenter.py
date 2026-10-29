"""Recognise finished stretches of speech while the user is still talking.

For a slow engine (Qwen3-ASR on a CPU) this hides most of the wait: by the time you stop,
everything before your last pause is already done and only the tail is left.
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np

from . import audio
from .stt import join_pieces

log = logging.getLogger(__name__)

_FRAME = int(audio.SR * 0.02)


def find_pause(x: np.ndarray, lo: int, hi: int, pause_s: float = 0.35) -> int | None:
    """Sample index in the middle of the last pause of at least `pause_s` within [lo, hi)."""
    lo, hi = max(0, lo), min(len(x), hi)
    n = (hi - lo) // _FRAME
    need = int(pause_s / 0.02)
    if n < need:
        return None
    rms = np.sqrt(np.mean(x[lo : lo + n * _FRAME].reshape(n, _FRAME) ** 2, axis=1))
    ctx = x[max(0, hi - 30 * audio.SR) : hi]
    k = len(ctx) // _FRAME
    ctx_rms = np.sqrt(np.mean(ctx[: k * _FRAME].reshape(k, _FRAME) ** 2, axis=1))
    quiet = rms < audio.speech_threshold(ctx_rms)
    run = 0
    for i in range(n - 1, -1, -1):
        if quiet[i]:
            run += 1
            continue
        if run >= need:
            return lo + (i + 1 + run // 2) * _FRAME
        run = 0
    return lo + (run // 2) * _FRAME if run >= need else None


class Segmenter:
    # Shorter pieces lose context and get less accurate; this is the shortest we cut off.
    MIN_S = 6.0

    def __init__(self, engine, glossary: list[str] | None = None) -> None:
        self.engine = engine
        self.glossary = glossary
        self.cut = 0
        self.submitted = 0
        self._texts: list[str] = []
        self._jobs: queue.Queue = queue.Queue()
        self._cancelled = False
        self._error: Exception | None = None
        self._worker = threading.Thread(target=self._run, name="segmenter", daemon=True)
        self._worker.start()

    @property
    def busy(self) -> bool:
        return self.submitted > len(self._texts)

    def _run(self) -> None:
        while True:
            seg = self._jobs.get()
            if seg is None:
                return
            if self._cancelled:
                continue
            try:
                clip = audio.trim_silence(seg)
                text = "" if audio.is_silent(clip) else self.engine.transcribe(clip, self.glossary)
            except Exception as e:  # reported by finish()
                log.exception("segment recognition failed")
                self._error = e
                text = ""
            self._texts.append(text)

    def _submit(self, seg: np.ndarray) -> None:
        self.submitted += 1
        self._jobs.put(seg.copy())

    def feed(self, x: np.ndarray) -> None:
        """Called every second or so with all audio recorded so far (16 kHz)."""
        if self._cancelled or len(x) - self.cut < int((self.MIN_S + 1.0) * audio.SR):
            return
        pos = find_pause(x, self.cut + int(self.MIN_S * audio.SR), len(x) - int(0.3 * audio.SR))
        if pos is not None and pos > self.cut:
            self._submit(x[self.cut : pos])
            self.cut = pos

    def finish(self, x: np.ndarray, timeout: float = 180.0) -> str:
        self._submit(x[self.cut :])
        self._jobs.put(None)
        self._worker.join(timeout)
        if self._error is not None and not any(self._texts):
            raise self._error
        return join_pieces(self._texts)

    def cancel(self) -> None:
        self._cancelled = True
        self._jobs.put(None)
