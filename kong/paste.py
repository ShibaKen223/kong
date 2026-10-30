"""Put text at the cursor of the window that was active when dictation started."""

from __future__ import annotations

import enum
import logging
import sys
import threading
import time

log = logging.getLogger(__name__)

# How long apps get to read the clipboard after Ctrl+V before we put the old content back.
# Electron apps read it asynchronously, so this cannot be very short.
RESTORE_DELAY_S = 1.2


class Result(enum.Enum):
    PASTED = "pasted"
    TYPED = "typed"
    CLIPBOARD = "clipboard"  # could not paste: the text is on the clipboard, press Ctrl+V
    FOCUS_LOST = "focus_lost"  # the original window would not come back: on the clipboard instead


def paste(text: str, target_hwnd, method: str = "clipboard", restore: bool = True) -> Result:
    if sys.platform != "win32":
        raise RuntimeError("pasting is only implemented on Windows")
    from . import win32

    if win32.target_blocks_input(target_hwnd):
        # Windows drops synthetic keys aimed at an elevated window; leave the text for Ctrl+V.
        win32.set_clipboard_text(text)
        log.info("target is elevated: left text on the clipboard")
        return Result.CLIPBOARD

    if not win32.focus(target_hwnd):
        if target_hwnd and win32.is_window(target_hwnd):
            # Pasting into whatever is in front now could put the words somewhere they don't belong.
            win32.set_clipboard_text(text)
            log.info("could not bring the original window back: left text on the clipboard")
            return Result.FOCUS_LOST
        log.info("the original window is gone; pasting into the current one")
    win32.release_modifiers()

    if method == "type":
        win32.type_text(text)
        return Result.TYPED

    kind, old, saved = ("empty", None, None)
    if restore:
        try:
            kind, old = win32.clipboard_snapshot()
            if kind == "other":
                # An image or files: keep a full copy to put back after pasting.
                saved = win32.clipboard_save()
        except OSError as e:
            log.info("clipboard snapshot failed: %s", e)
        if kind == "other" and saved is None:
            # Too large to hold: typing keeps it intact, but an input method can garble typing.
            win32.type_text(text)
            return Result.TYPED

    seq = win32.set_clipboard_text(text)
    time.sleep(0.03)
    if not win32.send_ctrl_v():
        return Result.CLIPBOARD

    if restore and ((kind == "text" and old is not None) or saved):

        def put_back():
            time.sleep(RESTORE_DELAY_S)
            # Only if nobody copied something else in the meantime.
            if win32.GetClipboardSequenceNumber() == seq:
                try:
                    if saved:
                        win32.clipboard_restore(saved)
                    else:
                        win32.set_clipboard_text(old)
                except OSError as e:
                    log.info("restoring the clipboard failed: %s", e)

        threading.Thread(target=put_back, name="clipboard-restore", daemon=True).start()
    return Result.PASTED


def copy(text: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("clipboard is only implemented on Windows")
    from . import win32

    win32.set_clipboard_text(text)
