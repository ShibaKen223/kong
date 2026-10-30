"""Kong's look: dark graphite, brushed aluminium, one glowing orange.

Taken from the key visual: four pillars on a graphite plinth, one of them aluminium with an
orange glass core. The palette is shared by the panel (drawn with Pillow) and the main window
(ttk, built on the stock "clam" theme so it needs no extra package).
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

IS_WIN = sys.platform == "win32"
FONT = "Microsoft JhengHei UI" if IS_WIN else "Noto Sans CJK TC"
HEAVY = "Segoe UI Black" if IS_WIN else "DejaVu Sans"  # Latin only: wordmark

BG = "#141718"  # graphite, a little cool like the plinth
FIELD = "#1d2122"  # text boxes and lists, one step up
HOVER = "#272c2e"
LINE = "#30373a"  # hairlines and outlines
EDGE = "#454d50"  # outlines that must be seen: buttons, checkboxes
ALU = "#c6cbcc"  # brushed aluminium: the open tab, keycaps
FG = "#e8eaea"
FG_SOFT = "#a3a9ab"
FG_FAINT = "#6d7476"
ORANGE = "#ff6b1f"  # the glow in the glass
ORANGE_HI = "#ff8a4a"  # the same, pointed at
GOOD = "#6fcf8a"
WHITE = "#ffffff"

# Type sizes in points. Body text is 12 pt: comfortable at arm's length on a laptop.
BODY = 12
SMALL = 11  # hints and captions; never smaller
TEXT = 13  # text boxes: what you said
TITLE = 24
MARK = 26


def rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


# For plain tk.Text, which ttk styles do not reach.
TEXT_COLORS = dict(
    bg=FIELD, fg=FG, insertbackground=ORANGE, selectbackground=ORANGE, selectforeground=BG,
    highlightthickness=1, highlightbackground=LINE, highlightcolor=ORANGE,
)


def apply(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=FONT, size=BODY)
        except tk.TclError:
            pass

    style.configure(
        ".", background=BG, foreground=FG, bordercolor=LINE, darkcolor=BG, lightcolor=BG,
        troughcolor=FIELD, focuscolor=BG, selectbackground=ORANGE, selectforeground=BG,
        fieldbackground=FIELD, insertcolor=FG, font=(FONT, BODY),
    )
    style.map(".", foreground=[("disabled", FG_FAINT)])

    # Buttons: a hairline on graphite that lights up when you point at it. Accent is the orange.
    style.configure("TButton", padding=(16, 8), relief="solid", borderwidth=1, background=FIELD, foreground=FG,
                    bordercolor=EDGE, lightcolor=FIELD, darkcolor=FIELD, font=(FONT, BODY, "bold"))
    style.map(
        "TButton",
        background=[("disabled", BG), ("pressed", LINE), ("active", HOVER)],
        foreground=[("disabled", FG_FAINT)],
        bordercolor=[("disabled", LINE), ("active", ALU)],
        lightcolor=[("pressed", LINE), ("active", HOVER)],
        darkcolor=[("pressed", LINE), ("active", HOVER)],
    )
    style.configure("Accent.TButton", background=ORANGE, foreground=BG, bordercolor=ORANGE,
                    lightcolor=ORANGE, darkcolor=ORANGE)
    style.map(
        "Accent.TButton",
        background=[("disabled", FIELD), ("pressed", ORANGE), ("active", ORANGE_HI)],
        foreground=[("disabled", FG_FAINT)],
        bordercolor=[("disabled", LINE), ("active", ORANGE_HI)],
        lightcolor=[("active", ORANGE_HI)],
        darkcolor=[("active", ORANGE_HI)],
    )

    # Tabs: plain words; the open one is a plate of aluminium.
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=FG_SOFT, padding=(20, 10), borderwidth=0,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, font=(FONT, BODY, "bold"))
    style.map(
        "TNotebook.Tab",
        background=[("selected", ALU), ("active", HOVER)],
        foreground=[("selected", BG), ("active", FG)],
        lightcolor=[("selected", ALU)],
        bordercolor=[("selected", ALU)],
        expand=[("selected", (0, 0, 0, 0))],
        padding=[("selected", (20, 10))],  # clam shrinks the open tab; keep all tabs level
    )

    style.configure("TLabelframe", background=BG, bordercolor=LINE, relief="solid", borderwidth=1,
                    lightcolor=BG, darkcolor=BG)
    style.configure("TLabelframe.Label", background=BG, foreground=FG, font=(FONT, BODY, "bold"))

    for kind in ("TRadiobutton", "TCheckbutton"):
        style.configure(kind, background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=BG, upperbordercolor=EDGE, lowerbordercolor=EDGE, indicatormargin=(0, 2, 8, 2))
        style.map(
            kind,
            background=[("active", BG)],
            indicatorbackground=[("disabled", BG), ("selected", ORANGE), ("active", HOVER)],
            upperbordercolor=[("disabled", LINE), ("selected", ORANGE)],
            lowerbordercolor=[("disabled", LINE), ("selected", ORANGE)],
        )

    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG, arrowcolor=FG_SOFT,
                    bordercolor=EDGE, lightcolor=FIELD, darkcolor=FIELD, padding=5)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", FIELD)],
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", FG)],
        background=[("active", HOVER)],
        bordercolor=[("focus", ORANGE)],
    )
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ORANGE)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    root.option_add("*TCombobox*Listbox.font", (FONT, BODY))

    style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG, rowheight=36,
                    bordercolor=LINE, lightcolor=FIELD, darkcolor=FIELD, font=(FONT, BODY))
    style.map("Treeview", background=[("selected", ORANGE)], foreground=[("selected", BG)])
    style.configure("Treeview.Heading", background=HOVER, foreground=FG_SOFT, relief="flat", padding=(8, 6),
                    bordercolor=LINE, lightcolor=HOVER, darkcolor=HOVER, font=(FONT, SMALL, "bold"))
    style.map("Treeview.Heading", background=[("active", LINE)])

    style.configure("TSeparator", background=LINE)
    style.configure("Vertical.TScrollbar", background=HOVER, troughcolor=FIELD, bordercolor=FIELD,
                    arrowcolor=FG_SOFT, lightcolor=HOVER, darkcolor=HOVER)

    style.configure("Title.TLabel", font=(FONT, TITLE, "bold"))
    style.configure("Sub.TLabel", font=(FONT, BODY), foreground=FG_SOFT)
    style.configure("Key.TLabel", font=(FONT, BODY + 1, "bold"))
    style.configure("Hint.TLabel", font=(FONT, SMALL), foreground=FG_SOFT)
    style.configure("Meta.TLabel", font=(FONT, SMALL, "bold"), foreground=FG_SOFT)
    style.configure("Mark.TLabel", font=(HEAVY, MARK), foreground=FG)
    # The quick-start guide: a big lead line, keycaps, and a green "it worked".
    style.configure("Lead.TLabel", font=(FONT, BODY + 2), foreground=FG)
    style.configure("Cap.TLabel", font=(FONT, BODY, "bold"), background=ALU, foreground=BG, padding=(10, 3))
    style.configure("Good.TLabel", font=(FONT, BODY + 1, "bold"), foreground=GOOD)
    style.configure("Bad.TLabel", font=(FONT, BODY), foreground=ORANGE)
    style.configure("Rule.TFrame", background=LINE)
    return style
