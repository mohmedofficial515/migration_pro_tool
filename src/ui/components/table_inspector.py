"""
Table Inspector UI
==================
Shows a detailed per-table inspection window before Copy / Move.

New in v2:
  - Column selection (☑/☐ per row) + Select All / Deselect All
  - Dedicated inline column-name editor panel below treeview
  - AI Response importer: paste JSON from AI → auto-applies renames

Flow:
    TableInspectorFlow.run() → iterates tables sequentially (B-mode),
    returns list[TableConfig] or None if user cancelled.
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk, messagebox

from src.database.inspector import (
    get_full_table_info,
    get_column_analytics,
    get_columns_quick_stats,
)
from src.ui.components.ollama_chat import OllamaChatWindow

# ─── Palette (matches app.py) ────────────────────────────────
BG          = "#0f1117"
PANEL_BG    = "#161b22"
HEADER_BG   = "#1c2128"
ACCENT      = "#2f81f7"
ACCENT2     = "#3fb950"
DANGER      = "#f85149"
WARNING     = "#d29922"
TEXT        = "#e6edf3"
TEXT_DIM    = "#8b949e"
BORDER      = "#30363d"
SEL_BG      = "#1f4068"
ENTRY_BG    = "#21262d"
BTN_BG      = "#21262d"
BTN_HOVER   = "#30363d"
TREE_ODD    = "#161b22"
TREE_EVEN   = "#1c2128"
PK_COLOR    = "#388bfd"
FK_COLOR    = "#e3b341"
UQ_COLOR    = "#3fb950"
IDX_COLOR   = "#8b949e"
CHECK_ON    = "#3fb950"
CHECK_OFF   = "#8b949e"

FONT_UI     = ("Segoe UI", 10)
FONT_BOLD   = ("Segoe UI", 10, "bold")
FONT_MONO   = ("Consolas", 9)
FONT_HEAD   = ("Segoe UI", 11, "bold")
FONT_TITLE  = ("Segoe UI", 13, "bold")
FONT_SMALL  = ("Segoe UI", 9)

TAG_PK  = "pk_row"
TAG_FK  = "fk_row"
TAG_UQ  = "uq_row"
TAG_ODD = "odd"
TAG_EVN = "even"
TAG_OFF = "col_off"        # deselected column row


# ─── Data model ──────────────────────────────────────────────

@dataclass
class TableConfig:
    original_name:    str
    target_name:      str
    column_renames:   dict[str, str]  = field(default_factory=dict)
    selected_columns: list[str] | None = None   # None = all columns
    skipped:          bool = False


# ─── FlatButton helper ────────────────────────────────────────

class _Btn(tk.Frame):
    def __init__(self, master, text, cmd=None,
                 bg=BTN_BG, fg=TEXT, hov=BTN_HOVER,
                 padx=12, pady=5, font=FONT_UI, w=None, **kw):
        super().__init__(master, bg=bg, **kw)
        self._bg = bg; self._hov = hov; self._cmd = cmd
        lbl = tk.Label(self, text=text, bg=bg, fg=fg,
                       font=font, padx=padx, pady=pady, cursor="hand2")
        if w:
            lbl.config(width=w)
        lbl.pack(fill="both", expand=True)
        lbl.bind("<Enter>",    lambda _: lbl.config(bg=self._hov))
        lbl.bind("<Leave>",    lambda _: lbl.config(bg=self._bg))
        lbl.bind("<Button-1>", lambda _: self._cmd() if self._cmd else None)
        self._lbl = lbl

    def set_text(self, t): self._lbl.config(text=t)
    def set_fg(self, c):   self._lbl.config(fg=c)


# ─── Inspector Window ────────────────────────────────────────

class TableInspectorWindow(tk.Toplevel):
    """
    Single-table inspector Toplevel.
    result: "proceed" | "skip" | "cancel"
    """

    COL_IDS = ("sel", "ord", "source", "type", "nullpct", "pk", "fk", "uq", "idx", "sample", "target")
    COL_CFG = {
        "sel":     {"label": "☑",              "width": 28,  "stretch": False, "anchor": "center"},
        "ord":     {"label": "#",              "width": 28,  "stretch": False, "anchor": "center"},
        "source":  {"label": "Source Column",  "width": 140, "stretch": True,  "anchor": "w"},
        "type":    {"label": "Type",           "width": 100, "stretch": False, "anchor": "w"},
        "nullpct": {"label": "Null %",         "width": 55,  "stretch": False, "anchor": "center"},
        "pk":      {"label": "PK",             "width": 26,  "stretch": False, "anchor": "center"},
        "fk":      {"label": "FK",             "width": 26,  "stretch": False, "anchor": "center"},
        "uq":      {"label": "UQ",             "width": 26,  "stretch": False, "anchor": "center"},
        "idx":     {"label": "IDX",            "width": 30,  "stretch": False, "anchor": "center"},
        "sample":  {"label": "Sample Values",  "width": 200, "stretch": True,  "anchor": "w"},
        "target":  {"label": "Target Column",  "width": 140, "stretch": True,  "anchor": "w"},
    }

    def __init__(self, master, table: str, index: int, total: int,
                 dsn: str, schema: str,
                 src_schema: str, tgt_schema: str,
                 initial_config: TableConfig):
        super().__init__(master)
        self.table          = table
        self.index          = index
        self.total          = total
        self.dsn            = dsn
        self.src_schema     = src_schema
        self.tgt_schema     = tgt_schema
        self.config_out     = initial_config
        self.result         = None

        # Internal state
        self._info: dict              = {}
        self._quick_stats: dict       = {}   # column_name -> pg_stats data
        self._selected_cols: set[str] = set()
        self._prompt_text: str        = ""
        self._edit_entry: tk.Entry | None = None
        self._edit_iid: str           = ""

        self._setup_window()
        self._build_ui()
        self._load_data()

    # ── Window setup ────────────────────────────────────────

    def _setup_window(self) -> None:
        self.title(f"📋 Table Inspector  ({self.index}/{self.total})  —  {self.src_schema}.{self.table}")
        self.geometry("1260x780")
        self.minsize(960, 600)
        self.configure(bg=BG)
        self.grab_set()
        self.resizable(True, True)

    # ── UI Build ────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Title bar ──
        title_bar = tk.Frame(self, bg=HEADER_BG, height=52)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        tk.Frame(title_bar, bg=ACCENT, width=4).pack(side="left", fill="y")

        lf = tk.Frame(title_bar, bg=HEADER_BG, padx=14)
        lf.pack(side="left", fill="y", pady=6)
        tk.Label(lf, text=f"📋  Table Inspector  ({self.index} of {self.total})",
                 bg=HEADER_BG, fg=TEXT, font=FONT_TITLE).pack(anchor="w")
        tk.Label(lf, text=f"  {self.src_schema}.{self.table}  →  {self.tgt_schema}",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_MONO).pack(anchor="w")

        self._status_lbl = tk.Label(title_bar, text="⏳ Loading...",
                                    bg=HEADER_BG, fg=WARNING, font=FONT_MONO, padx=16)
        self._status_lbl.pack(side="right", fill="y")

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ── Main 3-pane area ──
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=8)

        # Left pane
        self._left = tk.Frame(main, bg=PANEL_BG, width=210,
                               highlightbackground=BORDER, highlightthickness=1)
        self._left.pack(side="left", fill="y", padx=(0, 6))
        self._left.pack_propagate(False)
        self._build_left_pane()

        # Center pane
        center = tk.Frame(main, bg=PANEL_BG,
                          highlightbackground=BORDER, highlightthickness=1)
        center.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self._build_center_pane(center)

        # Right pane
        self._right = tk.Frame(main, bg=PANEL_BG, width=230,
                                highlightbackground=BORDER, highlightthickness=1)
        self._right.pack(side="left", fill="y")
        self._right.pack_propagate(False)
        self._build_right_pane()

        # ── Bottom bar ──
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_bottom_bar()

    # ── Left pane ───────────────────────────────────────────

    def _build_left_pane(self) -> None:
        f = self._left
        hdr = tk.Frame(f, bg=HEADER_BG, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  TABLE INFO", bg=HEADER_BG,
                 fg=TEXT_DIM, font=FONT_BOLD).pack(side="left", fill="y")

        body = tk.Frame(f, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(body, text="Target Table Name:",
                 bg=PANEL_BG, fg=TEXT_DIM, font=FONT_SMALL).pack(anchor="w", pady=(0, 2))
        self._tgt_name_var = tk.StringVar(value=self.config_out.target_name)
        name_entry = tk.Entry(body, textvariable=self._tgt_name_var,
                              bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
                              relief="flat", font=FONT_UI, bd=2)
        name_entry.pack(fill="x", pady=(0, 12))
        self._tgt_name_var.trace_add("write", self._on_target_name_change)

        self._stat_rows = self._make_stat(body, "Rows:", "—")
        self._stat_size = self._make_stat(body, "Size:", "—")
        self._stat_cols = self._make_stat(body, "Columns:", "—")
        self._stat_sel  = self._make_stat(body, "Selected:", "—")
        self._stat_pks  = self._make_stat(body, "PKs:", "—")
        self._stat_fks  = self._make_stat(body, "FKs:", "—")
        self._stat_uqs  = self._make_stat(body, "Unique:", "—")
        self._stat_idx  = self._make_stat(body, "Indexes:", "—")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=8)
        for color, label in [
            (PK_COLOR,  "PK — Primary Key"),
            (FK_COLOR,  "FK — Foreign Key"),
            (UQ_COLOR,  "UQ — Unique"),
            (IDX_COLOR, "IDX — Indexed"),
            (TEXT_DIM,  "☐  = Excluded from migration"),
        ]:
            row = tk.Frame(body, bg=PANEL_BG)
            row.pack(anchor="w", pady=1)
            tk.Frame(row, bg=color, width=12, height=12).pack(side="left", padx=(0, 6))
            tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT_DIM,
                     font=FONT_SMALL).pack(side="left")

    def _make_stat(self, parent, label: str, value: str):
        f = tk.Frame(parent, bg=PANEL_BG)
        f.pack(fill="x", pady=2)
        tk.Label(f, text=label, bg=PANEL_BG, fg=TEXT_DIM,
                 font=FONT_SMALL, width=10, anchor="w").pack(side="left")
        lbl = tk.Label(f, text=value, bg=PANEL_BG, fg=TEXT,
                       font=FONT_MONO, anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        return lbl

    # ── Center pane ─────────────────────────────────────────

    def _build_center_pane(self, parent: tk.Frame) -> None:
        # ── Header with selection controls ──
        hdr = tk.Frame(parent, bg=HEADER_BG, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="  COLUMNS",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_BOLD).pack(side="left", fill="y")

        # Select All / None buttons in header
        btn_row = tk.Frame(hdr, bg=HEADER_BG)
        btn_row.pack(side="right", fill="y", padx=6)
        _Btn(btn_row, "☑ All",  cmd=self._select_all_cols,
             bg=HEADER_BG, hov=BTN_HOVER, fg=CHECK_ON,
             padx=8, pady=4, font=FONT_SMALL).pack(side="left", padx=2, pady=4)
        _Btn(btn_row, "☐ None", cmd=self._deselect_all_cols,
             bg=HEADER_BG, hov=BTN_HOVER, fg=DANGER,
             padx=8, pady=4, font=FONT_SMALL).pack(side="left", padx=2, pady=4)

        # ── Treeview area ──
        tree_wrapper = tk.Frame(parent, bg=PANEL_BG)
        tree_wrapper.pack(fill="both", expand=True, padx=4, pady=4)

        vsb = ttk.Scrollbar(tree_wrapper, orient="vertical")
        hsb = ttk.Scrollbar(tree_wrapper, orient="horizontal")

        style = ttk.Style()
        style.configure("Insp.Treeview",
                         background=TREE_ODD, foreground=TEXT,
                         fieldbackground=TREE_ODD, rowheight=26,
                         borderwidth=0, font=FONT_UI)
        style.configure("Insp.Treeview.Heading",
                         background=HEADER_BG, foreground=TEXT_DIM,
                         borderwidth=0, relief="flat", font=FONT_BOLD)
        style.map("Insp.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])

        self.tree = ttk.Treeview(
            tree_wrapper, columns=self.COL_IDS, show="headings",
            selectmode="browse", style="Insp.Treeview",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
        )
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        for col in self.COL_IDS:
            cfg = self.COL_CFG[col]
            self.tree.heading(col, text=cfg["label"], anchor=cfg["anchor"])
            self.tree.column(col, width=cfg["width"],
                             stretch=cfg["stretch"], anchor=cfg["anchor"], minwidth=24)

        self.tree.tag_configure(TAG_ODD,  background=TREE_ODD)
        self.tree.tag_configure(TAG_EVN,  background=TREE_EVEN)
        self.tree.tag_configure(TAG_PK,   foreground=PK_COLOR)
        self.tree.tag_configure(TAG_FK,   foreground=FK_COLOR)
        self.tree.tag_configure(TAG_UQ,   foreground=UQ_COLOR)
        self.tree.tag_configure(TAG_OFF,  foreground="#444c56")   # dimmed = excluded

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_wrapper.grid_rowconfigure(0, weight=1)
        tree_wrapper.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_col_select)
        self.tree.bind("<Button-1>",         self._on_tree_click)
        self.tree.bind("<Double-1>",          self._on_col_double_click)

        # ── Inline column editor panel ──
        self._build_col_editor(parent)

    def _build_col_editor(self, parent: tk.Frame) -> None:
        """Dedicated editor bar below the treeview for direct column name editing."""
        editor = tk.Frame(parent, bg=HEADER_BG, height=44)
        editor.pack(fill="x", side="bottom")
        editor.pack_propagate(False)

        tk.Frame(editor, bg=BORDER, height=1).pack(fill="x", side="top")

        inner = tk.Frame(editor, bg=HEADER_BG)
        inner.pack(fill="both", expand=True, padx=8, pady=4)

        tk.Label(inner, text="Source:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left")
        self._editor_src_var = tk.StringVar(value="—")
        tk.Label(inner, textvariable=self._editor_src_var,
                 bg=HEADER_BG, fg=TEXT, font=FONT_MONO,
                 width=20, anchor="w").pack(side="left", padx=(4, 16))

        tk.Label(inner, text="Target Name:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left")
        self._editor_tgt_var = tk.StringVar()
        self._editor_entry = tk.Entry(
            inner, textvariable=self._editor_tgt_var,
            bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=FONT_UI, bd=2, width=22,
        )
        self._editor_entry.pack(side="left", padx=(4, 8))

        _Btn(inner, "✔ Apply", cmd=self._apply_editor,
             bg="#0f2d1a", hov="#1a4a2e", fg=ACCENT2,
             padx=10, pady=2, font=FONT_SMALL).pack(side="left", padx=(0, 4))
        _Btn(inner, "↺ Reset", cmd=self._reset_editor,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=2, font=FONT_SMALL).pack(side="left")

        tk.Label(inner, text="← Select a column to edit its target name",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_SMALL).pack(side="left", padx=12)

        self._editor_frame = editor
        self._current_edit_col: str = ""

    def _build_right_pane(self) -> None:
        f = self._right
        hdr = tk.Frame(f, bg=HEADER_BG, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  ANALYTICS", bg=HEADER_BG,
                 fg=TEXT_DIM, font=FONT_BOLD).pack(side="left", fill="y")

        self._analytics_frame = tk.Frame(f, bg=PANEL_BG)
        self._analytics_frame.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(self._analytics_frame,
                 text="Select a column\nto see analytics",
                 bg=PANEL_BG, fg=TEXT_DIM, font=FONT_SMALL, justify="center",
                 ).pack(expand=True)

    def _build_bottom_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, height=52)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=HEADER_BG)
        left.pack(side="left", fill="y", padx=10)

        _Btn(left, "🤖 Generate Prompt",
             cmd=self._generate_prompt,
             bg="#1a2a1a", hov="#2a3d2a", fg=ACCENT2, font=FONT_BOLD, pady=8,
             ).pack(side="left", padx=(0, 4))

        self._copy_btn = _Btn(left, "📋 Copy",
                              cmd=self._copy_prompt,
                              bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM, pady=8)
        self._copy_btn.pack(side="left", padx=(0, 4))

        _Btn(left, "📥 Apply AI Response",
             cmd=self._open_ai_importer,
             bg="#1a1a2e", hov="#2a2a4a", fg=ACCENT, font=FONT_BOLD, pady=8,
             ).pack(side="left", padx=(0, 4))

        _Btn(left, "💬 Chat with Ollama",
             cmd=self._open_ollama_chat,
             bg="#1a1530", hov="#2a2648", fg="#c084fc", font=FONT_BOLD, pady=8,
             ).pack(side="left")

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", padx=12, pady=8)

        right = tk.Frame(bar, bg=HEADER_BG)
        right.pack(side="right", fill="y", padx=10)

        _Btn(right, "❌ Cancel All",
             cmd=self._cancel,
             bg="#2a1010", hov="#3d1111", fg=DANGER, pady=8,
             ).pack(side="right", padx=(4, 0))

        mode_label = "▶  Proceed" if self.index == self.total else f"▶  Next  ({self.total - self.index} left)"
        _Btn(right, mode_label,
             cmd=self._proceed,
             bg="#0f2d1a", hov="#1a4a2e", fg=ACCENT2, font=FONT_BOLD, pady=8,
             ).pack(side="right", padx=4)

        _Btn(right, "⏭  Skip",
             cmd=self._skip,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM, pady=8,
             ).pack(side="right", padx=4)

        tk.Label(right, text=f"{self.index} / {self.total}",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_MONO, padx=10,
                 ).pack(side="right")

    # ── Data loading ────────────────────────────────────────

    def _load_data(self) -> None:
        threading.Thread(target=self._bg_load, daemon=True).start()

    def _bg_load(self) -> None:
        info        = get_full_table_info(self.dsn, self.table, self.src_schema)
        quick_stats = get_columns_quick_stats(self.dsn, self.table, self.src_schema)
        self.after(0, lambda: self._apply_info(info, quick_stats))

    def _apply_info(self, info: dict, quick_stats: dict | None = None) -> None:
        self._info        = info
        self._quick_stats = quick_stats or {}
        stats = info.get("stats", {})
        cols  = info.get("columns", [])

        # Init selection — all columns selected by default
        all_names = [c["column_name"] for c in cols]
        self._selected_cols = set(all_names)

        # Update stats
        self._stat_rows.config(text=f"{stats.get('rows', 0):,}")
        self._stat_size.config(text=stats.get("size_pretty", "—"))
        self._stat_cols.config(text=str(len(cols)))
        self._stat_sel.config(text=f"{len(cols)} / {len(cols)}")
        self._stat_pks.config(text=str(len(info.get("primary_keys", set()))))
        self._stat_fks.config(text=str(len(info.get("foreign_keys", {}))))
        self._stat_uqs.config(text=str(len(info.get("unique_cols", set()))))
        self._stat_idx.config(text=str(sum(
            len(v) for v in info.get("indexed_cols", {}).values()
        )))

        self._populate_tree(cols)
        self._status_lbl.config(
            text=f"✅  {len(cols)} columns loaded",
            fg=ACCENT2,
        )

        # If user clicked Chat before data was ready, open it now
        if getattr(self, "_chat_pending", False):
            self._chat_pending = False
            self._status_lbl.config(
                text="🚀 Opening Ollama Chat...",
                fg=ACCENT2,
            )
            self.after(200, self._open_ollama_chat)

    def _populate_tree(self, cols: list | None = None) -> None:
        """Re-render the treeview from self._info."""
        if cols is None:
            cols = self._info.get("columns", [])

        self.tree.delete(*self.tree.get_children())
        pks  = self._info.get("primary_keys",  set())
        fks  = self._info.get("foreign_keys",  {})
        uqs  = self._info.get("unique_cols",   set())
        idxs = self._info.get("indexed_cols",  {})

        for i, col in enumerate(cols):
            name  = col["column_name"]
            ctype = col["data_type"]
            pk    = "✓" if name in pks  else ""
            fk    = "→" if name in fks  else ""
            uq    = "◆" if name in uqs  else ""
            idx   = "⚡" if name in idxs else ""
            tgt   = self.config_out.column_renames.get(name, name)
            is_on = name in self._selected_cols
            chk   = "☑" if is_on else "☐"

            # Quick stats for this column
            qs         = self._quick_stats.get(name, {})
            null_pct   = qs.get("null_pct", None)
            sample_lst = qs.get("sample_values", [])

            # Format null%
            if null_pct is not None:
                nstr = f"{null_pct:.1f}%"
            else:
                nstr = "?"

            # Format sample values: "val1 (40%), val2 (30%)"
            if sample_lst:
                sample_str = "  │  ".join(
                    f"{s['value'][:14]}({s['pct']}%)"
                    for s in sample_lst[:3]
                )
            elif qs.get("has_stats") is False or not qs:
                sample_str = "—"
            else:
                sample_str = "(no common vals)"

            base_tag = TAG_EVN if i % 2 == 0 else TAG_ODD
            if not is_on:
                colour_tag = TAG_OFF
            elif name in pks:
                colour_tag = TAG_PK
            elif name in fks:
                colour_tag = TAG_FK
            elif name in uqs:
                colour_tag = TAG_UQ
            else:
                colour_tag = ""

            tags = (base_tag, colour_tag) if colour_tag else (base_tag,)
            self.tree.insert(
                "", "end", iid=name,
                values=(chk, i + 1, name, ctype, nstr, pk, fk, uq, idx, sample_str, tgt),
                tags=tags,
            )

    # ── Column selection toggle ──────────────────────────────

    def _on_tree_click(self, event) -> None:
        """Toggle ☑/☐ when user clicks the 'sel' column."""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id = self.tree.identify_column(event.x)
        if col_id != "#1":   # #1 = first column = "sel"
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_col_selection(iid)

    def _toggle_col_selection(self, col_name: str) -> None:
        if col_name in self._selected_cols:
            self._selected_cols.discard(col_name)
            self.tree.set(col_name, "sel", "☐")
            # Re-apply dimmed tag
            current_tags = list(self.tree.item(col_name, "tags"))
            new_tags = [t for t in current_tags
                        if t not in (TAG_PK, TAG_FK, TAG_UQ, "")] + [TAG_OFF]
            self.tree.item(col_name, tags=new_tags)
        else:
            self._selected_cols.add(col_name)
            self.tree.set(col_name, "sel", "☑")
            # Restore colour tag
            pks  = self._info.get("primary_keys", set())
            fks  = self._info.get("foreign_keys", {})
            uqs  = self._info.get("unique_cols", set())
            current_tags = [t for t in self.tree.item(col_name, "tags")
                            if t not in (TAG_OFF,)]
            if col_name in pks:
                current_tags.append(TAG_PK)
            elif col_name in fks:
                current_tags.append(TAG_FK)
            elif col_name in uqs:
                current_tags.append(TAG_UQ)
            self.tree.item(col_name, tags=current_tags)

        self._update_sel_stat()

    def _update_sel_stat(self) -> None:
        total = len(self.tree.get_children())
        selected = len(self._selected_cols)
        color = ACCENT2 if selected == total else (WARNING if selected > 0 else DANGER)
        self._stat_sel.config(text=f"{selected} / {total}", fg=color)

    def _select_all_cols(self) -> None:
        for iid in self.tree.get_children():
            if iid not in self._selected_cols:
                self._toggle_col_selection(iid)

    def _deselect_all_cols(self) -> None:
        for iid in list(self._selected_cols):
            self._toggle_col_selection(iid)

    # ── Column selection → editor + analytics ──────────────

    def _on_col_select(self, _=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        col_name = sel[0]

        # Update inline editor
        current_tgt = self.config_out.column_renames.get(col_name, col_name)
        self._current_edit_col = col_name
        self._editor_src_var.set(col_name)
        self._editor_tgt_var.set(current_tgt)
        self._editor_entry.focus_set()
        self._editor_entry.select_range(0, "end")

        # Lazy-load analytics
        col_type = "text"
        for c in self._info.get("columns", []):
            if c["column_name"] == col_name:
                col_type = c["data_type"]
                break

        self._show_analytics_loading(col_name)
        threading.Thread(
            target=self._bg_analytics,
            args=(col_name, col_type),
            daemon=True,
        ).start()

    def _show_analytics_loading(self, col_name: str) -> None:
        for w in self._analytics_frame.winfo_children():
            w.destroy()
        tk.Label(self._analytics_frame,
                 text=f"⏳  Loading\n{col_name}...",
                 bg=PANEL_BG, fg=WARNING, font=FONT_MONO).pack(expand=True)

    def _bg_analytics(self, col_name: str, col_type: str) -> None:
        data = get_column_analytics(self.dsn, self.table, self.src_schema,
                                    col_name, col_type)
        self.after(0, lambda: self._show_analytics(col_name, data))

    def _show_analytics(self, col_name: str, data: dict) -> None:
        for w in self._analytics_frame.winfo_children():
            w.destroy()
        f = self._analytics_frame

        tk.Label(f, text=col_name, bg=PANEL_BG, fg=TEXT,
                 font=FONT_BOLD, anchor="w").pack(fill="x", pady=(0, 6))

        if data.get("error"):
            tk.Label(f, text=f"⚠️ {data['error']}", bg=PANEL_BG,
                     fg=DANGER, font=FONT_SMALL, wraplength=200).pack()
            return

        def _row(lbl, val, color=TEXT):
            r = tk.Frame(f, bg=PANEL_BG)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=lbl, bg=PANEL_BG, fg=TEXT_DIM,
                     font=FONT_SMALL, width=10, anchor="w").pack(side="left")
            tk.Label(r, text=val, bg=PANEL_BG, fg=color,
                     font=FONT_MONO, anchor="w").pack(side="left")

        _row("Distinct:", f"{data['distinct_count']:,}")
        _row("Nulls:",    f"{data['null_count']:,}",
              DANGER if data['null_count'] > 0 else ACCENT2)
        if data["min_val"] is not None:
            _row("Min:", str(data["min_val"]))
            _row("Max:", str(data["max_val"]))

        fk_info = self._info.get("foreign_keys", {}).get(col_name)
        if fk_info:
            tk.Frame(f, bg=BORDER, height=1).pack(fill="x", pady=6)
            tk.Label(f, text="FK Reference:", bg=PANEL_BG,
                     fg=FK_COLOR, font=FONT_SMALL).pack(anchor="w")
            tk.Label(f, text=f"→ {fk_info['ref_table']}.{fk_info['ref_column']}",
                     bg=PANEL_BG, fg=FK_COLOR, font=FONT_MONO, wraplength=200).pack(anchor="w")

        if data["top_values"]:
            tk.Frame(f, bg=BORDER, height=1).pack(fill="x", pady=6)
            tk.Label(f, text="Top Values:", bg=PANEL_BG,
                     fg=TEXT_DIM, font=FONT_SMALL).pack(anchor="w", pady=(0, 3))
            for tv in data["top_values"]:
                row = tk.Frame(f, bg=PANEL_BG)
                row.pack(fill="x", pady=1)
                val_str = str(tv["value"])[:22] + ("…" if len(str(tv["value"])) > 22 else "")
                tk.Label(row, text=val_str, bg=PANEL_BG, fg=TEXT,
                         font=FONT_MONO, width=14, anchor="w").pack(side="left")
                tk.Label(row, text=f"{tv['pct']}%", bg=PANEL_BG,
                         fg=ACCENT, font=FONT_MONO).pack(side="left")

    # ── Inline editor (bottom bar) ────────────────────────

    def _apply_editor(self) -> None:
        """Apply the typed target name from the editor bar."""
        col = self._current_edit_col
        if not col:
            return
        new_name = self._editor_tgt_var.get().strip()
        if not new_name:
            new_name = col   # reset to original
        if new_name != col:
            self.config_out.column_renames[col] = new_name
        elif col in self.config_out.column_renames:
            del self.config_out.column_renames[col]
        # Update treeview cell
        if self.tree.exists(col):
            self.tree.set(col, "target", new_name)
        self._status_lbl.config(text=f"✏️  '{col}' → '{new_name}'", fg=ACCENT)

    def _reset_editor(self) -> None:
        """Reset target name to original source name."""
        col = self._current_edit_col
        if not col:
            return
        self.config_out.column_renames.pop(col, None)
        self._editor_tgt_var.set(col)
        if self.tree.exists(col):
            self.tree.set(col, "target", col)
        self._status_lbl.config(text=f"↺  '{col}' reset", fg=TEXT_DIM)

    # ── Double-click inline edit (legacy — still works) ─────

    def _on_col_double_click(self, event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id  = self.tree.identify_column(event.x)
        col_idx = int(col_id.lstrip("#")) - 1
        if col_idx != self.COL_IDS.index("target"):
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._start_inline_edit(iid)

    def _start_inline_edit(self, iid: str) -> None:
        if self._edit_entry:
            self._commit_inline_edit()
        bbox = self.tree.bbox(iid, "target")
        if not bbox:
            return
        x, y, w, h = bbox
        current_val = self.tree.set(iid, "target")
        self._edit_iid   = iid
        self._edit_entry = tk.Entry(
            self.tree, bg=SEL_BG, fg=TEXT,
            insertbackground=TEXT, relief="flat", font=FONT_UI, bd=0,
        )
        self._edit_entry.insert(0, current_val)
        self._edit_entry.select_range(0, "end")
        self._edit_entry.place(x=x, y=y, width=w, height=h)
        self._edit_entry.focus_set()
        self._edit_entry.bind("<Return>",   lambda _: self._commit_inline_edit())
        self._edit_entry.bind("<Escape>",   lambda _: self._cancel_inline_edit())
        self._edit_entry.bind("<FocusOut>", lambda _: self._commit_inline_edit())

    def _commit_inline_edit(self) -> None:
        if not self._edit_entry:
            return
        new_val  = self._edit_entry.get().strip()
        orig_col = self._edit_iid
        self._edit_entry.destroy()
        self._edit_entry = None
        if new_val and new_val != orig_col:
            self.config_out.column_renames[orig_col] = new_val
            self.tree.set(orig_col, "target", new_val)
            # Sync editor bar
            if self._current_edit_col == orig_col:
                self._editor_tgt_var.set(new_val)
        elif not new_val:
            self.tree.set(orig_col, "target", orig_col)

    def _cancel_inline_edit(self) -> None:
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None

    # ── Target name change ───────────────────────────────────

    def _on_target_name_change(self, *_) -> None:
        self.config_out.target_name = self._tgt_name_var.get().strip() or self.table

    def _open_ollama_chat(self) -> None:
        """Open Ollama chat window with full table context."""
        cols = self._info.get("columns", [])
        sel  = self._selected_cols

        # ── Data not ready yet: queue the request ────────────
        if not cols:
            self._chat_pending = True
            self._status_lbl.config(
                text="⏳ جاري تحميل بيانات الجدول... / Loading table data...",
                fg=WARNING,
            )
            # If _info is empty, the load hasn't started yet — trigger it now
            if not self._info:
                self._load_data()
            from tkinter import messagebox as _mb
            _mb.showinfo(
                "⏳ جاري التحميل / Loading",
                "سيتم فتح نافذة الدردشة تلقائياً بعد اكتمال تحميل بيانات الجدول.\n"
                "The chat window will open automatically once table data finishes loading.",
                parent=self,
            )
            return

        context = {
            "table_name":       self.table,
            "src_schema":       self.src_schema,
            "tgt_schema":       self.tgt_schema,
            "target_name":      self.config_out.target_name,
            "stats":            self._info.get("stats", {}),
            "columns":          cols,
            "primary_keys":     self._info.get("primary_keys",  set()),
            "foreign_keys":     self._info.get("foreign_keys",  {}),
            "unique_cols":      self._info.get("unique_cols",   set()),
            "indexed_cols":     self._info.get("indexed_cols",  {}),
            "quick_stats":      self._quick_stats,
            "column_renames":   self.config_out.column_renames,
            "selected_columns": sel,
            "dsn":              self.dsn,   # ← enables SQL execution in chat
        }

        def _on_apply(changes: dict) -> None:
            """Callback: apply AI-agreed changes back to this inspector."""
            # ── Refresh signal from AI after SQL execution ──
            if changes.get("__refresh__"):
                self._status_lbl.config(
                    text="🔄 جاري تحديث البيانات بعد التنفيذ...",
                    fg=WARNING,
                )
                self._bg_load()   # reload full inspector data
                return

            # Table name
            new_tbl = changes.get("target_table_name", "").strip()
            if new_tbl and new_tbl != self.table:
                self.config_out.target_name = new_tbl
                self._tgt_name_var.set(new_tbl)

            # Column renames
            for orig, new_name in changes.get("column_renames", {}).items():
                if isinstance(orig, str) and isinstance(new_name, str) and new_name.strip():
                    self.config_out.column_renames[orig] = new_name.strip()
                    if self.tree.exists(orig):
                        self.tree.set(orig, "target", new_name.strip())

            # Deselect columns
            for col in changes.get("deselect_columns", []):
                if isinstance(col, str) and col in self._selected_cols:
                    self._toggle_col_selection(col)

            self._status_lbl.config(
                text=f"✅ Ollama changes applied",
                fg=ACCENT2,
            )

        OllamaChatWindow(
            master   = self,
            context  = context,
            on_apply = _on_apply,
        )

    # ── AI Prompt generation ─────────────────────────────────

    def _generate_prompt(self) -> None:
        info     = self._info
        cols     = info.get("columns", [])
        pks      = info.get("primary_keys", set())
        fks      = info.get("foreign_keys", {})
        uqs      = info.get("unique_cols", set())
        idxs     = info.get("indexed_cols", {})
        stats    = info.get("stats", {})
        renames  = self.config_out.column_renames
        selected = self._selected_cols
        qs       = self._quick_stats   # column → pg_stats dict

        total_rows = stats.get("rows", 0) or 1

        lines = [
            "You are a database architect, data-quality expert, and naming specialist.",
            "Below is the complete schema and DATA STATISTICS for a PostgreSQL table being migrated.",
            "Use the null%, distinct values, avg byte size, and sample data to make high-quality decisions.",
            "Your goal: suggest clear, consistent, English snake_case names, identify data-quality issues,",
            "and recommend which columns should be excluded from the migration.",
            "",
            "## Migration Context",
            f"- Source table : `{self.src_schema}.{self.table}`",
            f"- Target table : `{self.tgt_schema}.{self.config_out.target_name}`",
            f"- Estimated rows: {total_rows:,}",
            f"- Table size   : {stats.get('size_pretty', '—')}",
            f"- Columns total: {len(cols)}  |  Selected for migration: {len(selected)}",
            "",
            "## Column Detail with Statistics",
            "```",
            f"{'#':<4} {'Source Column':<28} {'Type':<22} {'Null%':>6}  {'Distinct':>16}  {'AvgWidth':>8}  {'PK':>2} {'FK':>2} {'UQ':>2} {'IDX':>3}  {'Sel':>3}  {'Current Target':<25}  Top-5 Sample Values (% frequency)",
            "─" * 160,
        ]

        for col in cols:
            name   = col["column_name"]
            ctype  = col["data_type"]
            tgt    = renames.get(name, name)
            pk     = "✓" if name in pks  else " "
            fk     = "→" if name in fks  else " "
            uq     = "◆" if name in uqs  else " "
            idx    = "⚡" if name in idxs else " "
            sel    = "☑" if name in selected else "☐"
            cq     = qs.get(name, {})

            null_pct     = cq.get("null_pct", None)
            dist_label   = cq.get("distinct_label", "—")
            avg_width    = cq.get("avg_width", None)
            samples      = cq.get("sample_values", [])
            has_stats    = cq.get("has_stats", False)
            corr         = cq.get("correlation", None)

            null_str  = f"{null_pct:.1f}%" if null_pct is not None else ("?" if not has_stats else "0%")
            avg_str   = f"{avg_width}B"    if avg_width is not None else "—"

            lines.append(
                f"{col['ordinal_position']:<4} {name:<28} {ctype:<22} {null_str:>6}  "
                f"{dist_label:>16}  {avg_str:>8}  {pk:>2} {fk:>2} {uq:>2} {idx:>3}  {sel:>3}  "
                f"{tgt:<25}"
            )
            # Sample values on next indented line
            if samples:
                sample_txt = "  │  ".join(
                    f"'{s['value'][:18]}' ({s['pct']}%)" for s in samples
                )
                lines.append(f"     ↳ Samples: {sample_txt}")
            elif has_stats and not samples:
                lines.append("     ↳ Samples: (no frequent values — high cardinality)")
            elif not has_stats:
                lines.append("     ↳ Samples: (no pg_stats — run ANALYZE on this table)")

            if corr is not None:
                lines.append(f"     ↳ Correlation (monotony): {corr}  (1.0=sorted, 0=random, negative=reverse)")

        lines.append("```")

        # Foreign key relationships
        if fks:
            lines += ["", "## Foreign Key Relationships"]
            for col_name, ref in fks.items():
                tgt_col = renames.get(col_name, col_name)
                lines.append(
                    f"- Source `{col_name}` → `{ref['ref_table']}`.`{ref['ref_column']}`  "
                    f"(migrating as `{tgt_col}`)"
                )

        # Data quality observations
        high_null = [
            (c["column_name"], qs.get(c["column_name"], {}).get("null_pct", 0))
            for c in cols
            if qs.get(c["column_name"], {}).get("null_pct", 0) >= 10
        ]
        if high_null:
            lines += ["", "## ⚠️ High-Null Columns (≥10% NULL)"]
            for cname, npct in sorted(high_null, key=lambda x: -x[1]):
                lines.append(f"- `{cname}`: {npct:.1f}% NULL — consider excluding or flagging")

        lines += [
            "",
            "## Your Task",
            "1. **Table name**: suggest a better target name if needed (English snake_case)",
            "2. **Column names**: rename unclear/abbreviated/non-English names",
            "3. **Exclusions**: list columns that should be excluded (high-null, audit timestamps, internal IDs, etc.)",
            "4. **Data quality**: note any columns with suspicious null rates or sample values",
            "5. **Reasoning**: brief explanation of your decisions",
            "",
            "⚠️ Return ONLY valid JSON — no markdown, no extra text:",
            "```json",
            "{",
            f'  "target_table_name": "{self.config_out.target_name}",',
            '  "column_renames": {',
            '    "old_column_name": "new_column_name"',
            '  },',
            '  "deselect_columns": ["col1", "col2"],',
            '  "data_quality_notes": {',
            '    "column_name": "observation about this column"',
            '  },',
            '  "reasoning": "concise explanation of all decisions made"',
            "}",
            "```",
        ]

        self._prompt_text = "\n".join(lines)
        self._copy_btn.set_text("📋 Copy  ✓")
        self._status_lbl.config(text="🤖 Prompt ready — click Copy", fg=ACCENT2)


    def _copy_prompt(self) -> None:
        if not self._prompt_text:
            self._generate_prompt()
        self.clipboard_clear()
        self.clipboard_append(self._prompt_text)
        self._copy_btn.set_text("✅ Copied!")
        self.after(2000, lambda: self._copy_btn.set_text("📋 Copy"))

    # ── AI Response Importer ─────────────────────────────────

    def _open_ai_importer(self) -> None:
        """Open a dialog where the user can paste AI JSON response."""
        dlg = tk.Toplevel(self)
        dlg.title("📥 Apply AI Response")
        dlg.geometry("700x500")
        dlg.configure(bg=BG)
        dlg.grab_set()

        tk.Frame(dlg, bg=ACCENT, height=3).pack(fill="x")

        # Header
        hdr = tk.Frame(dlg, bg=HEADER_BG, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  📥  Paste the AI's JSON response below",
                 bg=HEADER_BG, fg=TEXT, font=FONT_BOLD, padx=10).pack(side="left", fill="y")

        # Instruction label
        instr = tk.Label(
            dlg,
            text='Expected JSON keys: "target_table_name", "column_renames", "deselect_columns"',
            bg=BG, fg=TEXT_DIM, font=FONT_SMALL, pady=6,
        )
        instr.pack(fill="x", padx=12)

        # Text area
        txt_frame = tk.Frame(dlg, bg=PANEL_BG, highlightbackground=BORDER,
                             highlightthickness=1)
        txt_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        vsb = ttk.Scrollbar(txt_frame, orient="vertical")
        self._ai_text = tk.Text(
            txt_frame, bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", wrap="word",
            yscrollcommand=vsb.set, padx=8, pady=8,
        )
        vsb.config(command=self._ai_text.yview)
        self._ai_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._ai_text.focus_set()

        # Status in dialog
        self._ai_status = tk.Label(dlg, text="", bg=BG, fg=ACCENT2,
                                    font=FONT_MONO, pady=4)
        self._ai_status.pack(fill="x", padx=12)

        # Buttons
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        _Btn(btn_row, "✔  Apply Changes",
             cmd=lambda: self._apply_ai_response(dlg),
             bg="#0f2d1a", hov="#1a4a2e", fg=ACCENT2, font=FONT_BOLD, pady=8,
             ).pack(side="left", padx=(0, 8))
        _Btn(btn_row, "✖  Close",
             cmd=dlg.destroy,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM, pady=8,
             ).pack(side="left")

    def _apply_ai_response(self, dlg: tk.Toplevel) -> None:
        """Parse JSON from dialog and apply changes to config_out + treeview."""
        raw = self._ai_text.get("1.0", "end").strip()

        # Strip markdown code fences if present
        if "```" in raw:
            lines = raw.splitlines()
            raw = "\n".join(
                l for l in lines
                if not l.strip().startswith("```")
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            self._ai_status.config(text=f"❌ JSON Error: {e}", fg=DANGER)
            return

        changes = 0

        # Apply target table name
        new_tbl = data.get("target_table_name", "").strip()
        if new_tbl and new_tbl != self.table:
            self.config_out.target_name = new_tbl
            self._tgt_name_var.set(new_tbl)
            changes += 1

        # Apply column renames
        col_renames = data.get("column_renames", {})
        if isinstance(col_renames, dict):
            for orig, new_name in col_renames.items():
                if not isinstance(orig, str) or not isinstance(new_name, str):
                    continue
                new_name = new_name.strip()
                if new_name and new_name != orig:
                    self.config_out.column_renames[orig] = new_name
                    if self.tree.exists(orig):
                        self.tree.set(orig, "target", new_name)
                    changes += 1

        # Apply deselect_columns
        deselect = data.get("deselect_columns", [])
        if isinstance(deselect, list):
            for col in deselect:
                if isinstance(col, str) and col in self._selected_cols:
                    self._toggle_col_selection(col)
                    changes += 1

        if changes:
            reasoning = data.get("reasoning", "")
            msg = f"✅  Applied {changes} change(s)."
            if reasoning:
                msg += f"  Reason: {reasoning[:80]}"
            self._ai_status.config(text=msg, fg=ACCENT2)
            self._status_lbl.config(
                text=f"✅ AI applied {changes} changes", fg=ACCENT2
            )
        else:
            self._ai_status.config(text="⚠️  No changes found in JSON.", fg=WARNING)

    # ── Navigation actions ───────────────────────────────────

    def _proceed(self) -> None:
        if self._edit_entry:
            self._commit_inline_edit()
        self.config_out.target_name = self._tgt_name_var.get().strip() or self.table
        # Save selected columns (None means all → skip saving set if all selected)
        all_cols = [c["column_name"] for c in self._info.get("columns", [])]
        if self._selected_cols != set(all_cols):
            # Only ordered columns that are selected
            self.config_out.selected_columns = [c for c in all_cols if c in self._selected_cols]
        else:
            self.config_out.selected_columns = None   # all = no restriction
        self.result = "proceed"
        self.destroy()

    def _skip(self) -> None:
        if self._edit_entry:
            self._cancel_inline_edit()
        self.config_out.skipped = True
        self.result = "skip"
        self.destroy()

    def _cancel(self) -> None:
        if messagebox.askyesno(
            "Cancel All?",
            "Cancel the entire migration batch?\nNo tables will be migrated.",
            parent=self,
        ):
            self.result = "cancel"
            self.destroy()


# ─── Flow orchestrator ────────────────────────────────────────

class TableInspectorFlow:
    """
    Runs the inspector window for each table sequentially.
    Returns list[TableConfig] or None if user cancelled entirely.
    """

    def __init__(self, master, tables: list[str], dsn: str,
                 src_schema: str, tgt_schema: str):
        self.master     = master
        self.tables     = tables
        self.dsn        = dsn
        self.src_schema = src_schema
        self.tgt_schema = tgt_schema

    def run(self) -> list[TableConfig] | None:
        total   = len(self.tables)
        configs = [
            TableConfig(original_name=t, target_name=t)
            for t in self.tables
        ]

        i = 0
        while i < total:
            table  = self.tables[i]
            config = configs[i]

            win = TableInspectorWindow(
                master         = self.master,
                table          = table,
                index          = i + 1,
                total          = total,
                dsn            = self.dsn,
                schema         = self.src_schema,
                src_schema     = self.src_schema,
                tgt_schema     = self.tgt_schema,
                initial_config = config,
            )
            self.master.wait_window(win)

            if win.result == "cancel":
                return None
            elif win.result in ("skip", "proceed"):
                i += 1
            else:
                return None   # window closed via X

        return configs
