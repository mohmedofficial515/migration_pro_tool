"""
PostgreSQL Bulk Architect Pro — v4.0
=====================================
UI Architecture:
- ttk.Treeview per panel → O(1) render for 1000+ tables (no widget-per-row overhead)
- connect_timeout=5 in DatabaseEngine — no more terminal freezing
- Instant filter via Treeview tag visibility
- Native multi-select (Ctrl+Click / Shift+Click)
- Sort by column header click
- Thread-safe refresh via after()
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

from src.database.engine import DatabaseEngine
from src.services.migration import run_bulk_migration

load_dotenv()

# ─── Color palette ────────────────────────────────────────────
BG         = "#0f1117"
PANEL_BG   = "#161b22"
HEADER_BG  = "#1c2128"
ACCENT     = "#2f81f7"
ACCENT2    = "#3fb950"
DANGER     = "#f85149"
WARNING    = "#d29922"
TEXT       = "#e6edf3"
TEXT_DIM   = "#8b949e"
BORDER     = "#30363d"
SEL_BG     = "#1f4068"
ENTRY_BG   = "#21262d"
BTN_BG     = "#21262d"
BTN_HOVER  = "#30363d"
TREE_ODD   = "#161b22"
TREE_EVEN  = "#1c2128"

FONT_UI    = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_MONO  = ("Consolas", 9)
FONT_HEAD  = ("Segoe UI", 11, "bold")


# ─── Styles ───────────────────────────────────────────────────

def _apply_styles(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")

    # Treeview body
    style.configure(
        "DB.Treeview",
        background=TREE_ODD,
        foreground=TEXT,
        fieldbackground=TREE_ODD,
        borderwidth=0,
        rowheight=30,
        font=FONT_UI,
    )
    style.configure(
        "DB.Treeview.Heading",
        background=HEADER_BG,
        foreground=TEXT_DIM,
        borderwidth=0,
        relief="flat",
        font=FONT_BOLD,
    )
    style.map(
        "DB.Treeview",
        background=[("selected", SEL_BG)],
        foreground=[("selected", TEXT)],
    )
    style.map(
        "DB.Treeview.Heading",
        background=[("active", BORDER)],
    )

    # Scrollbar
    style.configure(
        "DB.Vertical.TScrollbar",
        background=BORDER,
        troughcolor=PANEL_BG,
        arrowcolor=TEXT_DIM,
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "DB.Horizontal.TScrollbar",
        background=BORDER,
        troughcolor=PANEL_BG,
        arrowcolor=TEXT_DIM,
        borderwidth=0,
        relief="flat",
    )

    # Entry
    style.configure(
        "DB.TEntry",
        fieldbackground=ENTRY_BG,
        foreground=TEXT,
        insertcolor=TEXT,
        borderwidth=1,
        relief="flat",
    )

    # Separator
    style.configure("DB.TSeparator", background=BORDER)


# ─── Rounded-corner tk.Button helper ─────────────────────────

class FlatButton(tk.Frame):
    """Minimal flat button with hover effect."""

    def __init__(self, master, text: str, command=None,
                 bg=BTN_BG, fg=TEXT, hover=BTN_HOVER,
                 width=None, padx=14, pady=5, font=FONT_UI, **kw):
        super().__init__(master, bg=master["bg"] if hasattr(master, "__getitem__") else BG, **kw)
        self._bg  = bg
        self._hov = hover
        self._cmd = command

        self._lbl = tk.Label(
            self, text=text, bg=bg, fg=fg,
            font=font, padx=padx, pady=pady,
            cursor="hand2",
        )
        if width:
            self._lbl.config(width=width)
        self._lbl.pack(fill="both", expand=True)

        self._lbl.bind("<Enter>",   self._on_enter)
        self._lbl.bind("<Leave>",   self._on_leave)
        self._lbl.bind("<Button-1>", self._on_click)

    def configure_text(self, text: str) -> None:
        self._lbl.config(text=text)

    def _on_enter(self, _):  self._lbl.config(bg=self._hov)
    def _on_leave(self, _):  self._lbl.config(bg=self._bg)
    def _on_click(self, _):
        if self._cmd:
            self._cmd()


# ─── PanelView ────────────────────────────────────────────────

class PanelView(tk.Frame):
    """
    Self-contained panel: header, search bar, Treeview table list,
    bulk-action toolbar. All rendering is done inside the Treeview
    so 1000 tables = 0 lag.
    """

    COLS = ("name", "rows", "size")
    COL_CFG = {
        "name": {"label": "Table Name",  "width": 220, "stretch": True,  "anchor": "w"},
        "rows": {"label": "Rows",        "width": 90,  "stretch": False, "anchor": "e"},
        "size": {"label": "Size",        "width": 80,  "stretch": False, "anchor": "e"},
    }

    def __init__(self, master, title: str, dsn: str, side: str, app: "App", **kw):
        super().__init__(master, bg=PANEL_BG, highlightbackground=BORDER,
                         highlightthickness=1, **kw)
        self.dsn   = dsn
        self.side  = side
        self.app   = app
        self._all_rows: list[dict] = []      # Full dataset
        self._sort_col: str = "name"
        self._sort_rev: bool = False
        self._filter_after_id = None         # Debounce ID

        self._build_header(title)
        self._build_info_bar()
        self._build_toolbar()
        self._build_search()
        self._build_treeview()
        self._build_bulk_bar()

    # ── Builder helpers ──────────────────────────────────────

    def _build_header(self, title: str) -> None:
        hdr = tk.Frame(self, bg=HEADER_BG, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        indicator = tk.Frame(hdr, bg=ACCENT, width=4)
        indicator.pack(side="left", fill="y")

        tk.Label(
            hdr, text=title, bg=HEADER_BG, fg=TEXT,
            font=FONT_HEAD, padx=16,
        ).pack(side="left", fill="y")

        self._count_lbl = tk.Label(
            hdr, text="", bg=HEADER_BG, fg=TEXT_DIM,
            font=FONT_MONO, padx=10,
        )
        self._count_lbl.pack(side="right")

    def _build_info_bar(self) -> None:
        self._info_frame = tk.Frame(self, bg=PANEL_BG, height=34)
        self._info_frame.pack(fill="x")
        self._info_frame.pack_propagate(False)

        self._info_lbl = tk.Label(
            self._info_frame,
            text="⏳ Connecting...",
            bg=PANEL_BG, fg=WARNING,
            font=FONT_MONO, padx=14, anchor="w",
        )
        self._info_lbl.pack(fill="both", expand=True)

    def _build_toolbar(self) -> None:
        self._toolbar = tk.Frame(self, bg=PANEL_BG, height=36)
        self._toolbar.pack(fill="x", padx=10, pady=(4, 0))

        # Refresh
        FlatButton(
            self._toolbar, text="⟳  Refresh",
            command=self.app.refresh_ui,
            bg=BTN_BG, width=10,
        ).pack(side="left", padx=(0, 4))

        # Select All / None
        FlatButton(
            self._toolbar, text="☑ All",
            command=self._select_all,
            bg=BTN_BG, width=6,
        ).pack(side="left", padx=2)

        FlatButton(
            self._toolbar, text="☐ None",
            command=self._select_none,
            bg=BTN_BG, width=6,
        ).pack(side="left", padx=2)

        # Sort label (right-aligned)
        self._sort_lbl = tk.Label(
            self._toolbar, text="↕ name", bg=PANEL_BG,
            fg=TEXT_DIM, font=FONT_MONO,
        )
        self._sort_lbl.pack(side="right", padx=6)

    def _build_search(self) -> None:
        sf = tk.Frame(self, bg=PANEL_BG)
        sf.pack(fill="x", padx=10, pady=4)

        tk.Label(sf, text="🔍", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(0, 4))

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)

        entry = ttk.Entry(sf, textvariable=self._search_var, style="DB.TEntry", font=FONT_UI)
        entry.pack(side="left", fill="x", expand=True, ipady=4)

        # Clear button
        FlatButton(
            sf, text="✕", command=lambda: self._search_var.set(""),
            bg=ENTRY_BG, padx=8, pady=2, font=FONT_MONO,
        ).pack(side="left", padx=(4, 0))

    def _build_treeview(self) -> None:
        wrapper = tk.Frame(self, bg=PANEL_BG)
        wrapper.pack(fill="both", expand=True, padx=6, pady=4)

        # Scrollbars
        vsb = ttk.Scrollbar(wrapper, orient="vertical",   style="DB.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(wrapper, orient="horizontal", style="DB.Horizontal.TScrollbar")

        self.tree = ttk.Treeview(
            wrapper,
            columns=self.COLS,
            show="headings",
            selectmode="extended",
            style="DB.Treeview",
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
        )
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        # Configure columns
        for col in self.COLS:
            cfg = self.COL_CFG[col]
            self.tree.heading(
                col, text=cfg["label"],
                command=lambda c=col: self._sort_by(c),
                anchor=cfg["anchor"],
            )
            self.tree.column(
                col,
                width=cfg["width"],
                stretch=cfg["stretch"],
                anchor=cfg["anchor"],
                minwidth=50,
            )

        # Alternating row tags
        self.tree.tag_configure("odd",  background=TREE_ODD)
        self.tree.tag_configure("even", background=TREE_EVEN)

        # Layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrapper.grid_rowconfigure(0, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)

        # Selection change → update bulk bar
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)

    def _build_bulk_bar(self) -> None:
        self._bulk_bar = tk.Frame(self, bg=HEADER_BG, height=44)
        self._bulk_bar.pack_propagate(False)
        # Initially hidden — shown on selection

        self._sel_count_lbl = tk.Label(
            self._bulk_bar, text="", bg=HEADER_BG,
            fg=ACCENT, font=FONT_BOLD, padx=14,
        )
        self._sel_count_lbl.pack(side="left")

        FlatButton(
            self._bulk_bar, text="🚀 Migrate Selected",
            command=self._migrate_selected,
            bg="#1a3a5c", hover="#1f4068", pady=7,
        ).pack(side="left", padx=4)

        FlatButton(
            self._bulk_bar, text="🗑 Drop Selected",
            command=self._drop_selected,
            bg="#3d1111", hover="#5c1a1a", pady=7,
        ).pack(side="left", padx=4)

    # ── Public API ───────────────────────────────────────────

    def set_info(self, text: str, color: str = TEXT_DIM) -> None:
        self._info_lbl.config(text=text, fg=color)

    def set_loading(self) -> None:
        self._info_lbl.config(text="⏳ Loading...", fg=WARNING)
        self._count_lbl.config(text="")
        self.tree.delete(*self.tree.get_children())
        self._all_rows.clear()
        self._hide_bulk_bar()

    def populate(self, info: dict | None, tables: list) -> None:
        """Called from main thread after background fetch completes."""
        # Defensive clear — guards against after() callbacks firing twice
        self.tree.delete(*self.tree.get_children())
        self._all_rows = list(tables)

        if info:
            short_ver = info["ver"].replace("PostgreSQL ", "PG ")[:45]
            self.set_info(
                f"🟢  {short_ver}  │  {info['size']}  │  {len(tables)} tables",
                ACCENT2,
            )
        else:
            self.set_info("🔴  Connection failed  —  check DSN or DB status", DANGER)

        self._render_table(self._all_rows)

    def get_selected_names(self) -> list[str]:
        # values[0] holds the raw table name (no prefix)
        return [self.tree.item(iid)["values"][0] for iid in self.tree.selection()]

    # ── Private helpers ──────────────────────────────────────

    def _render_table(self, rows: list) -> None:
        """Fast bulk-insert into Treeview. Clears previous rows first."""
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(rows):
            tag = "even" if i % 2 == 0 else "odd"
            # Prefix iid with side to avoid TclError when both panels share table names
            iid = f"{self.side}::{row['name']}"
            self.tree.insert(
                "", "end",
                iid=iid,
                values=(row["name"], f"{row['rows']:,}", row["size"]),
                tags=(tag,),
            )
        self._count_lbl.config(text=f"{len(rows)} tables")

    def _on_search_change(self, *_) -> None:
        """Debounce 150 ms then filter."""
        if self._filter_after_id:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(150, self._apply_filter)

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        if not query:
            filtered = self._all_rows
        else:
            filtered = [r for r in self._all_rows if query in r["name"].lower()]
        self._render_table(filtered)

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        key_map = {"name": "name", "rows": "rows", "size": "bytes"}
        key = key_map.get(col, col)
        sorted_rows = sorted(self._all_rows, key=lambda r: r.get(key, 0), reverse=self._sort_rev)
        self._all_rows = sorted_rows

        arrow = " ▼" if self._sort_rev else " ▲"
        self._sort_lbl.config(text=f"↕ {col}{arrow}")
        self._apply_filter()

    def _select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _select_none(self) -> None:
        self.tree.selection_remove(self.tree.get_children())

    def _on_selection_change(self, _=None) -> None:
        count = len(self.tree.selection())
        if count > 0:
            self._show_bulk_bar(count)
        else:
            self._hide_bulk_bar()

    def _show_bulk_bar(self, count: int) -> None:
        self._sel_count_lbl.config(text=f"✓  {count} selected")
        if not self._bulk_bar.winfo_ismapped():
            self._bulk_bar.pack(fill="x", side="bottom")

    def _hide_bulk_bar(self) -> None:
        if self._bulk_bar.winfo_ismapped():
            self._bulk_bar.pack_forget()

    def _migrate_selected(self) -> None:
        tables = self.get_selected_names()
        if tables:
            self.app.initiate_bulk_migration(self.side, tables)

    def _drop_selected(self) -> None:
        tables = self.get_selected_names()
        if tables:
            self.app.initiate_bulk_delete(self.side, tables)


# ─── Main App ─────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("PostgreSQL Bulk Architect Pro  v4.0")
        self.geometry("1520x900")
        self.minsize(900, 600)
        self.configure(bg=BG)

        # Load DSNs
        self.source_dsn = os.getenv("SOURCE_DB_URL", "")
        self.target_dsn = os.getenv("TARGET_DB_URL", "")

        _apply_styles(self)
        self._build_ui()
        self.refresh_ui()

    # ── UI Construction ──────────────────────────────────────

    def _build_ui(self) -> None:
        # ── App header ──
        header = tk.Frame(self, bg=BG, height=60)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="🛡  PostgreSQL Bulk Architect Pro",
            bg=BG, fg=TEXT,
            font=FONT_TITLE, padx=24,
        ).pack(side="left", fill="y")

        self._global_status = tk.Label(
            header, text="", bg=BG, fg=TEXT_DIM, font=FONT_MONO, padx=20,
        )
        self._global_status.pack(side="right", fill="y")

        # ── Separator ──
        ttk.Separator(self, orient="horizontal", style="DB.TSeparator").pack(fill="x")

        # ── Panel container ──
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=16, pady=12)

        self._src_panel = PanelView(
            content,
            title="SOURCE DATABASE",
            dsn=self.source_dsn,
            side="source",
            app=self,
        )
        self._src_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))

        # ── Divider ──
        div = tk.Frame(content, bg=BORDER, width=1)
        div.pack(side="left", fill="y")

        self._tgt_panel = PanelView(
            content,
            title="TARGET DATABASE",
            dsn=self.target_dsn,
            side="target",
            app=self,
        )
        self._tgt_panel.pack(side="left", fill="both", expand=True, padx=(8, 0))

    # ── Data Refresh ─────────────────────────────────────────

    def refresh_ui(self) -> None:
        self._refresh_gen = getattr(self, "_refresh_gen", 0) + 1
        self._global_status.config(text="⟳  Refreshing...", fg=WARNING)
        self._src_panel.set_loading()
        self._tgt_panel.set_loading()
        gen = self._refresh_gen
        threading.Thread(target=self._fetch_all, args=(gen,), daemon=True).start()

    def _fetch_all(self, gen: int) -> None:
        """Runs in background thread — fetches both DBs in parallel."""
        results = {}

        def _fetch(key: str, dsn: str) -> None:
            results[key] = {
                "info":   DatabaseEngine.get_db_info(dsn),
                "tables": DatabaseEngine.get_tables_stats(dsn),
            }

        t1 = threading.Thread(target=_fetch, args=("src", self.source_dsn), daemon=True)
        t2 = threading.Thread(target=_fetch, args=("tgt", self.target_dsn), daemon=True)
        t1.start(); t2.start()
        t1.join();  t2.join()

        # Discard if a newer refresh was triggered while we were fetching
        if gen == getattr(self, "_refresh_gen", 0):
            self.after(0, lambda: self._apply_results(results))

    def _apply_results(self, results: dict) -> None:
        src = results.get("src", {})
        tgt = results.get("tgt", {})

        self._src_panel.populate(src.get("info"), src.get("tables", []))
        self._tgt_panel.populate(tgt.get("info"), tgt.get("tables", []))

        connected = sum([
            1 if src.get("info") else 0,
            1 if tgt.get("info") else 0,
        ])
        self._global_status.config(
            text=f"✓  {connected}/2 databases connected",
            fg=ACCENT2 if connected == 2 else (WARNING if connected == 1 else DANGER),
        )

    # ── Actions ──────────────────────────────────────────────

    def initiate_bulk_delete(self, side: str, table_names: list[str]) -> None:
        dsn = self.source_dsn if side == "source" else self.target_dsn

        if not messagebox.askyesno(
            "Confirm Drop Tables",
            f"Permanently drop {len(table_names)} table(s)?\n\n"
            + "\n".join(f"  • {t}" for t in table_names[:10])
            + (f"\n  … and {len(table_names)-10} more" if len(table_names) > 10 else ""),
        ):
            return

        try:
            conn = psycopg2.connect(dsn, connect_timeout=5)
            conn.autocommit = True
            with conn.cursor() as cur:
                for name in table_names:
                    cur.execute(
                        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(name))
                    )
            conn.close()
            messagebox.showinfo("Done", f"✅  Dropped {len(table_names)} table(s).")
            self.refresh_ui()
        except psycopg2.Error as e:
            messagebox.showerror("Error", str(e))

    def initiate_bulk_migration(self, side: str, table_names: list[str]) -> None:
        from_dsn = self.source_dsn if side == "source" else self.target_dsn
        to_dsn   = self.target_dsn if side == "source" else self.source_dsn

        # Estimate total rows using pg statistics (fast — no COUNT(*))
        total_batch_rows = 0
        try:
            conn = psycopg2.connect(from_dsn, connect_timeout=5)
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(table_names))
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(
                        GREATEST(n_live_tup, CAST(c.reltuples AS BIGINT), 0)
                    ), 0)
                    FROM pg_stat_user_tables s
                    JOIN pg_class c ON s.relid = c.oid
                    WHERE s.relname IN ({placeholders})
                    """,
                    table_names,
                )
                total_batch_rows = cur.fetchone()[0] or 0
            conn.close()
        except psycopg2.Error:
            total_batch_rows = 0  # Non-fatal: progress bar will still work

        threading.Thread(
            target=run_bulk_migration,
            args=(self, table_names, from_dsn, to_dsn, total_batch_rows),
            daemon=True,
        ).start()
