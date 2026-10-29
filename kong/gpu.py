"""NVIDIA GPU detection and loading the CUDA libraries faster-whisper needs on Windows."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# CUDA 12 needs at least these drivers.
_MIN_DRIVER = (527, 41) if sys.platform == "win32" else (525, 60)
_MIN_VRAM_MB = 3000


@dataclass
class GpuInfo:
    name: str
    vram_mb: int
    driver: str

    @property
    def driver_ok(self) -> bool:
        try:
            parts = tuple(int(p) for p in self.driver.split(".")[:2])
        except ValueError:
            return True
        return parts >= _MIN_DRIVER

    @property
    def vram_ok(self) -> bool:
        return self.vram_mb >= _MIN_VRAM_MB

    @property
    def usable(self) -> bool:
        return self.driver_ok and self.vram_ok

    @property
    def compute_type(self) -> str:
        return "float16" if self.vram_mb >= 6000 else "int8_float16"

    def describe(self) -> str:
        return f"{self.name}（{self.vram_mb / 1024:.0f} GB）"


def _nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if sys.platform == "win32":
        for p in (
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
            Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
        ):
            if p.is_file():
                return str(p)
    return None


def detect() -> GpuInfo | None:
    exe = _nvidia_smi()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.info("nvidia-smi failed: %s", e)
        return None
    best: GpuInfo | None = None
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            info = GpuInfo(parts[0], int(float(parts[1])), parts[2])
        except ValueError:
            continue
        if best is None or info.vram_mb > best.vram_mb:
            best = info
    return best


_dll_handles: list = []


def load_cuda_libs(dll_dir: Path) -> None:
    """Make cublas64_12.dll findable before ctranslate2 asks for it by name."""
    if sys.platform != "win32":
        return
    d = str(dll_dir)
    _dll_handles.append(os.add_dll_directory(d))
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    # Once loaded, a later LoadLibrary("cublas64_12.dll") by name returns this same module.
    for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
        _dll_handles.append(ctypes.WinDLL(str(dll_dir / name)))
