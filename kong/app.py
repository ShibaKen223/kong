"""Kong's main loop: 連按兩下 → 錄音 → 辨識 → 整理 → 貼上.

Threads:
  main        Tk event loop (the panel)
  tray        pystray's message loop (the menu)
  hotkey      the low-level keyboard hook; only posts events
  controller  turns hotkey events into state changes, one at a time
  process     one per dictation: transcribe, tidy, paste
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

from . import DISPLAY_NAME, __version__, audio, doctor, gpu, models, paths, textproc
from .config import HOTKEYS, Config
from .history import History
from .hotkey import HotkeyListener
from .paste import Result, copy, paste
from .polish import prompt
from .polish.claude_code import ClaudeCodePolisher, PolishError
from .ui.panel import Panel
from .ui.tray import Tray

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _open_path(path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = "loading"  # loading | idle | recording | busy
        self.engine = None
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
        self._job_cancel = threading.Event()
        self._gpu_setup_running = False
        self.paste_fn = paste if IS_WIN else (lambda *args: Result.CLIPBOARD)
        self.root: tk.Tk | None = None
        self.panel: Panel | None = None
        self.tray: Tray | None = None
        self.hotkey: HotkeyListener | None = None

    # --- status shown in the tray menu -----------------------------------------------------

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

    def _set_state(self, state: str, tooltip: str) -> None:
        self.state = state
        tray_state = {"idle": "idle", "recording": "recording", "busy": "busy"}.get(state, "loading")
        if self.tray is not None:
            self.tray.set_state("error" if self.models_missing else tray_state, tooltip)

    # --- lifecycle -----------------------------------------------------------------------

    def run(self) -> None:
        self.root = tk.Tk()
        self.root.title(DISPLAY_NAME)
        self.panel = Panel(self.root)
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
        threading.Thread(target=self._controller, name="controller", daemon=True).start()
        threading.Thread(target=self._startup, name="startup", daemon=True).start()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def quit(self) -> None:
        if self.panel is not None and self.root is not None:
            self.panel.call(self.root.quit)

    def _shutdown(self) -> None:
        log.info("shutting down")
        for step in (
            lambda: self.hotkey and self.hotkey.stop(),
            self.polisher.shutdown,
            self.recorder.cancel,
            lambda: self.tray and self.tray.stop(),
        ):
            try:
                step()
            except Exception:
                log.exception("shutdown step failed")

    # --- startup: models, engine, Claude Code ------------------------------------------

    def _startup(self) -> None:
        prompt.ensure_glossary_file(paths.glossary_file())
        if self.cfg.polish == "claude_code":
            self._start_polisher()
        self.gpu = gpu.detect()
        log.info("gpu: %s", self.gpu)
        self._setup_engine()
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
            self.download_status = f"下載{label}：{pct}%"
            if self.tray is not None:
                self.tray.set_state("loading" if self.engine is None else self.tray.state, self.download_status)
            if on_panel and self.panel is not None:
                self.panel.set_text(f"{label}：{done / 1e6:.0f} / {total / 1e6:.0f} MB（{pct}%）")

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
            self.engine = self._load_preferred_engine()
            self.models_missing = False
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
                hint="檢查網路後，從右下角系統匣的 Kong 圖示選「重新下載語音模型」",
                hide_after=10,
            )
            self.tray.refresh()
            return
        with self._lock:
            if self.state == "loading":
                self._set_state("idle", f"連按兩下{self.hotkey_label()}開始說話")
        self.tray.refresh()
        if first_run:
            self.panel.show(
                "ok",
                "準備好了",
                f"在任何文字框連按兩下「{self.hotkey_label()}」開始說話，講完再按一次。",
                hint="設定都在右下角系統匣的 Kong 圖示（右鍵）",
                hide_after=8,
            )

    def retry_setup(self) -> None:
        threading.Thread(target=self._setup_engine, name="setup", daemon=True).start()

    def _load_preferred_engine(self):
        want_gpu = self.cfg.engine == "whisper_gpu" or (self.cfg.engine == "auto" and self.cfg.gpu_enabled)
        g = self.gpu
        if want_gpu and g is not None and g.usable and models.WHISPER_TURBO.installed() and models.CUBLAS.installed():
            try:
                return self._load_whisper()
            except Exception:
                log.exception("Whisper on GPU failed; falling back to SenseVoice")
        from .stt.sensevoice import SenseVoice

        return SenseVoice(models.SENSEVOICE.dir)

    def _load_whisper(self):
        assert self.gpu is not None
        gpu.load_cuda_libs(models.CUBLAS.dir)
        from .stt.whisper_gpu import WhisperGPU

        return WhisperGPU(models.WHISPER_TURBO.dir, self.gpu.compute_type)

    def _maybe_offer_gpu(self) -> None:
        g, cfg = self.gpu, self.cfg
        if g is None or self.engine is None:
            return
        if not g.usable:
            log.info("GPU not used: driver_ok=%s vram_ok=%s", g.driver_ok, g.vram_ok)
            return
        gpu_ready = models.WHISPER_TURBO.installed() and models.CUBLAS.installed()
        if cfg.gpu_enabled and not gpu_ready and cfg.engine != "sensevoice":
            self._start_gpu_setup()  # an earlier download was interrupted
            return
        if cfg.engine != "auto" or cfg.gpu_offer_answered:
            return
        yes = False
        if IS_WIN:
            from . import win32

            yes = win32.message_box(
                f"偵測到 NVIDIA 顯示卡：{g.describe()}。\n\n"
                "要下載 Whisper 高準確度模型嗎？中英夾雜會準很多。\n"
                "需要下載約 2 GB（語音模型 1.6 GB＋顯示卡元件 0.4 GB），下載時可以照常使用。\n\n"
                "之後也可以在系統匣的 Kong 圖示 →「辨識引擎」切換。",
                DISPLAY_NAME,
                yes_no=True,
            )
        cfg.gpu_offer_answered = True
        cfg.gpu_enabled = yes
        cfg.save()
        if yes:
            self._start_gpu_setup()

    def _start_gpu_setup(self) -> None:
        if self._gpu_setup_running:
            return
        self._gpu_setup_running = True

        def work():
            try:
                for spec in (models.CUBLAS, models.WHISPER_TURBO):
                    models.install(spec, progress=self._progress(spec.label))
                self.download_status = "載入 Whisper 中…"
                self.tray.refresh()
                self.engine = self._load_whisper()
                self.download_status = ""
                self.panel.show("ok", "已切換到 Whisper（顯示卡）", "中英夾雜會更準。", hide_after=4)
            except Exception as e:
                log.exception("GPU setup failed")
                self.download_status = ""
                self.panel.show("warn", "顯示卡模式沒有啟用", str(e)[:200], hint="先繼續用 SenseVoice（CPU）", hide_after=8)
            finally:
                self._gpu_setup_running = False
                self.tray.refresh()

        threading.Thread(target=work, name="gpu-setup", daemon=True).start()

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
                    elif ev.startswith("timeout:") and self.state == "recording" and ev == f"timeout:{self._rec_token}":
                        self._stop_recording()
            except Exception:
                log.exception("handling %s failed", ev)

    def _on_double(self) -> None:
        if self.state == "idle":
            self._start_recording()
        elif self.state == "recording":
            self._stop_recording()
        elif self.state == "loading":
            if self.models_missing:
                self.panel.show("error", "語音模型還沒裝好", hint="從系統匣的 Kong 圖示選「重新下載語音模型」", hide_after=4)
            else:
                self.panel.show("info", "還在準備語音模型", self.download_status or "請稍候幾秒", hide_after=2.5)

    def _on_esc(self) -> None:
        if self.state == "recording":
            self.recorder.cancel()
            self._rec_token += 1
            self._set_state("idle", f"連按兩下{self.hotkey_label()}開始說話")
            self.panel.show("info", "已取消", hide_after=0.8)
        elif self.state == "busy":
            self._job_cancel.set()
            self._set_state("idle", f"連按兩下{self.hotkey_label()}開始說話")
            self.panel.show("info", "已取消", hide_after=0.8)

    def _start_recording(self) -> None:
        if self.engine is None:
            return
        if IS_WIN:
            from . import win32

            self._target = win32.GetForegroundWindow()
        try:
            self.recorder.device = self.cfg.mic_device
            self.recorder.start()
        except Exception as e:
            log.exception("mic failed")
            self.panel.show(
                "error", "麥克風打不開", str(e)[:150], hint="檢查 Windows 設定 → 隱私權與安全性 → 麥克風", hide_after=6
            )
            return
        self._rec_started = time.monotonic()
        self._rec_token += 1
        token = self._rec_token
        self._set_state("recording", "聽寫中")
        self.panel.show_listening(lambda: self.recorder.level, f"講完按一下「{self.hotkey_label()}」・Esc 取消")
        if self.cfg.live_preview and self.engine.live_preview:
            threading.Thread(target=self._preview, args=(token, self.engine), name="preview", daemon=True).start()
        t = threading.Timer(self.cfg.max_record_s, lambda: self._events.put(f"timeout:{token}"))
        t.daemon = True
        t.start()

    def _still_recording(self, token: int) -> bool:
        return self.state == "recording" and self._rec_token == token

    def _preview(self, token: int, engine) -> None:
        interval, shown = 0.9, ""
        while self._still_recording(token):
            time.sleep(interval)
            if not self._still_recording(token) or self.recorder.elapsed() < 0.7:
                continue
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
            if text and text != shown and self._still_recording(token):
                shown = text
                self.panel.set_text(text)

    def _stop_recording(self) -> None:
        clip = self.recorder.stop()
        self._rec_token += 1
        self._job_cancel = cancel = threading.Event()
        self._set_state("busy", "處理中")
        threading.Thread(
            target=self._process, args=(clip, self._target, self.engine, cancel), name="process", daemon=True
        ).start()

    # --- one dictation -----------------------------------------------------------------

    def _finish(self, cancel: threading.Event, kind: str, title: str, text: str = "", hint: str = "", hide: float = 2.0) -> None:
        with self._lock:
            if cancel is self._job_cancel and self.state == "busy":
                self._set_state("idle", f"連按兩下{self.hotkey_label()}開始說話")
        if not cancel.is_set():
            self.panel.show(kind, title, text, hint, hide_after=hide)

    def _process(self, clip, target, engine, cancel: threading.Event) -> None:
        cfg = self.cfg
        sp = cfg.cjk_latin_space
        t0 = time.monotonic()
        try:
            if audio.is_dead_mic(clip):
                self._finish(
                    cancel,
                    "error",
                    "麥克風沒有聲音",
                    "Windows 可能沒讓桌面程式使用麥克風：設定 → 隱私權與安全性 → 麥克風，打開「讓桌面應用程式存取您的麥克風」。",
                    hide=10,
                )
                return
            audio_s = len(clip) / audio.SR
            clip = audio.trim_silence(clip)
            if audio.is_silent(clip):
                self._finish(cancel, "info", "沒有聽到聲音", hide=1.2)
                return
            self.panel.show_working("辨識中")
            raw = engine.transcribe(clip, prompt.load_glossary(paths.glossary_file()))
            t_stt = time.monotonic() - t0
            if cancel.is_set():
                return
            base = textproc.basic_cleanup(raw, sp)
            if not base:
                self._finish(cancel, "info", "沒有聽到內容", hide=1.2)
                return

            final, note = base, ""
            t1 = time.monotonic()
            if cfg.polish == "claude_code":
                if not self.polisher.available:
                    note = self.polisher.status
                else:
                    self.panel.show_working("整理中", base)

                    def live(s: str) -> None:
                        if not cancel.is_set():
                            self.panel.set_text(textproc.finalize(s, sp))

                    try:
                        final = textproc.finalize(self.polisher.polish(base, on_text=live), sp)
                    except PolishError as e:
                        note = str(e)
                    except Exception as e:
                        log.exception("polish failed")
                        note = f"整理失敗：{e}"
            t_polish = time.monotonic() - t1
            if cancel.is_set():
                return
            if not final:
                self._finish(cancel, "info", "只有贅字，沒有要貼的內容", hide=1.5)
                return

            result = self.paste_fn(final, target, cfg.paste_method, cfg.restore_clipboard)
            self.history.add(final)
            log.info(
                "dictation: audio %.1fs, stt %.2fs (%s), polish %.2fs, total %.2fs, %s%s",
                audio_s, t_stt, engine.id, t_polish, time.monotonic() - t0, result.value,
                f", raw pasted: {note}" if note else "",
            )
            if cfg.log_text:
                log.info("raw: %s | final: %s", raw, final)
            if result is Result.CLIPBOARD:
                self._finish(cancel, "warn", "已放進剪貼簿，請按 Ctrl+V", final,
                             hint="這個視窗不接受模擬按鍵（例如以系統管理員身分執行的程式）", hide=6)
            elif note:
                self._finish(cancel, "warn", "已貼上原稿（沒有整理）", final, hint=note, hide=6)
            else:
                self._finish(cancel, "ok", "已貼上", final, hide=1.6)
        except Exception as e:
            log.exception("dictation failed")
            self._finish(cancel, "error", "出錯了", str(e)[:200], hint="可以從系統匣的 Kong 圖示匯出診斷檔", hide=8)

    # --- tray actions (tray thread) -------------------------------------------------------

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
            gpu_ready = models.WHISPER_TURBO.installed() and models.CUBLAS.installed()
            if value == "whisper_gpu" and not gpu_ready:
                cfg.gpu_enabled = True
                cfg.gpu_offer_answered = True
                cfg.save()
                self.panel.show("info", "開始下載 Whisper 模型（約 2 GB）", "下載完會自動切換，期間照常使用 SenseVoice。", hide_after=4)
                self._start_gpu_setup()
            else:
                threading.Thread(target=self._reload_engine, name="reload", daemon=True).start()
        self.tray.refresh()

    def _reload_engine(self) -> None:
        try:
            self.engine = self._load_preferred_engine()
            self.panel.show("info", f"辨識引擎：{self.engine.label}", hide_after=2)
        except Exception as e:
            log.exception("engine reload failed")
            self.panel.show("error", "切換辨識引擎失敗", str(e)[:200], hide_after=6)
        self.tray.refresh()

    def copy_last(self) -> None:
        text = self.history.last()
        if text and IS_WIN:
            copy(text)
            self.panel.show("ok", "已複製上一句", text, hide_after=1.6)

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

    def export_diagnostics(self) -> None:
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
                winreg.SetValueEx(k, "Kong", 0, winreg.REG_SZ, f'"{sys.executable}"')
        self.tray.refresh()


def _install_crash_logging() -> None:
    def hook(exc_type, exc, tb):
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda args: log.critical(
        "uncaught exception in %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def main() -> None:
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
            win32.message_box("Kong 已經在執行了：看右下角系統匣的麥克風圖示。", DISPLAY_NAME)
            return
        win32.set_dpi_aware()
    App(Config.load()).run()
