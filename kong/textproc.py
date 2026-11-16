"""Clean-up that needs no AI: 繁體台灣用字、全形標點、中英之間空格、口頭贅字。

Runs on every transcript before the AI tidy-up and again on its output, and is the whole job
when tidying is off or fails.
"""

from __future__ import annotations

import re
import threading

_HAN = "㐀-䶿一-鿿豈-﫿"
_PUNCT = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；"}

_cc: dict[str, object] = {}
_cc_lock = threading.Lock()
_ambiguous_chars: frozenset[str] | None = None

# Mainland words that mean nothing else in Taiwan. OpenCC's own phrase table (s2twp) also
# rewrites words Taiwan uses as they are: 支持→支援, 行政程序→行政程式, 項目→專案, 文件→檔案.
_TAIWAN_WORDS = {
    "人工智能": "人工智慧",
    "服務器": "伺服器",
    "數據庫": "資料庫",
    "軟件": "軟體",
    "硬件": "硬體",
    "視頻": "影片",
    "信息": "資訊",
    "內存": "記憶體",
    "硬盤": "硬碟",
    "鼠標": "滑鼠",
    "屏幕": "螢幕",
    "鏈接": "連結",
    "短信": "簡訊",
    "博客": "部落格",
    "打印": "列印",
}
_TAIWAN_WORDS_RE = re.compile("|".join(sorted(_TAIWAN_WORDS, key=len, reverse=True)))


def _converter(name: str = "s2tw"):
    with _cc_lock:
        if name not in _cc:
            import opencc

            _cc[name] = opencc.OpenCC(name)
        return _cc[name]


def _ambiguous() -> frozenset[str]:
    """Characters that are a simplified form and also written as they are in Taiwan: 里、干、后、
    台、范、咸… Taken as "the converter would change it, yet Big5 has it"."""
    global _ambiguous_chars
    if _ambiguous_chars is None:
        cc = _converter()
        found = set()
        for code in range(0x4E00, 0xA000):
            ch = chr(code)
            if cc.convert(ch) != ch:
                try:
                    ch.encode("big5")
                except UnicodeEncodeError:
                    continue
                found.add(ch)
        _ambiguous_chars = frozenset(found)
    return _ambiguous_chars


def _changed(text: str, name: str) -> int:
    out = _converter(name).convert(text)
    return sum(1 for a, b in zip(text, out) if a != b) if len(out) == len(text) else 0


def to_taiwan(text: str, assume_traditional: bool = False) -> str:
    """簡體轉成台灣繁體，再換掉幾個只有對岸用的詞（软件→軟體、视频→影片）。

    Text that is already 繁體 keeps its 里、干、后、台…: converting those again turns 里長 into
    裡長 and 干擾 into 幹擾. `assume_traditional` settles a text with no clear sign either way
    (the model's answer is 繁體; a recognizer's raw words are usually 簡體).
    """
    if not text:
        return text
    conv = _converter().convert(text)
    if conv != text and len(conv) == len(text):
        amb = _ambiguous()
        simplified = sum(1 for a, b in zip(text, conv) if a != b and a not in amb)
        traditional = _changed(text, "t2s")
        if (traditional and traditional >= simplified) or (assume_traditional and not simplified):
            conv = "".join(a if a != b and a in amb else b for a, b in zip(text, conv))
        else:
            # 台 is what people write; 臺 everywhere reads like a government form.
            conv = "".join("台" if a == "台" and b == "臺" else b for a, b in zip(text, conv))
    return _TAIWAN_WORDS_RE.sub(lambda m: _TAIWAN_WORDS[m.group(0)], conv)


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


# 額 and 恩 are how recognizers sometimes write 呃 and 嗯, but they also start real words
# (額外、額度、恩典): those two count only when a pause follows.
_FILLER = "(?:嗯+|呃+|欸+|誒+|(?:額+|恩+)(?=[，,、。？！ \\t]|$))"


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
    text = to_taiwan(strip_wrappers(polished), assume_traditional=True)
    text = fullwidth_punct(text)
    if cjk_latin_space:
        text = space_cjk_latin(text)
    return _tidy_spaces(text)
