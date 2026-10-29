"""Whisper large-v3-turbo on an NVIDIA GPU via faster-whisper (CTranslate2)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from ..audio import SR
from . import drop_hallucination

log = logging.getLogger(__name__)

# Whisper copies the style of its prompt: 繁體字, full-width punctuation, English kept as English.
_STYLE_PROMPT = "以下是台灣人說的話，用繁體中文，中英夾雜，例如：這個 implementation 很 elegant，我們下週再 review。"


class WhisperGPU:
    id = "whisper_gpu"
    label = "Whisper（NVIDIA 顯示卡）"
    live_preview = True
    incremental = False

    def __init__(self, model_dir: Path, compute_type: str = "float16") -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(model_dir), device="cuda", compute_type=compute_type)
        self._lock = threading.Lock()
        # The first call compiles kernels; pay for it now, not on the first sentence.
        self.transcribe(np.zeros(SR, dtype=np.float32))
        log.info("Whisper loaded on GPU (%s)", compute_type)

    def transcribe(self, audio: np.ndarray, glossary: list[str] | None = None) -> str:
        prompt = _STYLE_PROMPT
        if glossary:
            prompt += "常用詞：" + "、".join(glossary[:40]) + "。"
        with self._lock:
            segments, _info = self._model.transcribe(
                audio,
                language="zh",
                beam_size=5,
                initial_prompt=prompt,
                condition_on_previous_text=False,
                vad_filter=False,
                without_timestamps=True,
            )
            text = "".join(s.text for s in segments)
        return drop_hallucination(text, len(audio) / SR)
