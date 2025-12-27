import os
import io
import csv
import time
import threading
import psycopg2
from psycopg2 import extras, sql
import customtkinter as ctk
from tkinter import messagebox, simpledialog
from dotenv import load_dotenv
from tabulate import tabulate

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

load_dotenv()

class DatabaseEngine:
    @staticmethod
    def get_db_info(dsn):
        try:
            conn = psycopg2.connect(dsn)
            cur = conn.cursor(cursor_factory=extras.RealDictCursor)
            cur.execute("SELECT version();")
            ver = cur.fetchone()['version'].split(' on ')[0]
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
            size = cur.fetchone()['pg_size_pretty']
            conn.close()
            return {"ver": ver, "size": size}
        except: return None

    @staticmethod
    def get_tables_stats(dsn):
        query = """
            SELECT relname AS name, n_live_tup AS rows,
            pg_size_pretty(pg_total_relation_size(relid)) AS size,
            pg_total_relation_size(relid) as bytes
            FROM pg_stat_user_tables ORDER BY name ASC;
        """
        try:
            conn = psycopg2.connect(dsn); cur = conn.cursor(cursor_factory=extras.RealDictCursor)
            cur.execute(query); data = cur.fetchall(); conn.close()
            return data
        except: return []

    @staticmethod
    def get_precise_schema(dsn, table_name):
        query = f"""
            SELECT column_name, 
            CASE WHEN data_type = 'USER-DEFINED' THEN udt_name ELSE data_type END as actual_type,
            is_nullable FROM information_schema.columns 
            WHERE table_name = '{table_name}' ORDER BY ordinal_position;
        """
        conn = psycopg2.connect(dsn); cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(query); cols = cur.fetchall(); conn.close()
        return cols

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

class TableCard(ctk.CTkFrame):
    def __init__(self, master, table_data, current_dsn, target_dsn, app_instance, side, **kwargs):
        super().__init__(master, fg_color="#1e1e1e", corner_radius=12, border_width=1, border_color="#333", **kwargs)
        self.table_name = table_data['name']; self.rows = table_data['rows']
        self.current_dsn = current_dsn; self.target_dsn = target_dsn; self.app = app_instance; self.side = side

        # Checkbox للتحديد المتعدد
        self.check_var = ctk.BooleanVar(value=False)
        self.cb = ctk.CTkCheckBox(self, text="", variable=self.check_var, width=20, command=self.on_toggle)
        self.cb.pack(side="left", padx=(10, 0))

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(info_frame, text=self.table_name, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
        
        stats_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        stats_frame.pack(fill="x")
        ctk.CTkLabel(stats_frame, text=f" {table_data['size']} ", fg_color="#34495e", corner_radius=10, font=("Consolas", 9)).pack(side="left", padx=2)
        ctk.CTkLabel(stats_frame, text=f" {self.rows:,} rows ", fg_color="#27ae60", corner_radius=10, font=("Consolas", 9)).pack(side="left", padx=2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="right", padx=10)
        ctk.CTkButton(actions, text="🚀", width=35, fg_color="#d35400", command=lambda: self.app.initiate_bulk_migration(self.side, [self.table_name])).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="🗑", width=35, fg_color="#7b241c", command=lambda: self.app.initiate_bulk_delete(self.side, [self.table_name])).pack(side="left", padx=2)

    def on_toggle(self):
        self.app.update_selection_count(self.side)

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
        
        # Bulk Actions Toolbar (يظهر عند التحديد)
        bulk_tools = ctk.CTkFrame(p, fg_color="#1a1a1a", height=40, corner_radius=10)
        bulk_tools.pack(fill="x", padx=15, pady=5)
        bulk_tools.pack_forget() # مخفي في البداية
        
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
        for p in [self.left_panel, self.right_panel]:
            info = DatabaseEngine.get_db_info(p["dsn"])
            if info: p["info"].configure(text=f"🟢 {info['ver'][:35]}\n📦 Size: {info['size']}", text_color="#27ae60")
            for c in p["scroll"].winfo_children(): c.destroy()
            tables = DatabaseEngine.get_tables_stats(p["dsn"])
            other_dsn = self.target_dsn if p["side"] == "source" else self.source_dsn
            for t in tables: TableCard(p["scroll"], t, p["dsn"], other_dsn, self, p["side"]).pack(fill="x", padx=10, pady=5)
            p["bulk_tools"].pack_forget()

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
        
        # حساب إجمالي السجلات للباتش بالكامل
        total_batch_rows = 0
        try:
            conn = psycopg2.connect(from_dsn); cur = conn.cursor()
            for t in table_names:
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(t)))
                total_batch_rows += cur.fetchone()[0]
            conn.close()
        except: pass

        threading.Thread(target=self.run_bulk_migration_logic, 
                         args=(table_names, from_dsn, to_dsn, total_batch_rows), daemon=True).start()

    def run_bulk_migration_logic(self, table_names, from_dsn, to_dsn, total_batch_rows):
        win = MigrationProgressWindow(self, total_batch_rows)
        start_time = time.time()
        batch_migrated = 0
        
        try:
            src_conn = psycopg2.connect(from_dsn)
            tgt_conn = psycopg2.connect(to_dsn); tgt_conn.autocommit = True
            
            for table in table_names:
                win.log(f"Processing table: {table}")
                cols = DatabaseEngine.get_precise_schema(from_dsn, table)
                
                # إنشاء الهيكل
                col_defs = [f"{sql.Identifier(c['column_name']).as_string(tgt_conn)} {c['actual_type']} {'NOT NULL' if c['is_nullable']=='NO' else ''}" for c in cols]
                with tgt_conn.cursor() as cur:
                    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)});')
                
                # ضخ البيانات
                with src_conn.cursor(name=f"stream_{table}") as s_cur:
                    s_cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
                    while True:
                        rows = s_cur.fetchmany(10000)
                        if not rows: break
                        buf = io.StringIO()
                        writer = csv.writer(buf, delimiter='\t', lineterminator='\n')
                        writer.writerows(rows); buf.seek(0)
                        with tgt_conn.cursor() as t_cur:
                            t_cur.copy_expert(f'COPY "{table}" FROM STDIN WITH (FORMAT CSV, DELIMITER \'\t\', NULL \'\')', buf)
                        
                        batch_migrated += len(rows)
                        elapsed = time.time() - start_time
                        speed = batch_migrated / elapsed if elapsed > 0 else 0
                        eta = time.strftime('%H:%M:%S', time.gmtime((total_batch_rows - batch_migrated) / speed)) if speed > 0 else "--:--"
                        self.after(10, lambda c=batch_migrated, s=speed, e=eta, t=table: win.update_status(c, s, e, t))
                
                win.log(f"Table {table} done.", "SUCCESS")
            
            win.log("✨ ALL TABLES MIGRATED!", "SUCCESS")
            self.after(1000, self.refresh_ui)
        except Exception as e:
            win.log(f"FATAL ERROR: {str(e)}", "ERROR")
            messagebox.showerror("Bulk Migration Failed", str(e))

if __name__ == "__main__":
    App().mainloop()