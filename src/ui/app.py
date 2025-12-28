import os
import threading
import customtkinter as ctk
from tkinter import messagebox
from psycopg2 import sql
import psycopg2
from dotenv import load_dotenv

from src.database.engine import DatabaseEngine
from src.ui.components.table_card import TableCard
from src.services.migration import run_bulk_migration

load_dotenv()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PostgreSQL Bulk Architect Pro v3.0")
        self.geometry("1500x900")
        self.source_dsn = os.getenv("SOURCE_DB_URL")
        self.target_dsn = os.getenv("TARGET_DB_URL")
        
        self.header = ctk.CTkLabel(self, text="🛡️ PRODUCTION BULK MIGRATOR", font=("Segoe UI", 28, "bold"), text_color="#3498db")
        self.header.pack(pady=15)
        
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=20)
        
        self.left_panel = self.build_panel("SOURCE DB", self.source_dsn, "source")
        self.right_panel = self.build_panel("TARGET DB", self.target_dsn, "target")
        self.refresh_ui()

    def build_panel(self, title, dsn, side):
        p = ctk.CTkFrame(self.container, corner_radius=15, border_width=1, border_color="#2c3e50")
        p.pack(side="left", fill="both", expand=True, padx=10)
        
        header_frame = ctk.CTkFrame(p, fg_color="transparent")
        header_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(header_frame, text=title, font=("Segoe UI", 18, "bold")).pack(side="left", padx=20)
        
        # Bulk Actions Toolbar
        bulk_tools = ctk.CTkFrame(p, fg_color="#1a1a1a", height=40, corner_radius=10)
        bulk_tools.pack(fill="x", padx=15, pady=5)
        bulk_tools.pack_forget() 
        
        info = ctk.CTkLabel(p, text="Connecting...", font=("Consolas", 11), fg_color="#161616", height=50)
        info.pack(fill="x", padx=15, pady=5)
        
        search_var = ctk.StringVar()
        ctk.CTkEntry(p, placeholder_text="🔍 Filter tables...", textvariable=search_var).pack(fill="x", padx=15, pady=5)
        
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=5, pady=5)
        search_var.trace_add("write", lambda *args: self.filter_tables(scroll, search_var.get()))
        
        return {"scroll": scroll, "info": info, "dsn": dsn, "side": side, "bulk_tools": bulk_tools, "search_var": search_var}

    def update_selection_count(self, side):
        panel = self.left_panel if side == "source" else self.right_panel
        selected = [card.table_name for card in panel["scroll"].winfo_children() if card.check_var.get()]
        
        if len(selected) > 0:
            panel["bulk_tools"].pack(fill="x", padx=15, pady=5, before=panel["info"])
            for widget in panel["bulk_tools"].winfo_children(): widget.destroy()
            
            ctk.CTkLabel(panel["bulk_tools"], text=f"Selected: {len(selected)}", font=("Segoe UI", 11, "bold")).pack(side="left", padx=15)
            ctk.CTkButton(panel["bulk_tools"], text="🚀 Migrate Selected", fg_color="#d35400", height=25, 
                          command=lambda: self.initiate_bulk_migration(side, selected)).pack(side="left", padx=5)
            ctk.CTkButton(panel["bulk_tools"], text="🗑 Delete Selected", fg_color="#7b241c", height=25, 
                          command=lambda: self.initiate_bulk_delete(side, selected)).pack(side="left", padx=5)
        else:
            panel["bulk_tools"].pack_forget()

    def filter_tables(self, scroll, val):
        for card in scroll.winfo_children():
            card.pack(fill="x", padx=10, pady=5) if val.lower() in card.table_name.lower() else card.pack_forget()

    def refresh_ui(self):
        # Set loading state
        for p in [self.left_panel, self.right_panel]:
            p["info"].configure(text="⏳ Loading...", text_color="white")
            for c in p["scroll"].winfo_children(): c.destroy()
            p["bulk_tools"].pack_forget()

        # Run data fetch in background
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            # Fetch Source Data
            src_info = DatabaseEngine.get_db_info(self.source_dsn)
            src_tables = DatabaseEngine.get_tables_stats(self.source_dsn)
            
            # Fetch Target Data
            tgt_info = DatabaseEngine.get_db_info(self.target_dsn)
            tgt_tables = DatabaseEngine.get_tables_stats(self.target_dsn)
            
            # Schedule UI Update on Main Thread
            self.after(0, lambda: self._update_panels(src_info, src_tables, tgt_info, tgt_tables))
        except Exception as e:
            print(f"Error fetching data: {e}")

    def _update_panels(self, src_info, src_tables, tgt_info, tgt_tables):
        # Update Left Panel (Source)
        self._populate_panel(self.left_panel, src_info, src_tables, self.target_dsn)
        
        # Update Right Panel (Target)
        self._populate_panel(self.right_panel, tgt_info, tgt_tables, self.source_dsn)

    def _populate_panel(self, panel, info, tables, other_dsn):
        if info: 
            panel["info"].configure(text=f"🟢 {info['ver'][:35]}\n📦 Size: {info['size']}", text_color="#27ae60")
        else:
            panel["info"].configure(text="🔴 Connection Failed", text_color="#e74c3c")

        for t in tables: 
            TableCard(panel["scroll"], t, panel["dsn"], other_dsn, self, panel["side"]).pack(fill="x", padx=10, pady=5)

    def initiate_bulk_delete(self, side, table_names):
        dsn = self.source_dsn if side == "source" else self.target_dsn
        if messagebox.askyesno("Confirm Bulk Drop", f"Are you sure you want to delete {len(table_names)} tables?"):
            try:
                conn = psycopg2.connect(dsn); conn.autocommit = True
                cur = conn.cursor()
                for name in table_names:
                    cur.execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(name)))
                messagebox.showinfo("Success", f"Deleted {len(table_names)} tables.")
                self.refresh_ui()
            except Exception as e: messagebox.showerror("Error", str(e))

    def initiate_bulk_migration(self, side, table_names):
        from_dsn = self.source_dsn if side == "source" else self.target_dsn
        to_dsn = self.target_dsn if side == "source" else self.source_dsn
        
        # Calculate total rows for batch
        total_batch_rows = 0
        try:
            conn = psycopg2.connect(from_dsn); cur = conn.cursor()
            for t in table_names:
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(t)))
                total_batch_rows += cur.fetchone()[0]
            conn.close()
        except: pass

        threading.Thread(target=run_bulk_migration, 
                         args=(self, table_names, from_dsn, to_dsn, total_batch_rows), daemon=True).start()
