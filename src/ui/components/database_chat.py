"""
Database Chat Window
====================
A lightweight Ollama chat window for discussing an entire database
(multiple tables) — used from the main App toolbar.

Unlike OllamaChatWindow (single table, deep schema), this window:
  - Accepts a list of tables with basic stats (name, rows, size, schema)
  - Builds a high-level system prompt summarising the database
  - Streams responses with the same bubble UI
  - Supports SQL execution with the 4-level security system
  - Offers the action-card format (```actions blocks)
"""

from __future__ import annotations

import json
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable
from urllib import request
from urllib.error import URLError
import datetime

try:
    import psycopg2
    from psycopg2 import extras as pg_extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

try:
    from src.ai.sql_constitution import SQL_CONSTITUTION_GENERIC as _SQL_CONST
except ImportError:
    _SQL_CONST = ""

# Re-use all styling & helpers from ollama_chat
from src.ui.components.ollama_chat import (
    BG, PANEL_BG, HEADER_BG, ACCENT, ACCENT2, DANGER, WARNING,
    TEXT, TEXT_DIM, BORDER, ENTRY_BG, BTN_BG, BTN_HOVER,
    AI_BUBBLE, USER_BUBBLE, CODE_BG, SYS_TEXT,
    FONT_UI, FONT_BOLD, FONT_MONO, FONT_TITLE, FONT_SMALL, FONT_CHAT,
    RISK_COLOR, RISK_LABEL, RISK_BLOCKED, RISK_DANGER, RISK_WRITE, RISK_READ,
    OLLAMA_BASE_DEFAULT, PREDEFINED_MODELS,
    _Btn, _classify_sql, _clean_sql, _is_rtl,
)

OLLAMA_MODEL_DEFAULT = "glm-5:cloud"
INPUT_PLACEHOLDER    = "اسأل عن قواعد البيانات...  /  Ask about the databases..."


def _now_str() -> str:
    return datetime.datetime.now().strftime("%H:%M")


class DatabaseChatWindow(tk.Toplevel):
    """AI chat for an entire database or multi-database view."""

    def __init__(self,
                 master:  tk.Widget,
                 dsn:     str,
                 label:   str,
                 schema:  str,
                 tables:  list[dict]):
        super().__init__(master)
        self._dsn    = dsn
        self._label  = label
        self._schema = schema
        self._tables = tables

        # State
        self._messages:      list[dict] = []
        self._streaming:     bool       = False
        self._pending_sql:   str        = ""
        self._pending_risk:  str        = RISK_READ
        self._pending_verify: str       = ""
        self._last_ai_msg:   str        = ""
        self._base_url       = OLLAMA_BASE_DEFAULT
        self._model_var      = tk.StringVar(value=OLLAMA_MODEL_DEFAULT)
        self._url_var        = tk.StringVar(value=OLLAMA_BASE_DEFAULT)

        self._stream_text_widget:  tk.Text | None  = None
        self._stream_bubble_frame: tk.Frame | None = None
        self._dynamic_labels: list[tuple] = []

        self._init_window()
        self._build_system_prompt()
        self._build_ui()
        self._check_connection()

    # ── Window ────────────────────────────────────────────────

    def _init_window(self) -> None:
        self.title(f"🌐 Database AI Chat — {self._label}")
        self.geometry("1100x720")
        self.minsize(800, 500)
        self.configure(bg=BG)
        self.resizable(True, True)

    # ── System Prompt ─────────────────────────────────────────

    def _build_system_prompt(self) -> None:
        tables     = self._tables
        total_rows = sum(t.get("rows", 0) or 0 for t in tables)
        total_size = len(tables)
        schema     = self._schema

        lines = [
            "You are an expert PostgreSQL database architect and analyst.",
            "Respond in the same language the user writes in (Arabic or English).",
            "",
        ]

        # ── Inject SQL Constitution ────────────────────────────
        lines.append(_SQL_CONST)

        lines += [
            "=== FIRST RESPONSE FORMAT (MANDATORY — FOLLOW EXACTLY) ===",
            "Your FIRST response MUST be structured EXACTLY like this:",
            "",
            "## 🏦 تحليل قاعدة البيانات / Database Analysis",
            "[2-line greeting + summary of the database]",
            "",
            "### 📊 خريطة الجداول / Table Map",
            "| # | Table | Rows | Size | Health |",
            "| --- | --- | --- | --- | --- |",
            "[one row per table, sorted by rows desc, limited to top 20]",
            "[Health = ✅ Good / ⚠️ Has Issues / ❌ Critical]",
            "",
            "### 🔍 النتائج الرئيسية / Key Findings",
            "- List 3-5 concrete findings (empty tables, huge tables, missing patterns, etc.)",
            "",
            "### ⚡ الإجراءات المقترحة / Suggested Actions",
            "```actions",
            '[',
            '  {"title": "...",',
            '   "desc": "...",',
            '   "category": "Performance | Data Quality | Structure | Security | Cleanup",',
            '   "impact": "High | Medium | Low",',
            '   "sql": "FULL executable SQL, schema-qualified, no comments",',
            '   "verify_sql": "SELECT ... [proves it worked]",',
            '   "risk": "READ | WRITE | DANGER"}',
            ']',
            '```',
            "",
            "RULES FOR actions BLOCK:",
            "  - Include 4-6 high-value actions.",
            "  - category must be EXACTLY one of: Performance, Data Quality, Structure, Security, Cleanup",
            "  - impact must be EXACTLY one of: High, Medium, Low",
            "  - sql: clean, no comments, no placeholders, directly executable",
            "  - rank by impact (High first)",
            "",
            f"=== DATABASE: {self._label} ===",
            f"Schema  : {schema}",
            f"Tables  : {total_size}",
            f"Rows    : ~{total_rows:,}",
            "",
            "TABLE LIST (name | rows | size | schema):",
        ]

        for t in sorted(tables, key=lambda x: x.get("rows", 0) or 0, reverse=True)[:80]:
            name    = t.get("table_name", t.get("name", "?"))
            rows    = t.get("rows", 0) or 0
            size    = t.get("size_pretty", t.get("total_size", "?"))
            tschema = t.get("schema", t.get("table_schema", self._schema))
            db_tag  = f" [{t['_db']}]" if "_db" in t else ""
            health  = "✅" if rows > 0 else "⚠️"
            lines.append(f"  {health} {tschema}.{name:<30} {rows:>12,} rows   {size}{db_tag}")

        lines += [
            "",
            "Focus your analysis on real patterns visible in the data above.",
            "Be specific: name actual tables, exact row counts, real issues found.",
        ]
        self._system_prompt = "\n".join(lines)

    # ── UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_title_bar()
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_settings_bar()
        ttk.Separator(self, orient="horizontal").pack(fill="x")

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)

        chat_col = tk.Frame(main, bg=BG)
        chat_col.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        self._build_chat_area(chat_col)

        side = tk.Frame(main, bg=PANEL_BG, width=240,
                        highlightbackground=BORDER, highlightthickness=1)
        side.pack(side="left", fill="y", padx=(0, 8), pady=8)
        side.pack_propagate(False)
        self._build_side_panel(side)

        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self._build_input_bar()

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, height=50)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=ACCENT, width=4).pack(side="left", fill="y")
        lf = tk.Frame(bar, bg=HEADER_BG, padx=12)
        lf.pack(side="left", fill="y", pady=6)
        tk.Label(lf, text=f"🌐  Database AI Chat",
                 bg=HEADER_BG, fg=TEXT, font=FONT_TITLE).pack(anchor="w")
        tk.Label(lf, text=self._label,
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_MONO).pack(anchor="w")
        self._conn_lbl = tk.Label(bar, text="⏳ Connecting...",
                                   bg=HEADER_BG, fg=WARNING, font=FONT_MONO, padx=12)
        self._conn_lbl.pack(side="right", fill="y")

    def _build_settings_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        left = tk.Frame(bar, bg=HEADER_BG)
        left.pack(side="left", fill="y", padx=8)
        tk.Label(left, text="Model:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left", fill="y", padx=(0, 4))
        self._model_combo = ttk.Combobox(
            left, textvariable=self._model_var,
            values=PREDEFINED_MODELS, width=26, font=FONT_SMALL, state="normal")
        self._model_combo.pack(side="left", pady=7)
        tk.Label(bar, text="URL:", bg=HEADER_BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="left", fill="y")
        tk.Entry(bar, textvariable=self._url_var,
                 bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=FONT_SMALL, width=24, bd=1,
                 ).pack(side="left", padx=(4, 6), pady=7)
        self._url_var.trace_add("write",
            lambda *_: setattr(self, "_base_url", self._url_var.get().strip()))
        _Btn(bar, "🗑 Clear", cmd=self._clear_chat,
             bg=HEADER_BG, hov=BTN_HOVER, fg=DANGER,
             padx=8, pady=3, font=FONT_SMALL).pack(side="right", padx=8)

    def _build_chat_area(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=PANEL_BG,
                        highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(wrap, orient="vertical")
        vsb.pack(side="right", fill="y")
        self._chat_canvas = tk.Canvas(
            wrap, bg=PANEL_BG, highlightthickness=0, yscrollcommand=vsb.set)
        self._chat_canvas.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._chat_canvas.yview)
        self._msg_container = tk.Frame(self._chat_canvas, bg=PANEL_BG)
        self._canvas_win = self._chat_canvas.create_window(
            (0, 0), window=self._msg_container, anchor="nw")
        self._msg_container.bind("<Configure>", lambda _: self._chat_canvas.configure(
            scrollregion=self._chat_canvas.bbox("all")))
        self._chat_canvas.bind("<Configure>", self._on_canvas_resize)
        for w in (self._chat_canvas, self._msg_container):
            w.bind("<MouseWheel>",
                   lambda e: self._chat_canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _on_canvas_resize(self, event) -> None:
        w = event.width
        self._chat_canvas.itemconfig(self._canvas_win, width=w)
        avail = max(260, w - 80)
        for lbl, role in self._dynamic_labels:
            try:
                lbl.config(wraplength=int(avail * (0.65 if role == "user" else 0.80)))
            except tk.TclError:
                pass

    def _bind_scroll(self, widget: tk.Widget) -> None:
        """Recursively bind MouseWheel on widget and all children to the chat canvas."""
        def _scroll(e):
            self._chat_canvas.yview_scroll(-1 * (e.delta // 120), "units")
        widget.bind("<MouseWheel>", _scroll, add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _build_side_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="  DATABASE INFO",
                 bg=HEADER_BG, fg=TEXT_DIM, font=FONT_BOLD,
                 height=2).pack(fill="x")
        body = tk.Frame(parent, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=10, pady=8)
        total_rows = sum(t.get("rows", 0) or 0 for t in self._tables)

        def _row(lbl, val, fg=TEXT):
            fr = tk.Frame(body, bg=PANEL_BG); fr.pack(fill="x", pady=1)
            tk.Label(fr, text=lbl, bg=PANEL_BG, fg=TEXT_DIM,
                     font=FONT_SMALL, width=11, anchor="w").pack(side="left")
            tk.Label(fr, text=val, bg=PANEL_BG, fg=fg,
                     font=FONT_MONO, anchor="w").pack(side="left")

        _row("Label:", self._label[:22])
        _row("Schema:", self._schema)
        _row("Tables:", str(len(self._tables)), ACCENT2)
        _row("Total rows:", f"{total_rows:,}")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=8)

        _Btn(body, "📋 Copy Last Msg", cmd=self._copy_last,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=8, pady=6, font=FONT_SMALL).pack(fill="x", pady=(0, 4))

        self._sql_btn = _Btn(body, "⚡ Execute Pending SQL",
                              cmd=self._show_sql_approval,
                              bg="#1a1210", hov="#2e1a10", fg=WARNING,
                              font=FONT_BOLD, padx=8, pady=8)
        self._sql_btn.pack(fill="x")
        self._sql_btn._lbl.config(state="disabled", cursor="arrow", fg=TEXT_DIM)

        self._ctx_status = tk.Label(
            body, text="", bg=PANEL_BG, fg=TEXT_DIM,
            font=FONT_SMALL, wraplength=200, justify="left")
        self._ctx_status.pack(fill="x", pady=(8, 0))

    def _build_input_bar(self) -> None:
        outer = tk.Frame(self, bg=HEADER_BG, height=86)
        outer.pack(fill="x", side="bottom")
        outer.pack_propagate(False)
        tk.Label(outer, text="Enter ← إرسال  |  Shift+Enter ← سطر جديد",
                 bg=HEADER_BG, fg=TEXT_DIM, font=("Segoe UI", 8), padx=14).pack(anchor="w")
        inner = tk.Frame(outer, bg=HEADER_BG)
        inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._inp_frame = tk.Frame(inner, bg=ENTRY_BG,
                                    highlightbackground=BORDER, highlightthickness=1)
        self._inp_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._input = tk.Text(
            self._inp_frame, bg=ENTRY_BG, fg=TEXT_DIM, insertbackground=TEXT,
            relief="flat", font=FONT_CHAT, height=3, wrap="word", padx=12, pady=10)
        self._input.insert("1.0", INPUT_PLACEHOLDER)
        self._input.pack(fill="both", expand=True)
        self._placeholder_active = True
        self._input.bind("<FocusIn>",  self._on_focus_in)
        self._input.bind("<FocusOut>", self._on_focus_out)
        self._input.bind("<Return>",   self._on_enter)
        bc = tk.Frame(inner, bg=HEADER_BG)
        bc.pack(side="right", fill="y")
        self._send_btn = _Btn(bc, "📤 إرسال / Send",
                               cmd=self._send, bg="#0f2d1a", hov="#1a4a2e",
                               fg=ACCENT2, font=FONT_BOLD, padx=12, pady=10)
        self._send_btn.pack(fill="x", pady=(0, 4))
        _Btn(bc, "⏹ Stop", cmd=lambda: setattr(self, "_streaming", False),
             bg="#2a1010", hov="#3d1111", fg=DANGER, padx=12, pady=6,
             font=FONT_SMALL).pack(fill="x")

    def _on_focus_in(self, _) -> None:
        if self._placeholder_active:
            self._input.delete("1.0", "end")
            self._input.config(fg=TEXT)
            self._placeholder_active = False
        self._inp_frame.config(highlightbackground=ACCENT)

    def _on_focus_out(self, _) -> None:
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

    # ── Connection ─────────────────────────────────────────────

    def _check_connection(self) -> None:
        threading.Thread(target=self._bg_connect, daemon=True).start()

    def _bg_connect(self) -> None:
        try:
            req = request.Request(f"{self._base_url}/api/tags", method="GET")
            with request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            fetched = [m["name"] for m in data.get("models", [])]
            self.after(0, lambda: self._on_connected(fetched))
        except Exception as e:
            self.after(0, lambda: self._on_connect_failed(str(e)))

    def _on_connected(self, fetched: list[str]) -> None:
        self._conn_lbl.config(
            text=f"🟢 Ollama ({len(fetched)} local + {len(PREDEFINED_MODELS)} catalogue)",
            fg=ACCENT2)
        merged = list(dict.fromkeys(fetched + PREDEFINED_MODELS))
        self._model_combo["values"] = merged
        self._start_conversation()

    def _on_connect_failed(self, err: str) -> None:
        self._conn_lbl.config(text="🔴 Ollama not reachable", fg=DANGER)
        self._sys(f"⚠️ Cannot reach Ollama:\n  {err}\n\nRun: ollama serve")

    # ── Conversation ──────────────────────────────────────────

    def _start_conversation(self) -> None:
        self._messages = [{"role": "system", "content": self._system_prompt}]
        self._messages.append({
            "role": "user",
            "content": (
                "افحص قاعدة البيانات وابدأ بالتحليل. اتبع تنسيق الرد الأولي المطلوب بالضبط "
                "(ترحيب + ملخص + ```actions block مع التوصيات المقترحة)."
            ),
        })
        self._sys("✓ متصل / Connected — جاري تحليل قاعدة البيانات...")
        threading.Thread(target=self._stream, daemon=True).start()

    def _send(self) -> None:
        if self._streaming or self._placeholder_active:
            return
        text = self._input.get("1.0", "end-1c").strip()
        if not text:
            return
        self._input.delete("1.0", "end")
        self._on_focus_out(None)
        self._messages.append({"role": "user", "content": text})
        self._add_user_bubble(text)
        threading.Thread(target=self._stream, daemon=True).start()

    # ── Streaming ─────────────────────────────────────────────

    def _stream(self) -> None:
        self._streaming = True
        self.after(0, lambda: self._send_btn.set_text("⏳ يفكر..."))
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
            self.after(0, lambda r=reason: self._sys(f"❌ Ollama error: {r}"))
        except Exception as e:
            self.after(0, lambda: self._sys(f"❌ Error: {e}"))
        finally:
            if full:
                self._messages.append({"role": "assistant", "content": full})
                self._last_ai_msg = full
                self.after(0, lambda: self._finalize_ai_bubble(full))
                self.after(0, lambda: self._post_process(full))
            self._streaming = False
            self.after(0, lambda: self._send_btn.set_text("📤 إرسال / Send"))
            self.after(0, lambda: self._send_btn.set_fg(ACCENT2))

    def _post_process(self, text: str) -> None:
        sql_blocks = re.findall(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if sql_blocks:
            sql  = _clean_sql(sql_blocks[-1].strip())
            risk = _classify_sql(sql)
            self._pending_sql  = sql
            self._pending_risk = risk
            self._sql_btn._lbl.config(
                state="normal", cursor="hand2",
                text=f"⚡ Execute SQL [{RISK_LABEL[risk]}]",
                fg=RISK_COLOR[risk])
            self._sql_btn._cmd = self._show_sql_approval
        else:
            self._sql_btn._lbl.config(state="disabled", cursor="arrow",
                                       text="⚡ Execute Pending SQL", fg=TEXT_DIM)
            self._pending_sql = ""

    # ── Chat Display ──────────────────────────────────────────

    def _sys(self, msg: str) -> None:
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=20, pady=3)
        tk.Label(row, text=msg, bg=PANEL_BG, fg=SYS_TEXT,
                 font=("Segoe UI", 8), justify="center", wraplength=600).pack()
        self._scroll_bottom()

    def _add_user_bubble(self, text: str) -> None:
        is_rtl = _is_rtl(text)
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=12, pady=6)
        tk.Frame(row, bg=PANEL_BG).pack(side="left", fill="x", expand=True)
        col = tk.Frame(row, bg=PANEL_BG)
        col.pack(side="left")
        tk.Label(col, text=_now_str(), bg=PANEL_BG, fg=SYS_TEXT,
                 font=("Segoe UI", 8)).pack(anchor="e", padx=4)
        bubble = tk.Frame(col, bg=USER_BUBBLE, padx=14, pady=10)
        bubble.pack(anchor="e")
        lbl = tk.Label(bubble, text=text, bg=USER_BUBBLE, fg="#d0e8ff",
                       font=FONT_CHAT, justify="right" if is_rtl else "left",
                       wraplength=460, anchor="e" if is_rtl else "w")
        lbl.pack(fill="x")
        self._dynamic_labels.append((lbl, "user"))
        tk.Label(row, text="👤", bg=PANEL_BG,
                 font=("Segoe UI Emoji", 15), padx=6).pack(side="left", anchor="s", pady=6)
        self._scroll_bottom()

    def _begin_ai_bubble(self) -> None:
        row = tk.Frame(self._msg_container, bg=PANEL_BG)
        row.pack(fill="x", padx=12, pady=6)
        tk.Label(row, text="🤖", bg=PANEL_BG,
                 font=("Segoe UI Emoji", 15), padx=6).pack(side="left", anchor="n", pady=6)
        col = tk.Frame(row, bg=PANEL_BG)
        col.pack(side="left", fill="both", expand=True)
        hdr = tk.Frame(col, bg=PANEL_BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Ollama", bg=PANEL_BG, fg=ACCENT2,
                 font=FONT_BOLD).pack(side="left")
        tk.Label(hdr, text=f"  {_now_str()}", bg=PANEL_BG, fg=SYS_TEXT,
                 font=("Segoe UI", 8)).pack(side="left")
        bubble = tk.Frame(col, bg=AI_BUBBLE, padx=14, pady=10)
        bubble.pack(fill="x", anchor="w")
        txt = tk.Text(bubble, bg=AI_BUBBLE, fg=TEXT, font=FONT_CHAT,
                      relief="flat", wrap="word", state="disabled",
                      cursor="arrow", height=1, padx=0, pady=2)
        txt.pack(fill="x", expand=True)
        txt.bind("<MouseWheel>", lambda e: self._chat_canvas.yview_scroll(
            -1*(e.delta//120), "units"))
        self._stream_text_widget  = txt
        self._stream_bubble_frame = bubble
        self._scroll_bottom()

    def _append_stream_token(self, token: str) -> None:
        w = self._stream_text_widget
        if not w:
            return
        try:
            w.config(state="normal")
            w.insert("end", token)
            w.config(height=max(1, int(w.index("end-1c").split(".")[0])), state="disabled")
        except tk.TclError:
            pass
        self._scroll_bottom()

    def _finalize_ai_bubble(self, full_text: str) -> None:
        bubble = self._stream_bubble_frame
        txt    = self._stream_text_widget
        if not bubble or not txt:
            return
        try:
            txt.pack_forget(); txt.destroy()
        except tk.TclError:
            pass
        self._stream_text_widget  = None
        self._stream_bubble_frame = None
        self._render_in_bubble(bubble, full_text)
        # Bind scroll on every new widget so user can scroll from anywhere
        try:
            self._bind_scroll(bubble)
        except Exception:
            pass
        self._scroll_bottom()

    def _render_in_bubble(self, bubble: tk.Frame, text: str) -> None:
        parts = re.split(r"(```(?:\w*)\n?.*?```)", text, flags=re.DOTALL)
        for part in parts:
            m = re.match(r"```(\w*)\n?(.*?)```", part, re.DOTALL)
            if m:
                lang = m.group(1).strip().lower() or "sql"
                code = m.group(2).strip()
                if lang == "actions":
                    self._render_action_cards(bubble, code)
                else:
                    self._render_code_block(bubble, code, lang)
            else:
                s = part.strip()
                if not s:
                    continue
                is_rtl = _is_rtl(s)
                lbl = tk.Label(bubble, text=s, bg=AI_BUBBLE, fg=TEXT, font=FONT_CHAT,
                               justify="right" if is_rtl else "left",
                               anchor="e" if is_rtl else "w", wraplength=560)
                lbl.pack(fill="x", anchor="w", pady=(2, 0))
                self._dynamic_labels.append((lbl, "ai"))

    def _render_action_cards(self, parent: tk.Frame, json_str: str) -> None:
        try:
            actions = json.loads(json_str)
            if not isinstance(actions, list):
                raise ValueError
        except Exception:
            self._render_code_block(parent, json_str, "json")
            return

        # ── Section header ──────────────────────────────────────
        hdr_row = tk.Frame(parent, bg=AI_BUBBLE)
        hdr_row.pack(fill="x", pady=(10, 2))
        tk.Label(hdr_row,
                 text="⚡  الإجراءات المقترحة  /  Suggested Actions",
                 bg=AI_BUBBLE, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(hdr_row, text=f"{len(actions)} action{'s' if len(actions)!=1 else ''}",
                 bg=AI_BUBBLE, fg=TEXT_DIM, font=FONT_SMALL).pack(side="right")
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(0, 6))

        # Group by category
        cats:  dict[str, list] = {}
        for a in actions:
            c = a.get("category", "General")
            cats.setdefault(c, []).append(a)

        CAT_ICON = {
            "Performance":   ("⚡", "#f0c040"),
            "Data Quality":  ("🔍", "#7dcfff"),
            "Structure":     ("🏗",  "#a8d8ea"),
            "Security":      ("🔒", "#ff7b7b"),
            "Cleanup":       ("🧹", "#9ece6a"),
            "General":       ("📋", TEXT_DIM),
        }

        global_idx = 0
        for cat, cat_actions in cats.items():
            icon, cat_color = CAT_ICON.get(cat, ("•", TEXT_DIM))

            # Category label
            cat_hdr = tk.Frame(parent, bg=AI_BUBBLE)
            cat_hdr.pack(fill="x", pady=(4, 2))
            tk.Label(cat_hdr, text=f"{icon}  {cat}",
                     bg=AI_BUBBLE, fg=cat_color,
                     font=("Segoe UI", 9, "bold")).pack(side="left")

            for action in cat_actions:
                global_idx += 1
                self._render_single_action(parent, action, global_idx, cat_color)

    def _render_single_action(self, parent: tk.Frame, action: dict,
                               idx: int, accent: str = ACCENT) -> None:
        risk    = action.get("risk", "WRITE").upper()
        if risk not in RISK_COLOR:
            risk = "WRITE"
        color   = RISK_COLOR[risk]
        title   = action.get("title", f"Action {idx}")
        desc    = action.get("desc", "")
        impact  = action.get("impact", "")
        sql     = _clean_sql(action.get("sql", ""))
        verify  = action.get("verify_sql", "")

        # Impact badge colors
        IMPACT_COLOR = {"High": DANGER, "Medium": WARNING, "Low": ACCENT2}
        imp_color = IMPACT_COLOR.get(impact, TEXT_DIM)

        # ── Card shell ─────────────────────────────────────────
        card = tk.Frame(parent, bg="#12192a",
                        highlightbackground=color, highlightthickness=1)
        card.pack(fill="x", pady=(0, 6))

        # ── Top bar: number + risk + impact + title ────────────
        top = tk.Frame(card, bg="#0b1120")
        top.pack(fill="x")

        # Numbered circle badge
        tk.Label(top, text=f"  {idx:02}  ",
                 bg=color, fg="#0a0a12",
                 font=("Consolas", 9, "bold")).pack(side="left")

        tk.Label(top, text=f"  {title}",
                 bg="#0b1120", fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left", fill="y", pady=4)

        # Risk pill — use HEADER_BG as opaque background instead of alpha
        tk.Label(top, text=f"  {RISK_LABEL[risk]}  ",
                 bg=HEADER_BG, fg=color,
                 font=("Consolas", 8),
                 padx=4, pady=2).pack(side="right", padx=4, pady=5)

        # Impact pill
        if impact:
            tk.Label(top, text=f" {impact} ",
                     bg=HEADER_BG, fg=imp_color,
                     font=("Consolas", 8),
                     padx=4, pady=2).pack(side="right", pady=5)

        # ── Description ────────────────────────────────────────
        if desc:
            tk.Label(card, text=desc,
                     bg="#12192a", fg=TEXT_DIM,
                     font=FONT_SMALL,
                     justify="right" if _is_rtl(desc) else "left",
                     wraplength=600, padx=12, pady=4).pack(fill="x", anchor="w")

        # ── SQL preview (collapsible) ───────────────────────────
        if sql:
            preview = sql[:100] + ("…" if len(sql) > 100 else "")
            sql_lbl = tk.Label(card, text=f"  {preview}",
                               bg="#0d1525", fg="#6a9fb5",
                               font=("Consolas", 8),
                               anchor="w", padx=8, pady=4)
            sql_lbl.pack(fill="x")

        # ── Divider ────────────────────────────────────────────
        tk.Frame(card, bg="#1e2d45", height=1).pack(fill="x")

        # ── Action buttons ─────────────────────────────────────
        btn_row = tk.Frame(card, bg="#0b1120", padx=8, pady=6)
        btn_row.pack(fill="x")

        tk.Button(
            btn_row,
            text="  ▶  نفّذ / Execute  ",
            command=lambda s=sql, v=verify, r=risk: self._trigger_execute(s, v, r),
            bg=color, fg="#060c14",
            activebackground=color, activeforeground="#060c14",
            font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2",
            padx=12, pady=5, bd=0,
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            btn_row,
            text=" 👁 معاينة ",
            command=lambda s=sql: self._preview_sql(s),
            bg="#1a2840", fg=TEXT_DIM,
            activebackground="#243554", activeforeground=TEXT,
            font=("Consolas", 8),
            relief="flat", cursor="hand2",
            padx=8, pady=5, bd=0,
        ).pack(side="left")

    def _render_code_block(self, parent: tk.Frame, code: str, lang: str = "sql") -> None:
        wrap = tk.Frame(parent, bg=CODE_BG, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill="x", pady=(6, 4))
        badge = tk.Frame(wrap, bg="#0d1117", padx=10, pady=3)
        badge.pack(fill="x")
        tk.Label(badge, text=lang.upper() or "SQL",
                 bg="#0d1117", fg=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        est_h = min(max(3, code.count("\n") + 2), 25)
        txt = tk.Text(wrap, bg=CODE_BG, fg="#a5d6ff", font=FONT_MONO,
                      relief="flat", wrap="none", state="normal",
                      cursor="arrow", height=est_h, padx=12, pady=8)
        txt.insert("1.0", code)
        txt.config(state="disabled")
        txt.pack(fill="x")
        for w in (wrap, txt):
            w.bind("<MouseWheel>", lambda e: self._chat_canvas.yview_scroll(
                -1*(e.delta//120), "units"))

    def _scroll_bottom(self) -> None:
        self._chat_canvas.after(30, lambda: self._chat_canvas.yview_moveto(1.0))

    # ── SQL Execution ──────────────────────────────────────────

    def _trigger_execute(self, sql: str, verify_sql: str, risk: str) -> None:
        self._pending_sql    = sql
        self._pending_risk   = risk
        self._pending_verify = verify_sql
        self._show_sql_approval()

    def _preview_sql(self, sql: str) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("👁 SQL Preview"); dlg.geometry("680x350")
        dlg.configure(bg=BG); dlg.grab_set()
        txt = tk.Text(dlg, bg=CODE_BG, fg="#a5d6ff", font=FONT_MONO,
                      relief="flat", padx=12, pady=10, wrap="none")
        txt.insert("1.0", sql)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        _Btn(dlg, "✕ Close", cmd=dlg.destroy, bg=ENTRY_BG, hov=BTN_HOVER,
             fg=TEXT_DIM, padx=12, pady=6).pack(pady=(0, 8))

    def _show_sql_approval(self) -> None:
        if not self._pending_sql:
            return
        sql  = self._pending_sql
        risk = self._pending_risk
        if risk == RISK_BLOCKED:
            messagebox.showerror("⛔ Blocked",
                                 f"This SQL is blocked:\n\n{sql[:200]}", parent=self)
            return
        if not _PSYCOPG2_AVAILABLE:
            messagebox.showerror("Missing Dependency",
                                 "psycopg2 required. pip install psycopg2-binary", parent=self)
            return
        if not self._dsn:
            messagebox.showerror("No DSN", "No database connection available.", parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("⚡ SQL Approval")
        dlg.geometry("720x420")
        dlg.configure(bg=BG)
        dlg.grab_set()
        dlg.resizable(False, False)
        color = RISK_COLOR[risk]

        # ── Top risk stripe ──
        tk.Frame(dlg, bg=color, height=4).pack(fill="x")

        # ── Header ──
        hdr = tk.Frame(dlg, bg=HEADER_BG, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"  ⚡  {RISK_LABEL[risk]}  —  SQL Approval",
                 bg=HEADER_BG, fg=color, font=FONT_BOLD, padx=12).pack(side="left", fill="y")

        # ── Bottom area — packed BEFORE expand=True so it's never hidden ──────
        bottom = tk.Frame(dlg, bg=BG, padx=14, pady=10)
        bottom.pack(fill="x", side="bottom")

        # Warning label for destructive ops
        if risk == RISK_DANGER:
            warn_frame = tk.Frame(bottom, bg="#1a0a0a",
                                  highlightbackground=DANGER, highlightthickness=1,
                                  padx=12, pady=8)
            warn_frame.pack(fill="x", pady=(0, 8))
            tk.Label(warn_frame,
                     text="🔴  تحذير: هذا الأمر خطير ولا يمكن التراجع عنه.\n"
                          "     This is a destructive, irreversible operation.",
                     bg="#1a0a0a", fg=DANGER, font=FONT_SMALL,
                     justify="left", wraplength=660).pack(anchor="w")
        elif risk == RISK_WRITE:
            warn_frame = tk.Frame(bottom, bg="#1a1400",
                                  highlightbackground=WARNING, highlightthickness=1,
                                  padx=12, pady=8)
            warn_frame.pack(fill="x", pady=(0, 8))
            tk.Label(warn_frame,
                     text="🟡  سيتم تعديل البيانات فور الضغط على نفّذ.\n"
                          "     Data will be modified immediately upon clicking Execute.",
                     bg="#1a1400", fg=WARNING, font=FONT_SMALL,
                     justify="left", wraplength=660).pack(anchor="w")

        btn_row = tk.Frame(bottom, bg=BG)
        btn_row.pack(fill="x")

        def _do():
            dlg.destroy()
            threading.Thread(target=self._run_sql, args=(sql, risk), daemon=True).start()

        tk.Button(
            btn_row,
            text="  \u25b6  نفّذ الآن / Execute Now  ",
            command=_do,
            bg=color, fg=BG,
            activebackground=color, activeforeground=BG,
            font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2",
            padx=16, pady=10, bd=0,
        ).pack(side="left", padx=(0, 10))

        _Btn(btn_row, "✕ إلغاء / Cancel", cmd=dlg.destroy,
             bg=ENTRY_BG, hov=BTN_HOVER, fg=TEXT_DIM,
             padx=14, pady=10).pack(side="left")


        # ── SQL Preview — packed LAST so expand=True doesn't push buttons off ──
        prev = tk.Frame(dlg, bg=ENTRY_BG, highlightbackground=color, highlightthickness=2)
        prev.pack(fill="both", expand=True, padx=14, pady=(10, 6))
        sql_txt = tk.Text(prev, bg=ENTRY_BG, fg="#a5d6ff", font=FONT_MONO,
                          relief="flat", padx=10, pady=10, wrap="none")
        sql_txt.insert("1.0", sql)
        sql_txt.config(state="disabled")
        h_sb = ttk.Scrollbar(prev, orient="horizontal", command=sql_txt.xview)
        sql_txt.config(xscrollcommand=h_sb.set)
        sql_txt.pack(fill="both", expand=True)
        h_sb.pack(fill="x")

    def _run_sql(self, sql: str, risk: str) -> None:
        verify_sql = self._pending_verify
        self.after(0, lambda: self._sys(f"⏳ Executing [{RISK_LABEL[risk]}]..."))
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    if risk == RISK_READ:
                        rows = cur.fetchmany(50)
                        cols = [d.name for d in cur.description] if cur.description else []
                        conn.rollback()
                        self.after(0, lambda r=rows, c=cols: self._sys(
                            f"📊 {len(r)} row(s) returned."))
                        # Send results to AI for analysis
                        self.after(100, lambda r=rows, s=sql: self._ask_ai_verify(
                            s, len(r), r))
                    else:
                        count = cur.rowcount
                        conn.commit()
                        self.after(0, lambda n=count: self._sys(
                            f"✅ SQL executed — {n} row(s) affected."))
                        if verify_sql.strip():
                            self.after(200, lambda v=verify_sql, n=count:
                                       self._run_verify(v, n, sql))
                        else:
                            self.after(100, lambda n=count:
                                       self._ask_ai_verify(sql, n, None))
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._sys(f"❌ SQL Error: {err}"))
            self.after(100, lambda: self._ask_ai_error(sql, err))

    def _run_verify(self, verify_sql: str, affected: int, orig_sql: str) -> None:
        try:
            conn = psycopg2.connect(self._dsn, connect_timeout=10,
                                    cursor_factory=pg_extras.RealDictCursor)
            with conn.cursor() as cur:
                cur.execute(verify_sql)
                rows = cur.fetchmany(10)
            conn.close()
            self.after(0, lambda r=rows: self._ask_ai_verify(orig_sql, affected, r))
        except Exception:
            self.after(0, lambda: self._ask_ai_verify(orig_sql, affected, None))

    def _ask_ai_verify(self, sql: str, affected: int, rows) -> None:
        """Send execution result to AI for analysis and suggested next steps."""
        if rows:
            # Format rows as a mini table (max 20 rows)
            rows_txt = json.dumps(
                [{k: str(v) for k, v in r.items()} for r in rows[:20]],
                ensure_ascii=False, indent=2
            )
            rows_summary = f"{len(rows)} row(s) returned:\n{rows_txt}"
        else:
            rows_summary = f"{affected} row(s) affected. (no SELECT verification)"

        feed = (
            f"[EXECUTION REPORT — ANALYZE THIS]\n"
            f"SQL Executed:\n{sql[:400]}\n\n"
            f"Result:\n{rows_summary}\n\n"
            f"Based on the result above:\n"
            f"1. Confirm whether the operation succeeded as expected."
            f" Did it return/affect the right data?\n"
            f"2. Highlight any anomalies or surprising values.\n"
            f"3. Suggest the immediate next action or follow-up SQL if needed."
        )
        self._messages.append({"role": "user", "content": feed})
        threading.Thread(target=self._stream, daemon=True).start()

    def _ask_ai_error(self, sql: str, err: str) -> None:
        feed = (f"[SQL FAILED]\nSQL: {sql[:200]}\nError: {err}\n\n"
                "Explain why and provide a corrected ```sql block.")
        self._messages.append({"role": "user", "content": feed})
        threading.Thread(target=self._stream, daemon=True).start()

    def _copy_last(self) -> None:
        if self._last_ai_msg:
            self.clipboard_clear()
            self.clipboard_append(self._last_ai_msg)
            self._ctx_status.config(text="📋 Copied.", fg=TEXT_DIM)

    def _clear_chat(self) -> None:
        if not messagebox.askyesno("Clear?", "Reset conversation?", parent=self):
            return
        for w in self._msg_container.winfo_children():
            w.destroy()
        self._dynamic_labels.clear()
        self._stream_text_widget  = None
        self._stream_bubble_frame = None
        self._start_conversation()
