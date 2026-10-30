"""KONG 的主視窗：首頁、會議記錄、常用詞、設定。

Opens when Kong.exe is started (also when started again while Kong is running) and from a
left click on the tray icon. Closing it only hides it: Kong keeps running in the tray.
Everything here runs on the Tk thread; the app's own threads report back through `_ui()`.

Less is more here. 首頁 is the guide: one line on how it works, a box to try it in, and what
you said lately. Problems show up only when there is one; the technical details live in 設定.
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from PIL import ImageTk

from .. import DISPLAY_NAME, __version__, meeting, models, paths
from ..config import ENGINES, HOTKEYS, PASTE_METHODS, POLISH_MODES
from ..polish import prompt
from . import theme
from .icon import glyph, kong_icon
from .panel import HISTORY, Painter, View
from .tray import CLAUDE_MODELS

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
FONT = theme.FONT
REPO_URL = "https://github.com/ShibaKen223/kong"
BLOCK = (220, 92)  # the state graphic in the header, at 96 dpi
PLACEHOLDER = "點這裡，然後試著說一句話。"


class MainWindow:
    def __init__(self, app: "App", root: tk.Tk) -> None:
        self.app = app
        self.root = root
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title(DISPLAY_NAME)
        w.geometry("900x860")
        w.minsize(780, 720)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        self._app_icon = ImageTk.PhotoImage(kong_icon("idle", 64))
        w.iconphoto(False, self._app_icon)
        self._theme()

        self._build_header()
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(14, 16))
        self._build_home()
        self._build_meeting()
        self._build_glossary()
        self._build_settings()

        self._visible = False
        self._told_background = False
        self._history_count = -1
        self._block_kind = ""
        self._block_since = time.monotonic()
        self._block_view = View()
        self._tick()
        self._animate()

    # --- look ---------------------------------------------------------------------------

    def _theme(self) -> None:
        theme.apply(self.root)
        self.win.configure(bg=theme.BG)
        self._text_colors = theme.TEXT_COLORS
        try:
            self._scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._scale = 1.0
        self._painter = Painter(self._scale)
        self._glyphs: list[ImageTk.PhotoImage] = []

    def _paint_title_bar(self) -> None:
        if not IS_WIN:
            return
        from .. import win32

        try:
            hwnd = int(self.win.wm_frame(), 16)
            win32.set_caption_colors(hwnd, theme.BG, theme.FG, theme.LINE)
        except (tk.TclError, ValueError, OSError):
            log.debug("could not colour the title bar", exc_info=True)

    def _build_header(self) -> None:
        """Wordmark and state on the left, the four pillars flush right."""
        head = ttk.Frame(self.win)
        head.pack(fill="x", side="top")
        self.hd_block = ttk.Label(head, padding=0)
        self.hd_block.pack(side="right", anchor="n")
        left = ttk.Frame(head, padding=(18, 10, 12, 8))
        left.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="KONG", style="Mark.TLabel").pack(side="left")
        meta = ttk.Frame(left)
        meta.pack(fill="x", pady=(2, 0))
        self.hd_dot = tk.Canvas(meta, width=8, height=8, highlightthickness=0, bg=theme.BG)
        self.hd_dot.pack(side="left", padx=(1, 7))
        self._hd_dot = self.hd_dot.create_oval(0, 0, 8, 8, fill=theme.ORANGE, outline="")
        self.hd_state = ttk.Label(meta, text="", style="Meta.TLabel")
        self.hd_state.pack(side="left")
        ttk.Frame(self.win, style="Rule.TFrame", height=1).pack(fill="x", side="top")

    def _box(self, parent, icon: str, title: str) -> ttk.LabelFrame:
        """A hairline box whose label is a small orange line icon and a bold title."""
        label = ttk.Frame(parent)
        img = ImageTk.PhotoImage(glyph(icon, int(round(18 * self._scale))), master=self.win)
        self._glyphs.append(img)  # Tk drops images nobody holds on to
        ttk.Label(label, image=img).pack(side="left", padx=(0, 8))
        ttk.Label(label, text=title, style="Key.TLabel").pack(side="left")
        return ttk.LabelFrame(parent, labelwidget=label, padding=12)

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
        self.hd_dot.itemconfigure(self._hd_dot, fill=theme.FG_FAINT if kind == "ok" else theme.ORANGE)

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
                "info", "KONG 還在背景執行",
                f"連按兩下「{self.app.hotkey_label()}」就能說話；點右下角系統匣的 KONG 圖示可以再打開視窗。",
                hide_after=4,
            )

    def _ui(self, fn, *args) -> None:
        """Run on the Tk thread (callbacks from the app arrive on worker threads)."""
        self.app.panel.call(fn, *args)

    # --- 首頁: how it works, a place to try it, what you said ---------------------------------

    def _build_home(self) -> None:
        f = ttk.Frame(self.nb, padding=(6, 22, 6, 10))
        self.nb.add(f, text="首頁")
        self.h_title = ttk.Label(f, text="", style="Title.TLabel")
        self.h_title.pack(anchor="w")

        how = ttk.Frame(f)
        how.pack(anchor="w", pady=(10, 0))
        self.caps: list[ttk.Label] = []
        for part in ("連按兩下", "KEY", "說話，說完再按一下", "KEY", "貼上。"):
            if part == "KEY":
                cap = ttk.Label(how, text="", style="Cap.TLabel")
                cap.pack(side="left", padx=6)
                self.caps.append(cap)
            else:
                ttk.Label(how, text=part, style="Lead.TLabel").pack(side="left")

        # Only when something needs doing.
        self.h_alert = ttk.Frame(f)
        self.h_alert_text = ttk.Label(self.h_alert, text="", style="Bad.TLabel", wraplength=640, justify="left")
        self.h_alert_text.pack(side="left")
        self.h_alert_btn = ttk.Button(self.h_alert, text="重新下載語音模型", style="Accent.TButton", command=self.app.retry_setup)

        self.try_box = tk.Text(f, height=3, wrap="word", font=(FONT, theme.TEXT + 1), relief="flat", padx=14, pady=12,
                               borderwidth=0, **self._text_colors)
        self.try_box.pack(fill="x", pady=(18, 0))
        self.try_box.bind("<FocusIn>", lambda e: self._clear_placeholder())
        self.try_result = ttk.Label(f, text="", style="Good.TLabel", wraplength=820, justify="left")
        self.try_result.pack(anchor="w", pady=(6, 0))
        self._show_placeholder()

        # Shown once there is something in it: a first-time user sees only the try box.
        self.h_recent = ttk.Frame(f)
        ttk.Label(self.h_recent, text="最近說過的話", style="Key.TLabel").pack(anchor="w", pady=(16, 6))
        self.hist = ttk.Treeview(self.h_recent, columns=("t", "text"), show="", selectmode="browse", height=6)
        self.hist.column("t", width=70, stretch=False)
        self.hist.column("text", width=700)
        self.hist.pack(fill="both", expand=True)
        self.hist.bind("<Double-1>", lambda e: self._copy_selected())
        ttk.Label(self.h_recent, text="按兩下一句就會複製。只存在這台電腦。", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        self._hist_texts: dict[str, str] = {}

    def _show_placeholder(self) -> None:
        self.try_box.delete("1.0", "end")
        self.try_box.insert("1.0", PLACEHOLDER)
        self.try_box.configure(fg=theme.FG_FAINT)
        self._placeholder = True

    def _clear_placeholder(self) -> None:
        if self._placeholder:
            self.try_box.delete("1.0", "end")
            self.try_box.configure(fg=theme.FG)
            self._placeholder = False

    def _fill_history(self) -> None:
        items = self.app.history.items()
        if len(items) == self._history_count and items and self._hist_texts:
            return
        self._history_count = len(items)
        if items:
            self.h_recent.pack(fill="both", expand=True)
        else:
            self.h_recent.pack_forget()
        self.hist.delete(*self.hist.get_children())
        self._hist_texts.clear()
        for it in items:
            t = it.get("t", "")
            iid = self.hist.insert("", "end", values=(t[-8:-3] if len(t) >= 8 else t, it["text"].replace("\n", " ")))
            self._hist_texts[iid] = it["text"]

    def _copy_selected(self) -> None:
        sel = self.hist.selection()
        if sel:
            self.app.copy_text(self._hist_texts.get(sel[0], ""))

    def _refresh_home(self) -> None:
        app, cfg = self.app, self.app.cfg
        key = app.hotkey_label()
        for cap in self.caps:
            cap.configure(text=key)
        title, detail = app.headline()
        first_time = not app.history.has_items() and app.state == "idle"
        self.h_title.configure(text="歡迎使用 KONG" if first_time else title)

        alert, button = "", False
        if app.models_missing:
            alert, button = "語音模型沒有裝好。檢查網路後按右邊的按鈕再試一次。", True
        elif app.state == "loading":
            alert = app.download_status or "正在準備語音模型，第一次要下載約 160 MB…"
        elif app.state in ("meeting", "saving"):
            alert = detail
        elif cfg.polish == "claude_code" and not app.polisher.available and not app.polisher.status.startswith("尋找"):
            alert = f"{app.polisher.status}。沒有也能用，只是不會幫你整理（細節在「設定」）。"
        self.h_alert_text.configure(text=alert)
        if alert:
            self.h_alert.pack(anchor="w", fill="x", pady=(12, 0), after=self.caps[0].master)
        else:
            self.h_alert.pack_forget()
        if button:
            self.h_alert_btn.pack(side="right")
        else:
            self.h_alert_btn.pack_forget()

        said = "" if self._placeholder else self.try_box.get("1.0", "end").strip()
        if said:
            self.try_result.configure(text="成功了！在 LINE、Word、瀏覽器……任何能打字的地方都一樣用。")
        elif first_time:
            self.try_result.configure(text="")

    # --- 會議記錄 ---------------------------------------------------------------------------

    def _build_meeting(self) -> None:
        f = ttk.Frame(self.nb, padding=(6, 22, 6, 10))
        self.nb.add(f, text="會議記錄")
        ttk.Label(f, text="錄整場會議", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="邊錄邊轉成文字，結束後自動整理重點和待辦事項，存成檔案。", style="Lead.TLabel").pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(18, 10))
        self.m_btn = ttk.Button(bar, text="● 開始會議記錄", style="Accent.TButton", command=self._toggle_meeting)
        self.m_btn.pack(side="left")
        self.m_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.m_state.pack(side="left", padx=14)
        ttk.Button(bar, text="開啟資料夾", command=self.app.open_meetings).pack(side="right")
        self.m_out = tk.Text(f, height=10, wrap="word", font=(FONT, theme.TEXT), relief="flat", padx=14, pady=12, borderwidth=0,
                             **self._text_colors)
        self.m_out.pack(fill="both", expand=True)
        self._set_meeting_text("")
        self.v_keep_audio = tk.BooleanVar(value=self.app.cfg.meeting_keep_audio)
        ttk.Checkbutton(f, text="保留錄音檔（一小時約 110 MB）", variable=self.v_keep_audio,
                        command=lambda: self.app.set_option("meeting_keep_audio", self.v_keep_audio.get())).pack(anchor="w", pady=(12, 0))
        ttk.Label(f, text="戴耳機開線上會議時，錄不到對方的聲音。", style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
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
            self.m_state.configure(text=f"錄音中 {meeting.stamp(m.elapsed())}・{m.chars:,} 字")
        elif state == "saving":
            self.m_btn.configure(text="整理中…", state="disabled")
            self.m_state.configure(text="好了會自動打開檔案")
        else:
            self.m_btn.configure(text="● 開始會議記錄", state="normal" if state == "idle" else "disabled")
            last = app.last_meeting
            self.m_state.configure(text=f"上一場：{last.name}" if last else "")
        if self.v_keep_audio.get() != app.cfg.meeting_keep_audio:
            self.v_keep_audio.set(app.cfg.meeting_keep_audio)

    # --- 常用詞 ---------------------------------------------------------------------------

    def _build_glossary(self) -> None:
        f = ttk.Frame(self.nb, padding=(6, 22, 6, 10))
        self.nb.add(f, text="常用詞")
        ttk.Label(f, text="常用詞", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="KONG 會從你說的話自動學人名、公司和專有名詞，之後就聽得更準。", style="Lead.TLabel").pack(anchor="w", pady=(8, 0))
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(18, 8))
        self.v_auto_gloss = tk.BooleanVar(value=self.app.cfg.auto_glossary)
        ttk.Checkbutton(top, text="自動學習", variable=self.v_auto_gloss,
                        command=lambda: self.app.set_option("auto_glossary", self.v_auto_gloss.get())).pack(side="left")
        self.l_state = ttk.Label(top, text="", style="Sub.TLabel")
        self.l_state.pack(side="left", padx=14)
        ttk.Button(top, text="移除選取的詞", command=self._forget_selected).pack(side="right")
        self.learned = ttk.Treeview(f, columns=("term", "n"), show="headings", selectmode="extended", height=6)
        self.learned.heading("term", text="自動學到的詞")
        self.learned.heading("n", text="次數")
        self.learned.column("term", width=620)
        self.learned.column("n", width=90, stretch=False, anchor="center")
        self.learned.pack(fill="both", expand=True)
        self._learned_shown: list[tuple[str, int]] | None = None

        ttk.Label(f, text="自己加的詞（一行一個）", style="Key.TLabel").pack(anchor="w", pady=(16, 6))
        self.g_text = tk.Text(f, wrap="word", font=(FONT, theme.TEXT), relief="flat", padx=14, pady=10, undo=True, height=3,
                              **self._text_colors)
        self.g_text.pack(fill="x")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="儲存", style="Accent.TButton", command=self._save_glossary).pack(side="left")
        self.g_state = ttk.Label(bar, text="", style="Sub.TLabel")
        self.g_state.pack(side="left", padx=12)
        self._load_glossary()

    def _fill_learned(self) -> None:
        learner = self.app.learner
        terms = learner.terms()
        if terms != self._learned_shown:
            self._learned_shown = terms
            self.learned.delete(*self.learned.get_children())
            for t, n in terms:
                self.learned.insert("", "end", iid=t, values=(t, n))
        state = f"已學到 {len(terms)} 個" if terms else "說幾句話之後，這裡會出現學到的詞"
        if learner.last_note == "學習中…":
            state += "・學習中…"
        self.l_state.configure(text=state)
        if self.v_auto_gloss.get() != self.app.cfg.auto_glossary:
            self.v_auto_gloss.set(self.app.cfg.auto_glossary)

    def _forget_selected(self) -> None:
        for term in self.learned.selection():
            self.app.forget_word(term)
        self._fill_learned()

    def _load_glossary(self) -> None:
        """Only the words themselves: the file's # comments stay in the file."""
        path = paths.glossary_file()
        prompt.ensure_glossary_file(path)
        self.g_text.delete("1.0", "end")
        self.g_text.insert("1.0", "\n".join(prompt.load_user_glossary(path)))

    def _save_glossary(self) -> None:
        words = [w.strip() for w in self.g_text.get("1.0", "end").splitlines() if w.strip()]
        try:
            paths.glossary_file().write_text("# KONG 常用詞：一行一個。\n" + "\n".join(words) + "\n", encoding="utf-8")
            self.g_state.configure(text=f"已儲存 {len(words)} 個詞")
        except OSError as e:
            self.g_state.configure(text=f"存檔失敗：{e}")

    # --- 設定: everything technical --------------------------------------------------------

    def _build_settings(self) -> None:
        outer = ttk.Frame(self.nb, padding=(6, 18, 6, 10))
        self.nb.add(outer, text="設定")
        cfg = self.app.cfg
        cols = ttk.Frame(outer)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True)

        eng = self._box(left, "mic", "辨識引擎")
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
        self.s_gpu = ttk.Label(eng, text="", style="Hint.TLabel", wraplength=380, justify="left")
        self.s_gpu.pack(anchor="w", pady=(4, 0))

        pol = self._box(left, "spark", "整理")
        pol.pack(fill="x", pady=(12, 0))
        self.v_polish = tk.StringVar(value=cfg.polish)
        for key, label in POLISH_MODES.items():
            ttk.Radiobutton(pol, text=label, value=key, variable=self.v_polish,
                            command=lambda: self.app.set_option("polish", self.v_polish.get())).pack(anchor="w")
        row = ttk.Frame(pol)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Claude 模型").pack(side="left")
        self.v_model = tk.StringVar(value=CLAUDE_MODELS.get(cfg.claude_model, "Sonnet（建議）"))
        cb = ttk.Combobox(row, textvariable=self.v_model, values=list(CLAUDE_MODELS.values()), state="readonly", width=16)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.app.set_option("claude_model", self._key_of(CLAUDE_MODELS, self.v_model.get())))
        test = ttk.Frame(pol)
        test.pack(fill="x", pady=(8, 0))
        ttk.Button(test, text="測試整理", command=self._test_polish).pack(side="left")
        self.s_polish = ttk.Label(pol, text="", style="Hint.TLabel", wraplength=380, justify="left")
        self.s_polish.pack(anchor="w", pady=(6, 0))

        hk = self._box(right, "key", "快捷鍵")
        hk.pack(fill="x")
        self.v_hotkey = tk.StringVar(value=HOTKEYS[cfg.hotkey])
        hcb = ttk.Combobox(hk, textvariable=self.v_hotkey, values=list(HOTKEYS.values()), state="readonly", width=14)
        hcb.pack(anchor="w")
        hcb.bind("<<ComboboxSelected>>", lambda e: self.app.set_option("hotkey", self._key_of(HOTKEYS, self.v_hotkey.get())))

        pst = self._box(right, "paste", "貼上")
        pst.pack(fill="x", pady=(12, 0))
        self.v_paste = tk.StringVar(value=cfg.paste_method)
        for key, label in PASTE_METHODS.items():
            ttk.Radiobutton(pst, text=label, value=key, variable=self.v_paste,
                            command=lambda: self.app.set_option("paste_method", self.v_paste.get())).pack(anchor="w")

        gen = self._box(right, "sliders", "一般")
        gen.pack(fill="x", pady=(12, 0))
        self.v_live = tk.BooleanVar(value=cfg.live_preview)
        ttk.Checkbutton(gen, text="邊講邊顯示文字", variable=self.v_live,
                        command=lambda: self.app.set_option("live_preview", self.v_live.get())).pack(anchor="w")
        self.v_auto = tk.BooleanVar(value=self.app.autostart_enabled())
        ttk.Checkbutton(gen, text="開機時自動啟動", variable=self.v_auto,
                        command=self.app.toggle_autostart).pack(anchor="w", pady=(4, 0))

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="匯出診斷檔到桌面", command=self.app.export_diagnostics).pack(fill="x")
        ttk.Button(btns, text="開啟記錄檔資料夾", command=self.app.open_logs).pack(fill="x", pady=6)
        ttk.Button(btns, text="結束 KONG", command=self.app.quit).pack(fill="x")
        ttk.Label(right, text=f"{DISPLAY_NAME} {__version__}・{REPO_URL.removeprefix('https://')}", style="Hint.TLabel",
                  wraplength=380, justify="left").pack(anchor="w", pady=(12, 0))

    @staticmethod
    def _key_of(options: dict[str, str], label: str) -> str:
        return next((k for k, v in options.items() if v == label), next(iter(options)))

    def _test_polish(self) -> None:
        self.s_polish.configure(text="測試中…（請 Claude Code 整理一句範例）")
        self.app.test_polish(lambda ok, msg: self._ui(self._show_polish_test, ok, msg))

    def _show_polish_test(self, ok: bool, msg: str) -> None:
        if ok:
            self.s_polish.configure(text=f"整理正常 ✓　{msg}")
        else:
            self.s_polish.configure(
                text=f"沒有成功：{msg}\n解法：安裝 Claude Code 和 Git for Windows，在終端機執行 claude 登入一次，再按一次「測試整理」。"
            )

    def _sync_settings(self) -> None:
        """The tray menu changes some settings too: show what is really in effect."""
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
            "auto": "目前：" + (app.engine.label if app.engine is not None else "準備中"),
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
        if g is None:
            gpu = "顯示卡：沒有偵測到 NVIDIA 顯示卡（用 CPU 辨識）"
        elif not g.usable:
            gpu = f"顯示卡：{g.describe()}（" + ("驅動太舊，請更新" if not g.driver_ok else "記憶體不到 3 GB") + "）"
        else:
            gpu = f"顯示卡：{g.describe()}"
        if app.download_status:
            gpu += f"\n{app.download_status}"
        self.s_gpu.configure(text=gpu)
        if not self.s_polish.cget("text").startswith(("測試中", "整理正常", "沒有成功")):
            version = app.polisher.version.split()[0] if app.polisher.version else ""
            self.s_polish.configure(text=app.polish_status + (f"・Claude Code {version}" if version else ""))

    # --- keeping it current -------------------------------------------------------------

    def _tick(self) -> None:
        if self._visible:
            try:
                self._refresh()
            except Exception:
                log.exception("main window refresh failed")
        self.win.after(700, self._tick)

    def _refresh(self) -> None:
        title, _ = self.app.headline()
        self.hd_state.configure(text=f"[ {title.rstrip('…')} ]")
        self._refresh_home()
        self._fill_history()
        self._refresh_meeting()
        self._fill_learned()
        self._sync_settings()
