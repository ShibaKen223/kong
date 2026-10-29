"""Kong 的主視窗：狀態、試講、設定、最近說過的話、常用詞。

Opens when Kong.exe is started (also when started again while Kong is running) and from a
left click on the tray icon. Closing it only hides it: Kong keeps running in the tray.
Everything here runs on the Tk thread; the app's own threads report back through `_ui()`.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import TYPE_CHECKING

from PIL import ImageTk

from .. import DISPLAY_NAME, __version__, models, paths, textproc
from ..config import ENGINES, HOTKEYS, PASTE_METHODS, POLISH_MODES
from ..polish import prompt
from .icon import mic_icon
from .tray import CLAUDE_MODELS

if TYPE_CHECKING:
    from ..app import App, Outcome

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
FONT = "Microsoft JhengHei UI" if IS_WIN else "Noto Sans CJK TC"
REPO_URL = "https://github.com/ShibaKen223/kong"
TEST_MAX_S = 30


def _dark() -> bool:
    if not IS_WIN:
        return False
    from .. import win32

    return win32.apps_use_dark_theme()


class MainWindow:
    def __init__(self, app: "App", root: tk.Tk) -> None:
        self.app = app
        self.root = root
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title(DISPLAY_NAME)
        w.geometry("780x640")
        w.minsize(660, 560)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        self._icons = {s: ImageTk.PhotoImage(mic_icon(s, 56)) for s in ("idle", "recording", "busy", "loading", "error")}
        self._app_icon = ImageTk.PhotoImage(mic_icon("idle", 64))
        w.iconphoto(False, self._app_icon)
        self._theme()

        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=14, pady=14)
        self._build_home()
        self._build_settings()
        self._build_history()
        self._build_glossary()

        self._visible = False
        self._told_background = False
        self._testing = False
        self._test_started = 0
        self._history_count = -1
        self._tick()

    # --- look ---------------------------------------------------------------------------

    def _theme(self) -> None:
        self._is_dark = _dark()
        try:
            import sv_ttk

            sv_ttk.set_theme("dark" if self._is_dark else "light", root=self.root)
        except Exception:
            log.exception("sv-ttk theme unavailable; using the default ttk theme")
        # The theme's fonts are Segoe UI, which has no Chinese: use 微軟正黑體 at the same sizes.
        for name in tkfont.names(self.root):
            if name.startswith("SunValley") or name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                try:
                    tkfont.nametofont(name, root=self.root).configure(family=FONT)
                except tk.TclError:
                    pass
        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=(FONT, 18, "bold"))
        style.configure("Sub.TLabel", font=(FONT, 10))
        style.configure("Key.TLabel", font=(FONT, 10, "bold"))
        style.configure("Hint.TLabel", font=(FONT, 9))
        # Plain tk.Text is not themed by sv-ttk.
        self._text_colors = (
            dict(bg="#2b2b2b", fg="#f2f2f2", insertbackground="#f2f2f2", selectbackground="#3d5a80")
            if self._is_dark
            else dict(bg="#fbfbfb", fg="#1c1c1c", insertbackground="#1c1c1c", selectbackground="#cce0ff")
        )

    # --- showing ------------------------------------------------------------------------

    def show(self) -> None:
        w = self.win
        w.deiconify()
        w.state("normal")
        w.lift()
        # Windows only raises a window above the active app with a nudge like this.
        w.attributes("-topmost", True)
        w.after(300, lambda: w.attributes("-topmost", False))
        w.focus_force()
        self._visible = True
        self._refresh()

    def hide(self) -> None:
        self.win.withdraw()
        self._visible = False
        if not self._told_background:
            self._told_background = True
            self.app.panel.show(
                "info", "Kong 還在背景執行",
                f"連按兩下「{self.app.hotkey_label()}」就能說話；點右下角系統匣的 Kong 圖示可以再打開視窗。",
                hide_after=4,
            )

    def _ui(self, fn, *args) -> None:
        """Run on the Tk thread (callbacks from the app arrive on worker threads)."""
        self.app.panel.call(fn, *args)

    # --- 首頁 -----------------------------------------------------------------------------

    def _build_home(self) -> None:
        f = ttk.Frame(self.nb, padding=18)
        self.nb.add(f, text="首頁")
        head = ttk.Frame(f)
        head.pack(fill="x")
        self.h_icon = ttk.Label(head, image=self._icons["loading"])
        self.h_icon.pack(side="left", padx=(0, 14))
        txt = ttk.Frame(head)
        txt.pack(side="left", fill="x", expand=True)
        self.h_title = ttk.Label(txt, text="", style="Title.TLabel")
        self.h_title.pack(anchor="w")
        self.h_detail = ttk.Label(txt, text="", style="Sub.TLabel", wraplength=560, justify="left")
        self.h_detail.pack(anchor="w", pady=(2, 0))
        self.h_retry = ttk.Button(head, text="重新下載語音模型", command=self.app.retry_setup)

        ttk.Separator(f).pack(fill="x", pady=16)
        rows = ttk.Frame(f)
        rows.pack(fill="x")
        rows.columnconfigure(1, weight=1)
        self.r_engine = self._row(rows, 0, "語音辨識", "變更", lambda: self.nb.select(1))
        self.r_gpu = self._row(rows, 1, "顯示卡")
        self.r_polish = self._row(rows, 2, "整理", "測試整理", self._test_polish)
        self.r_gloss = self._row(rows, 3, "常用詞", "編輯", lambda: self.nb.select(3))
        self.h_tip = ttk.Label(f, text="", style="Hint.TLabel", wraplength=700, justify="left")
        self.h_tip.pack(anchor="w", pady=(10, 0))
        self.h_tip_btn = ttk.Button(f, text="改用 Qwen3-ASR", command=lambda: self.app.set_option("engine", "qwen3"))

        ttk.Separator(f).pack(fill="x", pady=16)
        ttk.Label(f, text="試講一句", style="Key.TLabel").pack(anchor="w")
        ttk.Label(f, text="結果只會顯示在下面，不會貼到別的地方。可以用來比較不同的辨識引擎。", style="Hint.TLabel").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 6))
        self.t_btn = ttk.Button(bar, text="● 開始試講", style="Accent.TButton", command=self._toggle_test)
        self.t_btn.pack(side="left")
        self.t_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.t_state.pack(side="left", padx=12)
        self.t_out = tk.Text(f, height=7, wrap="word", font=(FONT, 11), relief="flat", padx=10, pady=8, borderwidth=0,
                             **self._text_colors)
        self.t_out.pack(fill="both", expand=True)
        self._set_test_text("")

    def _row(self, parent, r: int, key: str, button: str | None = None, command=None) -> ttk.Label:
        ttk.Label(parent, text=key, style="Key.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 18), pady=5)
        val = ttk.Label(parent, text="", wraplength=470, justify="left")
        val.grid(row=r, column=1, sticky="w", pady=5)
        if button:
            ttk.Button(parent, text=button, command=command).grid(row=r, column=2, sticky="e", pady=3)
        return val

    def _set_test_text(self, text: str) -> None:
        self.t_out.configure(state="normal")
        self.t_out.delete("1.0", "end")
        self.t_out.insert("1.0", text)
        self.t_out.configure(state="disabled")

    def _toggle_test(self) -> None:
        if not self._testing:
            err = self.app.start_test()
            if err:
                self.t_state.configure(text=err)
                return
            self._testing = True
            self._test_started += 1
            token = self._test_started
            self.t_btn.configure(text="■ 停止")
            self.t_state.configure(text="錄音中…講一句話，講完按「停止」")
            self._set_test_text("")
            self.win.after(TEST_MAX_S * 1000, lambda: self._testing and self._test_started == token and self._toggle_test())
            return
        self._testing = False
        self.t_btn.configure(text="● 開始試講", state="disabled")
        self.t_state.configure(text="辨識中…")
        self.app.stop_test(lambda o: self._ui(self._show_test, o))

    def _show_test(self, o: "Outcome") -> None:
        self.t_btn.configure(state="normal")
        self.t_state.configure(text="")
        if o.status != "ok":
            msg = {
                "dead_mic": "麥克風沒有聲音：Windows 設定 → 隱私權與安全性 → 麥克風，打開「讓桌面應用程式存取您的麥克風」。",
                "silent": "沒有聽到聲音。檢查麥克風有沒有選對（Windows 設定 → 系統 → 音效 → 輸入）。",
                "empty": "沒有聽到內容。",
                "fillers": "只有贅字，沒有可貼的內容。",
                "cancelled": "已取消。",
            }.get(o.status, f"出錯了：{o.note}")
            self._set_test_text(msg)
            return
        polish = "沒有整理：" + o.note if o.note else f"整理後（{o.t_polish:.1f} 秒）"
        raw = textproc.basic_cleanup(o.raw, self.app.cfg.cjk_latin_space)
        self._set_test_text(f"辨識（{o.engine}，{o.t_stt:.1f} 秒）：\n{raw}\n\n{polish}：\n{o.final}")

    def _test_polish(self) -> None:
        self._set_test_text("測試整理中…（用一句範例口語，請 Claude Code 整理）")
        self.app.test_polish(lambda ok, msg: self._ui(self._show_polish_test, ok, msg))

    def _show_polish_test(self, ok: bool, msg: str) -> None:
        if ok:
            self._set_test_text(f"整理正常 ✓\n\n範例口語：{self.app_sample()}\n整理後：{msg}")
        else:
            self._set_test_text(
                f"整理沒有成功 ✗\n\n原因：{msg}\n\n"
                "常見解法：\n"
                "1. 安裝 Claude Code（https://code.claude.com/docs），並安裝 Git for Windows。\n"
                "2. 打開 PowerShell 執行 claude，登入你的 Claude Pro／Max 帳號一次。\n"
                "3. 回到這裡再按一次「測試整理」。"
            )

    @staticmethod
    def app_sample() -> str:
        from ..app import POLISH_SAMPLE

        return POLISH_SAMPLE

    # --- 設定 -----------------------------------------------------------------------------

    def _build_settings(self) -> None:
        outer = ttk.Frame(self.nb, padding=(18, 12))
        self.nb.add(outer, text="設定")
        cfg = self.app.cfg
        left = ttk.Frame(outer)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)

        eng = ttk.LabelFrame(left, text="辨識引擎", padding=10)
        eng.pack(fill="x")
        self.v_engine = tk.StringVar(value=cfg.engine)
        self.engine_notes: dict[str, ttk.Label] = {}
        self.engine_radios: dict[str, ttk.Radiobutton] = {}
        for key, label in ENGINES.items():
            rb = ttk.Radiobutton(eng, text=label, value=key, variable=self.v_engine,
                                 command=lambda: self.app.set_option("engine", self.v_engine.get()))
            rb.pack(anchor="w")
            self.engine_radios[key] = rb
            note = ttk.Label(eng, text="", style="Hint.TLabel")
            note.pack(anchor="w", padx=(28, 0), pady=(0, 4))
            self.engine_notes[key] = note

        pol = ttk.LabelFrame(left, text="整理（去贅字、修改口、修錯字）", padding=10)
        pol.pack(fill="x", pady=(12, 0))
        self.v_polish = tk.StringVar(value=cfg.polish)
        for key, label in POLISH_MODES.items():
            ttk.Radiobutton(pol, text=label, value=key, variable=self.v_polish,
                            command=lambda: self.app.set_option("polish", self.v_polish.get())).pack(anchor="w")
        row = ttk.Frame(pol)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Claude 模型").pack(side="left")
        self.v_model = tk.StringVar(value=CLAUDE_MODELS.get(cfg.claude_model, "Sonnet（建議）"))
        cb = ttk.Combobox(row, textvariable=self.v_model, values=list(CLAUDE_MODELS.values()), state="readonly", width=18)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.app.set_option("claude_model", self._key_of(CLAUDE_MODELS, self.v_model.get())))

        hk = ttk.LabelFrame(right, text="快捷鍵", padding=10)
        hk.pack(fill="x")
        ttk.Label(hk, text="連按兩下開始說話，講完再按一下").pack(anchor="w")
        self.v_hotkey = tk.StringVar(value=HOTKEYS[cfg.hotkey])
        hcb = ttk.Combobox(hk, textvariable=self.v_hotkey, values=list(HOTKEYS.values()), state="readonly", width=14)
        hcb.pack(anchor="w", pady=(6, 0))
        hcb.bind("<<ComboboxSelected>>", lambda e: self.app.set_option("hotkey", self._key_of(HOTKEYS, self.v_hotkey.get())))

        pst = ttk.LabelFrame(right, text="貼上", padding=10)
        pst.pack(fill="x", pady=(12, 0))
        self.v_paste = tk.StringVar(value=cfg.paste_method)
        for key, label in PASTE_METHODS.items():
            ttk.Radiobutton(pst, text=label, value=key, variable=self.v_paste,
                            command=lambda: self.app.set_option("paste_method", self.v_paste.get())).pack(anchor="w")

        gen = ttk.LabelFrame(right, text="一般", padding=10)
        gen.pack(fill="x", pady=(12, 0))
        self.v_live = tk.BooleanVar(value=cfg.live_preview)
        ttk.Checkbutton(gen, text="邊講邊顯示文字", variable=self.v_live,
                        command=lambda: self.app.set_option("live_preview", self.v_live.get())).pack(anchor="w")
        self.v_auto = tk.BooleanVar(value=self.app.autostart_enabled())
        ttk.Checkbutton(gen, text="開機時自動啟動（在背景待命）", variable=self.v_auto,
                        command=self.app.toggle_autostart).pack(anchor="w", pady=(4, 0))

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="匯出診斷檔到桌面", command=self.app.export_diagnostics).pack(fill="x")
        ttk.Button(btns, text="開啟記錄檔資料夾", command=self.app.open_logs).pack(fill="x", pady=6)
        ttk.Button(btns, text="結束 Kong", command=self.app.quit).pack(fill="x")
        ttk.Label(right, text=f"{DISPLAY_NAME} {__version__}\n{REPO_URL}", style="Hint.TLabel", justify="left").pack(anchor="w", pady=(14, 0))

    @staticmethod
    def _key_of(options: dict[str, str], label: str) -> str:
        return next((k for k, v in options.items() if v == label), next(iter(options)))

    # --- 最近說過的話 -----------------------------------------------------------------------

    def _build_history(self) -> None:
        f = ttk.Frame(self.nb, padding=18)
        self.nb.add(f, text="最近說過的話")
        ttk.Label(f, text="最近 20 句，只存在這台電腦。按兩下一句就會複製。", style="Hint.TLabel").pack(anchor="w", pady=(0, 8))
        self.hist = ttk.Treeview(f, columns=("t", "text"), show="headings", selectmode="browse")
        self.hist.heading("t", text="時間")
        self.hist.heading("text", text="內容")
        self.hist.column("t", width=150, stretch=False)
        self.hist.column("text", width=520)
        self.hist.pack(fill="both", expand=True)
        self.hist.bind("<Double-1>", lambda e: self._copy_selected())
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="複製選取的句子", command=self._copy_selected).pack(side="left")
        self._hist_texts: dict[str, str] = {}

    def _fill_history(self) -> None:
        items = self.app.history.items()
        if len(items) == self._history_count and items and self._hist_texts:
            return
        self._history_count = len(items)
        self.hist.delete(*self.hist.get_children())
        self._hist_texts.clear()
        for it in items:
            iid = self.hist.insert("", "end", values=(it.get("t", ""), it["text"].replace("\n", " ")))
            self._hist_texts[iid] = it["text"]

    def _copy_selected(self) -> None:
        sel = self.hist.selection()
        if sel:
            self.app.copy_text(self._hist_texts.get(sel[0], ""))

    # --- 常用詞 ---------------------------------------------------------------------------

    def _build_glossary(self) -> None:
        f = ttk.Frame(self.nb, padding=18)
        self.nb.add(f, text="常用詞")
        ttk.Label(
            f,
            text="一行一個：人名、公司名、產品名、專有名詞（例如 Claude、王小明）。整理時遇到發音相近的字，會優先用這些寫法。以 # 開頭的行會被忽略。",
            style="Hint.TLabel",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        self.g_text = tk.Text(f, wrap="word", font=(FONT, 11), relief="flat", padx=10, pady=8, undo=True,
                              **self._text_colors)
        self.g_text.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="儲存", style="Accent.TButton", command=self._save_glossary).pack(side="left")
        self.g_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.g_state.pack(side="left", padx=12)
        self._load_glossary()

    def _load_glossary(self) -> None:
        path = paths.glossary_file()
        prompt.ensure_glossary_file(path)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        self.g_text.delete("1.0", "end")
        self.g_text.insert("1.0", text)

    def _save_glossary(self) -> None:
        text = self.g_text.get("1.0", "end").rstrip() + "\n"
        try:
            paths.glossary_file().write_text(text, encoding="utf-8")
            n = len(prompt.load_glossary(paths.glossary_file()))
            self.g_state.configure(text=f"已儲存 {n} 個詞，下一句開始生效。")
        except OSError as e:
            self.g_state.configure(text=f"存檔失敗：{e}")

    # --- keeping it current -------------------------------------------------------------

    def _tick(self) -> None:
        if self._visible:
            try:
                self._refresh()
            except Exception:
                log.exception("main window refresh failed")
        self.win.after(700, self._tick)

    def _refresh(self) -> None:
        app, cfg = self.app, self.app.cfg
        title, detail = app.headline()
        self.h_title.configure(text=title)
        self.h_detail.configure(text=detail)
        icon = {"recording": "recording", "testing": "recording", "busy": "busy", "idle": "idle"}.get(app.state, "loading")
        self.h_icon.configure(image=self._icons["error" if app.models_missing else icon])
        if app.models_missing:
            self.h_retry.pack(side="right")
        else:
            self.h_retry.pack_forget()

        engine = app.engine_status
        if app.download_status:
            engine += f"　·　{app.download_status}"
        self.r_engine.configure(text=engine)
        g = app.gpu
        if g is None:
            self.r_gpu.configure(text="沒有偵測到 NVIDIA 顯示卡（用 CPU 辨識）")
        elif not g.usable:
            why = "顯示卡驅動太舊，請更新 NVIDIA 驅動" if not g.driver_ok else "顯示卡記憶體不到 3 GB"
            self.r_gpu.configure(text=f"{g.describe()}：{why}")
        else:
            self.r_gpu.configure(text=g.describe())
        polish = app.polish_status
        if cfg.polish == "claude_code" and app.polisher.version:
            polish += f"　·　Claude Code {app.polisher.version.split()[0]}"
        self.r_polish.configure(text=polish)
        self.r_gloss.configure(text=f"{len(prompt.load_glossary(paths.glossary_file()))} 個")

        # Nudge toward the more accurate engine when it fits this computer.
        tip = ""
        if app.engine is not None and app.engine.id == "sensevoice" and not app.downloading("qwen3"):
            if g is not None and g.usable:
                tip = "你的電腦有 NVIDIA 顯示卡：到「設定」改用 Whisper，和 Talky 同一個模型，又快又準。"
            else:
                tip = "想更準？可以改用 Qwen3-ASR（下載約 880 MB），口音、講很快、有雜音時準很多，每句多等 1～3 秒。"
        self.h_tip.configure(text=tip)
        if tip and (g is None or not g.usable):
            self.h_tip_btn.pack(anchor="w", pady=(6, 0), after=self.h_tip)
        else:
            self.h_tip_btn.pack_forget()

        self._sync_settings()
        self._fill_history()

    def _sync_settings(self) -> None:
        """The tray menu changes settings too: show what is really in effect."""
        app, cfg = self.app, self.app.cfg
        for var, value in (
            (self.v_engine, cfg.engine),
            (self.v_polish, cfg.polish),
            (self.v_paste, cfg.paste_method),
            (self.v_hotkey, HOTKEYS[cfg.hotkey]),
            (self.v_model, CLAUDE_MODELS.get(cfg.claude_model, cfg.claude_model)),
        ):
            if var.get() != value:
                var.set(value)
        if self.v_live.get() != cfg.live_preview:
            self.v_live.set(cfg.live_preview)
        auto = app.autostart_enabled()
        if self.v_auto.get() != auto:
            self.v_auto.set(auto)

        g = app.gpu
        has_gpu = g is not None and g.usable
        notes = {
            "auto": "目前：" + ("Whisper" if has_gpu and cfg.gpu_enabled and app.gpu_ready() else "SenseVoice"),
            "sensevoice": "已下載（163 MB）",
            "qwen3": "下載中…" if app.downloading("qwen3") else ("已下載" if models.QWEN3_ASR.installed() else "需下載約 880 MB"),
            "whisper_gpu": (
                "需要 NVIDIA 顯示卡（4 GB 以上）" if not has_gpu
                else "下載中…" if app.downloading("whisper")
                else "已下載" if app.gpu_ready() else "需下載約 2 GB"
            ),
        }
        for key, note in notes.items():
            self.engine_notes[key].configure(text=note)
        self.engine_radios["whisper_gpu"].configure(state="normal" if has_gpu else "disabled")
