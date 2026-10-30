"""Kong's main loop: 連按兩下 → 錄音 → 辨識 → 整理 → 貼上.

Threads:
  main        Tk event loop (the panel and the main window)
  tray        pystray's message loop (the menu)
  hotkey      the low-level keyboard hook; only posts events
  controller  turns hotkey events into state changes, one at a time
  process     one per dictation: recognise, tidy, paste
  meeting     while a 會議記錄 runs: recognises it piece by piece (see meeting.py)
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from . import DISPLAY_NAME, __version__, audio, doctor, gpu, meeting, models, paths, textproc
from .config import HOTKEYS, Config
from .history import History
from .hotkey import HotkeyListener
from .paste import Result, copy, paste
from .polish import prompt
from .polish.claude_code import ClaudeCodePolisher, PolishError
from .segmenter import Segmenter
from .ui.panel import Panel
from .ui.tray import Tray

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SHOW_EVENT = "Local\\Kong-dictation-show-window"
POLISH_SAMPLE = "嗯那個我們明天呃開會的時候要討論一下這個 implementation 然後順便看一下 roadmap"


def _open_path(path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


@dataclass
class Outcome:
    """What one dictation produced. status: ok | dead_mic | silent | empty | fillers | cancelled | error."""

    status: str
    raw: str = ""
    final: str = ""
    note: str = ""
    engine: str = ""
    audio_s: float = 0.0
    t_stt: float = 0.0
    t_polish: float = 0.0


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = "loading"  # loading | idle | recording | testing | busy | meeting | saving
        self.engine = None
        self._sensevoice = None  # live preview when the main engine is too slow for it
        self.gpu: gpu.GpuInfo | None = None
        self.download_status = ""
        self.models_missing = False
        self.history = History(paths.history_file())
        self.recorder = audio.Recorder(cfg.mic_device)
        self.polisher = ClaudeCodePolisher(cfg)
        self._polisher_started = False
        self._events: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._target = None
        self._rec_started = 0.0
        self._rec_token = 0
        self._segmenter: Segmenter | None = None
        self._job_cancel = threading.Event()
        self._downloads: set[str] = set()
        self.paste_fn = paste if IS_WIN else (lambda *args: Result.CLIPBOARD)
        self.root: tk.Tk | None = None
        self.panel: Panel | None = None
        self.tray: Tray | None = None
        self.main_window = None
        self.hotkey: HotkeyListener | None = None
        self.meeting: meeting.Meeting | None = None
        self.last_meeting = None

    # --- status shown in the tray menu and the main window ------------------------------

    @property
    def engine_status(self) -> str:
        if self.engine is not None:
            return self.engine.label
        return "語音模型未安裝" if self.models_missing else "準備中…"

    @property
    def polish_status(self) -> str:
        return "不整理" if self.cfg.polish == "none" else self.polisher.status

    def hotkey_label(self) -> str:
        return HOTKEYS.get(self.cfg.hotkey, "右 Ctrl")

    def headline(self) -> tuple[str, str]:
        hk = self.hotkey_label()
        if self.models_missing:
            return "語音模型沒有裝好", "檢查網路後按「重新下載語音模型」。"
        if self.state == "loading":
            return "準備中…", self.download_status or "載入語音模型中，請稍候。"
        if self.state == "recording":
            return "聽寫中", f"講完按一下「{hk}」，Esc 取消。"
        if self.state == "testing":
            return "試講中", "講完按「停止」。"
        if self.state == "busy":
            return "處理中…", ""
        if self.state == "meeting" and self.meeting is not None:
            m = self.meeting
            return "會議記錄中", f"已錄 {meeting.stamp(m.elapsed())}，轉成 {m.chars:,} 字。結束時在「會議記錄」按「結束並整理」。"
        if self.state == "saving":
            return "整理會議記錄中…", "辨識最後一段、請 Claude 寫重點，好了會打開檔案。"
        return "準備好了", f"在任何文字框連按兩下「{hk}」開始說話，講完再按一下。Esc 可以取消。"

    def _set_state(self, state: str, tooltip: str | None = None) -> None:
        self.state = state
        tray_state = {
            "idle": "idle", "recording": "recording", "testing": "recording", "meeting": "recording",
            "busy": "busy", "saving": "busy",
        }.get(state, "loading")
        if self.tray is not None:
            self.tray.set_state("error" if self.models_missing else tray_state, tooltip or self.headline()[0])

    def _idle_tooltip(self) -> str:
        return f"連按兩下{self.hotkey_label()}開始說話"

    # --- lifecycle -----------------------------------------------------------------------

    def run(self, show_window: bool = True) -> None:
        from .ui.main_window import MainWindow

        self.root = tk.Tk()
        self.root.title(DISPLAY_NAME)
        self.panel = Panel(self.root)
        self.main_window = MainWindow(self, self.root)
        self.tray = Tray(self)
        self.tray.start()
        self.hotkey = HotkeyListener(
            self.cfg.hotkey,
            self.cfg.double_tap_ms,
            on_double=lambda: self._events.put("double"),
            on_tap=lambda: self._events.put("tap"),
            on_esc=lambda: self._events.put("esc"),
        )
        self.hotkey.start()
        if IS_WIN:
            threading.Thread(target=self._wait_for_show_requests, name="show-event", daemon=True).start()
        threading.Thread(target=self._controller, name="controller", daemon=True).start()
        threading.Thread(target=self._startup, name="startup", daemon=True).start()
        if show_window:
            self.show_main()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def show_main(self) -> None:
        if self.panel is not None and self.main_window is not None:
            self.panel.call(self.main_window.show)

    def _wait_for_show_requests(self) -> None:
        """Starting Kong.exe again signals this event instead of opening a second copy."""
        from . import win32

        handle = win32.create_event(SHOW_EVENT)
        while handle:
            win32.wait_event(handle)
            self.show_main()

    def quit(self) -> None:
        if self.panel is not None and self.root is not None:
            self.panel.call(self.root.quit)

    def _shutdown(self) -> None:
        log.info("shutting down")
        for step in (
            self._save_meeting_on_exit,
            lambda: self.hotkey and self.hotkey.stop(),
            self.polisher.shutdown,
            self.recorder.cancel,
            lambda: self.tray and self.tray.stop(),
        ):
            try:
                step()
            except Exception:
                log.exception("shutdown step failed")

    # --- startup: models, engines, Claude Code ------------------------------------------

    def _startup(self) -> None:
        prompt.ensure_glossary_file(paths.glossary_file())
        if self.cfg.polish == "claude_code":
            self._start_polisher()
        self.gpu = gpu.detect()
        log.info("gpu: %s", self.gpu)
        self._setup_engine()
        if self.engine is None:
            return
        if self.cfg.engine == "qwen3" and not models.QWEN3_ASR.installed():
            self._start_download("qwen3")  # an earlier download was interrupted
        self._maybe_offer_gpu()

    def _start_polisher(self) -> None:
        if not self._polisher_started:
            self._polisher_started = True
            self.polisher.start()

    def _progress(self, label: str, on_panel: bool = False):
        last = [0.0]

        def cb(done: int, total: int) -> None:
            now = time.monotonic()
            if now - last[0] < 0.5 and done < total:
                return
            last[0] = now
            pct = int(100 * done / total) if total else 0
            self.download_status = f"下載{label}：{done / 1e6:.0f} / {total / 1e6:.0f} MB（{pct}%）"
            if self.tray is not None:
                self.tray.set_state("loading" if self.engine is None else self.tray.state, self.download_status)
            if on_panel and self.panel is not None:
                self.panel.set_text(self.download_status)

        return cb

    def _setup_engine(self) -> None:
        first_run = not models.SENSEVOICE.installed()
        try:
            if first_run:
                self._set_state("loading", "下載語音模型中")
                self.panel.show("info", "第一次使用：下載語音模型", "SenseVoice（163 MB），下載完就能離線辨識。", hide_after=None)
                models.install(models.SENSEVOICE, progress=self._progress("語音模型", on_panel=True))
            else:
                self._set_state("loading", "載入語音模型中")
            self.models_missing = False
            self.engine = self._load_preferred_engine()
            self.download_status = ""
        except Exception as e:
            log.exception("speech model setup failed")
            self.models_missing = True
            self.download_status = ""
            self._set_state("loading", "語音模型未安裝")
            self.panel.show(
                "error",
                "語音模型下載失敗" if isinstance(e, models.DownloadError) else "語音模型載入失敗",
                str(e)[:200],
                hint="檢查網路後，在 Kong 主視窗按「重新下載語音模型」",
                hide_after=10,
            )
            self.tray.refresh()
            return
        with self._lock:
            if self.state == "loading":
                self._set_state("idle", self._idle_tooltip())
        self.tray.refresh()
        if first_run:
            self.panel.show(
                "ok",
                "準備好了",
                f"在任何文字框連按兩下「{self.hotkey_label()}」開始說話，講完再按一下。",
                hint="設定都在 Kong 主視窗（點右下角系統匣的 Kong 圖示）",
                hide_after=8,
            )

    def retry_setup(self) -> None:
        threading.Thread(target=self._setup_engine, name="setup", daemon=True).start()

    def _get_sensevoice(self):
        if self._sensevoice is None:
            from .stt.sensevoice import SenseVoice

            self._sensevoice = SenseVoice(models.SENSEVOICE.dir)
        return self._sensevoice

    def gpu_ready(self) -> bool:
        return models.WHISPER_TURBO.installed() and models.CUBLAS.installed()

    def _wanted_engine(self) -> str:
        cfg, g = self.cfg, self.gpu
        if cfg.engine == "auto":
            return "whisper_gpu" if cfg.gpu_enabled and g is not None and g.usable else "sensevoice"
        return cfg.engine

    def _load_preferred_engine(self):
        want = self._wanted_engine()
        if want == "whisper_gpu" and self.gpu is not None and self.gpu.usable and self.gpu_ready():
            try:
                return self._load_whisper()
            except Exception:
                log.exception("Whisper on GPU failed; falling back to SenseVoice")
        if want == "qwen3" and models.QWEN3_ASR.installed():
            try:
                from .stt.qwen3 import Qwen3ASR

                engine = Qwen3ASR(models.QWEN3_ASR.dir)
                self._get_sensevoice()  # for the live preview
                return engine
            except Exception:
                log.exception("Qwen3-ASR failed; falling back to SenseVoice")
        return self._get_sensevoice()

    def _load_whisper(self):
        assert self.gpu is not None
        gpu.load_cuda_libs(models.CUBLAS.dir)
        from .stt.whisper_gpu import WhisperGPU

        return WhisperGPU(models.WHISPER_TURBO.dir, self.gpu.compute_type)

    def _maybe_offer_gpu(self) -> None:
        g, cfg = self.gpu, self.cfg
        if g is None:
            return
        if not g.usable:
            log.info("GPU not used: driver_ok=%s vram_ok=%s", g.driver_ok, g.vram_ok)
            return
        if cfg.gpu_enabled and not self.gpu_ready() and cfg.engine in ("auto", "whisper_gpu"):
            self._start_download("whisper")  # an earlier download was interrupted
            return
        if cfg.engine != "auto" or cfg.gpu_offer_answered:
            return
        yes = False
        if IS_WIN:
            from . import win32

            yes = win32.message_box(
                f"偵測到 NVIDIA 顯示卡：{g.describe()}。\n\n"
                "要下載 Whisper 模型嗎？這是 Talky 用的同一個模型，中英夾雜、口音都準很多。\n"
                "需要下載約 2 GB（語音模型 1.6 GB＋顯示卡元件 0.4 GB），下載時可以照常使用。\n\n"
                "之後也可以在 Kong 主視窗的「設定」切換。",
                DISPLAY_NAME,
                yes_no=True,
            )
        cfg.gpu_offer_answered = True
        cfg.gpu_enabled = yes
        cfg.save()
        if yes:
            self._start_download("whisper")

    def _start_download(self, kind: str) -> None:
        """kind: "whisper" (cuBLAS + Whisper model) or "qwen3". Switches engine when done."""
        with self._lock:
            if kind in self._downloads:
                return
            self._downloads.add(kind)
        specs = (models.CUBLAS, models.WHISPER_TURBO) if kind == "whisper" else (models.QWEN3_ASR,)

        def work():
            try:
                for spec in specs:
                    models.install(spec, progress=self._progress(spec.label))
                self.download_status = "載入模型中…"
                self.tray.refresh()
                if self._wanted_engine() == ("whisper_gpu" if kind == "whisper" else "qwen3"):
                    self._reload_engine()
            except Exception as e:
                log.exception("%s download failed", kind)
                self.panel.show("warn", "下載模型失敗", str(e)[:200], hint="先繼續用目前的辨識引擎；可以到主視窗的「設定」重試", hide_after=8)
            finally:
                self._downloads.discard(kind)
                self.download_status = ""
                self.tray.refresh()

        threading.Thread(target=work, name=f"download-{kind}", daemon=True).start()

    def downloading(self, kind: str) -> bool:
        return kind in self._downloads

    def _reload_engine(self) -> None:
        try:
            self.engine = self._load_preferred_engine()
            self.panel.show("info", f"辨識引擎：{self.engine.label}", hide_after=2.5)
        except Exception as e:
            log.exception("engine reload failed")
            self.panel.show("error", "切換辨識引擎失敗", str(e)[:200], hide_after=6)
        self.tray.refresh()

    # --- hotkey events ---------------------------------------------------------------

    def _controller(self) -> None:
        while True:
            ev = self._events.get()
            try:
                with self._lock:
                    if ev == "double":
                        self._on_double()
                    elif ev == "tap":
                        if self.state == "recording" and time.monotonic() - self._rec_started > 0.35:
                            self._stop_recording()
                    elif ev == "esc":
                        self._on_esc()
                    elif ev == f"timeout:{self._rec_token}" and self.state in ("recording", "testing"):
                        if self.state == "recording":
                            self._stop_recording()
                    elif ev == "meeting_limit" and self.state == "meeting":
                        self.stop_meeting()
            except Exception:
                log.exception("handling %s failed", ev)

    def _on_double(self) -> None:
        if self.state in ("meeting", "saving"):
            self.panel.show("info", "會議記錄中", "會議記錄和聽寫共用麥克風，結束會議記錄後才能聽寫。", hide_after=3)
        elif self.state == "idle":
            self._start_recording()
        elif self.state == "recording":
            self._stop_recording()
        elif self.state == "loading":
            if self.models_missing:
                self.panel.show("error", "語音模型還沒裝好", hint="在 Kong 主視窗按「重新下載語音模型」", hide_after=4)
            else:
                self.panel.show("info", "還在準備語音模型", self.download_status or "請稍候幾秒", hide_after=2.5)

    def _cancel_recording(self) -> None:
        self.recorder.cancel()
        self._rec_token += 1
        if self._segmenter is not None:
            self._segmenter.cancel()
            self._segmenter = None

    def _on_esc(self) -> None:
        if self.state in ("recording", "testing"):
            self._cancel_recording()
            self._set_state("idle", self._idle_tooltip())
            self.panel.show("info", "已取消", hide_after=0.8)
        elif self.state == "busy":
            self._job_cancel.set()
            self._set_state("idle", self._idle_tooltip())
            self.panel.show("info", "已取消", hide_after=0.8)

    def _open_mic(self) -> str | None:
        """Start recording; returns an error message instead of raising."""
        try:
            self.recorder.device = self.cfg.mic_device
            self.recorder.start()
            return None
        except Exception as e:
            log.exception("mic failed")
            return str(e)[:150]

    def _begin(self, state: str) -> int:
        """Common start of a dictation or a test: preview, background recognition, time limit."""
        engine = self.engine
        self._rec_started = time.monotonic()
        self._rec_token += 1
        token = self._rec_token
        self._set_state(state, "聽寫中")
        self._segmenter = None
        if engine.incremental:
            self._segmenter = Segmenter(engine, prompt.load_glossary(paths.glossary_file()))
            threading.Thread(target=self._feed_segmenter, args=(token, self._segmenter), name="segments", daemon=True).start()
        preview = engine if engine.live_preview else self._sensevoice
        if self.cfg.live_preview and state == "recording" and preview is not None:
            threading.Thread(target=self._preview, args=(token, preview), name="preview", daemon=True).start()
        t = threading.Timer(self.cfg.max_record_s, lambda: self._events.put(f"timeout:{token}"))
        t.daemon = True
        t.start()
        return token

    def _start_recording(self) -> None:
        if self.engine is None:
            return
        if IS_WIN:
            from . import win32

            self._target = win32.GetForegroundWindow()
        err = self._open_mic()
        if err:
            self.panel.show("error", "麥克風打不開", err, hint="檢查 Windows 設定 → 隱私權與安全性 → 麥克風", hide_after=6)
            return
        self._begin("recording")
        self.panel.show_listening(lambda: self.recorder.level, f"講完按一下「{self.hotkey_label()}」・Esc 取消")

    def _active(self, token: int) -> bool:
        return self.state in ("recording", "testing") and self._rec_token == token

    def _preview(self, token: int, engine) -> None:
        interval, shown = 0.9, ""
        while self._active(token):
            time.sleep(interval)
            if not self._active(token) or self.recorder.elapsed() < 0.7:
                continue
            seg = self._segmenter
            if seg is not None and seg.busy:
                continue  # leave the CPU to the real recognition
            clip = audio.trim_silence(self.recorder.snapshot(last_s=28))
            if len(clip) < audio.SR // 2:
                continue
            t = time.monotonic()
            try:
                text = engine.transcribe(clip)
            except Exception:
                log.exception("live preview failed")
                return
            dt = time.monotonic() - t
            if dt > interval * 0.6:
                interval = min(4.0, dt * 2)  # slow machine: preview less often
            text = textproc.basic_cleanup(text, self.cfg.cjk_latin_space)
            if text and text != shown and self._active(token):
                shown = text
                self.panel.set_text(text)

    def _feed_segmenter(self, token: int, seg: Segmenter) -> None:
        while self._active(token):
            time.sleep(1.0)
            if self._active(token):
                seg.feed(self.recorder.snapshot())

    def _take_recording(self):
        clip = self.recorder.stop()
        self._rec_token += 1
        seg, self._segmenter = self._segmenter, None
        self._job_cancel = cancel = threading.Event()
        self._set_state("busy", "處理中")
        return clip, seg, cancel

    def _stop_recording(self) -> None:
        clip, seg, cancel = self._take_recording()
        threading.Thread(
            target=self._process, args=(clip, self._target, self.engine, seg, cancel), name="process", daemon=True
        ).start()

    # --- one dictation -----------------------------------------------------------------

    def _recognize(self, clip, engine, seg: Segmenter | None, cancel: threading.Event, on_panel: bool) -> Outcome:
        cfg = self.cfg
        sp = cfg.cjk_latin_space
        out = Outcome("ok", engine=engine.label, audio_s=len(clip) / audio.SR)
        if audio.is_dead_mic(clip):
            out.status = "dead_mic"
            return out
        trimmed = audio.trim_silence(clip)
        if audio.is_silent(trimmed):
            if seg is not None:
                seg.cancel()
            out.status = "silent"
            return out
        if on_panel:
            self.panel.show_working("辨識中")
        t0 = time.monotonic()
        out.raw = seg.finish(clip) if seg is not None else engine.transcribe(trimmed, prompt.load_glossary(paths.glossary_file()))
        out.t_stt = time.monotonic() - t0
        if cancel.is_set():
            out.status = "cancelled"
            return out
        base = textproc.basic_cleanup(out.raw, sp)
        if not base:
            out.status = "empty"
            return out
        out.final = base
        t1 = time.monotonic()
        if cfg.polish == "claude_code":
            if not self.polisher.available:
                out.note = self.polisher.status
            else:
                if on_panel:
                    self.panel.show_working("整理中", base)

                def live(s: str) -> None:
                    if on_panel and not cancel.is_set():
                        self.panel.set_text(textproc.finalize(s, sp))

                try:
                    out.final = textproc.finalize(self.polisher.polish(base, on_text=live), sp)
                except PolishError as e:
                    out.note = str(e)
                except Exception as e:
                    log.exception("polish failed")
                    out.note = f"整理失敗：{e}"
        out.t_polish = time.monotonic() - t1
        if cancel.is_set():
            out.status = "cancelled"
        elif not out.final:
            out.status = "fillers"
        return out

    def _finish(self, cancel: threading.Event, kind: str, title: str, text: str = "", hint: str = "", hide: float = 2.0) -> None:
        with self._lock:
            if cancel is self._job_cancel and self.state == "busy":
                self._set_state("idle", self._idle_tooltip())
        if not cancel.is_set():
            self.panel.show(kind, title, text, hint, hide_after=hide)

    def _process(self, clip, target, engine, seg, cancel: threading.Event) -> None:
        cfg = self.cfg
        t0 = time.monotonic()
        try:
            o = self._recognize(clip, engine, seg, cancel, on_panel=True)
            if o.status == "cancelled":
                return
            if o.status == "dead_mic":
                self._finish(
                    cancel,
                    "error",
                    "麥克風沒有聲音",
                    "Windows 可能沒讓桌面程式使用麥克風：設定 → 隱私權與安全性 → 麥克風，打開「讓桌面應用程式存取您的麥克風」。",
                    hide=10,
                )
                return
            if o.status in ("silent", "empty", "fillers"):
                msg = {"silent": "沒有聽到聲音", "empty": "沒有聽到內容", "fillers": "只有贅字，沒有要貼的內容"}[o.status]
                self._finish(cancel, "info", msg, hide=1.4)
                return

            result = self.paste_fn(o.final, target, cfg.paste_method, cfg.restore_clipboard)
            self.history.add(o.final)
            log.info(
                "dictation: audio %.1fs, stt %.2fs (%s%s), polish %.2fs, total %.2fs, %s%s",
                o.audio_s, o.t_stt, engine.id, f", {seg.submitted} segments" if seg else "",
                o.t_polish, time.monotonic() - t0, result.value, f", raw pasted: {o.note}" if o.note else "",
            )
            if cfg.log_text:
                log.info("raw: %s | final: %s", o.raw, o.final)
            if result is Result.CLIPBOARD:
                self._finish(cancel, "warn", "已放進剪貼簿，請按 Ctrl+V", o.final,
                             hint="這個視窗不接受模擬按鍵（例如以系統管理員身分執行的程式）", hide=6)
            elif o.note:
                self._finish(cancel, "warn", "已貼上原稿（沒有整理）", o.final, hint=o.note, hide=8)
            else:
                self._finish(cancel, "ok", "已貼上", o.final, hide=1.6)
        except Exception as e:
            log.exception("dictation failed")
            self._finish(cancel, "error", "出錯了", str(e)[:200], hint="可以在 Kong 主視窗匯出診斷檔", hide=8)

    # --- 會議記錄 ---------------------------------------------------------------------------

    def start_meeting(self) -> str | None:
        """Returns an error message, or None once recording has started."""
        with self._lock:
            if self.engine is None:
                return "語音模型還沒準備好"
            if self.state != "idle":
                return "Kong 正在忙，稍後再試"
            err = self._open_mic()
            if err:
                return f"麥克風打不開：{err}"
            cfg = self.cfg
            try:
                self.meeting = meeting.Meeting(
                    self.recorder,
                    self.engine,
                    paths.meetings_dir(cfg.meeting_dir),
                    glossary=prompt.load_glossary(paths.glossary_file()),
                    cjk_space=cfg.cjk_latin_space,
                    keep_audio=cfg.meeting_keep_audio,
                    max_s=cfg.meeting_max_hours * 3600,
                    on_limit=lambda: self._events.put("meeting_limit"),
                )
            except OSError as e:
                self.recorder.cancel()
                return f"會議記錄資料夾無法寫入：{e}"
            self.meeting.start()
            self._set_state("meeting", "會議記錄中")
            log.info("meeting started (%s)", self.engine.id)
        if cfg.polish == "claude_code":
            self._start_polisher()
        self.panel.show(
            "info", "會議記錄開始", "邊錄邊轉成文字，結束時用 Claude 整理重點，存成檔案。",
            hint="錄音期間不能聽寫・結束：主視窗「會議記錄」或系統匣選單", hide_after=5,
        )
        self.tray.refresh()
        return None

    def stop_meeting(self) -> None:
        with self._lock:
            m = self.meeting
            if m is None or self.state != "meeting":
                return
            self._set_state("saving", "整理會議記錄中")
        self.tray.refresh()
        threading.Thread(target=self._finish_meeting, args=(m,), name="meeting-save", daemon=True).start()

    def _finish_meeting(self, m: meeting.Meeting) -> None:
        notes, err, path = "", "", None
        try:
            self.panel.show_working("會議記錄：辨識最後一段", f"已轉成 {m.chars:,} 字", hint="這段時間不能聽寫")
            m.finish()
            if not m.lines:
                err = "沒有辨識到內容"
            elif self.cfg.polish != "claude_code":
                err = "整理方式是「不整理」"
            else:
                self._start_polisher()
                for _ in range(60):  # finding Claude Code and starting it can take a few seconds
                    if self.polisher.available or not self.polisher.status.startswith("尋找"):
                        break
                    time.sleep(0.25)
                if not self.polisher.available:
                    err = self.polisher.status
                else:
                    self.panel.show_working("請 Claude 整理會議重點", f"{m.chars:,} 字・長的會議要一兩分鐘", hint="這段時間不能聽寫")
                    try:
                        notes = self.polisher.complete(
                            prompt.meeting_prompt(m.glossary),
                            prompt.meeting_message(m.transcript()),
                            meeting.notes_timeout(m.chars),
                        )
                    except PolishError as e:
                        err = str(e)
            path = m.write(notes, err, self.engine.label if self.engine else "")
            log.info("meeting saved: %.0f s, %d chars, notes %s", m.elapsed(), m.chars, "ok" if notes else err)
        except Exception as e:
            log.exception("saving the meeting failed")
            err = err or str(e)
            try:
                path = m.write(notes, err)
            except Exception:
                log.exception("writing the meeting file failed; the running transcript is kept")
        with self._lock:
            self.meeting = None
            if self.state == "saving":
                self._set_state("idle", self._idle_tooltip())
        self.tray.refresh()
        self.last_meeting = path
        if path is None:
            self.panel.show("error", "會議記錄存檔失敗", err[:200], hint="逐字稿在「Kong 會議記錄」資料夾的（錄音中）.txt", hide_after=10)
            return
        if notes:
            self.panel.show("ok", "會議記錄已存好", path.name, hint="已經打開檔案", hide_after=5)
        else:
            self.panel.show("warn", "會議記錄已存好（沒有整理重點）", path.name, hint=err, hide_after=8)
        _open_path(path)

    def _save_meeting_on_exit(self) -> None:
        """Quitting during a meeting keeps the transcript (without Claude's notes)."""
        m, self.meeting = self.meeting, None
        if m is not None:
            m.finish()
            m.write(note_error="會議還在錄的時候結束了 Kong", engine_label=self.engine.label if self.engine else "")

    def open_meetings(self) -> None:
        _open_path(paths.meetings_dir(self.cfg.meeting_dir))

    # --- tests from the main window (callbacks run on worker threads) -------------------

    def start_test(self) -> str | None:
        """Record a test sentence that is shown, not pasted. Returns an error message or None."""
        with self._lock:
            if self.engine is None:
                return "語音模型還沒準備好"
            if self.state != "idle":
                return "Kong 正在忙，稍後再試"
            err = self._open_mic()
            if err:
                return f"麥克風打不開：{err}"
            self._begin("testing")
            return None

    def stop_test(self, on_done: Callable[[Outcome], None]) -> None:
        with self._lock:
            if self.state != "testing":
                return
            engine = self.engine
            clip, seg, cancel = self._take_recording()

        def work():
            try:
                o = self._recognize(clip, engine, seg, cancel, on_panel=False)
            except Exception as e:
                log.exception("test dictation failed")
                o = Outcome("error", note=str(e), engine=engine.label)
            with self._lock:
                if cancel is self._job_cancel and self.state == "busy":
                    self._set_state("idle", self._idle_tooltip())
            on_done(o)

        threading.Thread(target=work, name="test", daemon=True).start()

    def test_polish(self, on_done: Callable[[bool, str], None]) -> None:
        def work():
            if self.cfg.polish != "claude_code":
                on_done(False, "整理方式目前是「不整理」，到「設定」改成 Claude Code。")
                return
            self._start_polisher()
            for _ in range(60):  # finding Claude Code and starting it can take a few seconds
                if self.polisher.available or not self.polisher.status.startswith("尋找"):
                    break
                time.sleep(0.25)
            if not self.polisher.available:
                on_done(False, self.polisher.status)
                return
            t = time.monotonic()
            try:
                out = textproc.finalize(self.polisher.polish(textproc.basic_cleanup(POLISH_SAMPLE)), self.cfg.cjk_latin_space)
                on_done(True, f"{out}（{time.monotonic() - t:.1f} 秒）")
            except PolishError as e:
                on_done(False, str(e))
            except Exception as e:
                log.exception("polish test failed")
                on_done(False, f"整理失敗：{e}")

        threading.Thread(target=work, name="polish-test", daemon=True).start()

    # --- settings (called from the tray thread or the Tk thread) ---------------------------

    def set_option(self, key: str, value) -> None:
        cfg = self.cfg
        if getattr(cfg, key) == value:
            return
        setattr(cfg, key, value)
        cfg._sanitize()
        cfg.save()
        if key == "hotkey":
            self.hotkey.set_key(cfg.hotkey, cfg.double_tap_ms)
            self.panel.show("info", f"快捷鍵：連按兩下「{self.hotkey_label()}」", hide_after=2.5)
        elif key in ("polish", "claude_model"):
            if cfg.polish == "claude_code":
                if self._polisher_started:
                    self.polisher.reconfigure(cfg)
                else:
                    self._start_polisher()
        elif key == "engine":
            if value == "whisper_gpu" and not self.gpu_ready():
                cfg.gpu_enabled = True
                cfg.gpu_offer_answered = True
                cfg.save()
                self.panel.show("info", "開始下載 Whisper 模型（約 2 GB）", "下載完會自動切換，期間照常使用目前的引擎。", hide_after=4)
                self._start_download("whisper")
            elif value == "qwen3" and not models.QWEN3_ASR.installed():
                self.panel.show("info", "開始下載 Qwen3-ASR（約 880 MB）", "下載完會自動切換，期間照常使用目前的引擎。", hide_after=4)
                self._start_download("qwen3")
            else:
                threading.Thread(target=self._reload_engine, name="reload", daemon=True).start()
        self.tray.refresh()

    def copy_text(self, text: str) -> None:
        if text and IS_WIN:
            copy(text)
            self.panel.show("ok", "已複製", text, hide_after=1.6)

    def copy_last(self) -> None:
        self.copy_text(self.history.last() or "")

    def open_history(self) -> None:
        f = paths.data_dir() / "最近說過的話.txt"
        f.write_text(self.history.as_text(), encoding="utf-8-sig")
        _open_path(f)

    def open_glossary(self) -> None:
        f = paths.glossary_file()
        prompt.ensure_glossary_file(f)
        _open_path(f)

    def open_logs(self) -> None:
        _open_path(paths.logs_dir())

    def export_diagnostics(self) -> str:
        state = {
            "state": self.state,
            "engine": self.engine_status,
            "polish": self.polish_status,
            "claude_version": self.polisher.version,
            "download": self.download_status,
        }
        f = paths.desktop_dir() / f"Kong-診斷-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        f.write_text(doctor.report(self.cfg, state), encoding="utf-8-sig")
        self.panel.show("ok", "診斷檔已存到桌面", f.name, hint="回報問題時附上這個檔案（不含你說過的話）", hide_after=5)
        return str(f)

    # --- start with Windows -------------------------------------------------------------

    @staticmethod
    def autostart_enabled() -> bool:
        if not IS_WIN:
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "Kong")
                return True
        except OSError:
            return False

    def toggle_autostart(self) -> None:
        if not IS_WIN or not getattr(sys, "frozen", False):
            self.panel.show("info", "只有安裝版可以設定開機啟動", hide_after=2.5)
            return
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if self.autostart_enabled():
                winreg.DeleteValue(k, "Kong")
            else:
                # Started at sign-in: stay in the tray instead of opening the window.
                winreg.SetValueEx(k, "Kong", 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        self.tray.refresh()


def _install_crash_logging() -> None:
    def hook(exc_type, exc, tb):
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda args: log.critical(
        "uncaught exception in %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # A windowed build has no console: libraries that print must not crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from . import log as klog

    klog.setup(debug=os.environ.get("KONG_DEBUG") == "1", to_stderr=not getattr(sys, "frozen", False))
    _install_crash_logging()
    log.info("Kong %s starting on %s", __version__, sys.platform)
    if IS_WIN:
        from . import win32

        if not win32.single_instance("Local\\Kong-dictation-instance"):
            # Already running: ask that copy to show its window, and leave.
            if not win32.signal_event(SHOW_EVENT):
                win32.message_box("Kong 已經在執行了：看右下角系統匣的麥克風圖示。", DISPLAY_NAME)
            return
        win32.set_dpi_aware()
    App(Config.load()).run(show_window="--background" not in argv)
