"""Qwen3-ASR 0.6B (int8) via sherpa-onnx: much better than SenseVoice on fast, accented or
noisy speech, at several times the cost. Too slow for live preview on a CPU, so the app shows
SenseVoice text while you talk and recognises finished stretches with this in the background.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from ..audio import SR, split_long
from . import join_pieces

log = logging.getLogger(__name__)


class Qwen3ASR:
    id = "qwen3"
    label = "Qwen3-ASR（CPU，高準確度）"
    live_preview = False
    incremental = True

    def __init__(self, model_dir: Path, threads: int | None = None) -> None:
        import sherpa_onnx

        threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))
        self._rec = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(model_dir / "conv_frontend.onnx"),
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            tokenizer=str(model_dir),
            num_threads=threads,
            max_new_tokens=384,
            max_total_len=1200,
        )
        self._lock = threading.Lock()
        log.info("Qwen3-ASR loaded (%d threads)", threads)

    def transcribe(self, audio: np.ndarray, glossary: list[str] | None = None) -> str:
        # Its hotword option made results worse in testing; the glossary goes to the tidy-up instead.
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
