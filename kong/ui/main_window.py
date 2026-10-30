"""Kong 的主視窗：狀態、試講、設定、最近說過的話、常用詞。

Opens when Kong.exe is started (also when started again while Kong is running) and from a
left click on the tray icon. Closing it only hides it: Kong keeps running in the tray.
Everything here runs on the Tk thread; the app's own threads report back through `_ui()`.
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from PIL import ImageTk

from .. import DISPLAY_NAME, __version__, meeting, models, paths, textproc
from ..config import ENGINES, HOTKEYS, PASTE_METHODS, POLISH_MODES
from ..polish import prompt
from . import theme
from .icon import kong_icon
from .panel import HISTORY, Painter, View
from .tray import CLAUDE_MODELS

if TYPE_CHECKING:
    from ..app import App, Outcome

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
FONT = theme.FONT
REPO_URL = "https://github.com/ShibaKen223/kong"
TEST_MAX_S = 30
BLOCK = (168, 76)  # the state graphic in the header, at 96 dpi


class MainWindow:
    def __init__(self, app: "App", root: tk.Tk) -> None:
        self.app = app
        self.root = root
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title(DISPLAY_NAME)
        w.geometry("820x780")
        w.minsize(700, 700)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        self._app_icon = ImageTk.PhotoImage(kong_icon("idle", 64))
        w.iconphoto(False, self._app_icon)
        self._theme()

        self._build_header()
        self._build_footer()
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        self._build_home()
        self._build_meeting()
        self._build_settings()
        self._build_history()
        self._build_glossary()

        self._visible = False
        self._told_background = False
        self._testing = False
        self._test_started = 0
        self._history_count = -1
        self._block_kind = ""
        self._block_since = time.monotonic()
        self._block_view = View()
        self._tick()
        self._animate()

    # --- look ---------------------------------------------------------------------------

    def _theme(self) -> None:
        theme.apply(self.root)
        self.win.configure(bg=theme.PAPER)
        self._text_colors = theme.TEXT_COLORS
        try:
            self._scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._scale = 1.0
        self._painter = Painter(self._scale)

    def _paint_title_bar(self) -> None:
        if not IS_WIN:
            return
        from .. import win32

        try:
            hwnd = int(self.win.wm_frame(), 16)
            win32.set_caption_colors(hwnd, theme.PAPER, theme.INK, theme.INK)
        except (tk.TclError, ValueError, OSError):
            log.debug("could not colour the title bar", exc_info=True)

    # --- header and footer --------------------------------------------------------------

    def _build_header(self) -> None:
        """A cassette label: wordmark and state on paper, the state graphic flush right."""
        head = ttk.Frame(self.win)
        head.pack(fill="x", side="top")
        self.hd_block = ttk.Label(head, padding=0)
        self.hd_block.pack(side="right", anchor="n")
        left = ttk.Frame(head, padding=(18, 10, 12, 8))
        left.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="KONG", style="Mark.TLabel").pack(side="left")
        ttk.Label(top, text="講", style="Title.TLabel").pack(side="left", padx=(6, 0))
        meta = ttk.Frame(left)
        meta.pack(fill="x", pady=(2, 0))
        self.hd_dot = tk.Canvas(meta, width=8, height=8, highlightthickness=0, bg=theme.PAPER)
        self.hd_dot.pack(side="left", padx=(1, 7))
        self._hd_dot = self.hd_dot.create_rectangle(0, 0, 8, 8, fill=theme.ORANGE, outline="")
        self.hd_state = ttk.Label(meta, text="", style="Meta.TLabel")
        self.hd_state.pack(side="left")
        ttk.Label(meta, text="繁體中文語音輸入 ／ WINDOWS", style="Hint.TLabel").pack(side="right")
        ttk.Frame(self.win, style="Ink.TFrame", height=1).pack(fill="x", side="top")

    def _build_footer(self) -> None:
        foot = ttk.Frame(self.win, padding=(18, 6, 18, 8))
        foot.pack(fill="x", side="bottom")
        ttk.Label(foot, text=f"{DISPLAY_NAME} {__version__}", style="Meta.TLabel").pack(side="left")
        ttk.Label(foot, text=REPO_URL.removeprefix("https://"), style="Hint.TLabel").pack(side="right")
        ttk.Frame(self.win, style="Ink.TFrame", height=1).pack(fill="x", side="bottom", padx=18)

    def _section(self, parent, num: str, title: str, hint: str = "") -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text=num, style="Num.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(row, text=title, style="Key.TLabel").pack(side="left")
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel").pack(anchor="w", pady=(2, 0))

    def _box(self, parent, num: str, title: str) -> ttk.LabelFrame:
        """A hairline box whose label is an orange number and a bold title."""
        label = ttk.Frame(parent)
        ttk.Label(label, text=num, style="Num.TLabel").pack(side="left", padx=(0, 6))
        ttk.Label(label, text=title, style="Key.TLabel").pack(side="left")
        return ttk.LabelFrame(parent, labelwidget=label, padding=10)

    def _state_kind(self) -> str:
        app = self.app
        if app.models_missing:
            return "error"
        return {"idle": "ok", "recording": "listen", "testing": "listen", "meeting": "listen"}.get(app.state, "work")

    def _draw_block(self) -> None:
        kind = self._state_kind()
        v = self._block_view
        now = time.monotonic()
        if kind != self._block_kind:
            self._block_kind = kind
            self._block_since = now
            v.history, v.level = [], 0.0
        v.kind = kind
        v.t = now - self._block_since
        if kind == "listen":
            level = min(1.0, (getattr(self.app.recorder, "level", 0.0) * 12) ** 0.6)
            v.level += (level - v.level) * (0.55 if level > v.level else 0.2)
            v.history = (v.history + [v.level])[-HISTORY:]
        w, h = (int(round(n * self._scale)) for n in BLOCK)
        self._block_img = ImageTk.PhotoImage(self._painter.block(v, w, h), master=self.win)
        self.hd_block.configure(image=self._block_img)
        self.hd_dot.itemconfigure(self._hd_dot, fill=theme.INK if kind == "ok" else theme.ORANGE)

    def _animate(self) -> None:
        """Run the header graphic while something is happening; idle frames cost nothing."""
        if self._visible and (
            self._block_kind in ("listen", "work")
            or time.monotonic() - self._block_since < 0.6
            or self._state_kind() != self._block_kind
        ):
            try:
                self._draw_block()
            except Exception:
                log.exception("header graphic failed")
        self.win.after(80, self._animate)

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
        self._paint_title_bar()
        self._refresh()
        self._draw_block()

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
        f = ttk.Frame(self.nb, padding=(4, 18, 4, 12))
        self.nb.add(f, text="首頁")
        head = ttk.Frame(f)
        head.pack(fill="x")
        txt = ttk.Frame(head)
        txt.pack(side="left", fill="x", expand=True)
        self.h_title = ttk.Label(txt, text="", style="Title.TLabel")
        self.h_title.pack(anchor="w")
        self.h_detail = ttk.Label(txt, text="", style="Sub.TLabel", wraplength=560, justify="left")
        self.h_detail.pack(anchor="w", pady=(2, 0))
        self.h_retry = ttk.Button(head, text="重新下載語音模型", style="Accent.TButton", command=self.app.retry_setup)

        ttk.Separator(f).pack(fill="x", pady=14)
        self._section(f, "01", "狀態")
        rows = ttk.Frame(f)
        rows.pack(fill="x", pady=(6, 0))
        rows.columnconfigure(1, weight=1)
        self.r_engine = self._row(rows, 0, "語音辨識", "變更", lambda: self.nb.select(2))
        self.r_gpu = self._row(rows, 1, "顯示卡")
        self.r_polish = self._row(rows, 2, "整理", "測試整理", self._test_polish)
        self.r_gloss = self._row(rows, 3, "常用詞", "編輯", lambda: self.nb.select(4))
        self.h_tip = ttk.Label(f, text="", style="Hint.TLabel", wraplength=700, justify="left")
        self.h_tip.pack(anchor="w", pady=(8, 0))
        self.h_tip_btn = ttk.Button(f, text="改用 Qwen3-ASR", command=lambda: self.app.set_option("engine", "qwen3"))

        ttk.Separator(f).pack(fill="x", pady=14)
        self._section(f, "02", "試講一句", "結果只會顯示在下面，不會貼到別的地方。可以用來比較不同的辨識引擎。")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 8))
        self.t_btn = ttk.Button(bar, text="● 開始試講", style="Accent.TButton", command=self._toggle_test)
        self.t_btn.pack(side="left")
        self.t_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.t_state.pack(side="left", padx=12)
        self.t_out = tk.Text(f, height=5, wrap="word", font=(FONT, 11), relief="flat", padx=12, pady=10, borderwidth=0,
                             **self._text_colors)
        self.t_out.pack(fill="both", expand=True)
        self._set_test_text("")

    def _row(self, parent, r: int, key: str, button: str | None = None, command=None) -> ttk.Label:
        ttk.Label(parent, text=key, style="Hint.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 18), pady=5)
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

    # --- 會議記錄 ---------------------------------------------------------------------------

    def _build_meeting(self) -> None:
        f = ttk.Frame(self.nb, padding=(4, 16, 4, 12))
        self.nb.add(f, text="會議記錄")
        self._section(
            f, "01", "會議記錄",
            "錄一整場會議：邊錄邊轉成文字，結束後請 Claude 整理出摘要、決議和待辦事項，存成 Markdown 檔。錄音期間不能聽寫。",
        )
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(10, 8))
        self.m_btn = ttk.Button(bar, text="● 開始會議記錄", style="Accent.TButton", command=self._toggle_meeting)
        self.m_btn.pack(side="left")
        self.m_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.m_state.pack(side="left", padx=12)
        ttk.Button(bar, text="開啟資料夾", command=self.app.open_meetings).pack(side="right")
        self.m_out = tk.Text(f, height=10, wrap="word", font=(FONT, 11), relief="flat", padx=12, pady=10, borderwidth=0,
                             **self._text_colors)
        self.m_out.pack(fill="both", expand=True)
        self._set_meeting_text("開始後，轉好的文字會一段一段出現在這裡。")

        ttk.Separator(f).pack(fill="x", pady=14)
        self._section(f, "02", "設定")
        self.v_keep_audio = tk.BooleanVar(value=self.app.cfg.meeting_keep_audio)
        ttk.Checkbutton(f, text="保留錄音檔（.wav，一小時約 110 MB）", variable=self.v_keep_audio,
                        command=lambda: self.app.set_option("meeting_keep_audio", self.v_keep_audio.get())).pack(anchor="w", pady=(6, 0))
        self.m_where = ttk.Label(f, text="", style="Hint.TLabel", wraplength=720, justify="left")
        self.m_where.pack(anchor="w", pady=(6, 0))
        ttk.Label(
            f,
            text="只錄得到麥克風聽見的聲音：線上會議請讓喇叭外放，戴耳機時對方的聲音不會被錄進去。",
            style="Hint.TLabel", wraplength=720, justify="left",
        ).pack(anchor="w", pady=(2, 0))
        self._meeting_seen = None
        self._meeting_lines = 0

    def _set_meeting_text(self, text: str) -> None:
        self.m_out.configure(state="normal")
        self.m_out.delete("1.0", "end")
        self.m_out.insert("1.0", text)
        self.m_out.configure(state="disabled")

    def _toggle_meeting(self) -> None:
        if self.app.state == "meeting":
            self.app.stop_meeting()
        else:
            err = self.app.start_meeting()
            if err:
                self.m_state.configure(text=err)
                return
        self._refresh_meeting()

    def _refresh_meeting(self) -> None:
        app = self.app
        m, state = app.meeting, app.state
        if m is not None and m is not self._meeting_seen:
            self._meeting_seen, self._meeting_lines = m, 0
            self._set_meeting_text("")
        if m is not None and len(m.lines) > self._meeting_lines:
            new = m.lines[self._meeting_lines:]
            self._meeting_lines += len(new)
            self.m_out.configure(state="normal")
            for ln in new:
                self.m_out.insert("end", f"[{meeting.stamp(ln.t)}] {ln.text}\n")
            self.m_out.see("end")
            self.m_out.configure(state="disabled")
        if state == "meeting" and m is not None:
            self.m_btn.configure(text="■ 結束並整理", state="normal")
            self.m_state.configure(text=f"錄音中 {meeting.stamp(m.elapsed())}・已轉成 {m.chars:,} 字")
        elif state == "saving":
            self.m_btn.configure(text="整理中…", state="disabled")
            self.m_state.configure(text="辨識最後一段、請 Claude 寫重點，好了會打開檔案")
        else:
            self.m_btn.configure(text="● 開始會議記錄", state="normal" if state == "idle" else "disabled")
            last = app.last_meeting
            self.m_state.configure(text=f"上一場：{last.name}" if last else "")
        self.m_where.configure(text=f"存放位置：{paths.meetings_dir(app.cfg.meeting_dir)}")
        if self.v_keep_audio.get() != app.cfg.meeting_keep_audio:
            self.v_keep_audio.set(app.cfg.meeting_keep_audio)

    # --- 設定 -----------------------------------------------------------------------------

    def _build_settings(self) -> None:
        outer = ttk.Frame(self.nb, padding=(4, 16, 4, 12))
        self.nb.add(outer, text="設定")
        cfg = self.app.cfg
        left = ttk.Frame(outer)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)

        eng = self._box(left, "01", "辨識引擎")
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

        pol = self._box(left, "02", "整理（去贅字、修改口、修錯字）")
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

        hk = self._box(right, "03", "快捷鍵")
        hk.pack(fill="x")
        ttk.Label(hk, text="連按兩下開始說話，講完再按一下").pack(anchor="w")
        self.v_hotkey = tk.StringVar(value=HOTKEYS[cfg.hotkey])
        hcb = ttk.Combobox(hk, textvariable=self.v_hotkey, values=list(HOTKEYS.values()), state="readonly", width=14)
        hcb.pack(anchor="w", pady=(6, 0))
        hcb.bind("<<ComboboxSelected>>", lambda e: self.app.set_option("hotkey", self._key_of(HOTKEYS, self.v_hotkey.get())))

        pst = self._box(right, "04", "貼上")
        pst.pack(fill="x", pady=(12, 0))
        self.v_paste = tk.StringVar(value=cfg.paste_method)
        for key, label in PASTE_METHODS.items():
            ttk.Radiobutton(pst, text=label, value=key, variable=self.v_paste,
                            command=lambda: self.app.set_option("paste_method", self.v_paste.get())).pack(anchor="w")

        gen = self._box(right, "05", "一般")
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

    @staticmethod
    def _key_of(options: dict[str, str], label: str) -> str:
        return next((k for k, v in options.items() if v == label), next(iter(options)))

    # --- 最近說過的話 -----------------------------------------------------------------------

    def _build_history(self) -> None:
        f = ttk.Frame(self.nb, padding=(4, 16, 4, 12))
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
        f = ttk.Frame(self.nb, padding=(4, 16, 4, 12))
        self.nb.add(f, text="常用詞")
        ttk.Label(
            f,
            text="一行一個：人名、公司名、產品名、專有名詞（例如 Claude、王小明）。整理時遇到發音相近的字，會優先用這些寫法。以 # 開頭的行會被忽略。",
            style="Hint.TLabel",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        self.g_text = tk.Text(f, wrap="word", font=(FONT, 11), relief="flat", padx=12, pady=10, undo=True,
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
        self.hd_state.configure(text=f"[ {title.rstrip('…')} ]")
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
        self._refresh_meeting()

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
