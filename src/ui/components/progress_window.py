import customtkinter as ctk
import time

class MigrationProgressWindow(ctk.CTkToplevel):
    def __init__(self, master, total_rows_total_batch):
        super().__init__(master)
        self.title("Bulk Migration Engine")
        self.geometry("700x550")
        self.attributes("-topmost", True)
        self.total_rows_total_batch = total_rows_total_batch

        self.lbl = ctk.CTkLabel(self, text="⚡ Bulk Data Streaming", font=("Segoe UI", 18, "bold"))
        self.lbl.pack(pady=10)

        self.pbar = ctk.CTkProgressBar(self, width=550)
        self.pbar.pack(pady=5); self.pbar.set(0)

        self.stats_lbl = ctk.CTkLabel(self, text="Initializing batch...", font=("Segoe UI", 13))
        self.stats_lbl.pack(pady=5)

        self.details_lbl = ctk.CTkLabel(self, text="Speed: 0 rows/s | ETA: --:--", font=("Consolas", 11), text_color="#3498db")
        self.details_lbl.pack(pady=2)

        self.log_txt = ctk.CTkTextbox(self, width=650, height=300, font=("Consolas", 11), fg_color="#000")
        self.log_txt.pack(pady=10, padx=10)

    def update_status(self, current_batch_done, speed, eta, current_table):
        pct = current_batch_done / self.total_rows_total_batch if self.total_rows_total_batch > 0 else 1
        self.pbar.set(pct)
        self.stats_lbl.configure(text=f"Table: {current_table} | Total: {current_batch_done:,}/{self.total_rows_total_batch:,} ({pct:.1%})")
        self.details_lbl.configure(text=f"🚀 Overall Speed: {speed:.0f} rows/s | ⏳ Batch ETA: {eta}")

    def log(self, msg, status="INFO"):
        prefix = "✅" if status == "SUCCESS" else "❌" if status == "ERROR" else "🔹"
        self.log_txt.insert("end", f"{prefix} [{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_txt.see("end")
