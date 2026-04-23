"""
Migration Progress Window — v4.0
==================================
Rebuilt with standard tkinter (no customtkinter dependency).

Features:
- Pause / Resume button (threading.Event)
- Table counter: Total | Done | Failed | Remaining (live updates)
- Save Report (JSON + CSV) after completion
- Thread-safe log() via after()
- Active tables display for parallel mode
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Palette (matches app.py) ──────────────────────────────────
BG       = "#0f1117"
PANEL    = "#161b22"
HEADER   = "#1c2128"
ACCENT   = "#2f81f7"
ACCENT2  = "#3fb950"
DANGER   = "#f85149"
WARNING  = "#d29922"
TEXT     = "#e6edf3"
TEXT_DIM = "#8b949e"
BORDER   = "#30363d"
ENTRY_BG = "#21262d"

FONT_UI   = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEAD = ("Segoe UI", 14, "bold")
FONT_MONO = ("Consolas", 10)


def _flat_btn(master, text: str, command=None, bg=ENTRY_BG,
              fg=TEXT, hover="#30363d", padx=14, pady=6,
              font=FONT_UI, width=None):
    """Returns a tk.Label configured as a flat button with hover."""
    lbl = tk.Label(
        master, text=text, bg=bg, fg=fg, font=font,
        padx=padx, pady=pady, cursor="hand2",
    )
    if width:
        lbl.config(width=width)

    def _in(_):  lbl.config(bg=hover)
    def _out(_): lbl.config(bg=bg)
    def _clk(_):
        if command: command()

    lbl.bind("<Enter>",    _in)
    lbl.bind("<Leave>",    _out)
    lbl.bind("<Button-1>", _clk)
    return lbl


class MigrationProgressWindow(tk.Toplevel):

    def __init__(self, master, total_rows_total_batch: int):
        super().__init__(master)
        self.title("⚡ Bulk Migration Engine")
        self.geometry("780x700")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.total_rows_total_batch = total_rows_total_batch

        # Pause event: set = running, cleared = paused
        self.pause_event = threading.Event()
        self.pause_event.set()

        self._report = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

    # ── Build UI ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Header
        hdr = tk.Frame(self, bg=HEADER, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Frame(hdr, bg=ACCENT, width=4).pack(side="left", fill="y")
        tk.Label(
            hdr, text="⚡  Bulk Data Streaming Engine",
            bg=HEADER, fg=TEXT, font=FONT_HEAD, padx=16,
        ).pack(side="left", fill="y")

        # Session / Workers bar
        top = tk.Frame(self, bg=BG, height=26)
        top.pack(fill="x", padx=16, pady=(8, 0))
        top.pack_propagate(False)
        self._session_lbl = tk.Label(top, text="Session: —", bg=BG, fg=TEXT_DIM, font=FONT_MONO, anchor="w")
        self._session_lbl.pack(side="left")
        self._workers_lbl = tk.Label(top, text="", bg=BG, fg=WARNING, font=FONT_MONO, anchor="e")
        self._workers_lbl.pack(side="right")

        # Progress bar (using ttk)
        style = ttk.Style(self)
        style.configure("Mig.Horizontal.TProgressbar",
                         troughcolor=HEADER, background=ACCENT, thickness=14)
        self._pbar = ttk.Progressbar(
            self, style="Mig.Horizontal.TProgressbar",
            orient="horizontal", length=750, mode="determinate",
        )
        self._pbar.pack(padx=16, pady=(8, 2), fill="x")

        # Stats
        self._stats_lbl = tk.Label(self, text="Initializing...", bg=BG, fg=TEXT, font=FONT_UI)
        self._stats_lbl.pack(pady=2)

        self._details_lbl = tk.Label(
            self, text="Speed: 0 rows/s  |  ETA: --:--",
            bg=BG, fg=ACCENT, font=FONT_MONO,
        )
        self._details_lbl.pack(pady=2)

        # Table counter bar
        ctr = tk.Frame(self, bg=PANEL, height=36)
        ctr.pack(fill="x", padx=16, pady=(4, 2))
        ctr.pack_propagate(False)

        self._counter_labels: dict[str, tk.Label] = {}
        for key, fg, default in [
            ("total",     TEXT_DIM, "Total: 0"),
            ("done",      ACCENT2,  "✅ Done: 0"),
            ("failed",    DANGER,   "❌ Failed: 0"),
            ("remaining", ACCENT,   "⏳ Remaining: 0"),
        ]:
            lbl = tk.Label(ctr, text=default, bg=PANEL, fg=fg, font=FONT_MONO, padx=16)
            lbl.pack(side="left", pady=4)
            self._counter_labels[key] = lbl

        # Active tables
        self._active_lbl = tk.Label(
            self, text="Active: ─",
            bg=BG, fg=WARNING, font=FONT_MONO,
            wraplength=740, justify="left", anchor="w",
        )
        self._active_lbl.pack(pady=2, padx=16, fill="x")

        # Validation status
        self._validation_lbl = tk.Label(self, text="", bg=BG, fg=WARNING, font=FONT_UI)
        self._validation_lbl.pack(pady=(0, 2))

        # Action buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=6)

        self._pause_btn = _flat_btn(
            btn_row, text="⏸  Pause",
            command=self._toggle_pause,
            bg="#5a3d00", hover="#7a5200", padx=18, pady=7,
        )
        self._pause_btn.pack(side="left", padx=4)

        self._save_btn = _flat_btn(
            btn_row, text="💾  Save Report",
            command=self._save_report,
            bg="#0d2137", hover="#132d4a", padx=18, pady=7,
        )
        self._save_btn.pack(side="left", padx=4)
        self._save_btn_enabled = False

        self._copy_btn = _flat_btn(
            btn_row, text="📋  Copy Log",
            command=self._copy_log,
            bg=PANEL, hover=HEADER, padx=18, pady=7,
        )
        self._copy_btn.pack(side="left", padx=4)

        _flat_btn(
            btn_row, text="✖  Close",
            command=self._on_close_request,
            bg="#2c2c2c", hover="#3d3d3d", padx=18, pady=7,
        ).pack(side="left", padx=4)

        # Log textbox
        log_container = tk.Frame(self, bg=BORDER, bd=1)
        log_container.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self._log_txt = tk.Text(
            log_container,
            bg="#0a0a0a", fg=TEXT, font=FONT_MONO,
            insertbackground=TEXT, bd=0, padx=8, pady=6,
            wrap="word", state="normal",
        )
        vsb = ttk.Scrollbar(log_container, orient="vertical", command=self._log_txt.yview)
        self._log_txt.config(yscrollcommand=vsb.set)
        self._log_txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # ── Public API (thread-safe, TclError-guarded) ────────────

    def _alive(self) -> bool:
        """Returns False if the window has been destroyed — safe to call from any thread."""
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def log(self, msg: str, status: str = "INFO") -> None:
        if threading.current_thread() is threading.main_thread():
            self._log_direct(msg, status)
        else:
            self.after(0, lambda m=msg, s=status: self._log_direct(m, s))

    def update_status(self, current_done: int, speed: float, eta: str, current_table: str) -> None:
        """
        Progress bar — Hybrid mode:
        • Row-based  (smooth/continuous) when total_batch_rows > 0  ← preferred
        • Table-based (step jumps)       when row estimate is zero   ← fallback
        This prevents the bar from staying at 0% during active UPSERT processing.
        """
        if not self._alive():
            return
        try:
            total        = self.total_rows_total_batch        # pg_stat row estimate
            total_tables = self._total_tables if hasattr(self, "_total_tables") else 0
            done_tables  = self._done_tables  if hasattr(self, "_done_tables")  else 0

            if total > 0 and current_done > 0:
                # ── Row-based: smooth continuous movement ──────────────
                # Cap to (done_tables+workers)/total_tables so it can't
                # overshoot past the actual table-completion boundary.
                row_pct   = current_done / int(total)
                table_cap = (done_tables + max(1, total_tables - done_tables)) / max(total_tables, 1)
                pct       = min(row_pct, table_cap, 1.0)
            elif total_tables > 0:
                # ── Table-based fallback: step at each table finish ────
                pct = done_tables / total_tables
            else:
                pct = 0.0

            self._pbar["value"] = pct * 100

            rows_text = (
                f"{current_done:,} rows"
                if total <= 0
                else f"{current_done:,} / {total:,} rows ({pct:.1%})"
            )
            self._stats_lbl.config(text=f"{rows_text}  —  {current_table[:40]}")
            self._details_lbl.config(text=f"Speed: {speed:,.0f} rows/s  |  ETA: {eta}")
        except tk.TclError:
            pass

    def update_table_counts(self, total: int, done: int, failed: int) -> None:
        # Store for table-based progress calculation
        self._total_tables = total
        self._done_tables  = done

        remaining = total - done - failed

        def _update():
            if not self._alive():
                return
            try:
                self._counter_labels["total"].config(text=f"Total: {total}")
                self._counter_labels["done"].config(text=f"✅ Done: {done}")
                self._counter_labels["failed"].config(
                    text=f"❌ Failed: {failed}",
                    fg=DANGER if failed > 0 else TEXT_DIM,
                )
                self._counter_labels["remaining"].config(text=f"⏳ Remaining: {remaining}")
                if total > 0:
                    pct = done / total
                    self._pbar["value"] = min(pct, 1.0) * 100
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_active_tables(self, tables: list[str]) -> None:
        def _update():
            if not self._alive():
                return
            try:
                if not tables:
                    self._active_lbl.config(text="Active: ─")
                    self._workers_lbl.config(text="⚙  Idle")
                else:
                    badges = "  ".join(f"[{t[:22]}]" for t in tables)
                    self._active_lbl.config(text=f"▶  {badges}")
                    self._workers_lbl.config(text=f"⚙  {len(tables)} workers active")
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_session_id(self, session_id: str, max_workers: int) -> None:
        def _update():
            if not self._alive():
                return
            try:
                self._session_lbl.config(text=f"Session: {session_id}")
                self._workers_lbl.config(text=f"⚙  MAX_WORKERS: {max_workers}")
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_validation_status(self, msg: str, ok: bool = True) -> None:
        color = ACCENT2 if ok else DANGER
        def _update():
            if not self._alive():
                return
            try:
                self._validation_lbl.config(text=msg, fg=color)
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def enable_save_report(self, report) -> None:
        self._report = report
        self._save_btn_enabled = True

        def _enable():
            if not self._alive():
                return
            try:
                self._save_btn.config(bg="#0d3320", fg=ACCENT2)
                self._pause_btn.config(bg="#2c2c2c", fg=TEXT_DIM)
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _enable()
        else:
            self.after(0, _enable)

    # ── Private ──────────────────────────────────────────────

    def _log_direct(self, msg: str, status: str) -> None:
        try:
            if not self.winfo_exists():
                return
            icons = {"SUCCESS": "✅", "ERROR": "⚠️ ", "INFO": "🔹"}
            prefix = icons.get(status, "🔹")
            self._log_txt.insert("end", f"{prefix} [{time.strftime('%H:%M:%S')}] {msg}\n")
            self._log_txt.see("end")
        except tk.TclError:
            pass

    def _toggle_pause(self) -> None:
        if self.pause_event.is_set():
            self.pause_event.clear()
            self._pause_btn.config(text="▶  Resume", bg="#0d3320", fg=ACCENT2)
            self._log_direct("⏸  Migration PAUSED — workers stop after current chunk.", "ERROR")
        else:
            self.pause_event.set()
            self._pause_btn.config(text="⏸  Pause", bg="#5a3d00", fg=WARNING)
            self._log_direct("▶️   Migration RESUMED.", "SUCCESS")

    def _save_report(self) -> None:
        if not self._save_btn_enabled or not self._report:
            messagebox.showwarning("No Report", "Report not available yet — wait for migration to finish.")
            return
        try:
            json_path, csv_path = self._report.save_all()
            messagebox.showinfo(
                "Report Saved",
                f"✅  Reports saved:\n\nJSON: {json_path}\nCSV:  {csv_path}",
            )
            self._log_direct(f"💾  Report → {json_path.name}", "SUCCESS")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    def _copy_log(self) -> None:
        content = self._log_txt.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(content)
        self._copy_btn.config(text="✅  Copied!")
        self.after(2000, lambda: self._copy_btn.config(text="📋  Copy Log"))

    def _on_close_request(self) -> None:
        if messagebox.askyesno(
            "Close Window?",
            "Migration may still be running in background.\nClose this window anyway?",
        ):
            if not self.pause_event.is_set():
                self.pause_event.set()
            self.destroy()
