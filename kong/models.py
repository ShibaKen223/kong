"""Model files: where they come from, how they are checked, where they live.

Every download is verified before it is used: a pinned SHA256 when we know it, otherwise
the SHA256 HuggingFace publishes for LFS files (X-Linked-Etag). Interrupted downloads resume.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, paths

log = logging.getLogger(__name__)

Progress = Callable[[int, int], None]  # (bytes done, bytes total)

_GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
_HF_TURBO = "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/"
_UA = f"Kong/{__version__}"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Source:
    url: str
    name: str
    size: int = 0
    sha256: str = ""
    # Optional sources may 404; a "files" model needs at least one of them.
    optional: bool = False


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    # "tar": a .tar.bz2 to unpack; "wheel": a .whl (zip) to pick files from; "files": plain files.
    kind: str
    sources: tuple[Source, ...]
    # tar/wheel: archive members to keep, saved flat under their base name.
    keep: tuple[str, ...] = ()
    approx_mb: int = 0

    @property
    def dir(self) -> Path:
        return paths.models_dir() / self.id

    def path(self, name: str) -> Path:
        return self.dir / name

    def installed(self) -> bool:
        if not (self.dir / ".ok").is_file():
            return False
        if self.kind == "files":
            required = [s.name for s in self.sources if not s.optional]
            optional = [s.name for s in self.sources if s.optional]
            if optional and not any((self.dir / n).is_file() for n in optional):
                return False
        else:
            required = [Path(k).name for k in self.keep]
        return all((self.dir / n).is_file() for n in required)


SENSEVOICE = ModelSpec(
    id="sensevoice-int8-2024-07-17",
    label="SenseVoice 語音模型",
    kind="tar",
    sources=(
        Source(
            _GH + "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
            "sensevoice.tar.bz2",
            size=163002883,
            sha256="7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
        ),
    ),
    keep=(
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/model.int8.onnx",
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/tokens.txt",
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/LICENSE",
    ),
    approx_mb=163,
)

WHISPER_TURBO = ModelSpec(
    id="faster-whisper-large-v3-turbo",
    label="Whisper large-v3-turbo 語音模型",
    kind="files",
    sources=(
        Source(_HF_TURBO + "config.json", "config.json"),
        Source(_HF_TURBO + "preprocessor_config.json", "preprocessor_config.json"),
        Source(_HF_TURBO + "tokenizer.json", "tokenizer.json"),
        Source(_HF_TURBO + "vocabulary.json", "vocabulary.json", optional=True),
        Source(_HF_TURBO + "vocabulary.txt", "vocabulary.txt", optional=True),
        Source(_HF_TURBO + "model.bin", "model.bin"),
    ),
    approx_mb=1620,
)

_QWEN3 = "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/"
QWEN3_ASR = ModelSpec(
    id="qwen3-asr-0.6b-int8-2026-03-25",
    label="Qwen3-ASR 高準確度模型",
    kind="tar",
    sources=(
        Source(
            _GH + "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2",
            "qwen3-asr.tar.bz2",
            size=878702423,
            sha256="393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96",
        ),
    ),
    # The tokenizer files are flattened next to the models; the folder doubles as tokenizer dir.
    keep=tuple(
        _QWEN3 + n
        for n in (
            "conv_frontend.onnx",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "tokenizer/merges.txt",
            "tokenizer/tokenizer_config.json",
            "tokenizer/vocab.json",
        )
    ),
    approx_mb=880,
)

# ctranslate2's Windows wheel loads cublas64_12.dll at runtime but does not ship it.
CUBLAS = ModelSpec(
    id="cuda12-cublas-12.4.5.8",
    label="NVIDIA 顯示卡元件（cuBLAS）",
    kind="wheel",
    sources=(
        Source(
            "https://files.pythonhosted.org/packages/e2/2a/"
            "4f27ca96232e8b5269074a72e03b4e0d43aa68c9b965058b1684d07c6ff8/"
            "nvidia_cublas_cu12-12.4.5.8-py3-none-win_amd64.whl",
            "nvidia_cublas_cu12-12.4.5.8-py3-none-win_amd64.whl",
            size=396895858,
            sha256="5a796786da89203a0657eda402bcdcec6180254a8ac22d72213abc42069522dc",
        ),
    ),
    keep=("nvidia/cublas/bin/cublas64_12.dll", "nvidia/cublas/bin/cublasLt64_12.dll"),
    approx_mb=400,
)


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _hf_sha256(url: str) -> str:
    """HuggingFace answers a HEAD on /resolve/ with a redirect carrying the LFS SHA256."""
    if "huggingface.co/" not in url:
        return ""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
    try:
        resp = opener.open(req, timeout=20)
        headers = resp.headers
    except urllib.error.HTTPError as e:  # the 302 itself lands here
        headers = e.headers
    except OSError as e:
        log.warning("HEAD %s failed: %s", url, e)
        return ""
    tag = (headers.get("X-Linked-Etag") or "").strip().strip('"').lower()
    return tag if len(tag) == 64 else ""


def _fetch(src: Source, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event | None) -> None:
    part = dest.with_name(dest.name + ".part")
    expected = src.sha256 or _hf_sha256(src.url)
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(src.url, headers={"User-Agent": _UA})
    if have:
        req.add_header("Range", f"bytes={have}-")
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 404 and src.optional:
            raise FileNotFoundError(src.url) from e
        if e.code == 416 and have:
            part.unlink()
            raise DownloadError(f"{src.name}: 續傳失敗，重新下載") from e
        raise DownloadError(f"{src.name}: HTTP {e.code}") from e
    except OSError as e:
        raise DownloadError(f"{src.name}: {e}") from e

    h = hashlib.sha256()
    with resp:
        if have and resp.status != 206:
            have = 0  # server ignored Range: start over
        if have:
            with open(part, "rb") as f:
                while buf := f.read(_CHUNK):
                    h.update(buf)
        on_bytes(have)
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    buf = resp.read(_CHUNK)
                except OSError as e:
                    raise DownloadError(f"{src.name}: 連線中斷（{e}）") from e
                if not buf:
                    break
                f.write(buf)
                h.update(buf)
                have += len(buf)
                on_bytes(have)

    if src.size and have != src.size:
        raise DownloadError(f"{src.name}: 檔案不完整（{have}/{src.size}）")
    if expected and h.hexdigest() != expected:
        part.unlink()
        raise DownloadError(f"{src.name}: 檢查碼不符，檔案已刪除")
    if not expected:
        log.info("%s: no published checksum; size %d accepted", src.name, have)
    part.replace(dest)


def _extract_tar(archive: Path, out: Path, keep: tuple[str, ...]) -> None:
    want = {k: Path(k).name for k in keep}
    with tarfile.open(archive, "r:bz2") as tar:
        for m in tar:
            name = m.name.lstrip("./")
            if m.isfile() and name in want:
                src = tar.extractfile(m)
                assert src is not None
                with src, open(out / want[name], "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK)
    missing = [n for n in want.values() if not (out / n).is_file()]
    if missing:
        raise DownloadError(f"{archive.name} 裡找不到 {', '.join(missing)}")


def _extract_zip(archive: Path, out: Path, keep: tuple[str, ...]) -> None:
    with zipfile.ZipFile(archive) as z:
        for k in keep:
            try:
                src = z.open(k)
            except KeyError as e:
                raise DownloadError(f"{archive.name} 裡找不到 {k}") from e
            with src, open(out / Path(k).name, "wb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK)


def install(spec: ModelSpec, progress: Progress | None = None, cancel: threading.Event | None = None) -> None:
    """Download, verify and unpack `spec` unless it is already installed. Safe to re-run."""
    if spec.installed():
        return
    progress = progress or (lambda done, total: None)
    out = spec.dir
    out.mkdir(parents=True, exist_ok=True)
    staging = paths.models_dir() / ".downloads"
    staging.mkdir(exist_ok=True)
    total = sum(s.size for s in spec.sources) or spec.approx_mb * 1_000_000
    done = 0
    t0 = time.monotonic()

    for src in spec.sources:
        dest = (out if spec.kind == "files" else staging) / src.name
        if not dest.exists():
            for attempt in range(4):
                try:
                    _fetch(src, dest, lambda n: progress(min(done + n, total), total), cancel)
                    break
                except FileNotFoundError:
                    break
                except DownloadError as e:
                    if attempt == 3:
                        raise
                    log.warning("%s (retrying)", e)
                    time.sleep(2 ** attempt)
            if not dest.exists():
                continue  # optional and missing
        done += src.size or dest.stat().st_size
        if spec.kind == "tar":
            _extract_tar(dest, out, spec.keep)
            dest.unlink()
        elif spec.kind == "wheel":
            _extract_zip(dest, out, spec.keep)
            dest.unlink()

    (out / ".ok").write_text(spec.id, encoding="utf-8")
    if not spec.installed():
        (out / ".ok").unlink()
        raise DownloadError(f"{spec.label} 安裝不完整")
    progress(total, total)
    log.info("installed %s in %.0fs", spec.id, time.monotonic() - t0)


def remove(spec: ModelSpec) -> None:
    shutil.rmtree(spec.dir, ignore_errors=True)
