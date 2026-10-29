"""Clean-up that needs no AI: 繁體台灣用字、全形標點、中英之間空格、口頭贅字。

Runs on every transcript before the AI tidy-up and again on its output, and is the whole job
when tidying is off or fails.
"""

from __future__ import annotations

import re
import threading

_HAN = "㐀-䶿一-鿿豈-﫿"
_PUNCT = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；"}

_cc = None
_cc_lock = threading.Lock()


def _converter():
    global _cc
    with _cc_lock:
        if _cc is None:
            import opencc

            _cc = opencc.OpenCC("s2twp")
        return _cc


def to_taiwan(text: str) -> str:
    """簡體或混用的字轉成台灣繁體與台灣用語（软件→軟體、视频→影片）。"""
    if not text:
        return text
    return _converter().convert(text)


def fullwidth_punct(text: str) -> str:
    """Half-width marks next to Chinese become full-width; "3.5", "v1.2" and English stay."""
    text = re.sub(rf"([{_HAN}])[ \t]*([,.?!:;])", lambda m: m.group(1) + _PUNCT[m.group(2)], text)
    text = re.sub(rf"([,?!:;])[ \t]*(?=[{_HAN}])", lambda m: _PUNCT[m.group(1)], text)
    text = re.sub(r"[ \t]+([，。？！：；、])", r"\1", text)
    text = re.sub(r"([，。？！：；、])[ \t]+(?=\S)", r"\1", text)
    return re.sub(r"([，、])\1+", r"\1", text)


def space_cjk_latin(text: str) -> str:
    """中文與英文字之間加半形空格：這個 implementation 很 elegant。"""
    text = re.sub(rf"([{_HAN}])([A-Za-z])", r"\1 \2", text)
    return re.sub(rf"([A-Za-z])([{_HAN}])", r"\1 \2", text)


_FILLER = "(?:嗯+|呃+|額+|欸+|誒+|恩+)"


def drop_fillers(text: str) -> str:
    """Only stand-alone 嗯／呃 between pauses; anything subtler is the AI's job."""
    text = re.sub(rf"^[ \t]*{_FILLER}[，,、 \t]*", "", text)
    text = re.sub(rf"(?<=[，。？！、])[ \t]*{_FILLER}[，,、 \t]*", "", text)
    return re.sub(r"[，、]$", "", text)


_WRAPPER_PREFIX = re.compile(r"^(?:整理後|整理结果|整理結果|輸出|输出|結果|結果如下)[:：]\s*")
_QUOTE_PAIRS = [("「", "」"), ("“", "”"), ('"', '"'), ("『", "』"), ("'", "'")]


def strip_wrappers(text: str) -> str:
    """Undo the ways a model sometimes decorates its answer."""
    text = text.strip()
    text = re.sub(r"</?transcript>", "", text).strip()
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = text[3:-3].strip()
    text = _WRAPPER_PREFIX.sub("", text)
    for a, b in _QUOTE_PAIRS:
        inner = text[len(a):-len(b)] if len(text) > len(a) + len(b) else ""
        if text.startswith(a) and text.endswith(b) and a not in inner and b not in inner:
            text = inner.strip()
            break
    return text


def _tidy_spaces(text: str) -> str:
    lines = [re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def basic_cleanup(raw: str, cjk_latin_space: bool = True) -> str:
    """The no-AI version of 整理: what gets pasted when tidying is off or fails."""
    text = to_taiwan(raw.strip())
    text = fullwidth_punct(text)
    text = drop_fillers(text)
    if cjk_latin_space:
        text = space_cjk_latin(text)
    return _tidy_spaces(text)


def finalize(polished: str, cjk_latin_space: bool = True) -> str:
    """Safety net on the AI's answer: still 繁體, still full-width, no decorations."""
    text = to_taiwan(strip_wrappers(polished))
    text = fullwidth_punct(text)
    if cjk_latin_space:
        text = space_cjk_latin(text)
    return _tidy_spaces(text)
