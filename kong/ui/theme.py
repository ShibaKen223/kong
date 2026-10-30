"""Kong's look: warm grey paper, black ink, one signal orange, hairline rules.

The palette is shared by the panel (drawn with Pillow) and the main window (ttk, built on
the stock "clam" theme so it needs no extra package).
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

IS_WIN = sys.platform == "win32"
FONT = "Microsoft JhengHei UI" if IS_WIN else "Noto Sans CJK TC"
HEAVY = "Segoe UI Black" if IS_WIN else "DejaVu Sans"  # Latin only: wordmark and numbers

PAPER = "#dadad6"
PAPER_HI = "#e9e9e5"  # fields and lists, a sheet lighter than the paper
PAPER_LO = "#cbcbc7"  # hover
INK = "#111111"
INK_SOFT = "#4e4e4c"
INK_FAINT = "#86867f"
ORANGE = "#ff4a26"
WHITE = "#ffffff"

# Type sizes in points. Body text is 12 pt: comfortable at arm's length on a laptop.
BODY = 12
SMALL = 11  # hints and captions; never smaller
TEXT = 13  # text boxes: what you said
TITLE = 24
MARK = 26
NUM = 14


def rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


# For plain tk.Text, which ttk styles do not reach.
TEXT_COLORS = dict(
    bg=PAPER_HI, fg=INK, insertbackground=INK, selectbackground=ORANGE, selectforeground=INK,
    highlightthickness=1, highlightbackground=INK, highlightcolor=ORANGE,
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
        ".", background=PAPER, foreground=INK, bordercolor=INK, darkcolor=PAPER, lightcolor=PAPER,
        troughcolor=PAPER_LO, focuscolor=PAPER, selectbackground=ORANGE, selectforeground=INK,
        fieldbackground=PAPER_HI, insertcolor=INK, font=(FONT, BODY),
    )
    style.map(".", foreground=[("disabled", INK_FAINT)])

    # Buttons: ink outline on paper; solid ink when you point at them. Accent is the orange block.
    style.configure("TButton", padding=(16, 8), relief="solid", borderwidth=1, background=PAPER, foreground=INK,
                    font=(FONT, BODY, "bold"))
    style.map(
        "TButton",
        background=[("disabled", PAPER), ("pressed", INK_SOFT), ("active", INK)],
        foreground=[("disabled", INK_FAINT), ("active", WHITE)],
        bordercolor=[("disabled", INK_FAINT)],
        lightcolor=[("pressed", INK_SOFT), ("active", INK)],
        darkcolor=[("pressed", INK_SOFT), ("active", INK)],
    )
    style.configure("Accent.TButton", background=ORANGE, foreground=INK, bordercolor=ORANGE,
                    lightcolor=ORANGE, darkcolor=ORANGE)
    style.map(
        "Accent.TButton",
        background=[("disabled", PAPER_LO), ("pressed", INK_SOFT), ("active", INK)],
        foreground=[("disabled", INK_FAINT), ("active", ORANGE)],
        bordercolor=[("disabled", PAPER_LO), ("active", INK)],
        lightcolor=[("active", INK)],
        darkcolor=[("active", INK)],
    )

    # Tabs: plain words; the open one is a black block.
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=PAPER, foreground=INK_SOFT, padding=(20, 10), borderwidth=0,
                    bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER, font=(FONT, BODY, "bold"))
    style.map(
        "TNotebook.Tab",
        background=[("selected", INK), ("active", PAPER_LO)],
        foreground=[("selected", WHITE), ("active", INK)],
        lightcolor=[("selected", INK)],
        bordercolor=[("selected", INK)],
        expand=[("selected", (0, 0, 0, 0))],
        padding=[("selected", (20, 10))],  # clam shrinks the open tab; keep all tabs level
    )

    style.configure("TLabelframe", background=PAPER, bordercolor=INK, relief="solid", borderwidth=1,
                    lightcolor=PAPER, darkcolor=PAPER)
    style.configure("TLabelframe.Label", background=PAPER, foreground=INK, font=(FONT, BODY, "bold"))

    for kind in ("TRadiobutton", "TCheckbutton"):
        style.configure(kind, background=PAPER, foreground=INK, indicatorbackground=PAPER_HI,
                        indicatorforeground=INK, upperbordercolor=INK, lowerbordercolor=INK, indicatormargin=(0, 2, 8, 2))
        style.map(
            kind,
            background=[("active", PAPER)],
            indicatorbackground=[("disabled", PAPER), ("selected", ORANGE), ("active", PAPER_LO)],
            upperbordercolor=[("disabled", INK_FAINT)],
            lowerbordercolor=[("disabled", INK_FAINT)],
        )

    style.configure("TCombobox", fieldbackground=PAPER_HI, background=PAPER, foreground=INK, arrowcolor=INK,
                    bordercolor=INK, lightcolor=PAPER_HI, darkcolor=PAPER_HI, padding=5)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PAPER_HI)],
        selectbackground=[("readonly", PAPER_HI)],
        selectforeground=[("readonly", INK)],
        background=[("active", PAPER_LO)],
    )
    root.option_add("*TCombobox*Listbox.background", PAPER_HI)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ORANGE)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
    root.option_add("*TCombobox*Listbox.font", (FONT, BODY))

    style.configure("Treeview", background=PAPER_HI, fieldbackground=PAPER_HI, foreground=INK, rowheight=36,
                    bordercolor=INK, lightcolor=PAPER_HI, darkcolor=PAPER_HI, font=(FONT, BODY))
    style.map("Treeview", background=[("selected", ORANGE)], foreground=[("selected", INK)])
    style.configure("Treeview.Heading", background=INK, foreground=WHITE, relief="flat", padding=(8, 6),
                    bordercolor=INK, lightcolor=INK, darkcolor=INK, font=(FONT, SMALL, "bold"))
    style.map("Treeview.Heading", background=[("active", INK_SOFT)])

    style.configure("TSeparator", background=INK)
    style.configure("Vertical.TScrollbar", background=PAPER, troughcolor=PAPER_HI, bordercolor=PAPER_HI,
                    arrowcolor=INK, lightcolor=PAPER, darkcolor=PAPER)

    style.configure("Title.TLabel", font=(FONT, TITLE, "bold"))
    style.configure("Sub.TLabel", font=(FONT, BODY), foreground=INK_SOFT)
    style.configure("Key.TLabel", font=(FONT, BODY + 1, "bold"))
    style.configure("Hint.TLabel", font=(FONT, SMALL), foreground=INK_SOFT)
    style.configure("Meta.TLabel", font=(FONT, SMALL, "bold"))
    style.configure("Num.TLabel", font=(HEAVY, NUM), foreground=ORANGE)
    style.configure("Mark.TLabel", font=(HEAVY, MARK), foreground=INK)
    # The quick-start guide: a big lead line, keycaps, and a green "it worked".
    style.configure("Lead.TLabel", font=(FONT, BODY + 2), foreground=INK)
    style.configure("Step.TLabel", font=(FONT, BODY + 1), foreground=INK)
    style.configure("Cap.TLabel", font=(FONT, BODY, "bold"), background=INK, foreground=WHITE, padding=(10, 3))
    style.configure("Good.TLabel", font=(FONT, BODY + 1, "bold"), foreground="#1d7a3a")
    style.configure("Bad.TLabel", font=(FONT, BODY), foreground=ORANGE)
    style.configure("Ink.TFrame", background=INK)
    return style
