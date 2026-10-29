"""Where Kong keeps its files.

Windows:
  設定、記錄、常用詞  %APPDATA%\\Kong
  模型（大檔）        %LOCALAPPDATA%\\Kong\\models

Set KONG_HOME to put everything under one folder (tests, portable use).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME

IS_WINDOWS = sys.platform == "win32"


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _home_override() -> Path | None:
    v = os.environ.get("KONG_HOME")
    return Path(v) if v else None


def data_dir() -> Path:
    if (h := _home_override()) is not None:
        return _ensure(h)
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return _ensure(base / APP_NAME)
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _ensure(base / APP_NAME.lower())


def models_dir() -> Path:
    if (h := _home_override()) is not None:
        return _ensure(h / "models")
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return _ensure(base / APP_NAME / "models")
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return _ensure(base / APP_NAME.lower() / "models")


def logs_dir() -> Path:
    return _ensure(data_dir() / "logs")


def config_file() -> Path:
    return data_dir() / "config.json"


def glossary_file() -> Path:
    return data_dir() / "glossary.txt"


def history_file() -> Path:
    return data_dir() / "history.json"


def claude_workdir() -> Path:
    """An empty folder Claude Code runs in, so it never picks up a project's CLAUDE.md or settings."""
    return _ensure(data_dir() / "claude-workdir")


def desktop_dir() -> Path:
    d = Path.home() / "Desktop"
    return d if d.is_dir() else Path.home()
