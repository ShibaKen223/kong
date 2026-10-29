"""Speech-to-text engines. Each takes 16 kHz mono float32 audio and returns raw text."""

from __future__ import annotations

import re
from typing import Protocol

import numpy as np


class Engine(Protocol):
    id: str
    label: str
    # Fast enough to re-run every second while you talk.
    live_preview: bool

    def transcribe(self, audio: np.ndarray, glossary: list[str] | None = None) -> str: ...


def join_pieces(pieces: list[str]) -> str:
    out = ""
    for p in (p.strip() for p in pieces):
        if not p:
            continue
        if out and re.match(r"[A-Za-z0-9]", p) and re.search(r"[A-Za-z0-9,.?!]$", out):
            out += " "
        out += p
    return out


# Lines Whisper is known to invent on silence or music (subtitle credits from its training data).
_HALLUCINATIONS = re.compile(
    r"(字幕|字幕由|Amara|amara|請不吝|点赞|點贊|點讚|订阅|訂閱|打赏|打賞|明镜|明鏡|感谢观看|感謝觀看|謝謝觀看|谢谢观看|中文字幕|Subtitles by)"
)


def drop_hallucination(text: str, audio_s: float) -> str:
    """Whisper's classic silence fillers: drop them when they are all there is."""
    t = text.strip()
    if t and _HALLUCINATIONS.search(t) and len(t) <= max(30, audio_s * 8):
        return ""
    return t
