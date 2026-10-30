"""會議記錄: an hour or more, recognised piece by piece while people talk, saved as a file.

Dictation pastes one utterance at the cursor. A meeting is different: nothing is pasted, the
audio is recognised at each pause as the meeting goes (so stopping takes seconds, not
minutes), every line is appended to a running .txt at once (a crash loses almost nothing),
and at the end the whole thing is written as Markdown with Claude's notes on top.
"""

from __future__ import annotations

import logging
import threading
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, textproc
from .segmenter import find_pause

log = logging.getLogger(__name__)

SR = audio.SR
PIECE_MIN_S = 12.0  # recognise once this much is waiting and someone pauses
PIECE_MAX_S = 25.0  # never let a piece grow past what Whisper sees at once
TICK_S = 1.0


@dataclass
class Line:
    t: float  # seconds from the start of the meeting
    text: str


def stamp(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


class Meeting:
    """Owns the recognition thread for one meeting. The recorder must already be running.

    `recorder` needs take() / stop() / elapsed(); `engine` needs transcribe(audio, glossary).
    """

    def __init__(
        self,
        recorder,
        engine,
        folder: Path,
        glossary: list[str] | None = None,
        cjk_space: bool = True,
        keep_audio: bool = False,
        max_s: float = 4 * 3600,
        on_line: Callable[[Line], None] | None = None,
        on_limit: Callable[[], None] | None = None,
    ) -> None:
        self.recorder = recorder
        self.engine = engine
        self.glossary = glossary or []
        self.cjk_space = cjk_space
        self.max_s = max_s
        self.on_line = on_line
        self.on_limit = on_limit
        self.started = datetime.now()
        self.lines: list[Line] = []
        self.chars = 0
        self.failed_pieces = 0
        stem = f"{self.started:%Y-%m-%d %H%M} 會議記錄"
        self.partial = folder / f"{stem}（錄音中）.txt"
        self.final = folder / f"{stem}.md"
        self.wav_path = folder / f"{stem}.wav" if keep_audio else None
        self._wav = None
        if self.wav_path is not None:
            self._wav = wave.open(str(self.wav_path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SR)
        self.partial.write_text(f"Kong 會議記錄（錄音中）{self.started:%Y-%m-%d %H:%M}\n\n", encoding="utf-8-sig")
        self._pending = np.zeros(0, dtype=np.float32)
        self._offset = 0  # samples already recognised (or skipped as silence)
        self._stop = threading.Event()
        self._limit_hit = False
        self._thread = threading.Thread(target=self._run, name="meeting", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def elapsed(self) -> float:
        return self.recorder.elapsed()

    # --- while recording ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self._take()
                self._drain(final=False)
            except Exception:
                log.exception("meeting: recognising a piece failed")
            if not self._limit_hit and self.elapsed() >= self.max_s:
                self._limit_hit = True
                log.info("meeting: reached %.1f h, stopping", self.max_s / 3600)
                if self.on_limit is not None:
                    self.on_limit()

    def _take(self, last: np.ndarray | None = None) -> None:
        new = self.recorder.take() if last is None else last
        if len(new):
            self._pending = np.concatenate([self._pending, new])
            if self._wav is not None:
                self._wav.writeframes((np.clip(new, -1, 1) * 32767).astype("<i2").tobytes())

    def _drain(self, final: bool) -> None:
        while True:
            x = self._pending
            n = len(x)
            if n == 0:
                return
            cut = None
            if not final and n < PIECE_MIN_S * SR:
                return
            if not final:
                # The last pause after the first few seconds: a sentence is over there.
                cut = find_pause(x, int(PIECE_MIN_S * 0.5 * SR), n - int(0.2 * SR))
            if cut is None:
                if n > PIECE_MAX_S * SR:
                    cut = len(audio.split_long(x, PIECE_MAX_S)[0])
                elif final:
                    cut = n
                else:
                    return  # still talking: wait for a pause
            self._recognize(x[:cut], self._offset)
            self._offset += cut
            self._pending = x[cut:]

    def _recognize(self, piece: np.ndarray, offset: int) -> None:
        trimmed = audio.trim_silence(piece)
        if audio.is_silent(trimmed):
            return
        try:
            raw = self.engine.transcribe(trimmed, self.glossary)
        except Exception:
            self.failed_pieces += 1
            log.exception("meeting: piece at %s failed", stamp(offset / SR))
            raw = ""
        text = textproc.basic_cleanup(raw, self.cjk_space)
        if not text:
            return
        line = Line(offset / SR, text)
        self.lines.append(line)
        self.chars += len(text)
        with self.partial.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp(line.t)}] {text}\n")
        if self.on_line is not None:
            self.on_line(line)

    # --- the end --------------------------------------------------------------------------

    def finish(self) -> None:
        """Stop recording and recognise what is left. Blocks for a few seconds."""
        self._stop.set()
        if self._thread.ident is not None:  # started
            self._thread.join(timeout=120)
        self._take(self.recorder.stop())
        self._drain(final=True)
        if self._wav is not None:
            self._wav.close()
            self._wav = None

    def transcript(self) -> str:
        return "\n".join(f"[{stamp(ln.t)}] {ln.text}" for ln in self.lines)

    def write(self, notes: str = "", note_error: str = "", engine_label: str = "") -> Path:
        """The finished Markdown file; the running .txt is removed once it is safely written."""
        end = datetime.now()
        minutes = max(1, round(self.elapsed() / 60))
        head = [
            f"# 會議記錄　{self.started:%Y-%m-%d %H:%M}",
            "",
            f"- 時間：{self.started:%H:%M}–{end:%H:%M}（{minutes} 分鐘）",
            f"- 逐字稿：{self.chars:,} 字" + (f"（辨識引擎：{engine_label}）" if engine_label else ""),
        ]
        if self.wav_path is not None:
            head.append(f"- 錄音檔：{self.wav_path.name}")
        if self.failed_pieces:
            head.append(f"- 注意：有 {self.failed_pieces} 段沒有辨識成功，細節在記錄檔")
        body = ["", notes.strip() if notes.strip() else f"> 沒有整理重點：{note_error or '逐字稿是空的'}", ""]
        script = ["---", "", "## 逐字稿", ""]
        script += [f"**[{stamp(ln.t)}]** {ln.text}  " for ln in self.lines] or ["（沒有辨識到內容）"]
        text = "\n".join(head + body + script) + "\n"
        tmp = self.final.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8-sig")
        tmp.replace(self.final)
        try:
            self.partial.unlink()
        except OSError:
            pass
        return self.final


def notes_timeout(chars: int) -> float:
    """Seconds to give Claude for the notes: a long meeting is a long read."""
    return max(180.0, min(900.0, 60.0 + chars / 40))
