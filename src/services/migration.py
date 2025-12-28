import time
import io
import csv
import psycopg2
from psycopg2 import sql
from tkinter import messagebox
from src.database.engine import DatabaseEngine
from src.ui.components.progress_window import MigrationProgressWindow

def run_bulk_migration(master_app, table_names, from_dsn, to_dsn, total_batch_rows):
    win = MigrationProgressWindow(master_app, total_batch_rows)
    start_time = time.time()
    batch_migrated = 0
    
    try:
        src_conn = psycopg2.connect(from_dsn)
        tgt_conn = psycopg2.connect(to_dsn); tgt_conn.autocommit = True
        
        for table in table_names:
            win.log(f"Processing table: {table}")
            cols = DatabaseEngine.get_precise_schema(from_dsn, table)
            
            # Create Schema
            col_defs = [f"{sql.Identifier(c['column_name']).as_string(tgt_conn)} {c['actual_type']} {'NOT NULL' if c['is_nullable']=='NO' else ''}" for c in cols]
            with tgt_conn.cursor() as cur:
                cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)});')
            
            # Data Streaming
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
                    win.after(10, lambda c=batch_migrated, s=speed, e=eta, t=table: win.update_status(c, s, e, t))
            
            win.log(f"Table {table} done.", "SUCCESS")
        
        win.log("✨ ALL TABLES MIGRATED!", "SUCCESS")
        win.after(1000, master_app.refresh_ui)
    except Exception as e:
        win.log(f"FATAL ERROR: {str(e)}", "ERROR")
        messagebox.showerror("Bulk Migration Failed", str(e))
