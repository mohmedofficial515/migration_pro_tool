"""
Migration Progress Window — Phase 4
=====================================
New in Phase 4:
- Pause / Resume button (uses threading.Event)
- Table counter: Total | Done | Failed | Remaining (live updates)
- Save Report button (JSON + CSV export after completion)
- Thread-safe log() routing via after() for parallel workers
- Active tables display for parallel mode
"""

import threading
import time
import customtkinter as ctk
from tkinter import filedialog, messagebox


class MigrationProgressWindow(ctk.CTkToplevel):

    def __init__(self, master, total_rows_total_batch: int):
        super().__init__(master)
        self.title("⚡ Bulk Migration Engine")
        self.geometry("760x680")
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.total_rows_total_batch = total_rows_total_batch

        # ── Pause event: set = running, cleared = paused ──
        self.pause_event = threading.Event()
        self.pause_event.set()  # Start in running state

        # Store report reference (set by migration service after completion)
        self._report = None

        # ── Header ──
        ctk.CTkLabel(
            self, text="⚡ Bulk Data Streaming Engine",
            font=("Segoe UI", 18, "bold"), text_color="#3498db"
        ).pack(pady=(15, 2))

        # ── Session + Active workers info ──
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=20, pady=(0, 4))

        self.session_lbl = ctk.CTkLabel(
            top_bar, text="Session: —", font=("Consolas", 10), text_color="#888"
        )
        self.session_lbl.pack(side="left")

        self.workers_lbl = ctk.CTkLabel(
            top_bar, text="", font=("Consolas", 10), text_color="#f39c12"
        )
        self.workers_lbl.pack(side="right")

        # ── Progress bar ──
        self.pbar = ctk.CTkProgressBar(self, width=720, height=18)
        self.pbar.pack(pady=(6, 2), padx=20)
        self.pbar.set(0)

        # ── Stats ──
        self.stats_lbl = ctk.CTkLabel(
            self, text="Initializing...", font=("Segoe UI", 13)
        )
        self.stats_lbl.pack(pady=2)

        self.details_lbl = ctk.CTkLabel(
            self, text="Speed: 0 rows/s  |  ETA: --:--",
            font=("Consolas", 11), text_color="#3498db"
        )
        self.details_lbl.pack(pady=2)

        # ── Table counter (Phase 4) ──────────────────────
        counter_frame = ctk.CTkFrame(self, fg_color="#161616", corner_radius=10, height=36)
        counter_frame.pack(fill="x", padx=20, pady=(4, 2))
        counter_frame.pack_propagate(False)

        self._counter_labels: dict[str, ctk.CTkLabel] = {}
        for key, color, default in [
            ("total",     "#888888", "Total: 0"),
            ("done",      "#27ae60", "✅ Done: 0"),
            ("failed",    "#e74c3c", "❌ Failed: 0"),
            ("remaining", "#3498db", "⏳ Remaining: 0"),
        ]:
            lbl = ctk.CTkLabel(
                counter_frame, text=default,
                font=("Consolas", 11, "bold"), text_color=color
            )
            lbl.pack(side="left", padx=16, pady=4)
            self._counter_labels[key] = lbl

        # ── Active tables display (parallel mode) ──
        self.active_lbl = ctk.CTkLabel(
            self, text="Active: ─",
            font=("Consolas", 10), text_color="#f39c12",
            wraplength=720, justify="left"
        )
        self.active_lbl.pack(pady=2, padx=20, anchor="w")

        # ── Validation status ──
        self.validation_lbl = ctk.CTkLabel(
            self, text="", font=("Segoe UI", 11), text_color="#f39c12"
        )
        self.validation_lbl.pack(pady=(0, 2))

        # ── Action buttons ──────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=4)

        # Pause / Resume
        self.pause_btn = ctk.CTkButton(
            btn_frame, text="⏸ Pause", width=130,
            fg_color="#f39c12", hover_color="#e67e22",
            command=self._toggle_pause
        )
        self.pause_btn.pack(side="left", padx=6)

        # Save Report (disabled until migration completes)
        self.save_btn = ctk.CTkButton(
            btn_frame, text="💾 Save Report", width=140,
            fg_color="#1a5276", hover_color="#154360",
            state="disabled",
            command=self._save_report
        )
        self.save_btn.pack(side="left", padx=6)

        # Copy Log
        self.copy_btn = ctk.CTkButton(
            btn_frame, text="📋 Copy Log", width=130,
            fg_color="#2c3e50", hover_color="#34495e",
            command=self._copy_log
        )
        self.copy_btn.pack(side="left", padx=6)

        # Close
        self.close_btn = ctk.CTkButton(
            btn_frame, text="✖ Close", width=100,
            fg_color="#5d6d7e", hover_color="#4a5568",
            command=self._on_close_request
        )
        self.close_btn.pack(side="left", padx=6)

        # ── Log textbox ──────────────────────────────────
        self.log_txt = ctk.CTkTextbox(
            self, font=("Consolas", 11),
            fg_color="#0a0a0a", text_color="#e0e0e0"
        )
        self.log_txt.pack(pady=8, padx=15, fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

    # ─────────────────────────────────────────────────────────
    # Public API — all thread-safe
    # ─────────────────────────────────────────────────────────

    def log(self, msg: str, status: str = "INFO") -> None:
        """
        Thread-safe log. From main thread → direct write.
        From any worker thread → via after(0) to main thread.
        """
        if threading.current_thread() is threading.main_thread():
            self._log_direct(msg, status)
        else:
            self.after(0, lambda m=msg, s=status: self._log_direct(m, s))

    def update_status(self, current_done: int, speed: float, eta: str, current_table: str) -> None:
        """Updates progress bar + stats. Must be called via win.after() from worker threads."""
        total = self.total_rows_total_batch
        pct = current_done / total if total > 0 else 1.0
        self.pbar.set(min(pct, 1.0))
        self.stats_lbl.configure(
            text=f"{current_done:,} / {total:,} rows ({pct:.1%})"
                 f"  —  last: {current_table[:38]}"
        )
        self.details_lbl.configure(
            text=f"🚀 Speed: {speed:,.0f} rows/s  |  ⏳ ETA: {eta}"
        )

    def update_table_counts(self, total: int, done: int, failed: int) -> None:
        """
        Updates the live table counter badges.
        Thread-safe.
        """
        remaining = total - done - failed

        def _update():
            self._counter_labels["total"].configure(text=f"Total: {total}")
            self._counter_labels["done"].configure(text=f"✅ Done: {done}")
            self._counter_labels["failed"].configure(
                text=f"❌ Failed: {failed}",
                text_color="#e74c3c" if failed > 0 else "#888"
            )
            self._counter_labels["remaining"].configure(
                text=f"⏳ Remaining: {remaining}"
            )

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_active_tables(self, tables: list[str]) -> None:
        """Shows currently-running parallel table names. Thread-safe."""
        def _update():
            if not tables:
                self.active_lbl.configure(text="Active: ─")
                self.workers_lbl.configure(text="⚙ Idle")
            else:
                badges = "  ".join(f"[{t[:20]}]" for t in tables)
                self.active_lbl.configure(text=f"▶ {badges}")
                self.workers_lbl.configure(text=f"⚙ {len(tables)} workers")

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_session_id(self, session_id: str, max_workers: int) -> None:
        """Thread-safe session display."""
        def _update():
            self.session_lbl.configure(text=f"Session: {session_id}")
            self.workers_lbl.configure(text=f"⚙ MAX_WORKERS: {max_workers}")
        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.after(0, _update)

    def set_validation_status(self, msg: str, ok: bool = True) -> None:
        """Thread-safe validation label update."""
        color = "#27ae60" if ok else "#e74c3c"
        if threading.current_thread() is threading.main_thread():
            self.validation_lbl.configure(text=msg, text_color=color)
        else:
            self.after(0, lambda: self.validation_lbl.configure(text=msg, text_color=color))

    def enable_save_report(self, report) -> None:
        """
        Called by migration service when migration is complete.
        Enables the Save Report button and stores the report reference.
        Thread-safe.
        """
        self._report = report

        def _enable():
            self.save_btn.configure(state="normal", fg_color="#1e8449")
            self.pause_btn.configure(state="disabled", fg_color="#5d6d7e")

        if threading.current_thread() is threading.main_thread():
            _enable()
        else:
            self.after(0, _enable)

    # ─────────────────────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────────────────────

    def _log_direct(self, msg: str, status: str) -> None:
        """Writes to log textbox — MUST be called from main thread."""
        icons = {"SUCCESS": "✅", "ERROR": "⚠️ ", "INFO": "🔹"}
        prefix = icons.get(status, "🔹")
        self.log_txt.insert("end", f"{prefix} [{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_txt.see("end")

    def _toggle_pause(self) -> None:
        """Pause / Resume toggle — runs on main thread (button click)."""
        if self.pause_event.is_set():
            # Currently running → Pause
            self.pause_event.clear()
            self.pause_btn.configure(text="▶ Resume", fg_color="#27ae60", hover_color="#1e8449")
            self._log_direct("⏸ Migration PAUSED. Workers will stop after current chunk.", "ERROR")
        else:
            # Currently paused → Resume
            self.pause_event.set()
            self.pause_btn.configure(text="⏸ Pause", fg_color="#f39c12", hover_color="#e67e22")
            self._log_direct("▶️  Migration RESUMED.", "SUCCESS")

    def _save_report(self) -> None:
        """Saves JSON + CSV report files and shows confirmation."""
        if not self._report:
            messagebox.showwarning("No Report", "No migration report available yet.")
            return
        try:
            json_path, csv_path = self._report.save_all()
            messagebox.showinfo(
                "Report Saved",
                f"✅ Reports saved to:\n\n"
                f"JSON: {json_path}\n"
                f"CSV:  {csv_path}"
            )
            self._log_direct(f"💾 Report saved → {json_path.name}", "SUCCESS")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    def _copy_log(self) -> None:
        """Copies log content to clipboard."""
        content = self.log_txt.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(content)
        self.copy_btn.configure(text="✅ Copied!")
        self.after(2000, lambda: self.copy_btn.configure(text="📋 Copy Log"))

    def _on_close_request(self) -> None:
        """Confirms before closing (migration may be running in background)."""
        if messagebox.askyesno(
            "Close Window?",
            "Migration may still be running in the background.\nClose this window?"
        ):
            # If paused, resume so workers can exit naturally
            if not self.pause_event.is_set():
                self.pause_event.set()
            self.destroy()
