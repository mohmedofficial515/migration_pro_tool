"""
Ollama Chat Window - Modern Chat UI Edition
============================================
Professional bubble-based chat interface connected to local Ollama.
Pre-loaded with full PostgreSQL table schema context.

Layout:
  - Left panel : modern chat bubbles (user right / AI left)
  - Right panel: Table Context + action buttons
  - Bottom     : multi-line input with RTL auto-detect

Features:
  • Bubble-style messages (ChatGPT-like) with avatars
  • Code-block highlighting inside AI bubbles
  • Real-time streaming with live token append
  • SQL classification (4-level security) + approval dialog
  • Arabic / English bilingual with automatic RTL detection
"""

from __future__ import annotations

import datetime
import json
import re
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from urllib import request
from urllib.error import URLError
from typing import Callable

try:
    import psycopg2
    from psycopg2 import extras as pg_extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

try:
    from src.ai.sql_constitution import build_constitution as _build_sql_constitution
    _SQL_CONSTITUTION_AVAILABLE = True
except ImportError:
    _SQL_CONSTITUTION_AVAILABLE = False
    def _build_sql_constitution(schema="public", table="table") -> str:
        return ""

from src.database.inspector import resolve_active_table

try:
    from src.ai.chat_history import ChatHistory
    _CHAT_HISTORY_AVAILABLE = True
except ImportError:
    _CHAT_HISTORY_AVAILABLE = False
    ChatHistory = None  # type: ignore


# ─── Palette ─────────────────────────────────────────────────
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
ENTRY_BG   = "#21262d"
BTN_BG     = "#21262d"
BTN_HOVER  = "#30363d"

# ── Chat bubble colors
USER_BUBBLE   = "#1b3a5c"   # deep blue for user
AI_BUBBLE     = "#1e2330"   # dark slate for AI
CODE_BG       = "#0d1117"   # darker for code blocks
SYS_TEXT      = "#6e7681"   # dimmed system notes

FONT_UI    = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_MONO  = ("Consolas",  9)
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_SMALL = ("Segoe UI",  9)
FONT_CHAT  = ("Segoe UI", 10)

OLLAMA_BASE_DEFAULT  = "http://localhost:11434"
OLLAMA_MODEL_DEFAULT = "glm-5:cloud"
INPUT_PLACEHOLDER    = "اكتب رسالتك...  /  Type your message..."

PREDEFINED_MODELS: list[str] = [
    "llama3.2", "llama3.2:1b", "llama3.2:3b",
    "llama3.1:8b", "llama3.1:70b", "llama3:8b", "llama3:70b",
    "mistral", "mistral:7b", "mistral-nemo",
    "phi4", "phi4-mini", "phi3", "phi3:mini",
    "gemma3:4b", "gemma3:12b", "gemma3:27b",
    "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b", "qwen2.5:72b",
    "deepseek-r1:7b", "deepseek-r1:14b", "deepseek-r1:32b", "deepseek-v2:16b",
    "codellama", "codellama:13b", "codegemma:7b", "llava:13b",
    "kimi-k2.5:cloud", "glm-5:cloud", "minimax-m2.7:cloud",
    "gemma4:31b-cloud", "qwen3.5:397b-cloud",
    "gpt-oss:120b-cloud", "gpt-oss:20b-cloud",
]

# ─── SQL Security ────────────────────────────────────────────
RISK_BLOCKED = "BLOCKED";  RISK_DANGER = "DANGER"
RISK_WRITE   = "WRITE";    RISK_READ   = "READ"

RISK_COLOR = {
    RISK_BLOCKED: "#f85149", RISK_DANGER: "#e3682a",
    RISK_WRITE:   "#d29922", RISK_READ:   "#3fb950",
}
RISK_LABEL = {
    RISK_BLOCKED: "[blocked]  محظور - Blocked",
    RISK_DANGER:  "[red]  خطر - Structural DDL",
    RISK_WRITE:   "[yellow]  تعديل - Data Write",
    RISK_READ:    "[green]  قراءة - Read Only",
}
RISK_CONFIRM_KEYWORD = {RISK_DANGER: "EXECUTE", RISK_WRITE: "CONFIRM"}

_BLOCKED_PAT = [
    re.compile(r'\bDROP\s+DATABASE\b',  re.I),
    re.compile(r'\bDROP\s+SCHEMA\b',    re.I),
    re.compile(r'\bTRUNCATE\b',         re.I),
    re.compile(r'\bDELETE\s+FROM\s+\S+\s*(?:;|$)', re.I | re.M),
]
_DANGER_PAT = [
    re.compile(r'\bALTER\s+TABLE\b',  re.I),
    re.compile(r'\bDROP\s+TABLE\b',   re.I),
    re.compile(r'\bRENAME\s+',        re.I),
    re.compile(r'\bCREATE\s+TABLE\b', re.I),
    re.compile(r'\bCREATE\s+INDEX\b', re.I),
    re.compile(r'\bDROP\s+INDEX\b',   re.I),
    re.compile(r'\bADD\s+COLUMN\b',   re.I),
    re.compile(r'\bDROP\s+COLUMN\b',  re.I),
]
_WRITE_PAT = [
    re.compile(r'\bINSERT\s+INTO\b',        re.I),
    re.compile(r'\bUPDATE\s+\S+\s+SET\b',   re.I),
    re.compile(r'\bDELETE\s+FROM\b',        re.I),
]


def _classify_sql(sql: str) -> str:
    for p in _BLOCKED_PAT:
        if p.search(sql): return RISK_BLOCKED
    for p in _DANGER_PAT:
        if p.search(sql): return RISK_DANGER
    for p in _WRITE_PAT:
        if p.search(sql): return RISK_WRITE
    return RISK_READ


_INLINE_COMMENT  = re.compile(r'--[^\n]*')           # -- ...
_BLOCK_COMMENT   = re.compile(r'/\*.*?\*/', re.DOTALL)  # /* ... */
_PLACEHOLDER_RE  = re.compile(
    r'<[^>]+>|\[YOUR_[^\]]+\]|\{\{[^}]+\}\}', re.I
)
_ELLIPSIS_LINE   = re.compile(r'(?m)^\s*\.\.\.\s*$')  # lone ... lines
_MULTI_SEMI = re.compile(r';\s*;+')                   # duplicate semicolons


def _clean_sql(sql: str) -> str:
    """
    Strip everything that would cause psycopg2 to reject the statement:
      1. Block comments  /* ... */
      2. Line comments   -- ...
      3. Placeholder tokens  <name>, {{var}}, [YOUR_*]
      4. Lone ellipsis lines  ...
      5. Normalize whitespace / blank lines
      6. Ensure single trailing semicolon
    """
    s = _BLOCK_COMMENT.sub('', sql)
    s = _INLINE_COMMENT.sub('', s)
    s = _PLACEHOLDER_RE.sub('', s)
    s = _ELLIPSIS_LINE.sub('', s)
    s = _MULTI_SEMI.sub(';', s)
    # collapse blank lines
    lines = [ln.rstrip() for ln in s.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    s = '\n'.join(lines).strip()
    if s and not s.endswith(';'):
        s += ';'
    return s


_RTL_RE = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
)


def _is_rtl(text: str) -> bool:
    return bool(_RTL_RE.search(text))


def _now_str() -> str:
    return datetime.datetime.now().strftime("%H:%M")


# ─── Flat Button ─────────────────────────────────────────────

class _Btn(tk.Frame):
    def __init__(self, master, text, cmd=None,
                 bg=BTN_BG, fg=TEXT, hov=BTN_HOVER,
                 padx=10, pady=5, font=FONT_UI, **kw):
        super().__init__(master, bg=bg, **kw)
        self._bg = bg; self._hov = hov; self._cmd = cmd; self._fg = fg
        self._lbl = tk.Label(self, text=text, bg=bg, fg=fg,
                             font=font, padx=padx, pady=pady, cursor="hand2")
        self._lbl.pack(fill="both", expand=True)
        self._lbl.bind("<Enter>",    lambda _: self._lbl.config(bg=self._hov))
        self._lbl.bind("<Leave>",    lambda _: self._lbl.config(bg=self._bg))
        self._lbl.bind("<Button-1>", lambda _: (self._cmd() if self._cmd else None))

    def set_text(self, t): self._lbl.config(text=t)
    def set_fg(self, c):   self._lbl.config(fg=c)
    
    def set_disabled(self):
        self._lbl.unbind("<Enter>")
        self._lbl.unbind("<Leave>")
        self._lbl.unbind("<Button-1>")
        self._lbl.config(fg="#484f58", cursor="arrow")

    def set_enabled(self):
        self._lbl.bind("<Enter>",    lambda _: self._lbl.config(bg=self._hov))
        self._lbl.bind("<Leave>",    lambda _: self._lbl.config(bg=self._bg))
        self._lbl.bind("<Button-1>", lambda _: (self._cmd() if self._cmd else None))
        self._lbl.config(fg=self._fg, cursor="hand2")


# ─── Main Chat Window ─────────────────────────────────────────

class OllamaChatWindow(tk.Toplevel):
    """Modern bubble-based Ollama Chat - Arabic/English, SQL execution, streaming."""

    def __init__(self,
                 master:   tk.Widget,
                 context:  dict,
                 on_apply: Callable[[dict], None]):
        super().__init__(master)

        self.context   = context
        self.on_apply  = on_apply

        # State
        self._messages:       list[dict] = []
        self._streaming:      bool       = False
        self._pending_sql:    str        = ""
        self._pending_risk:   str        = RISK_READ
        self._pending_verify: str        = ""
        self._last_ai_msg:    str        = ""
        self._base_url        = OLLAMA_BASE_DEFAULT
        self._model_var       = tk.StringVar(value=OLLAMA_MODEL_DEFAULT)
        self._url_var         = tk.StringVar(value=OLLAMA_BASE_DEFAULT)
        self._dsn: str        = context.get("dsn", "")
        self._rel_context:    dict       = {}   # FK discovery results
        self._workspace_snapshots: list[str] = []
        self._undo_btn: tk.Widget | None     = None

        # Chat UI state
        self._stream_text_widget: tk.Text | None = None
        self._stream_bubble_frame: tk.Frame | None = None
        self._dynamic_labels: list[tk.Label] = []   # for wraplength resize
        self._current_agent: str = 'general'
        self._agent_btns:    dict = {}


        self._setup_window()
        self._data_ready = bool(context.get("columns"))

        # ── Chat History (per-table persistence) ─────────────────────────
        import os
        _project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        schema = context.get("src_schema", "public")
        table  = context.get("table_name", "table")
        if _CHAT_HISTORY_AVAILABLE and ChatHistory is not None:
            self._history = ChatHistory(_project_root, schema, table)
        else:
            self._history = None

        self._build_system_prompt()
        self._build_ui()
        self._check_connection()
        # Fetch relationship context in background
        threading.Thread(target=self._fetch_relationship_context, daemon=True).start()

        if not self._data_ready:
            self.after(500, self._warn_no_data)

        # ── Restore previous messages into UI ──────────────────────────
        if self._history and self._history.has_history:
            self.after(300, self._restore_history_ui)
        else:
            # First open: fetch live column samples & send initial analysis
            self.after(1200, self._trigger_initial_analysis)

    # ── Window ────────────────────────────────────────────────

    def _setup_window(self) -> None:
        ctx = self.context
        self.title(f"💬 Ollama Chat - {ctx.get('src_schema')}.{ctx.get('table_name')}")
        self.geometry("1220x780")
        self.minsize(840, 540)
        self.configure(bg=BG)
        self.resizable(True, True)
        self.bind_all("<MouseWheel>", self._global_mouse_wheel, add="+")

    def _global_mouse_wheel(self, event) -> None:
        """Global mouse wheel handler to fix Tkinter scrolling on Windows."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        if not widget or widget.winfo_toplevel() != self:
            return
        
        # Try direct scroll if it's already a scrollable widget
        if hasattr(widget, "yview_scroll") and widget != getattr(self, "_chat_canvas", None):
            try:
                widget.yview_scroll(-1 * (event.delta // 120), "units")
                return
            except Exception:
                pass
                
        # Fallback: route to the main chat canvas
        if getattr(self, "_chat_canvas", None):
            self._chat_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    # ── System Prompt ─────────────────────────────────────────

    def _build_system_prompt(self) -> None:
        """Build the system prompt using the active agent's prompt function."""
        from src.ai.agents import AGENTS
        agent = AGENTS.get(self._current_agent, AGENTS["general"])
        self._system_prompt = agent.prompt_fn(self.context, self._rel_context)
        # Also tack on RESPONSE FORMAT for all agents except general
        if self._current_agent != "general":
            ctx = self.context
            extra = (
                "\n\n=== RESPONSE FORMAT ===\n"
                "Always respond with an ```actions block containing 1-5 concrete SQL actions.\n"
                f"Use full qualified table name: {ctx.get('src_schema','public')}.{ctx.get('table_name','table')}\n"
                "risk must be exactly: READ / WRITE / DANGER"
            )
            self._system_prompt += extra
        return

        ctx   = self.context  # dead code kept for reference below - overridden above
        stats = ctx.get("stats", {})
        cols  = ctx.get("columns", [])
        pks   = ctx.get("primary_keys", set())
        fks   = ctx.get("foreign_keys", {})
        uqs   = ctx.get("unique_cols", set())
        idxs  = ctx.get("indexed_cols", {})
        qs    = ctx.get("quick_stats", {})
        sel   = ctx.get("selected_columns", {c["column_name"] for c in cols})

        lines = [
            "You are an expert PostgreSQL database architect helping migrate a table.",
            "Respond in the same language the user writes in (Arabic or English).",
            "",
        ]

        # ── Inject SQL Constitution ─────────────────────────────
        constitution = _build_sql_constitution(
            schema=ctx.get('src_schema', 'public'),
            table=ctx.get('table_name', 'table'),
        )
        lines.append(constitution)

        # ── Inject previous session history ─────────────────────
        if hasattr(self, "_history") and self._history and self._history.has_history:
            hist_ctx = self._history.build_history_context()
            if hist_ctx:
                lines.append(hist_ctx)

        lines += [
            f"=== TABLE: {ctx.get('src_schema')}.{ctx.get('table_name')} ===",
            f"Target : {ctx.get('tgt_schema')}.{ctx.get('target_name')}",
            f"Rows   : ~{stats.get('rows', 0):,}   Size: {stats.get('size_pretty', '-')}",
            f"Columns: {len(cols)} total, {len(sel)} selected",
            "",
            "COLUMNS (name | type | NULL% | distinct | flags | target):",
        ]

        for col in cols:
            name  = col.get("column_name", "")
            ctype = col.get("data_type", "?")
            tgt   = ctx.get("column_renames", {}).get(name, name)
            flags = []
            if name in pks:  flags.append("PK")
            if name in fks:  flags.append(f"FK->{fks[name]['ref_table']}.{fks[name]['ref_column']}")
            if name in uqs:  flags.append("UNIQUE")
            if name in idxs: flags.append("IDX")
            f_str = f" [{', '.join(flags)}]" if flags else ""
            is_sel = "☑" if name in sel else "☐"
            cq       = qs.get(name, {})
            n_pct    = cq.get("null_pct")
            dlabel   = cq.get("distinct_label", "?")
            samples  = cq.get("sample_values", [])
            null_s   = f"{n_pct:.1f}% NULL" if n_pct is not None else "NULL%=?"
            # ── Privacy: only send sample values for non-cloud models ──
            is_cloud = "cloud" in self._model_var.get().lower()
            if is_cloud:
                samp_str = "(hidden - cloud model active, samples not sent)"
            else:
                samp_str = " | ".join(f"'{s['value']}'({s['pct']}%)" for s in samples[:3])
                if not samp_str:
                    samp_str = "(run ANALYZE for samples)"
            lines.append(
                f"  {is_sel} {name:<26} {ctype:<18} {null_s:<14} {dlabel:<18} -> {tgt}{f_str}"
            )
            lines.append(f"        Samples: {samp_str}")

        high_null = [
            (c.get("column_name", ""),
             qs.get(c.get("column_name", ""), {}).get("null_pct", 0))
            for c in cols
            if qs.get(c.get("column_name", ""), {}).get("null_pct", 0) >= 10
        ]
        if high_null:
            lines += ["", "HIGH-NULL (>=10%):"]
            for cn, pct in sorted(high_null, key=lambda x: -x[1]):
                lines.append(f"  {cn}: {pct:.1f}%")

        lines += [
            "",
            "=== INITIAL RESPONSE FORMAT (MANDATORY) ===",
            "Your FIRST message MUST follow this exact structure:",
            "",
            "1. A 2-sentence welcome greeting in Arabic (if schema has Arabic data) or English.",
            "2. A 1-sentence table health summary (rows, nulls, top issues).",
            "3. A heading '## الإجراءات المقترحة / Suggested Actions'",
            "4. Immediately followed by a ```actions block with 3-5 actions:",
            "",
            "```actions",
            '[',
            '  {"title": "وصف قصير / Short title",',
            '   "desc": "لماذا هذا الإجراء مهم / Why this matters",',
            f'   "sql": "FULL executable SQL using real schema {ctx.get("src_schema")}.{ctx.get("table_name")}",',
            '   "verify_sql": "SELECT COUNT(*) as check FROM ... WHERE ... [proves it worked]",',
            '   "risk": "READ or WRITE or DANGER"}',
            ']',
            '```',
            "",
            "=== MULTI-STEP ACTIONS (for relationship / structural changes) ===",
            "For complex operations, use type=multistep:",
            "```actions",
            '[{"type": "multistep",',
            '  "title": "إنشاء علاقة / Create FK relationship",',
            '  "desc": "Why this relationship matters",',
            '  "steps": [',
            '    {"step":1,"desc":"Check column exists","sql":"SELECT column_name FROM information_schema.columns WHERE ...","risk":"READ"},',
            '    {"step":2,"desc":"Check no existing FK","sql":"SELECT COUNT(*) FROM information_schema.table_constraints WHERE ...","risk":"READ"},',
            '    {"step":3,"desc":"Create FK constraint","sql":"ALTER TABLE schema.t ADD CONSTRAINT fk_name FOREIGN KEY (col) REFERENCES schema.ref(id);","risk":"DANGER","verify_sql":"SELECT COUNT(*) FROM information_schema.referential_constraints WHERE constraint_name=\'fk_name\'"},',
            '    {"step":4,"desc":"Final verification","sql":"SELECT ...","risk":"READ"}',
            '  ]',
            '}]',
            '```',
            "",
            "RULES FOR THE actions BLOCK:",
            "  - sql: clean, no comments, no placeholders, directly executable",
            f"  - Always use full qualified name: {ctx.get('src_schema')}.{ctx.get('table_name')}",
            "  - verify_sql: a SELECT that returns 0 if action succeeded",
            "  - risk must be exactly one of: READ, WRITE, DANGER",
            "  - Rank by impact (highest impact first)",
            "  - Focus on real issues found from the column data above",
        ]

        # Append live relationship data if already fetched
        if self._rel_context:
            self._append_rel_lines(lines)

        self._system_prompt = "\n".join(lines)

    def _append_rel_lines(self, lines: list) -> None:
        """Inject relationship context into the system prompt lines list."""
        ctx = self.context
        schema = ctx.get("src_schema", "public")
        table  = ctx.get("table_name", "")
        lines.append("")
        lines.append(f"=== RELATIONSHIPS FOR {schema}.{table} ===")

        existing = self._rel_context.get("existing_fks", [])
        if existing:
            lines.append("Existing Foreign Keys:")
            for fk in existing:
                lines.append(
                    f"  FK {fk['constraint_name']}: {fk['fk_column']} -> "
                    f"{fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}"
                )
        else:
            lines.append("Existing Foreign Keys: (none found)")

        candidates = self._rel_context.get("candidate_fks", [])
        if candidates:
            lines.append("Candidate FK columns (no FK yet, matched by name):")
            for c in candidates:
                lines.append(
                    f"  {c['column_name']} ({c['data_type']}) -> "
                    f"possible ref: {c['potential_ref_table']}.{c['potential_ref_column']}"
                )
        else:
            lines.append("Candidate FK columns: (no automatic matches found)")

    # ── Initial Analysis (first open) ─────────────────────────

    def _trigger_initial_analysis(self) -> None:
        """Called once on first open (no history). Fetches live column samples
        in a background thread then sends the result to the AI for analysis."""
        if not _PSYCOPG2_AVAILABLE or not self._dsn:
            return
        ctx = self.context
        if not ctx.get("columns"):
            return   # no schema loaded yet - skip

        self._sys("[wait] جاري جلب عينات البيانات لأول تحليل...")
        threading.Thread(target=self._fetch_and_send_initial_analysis,
                         daemon=True).start()

    def _fetch_and_send_initial_analysis(self) -> None:
        """
        Background thread:
        1. Queries the DB for per-column samples, NULL%, distinct count,
           min/max (for numeric / date cols).
        2. Builds a rich markdown prompt.
        3. Injects it as the first user→AI exchange so the AI starts
           with full data awareness.
        """
        ctx    = self.context
        schema = ctx.get("src_schema", "public")
        table  = ctx.get("table_name", "")
        cols   = ctx.get("columns", [])
        stats  = ctx.get("stats", {})
        pks    = ctx.get("primary_keys", set())
        fks    = ctx.get("foreign_keys", {})
        qs     = ctx.get("quick_stats", {})

        if not table or not cols:
            return

        # Resolve actual workspace table
        schema_n, orig_n, copy_n = self._get_safe_copy_name()
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            col_data: list[dict] = []

            with conn.cursor() as cur:
                # Check if workspace exists
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_name=%s",
                    (schema_n, copy_n)
                )
                active = copy_n if cur.fetchone() else orig_n

                for col in cols[:40]:   # cap at 40 cols for performance
                    cname = col.get("column_name", "")
                    ctype = col.get("data_type", "text")
                    if not cname:
                        continue

                    entry = {
                        "name":  cname,
                        "type":  ctype,
                        "flags": [],
                    }

                    # Flags
                    if cname in pks:   entry["flags"].append("PK")
                    if cname in fks:   entry["flags"].append(f"FK")
                    cq = qs.get(cname, {})
                    n_pct = cq.get("null_pct")
                    entry["null_pct"]     = n_pct
                    entry["distinct_lbl"] = cq.get("distinct_label", "?")

                    # Live samples (3 distinct non-null values)
                    try:
                        quoted_col    = f'"{cname}"'
                        quoted_schema = f'"{schema_n}"'
                        quoted_table  = f'"{active}"'
                        cur.execute(
                            f"SELECT DISTINCT {quoted_col}::text AS v "
                            f"FROM {quoted_schema}.{quoted_table} "
                            f"WHERE {quoted_col} IS NOT NULL "
                            f"LIMIT 5"
                        )
                        entry["samples"] = [r["v"] for r in cur.fetchall()]
                    except Exception:
                        entry["samples"] = cq.get("sample_values", [])
                        if isinstance(entry["samples"], list) and entry["samples"]:
                            entry["samples"] = [
                                s["value"] if isinstance(s, dict) else str(s)
                                for s in entry["samples"][:5]
                            ]

                    # Min/max for numeric and date columns
                    is_numeric = any(t in ctype.lower() for t in
                                     ("int", "float", "numeric", "double", "real", "serial"))
                    is_date    = any(t in ctype.lower() for t in
                                     ("date", "time", "timestamp", "interval"))
                    if is_numeric or is_date:
                        try:
                            cur.execute(
                                f"SELECT MIN({quoted_col}::text) AS mn, "
                                f"MAX({quoted_col}::text) AS mx "
                                f"FROM {quoted_schema}.{quoted_table} "
                                f"WHERE {quoted_col} IS NOT NULL"
                            )
                            row = cur.fetchone()
                            if row:
                                entry["min"] = row["mn"]
                                entry["max"] = row["mx"]
                        except Exception:
                            pass

                    col_data.append(entry)

            conn.rollback()
            conn.close()

        except Exception as e:
            self.after(0, lambda err=str(e):
                       self._sys(f"[x] فشل جلب العينات: {err}"))
            return

        # ── Build the analysis prompt ────────────────────────────────────
        lines = [
            f"## تحليل أولي لجدول: `{schema}.{table}`",
            f"**الصفوف:** ~{stats.get('rows', 0):,}  |  **الحجم:** {stats.get('size_pretty', '-')}  |  "
            f"**الأعمدة:** {len(cols)}",
            "",
            "### عينات بيانات حقيقية من كل عمود:",
            "```",
        ]

        # Table header
        lines.append(f"{'العمود':<28} {'النوع':<18} {'NULL%':<8} {'Distinct':<12} {'عينات'}")
        lines.append("-" * 100)

        high_null   = []
        no_samples  = []

        for c in col_data:
            null_str  = f"{c['null_pct']:.1f}%" if c.get("null_pct") is not None else "?"
            flags_str = f"[{','.join(c['flags'])}]" if c["flags"] else ""
            samps     = c.get("samples", [])
            samp_str  = " | ".join(f"'{str(s)[:25]}'" for s in samps[:3]) if samps else "(no data)"
            mn = c.get("min"); mx = c.get("max")
            if mn is not None:
                samp_str += f"  [min:{mn} max:{mx}]"
            lines.append(
                f"{(c['name']+' '+flags_str):<28} {c['type']:<18} {null_str:<8} "
                f"{c['distinct_lbl']:<12} {samp_str}"
            )
            if c.get("null_pct") and c["null_pct"] >= 20:
                high_null.append((c["name"], c["null_pct"]))
            if not samps:
                no_samples.append(c["name"])

        lines.append("```")
        lines.append("")

        if high_null:
            lines.append("### [!] أعمدة بنسب NULL عالية:")
            for cn, pct in sorted(high_null, key=lambda x: -x[1]):
                lines.append(f"- `{cn}`: **{pct:.1f}%** NULL")
            lines.append("")

        if no_samples:
            lines.append(f"### [!] أعمدة بدون بيانات ({len(no_samples)}):")
            lines.append(", ".join(f"`{c}`" for c in no_samples[:10]))
            lines.append("")

        lines += [
            "---",
            "بناءً على هذه البيانات الحقيقية من الجدول:",
            "1. حدد أبرز مشاكل جودة البيانات",
            "2. اقترح أهم 3-5 إجراءات هجرة مرتّبة حسب الأولوية",
            "3. استخدم عينات البيانات الفعلية لتبرير كل اقتراح",
        ]

        prompt = "\n".join(lines)

        # Inject as a system message then trigger AI response
        self.after(0, lambda p=prompt: (
            self._sys(f"[ok] تم جلب عينات {len(col_data)} عمود. جاري التحليل..."),
            self._inject_and_send(p)
        ))

    def _fetch_relationship_context(self) -> None:

        """Query information_schema for existing and candidate FK relationships.
        Runs in a background thread; updates _rel_context and refreshes the system prompt.
        """
        if not _PSYCOPG2_AVAILABLE or not self._dsn:
            return
        ctx    = self.context
        schema = ctx.get("src_schema", "public")
        table  = ctx.get("table_name", "")
        if not table:
            return

        active_table = resolve_active_table(self._dsn, table, schema)

        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=8,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = True
            with conn.cursor() as cur:

                # ── 1. Existing Foreign Keys ──────────────────────
                cur.execute("""
                    SELECT
                        tc.constraint_name,
                        kcu.column_name                      AS fk_column,
                        ccu.table_schema                     AS ref_schema,
                        ccu.table_name                       AS ref_table,
                        ccu.column_name                      AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema   = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                         ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema    = %s
                      AND tc.table_name      = %s
                    ORDER BY kcu.column_name;
                """, (schema, active_table))
                existing_fks = [dict(r) for r in cur.fetchall()]

                # ── 2. Candidate FK columns (name-pattern matching) ─
                cur.execute("""
                    SELECT DISTINCT
                        c.column_name,
                        c.data_type,
                        t.table_name  AS potential_ref_table,
                        pk.column_name AS potential_ref_column
                    FROM information_schema.columns c
                    JOIN information_schema.tables t
                         ON t.table_schema = c.table_schema
                         AND t.table_type  = 'BASE TABLE'
                         AND t.table_name <> c.table_name
                         AND (
                             c.column_name = t.table_name || '_id'
                          OR c.column_name = t.table_name || 'id'
                          OR c.column_name SIMILAR TO '%%' || t.table_name || '_%%'
                         )
                    JOIN information_schema.columns pk
                         ON pk.table_schema = t.table_schema
                         AND pk.table_name   = t.table_name
                         AND pk.column_name IN ('id', 'pk', 'uuid', 'key',
                                                 t.table_name || '_id')
                    WHERE c.table_schema = %s
                      AND c.table_name   = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM information_schema.table_constraints xtc
                          JOIN information_schema.key_column_usage xkcu
                               ON xtc.constraint_name = xkcu.constraint_name
                          WHERE xtc.constraint_type = 'FOREIGN KEY'
                            AND xtc.table_schema     = c.table_schema
                            AND xtc.table_name       = c.table_name
                            AND xkcu.column_name     = c.column_name
                      )
                    ORDER BY c.column_name
                    LIMIT 20;
                """, (schema, table))
                candidate_fks = [dict(r) for r in cur.fetchall()]

            conn.close()
            self._rel_context = {
                "existing_fks":  existing_fks,
                "candidate_fks": candidate_fks,
            }
            # Rebuild system prompt with relationship data (affects next message)
            self.after(0, self._build_system_prompt)
            # Update sidebar relationship section
            self.after(0, self._update_rel_sidebar)

        except Exception as e:
            self._rel_context = {"error": str(e)}

    # ── UI Construction ───────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_title_bar()
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_agents_bar()
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_settings_bar()
        ttk.Separator(self, orient="horizontal").pack(fill="x")

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)

        # ── Chat column (left, expandable) ─────────────────────
        chat_col = tk.Frame(main, bg=BG)
        chat_col.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        self._build_chat_area(chat_col)

        # ── Context panel (right, fixed width) ─────────────────
        ctx_col = tk.Frame(main, bg=PANEL_BG, width=268,
                           highlightbackground=BORDER, highlightthickness=1)
        ctx_col.pack(side="left", fill="y", padx=(0, 8), pady=8)
        ctx_col.pack_propagate(False)
        self._build_context_panel(ctx_col)

        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_input_bar()

    # ── Agents Bar ───────────────────────────────

    def _build_agents_bar(self) -> None:
        """Horizontal agent-selector bar just below the title."""
        from src.ai.agents import AGENTS, AGENT_ORDER

        bar = tk.Frame(self, bg="#0d1117", height=50)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="Agent:",
                 bg="#0d1117", fg="#8b949e",
                 font=("Segoe UI", 9)).pack(side="left", padx=(12, 6), pady=0)

        self._agent_btns = {}
        for agent_id in AGENT_ORDER:
            agent = AGENTS[agent_id]
            btn_frame = tk.Frame(bar, bg="#21262d", cursor="hand2")
            btn_frame.pack(side="left", padx=3, pady=8)

            lbl = tk.Label(
                btn_frame,
                text=f"{agent.icon}  {agent.name_ar}",
                bg="#21262d", fg="#8b949e",
                font=("Segoe UI", 9),
                padx=10, pady=4,
            )
            lbl.pack()

            def _on_enter(e, f=btn_frame, c=agent.color):
                f.config(bg=c)
                for w in f.winfo_children():
                    w.config(bg=c, fg="#0d1117")

            def _on_leave(e, f=btn_frame, aid=agent_id):
                is_active = (aid == self._current_agent)
                bg = AGENTS[aid].color if is_active else "#21262d"
                fg = "#0d1117" if is_active else "#8b949e"
                f.config(bg=bg)
                for w in f.winfo_children():
                    w.config(bg=bg, fg=fg)

            def _on_click(e, aid=agent_id):
                self._switch_agent(aid)

            lbl.bind("<Enter>",    _on_enter)
            lbl.bind("<Leave>",    _on_leave)
            lbl.bind("<Button-1>", _on_click)
            btn_frame.bind("<Enter>",    _on_enter)
            btn_frame.bind("<Leave>",    _on_leave)
            btn_frame.bind("<Button-1>", _on_click)

            self._agent_btns[agent_id] = btn_frame

        self._refresh_agent_buttons()

    def _refresh_agent_buttons(self) -> None:
        """Update all agent buttons to reflect the active state."""
        from src.ai.agents import AGENTS
        for aid, frame in self._agent_btns.items():
            is_active = (aid == self._current_agent)
            bg = AGENTS[aid].color if is_active else "#21262d"
            fg = "#0d1117" if is_active else "#8b949e"
            frame.config(bg=bg)
            for w in frame.winfo_children():
                w.config(bg=bg, fg=fg)

    def _switch_agent(self, agent_id: str) -> None:
        """Switch to a new agent: rebuild system prompt and show greeting."""
        from src.ai.agents import AGENTS
        if agent_id == self._current_agent:
            return

        agent = AGENTS.get(agent_id)
        if not agent:
            return

        self._current_agent = agent_id
        self._build_system_prompt()
        self._refresh_agent_buttons()

        # Visual separator
        sep_row = tk.Frame(self._msg_container, bg="#0d1117", height=28)
        sep_row.pack(fill="x", padx=0, pady=(8, 2))
        tk.Label(
            sep_row,
            text=f"────  {agent.icon} {agent.name_ar}  ────",
            bg="#0d1117", fg=agent.color,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="top", pady=4)

        # Greeting after short delay
        self.after(120, lambda a=agent: self._agent_greet(a))

    def _agent_greet(self, agent) -> None:
        """Insert the agent greeting into the chat as an AI bubble."""
        greeting = agent.greeting_fn(self.context)
        self._begin_ai_bubble()
        self._finalize_ai_bubble(greeting)

    # ── Title Bar ─────────────────────────────────────────────

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=ACCENT, width=4).pack(side="left", fill="y")

        lf = tk.Frame(bar, bg=HEADER_BG, padx=12)
        lf.pack(side="left", fill="y", pady=6)
        ctx = self.context
        tk.Label(lf, text="💬  Ollama Chat - Schema Advisor",
                 bg=HEADER_BG, fg=TEXT, font=FONT_TITLE).pack(anchor="w")
        tk.Label(lf,
                 text=f"{ctx.get('src_schema')}.{ctx.get('table_name')}  ->  "
                      f"{ctx.get('tgt_schema')}.{ctx.get('target_name')}",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_MONO).pack(anchor="w")

        self._conn_lbl = tk.Label(bar, text="[wait] Connecting...",
                                   bg=HEADER_BG, fg=WARNING, font=FONT_MONO, padx=14)
        self._conn_lbl.pack(side="right", fill="y")

    # ── Settings Bar ──────────────────────────────────────────

    def _build_settings_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=HEADER_BG)
        left.pack(side="left", fill="y", padx=8)

        tk.Label(left, text="Model:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left", fill="y", padx=(0, 4))
        self._model_combo = ttk.Combobox(
            left, textvariable=self._model_var,
            values=PREDEFINED_MODELS, width=26,
            font=FONT_SMALL, state="normal",
        )
        self._model_combo.pack(side="left", pady=8)
        self._model_combo.bind("<<ComboboxSelected>>",
                               lambda _: self._auto_check_model())

        self._check_btn = _Btn(
            left, "[search] Check", cmd=self._check_model,
            bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
            padx=8, pady=3, font=FONT_SMALL,
        )
        self._check_btn.pack(side="left", padx=(6, 0))

        self._model_status = tk.Label(
            left, text="", bg=HEADER_BG, fg=TEXT_DIM, font=FONT_MONO, padx=6)
        self._model_status.pack(side="left", fill="y")

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=6, padx=10)

        tk.Label(bar, text="URL:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left", fill="y")
        tk.Entry(bar, textvariable=self._url_var,
                 bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=FONT_SMALL, width=24, bd=1,
                 ).pack(side="left", padx=(4, 6), pady=8)
        self._url_var.trace_add("write",
            lambda *_: setattr(self, "_base_url", self._url_var.get().strip()))

        _Btn(bar, "[reload] Refresh", cmd=self._refresh_models,
             bg=HEADER_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=3, font=FONT_SMALL).pack(side="left")

        _Btn(bar, "🗑 Clear", cmd=self._clear_chat,
             bg=HEADER_BG, hov=BTN_HOVER, fg=DANGER,
             padx=8, pady=3, font=FONT_SMALL).pack(side="right", padx=8)

        self._undo_btn = _Btn(bar, "[back] تراجع (Undo)", cmd=self._undo_last_workspace_action,
             bg=HEADER_BG, hov="#1f6feb", fg="#58a6ff",
             padx=4, pady=3, font=FONT_SMALL)
        self._undo_btn.pack(side="right", padx=4)
        self._undo_btn.set_disabled()

        _Btn(bar, "[*] New Chat", cmd=self._new_chat,
             bg=HEADER_BG, hov=BTN_HOVER, fg=ACCENT2,
             padx=8, pady=3, font=FONT_SMALL).pack(side="right", padx=(0, 4))
             
        _Btn(bar, "🚀 تطبيق على الأصلي", cmd=self._commit_workspace_dialog,
             bg="#238636", hov="#2ea043", fg="#ffffff",
             padx=12, pady=3, font=("Segoe UI", 9, "bold")).pack(side="right", padx=(0, 16))

    # ── Chat Area (bubble-based, scrollable canvas) ───────────

    def _build_chat_area(self, parent: tk.Frame) -> None:
        # Scrollable container
        wrap = tk.Frame(parent, bg=PANEL_BG,
                        highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(wrap, orient="vertical")
        vsb.pack(side="right", fill="y")

        self._chat_canvas = tk.Canvas(
            wrap, bg=PANEL_BG,
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        self._chat_canvas.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._chat_canvas.yview)

        # Inner frame (holds all message rows)
        self._msg_container = tk.Frame(self._chat_canvas, bg=PANEL_BG)
        self._canvas_win = self._chat_canvas.create_window(
            (0, 0), window=self._msg_container, anchor="nw"
        )

        # Resize handlers
        self._msg_container.bind(
            "<Configure>",
            lambda _: self._chat_canvas.configure(
                scrollregion=self._chat_canvas.bbox("all")
            )
        )
        self._chat_canvas.bind("<Configure>", self._on_canvas_resize)

        # Mouse wheel
        for w in (self._chat_canvas, self._msg_container):
            w.bind("<MouseWheel>",
                   lambda e: self._chat_canvas.yview_scroll(
                       -1 * (e.delta // 120), "units"))

    def _on_canvas_resize(self, event) -> None:
        w = event.width
        self._chat_canvas.itemconfig(self._canvas_win, width=w)
        avail = max(280, w - 100)
        user_w = int(avail * 0.68)
        ai_w   = int(avail * 0.80)
        for lbl, role in self._dynamic_labels:
            try:
                lbl.config(wraplength=user_w if role == "user" else ai_w)
            except tk.TclError:
                pass

    def _scroll_bottom(self) -> None:
        """Scroll the chat canvas to the very bottom.
        Uses two-stage delay: first update layout, then scroll.
        """
        def _do_scroll():
            try:
                self._chat_canvas.update_idletasks()
                self._chat_canvas.configure(
                    scrollregion=self._chat_canvas.bbox("all"))
                self._chat_canvas.yview_moveto(1.0)
            except tk.TclError:
                pass
        # First pass: quickly after widget creation
        self._chat_canvas.after(50,  _do_scroll)
        # Second pass: after Tkinter finishes redrawing (belt-and-suspenders)
        self._chat_canvas.after(250, _do_scroll)

    def _prune_old_bubbles(self, keep: int = 60) -> None:
        """Remove oldest message rows when the chat grows too large.
        Keeps the most recent `keep` rows to prevent canvas widget overload
        which causes the scroll region to freeze.
        """
        children = list(self._msg_container.pack_slaves())
        excess   = len(children) - keep
        if excess <= 0:
            return
        for widget in children[:excess]:
            try:
                widget.destroy()
            except tk.TclError:
                pass

    # ── Context Panel ─────────────────────────────────────────

    def _build_context_panel(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=HEADER_BG, height=34)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  TABLE CONTEXT",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_BOLD).pack(side="left", fill="y")

        body = tk.Frame(parent, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=10, pady=8)

        ctx   = self.context
        stats = ctx.get("stats", {})
        cols  = ctx.get("columns", [])
        qs    = ctx.get("quick_stats", {})
        sel   = ctx.get("selected_columns",
                        {c.get("column_name", "") for c in cols})

        def _row(lbl, val, fg=TEXT):
            fr = tk.Frame(body, bg=PANEL_BG); fr.pack(fill="x", pady=1)
            tk.Label(fr, text=lbl, bg=PANEL_BG, fg=TEXT_DIM,
                     font=FONT_SMALL, width=11, anchor="w").pack(side="left")
            tk.Label(fr, text=val, bg=PANEL_BG, fg=fg,
                     font=FONT_MONO, anchor="w").pack(side="left")

        _row("Source:", f"{ctx.get('src_schema')}.{ctx.get('table_name')}")
        _row("Target:", f"{ctx.get('tgt_schema')}.{ctx.get('target_name')}", ACCENT2)
        _row("Rows:",   f"{stats.get('rows', 0):,}")
        _row("Size:",   stats.get("size_pretty", "-"))
        col_count = len(cols); sel_count = len(sel)
        _row("Columns:", f"{col_count} total",
             ACCENT2 if col_count else WARNING)
        _row("Selected:", f"{sel_count} cols",
             ACCENT2 if sel_count else (WARNING if col_count else DANGER))

        if col_count == 0:
            tk.Label(body,
                     text="[!] Schema data not loaded yet.",
                     bg=PANEL_BG, fg=WARNING, font=FONT_SMALL,
                     justify="left", wraplength=220).pack(anchor="w", pady=(6, 0))

        high_null = [
            (c.get("column_name", ""),
             qs.get(c.get("column_name", ""), {}).get("null_pct", 0))
            for c in cols
            if qs.get(c.get("column_name", ""), {}).get("null_pct", 0) >= 10
        ]
        if high_null:
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=6)
            tk.Label(body, text="[!] High-Null Cols:",
                     bg=PANEL_BG, fg=WARNING, font=FONT_BOLD).pack(anchor="w")
            for cn, pct in sorted(high_null, key=lambda x: -x[1])[:7]:
                fr = tk.Frame(body, bg=PANEL_BG); fr.pack(fill="x", pady=1)
                tk.Label(fr, text=f"  {cn[:20]}", bg=PANEL_BG,
                         fg=DANGER, font=FONT_SMALL).pack(side="left")
                tk.Label(fr, text=f"{pct:.0f}%", bg=PANEL_BG,
                         fg=WARNING, font=FONT_MONO).pack(side="right")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=8)

        _Btn(body, "[ok] Apply Last Response", cmd=self._apply_last_response,
             bg="#0f2d1a", hov="#1a4a2e", fg=ACCENT2,
             font=FONT_BOLD, padx=8, pady=8).pack(fill="x", pady=(0, 4))

        self._sql_btn = _Btn(body, "⚡ Execute Pending SQL",
                              cmd=self._show_sql_approval,
                              bg="#1a1210", hov="#2e1a10", fg=WARNING,
                              font=FONT_BOLD, padx=8, pady=8)
        self._sql_btn.pack(fill="x", pady=(0, 4))
        self._sql_btn._lbl.config(state="disabled", cursor="arrow", fg=TEXT_DIM)

        _Btn(body, "[clipboard] Copy Last Message", cmd=self._copy_last,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=6, font=FONT_SMALL).pack(fill="x", pady=(0, 4))

        _Btn(body, "[send] Export Chat", cmd=self._export_chat,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=6, font=FONT_SMALL).pack(fill="x", pady=(0, 4))

        _Btn(body, "🛠 SQL Editor", cmd=self._open_sql_editor,
             bg="#0d1929", hov="#1a2e45", fg="#58a6ff",
             font=FONT_BOLD, padx=8, pady=8).pack(fill="x", pady=(0, 4))

        # ── Relationship Analysis button ──────────────────────
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(6, 4))
        tk.Label(body, text="  🔗 RELATIONSHIPS",
                 bg=PANEL_BG, fg=ACCENT, font=FONT_BOLD).pack(anchor="w")

        _Btn(body, "[search] تحليل العلاقات / Analyze", cmd=self._analyze_relationships,
             bg="#0d1f2d", hov="#1a2e40", fg=ACCENT,
             font=FONT_BOLD, padx=8, pady=8).pack(fill="x", pady=(4, 2))

        # Dynamic section updated by _update_rel_sidebar()
        self._rel_sidebar_frame = tk.Frame(body, bg=PANEL_BG)
        self._rel_sidebar_frame.pack(fill="x", pady=(2, 0))

        self._ctx_status = tk.Label(
            body, text="", bg=PANEL_BG, fg=TEXT_DIM,
            font=FONT_SMALL, wraplength=230, justify="left")
        self._ctx_status.pack(fill="x", pady=(8, 0))

    # ── Input Bar ─────────────────────────────────────────────

    def _build_input_bar(self) -> None:
        outer = tk.Frame(self, bg=HEADER_BG, height=86)
        outer.pack(fill="x", side="bottom")
        outer.pack_propagate(False)

        tk.Label(outer,
                 text="Enter <- إرسال  |  Shift+Enter <- سطر جديد / new line",
                 bg=HEADER_BG, fg=TEXT_DIM, font=("Segoe UI", 8), padx=14,
                 ).pack(anchor="w")

        inner = tk.Frame(outer, bg=HEADER_BG)
        inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._inp_frame = tk.Frame(inner, bg=ENTRY_BG,
                                    highlightbackground=BORDER,
                                    highlightthickness=1)
        self._inp_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self._input = tk.Text(
            self._inp_frame,
            bg=ENTRY_BG, fg=TEXT_DIM, insertbackground=TEXT,
            relief="flat", font=FONT_CHAT, height=3, wrap="word",
            padx=12, pady=10,
        )
        self._input.insert("1.0", INPUT_PLACEHOLDER)
        self._input.pack(fill="both", expand=True)
        self._input.bind("<FocusIn>",      self._on_input_focus_in)
        self._input.bind("<FocusOut>",     self._on_input_focus_out)
        self._input.bind("<Return>",       self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)
        self._input.bind("<KeyRelease>",   self._update_input_dir)
        self._placeholder_active = True

        bc = tk.Frame(inner, bg=HEADER_BG)
        bc.pack(side="right", fill="y")

        self._send_btn = _Btn(bc, "[send]  إرسال / Send",
                               cmd=self._send,
                               bg="#0f2d1a", hov="#1a4a2e", fg=ACCENT2,
                               font=FONT_BOLD, padx=12, pady=10)
        self._send_btn.pack(fill="x", pady=(0, 4))

        _Btn(bc, "⏹ Stop",
             cmd=self._stop,
             bg="#2a1010", hov="#3d1111", fg=DANGER,
             padx=12, pady=6, font=FONT_SMALL).pack(fill="x")

    # ── Input helpers ─────────────────────────────────────────

    def _on_input_focus_in(self, _) -> None:
        if self._placeholder_active:
            self._input.delete("1.0", "end")
            self._input.config(fg=TEXT)
            self._placeholder_active = False
        self._inp_frame.config(highlightbackground=ACCENT)

    def _on_input_focus_out(self, _) -> None:
        self._inp_frame.config(highlightbackground=BORDER)
        if not self._input.get("1.0", "end-1c").strip():
            self._input.insert("1.0", INPUT_PLACEHOLDER)
            self._input.config(fg=TEXT_DIM)
            self._placeholder_active = True

    def _on_enter(self, event) -> str:
        if not (event.state & 1):
            self._send()
            return "break"
        return None

    def _update_input_dir(self, _=None) -> None:
        if self._placeholder_active:
            return
        text = self._input.get("1.0", "end")
        rtl  = _is_rtl(text)
        self._input.tag_configure("dir", justify="right" if rtl else "left")
        self._input.tag_add("dir", "1.0", "end")

    # ── Connection & Model ────────────────────────────────────

    def _check_connection(self) -> None:
        threading.Thread(target=self._bg_connect, daemon=True).start()

    def _bg_connect(self) -> None:
        try:
            req = request.Request(f"{self._base_url}/api/tags", method="GET")
            with request.urlopen(req, timeout=5) as resp:
                data   = json.loads(resp.read())
            fetched = [m["name"] for m in data.get("models", [])]
            self.after(0, lambda: self._on_connected(fetched))
        except Exception as e:
            self.after(0, lambda: self._on_connect_failed(str(e)))

    def _on_connected(self, fetched: list[str]) -> None:
        n = len(fetched)
        self._conn_lbl.config(
            text=f"[green] Ollama  ({n} local + {len(PREDEFINED_MODELS)} catalogue)",
            fg=ACCENT2)
        merged = list(dict.fromkeys(fetched + PREDEFINED_MODELS))
        self._model_combo["values"] = merged
        if self._model_var.get() not in merged:
            self._model_var.set(fetched[0] if fetched else OLLAMA_MODEL_DEFAULT)
        # Cloud privacy warning
        model = self._model_var.get()
        if "cloud" in model.lower():
            self._sys(
                "[!]  Privacy Warning: Cloud model active\n"
                f"  Model: {model}\n"
                "  ☁  Schema info & query results will be sent to external servers.\n"
                "  Do NOT use with sensitive data (PII/passwords/customer data).\n"
                "  معلومات قاعدة البيانات ستُرسل لخوادم خارجية. لا تستخدم مع بيانات حساسة."
            )
        self._start_conversation()

    def _on_connect_failed(self, err: str) -> None:
        self._conn_lbl.config(text="[red] Ollama not reachable", fg=DANGER)
        self._model_combo["values"] = PREDEFINED_MODELS
        self._sys(f"[!] Cannot reach Ollama:\n  {err}\n\nRun: ollama serve  ->  then click [reload] Refresh")

    def _refresh_models(self) -> None:
        self._conn_lbl.config(text="[wait] Refreshing...", fg=WARNING)
        self._model_status.config(text="")
        threading.Thread(target=self._bg_connect, daemon=True).start()

    def _auto_check_model(self) -> None:
        model = self._model_var.get().strip()
        self._model_status.config(text="[wait]", fg=WARNING)
        threading.Thread(target=self._bg_check_model, args=(model,), daemon=True).start()

    def _check_model(self) -> None:
        model = self._model_var.get().strip()
        if not model:
            self._model_status.config(text="[!] empty", fg=WARNING)
            return
        self._model_status.config(text="[wait] checking...", fg=WARNING)
        self._check_btn._lbl.config(text="[wait]")
        threading.Thread(target=self._bg_check_model, args=(model,), daemon=True).start()

    def _bg_check_model(self, model: str) -> None:
        try:
            payload = json.dumps({
                "model":    model,
                "messages": [{"role": "user", "content": "Reply with: OK"}],
                "stream":   False,
                "options":  {"num_predict": 3},
            }).encode()
            req = request.Request(
                f"{self._base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=30) as resp:
                data  = json.loads(resp.read())
                reply = data.get("message", {}).get("content", "").strip()[:20]
            icon = "☁" if "cloud" in model else "[ok]"
            self.after(0, lambda: self._on_model_ok(model, icon, reply))
        except Exception as e:
            self.after(0, lambda: self._on_model_fail(model, str(e)[:50]))

    def _on_model_ok(self, model: str, icon: str, reply: str) -> None:
        self._model_status.config(text=f"{icon} ready", fg=ACCENT2)
        self._check_btn._lbl.config(text="[search] Check")
        tag = "☁ cloud" if "cloud" in model else "[ok] local"
        self._sys(f"Model '{model}' is ready [{tag}]. Reply: {reply}")

    def _on_model_fail(self, model: str, err: str) -> None:
        self._model_status.config(text=f"[x] {err[:30]}", fg=DANGER)
        self._check_btn._lbl.config(text="[search] Check")
        self._sys(f"[x] Model '{model}' failed: {err}\nRun: ollama pull {model}")

    def _warn_no_data(self) -> None:
        self._sys(
            "[!] بيانات الجدول لا تزال تُحمَّل / Table data is still loading.\n"
            "ستبدأ المحادثة تلقائياً بعد الاكتمال. / Conversation starts automatically after load."
        )
        self._animate_loading(0)

    def _animate_loading(self, tick: int) -> None:
        if self._data_ready:
            return
        frames = ["  ⠋", "  ⠙", "  ⠹", "  ⠸", "  ⠼", "  ⠴", "  ⠦", "  ⠧"]
        frame  = frames[tick % len(frames)]
        try:
            self._conn_lbl.config(text=f"{frame} جاري التحميل...", fg=WARNING)
        except tk.TclError:
            return
        self.after(150, lambda: self._animate_loading(tick + 1))

    # ── Conversation ──────────────────────────────────────────

    def _start_conversation(self) -> None:
        if not self._data_ready:
            self._sys("[wait] انتظار بيانات الجدول... / Waiting for table data...")
            return
        self._messages = [{"role": "system", "content": self._system_prompt}]
        # Seed with a structured-analysis request so model follows the action format
        self._messages.append({
            "role": "user",
            "content": (
                "افحص الجدول وابدأ بالتحليل. اتبع تنسيق الرد الأولي المطلوب بالضبط "
                "(ترحيب + ملخص + ```actions块 مع الإجراءات المقترحة)."
            ),
        })
        self._sys("✓ متصل / Connected - جاري تحليل الجدول...")
        threading.Thread(target=self._stream, daemon=True).start()

    def _inject_and_send(self, prompt: str) -> None:
        """
        Programmatically inject a prompt into the chat and send it.
        Used internally by buttons, analysis features, etc.
        Replaces the old _send_message() that didn't exist.
        """
        if self._streaming:
            return
        self._placeholder_active = False
        self._input.config(fg=TEXT)
        self._input.delete("1.0", "end")
        self._input.insert("1.0", prompt)
        self._update_input_dir()
        self._send()

    def _send(self) -> None:
        if self._streaming or self._placeholder_active:
            return
        text = self._input.get("1.0", "end-1c").strip()
        if not text:
            return
        self._input.delete("1.0", "end")
        self._on_input_focus_out(None)
        self._messages.append({"role": "user", "content": text})
        # Persist user message
        if self._history:
            self._history.add_message("user", text)
        self._add_user_bubble(text)
        threading.Thread(target=self._stream, daemon=True).start()

    def _stop(self) -> None:
        self._streaming = False

    # ── Streaming ─────────────────────────────────────────────

    def _stream(self) -> None:
        self._streaming = True
        self.after(0, lambda: self._send_btn.set_text("[wait] يفكر..."))
        self.after(0, lambda: self._send_btn.set_fg(WARNING))
        full = ""

        try:
            payload = json.dumps({
                "model":    self._model_var.get(),
                "messages": self._messages,
                "stream":   True,
            }).encode()
            req = request.Request(
                f"{self._base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=120) as resp:
                self.after(0, self._begin_ai_bubble)
                for raw in resp:
                    if not self._streaming:
                        break
                    try:
                        chunk = json.loads(raw.decode())
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full += token
                        self.after(0, lambda t=token: self._append_stream_token(t))
                    if chunk.get("done"):
                        break

        except URLError as e:
            reason = getattr(e, "reason", None) or str(e)
            self.after(0, lambda r=reason: self._sys(f"[x] Ollama error: {r}"))
        except Exception as e:
            self.after(0, lambda: self._sys(f"[x] Error: {e}"))
        finally:
            if full:
                self._messages.append({"role": "assistant", "content": full})
                self._last_ai_msg = full
                self.after(0, lambda: self._finalize_ai_bubble(full))
                self.after(0, lambda: self._post_process_ai_message(full))
            self._streaming = False
            self.after(0, lambda: self._send_btn.set_text("[send]  إرسال / Send"))
            self.after(0, lambda: self._send_btn.set_fg(ACCENT2))

    def _post_process_ai_message(self, text: str) -> None:
        sql_blocks = re.findall(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if sql_blocks:
            raw_sql = sql_blocks[-1].strip()
            sql     = _clean_sql(raw_sql)          # <- strip comments / placeholders
            risk    = _classify_sql(sql)
            self._pending_sql  = sql
            self._pending_risk = risk
            color = RISK_COLOR[risk]
            self._sql_btn._lbl.config(
                state="normal", cursor="hand2",
                text=f"⚡ Execute SQL  [{RISK_LABEL[risk]}]",
                fg=color,
            )
            self._sql_btn._cmd = self._show_sql_approval

            # Notify user if cleaning removed something
            cleaned = sql != raw_sql
            note = " (تم تنظيفه / cleaned)" if cleaned else ""
            self._sys(
                f"SQL detected{note}  ->  Risk: {RISK_LABEL[risk]}\n"
                "Click '⚡ Execute SQL' in the side panel to review."
            )
        else:
            self._sql_btn._lbl.config(
                state="disabled", cursor="arrow",
                text="⚡ Execute Pending SQL", fg=TEXT_DIM)
            self._pending_sql = ""

    # ── Chat Display - Bubble System ──────────────────────────

    def _sys(self, msg: str) -> None:
        """Small centered system / info message - not a bubble."""
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=20, pady=3)
        lbl = tk.Label(row, text=msg, bg=PANEL_BG, fg=SYS_TEXT,
                       font=("Segoe UI", 8), justify="center",
                       wraplength=600)
        lbl.pack()
        self._scroll_bottom()

    def _add_user_bubble(self, text: str) -> None:
        """Right-aligned dark-blue bubble for user messages."""
        is_rtl = _is_rtl(text)
        ts     = _now_str()

        self._prune_old_bubbles()
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=12, pady=6)

        # Push bubble to the right
        tk.Frame(row, bg=PANEL_BG).pack(side="left", fill="x", expand=True)

        # Bubble column
        col = tk.Frame(row, bg=PANEL_BG)
        col.pack(side="left")

        # Timestamp
        tk.Label(col, text=ts, bg=PANEL_BG, fg=SYS_TEXT,
                 font=("Segoe UI", 8)).pack(anchor="e", padx=4)

        # Bubble frame
        bubble = tk.Frame(col, bg=USER_BUBBLE,
                          padx=14, pady=10)
        bubble.pack(anchor="e")

        lbl = tk.Label(bubble, text=text, bg=USER_BUBBLE,
                       fg="#d0e8ff",
                       font=FONT_CHAT,
                       justify="right" if is_rtl else "left",
                       wraplength=480,
                       anchor="e" if is_rtl else "w")
        lbl.pack(fill="x")
        self._dynamic_labels.append((lbl, "user"))

        # Avatar
        tk.Label(row, text="👤", bg=PANEL_BG,
                 font=("Segoe UI Emoji", 15), padx=6
                 ).pack(side="left", anchor="s", pady=6)

        self._scroll_bottom()

    def _begin_ai_bubble(self) -> None:
        """Create an AI bubble with a live-updating Text widget for streaming."""
        self._prune_old_bubbles()
        ts  = _now_str()
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=12, pady=6)
        # NOTE: do NOT call row.pack_propagate(False) - it prevents
        # the row from growing after the streaming Text is replaced by Labels.

        # Avatar
        tk.Label(row, text="[AI]", bg=PANEL_BG,
                 font=("Segoe UI Emoji", 15), padx=6
                 ).pack(side="left", anchor="n", pady=6)

        # Right side column
        col = tk.Frame(row, bg=PANEL_BG)
        col.pack(side="left", fill="both", expand=True)

        # Name + timestamp
        hdr = tk.Frame(col, bg=PANEL_BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Ollama", bg=PANEL_BG, fg=ACCENT2,
                 font=FONT_BOLD).pack(side="left")
        tk.Label(hdr, text=f"  {ts}", bg=PANEL_BG, fg=SYS_TEXT,
                 font=("Segoe UI", 8)).pack(side="left")

        # Bubble
        bubble = tk.Frame(col, bg=AI_BUBBLE, padx=14, pady=10)
        bubble.pack(fill="x", anchor="w")

        # Streaming text widget - temporary until stream ends
        txt = tk.Text(
            bubble,
            bg=AI_BUBBLE, fg=TEXT,
            font=FONT_CHAT,
            relief="flat", wrap="word",
            state="disabled", cursor="arrow",
            height=2, padx=0, pady=4,
            spacing3=2,
        )
        txt.pack(fill="x", expand=True)
        txt.bind("<MouseWheel>",
                 lambda e: self._chat_canvas.yview_scroll(
                     -1 * (e.delta // 120), "units"))

        self._stream_text_widget  = txt
        self._stream_bubble_frame = bubble
        self._scroll_bottom()

    def _append_stream_token(self, token: str) -> None:
        """Append a streaming token to the live AI Text widget."""
        w = self._stream_text_widget
        if not w:
            return
        try:
            w.config(state="normal")
            w.insert("end", token)
            lines = int(w.index("end-1c").split(".")[0])
            w.config(height=max(1, lines), state="disabled")
        except tk.TclError:
            pass
        self._scroll_bottom()

    def _finalize_ai_bubble(self, full_text: str) -> None:
        """
        After streaming ends: replace the plain Text widget with a
        properly rendered bubble that highlights code blocks and action cards.
        """
        bubble = self._stream_bubble_frame
        txt    = self._stream_text_widget
        if not bubble or not txt:
            return

        # Remove streaming widget
        try:
            txt.pack_forget()
            txt.destroy()
        except tk.TclError:
            pass
        self._stream_text_widget  = None
        self._stream_bubble_frame = None

        # Re-render content with proper labels / code-blocks / action cards
        self._render_in_bubble(bubble, full_text)

        # Extract suggestions from the AI response (Section 15 of constitution)
        # and render as quick-reply chips below the bubble
        parent_row = bubble.master.master   # bubble -> col -> row

        # ── Multi-SQL batch banner (when AI writes 2+ standalone SQL blocks) ─
        bare_sqls = self._detect_bare_sql_blocks(full_text)
        if len(bare_sqls) >= 2:
            self._render_batch_sql_banner(parent_row.master, bare_sqls)

        self._render_quick_replies(parent_row.master, full_text)

        # ── Persist AI response to chat history ─────────────────
        if self._history:
            self._history.add_message("assistant", full_text)
            self._history.save()

        # Force the canvas layout to update NOW so labels are visible
        bubble.update_idletasks()
        self._chat_canvas.configure(
            scrollregion=self._chat_canvas.bbox("all"))
        self._scroll_bottom()

    def _render_in_bubble(self, bubble: tk.Frame, text: str) -> None:
        """Split text on fenced blocks; dispatch choices/suggestions/actions/code."""
        parts = re.split(r"(```(?:\w*)\n?.*?```)", text, flags=re.DOTALL)
        for part in parts:
            code_match = re.match(r"```(\w*)\n?(.*?)```", part, re.DOTALL)
            if code_match:
                lang = code_match.group(1).strip().lower() or "sql"
                code = code_match.group(2).strip()
                if lang == "choices":       # Section 14 - structured JSON choices
                    self._render_structured_choices(bubble, code)
                elif lang == "suggestions": # Section 15 - skipped here, handled separately
                    pass
                elif lang == "actions":     # Legacy action cards (backward compat)
                    self._render_action_cards(bubble, code)
                else:
                    self._render_code_block(bubble, code, lang)
            else:
                self._render_markdown_text(bubble, part)

    def _render_markdown_text(self, parent: tk.Frame, text: str) -> None:
        """Parses simple markdown tables, lists, and stats into professional Tkinter widgets."""
        lines = text.strip().split('\n')
        
        in_table = False
        table_headers = []
        table_rows = []
        
        def commit_table():
            nonlocal in_table, table_headers, table_rows
            if not in_table or not table_headers:
                return
            
            # Container for the table with a clean border
            tf = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
            tf.pack(fill="x", anchor="center", pady=(10, 16), padx=16)
            
            style = ttk.Style()
            # Professional treeview styling
            style.configure("Chat.Treeview", background="#0d1117", 
                            foreground="#c9d1d9", fieldbackground="#0d1117", 
                            rowheight=28, font=("Segoe UI", 10))
            style.configure("Chat.Treeview.Heading", background="#161b22", 
                            foreground="#58a6ff", font=("Segoe UI", 10, "bold"))
            style.layout("Chat.Treeview", [('Chat.Treeview.treearea', {'sticky': 'nswe'})])
            
            tree = ttk.Treeview(tf, columns=table_headers, show="headings", 
                                height=min(len(table_rows), 12), style="Chat.Treeview")
            tree.pack(fill="x")
            
            tree.tag_configure('odd', background='#161b22')
            tree.tag_configure('even', background='#0d1117')
            
            # Detect mostly Arabic headers to switch alignments
            headers_rtl = any(_is_rtl(h) for h in table_headers)
            anchor_col = "e" if headers_rtl else "w"
            
            for h in table_headers:
                tree.heading(h, text=h.strip())
                tree.column(h, width=150, anchor="center", stretch=True)
                
            for i, r in enumerate(table_rows):
                padded = r + [""] * (len(table_headers) - len(r))
                tag = 'odd' if i % 2 == 0 else 'even'
                tree.insert("", "end", values=padded[:len(table_headers)], tags=(tag,))
                
            in_table = False
            table_headers = []
            table_rows = []

        current_para = []
        for line in lines:
            stripped = line.strip()
            
            if not stripped:
                if current_para:
                    self._create_text_label(parent, "\n".join(current_para))
                    current_para = []
                commit_table()
                continue
                
            # Table detection
            if stripped.startswith('|') and stripped.endswith('|'):
                if current_para:
                    self._create_text_label(parent, "\n".join(current_para))
                    current_para = []
                
                parts = [p.strip() for p in stripped.split('|')[1:-1]]
                if all(all(c in '-:' for c in p) for p in parts if p):
                    continue
                    
                if not in_table:
                    in_table = True
                    table_headers = parts
                else:
                    table_rows.append(parts)
                continue
                
            # Lists/Stats detection
            if stripped.startswith('- ') or stripped.startswith('* ') or re.match(r'^\d+\.\s', stripped):
                if current_para:
                    self._create_text_label(parent, "\n".join(current_para))
                    current_para = []
                commit_table()
                
                raw_txt = re.sub(r'^(-\s|\*\s|\d+\.\s)', '', stripped)
                is_rtl = _is_rtl(stripped)
                
                stat_match = re.search(r'(\d+(?:\.\d+)?)\s*%', raw_txt)
                if stat_match or ':' in raw_txt:
                    # Render as a structured Stat item (Key-Value look)
                    row = tk.Frame(parent, bg="#0d1117", highlightbackground=BORDER, highlightthickness=1)
                    row.pack(fill="x", anchor="w", pady=(3, 3), padx=16)
                    
                    if ":" in raw_txt:
                        parts = raw_txt.rsplit(':', 1)
                        if is_rtl:
                            # In RTL, right side is the key, left is the value
                            tk.Label(row, text="  " + parts[0].strip() + "  ", bg="#0d1117", fg=TEXT, font=FONT_CHAT).pack(side="right", padx=8, pady=6)
                            tk.Label(row, text=parts[1].strip(), bg="#0d1117", fg="#79c0ff", font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=6)
                        else:
                            tk.Label(row, text="  " + parts[0].strip() + "  ", bg="#0d1117", fg=TEXT, font=FONT_CHAT).pack(side="left", padx=8, pady=6)
                            tk.Label(row, text=parts[1].strip(), bg="#0d1117", fg="#79c0ff", font=("Segoe UI", 10, "bold")).pack(side="right", padx=8, pady=6)
                    else:
                        tk.Label(row, text="  •  " + raw_txt, bg="#0d1117", fg="#79c0ff", font=("Segoe UI", 10, "bold"),
                                 justify="right" if is_rtl else "left").pack(side="right" if is_rtl else "left", padx=8, pady=6)
                else:
                    self._create_text_label(parent, "  •  " + raw_txt, padx=16, fg="#d2a8ff")
                continue
                
            # Headers
            if stripped.startswith('#'):
                if current_para:
                    self._create_text_label(parent, "\n".join(current_para))
                    current_para = []
                commit_table()
                
                level = len(stripped) - len(stripped.lstrip('#'))
                f_size = 14 if level == 1 else 12 if level == 2 else 11
                txt = stripped.lstrip('#').strip()
                is_hdr_rtl = _is_rtl(txt)
                
                hdr_container = tk.Frame(parent, bg=AI_BUBBLE)
                hdr_container.pack(fill="x", pady=(18, 6), padx=4)
                
                lbl = tk.Label(hdr_container, text=txt, bg=AI_BUBBLE, fg="#58a6ff", 
                               font=("Segoe UI", f_size, "bold"))
                lbl.pack(anchor="e" if is_hdr_rtl else "w", padx=8)
                
                # Bottom border for headers
                tk.Frame(hdr_container, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 0))
                continue
                
            commit_table()
            current_para.append(stripped)
            
        if current_para:
            self._create_text_label(parent, "\n".join(current_para))
        commit_table()

    def _create_text_label(self, parent: tk.Frame, text: str, padx: int = 0, fg: str = TEXT) -> None:
        if not text: return
        is_rtl = _is_rtl(text)
        
        # Clean basic markdown tags
        clean_text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        clean_text = re.sub(r'__(.*?)__', r'\1', clean_text)
        clean_text = re.sub(r'`(.*?)`', r' \1 ', clean_text)
        
        lbl = tk.Label(parent, text=clean_text, 
                       bg=AI_BUBBLE, fg=fg, font=FONT_CHAT,
                       justify="right" if is_rtl else "left",
                       anchor="e" if is_rtl else "w",
                       wraplength=580)
        lbl.pack(fill="x", anchor="w", pady=(2, 2), padx=(padx, padx))
        self._dynamic_labels.append((lbl, "ai"))

    # ── Structured Choices (Section 14 of Constitution) ────────

    def _render_structured_choices(self, bubble: tk.Frame, json_str: str) -> None:
        """
        Parse a ```choices JSON block and render each item as a clickable action button.
        Supports action types: SQL (show approval dialog) and SEND (send prompt to AI).
        """
        # --- Robust JSON Cleanup (Auto-Healing) ---
        clean_str = json_str.strip()
        
        # 1. Remove accidental markdown fences if any slipped through
        if clean_str.startswith("```"):
            clean_str = re.sub(r'^```(?:json|choices)?', '', clean_str, flags=re.IGNORECASE).strip()
        if clean_str.endswith("```"):
            clean_str = re.sub(r'```$', '', clean_str).strip()
            
        # 2. Fix trailing commas before closing brackets (common LLM hallucination)
        clean_str = re.sub(r',\s*\}', '}', clean_str)
        clean_str = re.sub(r',\s*\]', ']', clean_str)

        try:
            choices = json.loads(clean_str)
        except Exception:
            # 3. Fallback: Aggressive extraction (find the first '[' and last ']')
            m = re.search(r'\[[\s\S]*\]', clean_str)
            if m:
                extracted = m.group(0)
                try:
                    choices = json.loads(extracted)
                except Exception:
                    self._render_code_block(bubble, json_str, "json")
                    return
            else:
                self._render_code_block(bubble, json_str, "json")
                return

        if not isinstance(choices, list) or not choices:
            self._render_code_block(bubble, json_str, "json")
            return

        RISK_COLORS = {"READ": ACCENT2, "WRITE": WARNING, "DANGER": DANGER}
        ACTION_ICON = {"SQL": ">", "SEND": "->"}
        CHIP_COLORS = [ACCENT, ACCENT2, WARNING, "#7c6af7", "#2ea043", "#e05c5c"]

        hdr = tk.Frame(bubble, bg=AI_BUBBLE)
        hdr.pack(fill="x", pady=(8, 4))
        tk.Frame(hdr, bg=BORDER, height=1).pack(fill="x")
        tk.Label(hdr, text="⚡  اختر إجراءً / Choose an action:",
                 bg=AI_BUBBLE, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(4, 0))

        for i, choice in enumerate(choices):
            num     = choice.get("num", i + 1)
            label   = choice.get("label", f"Option {num}")
            desc    = choice.get("desc", "")
            action  = choice.get("action", "SEND").upper()
            sql     = choice.get("sql", "")
            risk    = choice.get("risk", "WRITE").upper()
            prompt  = choice.get("prompt", label)

            if risk not in RISK_COLORS:
                risk = "WRITE"

            chip_color = CHIP_COLORS[i % len(CHIP_COLORS)]
            risk_color = RISK_COLORS.get(risk, WARNING)
            btn_color  = risk_color if action == "SQL" else chip_color

            # Card container
            card = tk.Frame(bubble, bg="#161b22",
                            highlightbackground=btn_color, highlightthickness=1)
            card.pack(fill="x", pady=(0, 5))

            # Header row: badge + label + risk pill
            card_hdr = tk.Frame(card, bg="#0d1117")
            card_hdr.pack(fill="x")

            badge = tk.Label(card_hdr, text=f" {num} ",
                             bg=btn_color, fg=BG,
                             font=("Segoe UI", 10, "bold"), width=3)
            badge.pack(side="left", fill="y", padx=(0, 0))

            lbl_frame = tk.Frame(card_hdr, bg="#0d1117", padx=10, pady=6)
            lbl_frame.pack(side="left", fill="both", expand=True)

            is_rtl = _is_rtl(label)
            tk.Label(lbl_frame, text=label,
                     bg="#0d1117", fg=TEXT,
                     font=FONT_BOLD,
                     anchor="e" if is_rtl else "w").pack(anchor="w")
            if desc:
                is_rtl_d = _is_rtl(desc)
                tk.Label(lbl_frame, text=desc,
                         bg="#0d1117", fg=TEXT_DIM,
                         font=FONT_SMALL,
                         anchor="e" if is_rtl_d else "w",
                         wraplength=440).pack(anchor="w")

            # Type pill (SQL/SEND)
            pill_f = tk.Frame(card_hdr, bg="#0d1117", padx=8)
            pill_f.pack(side="right", fill="y")
            pill_inner = tk.Frame(pill_f, bg=btn_color, padx=6, pady=3)
            pill_inner.pack(anchor="center", expand=True)
            tk.Label(pill_inner,
                     text=f"{ACTION_ICON.get(action,'?')} {risk if bool(sql) else 'SEND'}",
                     bg=btn_color, fg=BG,
                     font=("Segoe UI", 7, "bold")).pack()

            # Stats (if any)
            stats = choice.get("stats")
            if stats and isinstance(stats, list):
                stats_container = tk.Frame(card, bg="#161b22", padx=28, pady=4)
                stats_container.pack(fill="x")
                for st in stats:
                    st_name = st.get("name", "")
                    st_val = st.get("value", "")
                    st_prog = st.get("progress")
                    
                    row = tk.Frame(stats_container, bg="#161b22")
                    row.pack(fill="x", pady=1)
                    
                    val_lbl = tk.Label(row, text=str(st_val), bg="#161b22", fg="#79c0ff", font=("Consolas", 9, "bold"))
                    val_lbl.pack(side="right")
                    
                    is_rtl_st = _is_rtl(str(st_name))
                    tk.Label(row, text=str(st_name), bg="#161b22", fg="#8b949e", font=("Segoe UI", 9), anchor="e" if is_rtl_st else "w").pack(side="left")
                    
                    if isinstance(st_prog, (int, float)):
                        prog_bg = tk.Frame(stats_container, bg="#2d333b", height=5)
                        prog_bg.pack(fill="x", pady=(2, 6))
                        prog_bg.pack_propagate(False)
                        pct_width = max(0, min(1.0, float(st_prog) / 100.0))
                        if pct_width > 0:
                            # Use btn_color for normal action, or fallback
                            prog_bar = tk.Frame(prog_bg, bg=btn_color)
                            prog_bar.place(relx=0, rely=0, relwidth=pct_width, relheight=1.0)
                        else:
                            tk.Frame(prog_bg, bg="#161b22").pack()

            # Execute button
            btn_row = tk.Frame(card, bg="#161b22", padx=8, pady=6)
            btn_row.pack(fill="x")

            if (action == "SQL" or action in ["READ", "WRITE", "DANGER"]) and sql:
                clean = _clean_sql(sql)

                def _make_sql_cb(s=clean, r=risk):
                    def _cb(event=None):
                        self._pending_sql  = s
                        self._pending_risk = r
                        self._pending_verify = ""
                        self._show_sql_approval()
                    return _cb

                _Btn(btn_row, f"  >  نفّذ الآن / Execute Now  ",
                     cmd=_make_sql_cb(),
                     bg=btn_color, hov=btn_color, fg=BG,
                     font=("Segoe UI", 9, "bold"), padx=8, pady=5
                     ).pack(side="left", padx=(0, 6))
            else:
                def _make_send_cb(p=prompt):
                    def _cmd():
                        if self._streaming:
                            return
                        self._placeholder_active = False
                        self._input.config(fg=TEXT)
                        self._input.delete("1.0", "end")
                        self._input.insert("1.0", p)
                        self._update_input_dir()
                        self._send()
                    return _cmd

                _Btn(btn_row, f"  ->  اختيار / Select  ",
                     cmd=_make_send_cb(),
                     bg=btn_color, hov=btn_color, fg=BG,
                     font=("Segoe UI", 9, "bold"), padx=8, pady=5
                     ).pack(side="left", padx=(0, 6))

    # ── Quick Reply Suggestions (Section 15 of Constitution) ────

    _FALLBACK_SUGGESTIONS: list[str] = [
        "حلل جميع الأعمدة تفصيلياً",
        "اكتشف العلاقات الممكنة مع جداول أخرى",
        "ابحث عن أعمدة NULL عالية النسبة",
        "اقترح فهارس لتحسين الأداء",
        "تحقق من القيود والمفاتيح الخارجية",
        "أحصائيات مفصلة لكل الأعمدة",
    ]

    def _extract_suggestions_from_ai(self, ai_text: str) -> list[str]:
        """
        Parse the ```suggestions [...] ``` block output by the AI (Section 15).
        Returns list of suggestion strings, or [] if not found.
        """
        m = re.search(r"```suggestions\s*(\[.*?\])\s*```", ai_text, re.DOTALL)
        if not m:
            return []
        try:
            suggestions = json.loads(m.group(1))
            if isinstance(suggestions, list) and all(isinstance(s, str) for s in suggestions):
                return [s.strip() for s in suggestions[:3] if s.strip()]
        except Exception:
            pass
        return []

    def _render_quick_replies(self, container: tk.Frame, ai_text: str) -> None:
        """
        Render contextual quick-reply chips below the AI bubble.
        First tries to use suggestions provided by the AI (Section 15 block);
        falls back to keyword-based selection if block not found.
        """
        # 1. Try AI-provided suggestions
        suggestions = self._extract_suggestions_from_ai(ai_text)

        # 2. Fallback: keyword-based from pool
        if not suggestions:
            text_lo = ai_text.lower()
            pool = list(self._FALLBACK_SUGGESTIONS)
            priority = []
            if any(w in text_lo for w in ["null", "فارغ"]):
                priority.append("ابحث عن أعمدة NULL عالية النسبة")
            if any(w in text_lo for w in ["index", "فهرس", "أداء"]):
                priority.append("اقترح فهارس لتحسين الأداء")
            if any(w in text_lo for w in ["foreign", "علاقة", "fk", "constraint"]):
                priority.append("اكتشف العلاقات الممكنة مع جداول أخرى")
            if any(w in text_lo for w in ["column", "أعمدة", "بيانات"]):
                priority.append("حلل جميع الأعمدة تفصيلياً")
            if any(w in text_lo for w in ["إحصاء", "count", "statistics"]):
                priority.append("أحصائيات مفصلة لكل الأعمدة")

            seen, final = set(), []
            for item in priority + pool:
                if item not in seen:
                    seen.add(item)
                    final.append(item)
                if len(final) == 3:
                    break
            suggestions = final

        if not suggestions:
            return

        chip_row = tk.Frame(container, bg=PANEL_BG)
        chip_row.pack(fill="x", padx=48, pady=(0, 6))

        for text in suggestions:
            short = text if len(text) <= 48 else text[:45] + "..."

            def _make_cmd(t=text):
                def _cmd():
                    if self._streaming:
                        return
                    self._placeholder_active = False
                    self._input.config(fg=TEXT)
                    self._input.delete("1.0", "end")
                    self._input.insert("1.0", t)
                    self._update_input_dir()
                    self._send()
                return _cmd

            chip = tk.Button(
                chip_row,
                text=f"↪ {short}",
                command=_make_cmd(),
                bg=ENTRY_BG, fg=TEXT_DIM,
                activebackground=BTN_HOVER, activeforeground=TEXT,
                font=("Segoe UI", 8),
                padx=10, pady=4,
                cursor="hand2",
                relief="flat",
                bd=0,
                highlightbackground=BORDER,
                highlightthickness=1,
                overrelief="flat",
            )
            chip.pack(side="left", padx=(0, 6), pady=2)
            chip.bind("<Enter>",
                      lambda e, c=chip: c.config(bg=BTN_HOVER, fg=TEXT,
                                                  highlightbackground=ACCENT))
            chip.bind("<Leave>",
                      lambda e, c=chip: c.config(bg=ENTRY_BG, fg=TEXT_DIM,
                                                  highlightbackground=BORDER))

    # ── Action Cards ──────────────────────────────────────────

    def _render_action_cards(self, parent: tk.Frame, json_str: str) -> None:
        """Parse ```actions JSON block and render interactive execute cards."""
        try:
            actions = json.loads(json_str)
            if not isinstance(actions, list):
                raise ValueError("Expected list")
        except Exception:
            self._render_code_block(parent, json_str, "json")
            return

        hdr = tk.Frame(parent, bg=AI_BUBBLE)
        hdr.pack(fill="x", pady=(6, 4))
        tk.Label(hdr, text="⚡ الإجراءات المقترحة / Suggested Actions",
                 bg=AI_BUBBLE, fg=ACCENT, font=FONT_BOLD).pack(anchor="w")
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(0, 4))

        for i, action in enumerate(actions, 1):
            if action.get("type") == "multistep":
                self._render_multistep_card(parent, action, i)
            else:
                self._render_single_action(parent, action, i)

    def _render_single_action(self, parent: tk.Frame, action: dict, idx: int) -> None:
        risk  = action.get("risk", "WRITE").upper()
        if risk not in RISK_COLOR:
            risk = "WRITE"
        color = RISK_COLOR[risk]
        title = action.get("title", f"Action {idx}")
        desc  = action.get("desc", "")
        raw_sql    = action.get("sql", "")
        verify_sql = action.get("verify_sql", "")
        sql   = _clean_sql(raw_sql)

        # ── Card shell ──
        card = tk.Frame(parent, bg="#1a1f2e",
                        highlightbackground=color, highlightthickness=1)
        card.pack(fill="x", pady=(0, 6))

        # ── Header bar ──
        hdr = tk.Frame(card, bg="#0d1320", padx=0, pady=0)
        hdr.pack(fill="x")

        # Numbered badge
        badge = tk.Frame(hdr, bg=color, width=34)
        badge.pack(side="left", fill="y")
        badge.pack_propagate(False)
        tk.Label(badge, text=str(idx), bg=color, fg=BG,
                 font=("Segoe UI", 11, "bold")).pack(expand=True)

        # Title section
        title_col = tk.Frame(hdr, bg="#0d1320", padx=10, pady=8)
        title_col.pack(side="left", fill="both", expand=True)
        tk.Label(title_col, text=title, bg="#0d1320", fg=TEXT,
                 font=FONT_BOLD, anchor="w").pack(anchor="w")
        if desc:
            is_rtl = _is_rtl(desc)
            tk.Label(title_col, text=desc, bg="#0d1320", fg=TEXT_DIM,
                     font=FONT_SMALL,
                     justify="right" if is_rtl else "left",
                     anchor="e" if is_rtl else "w",
                     wraplength=460).pack(anchor="w", pady=(2, 0))

        # Risk pill on the right
        pill = tk.Frame(hdr, bg="#0d1320", padx=10)
        pill.pack(side="right", fill="y")
        pill_inner = tk.Frame(pill, bg=color, padx=6, pady=3)
        pill_inner.pack(anchor="center", expand=True)
        tk.Label(pill_inner, text=RISK_LABEL[risk],
                 bg=color, fg=BG, font=("Segoe UI", 8, "bold")).pack()

        # ── SQL preview strip ──
        sql_strip = tk.Frame(card, bg="#0d1117", padx=10, pady=5)
        sql_strip.pack(fill="x")
        preview = sql[:110] + ("…" if len(sql) > 110 else "")
        tk.Label(sql_strip, text=preview, bg="#0d1117", fg="#58a6ff",
                 font=FONT_MONO, anchor="w").pack(side="left", fill="x", expand=True)

        # ── Button row ──
        btn_row = tk.Frame(card, bg="#1a1f2e", padx=10, pady=7)
        btn_row.pack(fill="x")

        _Btn(btn_row, "  >  نفّذ / Execute  ",
             cmd=lambda s=sql, v=verify_sql, r=risk:
                 self._trigger_action_execute(s, v, r),
             bg=color, hov=color, fg=BG,
             font=("Segoe UI", 9, "bold"), padx=8, pady=5
             ).pack(side="left", padx=(0, 6))

        _Btn(btn_row, "👁 معاينة SQL",
             cmd=lambda s=sql: self._preview_action_sql(s),
             bg="#21262d", hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=5, font=FONT_SMALL).pack(side="left")

    def _trigger_action_execute(self, sql: str, verify_sql: str, risk: str) -> None:
        """Show security approval then run SQL + verify + ask AI to report."""
        self._pending_sql      = sql
        self._pending_risk     = risk
        self._pending_verify   = verify_sql   # store for post-exec verification
        self._show_sql_approval()

    def _preview_action_sql(self, sql: str) -> None:
        """Show the full SQL in a scrollable popup."""
        dlg = tk.Toplevel(self)
        dlg.title("👁 SQL Preview")
        dlg.geometry("700x380")
        dlg.configure(bg=BG)
        dlg.grab_set()
        tk.Frame(dlg, bg=ACCENT, height=3).pack(fill="x")
        txt = tk.Text(dlg, bg=CODE_BG, fg="#a5d6ff",
                      font=FONT_MONO, relief="flat", padx=14, pady=12, wrap="none")
        txt.insert("1.0", sql)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        _Btn(dlg, "✕ Close", cmd=dlg.destroy,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=12, pady=6).pack(pady=(0, 10))

    def _render_code_block(self, parent: tk.Frame, code: str,
                            lang: str = "sql") -> None:
        """Nice code block: language badge + monospaced dark box.
        For SQL blocks, an inline 'Run & Feed AI' button is added."""
        is_sql = lang.lower() in ("sql", "pgsql", "postgresql")
        border_col = ACCENT if is_sql else BORDER

        wrap = tk.Frame(parent, bg=CODE_BG,
                        highlightbackground=border_col, highlightthickness=1)
        wrap.pack(fill="x", pady=(6, 4))

        # Badge row: language label  +  (for SQL) action buttons on right
        badge_row = tk.Frame(wrap, bg="#0d1117", padx=10, pady=3)
        badge_row.pack(fill="x")
        tk.Label(badge_row, text=lang.upper() or "SQL",
                 bg="#0d1117", fg=TEXT_DIM, font=("Consolas", 8)).pack(side="left")

        if is_sql:
            _captured_code = code  # closure capture

            def _copy_sql(c=_captured_code):
                self.clipboard_clear()
                self.clipboard_append(c)

            def _run_cb(c=_captured_code):
                threading.Thread(
                    target=self._run_inline_sql, args=(c,), daemon=True
                ).start()

            tk.Button(badge_row, text="[clipboard]",
                      command=_copy_sql,
                      bg="#0d1117", fg=TEXT_DIM,
                      activebackground=BTN_HOVER, activeforeground=TEXT,
                      relief="flat", bd=0, cursor="hand2",
                      font=("Segoe UI", 9), padx=4, pady=0
                      ).pack(side="right", padx=2)

            tk.Button(badge_row,
                      text="  > Run & Feed AI  ",
                      command=_run_cb,
                      bg=ACCENT, fg=BG,
                      activebackground=ACCENT2, activeforeground=BG,
                      relief="flat", bd=0, cursor="hand2",
                      font=("Segoe UI", 8, "bold"), padx=8, pady=2
                      ).pack(side="right", padx=(0, 8))

        # code text
        est_h = min(max(3, code.count("\n") + 2), 30)
        txt = tk.Text(
            wrap, bg=CODE_BG, fg="#a5d6ff", font=FONT_MONO,
            relief="flat", wrap="none", state="normal",
            cursor="arrow", height=est_h, padx=12, pady=8,
        )
        txt.insert("1.0", code)
        txt.config(state="disabled")
        txt.pack(fill="x")
        max_line = max((len(l) for l in code.splitlines()), default=10)
        if max_line > 80:
            hsb = ttk.Scrollbar(wrap, orient="horizontal", command=txt.xview)
            txt.config(xscrollcommand=hsb.set)
            hsb.pack(fill="x")
        for w in (wrap, txt):
            w.bind("<MouseWheel>",
                   lambda e: self._chat_canvas.yview_scroll(
                       -1 * (e.delta // 120), "units"))

    # ── Multi-Step Action Cards ────────────────────────────────

    def _render_multistep_card(self, parent: tk.Frame,
                                action: dict, idx: int) -> None:
        """Render a sequential multi-step action card with step-by-step tracking."""
        steps  = action.get("steps", [])
        title  = action.get("title", f"Multi-Step Action {idx}")
        desc   = action.get("desc", "")
        # Determine overall risk (highest across all steps)
        all_risks  = [s.get("risk", "READ").upper() for s in steps]
        risk_order = [RISK_DANGER, RISK_WRITE, RISK_READ]
        overall_risk = next((r for r in risk_order if r in all_risks), RISK_READ)
        color = RISK_COLOR[overall_risk]

        # ── Card shell ──
        card = tk.Frame(parent, bg="#0f1929",
                        highlightbackground=color, highlightthickness=2)
        card.pack(fill="x", pady=(0, 8))

        # ── Header ──
        hdr = tk.Frame(card, bg="#080f1a")
        hdr.pack(fill="x")

        badge = tk.Label(hdr, text=f"  {idx:02d}  ",
                         bg=color, fg=BG,
                         font=("Segoe UI", 11, "bold"))
        badge.pack(side="left")

        tk.Label(hdr, text=f"  🔗  {title}",
                 bg="#080f1a", fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left", fill="y", padx=6)

        risk_pill = tk.Label(hdr, text=f"  {RISK_LABEL[overall_risk]}  ",
                             bg=HEADER_BG, fg=color,
                             font=FONT_SMALL, padx=6, pady=2)
        risk_pill.pack(side="right", padx=8, pady=4)

        # ── Description ──
        if desc:
            tk.Label(card, text=desc, bg="#0f1929", fg=TEXT_DIM,
                     font=FONT_SMALL, anchor="w", justify="left",
                     wraplength=700, padx=12, pady=4).pack(fill="x")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        # ── Steps list ──
        steps_area = tk.Frame(card, bg="#0f1929")
        steps_area.pack(fill="x", padx=10, pady=6)

        step_status_labels: dict[int, tk.Label] = {}

        for s in steps:
            snum  = s.get("step", "?")
            sdesc = s.get("desc", f"Step {snum}")
            srisk = s.get("risk", "READ").upper()
            scol  = RISK_COLOR.get(srisk, RISK_COLOR[RISK_READ])

            row = tk.Frame(steps_area, bg="#131c2c",
                           highlightbackground="#1c2a3a", highlightthickness=1)
            row.pack(fill="x", pady=(0, 3))

            # Step number circle
            step_circ = tk.Label(row, text=f" {snum} ",
                                  bg=scol, fg=BG,
                                  font=("Consolas", 8, "bold"))
            step_circ.pack(side="left")

            tk.Label(row, text=f"  {sdesc}",
                     bg="#131c2c", fg=TEXT,
                     font=FONT_SMALL, anchor="w").pack(side="left", fill="y", padx=4)

            # Risk badge
            tk.Label(row, text=f"{RISK_LABEL[srisk]}",
                     bg="#131c2c", fg=scol,
                     font=("Consolas", 7)).pack(side="right", padx=6)

            # Status label (updated during execution)
            stat_lbl = tk.Label(row, text="⏸", bg="#131c2c", fg=TEXT_DIM,
                                 font=FONT_SMALL)
            stat_lbl.pack(side="right")
            step_status_labels[snum] = stat_lbl

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        # ── Action buttons ──
        btn_row = tk.Frame(card, bg="#080f1a", padx=10, pady=8)
        btn_row.pack(fill="x")

        progress_lbl = tk.Label(btn_row, text="",
                                 bg="#080f1a", fg=TEXT_DIM, font=FONT_SMALL)
        progress_lbl.pack(side="left", padx=(0, 8))

        def _execute_steps():
            """Fire sequential execution in background thread."""
            threading.Thread(
                target=self._run_steps_sequentially,
                args=(steps, step_status_labels, progress_lbl),
                daemon=True,
            ).start()

        exec_btn = tk.Button(
            btn_row,
            text="  >  تنفيذ الكل / Run All Steps  ",
            command=_execute_steps,
            bg=color, fg=BG,
            activebackground=color, activeforeground=BG,
            font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2",
            padx=16, pady=6, bd=0,
        )
        exec_btn.pack(side="right")

    def _run_steps_sequentially(self, steps: list[dict],
                                  status_map: dict, progress_lbl: tk.Label) -> None:
        """Execute multi-step actions one at a time; update status labels live."""
        ctx    = self.context
        schema = ctx.get("src_schema", "public")
        table  = ctx.get("table_name", "")

        def _set_status(snum, icon, col):
            lbl = status_map.get(snum)
            if lbl:
                self.after(0, lambda l=lbl, i=icon, c=col: l.config(text=i, fg=c))

        def _set_progress(msg, col=TEXT_DIM):
            self.after(0, lambda: progress_lbl.config(text=msg, fg=col))

        total   = len(steps)
        results = []

        for step in steps:
            snum  = step.get("step", "?")
            sdesc = step.get("desc", f"Step {snum}")
            risk  = step.get("risk", "READ").upper()
            if risk not in RISK_COLOR:
                risk = RISK_READ

            raw_sql     = step.get("sql", "")
            verify_sql  = step.get("verify_sql", "")
            sql         = _clean_sql(raw_sql)

            if not sql:
                _set_status(snum, "[!]", WARNING)
                continue

            # For DANGER steps: show approval dialog first
            if risk == RISK_DANGER:
                confirmed = threading.Event()
                approved  = [False]

                def _ask_approve(s=sql, r=risk, ev=confirmed, ok=approved, d=sdesc):
                    dlg = tk.Toplevel(self)
                    dlg.title(f"⚡ تأكيد الخطوة / Step Approval - {d}")
                    dlg.geometry("680x300")
                    dlg.configure(bg=BG)
                    dlg.grab_set()
                    dlg.resizable(False, False)
                    color_d = RISK_COLOR[r]
                    tk.Frame(dlg, bg=color_d, height=4).pack(fill="x")
                    tk.Label(dlg, text=f"[red]  {d}",
                             bg=BG, fg=color_d,
                             font=("Segoe UI", 12, "bold"),
                             padx=14, pady=8).pack(anchor="w")
                    txt = tk.Text(dlg, bg=CODE_BG, fg="#a5d6ff",
                                  font=FONT_MONO, relief="flat",
                                  height=6, padx=10, pady=8)
                    txt.insert("1.0", s); txt.config(state="disabled")
                    txt.pack(fill="x", padx=14, pady=(0, 8))
                    br = tk.Frame(dlg, bg=BG); br.pack(fill="x", padx=14, pady=8)

                    def _yes():
                        ok[0] = True; dlg.destroy(); ev.set()

                    def _no():
                        dlg.destroy(); ev.set()

                    tk.Button(br, text=">  نفّذ / Execute",
                              command=_yes,
                              bg=color_d, fg=BG,
                              font=("Segoe UI", 10, "bold"),
                              relief="flat", cursor="hand2",
                              padx=14, pady=8, bd=0).pack(side="left", padx=(0, 8))
                    tk.Button(br, text="✕  تخطّ / Skip",
                              command=_no,
                              bg="#21262d", fg=TEXT_DIM,
                              font=FONT_UI, relief="flat",
                              cursor="hand2", padx=10, pady=8, bd=0).pack(side="left")

                self.after(0, _ask_approve)
                confirmed.wait(timeout=300)   # wait up to 5 min for user
                if not approved[0]:
                    _set_status(snum, "⏭", TEXT_DIM)
                    _set_progress(f"⏭ Skipped step {snum}", WARNING)
                    results.append({"step": snum, "status": "skipped"})
                    continue

            # Execute SQL
            _set_status(snum, "[wait]", WARNING)
            _set_progress(f"[wait] Executing step {snum}/{total}: {sdesc[:30]}…", WARNING)

            try:
                conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                        cursor_factory=pg_extras.RealDictCursor)
                conn.autocommit = False
                step_result: dict = {"step": snum, "status": "ok"}

                with conn.cursor() as cur:
                    if risk == RISK_READ:
                        # ── Always run READ on Workspace if it exists ──
                        _schema, _orig, _copy = self._get_safe_copy_name()
                        cur.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema=%s AND table_name=%s",
                            (_schema, _copy)
                        )
                        _ws = cur.fetchone() is not None
                        _target = _copy if _ws else _orig
                        _read_sql = self._rewrite_sql_for_copy(sql, _schema, _orig, _target)
                        cur.execute(_read_sql)
                        rows = list(cur.fetchmany(1000))
                        cols = [d.name for d in cur.description] if cur.description else []
                        conn.rollback()
                        step_result["rows"] = rows
                        step_result["cols"] = cols
                        self.after(0, lambda r=rows, c=cols: self._show_results(r, c))
                    else:
                        cur.execute(sql)
                        rowcount = cur.rowcount
                        conn.commit()
                        step_result["rowcount"] = rowcount

                # verify_sql check
                if verify_sql and risk != RISK_READ:
                    vsql = _clean_sql(verify_sql)
                    with conn.cursor() as cur:
                        cur.execute(vsql)
                        vrow = cur.fetchone()
                        vval = list(vrow.values())[0] if vrow else None
                        step_result["verify"] = vval
                        if vval == 0:
                            step_result["status"] = "verify_fail"

                conn.close()
                if step_result["status"] == "verify_fail":
                    _set_status(snum, "[!]", WARNING)
                    _set_progress(f"[!] Step {snum} verify returned 0", WARNING)
                else:
                    _set_status(snum, "[ok]", ACCENT2)

            except Exception as e:
                _set_status(snum, "[x]", DANGER)
                _set_progress(f"[x] Step {snum} failed: {str(e)[:60]}", DANGER)
                results.append({"step": snum, "status": "error", "error": str(e)})
                # Send failure report to AI
                err_report = (
                    f"فشلت الخطوة {snum}: {sdesc}\n"
                    f"SQL: {sql[:200]}\n"
                    f"Error: {e}\n"
                    f"ما الإجراء المناسب لإصلاح هذا الخطأ؟"
                )
                self.after(100, lambda r=err_report: self._inject_and_send(r))
                return
            else:
                results.append(step_result)

        # ── All steps done - generate AI summary ──
        ok_steps    = [r for r in results if r["status"] == "ok"]
        fail_steps  = [r for r in results if r["status"] == "error"]
        skip_steps  = [r for r in results if r["status"] == "skipped"]
        _set_progress(f"[ok] Done: {len(ok_steps)}/{total} steps | "
                      f"⏭ {len(skip_steps)} skipped", ACCENT2)

        summary = (
            f"تم الانتهاء من تنفيذ {total} خطوة:\n"
            f"[ok] ناجح: {len(ok_steps)} | [x] فشل: {len(fail_steps)} | ⏭ تخطّ: {len(skip_steps)}\n\n"
            + "\n".join(
                f"  خطوة {r['step']}: {r['status']}" +
                (f" - {r.get('error', '')[:80]}" if r["status"] == "error" else "")
                for r in results
            )
            + "\n\nقدّم ملخصاً للنتيجة وأي تحقق إضافي مطلوب."
        )
        self.after(200, lambda s=summary: self._inject_and_send(s))
        # Refresh table data
        try:
            self.after(500, lambda: self.on_apply({"__refresh__": True}))
        except Exception:
            pass

    # ── Relationship Analysis ──────────────────────────────────

    def _analyze_relationships(self) -> None:
        """Send a targeted relationship analysis prompt to the AI."""
        schema = self.context.get("src_schema", "public")
        table  = self.context.get("table_name", "?")
        rel    = self._rel_context

        if not rel:
            self._sys("[wait] جاري جلب بيانات العلاقات…  /  Fetching relationship data…")
            threading.Thread(target=self._fetch_relationship_context, daemon=True).start()
            self.after(3000, self._analyze_relationships)
            return

        existing   = rel.get("existing_fks", [])
        candidates = rel.get("candidate_fks", [])

        lines = [
            f"## تحليل علاقات الجدول {schema}.{table}",
            "",
            f"### العلاقات الموجودة ({len(existing)}):",
        ]
        if existing:
            for fk in existing:
                lines.append(
                    f"- FK `{fk['constraint_name']}`: "
                    f"`{fk['fk_column']}` -> "
                    f"`{fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}`"
                )
        else:
            lines.append("- لا توجد Foreign Keys مُعرَّفة حالياً.")

        lines += [
            "",
            f"### الأعمدة المرشحة لعلاقات جديدة ({len(candidates)}):",
        ]
        if candidates:
            for c in candidates:
                lines.append(
                    f"- `{c['column_name']}` ({c['data_type']}) -> "
                    f"possible ref: `{c['potential_ref_table']}.{c['potential_ref_column']}`"
                )
        else:
            lines.append("- لم يتم اكتشاف أعمدة مرشحة تلقائياً.")

        lines += [
            "",
            "بناءً على هذه المعلومات:",
            "1. قيّم جودة العلاقات الموجودة (هل هناك FKs مفقودة؟)",
            "2. لكل عمود مرشح: أنشئ إجراء **multistep** يتحقق من الأعمدة وينشئ الـ FK",
            "3. استخدم `.is_nullable = YES` كمؤشر لـ FK اختياري",
            "4. تأكد من تطابق الـ data types قبل إنشاء الـ FK",
        ]

        prompt = "\n".join(lines)
        self._inject_and_send(prompt)

    def _update_rel_sidebar(self) -> None:
        """Refresh the relationship mini-panel in the sidebar with fetched FK data."""
        frame = getattr(self, "_rel_sidebar_frame", None)
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()

        rel = self._rel_context
        err = rel.get("error")
        if err:
            tk.Label(frame, text=f"[!] {err[:50]}",
                     bg=PANEL_BG, fg=DANGER, font=FONT_SMALL,
                     wraplength=220).pack(anchor="w")
            return

        existing   = rel.get("existing_fks", [])
        candidates = rel.get("candidate_fks", [])

        def _mini(text, fg=TEXT_DIM):
            tk.Label(frame, text=text, bg=PANEL_BG, fg=fg,
                     font=("Consolas", 8), anchor="w",
                     wraplength=224).pack(fill="x")

        _mini(f"FKs: {len(existing)} found", ACCENT2 if existing else TEXT_DIM)
        for fk in existing[:4]:
            _mini(f"  ↳ {fk['fk_column']} -> {fk['ref_table']}.{fk['ref_column']}", ACCENT2)
        if len(existing) > 4:
            _mini(f"  … +{len(existing)-4} more")

        if candidates:
            _mini(f"Candidates: {len(candidates)}", WARNING)
            for c in candidates[:3]:
                _mini(f"  ? {c['column_name']} -> {c['potential_ref_table']}", WARNING)
            if len(candidates) > 3:
                _mini(f"  … +{len(candidates)-3} more")
        else:
            _mini("Candidates: none", TEXT_DIM)

    # ── SQL Execution ──────────────────────────────────────────

    def _show_sql_approval(self) -> None:
        if not self._pending_sql:
            return
        sql  = self._pending_sql
        risk = self._pending_risk

        if risk == RISK_BLOCKED:
            messagebox.showerror(
                "[blocked] محظور / Blocked",
                f"هذا الأمر محظور لأسباب أمنية.\n"
                f"This SQL is blocked for security reasons:\n\n{sql[:200]}",
                parent=self,
            )
            return

        if not _PSYCOPG2_AVAILABLE:
            messagebox.showerror(
                "Missing Dependency",
                "psycopg2 is required for SQL execution.\n"
                "Install: pip install psycopg2-binary",
                parent=self,
            )
            return

        if not self._dsn:
            messagebox.showerror("No Connection",
                                 "Database connection (DSN) is not available.",
                                 parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("⚡ SQL Approval / موافقة على التنفيذ")
        dlg.geometry("720x640")
        dlg.configure(bg=BG)
        dlg.grab_set()
        dlg.resizable(False, False)

        color = RISK_COLOR[risk]

        # ── Top accent bar ──
        tk.Frame(dlg, bg=color, height=4).pack(fill="x")

        # ── Header ──
        hdr = tk.Frame(dlg, bg=HEADER_BG, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        risk_icon = {
            RISK_READ:   "[ok]", RISK_WRITE:  "[yellow]", RISK_DANGER: "[red]",
        }.get(risk, "⚡")
        tk.Label(hdr, text=f"  {risk_icon}  {RISK_LABEL[risk]}",
                 bg=HEADER_BG, fg=color, font=("Segoe UI", 13, "bold"),
                 padx=12).pack(side="left", fill="y")
        desc_map = {
            RISK_READ:   "استعلام فقط - Read-only query, no changes",
            RISK_WRITE:  "تعديل بيانات - This will modify existing data",
            RISK_DANGER: "تعديل هيكل - This will alter the table structure (DDL)",
        }
        tk.Label(hdr, text=desc_map.get(risk, ""),
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_SMALL,
                 padx=20).pack(side="left", fill="y")

        # ── Bottom action area - packed BEFORE expand=True widget ──────────────
        bottom = tk.Frame(dlg, bg=BG, padx=16, pady=12)
        bottom.pack(fill="x", side="bottom")

        # ── Safe Copy Banner (WRITE / DANGER only) - inside bottom ──────────────
        if risk in (RISK_WRITE, RISK_DANGER):
            schema, orig_table, copy_table = self._get_safe_copy_name()
            safe_bg = "#091420"
            safe_frame = tk.Frame(bottom, bg=safe_bg,
                                  highlightbackground="#1f6feb",
                                  highlightthickness=1)
            safe_frame.pack(fill="x", pady=(0, 6))
            tk.Label(safe_frame,
                     text=f"🔵  Workspace Mode  -  جدول العمل الثابت  ({copy_table})",
                     bg=safe_bg, fg="#58a6ff",
                     font=("Segoe UI", 9, "bold"),
                     padx=14, pady=6).pack(anchor="w")
            tk.Label(safe_frame,
                     text=f'   سيتم تطبيق الإجراء على Workspace ثابت - الجدول الأصلي لن يُمسّ:',
                     bg=safe_bg, fg=TEXT_DIM,
                     font=FONT_SMALL, padx=14).pack(anchor="w")
            tk.Label(safe_frame,
                     text=f'   [clipboard]  "{schema}"."{copy_table}"',
                     bg=safe_bg, fg="#79c0ff",
                     font=FONT_MONO, padx=14).pack(anchor="w", pady=(0, 6))
            tk.Label(safe_frame,
                     text="   كل الشوت تتراكم على نفس الـ Workspace حتى تضغط 'تطبيق على الأصلي'.",
                     bg=safe_bg, fg=TEXT_DIM,
                     font=("Segoe UI", 8, "italic"),
                     padx=14,
                     wraplength=680).pack(anchor="w", pady=(0, 8))
        else:
            schema, orig_table, copy_table = (
                self.context.get("src_schema", "public"),
                self.context.get("table_name", "table"),
                ""
            )

        if risk in (RISK_WRITE, RISK_DANGER):
            warn_bg = "#1a0a0a" if risk == RISK_DANGER else "#1a1400"
            notice  = {
                RISK_WRITE:  "[yellow]  سيتم تعديل بيانات على النسخة الآمنة.",
                RISK_DANGER: "[red]  سيتم تعديل هيكل النسخة الآمنة (DDL).",
            }[risk]
            warn_frame = tk.Frame(bottom, bg=warn_bg,
                                  highlightbackground=color, highlightthickness=1,
                                  padx=14, pady=8)
            warn_frame.pack(fill="x", pady=(0, 8))
            tk.Label(warn_frame, text=notice, bg=warn_bg, fg=color,
                     font=(FONT_SMALL[0], FONT_SMALL[1]),
                     justify="left", wraplength=660).pack(anchor="w")

        btn_row = tk.Frame(bottom, bg=BG)
        btn_row.pack(fill="x")

        def _do_execute():
            dlg.destroy()
            threading.Thread(target=self._run_sql,
                             args=(sql, risk), daemon=True).start()

        tk.Button(
            btn_row,
            text="  >  نفّذ الآن / Execute Now  ",
            command=_do_execute,
            bg=color, fg=BG,
            activebackground=color, activeforeground=BG,
            font=("Segoe UI", 12, "bold"),
            relief="flat", cursor="hand2",
            padx=20, pady=10, bd=0,
        ).pack(side="left", padx=(0, 12))

        tk.Button(
            btn_row,
            text="  ✕  إلغاء / Cancel  ",
            command=dlg.destroy,
            bg="#21262d", fg=TEXT_DIM,
            activebackground=BTN_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 11),
            relief="flat", cursor="hand2",
            padx=14, pady=10, bd=0,
        ).pack(side="left")

        # ── SQL preview - packed LAST so it doesn't push buttons off screen ──────
        prev = tk.Frame(dlg, bg="#0d1117",
                        highlightbackground=color, highlightthickness=2)
        prev.pack(fill="both", expand=True, padx=16, pady=(12, 6))

        badge = tk.Frame(prev, bg="#090e1a", padx=10, pady=4)
        badge.pack(fill="x")
        tk.Label(badge, text="SQL (Original - will be rewritten to target the copy)",
                 bg="#090e1a", fg=color,
                 font=("Consolas", 9, "bold")).pack(side="left")

        sql_txt = tk.Text(prev, bg="#0d1117", fg="#58a6ff",
                          font=FONT_MONO, relief="flat", padx=14, pady=10,
                          wrap="none", selectbackground="#264f78")
        sql_txt.insert("1.0", sql)
        sql_txt.config(state="disabled")

        sb = ttk.Scrollbar(prev, orient="horizontal", command=sql_txt.xview)
        sql_txt.config(xscrollcommand=sb.set)
        sql_txt.pack(fill="both", expand=True)
        sb.pack(fill="x")


    # ── Safe Copy Helpers ─────────────────────────────────────

    def _get_safe_copy_name(self) -> tuple[str, str, str]:
        """
        Returns (schema, original_table, workspace_table).

        Smart detection rules:
        - If the table name already ends with '_tmp', it IS the workspace.
          In that case: orig_table = table without suffix, copy = table as-is.
        - Otherwise: orig_table = table, copy = {table}_tmp
        This prevents ever creating a _tmp_tmp chain.
        """
        schema = self.context.get("src_schema", "public")
        table  = self.context.get("table_name", "table")

        if table.endswith("_tmp"):
            # Already working on a workspace table - use as-is
            orig   = table[:-4]   # strip "_tmp" suffix for display/commit
            copy   = table        # workspace IS this table
        else:
            orig   = table
            copy   = f"{table}_tmp"

        return schema, orig, copy

    def _commit_workspace_dialog(self) -> None:
        """Prompt to commit the _tmp workspace to original."""
        from tkinter import messagebox
        schema, table, copy_table = self._get_safe_copy_name()
        
        # Check if tmp exists
        try:
            import psycopg2
            from psycopg2 import sql
            conn = psycopg2.connect(self._dsn, connect_timeout=5)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = %s AND table_name = %s
                    )
                """, (schema, copy_table))
                exists = cur.fetchone()[0]
            conn.close()
        except:
            exists = False

        if not exists:
            messagebox.showinfo("No Workspace", "لا توجد بيئة عمل (Workspace) نشطة أو لم تقم بأي تعديلات بعد.", parent=self)
            return

        msg = (
            f"[!] هل أنت متأكد من تطبيق تعديلات بيئة العمل على الجدول الأصلي؟\n\n"
            f"من: {schema}.{copy_table}\n"
            f"إلى: {schema}.{table}\n\n"
            f"هذا الإجراء سيقوم باستبدال الجدول الأصلي بكافة محتويات الـ Workspace."
        )
        if messagebox.askyesno("Commit Workspace", msg, parent=self):
            threading.Thread(target=self._commit_workspace, daemon=True).start()

    def _commit_workspace(self) -> None:
        schema, orig_table, copy_table = self._get_safe_copy_name()
        self.after(0, lambda: self._sys(f"🚀 بدء نقل بيانات `{copy_table}` إلى `{orig_table}`..."))
        try:
            import psycopg2
            from psycopg2 import sql
            conn = psycopg2.connect(self._dsn, connect_timeout=15)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                        sql.Identifier(schema), sql.Identifier(orig_table)
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                        sql.Identifier(schema), sql.Identifier(copy_table), sql.Identifier(orig_table)
                    )
                )
            conn.close()
            self._clear_workspace_snapshots()
            self.after(0, lambda: self._sys(f"[ok] تم التطبيق بنجاح! تم حفظ كافة التعديلات في الأصل."))
        except Exception as e:
            self.after(0, lambda: self._sys(f"[x] خطأ أثناء التطبيق: {e}"))

    def _rewrite_sql_for_copy(self, sql: str,
                               schema: str, orig: str, copy: str) -> str:
        """Replace all table references with the workspace copy name."""
        if orig == copy:
            return sql

        result   = sql
        esc_orig = re.escape(orig)
        esc_sch  = re.escape(schema)
        # suffix that distinguishes copy from orig (e.g. _tmp)
        sfx = copy[len(orig):] if len(copy) > len(orig) else None

        def _sub(pattern, repl):
            nonlocal result
            try:
                result = re.sub(pattern, repl, result)
            except re.error:
                pass

        neg = (r'(?!' + re.escape(sfx) + r')') if sfx else ''

        # P1: "schema"."orig"  (double-quoted)
        _sub(
            '"' + esc_sch + '"' + r'[\s]*\.' + r'[\s]*' + '"' + esc_orig + '"' + neg,
            '"' + schema + '"' + '.' + '"' + copy + '"'
        )

        # P2: schema.orig (unquoted)
        _sub(
            r'(?<![\w.])' + esc_sch + r'\.' + esc_orig + neg + r'(?=[\s,;()|]|$)',
            schema + '.' + copy
        )

        # P3: "orig" (quoted, no schema)
        _sub(
            r'(?<!\.)' + '"' + esc_orig + '"' + neg,
            '"' + copy + '"'
        )

        # P4: 'orig' string literal (e.g. table_name = 'orders')
        _sub(
            "'" + esc_orig + "'" + neg,
            "'" + copy + "'"
        )

        return result

    def _run_sql(self, sql: str, risk: str) -> None:
        """Route to safe-copy execution for WRITE/DANGER, direct for READ."""
        if risk in (RISK_WRITE, RISK_DANGER):
            self._run_sql_on_copy(sql, risk)
        else:
            self._run_sql_direct(sql, risk)

    # ── Inline SQL Execution (> Run & Feed AI) ────────────────────────────

    def _run_inline_sql(self, sql: str) -> None:
        """
        Run a standalone SQL block from the AI chat.
        - READ queries: rewrite table refs to workspace (_tmp) then execute.
        - WRITE/DANGER: route through approval dialog first.
        """
        sql_clean = sql.strip()
        risk = _classify_sql(sql_clean)

        if risk != RISK_READ:
            # Let the normal approval flow handle it
            self.after(0, lambda: (
                setattr(self, "_pending_sql",  sql_clean),
                setattr(self, "_pending_risk", risk),
                self._show_sql_approval()
            ))
            return

        # ── Redirect READ to workspace so analysis is always on latest data ─
        schema, orig_table, copy_table = self._get_safe_copy_name()
        
        def execute_read():
            try:
                conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                        cursor_factory=pg_extras.RealDictCursor)
                conn.autocommit = False
                try:
                    with conn.cursor() as cur:
                        # Check if workspace exists; if yes, rewrite SQL to target it
                        cur.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema=%s AND table_name=%s",
                            (schema, copy_table)
                        )
                        ws_exists = cur.fetchone() is not None

                        target    = copy_table if ws_exists else orig_table
                        read_sql  = self._rewrite_sql_for_copy(
                            sql_clean, schema, orig_table, target
                        )

                        if ws_exists and target == copy_table:
                            self.after(0, lambda: self._sys(
                                f"🔵 الاستعلام يعمل على Workspace: {copy_table}"
                            ))

                        cur.execute(read_sql)
                        rows = list(cur.fetchmany(1000))
                        cols = [d.name for d in cur.description] if cur.description else []
                        conn.rollback()
                finally:
                    conn.close()

                result_text = self._format_results_for_ai(
                    rows, cols, label=sql_clean[:80])
                feedback = (
                    f"نتائج الاستعلام:\n\n{result_text}\n\n"
                    f"بناءً على هذه النتائج، أكمل تحليلك."
                )
                self.after(0, lambda f=feedback: self._inject_and_send(f))

            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._sys(f"[x] Inline SQL Error: {err}"))
                self._ask_ai_about_error(sql_clean, err)

        self.after(0, lambda: self._sys("[wait] Running inline SQL on Workspace..."))
        threading.Thread(target=execute_read, daemon=True).start()

    # ── Multi-SQL Batch (Run All & Feed Results) ───────────────────────────

    _SQL_BLOCK_RE = re.compile(
        r"```(?:sql|pgsql|postgresql)\n(.*?)```",
        re.DOTALL | re.IGNORECASE
    )
    # Blocks that already have dedicated UI (skip from "bare" detection)
    _SKIP_PATTERNS = re.compile(
        r"```(?:actions|choices|multistep)", re.IGNORECASE
    )

    def _detect_bare_sql_blocks(self, text: str) -> list[str]:
        """
        Find SQL code blocks that are NOT inside actions/choices/multistep.
        Returns list of SQL strings if 2+ found (AI is waiting for results).
        """
        # Strip out blocks that have dedicated cards first
        stripped = re.sub(r"```(?:actions|choices|multistep)\b.*?```",
                          "", text, flags=re.DOTALL | re.IGNORECASE)
        sqls = self._SQL_BLOCK_RE.findall(stripped)
        return [s.strip() for s in sqls if s.strip()]

    def _render_batch_sql_banner(self, container: tk.Frame,
                                  sql_list: list[str]) -> None:
        """Show a 'Run All' banner when AI wrote 2+ SQL blocks."""
        n = len(sql_list)
        card = tk.Frame(container, bg="#0a1a2a",
                        highlightbackground=ACCENT, highlightthickness=2)
        card.pack(fill="x", padx=8, pady=(4, 6))

        hdr = tk.Frame(card, bg="#091420", padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr,
                 text=f"[chart]  الذكاء الاصطناعي ينتظر نتائج {n} استعلامات",
                 bg="#091420", fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(hdr,
                 text=f"   AI is waiting for results of {n} queries - run them and feed results back.",
                 bg="#091420", fg=TEXT_DIM,
                 font=FONT_SMALL).pack(anchor="w")

        btn_f = tk.Frame(card, bg="#0a1a2a", padx=12, pady=8)
        btn_f.pack(fill="x")

        def _run_all():
            threading.Thread(
                target=self._run_batch_sql, args=(sql_list,), daemon=True
            ).start()

        def _run_sequential():
            self._batch_sequential_idx = 0
            self._batch_sequential_list = sql_list
            self._batch_sequential_results: list[str] = []
            self.after(0, self._run_next_in_sequential_batch)

        tk.Button(btn_f,
                  text="  > Run All & Feed Results  ",
                  command=_run_all,
                  bg=ACCENT, fg=BG,
                  activebackground=ACCENT2, activeforeground=BG,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=6
                  ).pack(side="left", padx=(0, 8))

        tk.Button(btn_f,
                  text="  [clipboard] Run One by One  ",
                  command=_run_sequential,
                  bg=ENTRY_BG, fg=TEXT,
                  activebackground=BTN_HOVER, activeforeground=TEXT,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=6
                  ).pack(side="left")

    def _run_batch_sql(self, sql_list: list[str]) -> None:
        """Execute all SQL queries sequentially, compile results, feed to AI."""
        n = len(sql_list)
        self.after(0, lambda: self._sys(f"[wait] Running {n} queries in batch..."))
        all_results: list[str] = []

        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=15,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    for idx, sql in enumerate(sql_list, 1):
                        try:
                            cur.execute(sql)
                            rows = cur.fetchmany(500)
                            cols = [d.name for d in cur.description] if cur.description else []
                            block = self._format_results_for_ai(
                                rows, cols, label=f"Query {idx}/{n}")
                            all_results.append(block)
                            self.after(0, lambda i=idx, b=block: self._sys(
                                f"[ok] Query {i}/{n} done"))
                        except Exception as e:
                            all_results.append(
                                f"**Query {idx}/{n}:** ERROR - {e}")
                conn.rollback()
            finally:
                conn.close()

            combined = "\n\n".join(all_results)
            feedback = (
                f"نتائج جميع الاستعلامات ({n} استعلامات):\n\n"
                f"{combined}\n\n"
                f"بناءً على هذه النتائج المجمّعة، أكمل تحليلك وأعطِ توصياتك."
            )
            self.after(300, lambda: self._inject_and_send(feedback))

        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._sys(f"[x] Batch error: {err}"))

    def _run_next_in_sequential_batch(self) -> None:
        """Step through SQL list one-by-one for sequential batch mode."""
        idx = getattr(self, "_batch_sequential_idx", 0)
        lst = getattr(self, "_batch_sequential_list", [])
        res = getattr(self, "_batch_sequential_results", [])

        if idx >= len(lst):
            # All done - feed combined to AI
            combined = "\n\n".join(res)
            feedback = (
                f"نتائج الاستعلامات ({len(lst)} استعلامات):\n\n"
                f"{combined}\n\nأكمل التحليل."
            )
            self._inject_and_send(feedback)
            return

        sql = lst[idx]
        self._sys(f"[wait] Running query {idx+1}/{len(lst)}...")
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchmany(500)
                cols = [d.name for d in cur.description] if cur.description else []
                conn.rollback()
            conn.close()
            block = self._format_results_for_ai(rows, cols, f"Query {idx+1}")
            res.append(block)
        except Exception as e:
            res.append(f"Query {idx+1} ERROR: {e}")

        self._batch_sequential_idx = idx + 1
        self._batch_sequential_results = res
        self.after(500, self._run_next_in_sequential_batch)

    @staticmethod
    def _format_results_for_ai(rows: list, cols: list,
                                label: str = "Result") -> str:
        """Format query result rows as a compact text table for AI context."""
        if not rows:
            return f"**{label}:** (no rows returned)"

        header = " | ".join(cols)
        sep    = "-+-".join("-" * min(len(c), 20) for c in cols)
        lines  = [f"**{label}** ({len(rows)} rows):", header, sep]

        for row in rows:
            vals = []
            for c in cols:
                v = row.get(c)
                s = str(v) if v is not None else "NULL"
                vals.append(s[:120] + ("..." if len(s) > 120 else ""))
            lines.append(" | ".join(vals))

        return "\n".join(lines)

    # ── SQL Editor Window ──────────────────────────────────────────────────

    def _open_sql_editor(self) -> None:
        """Open a full-featured SQL editor window with AI integration."""
        editor = _SqlEditorWindow(self, self._dsn, self.context,
                                  on_feed_ai=self._inject_and_send)
        editor.grab_set()

    def _run_sql_direct(self, sql: str, risk: str) -> None:
        """Execute READ queries - always redirected to Workspace if it exists."""
        verify_sql = getattr(self, "_pending_verify", "")
        schema, orig_table, copy_table = self._get_safe_copy_name()

        def _do_read():
            try:
                conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                        cursor_factory=pg_extras.RealDictCursor)
                conn.autocommit = False
                try:
                    with conn.cursor() as cur:
                        # Prefer workspace for accuracy
                        cur.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema=%s AND table_name=%s",
                            (schema, copy_table)
                        )
                        ws_exists = cur.fetchone() is not None
                        target   = copy_table if ws_exists else orig_table
                        rsql     = self._rewrite_sql_for_copy(sql, schema, orig_table, target)

                        self.after(0, lambda t=target: self._sys(
                            f"🔵 READ -> {t}"
                        ))
                        cur.execute(rsql)
                        rows = list(cur.fetchmany(1000))
                        cols = [d.name for d in cur.description] if cur.description else []
                        conn.rollback()
                        self.after(0, lambda r=rows, c=cols: self._show_results(r, c))
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._sys(f"[x] SQL Error: {err}"))
                self._ask_ai_about_error(sql, err)

        self.after(0, lambda: self._sys(f"[wait] Executing [{RISK_LABEL[risk]}]..."))
        threading.Thread(target=_do_read, daemon=True).start()

    def _run_sql_on_copy(self, sql: str, risk: str) -> None:
        """
        Persistent Workspace (_tmp) execution flow — Savepoint edition:

        ARCHITECTURE:
        • Workspace (_tmp) = empty shell created at migration time (structure only).
        • First WRITE → workspace gets populated lazily from original (if empty).
        • Every subsequent WRITE → SAVEPOINT set before, execute, no extra tables.
        • Undo → ROLLBACK TO SAVEPOINT (zero storage cost).

        WHY SAVEPOINTS instead of _bak_ tables:
        • Old approach: full table copy per action → 8 GB table = 8 GB extra per step.
        • New approach: Savepoint inside the same transaction → 0 bytes extra storage.
        """
        verify_sql  = getattr(self, "_pending_verify", "")
        schema, orig_table, copy_table = self._get_safe_copy_name()

        # If table_name itself ends with _tmp it IS the workspace - flag this
        is_already_workspace = (orig_table + "_tmp" == copy_table) is False and \
                               self.context.get("table_name", "").endswith("_tmp")

        self.after(0, lambda: self._sys(
            f"🔵 Workspace Mode — Savepoint Engine:\n"
            f'   "{schema}"."{copy_table}"'))

        try:
            # ── Re-use a persistent workspace connection (keeps savepoints alive) ──
            ws_conn = getattr(self, "_ws_conn", None)
            if ws_conn is None or ws_conn.closed:
                ws_conn = psycopg2.connect(
                    self._dsn, connect_timeout=15,
                    cursor_factory=pg_extras.RealDictCursor,
                )
                ws_conn.autocommit = False
                self._ws_conn = ws_conn

            with ws_conn.cursor() as cur:

                # ── Step 1: Ensure workspace table exists ────────────────────
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (schema, copy_table)
                )
                workspace_exists = cur.fetchone() is not None

                if is_already_workspace:
                    workspace_exists = True
                    self.after(0, lambda: self._sys(
                        f'[i] الجدول الحالي هو نفسه Workspace (ينتهي بـ _tmp).\n'
                        f'   لن يتم إنشاء نسخة جديدة.'))

                elif not workspace_exists:
                    # Workspace was dropped or never created → rebuild structure
                    cur.execute(
                        f'CREATE TABLE "{schema}"."{copy_table}" '
                        f'(LIKE "{schema}"."{orig_table}" INCLUDING ALL);'
                    )
                    ws_conn.commit()
                    workspace_exists = False   # still empty — triggers lazy fill below
                    self.after(0, lambda: self._sys(
                        f'[+] Workspace shell recreated (empty):\n'
                        f'   "{schema}"."{copy_table}"'))

                # ── Step 2: Lazy-fill workspace if it is empty ───────────────
                if not is_already_workspace:
                    cur.execute(f'SELECT 1 FROM "{schema}"."{copy_table}" LIMIT 1;')
                    is_empty = cur.fetchone() is None
                    if is_empty:
                        self.after(0, lambda: self._sys(
                            f'[wait] Workspace is empty — loading data from original…'))
                        cur.execute(
                            f'INSERT INTO "{schema}"."{copy_table}" '
                            f'SELECT * FROM "{schema}"."{orig_table}";'
                        )
                        ws_conn.commit()
                        self.after(0, lambda: self._sys(
                            f'[ok] Workspace populated from original.\n'
                            f'   الجدول الأصلي لن يُعدَّل نهائياً.'))
                    else:
                        self.after(0, lambda: self._sys(
                            f'[reload] Reusing existing workspace:\n'
                            f'   "{schema}"."{copy_table}"'))

                # ── Step 3: SAVEPOINT before every WRITE (replaces _bak_ tables) ─
                sp_name = f"sp_{int(time.time())}"
                cur.execute(f"SAVEPOINT {sp_name};")
                self._workspace_snapshots.append(sp_name)
                self.after(0, lambda: self._undo_btn.set_enabled() if self._undo_btn else None)

                # ── Step 4: Rewrite SQL to target workspace ──────────────────
                copy_sql = self._rewrite_sql_for_copy(
                    sql, schema, orig_table, copy_table)

                self.after(0, lambda s=copy_sql: self._sys(
                    f"[gear] Executing on workspace:\n   {s[:120]}..."
                    if len(s) > 120 else f"[gear] Executing on workspace:\n   {s}"))

                # ── Step 5: Execute — original table untouched ───────────────
                cur.execute(copy_sql)
                count = cur.rowcount
                ws_conn.commit()

                # ── Step 6: Notify + show workspace card ─────────────────────
                self.after(0, lambda n=count, ct=copy_table:
                           self._on_copy_exec_success(
                               sql, copy_sql, schema, orig_table, ct, n))

                if verify_sql.strip():
                    verify_on_copy = self._rewrite_sql_for_copy(
                        verify_sql, schema, orig_table, copy_table)
                    self.after(200, lambda v=verify_on_copy, n=count:
                               self._run_verify(v, n, sql))

        except Exception as e:
            # Roll back to last savepoint if possible, otherwise full rollback
            try:
                ws_conn = getattr(self, "_ws_conn", None)
                if ws_conn and not ws_conn.closed:
                    ws_conn.rollback()
            except Exception:
                pass
            err = str(e)
            self.after(0, lambda: self._sys(f"[x] Workspace Error: {err}"))
            self._ask_ai_about_error(sql, err)

    def _on_copy_exec_success(self, orig_sql: str, copy_sql: str,
                               schema: str, orig_table: str,
                               copy_table: str, affected: int) -> None:
        """Show success message + workspace action card in the chat."""
        self._sys(
            f"[ok] Executed successfully on workspace ({affected} row(s) affected).\n"
            f'   Workspace: "{schema}"."{copy_table}"\n'
            f"   [ok] الجدول الأصلي \"{orig_table}\" لم يُلمَس نهائياً."
        )

        # ── Build a persistent workspace card in the chat ────────────────
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=12, pady=4)

        card = tk.Frame(row, bg="#091420",
                        highlightbackground="#1f6feb", highlightthickness=2)
        card.pack(fill="x", padx=48)

        # Header
        card_hdr = tk.Frame(card, bg="#0d1117", padx=14, pady=8)
        card_hdr.pack(fill="x")
        tk.Label(card_hdr,
                 text="🔵  Workspace _tmp محدَّثة - التعديل طُبِّق على جدول العمل",
                 bg="#0d1117", fg="#58a6ff",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(card_hdr,
                 text=f'   Workspace : "{schema}"."{copy_table}"\n'
                      f'   Original  : "{schema}"."{orig_table}"  <- لم يُعدَّل',
                 bg="#0d1117", fg=TEXT_DIM,
                 font=FONT_MONO).pack(anchor="w", pady=(4, 0))
        tk.Label(card_hdr,
                 text="   يمكنك الاستمرار في تطبيق المزيد من الإجراءات على نفس الـ Workspace.",
                 bg="#0d1117", fg="#6e7681",
                 font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(2, 0))

        # Buttons
        btn_f = tk.Frame(card, bg="#091420", padx=14, pady=10)
        btn_f.pack(fill="x")

        _Btn(btn_f, "  [ok]  تطبيق على الأصلي / Apply to Original  ",
             cmd=lambda: self._apply_to_original(
                 orig_sql, schema, orig_table, copy_table),
             bg=ACCENT2, hov=ACCENT2, fg=BG,
             font=("Segoe UI", 10, "bold"), padx=12, pady=7
             ).pack(side="left", padx=(0, 8))

        _Btn(btn_f, "  [reload]  إعادة تعيين Workspace  ",
             cmd=lambda: self._reset_workspace(schema, orig_table, copy_table),
             bg="#21262d", hov=BTN_HOVER, fg=TEXT_DIM,
             padx=10, pady=7, font=FONT_SMALL
             ).pack(side="left")

        self._scroll_bottom()

        # ── Trigger Background Live Context Sync ──────────────────
        threading.Thread(
            target=self._sync_workspace_context, 
            args=(schema, copy_table), 
            daemon=True
        ).start()

    def _sync_workspace_context(self, schema: str, copy_table: str) -> None:
        """Background thread to sync live workspace schema changes back to the AI context."""
        try:
            from src.database.inspector import get_full_table_info
            # Fetch fresh columns and stats from the workspace
            info = get_full_table_info(self._dsn, copy_table, schema)
            
            if "columns" in info:
                # Update context with the live copy columns while keeping original table name
                self.context["columns"] = info["columns"]
                if "primary_keys" in info:
                    self.context["primary_keys"] = info["primary_keys"]
                
                # Rebuild System Prompt with the updated context
                self.after(0, self._build_system_prompt)
                self.after(0, lambda: self._sys("♻ تم تحديث سياق الذكاء الاصطناعي بآخر تغييرات الـ Workspace أوتوماتيكياً."))
        except Exception as e:
            self.after(0, lambda: self._sys(f"[!] تحذير: فشل مزامنة السياق المباشر: {e}"))

    def _undo_last_workspace_action(self) -> None:
        """Roll back the workspace to the previous SAVEPOINT — zero storage cost."""
        if not self._workspace_snapshots:
            return

        sp_name = self._workspace_snapshots.pop()

        if not self._workspace_snapshots and self._undo_btn:
            self._undo_btn.set_disabled()

        ws_conn = getattr(self, "_ws_conn", None)
        if ws_conn is None or ws_conn.closed:
            self.after(0, lambda: self._sys(
                "[x] لا يوجد اتصال Workspace نشط — لا يمكن التراجع."))
            return

        def execute_undo():
            try:
                with ws_conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                    cur.execute(f"RELEASE SAVEPOINT {sp_name};")
                ws_conn.commit()
                self.after(0, lambda: self._sys(
                    f"[back] تم التراجع بنجاح إلى نقطة الحفظ: {sp_name}"))
                schema, _, copy_table = self._get_safe_copy_name()
                self.after(0, lambda: self._sync_workspace_context(schema, copy_table))
            except Exception as e:
                self.after(0, lambda: self._sys(f"[x] فشل التراجع: {str(e)}"))

        self._sys("[back] جاري التراجع عن آخر إجراء…")
        threading.Thread(target=execute_undo, daemon=True).start()


    def _clear_workspace_snapshots(self) -> None:
        """Release all savepoints by closing the persistent workspace connection."""
        self._workspace_snapshots.clear()

        if self._undo_btn:
            self._undo_btn.set_disabled()

        ws_conn = getattr(self, "_ws_conn", None)
        if ws_conn and not ws_conn.closed:
            def _close():
                try:
                    ws_conn.rollback()   # cancel any uncommitted work
                    ws_conn.close()
                except Exception:
                    pass
            threading.Thread(target=_close, daemon=True).start()
            self._ws_conn = None



    def _apply_to_original(self, orig_sql: str, schema: str,
                            orig_table: str, copy_table: str) -> None:
        """Show a final confirmation then run the original SQL on the original table."""
        confirmed = messagebox.askyesno(
            "[!] تطبيق على الأصلي / Apply to Original",
            f"سيتم الآن تنفيذ التعديل على الجدول الأصلي:\n"
            f'  "{schema}"."{orig_table}"\n\n'
            f"هل أنت متأكد؟\n\n"
            f"This will modify the ORIGINAL table, not the copy.",
            parent=self,
        )
        if not confirmed:
            return

        self._sys(f'[wait] Applying to original: "{schema}"."{orig_table}"...')
        threading.Thread(
            target=self._run_sql_on_original,
            args=(orig_sql, schema, orig_table, copy_table),
            daemon=True
        ).start()

    def _run_sql_on_original(self, sql: str, schema: str,
                              orig_table: str, copy_table: str) -> None:
        """Execute original SQL on the original table + optionally drop copy."""
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=15,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    count = cur.rowcount
                    conn.commit()
                self.after(0, lambda n=count, ot=orig_table: self._sys(
                    f'[ok] Applied to original "{ot}" - {n} row(s) affected.\n'
                    f'   يمكنك حذف النسخة الآمنة إذا لم تعد تحتاجها.'))

                # Trigger schema refresh
                try:
                    self.on_apply({"__refresh__": True})
                except Exception:
                    pass

                self._ask_ai_to_verify(sql, count, None)

            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._sys(
                f"[x] Apply to Original failed: {err}"))

    def _reset_workspace(self, schema: str,
                          orig_table: str, copy_table: str) -> None:
        """
        Drop and recreate the workspace table from original structure.
        This gives the user a clean slate without touching the original.
        """
        confirmed = messagebox.askyesno(
            "[reload] إعادة تعيين Workspace",
            f'سيتم حذف وإعادة إنشاء جدول العمل:\n  "{schema}"."{copy_table}"\n\n'
            f'الجدول الأصلي "{orig_table}" لن يتأثر.\nمتأكد؟',
            parent=self,
        )
        if not confirmed:
            return

        def _do_reset():
            try:
                conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                        cursor_factory=pg_extras.RealDictCursor)
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(
                        f'DROP TABLE IF EXISTS "{schema}"."{copy_table}";')
                    cur.execute(
                        f'CREATE TABLE "{schema}"."{copy_table}" '
                        f'(LIKE "{schema}"."{orig_table}" INCLUDING ALL);'
                    )
                    conn.commit()
                conn.close()
                self.after(0, lambda: self._sys(
                    f'[reload] Workspace reset (structure only):\n'
                    f'   "{schema}"."{copy_table}" - fresh & clean.'))
            except Exception as e:
                self.after(0, lambda: self._sys(f"[x] Reset failed: {e}"))

        threading.Thread(target=_do_reset, daemon=True).start()

    def _run_verify(self, verify_sql: str, affected: int, orig_sql: str) -> None:
        """Run verification SELECT and feed result to AI for a report."""
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            with conn.cursor() as cur:
                cur.execute(verify_sql)
                rows = cur.fetchmany(10)
                cols = [d.name for d in cur.description] if cur.description else []
            conn.close()
            self.after(0, lambda r=rows, c=cols:
                       self._ask_ai_to_verify(orig_sql, affected, r))
        except Exception as e:
            self.after(0, lambda: self._ask_ai_to_verify(orig_sql, affected, None))

    def _ask_ai_to_verify(self, sql: str, affected: int,
                          verify_rows: list | None) -> None:
        """Send execution result to AI so it reports status and next steps."""
        if verify_rows is not None:
            rows_txt = json.dumps([
                {k: str(v) for k, v in r.items()} for r in verify_rows
            ], ensure_ascii=False)
        else:
            rows_txt = "(verification query not run)"

        feed = (
            f"[EXECUTION REPORT]\n"
            f"SQL executed: {sql[:200]}\n"
            f"Rows affected: {affected}\n"
            f"Verification result: {rows_txt}\n\n"
            "Please:\n"
            "1. Confirm whether the action succeeded based on the verification data.\n"
            "2. Show the BEFORE/AFTER state briefly.\n"
            "3. Suggest the next action or confirm the table is improved.\n"
            "4. If there is still a problem, suggest a fix with a new ```sql block."
        )
        self._messages.append({"role": "user", "content": feed})
        threading.Thread(target=self._stream, daemon=True).start()

    def _ask_ai_about_error(self, sql: str, err: str) -> None:
        """When SQL fails, Auto-Healing kicks in: ask AI to diagnose and suggest a fix visually."""
        feed = (
            f"[x] فشل تنفيذ الاستعلام الخاص بك (SQL Execution Failed):\n\n"
            f"```sql\n{sql[:500]}\n```\n\n"
            f"**Error Details:**\n{err}\n\n"
            "يرجى مراجعة الخطأ أعلاه بعناية وتقديم استعلام SQL مصحح داخل ` ```sql `، "
            "مع توفير قائمة ` ```choices ` تحتوي على إجراء SQL لتمكيني من تنفيذ الكود المصحح مجدداً."
        )
        self._messages.append({"role": "user", "content": feed})
        if self._history:
            self._history.add_message("user", feed)
            
        self.after(0, lambda f=feed: self._add_user_bubble(f))
        threading.Thread(target=self._stream, daemon=True).start()

    def _show_results(self, rows: list, cols: list[str]) -> None:
        if not rows:
            self._sys("[i] Query returned no rows.")
            return

        # ── Privacy: limit rows sent to AI for cloud models ──
        is_cloud    = "cloud" in self._model_var.get().lower()
        max_ai_rows = 5 if is_cloud else 20

        widths = {c: len(c) for c in cols}
        for row in rows:
            for c in cols:
                widths[c] = max(widths[c], len(str(row.get(c, "") or "")))

        lines = [" │ ".join(c.ljust(widths[c]) for c in cols),
                 "─┼─".join("─" * widths[c] for c in cols)]
        for row in rows:
            lines.append(" │ ".join(
                str(row.get(c, "") or "").ljust(widths[c]) for c in cols))

        result_text = (f"[chart] Result: {len(rows)} row(s)"
                       + (" (max 50)" if len(rows) == 50 else "")
                       + "\n" + "\n".join(lines))

        # Show as code block in a new bubble
        row_f = tk.Frame(self._msg_container, bg=PANEL_BG)
        row_f.pack(fill="x", padx=12, pady=4)
        tk.Label(row_f, text="[chart]", bg=PANEL_BG,
                 font=("Segoe UI Emoji", 15), padx=6
                 ).pack(side="left", anchor="n", pady=6)
        col_f = tk.Frame(row_f, bg=PANEL_BG)
        col_f.pack(side="left", fill="both", expand=True)
        bubble = tk.Frame(col_f, bg=AI_BUBBLE, padx=14, pady=10)
        bubble.pack(fill="x")
        self._render_code_block(bubble, "\n".join(lines), "result")
        self._scroll_bottom()

        # Feed limited rows back to AI
        ai_rows     = rows[:max_ai_rows]
        ai_lines    = lines[:max_ai_rows + 2]   # header + separator + rows
        ai_text     = "\n".join(ai_lines)
        privacy_note = (
            f"  [Only {max_ai_rows} of {len(rows)} rows shared - cloud privacy mode]"
            if is_cloud and len(rows) > max_ai_rows else ""
        )
        self._messages.append({
            "role":    "user",
            "content": f"[SQL Result - {len(rows)} row(s)]{privacy_note}\n{ai_text}\nBriefly interpret these results.",
        })
        threading.Thread(target=self._stream, daemon=True).start()

    # ── Apply / Copy / Export ──────────────────────────────────

    def _apply_last_response(self) -> None:
        msg = self._last_ai_msg
        if not msg:
            self._ctx_status.config(text="No AI response yet.", fg=WARNING)
            return
        patterns = [
            r"```json\s*(\{.*?\})\s*```",
            r"```\s*(\{.*?\})\s*```",
            r'(\{[^{}]*"target_table_name"[^{}]*\})',
            r'(\{[^{}]*"column_renames"[^{}]*\})',
        ]
        data = None
        for pat in patterns:
            m = re.search(pat, msg, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1)); break
                except json.JSONDecodeError:
                    continue

        if data is None:
            self._ctx_status.config(
                text="[!] No JSON found.\nAsk AI to output final JSON block.",
                fg=WARNING)
            return

        try:
            self.on_apply(data)
            n = (len(data.get("column_renames", {})) +
                 len(data.get("deselect_columns", [])) +
                 (1 if data.get("target_table_name") else 0))
            self._ctx_status.config(text=f"[ok] Applied {n} change(s).", fg=ACCENT2)
            self._sys(f"[ok] Changes applied to inspector ({n} updates).")
        except Exception as e:
            self._ctx_status.config(text=f"[x] {e}", fg=DANGER)

    def _copy_last(self) -> None:
        if not self._last_ai_msg:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_ai_msg)
        self._ctx_status.config(text="[clipboard] Copied.", fg=TEXT_DIM)

    def _export_chat(self) -> None:
        lines = []
        for m in self._messages:
            if m["role"] == "system":
                continue
            prefix = "أنت / You:" if m["role"] == "user" else "Ollama:"
            lines.append(f"\n{prefix}\n{m['content']}\n{'─' * 60}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self._ctx_status.config(text="[send] Exported to clipboard.", fg=TEXT_DIM)

    def _clear_chat(self) -> None:
        if not messagebox.askyesno("Clear?", "Reset conversation?", parent=self):
            return
        for w in self._msg_container.winfo_children():
            w.destroy()
        self._dynamic_labels.clear()
        self._last_ai_msg = ""
        self._pending_sql = ""
        self._stream_text_widget  = None
        self._stream_bubble_frame = None
        self._start_conversation()

    # ── New Chat (fresh session, history preserved) ───────────────────

    def _new_chat(self) -> None:
        """
        Start a new conversation session while preserving past history.
        The AI will still have access to previous sessions via the system prompt.
        """
        if not messagebox.askyesno(
            "[*] New Chat",
            "سيتم بدء جلسة جديدة.\n"
            "المحادثات السابقة محفوظة وسيظل الذكاء الاصطناعي على دراية بها.\n\n"
            "A new session will start. Previous messages are saved and the AI will still \n"
            "know the history of work done on this table.",
            parent=self,
        ):
            return

        # Save current session if it has messages
        if self._history:
            self._history.save()
            self._history.start_new_session()

        # Clear UI
        for w in self._msg_container.winfo_children():
            w.destroy()
        self._dynamic_labels.clear()
        self._last_ai_msg = ""
        self._pending_sql = ""
        self._stream_text_widget  = None
        self._stream_bubble_frame = None

        # Rebuild system prompt to include the newly finalized session in history
        self._build_system_prompt()
        self._messages = [{
            "role":    "system",
            "content": self._system_prompt,
        }]

        # Show "new session" banner
        sess_label = self._history.session_label if self._history else "New Session"
        self._sys(f"[*] {sess_label} started - جلسة جديدة (previous history loaded in context)")
        threading.Thread(target=self._stream, daemon=True).start()

    # ── Restore previous chat history in UI ───────────────────────

    def _restore_history_ui(self) -> None:
        """
        When the chat window opens for a table with saved history,
        show the last few messages and a banner at the top.
        """
        if not self._history or not self._history.has_history:
            return

        # ── Header banner ─────────────────────────────────
        banner_f = tk.Frame(self._msg_container, bg="#091420",
                            highlightbackground="#1f6feb", highlightthickness=1)
        banner_f.pack(fill="x", padx=8, pady=(4, 8))

        total    = self._history.total_sessions - 1   # exclude current
        updated  = self._history.updated_at
        sessions = self._history.all_session_labels[:-1]  # previous only
        sess_txt = "  |  ".join(sessions[-3:])  # last 3 labels

        tk.Label(banner_f,
                 text=f"📚  سجل المحادثات محمل  -  Chat History Loaded",
                 bg="#091420", fg="#58a6ff",
                 font=("Segoe UI", 9, "bold"),
                 padx=12, pady=6).pack(anchor="w")
        tk.Label(banner_f,
                 text=f"   📌  {total} session(s) | Last: {updated[:16]} | {sess_txt}",
                 bg="#091420", fg=TEXT_DIM,
                 font=FONT_SMALL, padx=12).pack(anchor="w")
        tk.Label(banner_f,
                 text="   الذكاء الاصطناعي يعرف بتاريخ عملك على هذا الجدول ويمكنه الإجابة بدقة أعلى.",
                 bg="#091420", fg=TEXT_DIM,
                 font=("Segoe UI", 8, "italic"),
                 padx=12,
                 wraplength=700).pack(anchor="w", pady=(0, 6))

        # ── Replay last N messages from all previous sessions ────
        all_sessions = self._history._data.get("sessions", [])
        prev_sessions = all_sessions[:-1]  # exclude current

        all_prev: list[dict] = []
        for sess in prev_sessions:
            for m in sess.get("messages", []):
                all_prev.append(m)

        REPLAY_COUNT = 6   # show last N messages as ghost bubbles
        tail = all_prev[-REPLAY_COUNT:] if len(all_prev) > REPLAY_COUNT else all_prev

        if tail:
            sep = tk.Frame(self._msg_container, bg="#1a1f2e", height=1)
            sep.pack(fill="x", padx=8, pady=4)
            tk.Label(self._msg_container,
                     text=f"[wait] آخر {len(tail)} رسائل من الجلسات السابقة  /  Last {len(tail)} messages from history:",
                     bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 8, "italic"),
                     padx=52).pack(anchor="w")

            for m in tail:
                role    = m.get("role", "user")
                content = m.get("content", "")[:400]   # truncate long messages
                ts      = m.get("timestamp", "")[:16]
                if len(m.get("content", "")) > 400:
                    content += "..."

                if role == "user":
                    self._add_user_bubble(f"📌 [prev] {content}")
                else:
                    # ghost AI bubble - non-interactive, dim
                    row = tk.Frame(self._msg_container, bg=PANEL_BG)
                    row.pack(fill="x", padx=12, pady=2)
                    tk.Label(row, text="[AI]",
                             bg=PANEL_BG, fg=TEXT_DIM,
                             font=("Segoe UI", 14)).pack(side="left", anchor="n", padx=(0, 6))
                    col = tk.Frame(row, bg=PANEL_BG)
                    col.pack(side="left", fill="x", expand=True)
                    tk.Label(col,
                             text=f"[{ts}] {content}",
                             bg="#161b24", fg=TEXT_DIM,
                             font=("Segoe UI", 8),
                             justify="left", wraplength=660,
                             padx=8, pady=4,
                             anchor="w").pack(anchor="w")

            sep2 = tk.Frame(self._msg_container, bg="#1a1f2e", height=1)
            sep2.pack(fill="x", padx=8, pady=(4, 8))
            tk.Label(self._msg_container,
                     text="↩  جلسة جديدة / New session starts here",
                     bg=PANEL_BG, fg=ACCENT2,
                     font=("Segoe UI", 8, "bold"),
                     padx=52).pack(anchor="w")

        # Scroll to bottom after restore
        self.after(100, self._scroll_bottom)


# ─── SQL Editor Window ─────────────────────────────────────────────────────

class _SqlEditorWindow(tk.Toplevel):
    """
    Standalone SQL editor with:
    - Syntax-highlighted editor (tk.Text)
    - Execute button (respects risk classification + safe-copy)
    - Results in ttk.Treeview
    - [AI] Ask AI - send SQL to the chat AI for review/improvement
    - [send] Feed to AI - inject results into the chat conversation
    """

    def __init__(self, chat_win: "OllamaChatWindow",
                 dsn: str, context: dict,
                 on_feed_ai: Callable[[str], None]) -> None:
        super().__init__(chat_win)
        self._chat  = chat_win
        self._dsn   = dsn
        self._ctx   = context
        self._feed  = on_feed_ai
        self._last_rows: list = []
        self._last_cols: list = []

        schema = context.get("src_schema", "public")
        table  = context.get("table_name", "")
        self.title(f"🛠 SQL Editor - {schema}.{table}")
        self.geometry("1000x620")
        self.minsize(700, 440)
        self.configure(bg=BG)
        self.resizable(True, True)

        self._build_ui()
        self._insert_default_sql()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Top accent
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")

        # Header
        hdr = tk.Frame(self, bg=HEADER_BG, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  🛠  SQL Editor",
                 bg=HEADER_BG, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="left", fill="y")
        tk.Button(hdr, text=" ✕ ", command=self.destroy,
                  bg=HEADER_BG, fg=TEXT_DIM,
                  activebackground=DANGER, activeforeground=TEXT,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 11)).pack(side="right")

        # Main paned layout: editor left, results right
        paned = tk.PanedWindow(self, orient="horizontal",
                               bg=BG, sashwidth=4,
                               sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # ── Left: Editor ───────────────────────────────────────────────
        left = tk.Frame(paned, bg=BG)
        paned.add(left, minsize=350)

        ed_hdr = tk.Frame(left, bg=HEADER_BG, padx=10, pady=6)
        ed_hdr.pack(fill="x")
        tk.Label(ed_hdr, text="✏  Editor",
                 bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side="left")

        # Risk indicator label
        self._risk_lbl = tk.Label(ed_hdr, text="READ",
                                  bg=HEADER_BG, fg=ACCENT2,
                                  font=FONT_SMALL)
        self._risk_lbl.pack(side="right")

        ed_wrap = tk.Frame(left, bg=CODE_BG,
                           highlightbackground=BORDER, highlightthickness=1)
        ed_wrap.pack(fill="both", expand=True, padx=8, pady=4)

        self._editor = tk.Text(
            ed_wrap, bg=CODE_BG, fg="#a5d6ff", font=FONT_MONO,
            relief="flat", wrap="none",
            insertbackground=TEXT, selectbackground="#264f78",
            undo=True, padx=12, pady=10,
        )
        ed_vsb = ttk.Scrollbar(ed_wrap, orient="vertical",
                                command=self._editor.yview)
        ed_hsb = ttk.Scrollbar(ed_wrap, orient="horizontal",
                                command=self._editor.xview)
        self._editor.config(yscrollcommand=ed_vsb.set,
                            xscrollcommand=ed_hsb.set)
        ed_vsb.pack(side="right", fill="y")
        ed_hsb.pack(side="bottom", fill="x")
        self._editor.pack(fill="both", expand=True)
        self._editor.bind("<KeyRelease>", self._on_key)

        # Editor toolbar
        ed_bar = tk.Frame(left, bg=HEADER_BG, padx=8, pady=6)
        ed_bar.pack(fill="x")

        self._exec_btn = tk.Button(
            ed_bar, text="  >  Execute  ",
            command=self._execute,
            bg=ACCENT2, fg=BG,
            activebackground=ACCENT, activeforeground=BG,
            relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI", 10, "bold"), padx=12, pady=6,
        )
        self._exec_btn.pack(side="left", padx=(0, 8))

        tk.Button(ed_bar, text="[AI] Ask AI",
                  command=self._ask_ai,
                  bg="#0d1f2d", fg=ACCENT,
                  activebackground="#1a2e40", activeforeground=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=6,
                  ).pack(side="left", padx=(0, 8))

        tk.Button(ed_bar, text="🗑 Clear",
                  command=lambda: (self._editor.delete("1.0", "end"),
                                   self._insert_default_sql()),
                  bg=ENTRY_BG, fg=TEXT_DIM,
                  activebackground=BTN_HOVER, activeforeground=TEXT,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9), padx=8, pady=6,
                  ).pack(side="left")

        # Status bar
        self._status = tk.Label(left, text="Ready",
                                bg=HEADER_BG, fg=TEXT_DIM,
                                font=FONT_SMALL, anchor="w", padx=10, pady=4)
        self._status.pack(fill="x")

        # ── Right: Results ─────────────────────────────────────────────
        right = tk.Frame(paned, bg=BG)
        paned.add(right, minsize=300)

        res_hdr = tk.Frame(right, bg=HEADER_BG, padx=10, pady=6)
        res_hdr.pack(fill="x")
        tk.Label(res_hdr, text="[chart]  Results",
                 bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side="left")
        self._res_count = tk.Label(res_hdr, text="",
                                   bg=HEADER_BG, fg=TEXT_DIM,
                                   font=FONT_SMALL)
        self._res_count.pack(side="right")

        # Treeview results
        tree_wrap = tk.Frame(right, bg=BG)
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=4)

        vsb = ttk.Scrollbar(tree_wrap, orient="vertical")
        hsb = ttk.Scrollbar(tree_wrap, orient="horizontal")
        self._tree = ttk.Treeview(
            tree_wrap,
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
            selectmode="extended", show="headings",
        )
        vsb.config(command=self._tree.yview)
        hsb.config(command=self._tree.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True)

        # Style
        style = ttk.Style()
        style.configure("Treeview",
                         background="#0d1117", foreground=TEXT,
                         fieldbackground="#0d1117", rowheight=22,
                         font=FONT_MONO)
        style.configure("Treeview.Heading",
                         background=HEADER_BG, foreground=ACCENT,
                         font=FONT_BOLD)

        # Result toolbar
        res_bar = tk.Frame(right, bg=HEADER_BG, padx=8, pady=6)
        res_bar.pack(fill="x")

        tk.Button(res_bar, text="[send] Feed to AI",
                  command=self._feed_results_to_ai,
                  bg="#0f2d1a", fg=ACCENT2,
                  activebackground="#1a4a2e", activeforeground=ACCENT2,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=5,
                  ).pack(side="left", padx=(0, 8))

        tk.Button(res_bar, text="[clipboard] Copy CSV",
                  command=self._copy_csv,
                  bg=ENTRY_BG, fg=TEXT_DIM,
                  activebackground=BTN_HOVER, activeforeground=TEXT,
                  relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9), padx=8, pady=5,
                  ).pack(side="left")

    # ── Logic ───────────────────────────────────────────────────────────────

    def _insert_default_sql(self) -> None:
        schema = self._ctx.get("src_schema", "public")
        table  = self._ctx.get("table_name", "")
        if table:
            self._editor.insert("1.0",
                f'SELECT *\nFROM "{schema}"."{table}"\nLIMIT 10;')
        self._update_risk_label()

    def _on_key(self, _=None) -> None:
        self._update_risk_label()

    def _update_risk_label(self) -> None:
        sql  = self._editor.get("1.0", "end-1c").strip()
        risk = _classify_sql(sql) if sql else RISK_READ
        color  = RISK_COLOR.get(risk, TEXT_DIM)
        label  = {"READ": "[green] READ", "WRITE": "[yellow] WRITE",
                  "DANGER": "[red] DANGER", "BLOCKED": "[blocked] BLOCKED"}.get(risk, risk)
        self._risk_lbl.config(text=label, fg=color)
        exec_col = {"READ": ACCENT2, "WRITE": WARNING,
                    "DANGER": DANGER, "BLOCKED": TEXT_DIM}.get(risk, ACCENT2)
        self._exec_btn.config(bg=exec_col)

    def _execute(self) -> None:
        sql   = self._editor.get("1.0", "end-1c").strip()
        if not sql:
            return
        risk  = _classify_sql(sql)

        if risk == RISK_BLOCKED:
            self._set_status("[blocked] Blocked - this operation is forbidden.", DANGER)
            return

        if risk in (RISK_WRITE, RISK_DANGER):
            # Delegate to the chat window's safe-copy approval flow
            self._chat.after(0, lambda: (
                setattr(self._chat, "_pending_sql",  sql),
                setattr(self._chat, "_pending_risk", risk),
                self._chat._show_sql_approval()
            ))
            return

        # READ - run directly
        self._set_status("[wait] Executing...", TEXT_DIM)
        threading.Thread(target=self._run_read, args=(sql,), daemon=True).start()

    def _run_read(self, sql: str) -> None:
        import time
        t0 = time.time()
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchmany(200)
                cols = [d.name for d in cur.description] if cur.description else []
                conn.rollback()
            conn.close()
            elapsed = time.time() - t0
            self.after(0, lambda r=rows, c=cols, e=elapsed:
                       self._show_results(r, c, e))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._set_status(f"[x] {err}", DANGER))

    def _show_results(self, rows: list, cols: list,
                      elapsed: float = 0.0) -> None:
        self._last_rows = rows
        self._last_cols = cols

        # Rebuild tree columns
        self._tree.delete(*self._tree.get_children())
        self._tree["columns"] = cols
        for c in cols:
            self._tree.heading(c, text=c)
            self._tree.column(c, width=max(80, min(200, len(c) * 10)),
                              stretch=True)

        for row in rows:
            vals = [str(row.get(c, "")) if row.get(c) is not None else "NULL"
                    for c in cols]
            self._tree.insert("", "end", values=vals)

        n = len(rows)
        self._res_count.config(
            text=f"{n} row{'s' if n != 1 else ''} | {elapsed:.2f}s")
        self._set_status(f"[ok] {n} rows returned in {elapsed:.2f}s", ACCENT2)

    def _set_status(self, msg: str, color: str = TEXT_DIM) -> None:
        self._status.config(text=msg, fg=color)

    def _ask_ai(self) -> None:
        """Send the editor SQL to the AI in the chat for review."""
        sql = self._editor.get("1.0", "end-1c").strip()
        if not sql:
            return
        prompt = (
            f"راجع هذا الـ SQL وحسّنه إن أمكن، واشرح ما يفعله:\n\n"
            f"```sql\n{sql}\n```"
        )
        self._feed(prompt)
        self._set_status("[AI] SQL sent to AI for review.", ACCENT)

    def _feed_results_to_ai(self) -> None:
        """Inject current query results into the chat conversation."""
        if not self._last_rows:
            self._set_status("[!] No results to feed. Run a query first.", WARNING)
            return
        sql     = self._editor.get("1.0", "end-1c").strip()[:100]
        summary = OllamaChatWindow._format_results_for_ai(
            self._last_rows, self._last_cols, label=sql)
        prompt  = (
            f"نتائج الاستعلام من SQL Editor:\n\n{summary}\n\n"
            f"حلّل هذه النتائج وأعطِ توصياتك."
        )
        self._feed(prompt)
        self._set_status("[send] Results fed to AI.", ACCENT2)

    def _copy_csv(self) -> None:
        if not self._last_rows or not self._last_cols:
            return
        lines = [",".join(self._last_cols)]
        for row in self._last_rows:
            lines.append(",".join(
                f'"{str(row.get(c,""))}"' for c in self._last_cols))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self._set_status("[clipboard] Copied as CSV.", TEXT_DIM)
