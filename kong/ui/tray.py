"""System tray icon and its menu (右鍵選單)."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import pystray

from .. import DISPLAY_NAME, __version__
from ..config import ENGINES, HOTKEYS, PASTE_METHODS, POLISH_MODES
from .icon import mic_icon

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

CLAUDE_MODELS = {"sonnet": "Sonnet（建議）", "haiku": "Haiku（較省額度）", "opus": "Opus", "": "Claude Code 預設"}


class Tray:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.state = "loading"
        self.icon = pystray.Icon("Kong", mic_icon("loading"), DISPLAY_NAME, menu=pystray.Menu(self._items))

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
            self.icon.icon = mic_icon(state)
        if tooltip is not None:
            self.icon.title = f"{DISPLAY_NAME}：{tooltip}"[:120]

    def refresh(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    # --- menu ------------------------------------------------------------------------------

    def _radio(self, title: str, options: dict[str, str], key: str, enabled=lambda v: True):
        app = self.app

        def item(value, label):
            return pystray.MenuItem(
                label,
                lambda icon, it: app.set_option(key, value),
                checked=lambda it: getattr(app.cfg, key) == value,
                radio=True,
                enabled=enabled(value),
            )

        return pystray.MenuItem(title, pystray.Menu(*[item(v, label) for v, label in options.items()]))

    def _items(self):
        app = self.app
        cfg = app.cfg
        has_gpu = app.gpu is not None and app.gpu.usable
        yield pystray.MenuItem(f"{DISPLAY_NAME} {__version__}", None, enabled=False)
        yield pystray.MenuItem(f"辨識：{app.engine_status}", None, enabled=False)
        yield pystray.MenuItem(f"整理：{app.polish_status}", None, enabled=False)
        if app.download_status:
            yield pystray.MenuItem(app.download_status, None, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield self._radio("整理方式", POLISH_MODES, "polish")
        yield self._radio("Claude 模型", CLAUDE_MODELS, "claude_model")
        yield self._radio(
            "辨識引擎", ENGINES, "engine", enabled=lambda v: has_gpu or v != "whisper_gpu"
        )
        yield self._radio("快捷鍵（連按兩下開始）", HOTKEYS, "hotkey")
        yield self._radio("貼上方式", PASTE_METHODS, "paste_method")
        yield pystray.MenuItem(
            "邊講邊顯示文字",
            lambda icon, it: app.set_option("live_preview", not cfg.live_preview),
            checked=lambda it: cfg.live_preview,
        )
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("複製上一句", lambda icon, it: app.copy_last(), enabled=app.history.has_items())
        yield pystray.MenuItem("最近說過的話…", lambda icon, it: app.open_history())
        yield pystray.MenuItem("編輯常用詞…", lambda icon, it: app.open_glossary())
        if app.models_missing:
            yield pystray.MenuItem("重新下載語音模型", lambda icon, it: app.retry_setup())
        yield pystray.MenuItem(
            "開機時自動啟動",
            lambda icon, it: app.toggle_autostart(),
            checked=lambda it: app.autostart_enabled(),
        )
        yield pystray.MenuItem("匯出診斷檔到桌面", lambda icon, it: app.export_diagnostics())
        yield pystray.MenuItem("開啟記錄檔資料夾", lambda icon, it: app.open_logs())
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("結束", lambda icon, it: app.quit())
