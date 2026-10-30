"""常用詞 that fill themselves in: names and terms learned from what you say.

Every few dictations (and after each meeting) Claude Code reads what was said, both the
recognizer's raw words and the tidied text, and names the words worth remembering: people,
companies, products, projects, technical and English terms, above all ones the recognizer got
wrong and the tidy-up corrected. Each must appear in what you said, so nothing is invented.

Learned words live in glossary-learned.txt next to the user's own glossary.txt, and
`prompt.load_glossary` reads both, so recognition and tidying use them at once. Removing a
word blocks it for good. State (counts, blocked words, sentences waiting) is learn.json.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from .polish import prompt

log = logging.getLogger(__name__)

BATCH = 8  # dictations per learning pass
MAX_TERMS = 150
MAX_SAMPLE_CHARS = 12000
TIMEOUT_S = 90.0

# ask(system_prompt, message, timeout) -> answer: ClaudeCodePolisher.complete, bound to a name.
Ask = Callable[[str, str, float], str]


def parse_terms(answer: str) -> list[str]:
    """The JSON array in Claude's answer, tolerating a stray word around it."""
    start, end = answer.find("["), answer.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(answer[start : end + 1])
    except ValueError:
        return []
    out = []
    for t in items if isinstance(items, list) else []:
        if isinstance(t, str) and 1 <= len(t.strip()) <= 40 and "\n" not in t:
            out.append(t.strip())
    return out


class Learner:
    def __init__(self, glossary: Path, state_file: Path) -> None:
        self.glossary = glossary
        self.learned_file = prompt.learned_file(glossary)
        self.state_file = state_file
        self._lock = threading.Lock()
        self._busy = False
        self.pending: list[list[str]] = []  # [raw, final] since the last pass
        self.counts: dict[str, int] = {}
        self.blocked: set[str] = set()
        self.last_note = ""
        self._load()

    # --- state -------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.pending = [p for p in data.get("pending", []) if isinstance(p, list) and len(p) == 2][-BATCH * 3 :]
        self.counts = {k: int(v) for k, v in data.get("counts", {}).items() if isinstance(k, str)}
        self.blocked = {b for b in data.get("blocked", []) if isinstance(b, str)}

    def _save(self) -> None:
        data = {"pending": self.pending, "counts": self.counts, "blocked": sorted(self.blocked)}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    def terms(self) -> list[tuple[str, int]]:
        """Learned words, most seen first (what glossary-learned.txt holds)."""
        own = {t.lower() for t in prompt.load_user_glossary(self.glossary)}
        items = [(t, n) for t, n in self.counts.items() if t not in self.blocked and t.lower() not in own]
        items.sort(key=lambda x: (-x[1], x[0]))
        return items[:MAX_TERMS]

    def _write_terms(self) -> None:
        lines = ["# Kong 從你說過的話自動學到的詞（會自動更新）。不要的詞請在主視窗「常用詞」移除。"]
        lines += [t for t, _ in self.terms()]
        tmp = self.learned_file.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.learned_file)

    # --- learning ------------------------------------------------------------------------

    def add(self, raw: str, final: str) -> bool:
        """Remember one dictation; True when enough are waiting for a learning pass."""
        with self._lock:
            self.pending.append([raw, final])
            self._save()
            return len(self.pending) >= BATCH and not self._busy

    def learn_pending(self, ask: Ask) -> list[str]:
        with self._lock:
            if self._busy or not self.pending:
                return []
            self._busy = True
            batch = list(self.pending)
        try:
            new = self._learn([(r, f) for r, f in batch], ask)
            with self._lock:
                self.pending = self.pending[len(batch) :]
                self._save()
            return new
        finally:
            self._busy = False

    def learn_text(self, lines: list[str], ask: Ask) -> list[str]:
        """From a meeting: its transcript lines (only tidied text, no raw)."""
        with self._lock:
            if self._busy:
                return []
            self._busy = True
        try:
            return self._learn([("", ln) for ln in lines], ask)
        finally:
            self._busy = False

    def _learn(self, samples: list[tuple[str, str]], ask: Ask) -> list[str]:
        # Keep the message bounded: a long meeting is sampled evenly, not just its start.
        total = sum(len(r) + len(f) + 40 for r, f in samples)  # 40: the tags around each one
        if total > MAX_SAMPLE_CHARS:
            step = total / MAX_SAMPLE_CHARS
            samples = samples[:: max(1, int(step + 0.999))]
        said = "\n".join(f for _, f in samples).lower()
        try:
            answer = ask(prompt.LEARN_PROMPT, prompt.learn_message(samples), TIMEOUT_S)
        except Exception as e:
            self.last_note = f"這次沒有學到新詞：{e}"
            log.info("learning words failed: %s", e)
            return []
        new = []
        with self._lock:
            for t in parse_terms(answer):
                if t in self.blocked or t.lower() not in said:  # only words that were really said
                    continue
                if t not in self.counts:
                    new.append(t)
                self.counts[t] = self.counts.get(t, 0) + 1
            if len(self.counts) > MAX_TERMS * 2:  # forget the rarest
                keep = sorted(self.counts.items(), key=lambda x: -x[1])[: MAX_TERMS * 2]
                self.counts = dict(keep)
            self._save()
            self._write_terms()
        self.last_note = f"學到 {len(new)} 個新詞" if new else "這次沒有新詞"
        log.info("learned %d new words from %d samples", len(new), len(samples))
        return new

    def remove(self, term: str) -> None:
        """Gone for good: it will not be learned again."""
        with self._lock:
            self.blocked.add(term)
            self.counts.pop(term, None)
            self._save()
            self._write_terms()
