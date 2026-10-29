"""User settings, stored as JSON in %APPDATA%\\Kong\\config.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

HOTKEYS = {
    "right_ctrl": "右 Ctrl",
    "right_alt": "右 Alt",
    "right_shift": "右 Shift",
}

ENGINES = {
    "auto": "自動（有 NVIDIA 顯示卡就用 Whisper）",
    "sensevoice": "SenseVoice（CPU，最快）",
    "qwen3": "Qwen3-ASR（CPU，高準確度，每句多等 1～3 秒）",
    "whisper_gpu": "Whisper（NVIDIA 顯示卡，又快又準）",
}

POLISH_MODES = {
    "claude_code": "Claude Code（用你的訂閱）",
    "none": "不整理（只轉繁體與標點）",
}

PASTE_METHODS = {
    "clipboard": "剪貼簿＋Ctrl+V（最相容）",
    "type": "模擬打字（不動剪貼簿）",
}


@dataclass
class Config:
    hotkey: str = "right_ctrl"
    double_tap_ms: int = 400

    engine: str = "auto"
    # The user agreed to download the GPU pack (cuBLAS + Whisper model).
    gpu_enabled: bool = False
    gpu_offer_answered: bool = False

    polish: str = "claude_code"
    # sonnet: tidies reliably in ~2 s. haiku is about as fast here but sometimes adds commentary.
    claude_model: str = "sonnet"
    claude_path: str = ""
    claude_timeout_s: float = 25.0
    claude_prewarm: bool = True
    # Talky-style isolation: drop ANTHROPIC_API_KEY so Claude Code uses the subscription login.
    claude_keep_api_key_env: bool = False

    paste_method: str = "clipboard"
    restore_clipboard: bool = True

    live_preview: bool = True
    mic_device: str = ""
    max_record_s: int = 300
    cjk_latin_space: bool = True

    # Off by default: the log never contains what you said.
    log_text: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or paths.config_file()
        cfg = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cfg
        except (OSError, ValueError) as e:
            log.warning("config unreadable (%s); using defaults", e)
            return cfg
        known = {f.name: f for f in fields(cls)}
        for k, v in raw.items():
            f = known.get(k)
            if f is None:
                continue
            default = getattr(cfg, k)
            # Keep the default when the stored value has the wrong type (hand-edited file).
            if isinstance(default, bool) != isinstance(v, bool):
                continue
            if isinstance(default, (int, float)) and not isinstance(v, (int, float)):
                continue
            if isinstance(default, str) and not isinstance(v, str):
                continue
            setattr(cfg, k, v)
        cfg._sanitize()
        return cfg

    def _sanitize(self) -> None:
        if self.hotkey not in HOTKEYS:
            self.hotkey = "right_ctrl"
        if self.engine not in ENGINES:
            self.engine = "auto"
        if self.polish not in POLISH_MODES:
            self.polish = "claude_code"
        if self.paste_method not in PASTE_METHODS:
            self.paste_method = "clipboard"
        self.double_tap_ms = max(200, min(int(self.double_tap_ms), 800))
        self.max_record_s = max(10, min(int(self.max_record_s), 1800))

    def save(self, path: Path | None = None) -> None:
        path = path or paths.config_file()
        data = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
