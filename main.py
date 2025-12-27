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
            SELECT 
                column_name, 
                CASE 
                    WHEN data_type = 'USER-DEFINED' THEN udt_name 
                    ELSE data_type 
                END as actual_type,
                is_nullable,
                column_default
            FROM information_schema.columns 
            WHERE table_name = '{table_name}'
            ORDER BY ordinal_position;
        """
        conn = psycopg2.connect(dsn); cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(query); cols = cur.fetchall(); conn.close()
        return cols

    @staticmethod
    def check_and_fix_extensions(target_dsn, schema_cols):
        needs_postgis = any('geometry' in c['actual_type'] for c in schema_cols)
        if needs_postgis:
            try:
                conn = psycopg2.connect(target_dsn); conn.autocommit = True
                conn.cursor().execute("CREATE EXTENSION IF NOT EXISTS postgis;")
                conn.close()
            except: pass

class MigrationProgressWindow(ctk.CTkToplevel):
    def __init__(self, master, total_rows):
        super().__init__(master)
        self.title("Production Migration Engine")
        self.geometry("650x500")
        self.attributes("-topmost", True)
        self.total_rows = total_rows

        self.lbl = ctk.CTkLabel(self, text="⚡ Real-time Data Streaming", font=("Segoe UI", 16, "bold"))
        self.lbl.pack(pady=10)

        self.pbar = ctk.CTkProgressBar(self, width=500)
        self.pbar.pack(pady=5)
        self.pbar.set(0)

        self.stats_lbl = ctk.CTkLabel(self, text="Preparing... 0%", font=("Segoe UI", 12))
        self.stats_lbl.pack(pady=5)

        self.details_lbl = ctk.CTkLabel(self, text="Speed: 0 rows/s | ETA: --:--", font=("Consolas", 11), text_color="#3498db")
        self.details_lbl.pack(pady=2)

        self.log_txt = ctk.CTkTextbox(self, width=600, height=250, font=("Consolas", 11), fg_color="#000")
        self.log_txt.pack(pady=10)

    def update_status(self, current, speed, eta):
        percentage = current / self.total_rows if self.total_rows > 0 else 1
        self.pbar.set(percentage)
        self.stats_lbl.configure(text=f"Transferred: {current:,} / {self.total_rows:,} ({percentage:.1%})")
        self.details_lbl.configure(text=f"🚀 Speed: {speed:.0f} rows/s | ⏳ ETA: {eta}")

    def log(self, msg, status="INFO"):
        prefix = "✅" if status == "SUCCESS" else "❌" if status == "ERROR" else "🔹"
        self.log_txt.insert("end", f"{prefix} [{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_txt.see("end")

class TableCard(ctk.CTkFrame):
    def __init__(self, master, table_data, current_dsn, target_dsn, app_instance, **kwargs):
        super().__init__(master, fg_color="#1e1e1e", corner_radius=12, border_width=1, border_color="#333", **kwargs)
        self.table_name = table_data['name']; self.rows = table_data['rows']
        self.current_dsn = current_dsn; self.target_dsn = target_dsn; self.app = app_instance

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=15, pady=10)
        ctk.CTkLabel(info_frame, text=self.table_name, font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x")
        
        stats_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        stats_frame.pack(fill="x")
        ctk.CTkLabel(stats_frame, text=f" {table_data['size']} ", fg_color="#34495e", corner_radius=10).pack(side="left", padx=2)
        ctk.CTkLabel(stats_frame, text=f" {self.rows:,} rows ", fg_color="#27ae60", corner_radius=10).pack(side="left", padx=2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="right", padx=10)
        ctk.CTkButton(actions, text="🚀 Migrate", width=80, fg_color="#d35400", command=self.start_mig).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="🗑", width=35, fg_color="#7b241c", command=self.delete_table).pack(side="left", padx=2)

    def delete_table(self):
        if messagebox.askyesno("Confirm", f"Drop table {self.table_name}?"):
            try:
                conn = psycopg2.connect(self.current_dsn); conn.autocommit = True
                conn.cursor().execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(self.table_name)))
                self.app.refresh_ui()
            except Exception as e: messagebox.showerror("Error", str(e))

    def start_mig(self):
        self.app.initiate_migration(self.table_name, self.current_dsn, self.target_dsn)

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PostgreSQL Industrial Data Architect v2.0")
        self.geometry("1400x850")
        self.source_dsn = os.getenv("SOURCE_DB_URL")
        self.target_dsn = os.getenv("TARGET_DB_URL")
        
        self.header = ctk.CTkLabel(self, text="🛡️ PRODUCTION DATA MIGRATOR", font=("Segoe UI", 28, "bold"), text_color="#3498db")
        self.header.pack(pady=20)
        
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=20)
        
        self.left_panel = self.build_panel("SOURCE DB", self.source_dsn, self.target_dsn)
        self.right_panel = self.build_panel("TARGET DB", self.target_dsn, self.source_dsn)
        self.refresh_ui()

    def build_panel(self, title, dsn, other):
        p = ctk.CTkFrame(self.container, corner_radius=15, border_width=1, border_color="#2c3e50")
        p.pack(side="left", fill="both", expand=True, padx=10)
        ctk.CTkLabel(p, text=title, font=("Segoe UI", 18, "bold")).pack(pady=10)
        
        info = ctk.CTkLabel(p, text="Connecting...", font=("Consolas", 11), fg_color="#161616", height=60)
        info.pack(fill="x", padx=15, pady=5)
        
        search_var = ctk.StringVar()
        ctk.CTkEntry(p, placeholder_text="🔍 Filter tables...", textvariable=search_var).pack(fill="x", padx=15, pady=5)
        
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=5, pady=5)
        search_var.trace_add("write", lambda *args: self.filter_tables(scroll, search_var.get()))
        
        return {"scroll": scroll, "info": info, "dsn": dsn, "other": other, "search_var": search_var}

    def filter_tables(self, scroll, val):
        for card in scroll.winfo_children():
            card.pack(fill="x", padx=10, pady=5) if val.lower() in card.table_name.lower() else card.pack_forget()

    def refresh_ui(self):
        for p in [self.left_panel, self.right_panel]:
            info = DatabaseEngine.get_db_info(p["dsn"])
            if info: p["info"].configure(text=f"🟢 {info['ver'][:35]}\n📦 Size: {info['size']}", text_color="#27ae60")
            for c in p["scroll"].winfo_children(): c.destroy()
            tables = DatabaseEngine.get_tables_stats(p["dsn"])
            for t in tables: TableCard(p["scroll"], t, p["dsn"], p["other"], self).pack(fill="x", padx=10, pady=5)

    def initiate_migration(self, table_name, from_dsn, to_dsn):
        new_name = simpledialog.askstring("Rename", f"Target name for '{table_name}':", initialvalue=table_name)
        if not new_name: return
        
        try:
            temp_conn = psycopg2.connect(from_dsn)
            temp_cur = temp_conn.cursor()
            temp_cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
            total_rows = temp_cur.fetchone()[0]
            temp_conn.close()
        except:
            total_rows = 0

        threading.Thread(target=self.run_secure_migration, 
                         args=(table_name, new_name, from_dsn, to_dsn, total_rows), 
                         daemon=True).start()

    def run_secure_migration(self, src_t, tgt_t, from_dsn, to_dsn, total_rows):
        win = MigrationProgressWindow(self, total_rows)
        start_time = time.time()
        try:
            win.log(f"Analyzing schema for {src_t}...")
            cols = DatabaseEngine.get_precise_schema(from_dsn, src_t)
            DatabaseEngine.check_and_fix_extensions(to_dsn, cols)
            
            col_defs = []
            for c in cols:
                col_name = sql.Identifier(c['column_name']).as_string(psycopg2.connect(to_dsn))
                col_type = c['actual_type']
                null_part = "NOT NULL" if c['is_nullable'] == 'NO' else ""
                col_defs.append(f"{col_name} {col_type} {null_part}")
            
            create_sql = f'CREATE TABLE IF NOT EXISTS "{tgt_t}" ({", ".join(col_defs)});'
            
            tgt_conn = psycopg2.connect(to_dsn); tgt_conn.autocommit = True
            with tgt_conn.cursor() as cur:
                cur.execute(create_sql)
            win.log(f"Schema for {tgt_t} verified/created.", "SUCCESS")

            src_conn = psycopg2.connect(from_dsn)
            with src_conn.cursor(name="prod_stream") as s_cur:
                s_cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(src_t)))
                total_migrated = 0
                while True:
                    rows = s_cur.fetchmany(10000)
                    if not rows: break
                    buf = io.StringIO()
                    writer = csv.writer(buf, delimiter='\t', lineterminator='\n')
                    writer.writerows(rows); buf.seek(0)
                    with tgt_conn.cursor() as t_cur:
                        t_cur.copy_expert(f'COPY "{tgt_t}" FROM STDIN WITH (FORMAT CSV, DELIMITER \'\t\', NULL \'\')', buf)
                    
                    total_migrated += len(rows)
                    elapsed = time.time() - start_time
                    speed = total_migrated / elapsed if elapsed > 0 else 0
                    rem = total_rows - total_migrated
                    eta_sec = rem / speed if speed > 0 else 0
                    eta_str = time.strftime('%H:%M:%S', time.gmtime(eta_sec))
                    
                    self.after(10, lambda c=total_migrated, s=speed, e=eta_str: win.update_status(c, s, e))
            
            win.log(f"All data for {src_t} migrated successfully!", "SUCCESS")
            self.after(1000, self.refresh_ui)
        except Exception as e:
            win.log(f"ERROR: {str(e)}", "ERROR")
            messagebox.showerror("Migration Failed", str(e))

if __name__ == "__main__":
    App().mainloop()