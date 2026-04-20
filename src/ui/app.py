"""
PostgreSQL Bulk Architect Pro — v5.0
=====================================
New in v5.0:
- Schema Selector (Combobox) per panel — loads live schemas from DB
- 🔁 Copy mode: migrate tables preserving source
- ✂️ Move mode: migrate + DROP source tables (with double confirmation)
- All queries are fully schema-qualified ("schema"."table")

Preserved from v4.0:
- ttk.Treeview per panel → O(1) render for 1000+ tables
- connect_timeout=5 — no terminal freezing
- Instant filter via search box + debounce
- Sort by column header click
- Native multi-select (Ctrl+Click / Shift+Click)
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
from src.ui.components.table_inspector import TableInspectorFlow
from src.ui.components.database_chat import DatabaseChatWindow


def _parse_db_name(dsn: str) -> str:
    """
    Extracts the database name from a DSN string.
    Supports both URL format (postgresql://user:pass@host:port/dbname)
    and key=value format (dbname=foo host=bar ...).
    """
    if not dsn:
        return "—"
    try:
        # URL format: postgresql://.../.../dbname
        if dsn.startswith(("postgresql://", "postgres://")):
            from urllib.parse import urlparse
            parsed = urlparse(dsn)
            db = parsed.path.lstrip("/").split("/")[0]
            host = parsed.hostname or "localhost"
            port = parsed.port or 5432
            return f"{db}  @  {host}:{port}"
        # Key-value format
        import re
        m_db   = re.search(r"dbname=([\w-]+)",   dsn)
        m_host = re.search(r"host=([\w.\-]+)",   dsn)
        m_port = re.search(r"port=(\d+)",         dsn)
        db   = m_db.group(1)  if m_db   else "unknown"
        host = m_host.group(1) if m_host else "localhost"
        port = m_port.group(1) if m_port else "5432"
        return f"{db}  @  {host}:{port}"
    except Exception:
        return dsn[:40]

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
SCHEMA_BG  = "#1a1f29"
MOVE_BG    = "#3d1a11"
MOVE_HOVER = "#5c2a1a"
COPY_BG    = "#0f2d1a"
COPY_HOVER = "#1a4a2e"

FONT_UI    = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_MONO  = ("Consolas", 9)
FONT_HEAD  = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 9)


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

    # Combobox
    style.configure(
        "Schema.TCombobox",
        fieldbackground=ENTRY_BG,
        background=BTN_BG,
        foreground=TEXT,
        selectbackground=SEL_BG,
        selectforeground=TEXT,
        arrowcolor=TEXT_DIM,
        borderwidth=1,
        relief="flat",
    )
    style.map(
        "Schema.TCombobox",
        fieldbackground=[("readonly", ENTRY_BG)],
        foreground=[("readonly", TEXT)],
    )

    # Separator
    style.configure("DB.TSeparator", background=BORDER)


# ─── Flat Button helper ──────────────────────────────────────

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

    def config_state(self, enabled: bool) -> None:
        self._lbl.config(state="normal" if enabled else "disabled")

    def _on_enter(self, _):  self._lbl.config(bg=self._hov)
    def _on_leave(self, _):  self._lbl.config(bg=self._bg)
    def _on_click(self, _):
        if self._cmd:
            self._cmd()


# ─── PanelView ────────────────────────────────────────────────

class PanelView(tk.Frame):
    """
    Self-contained panel with:
    - Live schema selector (Combobox) loaded from DB
    - Treeview table list for the selected schema
    - Search/filter + column sort
    - Bulk action bar with 🔁 Copy and ✂️ Move buttons
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
        self.dsn          = dsn
        self.side         = side
        self.app          = app
        self._panel_title = title          # e.g. "SOURCE" or "TARGET"

        self._all_rows:   list[dict] = []
        self._all_tables: list[dict] = []   # populated by populate(); used by AI chat
        self._sort_col: str  = "name"
        self._sort_rev: bool = False
        self._filter_after_id = None

        # Schema state
        self._schema_var     = tk.StringVar(value="public")
        self._schemas: list[str] = ["public"]

        self._build_header(title)
        self._build_schema_bar()
        self._build_info_bar()
        self._build_toolbar()
        self._build_search()
        self._build_treeview()
        self._build_bulk_bar()

    # ── Properties ───────────────────────────────────────────

    @property
    def current_schema(self) -> str:
        return self._schema_var.get() or "public"

    # ── Builder helpers ──────────────────────────────────────

    def _build_header(self, title: str) -> None:
        hdr = tk.Frame(self, bg=HEADER_BG, height=64)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        indicator = tk.Frame(hdr, bg=ACCENT, width=4)
        indicator.pack(side="left", fill="y")

        # Title + DB name stacked vertically
        label_frame = tk.Frame(hdr, bg=HEADER_BG, padx=16)
        label_frame.pack(side="left", fill="y", pady=6)

        tk.Label(
            label_frame, text=title, bg=HEADER_BG, fg=TEXT,
            font=FONT_HEAD, anchor="w",
        ).pack(anchor="w")

        db_name = _parse_db_name(self.dsn)
        tk.Label(
            label_frame,
            text=f"🗄  {db_name}",
            bg=HEADER_BG, fg=ACCENT,
            font=FONT_MONO, anchor="w",
        ).pack(anchor="w")

        self._count_lbl = tk.Label(
            hdr, text="", bg=HEADER_BG, fg=TEXT_DIM,
            font=FONT_MONO, padx=10,
        )
        self._count_lbl.pack(side="right")

    def _build_schema_bar(self) -> None:
        """Schema selector bar shown below the header."""
        bar = tk.Frame(self, bg=SCHEMA_BG, height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(
            bar, text="  Schema:", bg=SCHEMA_BG, fg=TEXT_DIM,
            font=FONT_SMALL,
        ).pack(side="left", padx=(10, 4))

        self._schema_combo = ttk.Combobox(
            bar,
            textvariable=self._schema_var,
            values=self._schemas,
            state="readonly",
            style="Schema.TCombobox",
            width=22,
            font=FONT_UI,
        )
        self._schema_combo.pack(side="left", pady=6)
        self._schema_combo.bind("<<ComboboxSelected>>", self._on_schema_change)

        # Reload schemas button
        FlatButton(
            bar, text="⟳", command=self._reload_schemas,
            bg=SCHEMA_BG, hover=BORDER,
            padx=8, pady=2, font=FONT_MONO,
        ).pack(side="left", padx=4)

        self._schema_status = tk.Label(
            bar, text="", bg=SCHEMA_BG, fg=TEXT_DIM,
            font=FONT_MONO, padx=6,
        )
        self._schema_status.pack(side="left")

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

        FlatButton(
            self._toolbar, text="⟳  Refresh",
            command=self.app.refresh_ui,
            bg=BTN_BG, width=10,
        ).pack(side="left", padx=(0, 4))

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

        # 💬 AI Chat button — per database panel
        FlatButton(
            self._toolbar, text="💬 AI Chat",
            command=self._open_db_chat,
            bg="#0f2d1a", hover="#1a4a2e",
            fg="#3fb950", width=10,
        ).pack(side="left", padx=(8, 2))

        self._sort_lbl = tk.Label(
            self._toolbar, text="↕ name", bg=PANEL_BG,
            fg=TEXT_DIM, font=FONT_MONO,
        )
        self._sort_lbl.pack(side="right", padx=6)

    def _open_db_chat(self) -> None:
        """Open AI chat for all tables in this database panel."""
        tables = self._all_tables   # stored in populate()
        dsn    = self.dsn
        label  = f"{self._panel_title}  ({self.current_schema})"
        if not tables:
            from tkinter import messagebox
            messagebox.showinfo("No Data", "Load tables first.", parent=self)
            return
        DatabaseChatWindow(master=self, dsn=dsn, label=label,
                           schema=self.current_schema, tables=tables)

    def _build_search(self) -> None:
        sf = tk.Frame(self, bg=PANEL_BG)
        sf.pack(fill="x", padx=10, pady=4)

        tk.Label(sf, text="🔍", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(0, 4))

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)

        entry = ttk.Entry(sf, textvariable=self._search_var, style="DB.TEntry", font=FONT_UI)
        entry.pack(side="left", fill="x", expand=True, ipady=4)

        FlatButton(
            sf, text="✕", command=lambda: self._search_var.set(""),
            bg=ENTRY_BG, padx=8, pady=2, font=FONT_MONO,
        ).pack(side="left", padx=(4, 0))

    def _build_treeview(self) -> None:
        wrapper = tk.Frame(self, bg=PANEL_BG)
        wrapper.pack(fill="both", expand=True, padx=6, pady=4)

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

        self.tree.tag_configure("odd",  background=TREE_ODD)
        self.tree.tag_configure("even", background=TREE_EVEN)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrapper.grid_rowconfigure(0, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)

    def _build_bulk_bar(self) -> None:
        self._bulk_bar = tk.Frame(self, bg=HEADER_BG, height=50)
        self._bulk_bar.pack_propagate(False)

        self._sel_count_lbl = tk.Label(
            self._bulk_bar, text="", bg=HEADER_BG,
            fg=ACCENT, font=FONT_BOLD, padx=14,
        )
        self._sel_count_lbl.pack(side="left")

        # 🔁 Copy button
        FlatButton(
            self._bulk_bar, text="🔁 Copy",
            command=self._copy_selected,
            bg=COPY_BG, hover=COPY_HOVER,
            fg="#3fb950", pady=8, padx=16,
            font=FONT_BOLD,
        ).pack(side="left", padx=4)

        # ✂️ Move button
        FlatButton(
            self._bulk_bar, text="✂️ Move",
            command=self._move_selected,
            bg=MOVE_BG, hover=MOVE_HOVER,
            fg="#f85149", pady=8, padx=16,
            font=FONT_BOLD,
        ).pack(side="left", padx=4)

        # 🗑 Drop button
        FlatButton(
            self._bulk_bar, text="🗑 Drop",
            command=self._drop_selected,
            bg="#2a1010", hover="#3d1111",
            fg=TEXT_DIM, pady=8, padx=12,
        ).pack(side="left", padx=4)

    # ── Schema Methods ───────────────────────────────────────

    def load_schemas(self, schemas: list[str]) -> None:
        """Called from App after fetching schemas in background thread."""
        if not schemas:
            schemas = ["public"]
        self._schemas = schemas
        self._schema_combo["values"] = schemas
        current = self._schema_var.get()
        if current not in schemas:
            self._schema_var.set(schemas[0])
        self._schema_status.config(
            text=f"{len(schemas)} schema{'s' if len(schemas) != 1 else ''}",
            fg=TEXT_DIM,
        )

    def _on_schema_change(self, _=None) -> None:
        """User changed the schema → reload the table list for this schema."""
        schema = self.current_schema
        self._schema_status.config(text=f"⟳ Loading {schema}...", fg=WARNING)
        self.set_loading_tables()
        threading.Thread(
            target=self._fetch_tables_for_schema,
            args=(schema,),
            daemon=True,
        ).start()

    def _reload_schemas(self) -> None:
        """Reload the schemas list from DB in background."""
        self._schema_status.config(text="⟳ Refreshing...", fg=WARNING)
        threading.Thread(
            target=self._bg_reload_schemas,
            daemon=True,
        ).start()

    def _bg_reload_schemas(self) -> None:
        schemas = DatabaseEngine.get_schemas(self.dsn)
        self.after(0, lambda: self.load_schemas(schemas))
        self.after(0, self._on_schema_change)

    def _fetch_tables_for_schema(self, schema: str) -> None:
        tables = DatabaseEngine.get_tables_stats(self.dsn, schema)
        self.after(0, lambda: self._populate_tables(tables, schema))

    def _populate_tables(self, tables: list, schema: str) -> None:
        self._all_rows   = list(tables)
        self._all_tables = list(tables)   # ← keep AI chat context in sync
        self._render_table(self._all_rows)
        self._schema_status.config(
            text=f"{len(tables)} table{'s' if len(tables) != 1 else ''}",
            fg=ACCENT2,
        )

    # ── Public API ───────────────────────────────────────────

    def set_info(self, text: str, color: str = TEXT_DIM) -> None:
        self._info_lbl.config(text=text, fg=color)

    def set_loading(self) -> None:
        """Full panel loading state (used during full refresh)."""
        self._info_lbl.config(text="⏳ Connecting...", fg=WARNING)
        self._count_lbl.config(text="")
        self._schema_status.config(text="", fg=TEXT_DIM)
        self._schema_combo["values"] = ["public"]
        self.set_loading_tables()

    def set_loading_tables(self) -> None:
        """Only clear the table list (used on schema change)."""
        self.tree.delete(*self.tree.get_children())
        self._all_rows.clear()
        self._count_lbl.config(text="")
        self._hide_bulk_bar()

    def populate(
        self,
        info: dict | None,
        tables: list[dict],
        schemas: list[str],
    ) -> None:
        self._all_tables = tables   # keep for AI chat context
        """Called from main thread after background fetch completes."""
        self.tree.delete(*self.tree.get_children())
        self._all_rows = list(tables)

        if info:
            short_ver = info["ver"].replace("PostgreSQL ", "PG ")[:45]
            self.set_info(
                f"🟢  {short_ver}  │  {info['size']}",
                ACCENT2,
            )
        else:
            self.set_info("🔴  Connection failed  —  check DSN or DB status", DANGER)

        self.load_schemas(schemas)
        self._render_table(self._all_rows)

    def get_selected_names(self) -> list[str]:
        return [self.tree.item(iid)["values"][0] for iid in self.tree.selection()]

    # ── Private helpers ──────────────────────────────────────

    def _render_table(self, rows: list) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(rows):
            tag = "even" if i % 2 == 0 else "odd"
            iid = f"{self.side}::{row['name']}"
            self.tree.insert(
                "", "end",
                iid=iid,
                values=(row["name"], f"{row['rows']:,}", row["size"]),
                tags=(tag,),
            )
        self._count_lbl.config(text=f"{len(rows)} tables")

    def _on_search_change(self, *_) -> None:
        if self._filter_after_id:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(150, self._apply_filter)

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        filtered = self._all_rows if not query else [
            r for r in self._all_rows if query in r["name"].lower()
        ]
        self._render_table(filtered)

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        key_map = {"name": "name", "rows": "rows", "size": "bytes"}
        key = key_map.get(col, col)
        sorted_rows = sorted(
            self._all_rows,
            key=lambda r: r.get(key, 0),
            reverse=self._sort_rev,
        )
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

    def _copy_selected(self) -> None:
        tables = self.get_selected_names()
        if tables:
            self.app.initiate_bulk_migration(self.side, tables, move_mode=False)

    def _move_selected(self) -> None:
        tables = self.get_selected_names()
        if not tables:
            return

        # Double confirmation for destructive move
        src_schema = self.current_schema
        tgt_panel  = self.app._tgt_panel if self.side == "source" else self.app._src_panel
        tgt_schema = tgt_panel.current_schema

        confirmed = messagebox.askyesno(
            "⚠️  Confirm MOVE Operation",
            f"MOVE will copy {len(tables)} table(s) from:\n"
            f"  {src_schema} → {tgt_schema}\n\n"
            f"Then permanently DROP them from '{src_schema}'.\n\n"
            f"{'  • ' + chr(10).join(f'  • {t}' for t in tables[:8])}"
            f"{chr(10) + f'  … and {len(tables)-8} more' if len(tables) > 8 else ''}\n\n"
            "This CANNOT be undone. Continue?",
            icon="warning",
        )
        if confirmed:
            self.app.initiate_bulk_migration(self.side, tables, move_mode=True)

    def _drop_selected(self) -> None:
        tables = self.get_selected_names()
        if tables:
            self.app.initiate_bulk_delete(self.side, tables)


# ─── Main App ─────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("PostgreSQL Bulk Architect Pro  v5.0")
        self.geometry("1520x920")
        self.minsize(900, 600)
        self.configure(bg=BG)

        self.source_dsn = os.getenv("SOURCE_DB_URL", "")
        self.target_dsn = os.getenv("TARGET_DB_URL", "")

        _apply_styles(self)
        self._build_ui()
        self.refresh_ui()

    # ── UI Construction ──────────────────────────────────────

    def _build_ui(self) -> None:
        # ── App header ──
        header = tk.Frame(self, bg=BG, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="🛡  PostgreSQL Bulk Architect Pro",
            bg=BG, fg=TEXT,
            font=FONT_TITLE, padx=24,
        ).pack(side="left", fill="y")

        # 🌐 Global AI Chat button
        FlatButton(
            header, text="🌐 Global AI Chat",
            command=self._open_global_chat,
            bg="#1a1f3a", hover="#252b50",
            fg="#a5d6ff", padx=14,
        ).pack(side="left", fill="y", padx=(0, 10))

        # Legend
        leg = tk.Frame(header, bg=BG)
        leg.pack(side="right", padx=20, fill="y")

        tk.Label(leg, text="🔁 Copy = migrate, keep source",
                 bg=BG, fg=ACCENT2, font=FONT_SMALL).pack(anchor="e")
        tk.Label(leg, text="✂️ Move = migrate + DROP source",
                 bg=BG, fg=DANGER,  font=FONT_SMALL).pack(anchor="e")

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

        def _fetch(key: str, dsn: str, schema: str) -> None:
            results[key] = {
                "info":    DatabaseEngine.get_db_info(dsn),
                "tables":  DatabaseEngine.get_tables_stats(dsn, schema),
                "schemas": DatabaseEngine.get_schemas(dsn),
            }

        src_schema = self._src_panel.current_schema
        tgt_schema = self._tgt_panel.current_schema

        t1 = threading.Thread(target=_fetch, args=("src", self.source_dsn, src_schema), daemon=True)
        t2 = threading.Thread(target=_fetch, args=("tgt", self.target_dsn, tgt_schema), daemon=True)
        t1.start(); t2.start()
        t1.join();  t2.join()

        if gen == getattr(self, "_refresh_gen", 0):
            self.after(0, lambda: self._apply_results(results))

    def _apply_results(self, results: dict) -> None:
        src = results.get("src", {})
        tgt = results.get("tgt", {})

        self._src_panel.populate(
            src.get("info"),
            src.get("tables", []),
            src.get("schemas", ["public"]),
        )
        self._tgt_panel.populate(
            tgt.get("info"),
            tgt.get("tables", []),
            tgt.get("schemas", ["public"]),
        )

        connected = sum([
            1 if src.get("info") else 0,
            1 if tgt.get("info") else 0,
        ])
        self._global_status.config(
            text=f"✓  {connected}/2 databases connected",
            fg=ACCENT2 if connected == 2 else (WARNING if connected == 1 else DANGER),
        )

    # ── AI Chat ──────────────────────────────────────────────

    def _open_global_chat(self) -> None:
        """Open AI chat with all tables from both databases combined."""
        src_tables = getattr(self._src_panel, "_all_tables", [])
        tgt_tables = getattr(self._tgt_panel, "_all_tables", [])
        all_tables = [
            {**t, "_db": "SOURCE"} for t in src_tables
        ] + [
            {**t, "_db": "TARGET"} for t in tgt_tables
        ]
        if not all_tables:
            from tkinter import messagebox
            messagebox.showinfo(
                "No Data",
                "No tables loaded yet. Try refreshing first.",
                parent=self,
            )
            return
        DatabaseChatWindow(
            master=self,
            dsn=self.source_dsn,
            label="Global — SOURCE + TARGET databases",
            schema="all",
            tables=all_tables,
        )

    # ── Actions ──────────────────────────────────────────────

    def initiate_bulk_delete(self, side: str, table_names: list[str]) -> None:
        panel  = self._src_panel if side == "source" else self._tgt_panel
        dsn    = self.source_dsn if side == "source" else self.target_dsn
        schema = panel.current_schema

        if not messagebox.askyesno(
            "Confirm Drop Tables",
            f"Permanently drop {len(table_names)} table(s) from schema '{schema}'?\n\n"
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
                        sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                            sql.Identifier(schema),
                            sql.Identifier(name),
                        )
                    )
            conn.close()
            messagebox.showinfo("Done", f"✅  Dropped {len(table_names)} table(s) from '{schema}'.")
            self.refresh_ui()
        except psycopg2.Error as e:
            messagebox.showerror("Error", str(e))

    def initiate_bulk_migration(
        self,
        side: str,
        table_names: list[str],
        move_mode: bool = False,
    ) -> None:
        src_panel = self._src_panel if side == "source" else self._tgt_panel
        tgt_panel = self._tgt_panel if side == "source" else self._src_panel

        from_dsn   = self.source_dsn if side == "source" else self.target_dsn
        to_dsn     = self.target_dsn if side == "source" else self.source_dsn
        src_schema = src_panel.current_schema
        tgt_schema = tgt_panel.current_schema

        # ── Step 1: Table Inspector Flow ──
        # Opens a Toplevel inspector window per table (block until all done)
        flow = TableInspectorFlow(
            master     = self,
            tables     = table_names,
            dsn        = from_dsn,
            src_schema = src_schema,
            tgt_schema = tgt_schema,
        )
        table_configs = flow.run()   # blocks — returns list[TableConfig] or None

        if table_configs is None:
            # User cancelled the entire batch
            return

        # Filter out skipped tables
        active_configs = [c for c in table_configs if not c.skipped]
        if not active_configs:
            messagebox.showinfo("Skipped", "All tables were skipped. Nothing to migrate.")
            return

        active_tables = [c.original_name for c in active_configs]

        # ── Step 2: Estimate total rows ──
        total_batch_rows = 0
        try:
            conn = psycopg2.connect(from_dsn, connect_timeout=5)
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(active_tables))
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(
                        GREATEST(n_live_tup, CAST(c.reltuples AS BIGINT), 0)
                    ), 0)
                    FROM pg_stat_user_tables s
                    JOIN pg_class c ON s.relid = c.oid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s
                      AND s.relname IN ({placeholders})
                    """,
                    [src_schema] + active_tables,
                )
                total_batch_rows = cur.fetchone()[0] or 0
            conn.close()
        except psycopg2.Error:
            total_batch_rows = 0

        # ── Step 3: Launch migration thread with configs ──
        threading.Thread(
            target=run_bulk_migration,
            args=(self, active_tables, from_dsn, to_dsn, total_batch_rows),
            kwargs={
                "src_schema":    src_schema,
                "tgt_schema":    tgt_schema,
                "move_mode":     move_mode,
                "table_configs": active_configs,
            },
            daemon=True,
        ).start()
