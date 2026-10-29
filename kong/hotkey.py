"""Double-tap detection for a lone modifier key (右 Ctrl by default).

Windows events come from a low-level keyboard hook (pynput's win32 listener with a raw
event filter); the filter never suppresses anything, so the key still reaches other apps.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

# A press held longer than this is a hold, not a tap.
TAP_MAX_S = 0.35
# Forget a key we saw go down but never come up (lock screen, focus lost to a UAC prompt).
# Held keys auto-repeat, so a key that is really still down keeps refreshing itself.
STALE_S = 1.5


class TapDetector:
    """Turns key events into taps of one key.

    A tap counts only if no other key went down while the key was held (so Ctrl+C is never a
    tap), and any other key between two taps breaks a double. The second tap of a double fires
    `on_double` instead of `on_tap`, so a double-tap never also looks like a single tap.
    """

    def __init__(
        self,
        key: str,
        double_s: float = 0.4,
        on_tap: Callable[[], None] | None = None,
        on_double: Callable[[], None] | None = None,
        on_other: Callable[[str], None] | None = None,
    ) -> None:
        self.key = key
        self.double_s = double_s
        self.on_tap = on_tap or (lambda: None)
        self.on_double = on_double or (lambda: None)
        self.on_other = on_other or (lambda key_id: None)
        self._down_at: float | None = None
        self._seen_at = 0.0
        self._clean = False
        self._last_tap_up: float | None = None
        self._others: dict[str, float] = {}

    def reset(self) -> None:
        self._down_at = None
        self._clean = False
        self._last_tap_up = None
        self._others.clear()

    def _purge(self, t: float) -> None:
        for k in [k for k, last in self._others.items() if t - last > STALE_S]:
            del self._others[k]

    def feed(self, key_id: str, down: bool, t: float) -> None:
        self._purge(t)
        if key_id == self.key:
            if down:
                if self._down_at is not None and t - self._seen_at <= STALE_S:
                    self._seen_at = t
                    return  # auto-repeat while held
                self._down_at = self._seen_at = t
                self._clean = not self._others
                return
            if self._down_at is None:
                return
            down_at, self._down_at = self._down_at, None
            if not self._clean or t - down_at > TAP_MAX_S:
                self._last_tap_up = None
                return
            if self._last_tap_up is not None and down_at - self._last_tap_up <= self.double_s:
                self._last_tap_up = None
                self.on_double()
            else:
                self._last_tap_up = t
                self.on_tap()
            return

        if down:
            if key_id not in self._others:
                self.on_other(key_id)
            self._others[key_id] = t
            self._clean = False
            self._last_tap_up = None
        else:
            self._others.pop(key_id, None)


# --- Windows key mapping -----------------------------------------------------------------

_VK_NAMES = {
    0xA0: "left_shift",
    0xA1: "right_shift",
    0xA2: "left_ctrl",
    0xA3: "right_ctrl",
    0xA4: "left_alt",
    0xA5: "right_alt",
    0x1B: "esc",
}
_LLKHF_EXTENDED = 0x01
_LLKHF_INJECTED = 0x10
_LLKHF_LOWER_IL_INJECTED = 0x02
_WM_KEYDOWN = 0x100
_WM_SYSKEYDOWN = 0x104


def map_win_key(vk: int, scan: int, flags: int) -> str | None:
    """Name a key from a KBDLLHOOKSTRUCT. Returns None for events to ignore."""
    if vk == 0xA2 and (scan & 0x200):
        return None  # the fake left Ctrl that AltGr layouts send with right Alt
    if vk == 0x11:
        return "right_ctrl" if flags & _LLKHF_EXTENDED else "left_ctrl"
    if vk == 0x12:
        return "right_alt" if flags & _LLKHF_EXTENDED else "left_alt"
    if vk == 0x10:
        return "right_shift" if (scan & 0xFF) == 0x36 else "left_shift"
    return _VK_NAMES.get(vk, f"vk{vk:02x}")


class HotkeyListener:
    """Global listener. Callbacks run on the hook thread: keep them quick (post to a queue)."""

    def __init__(
        self,
        key: str,
        double_ms: int,
        on_double: Callable[[], None],
        on_tap: Callable[[], None],
        on_esc: Callable[[], None],
    ) -> None:
        self._on_esc = on_esc
        self._lock = threading.Lock()
        self._detector = TapDetector(
            key, double_ms / 1000.0, on_tap=on_tap, on_double=on_double, on_other=self._other
        )
        self._listener = None

    def _other(self, key_id: str) -> None:
        if key_id == "esc":
            self._on_esc()

    def set_key(self, key: str, double_ms: int) -> None:
        with self._lock:
            self._detector.key = key
            self._detector.double_s = double_ms / 1000.0
            self._detector.reset()

    def _feed(self, key_id: str, down: bool) -> None:
        with self._lock:
            self._detector.feed(key_id, down, time.monotonic())

    def _win_filter(self, msg, data) -> bool:
        try:
            if not data.flags & (_LLKHF_INJECTED | _LLKHF_LOWER_IL_INJECTED):
                key_id = map_win_key(data.vkCode, data.scanCode, data.flags)
                if key_id is not None:
                    self._feed(key_id, msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN))
        except Exception:
            log.exception("hotkey filter failed")
        # False = skip pynput's own processing; the event still goes to every other app.
        return False

    def start(self) -> None:
        from pynput import keyboard

        if sys.platform == "win32":
            self._listener = keyboard.Listener(win32_event_filter=self._win_filter)
        else:
            names = {
                keyboard.Key.ctrl_r: "right_ctrl",
                keyboard.Key.ctrl_l: "left_ctrl",
                keyboard.Key.alt_r: "right_alt",
                keyboard.Key.alt_gr: "right_alt",
                keyboard.Key.shift_r: "right_shift",
                keyboard.Key.esc: "esc",
            }

            def name(k):
                return names.get(k, str(k))

            self._listener = keyboard.Listener(
                on_press=lambda k, *a: self._feed(name(k), True),
                on_release=lambda k, *a: self._feed(name(k), False),
            )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
