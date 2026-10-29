"""The last 20 things you dictated, kept only on this computer."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_ITEMS = 20


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            self._items: list[dict] = [i for i in items if isinstance(i, dict) and "text" in i][-MAX_ITEMS:]
        except (OSError, ValueError):
            self._items = []

    def add(self, text: str) -> None:
        with self._lock:
            self._items.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text})
            self._items = self._items[-MAX_ITEMS:]
            try:
                self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass

    def items(self) -> list[dict]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._items))

    def has_items(self) -> bool:
        return bool(self._items)

    def last(self) -> str | None:
        with self._lock:
            return self._items[-1]["text"] if self._items else None

    def as_text(self) -> str:
        with self._lock:
            items = list(reversed(self._items))
        if not items:
            return "（還沒有紀錄）\n"
        return "\n\n".join(f"[{i.get('t', '')}]\n{i['text']}" for i in items) + "\n"
