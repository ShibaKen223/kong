"""Put text at the cursor of the window that was active when dictation started."""

from __future__ import annotations

import enum
import logging
import sys
import threading
import time

log = logging.getLogger(__name__)

# Putting the old clipboard back waits until the app has actually read our text (see
# win32.ClipboardOffer), then this long more so it can finish with it.
AFTER_READ_S = 0.3
# Nobody read it by then: the paste did not happen. The text stays, for a Ctrl+V by hand.
READ_WAIT_S = 6.0
# Without an offer (it could not be set up), the old guess: put it back after this long.
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

    def put_old_back() -> None:
        try:
            if saved:
                win32.clipboard_restore(saved)
            else:
                win32.set_clipboard_text(old)
        except OSError as e:
            log.info("restoring the clipboard failed: %s", e)

    if restore and ((kind == "text" and old is not None) or saved):
        try:
            offer = win32.ClipboardOffer(text)
        except OSError as e:
            log.info("clipboard offer failed (%s); using a fixed delay", e)
            offer = None
        if offer is not None:
            time.sleep(0.03)
            if not win32.send_ctrl_v():
                offer.close()
                return Result.CLIPBOARD

            def after_paste():
                if not offer.wait_read(READ_WAIT_S):
                    log.info("nobody read the text within %.0f s: leaving it on the clipboard", READ_WAIT_S)
                    offer.close()
                    return
                time.sleep(AFTER_READ_S)
                if offer.still_ours():  # nobody copied something else meanwhile
                    put_old_back()
                offer.close()

            threading.Thread(target=after_paste, name="clipboard-restore", daemon=True).start()
            return Result.PASTED

    seq = win32.set_clipboard_text(text)
    time.sleep(0.03)
    if not win32.send_ctrl_v():
        return Result.CLIPBOARD

    if restore and ((kind == "text" and old is not None) or saved):

        def put_back():
            time.sleep(RESTORE_DELAY_S)
            # Only if nobody copied something else in the meantime.
            if win32.GetClipboardSequenceNumber() == seq:
                put_old_back()

        threading.Thread(target=put_back, name="clipboard-restore", daemon=True).start()
    return Result.PASTED


def copy(text: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("clipboard is only implemented on Windows")
    from . import win32

    win32.set_clipboard_text(text)
