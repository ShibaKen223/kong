"""SenseVoice-Small via sherpa-onnx: Mandarin/English/Cantonese/Japanese/Korean, fast on CPU."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from ..audio import SR, split_long
from . import join_pieces

log = logging.getLogger(__name__)


class SenseVoice:
    id = "sensevoice"
    label = "SenseVoice（CPU）"
    live_preview = True

    def __init__(self, model_dir: Path, threads: int | None = None) -> None:
        import sherpa_onnx

        threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=threads,
            use_itn=True,
            language="auto",
        )
        self._lock = threading.Lock()
        log.info("SenseVoice loaded (%d threads)", threads)

    def transcribe(self, audio: np.ndarray, glossary: list[str] | None = None) -> str:
        pieces = []
        for chunk in split_long(audio):
            if len(chunk) < SR // 10:
                continue
            with self._lock:
                s = self._rec.create_stream()
                s.accept_waveform(SR, chunk)
                self._rec.decode_stream(s)
            pieces.append(s.result.text)
        return join_pieces(pieces)
