"""Command line for testing and support (also works on the frozen Kong.exe).

    Kong.exe doctor                     診斷報告
    Kong.exe transcribe a.wav [--polish] [--engine sensevoice|whisper_gpu]
    Kong.exe polish "那個我們明天開會"
    Kong.exe download sensevoice|whisper|cublas
    Kong.exe selftest --out report.json [--wav a.wav]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback

from . import __version__, models, paths, textproc
from .config import Config

COMMANDS = ("doctor", "transcribe", "polish", "download", "selftest", "version")


def _out(text: str) -> None:
    if sys.stdout is not None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def _read_wav(path: str):
    import wave

    import numpy as np

    from .audio import resample

    with wave.open(path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit("only 16-bit PCM wav files are supported")
    a = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return resample(a, sr)


def _engine(kind: str):
    if kind == "qwen3":
        from .stt.qwen3 import Qwen3ASR

        models.install(models.QWEN3_ASR)
        return Qwen3ASR(models.QWEN3_ASR.dir)
    if kind == "whisper_gpu":
        from . import gpu
        from .stt.whisper_gpu import WhisperGPU

        g = gpu.detect()
        if g is None:
            raise SystemExit("no NVIDIA GPU found")
        models.install(models.CUBLAS)
        models.install(models.WHISPER_TURBO)
        gpu.load_cuda_libs(models.CUBLAS.dir)
        return WhisperGPU(models.WHISPER_TURBO.dir, g.compute_type)
    from .stt.sensevoice import SenseVoice

    models.install(models.SENSEVOICE)
    return SenseVoice(models.SENSEVOICE.dir)


def _polisher(cfg: Config):
    from .polish.claude_code import ClaudeCodePolisher

    p = ClaudeCodePolisher(cfg)
    p._setup()
    if not p.available:
        raise SystemExit(p.status)
    return p


def cmd_transcribe(args) -> None:
    cfg = Config.load()
    eng = _engine(args.engine)
    polisher = _polisher(cfg) if args.polish else None
    for f in args.wav:
        audio = _read_wav(f)
        t = time.monotonic()
        raw = eng.transcribe(audio)
        t_stt = time.monotonic() - t
        base = textproc.basic_cleanup(raw)
        line = f"{f}\n  辨識（{t_stt:.2f}s）：{base}"
        if polisher is not None:
            t = time.monotonic()
            out = textproc.finalize(polisher.polish(base))
            line += f"\n  整理（{time.monotonic() - t:.2f}s）：{out}"
        _out(line)
    if polisher is not None:
        polisher.shutdown()


def cmd_polish(args) -> None:
    p = _polisher(Config.load())
    time.sleep(3)
    for text in args.text:
        t = time.monotonic()
        out = textproc.finalize(p.polish(textproc.basic_cleanup(text)))
        _out(f"[{time.monotonic() - t:.2f}s] {out}")
        time.sleep(2)
    p.shutdown()


def cmd_download(args) -> None:
    spec = {"sensevoice": models.SENSEVOICE, "whisper": models.WHISPER_TURBO, "cublas": models.CUBLAS}[args.what]
    last = [0.0]

    def progress(done, total):
        if time.monotonic() - last[0] > 1 or done == total:
            last[0] = time.monotonic()
            _out(f"{spec.label}: {done / 1e6:.0f}/{total / 1e6:.0f} MB")

    models.install(spec, progress)
    _out(f"installed at {spec.dir}")


def cmd_selftest(args) -> None:
    """Checks that a frozen build carries everything it needs. Writes a JSON report."""
    report: dict = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "checks": {}}
    # Depend on the machine (a CI runner has no GPU, maybe no desktop session): reported, not fatal.
    soft = {"clipboard", "foreground", "gpu"}

    def check(name, fn):
        t = time.monotonic()
        try:
            value = fn()
            report["checks"][name] = {"ok": True, "value": value, "s": round(time.monotonic() - t, 2)}
        except BaseException as e:  # noqa: BLE001 - report everything, including SystemExit
            report["checks"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}

    check("opencc", lambda: textproc.basic_cleanup("这个软件的视频质量很好，信息很多"))
    check("numpy", lambda: __import__("numpy").__version__)
    check("sherpa_onnx", lambda: __import__("sherpa_onnx").__name__)
    check("sounddevice", lambda: __import__("sounddevice").get_portaudio_version()[1])
    check("pynput", lambda: __import__("pynput.keyboard").__name__)
    check("pystray", lambda: __import__("pystray").__name__)
    check("PIL", lambda: __import__("PIL.Image").__name__)
    check("faster_whisper", lambda: __import__("faster_whisper").__version__)
    check("ctranslate2", lambda: __import__("ctranslate2").__version__)

    def tk_window():
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.update_idletasks()
        v = r.tk.call("info", "patchlevel")
        r.destroy()
        return v

    check("tkinter", tk_window)

    def theme():
        import tkinter as tk

        from PIL import ImageTk

        from .ui import theme as kong_theme
        from .ui.icon import mic_icon
        from .ui.panel import Painter, View

        r = tk.Tk()
        r.withdraw()
        style = kong_theme.apply(r)
        ImageTk.PhotoImage(mic_icon("idle", 32), master=r)
        ImageTk.PhotoImage(Painter().render(View("listen", "聽寫中", "測試")), master=r)
        v = style.theme_use()
        r.destroy()
        return v

    check("theme", theme)
    if sys.platform == "win32":
        from . import win32

        def clip():
            win32.set_clipboard_text("Kong 剪貼簿測試")
            with win32.Clipboard() as cb:
                return cb.get_text()

        check("clipboard", clip)
        check("foreground", lambda: int(win32.GetForegroundWindow() or 0))
    check("gpu", lambda: str(__import__("kong.gpu", fromlist=["detect"]).detect()))
    if args.wav:
        def stt():
            eng = _engine("sensevoice")
            return textproc.basic_cleanup(eng.transcribe(_read_wav(args.wav)))

        check("sensevoice", stt)
        if args.qwen:

            def qwen():
                eng = _engine("qwen3")
                return textproc.basic_cleanup(eng.transcribe(_read_wav(args.wav)))

            check("qwen3", qwen)
    check("models_dir", lambda: str(paths.models_dir()))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    _out(text)
    if not all(c["ok"] for n, c in report["checks"].items() if n not in soft):
        raise SystemExit(1)


def _utf8_console() -> None:
    # Windows consoles and CI pipes default to a legacy code page that cannot print Chinese.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> None:
    _utf8_console()
    from . import log as klog

    klog.setup(to_stderr=True)
    ap = argparse.ArgumentParser(prog="Kong")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("version")
    p = sub.add_parser("transcribe")
    p.add_argument("wav", nargs="+")
    p.add_argument("--engine", default="sensevoice", choices=["sensevoice", "qwen3", "whisper_gpu"])
    p.add_argument("--polish", action="store_true")
    p = sub.add_parser("polish")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("download")
    p.add_argument("what", choices=["sensevoice", "whisper", "cublas"])
    p = sub.add_parser("selftest")
    p.add_argument("--out")
    p.add_argument("--wav")
    p.add_argument("--qwen", action="store_true", help="also download and run Qwen3-ASR (880 MB)")
    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        from . import doctor

        _out(doctor.report(Config.load()))
    elif args.cmd == "version":
        _out(__version__)
    else:
        globals()[f"cmd_{args.cmd}"](args)
