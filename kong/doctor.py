"""診斷檔: everything useful for a bug report, nothing secret, nothing you said."""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict

from . import __version__, gpu, models, paths
from .config import Config
from .polish.claude_code import find_claude


def report(cfg: Config, app_state: dict | None = None) -> str:
    lines = [
        f"Kong {__version__} 診斷檔  {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"系統：{platform.platform()}  Python {sys.version.split()[0]}  {'frozen' if getattr(sys, 'frozen', False) else 'source'}",
        "",
        "## 設定",
        json.dumps(asdict(cfg), ensure_ascii=False, indent=1),
        "",
        "## 顯示卡",
    ]
    g = gpu.detect()
    if g is None:
        lines.append("沒有偵測到 NVIDIA 顯示卡")
    else:
        lines.append(f"{g.name}  VRAM {g.vram_mb} MB  驅動 {g.driver}  driver_ok={g.driver_ok} vram_ok={g.vram_ok}")
    lines += ["", "## 模型"]
    for spec in (models.SENSEVOICE, models.WHISPER_TURBO, models.CUBLAS):
        lines.append(f"{spec.id}: {'已安裝' if spec.installed() else '未安裝'}  ({spec.dir})")
    lines += ["", "## Claude Code"]
    argv = find_claude(cfg.claude_path)
    lines.append(f"位置：{argv or '找不到'}")
    lines += ["", "## 麥克風"]
    try:
        from .audio import list_input_devices

        lines += [f"- {n}" for n in list_input_devices()] or ["（沒有輸入裝置）"]
    except Exception as e:
        lines.append(f"無法列出：{e}")
    if app_state:
        lines += ["", "## 目前狀態", json.dumps(app_state, ensure_ascii=False, indent=1)]
    lines += ["", "## 記錄檔（最後 150 行）"]
    log_file = paths.logs_dir() / "kong.log"
    try:
        lines += log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-150:]
    except OSError:
        lines.append("（沒有記錄檔）")
    return "\n".join(lines) + "\n"
