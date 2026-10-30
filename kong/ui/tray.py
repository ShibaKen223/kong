"""System tray icon and its menu (右鍵選單)."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import pystray

from .. import DISPLAY_NAME
from .icon import kong_icon

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

CLAUDE_MODELS = {"sonnet": "Sonnet（建議）", "haiku": "Haiku（較省額度）", "opus": "Opus", "": "Claude Code 預設"}


class Tray:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.state = "loading"
        self.icon = pystray.Icon("Kong", kong_icon("loading"), DISPLAY_NAME, menu=pystray.Menu(self._items))

    def start(self) -> None:
        threading.Thread(target=self._run, name="tray", daemon=True).start()

    def _run(self) -> None:
        try:
            self.icon.run()
        except Exception:
            log.exception("tray icon stopped")

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        if state != self.state:
            self.state = state
            self.icon.icon = kong_icon(state)
        if tooltip is not None:
            self.icon.title = f"{DISPLAY_NAME}：{tooltip}"[:120]

    def refresh(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _start_meeting(self) -> None:
        err = self.app.start_meeting()
        if err:
            self.app.panel.show("error", "沒辦法開始會議記錄", err, hide_after=5)

    # --- menu ------------------------------------------------------------------------------

    def _items(self):
        """Only the everyday actions; every setting lives in the main window's 設定."""
        app = self.app
        # default=True: a left click on the tray icon opens the main window.
        yield pystray.MenuItem("開啟 KONG", lambda icon, it: app.show_main(), default=True)
        if app.download_status:
            yield pystray.MenuItem(app.download_status, None, enabled=False)
        if app.models_missing:
            yield pystray.MenuItem("重新下載語音模型", lambda icon, it: app.retry_setup())
        yield pystray.Menu.SEPARATOR
        if app.state == "meeting":
            yield pystray.MenuItem("■ 結束會議記錄並整理", lambda icon, it: app.stop_meeting())
        elif app.state == "saving":
            yield pystray.MenuItem("整理會議記錄中…", None, enabled=False)
        else:
            yield pystray.MenuItem("● 開始會議記錄", lambda icon, it: self._start_meeting(), enabled=app.state == "idle")
        yield pystray.MenuItem("複製上一句", lambda icon, it: app.copy_last(), enabled=app.history.has_items())
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("結束 KONG", lambda icon, it: app.quit())
