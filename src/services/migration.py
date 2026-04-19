"""
Migration Service — Phase 4: Report + Pause/Resume
====================================================
New in Phase 4:
- MigrationReport: tracks per-table rows/duration/speed/errors
- Pause/Resume: workers check pause_event between chunks
- Table counters: live update after each table completes
- Final report displayed in log + saved to disk

Preserved from Phase 3:
- ThreadPoolExecutor parallel workers (MAX_WORKERS)
- Per-worker dedicated connection pair
- Schema queries reuse existing connection (no extra conn)
- Thread-safe checkpoint + UI updates
"""

import io
import csv
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2 import extras, sql
from tkinter import messagebox

from src.services.checkpoint import CheckpointManager
from src.services.retry import RETRYABLE_ERRORS
from src.services.schema_validator import validate_migration
from src.services.migration_report import MigrationReport
from src.ui.components.progress_window import MigrationProgressWindow

logger = logging.getLogger(__name__)

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "10000"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))


# ──────────────────────────────────────────────────────────
# Schema helpers (reuse an existing open connection)
# ──────────────────────────────────────────────────────────

def _fetch_columns_on_conn(conn, table_name: str) -> list:
    query = """
        SELECT
            column_name,
            CASE WHEN data_type = 'USER-DEFINED' THEN udt_name ELSE data_type END AS actual_type,
            is_nullable,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position;
    """
    with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
        cur.execute(query, (table_name,))
        return cur.fetchall()


def _fetch_constraints_on_conn(conn, table_name: str) -> dict:
    pk_query = """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position;
    """
    idx_query = """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = %s
          AND indexdef NOT LIKE '%_pkey%';
    """
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(pk_query, (table_name,))
            pks = [r["column_name"] for r in cur.fetchall()]
            cur.execute(idx_query, (table_name,))
            indexes = cur.fetchall()
        return {"primary_keys": pks, "indexes": list(indexes)}
    except psycopg2.Error as e:
        logger.warning("Constraint fetch failed for %s: %s", table_name, e)
        return {"primary_keys": [], "indexes": []}


# ──────────────────────────────────────────────────────────
# Column DDL builder
# ──────────────────────────────────────────────────────────

def _build_column_def(col: dict) -> str:
    col_type = col["actual_type"]
    if col["character_maximum_length"] and col_type in (
        "character varying", "varchar", "char", "character"
    ):
        col_type = f"{col_type}({col['character_maximum_length']})"
    elif col["numeric_precision"] and col_type in ("numeric", "decimal"):
        scale = col["numeric_scale"] or 0
        col_type = f"{col_type}({col['numeric_precision']},{scale})"
    null_clause = "NOT NULL" if col["is_nullable"] == "NO" else ""
    return f'"{col["column_name"]}" {col_type} {null_clause}'.strip()


# ──────────────────────────────────────────────────────────
# NULL-safe COPY chunk
# ──────────────────────────────────────────────────────────

def _copy_chunk(tgt_conn, table: str, col_names_quoted: str, rows: list) -> None:
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter="\t", lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL, escapechar="\\"
    )
    writer.writerows([["" if v is None else v for v in row] for row in rows])
    buf.seek(0)
    with tgt_conn.cursor() as cur:
        cur.copy_expert(
            f'COPY "{table}" ({col_names_quoted}) '
            f"FROM STDIN WITH (FORMAT CSV, DELIMITER '\t', NULL '', QUOTE '\"')",
            buf,
        )


# ──────────────────────────────────────────────────────────
# Core per-table migration
# ──────────────────────────────────────────────────────────

def _migrate_single_table(
    src_conn: psycopg2.extensions.connection,
    tgt_conn: psycopg2.extensions.connection,
    table: str,
    win: MigrationProgressWindow,
    pause_event: threading.Event,
) -> int:
    """
    Migrates one table.
    Phase 4 addition: checks pause_event between chunks.
    """
    win.log(f"📋 Schema → {table}")
    cols = _fetch_columns_on_conn(src_conn, table)
    if not cols:
        win.log(f"⚠️  No columns in '{table}' — skipping.", "ERROR")
        return 0

    constraints = _fetch_constraints_on_conn(src_conn, table)
    col_defs = [_build_column_def(c) for c in cols]
    col_names_quoted = ", ".join(f'"{c["column_name"]}"' for c in cols)
    sp_name = f"sp_{table[:28].replace('-','_').replace(' ','_')}"

    # ── Create table + PK ──
    with tgt_conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {sp_name}")
        try:
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)});'
            )
            if constraints["primary_keys"]:
                pk_cols = ", ".join(f'"{p}"' for p in constraints["primary_keys"])
                try:
                    cur.execute(f'ALTER TABLE "{table}" ADD PRIMARY KEY ({pk_cols});')
                except psycopg2.Error:
                    tgt_conn.rollback()
                    cur.execute(f"SAVEPOINT {sp_name}")
                    win.log(f"⚠️  PK already exists on '{table}' — skipped.", "ERROR")
        except psycopg2.Error as e:
            cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            raise RuntimeError(f"Schema DDL failed for '{table}': {e}") from e

    # ── Stream data ──
    rows_migrated = 0
    cursor_name = f"cur_{table[:28].replace('-','_').replace(' ','_')}"

    with src_conn.cursor(name=cursor_name) as s_cur:
        s_cur.itersize = CHUNK_SIZE
        s_cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))

        while True:
            rows = s_cur.fetchmany(CHUNK_SIZE)
            if not rows:
                break

            # ── Phase 4: Pause check (between chunks) ──
            if not pause_event.is_set():
                win.log(f"⏸ '{table}' waiting (paused)...")
                pause_event.wait()   # Blocks until Resume is clicked
                win.log(f"▶️  '{table}' resumed.")

            # Retry chunk on transient errors
            for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                try:
                    _copy_chunk(tgt_conn, table, col_names_quoted, rows)
                    tgt_conn.commit()
                    break
                except RETRYABLE_ERRORS as e:
                    if attempt == MAX_RETRY_ATTEMPTS:
                        raise
                    win.log(f"⟳ Chunk retry {attempt}/{MAX_RETRY_ATTEMPTS} '{table}': {e}", "ERROR")
                    time.sleep(2 ** attempt)

            rows_migrated += len(rows)

    # ── Apply indexes ──
    with tgt_conn.cursor() as cur:
        for idx in constraints["indexes"]:
            try:
                cur.execute(idx["indexdef"])
                tgt_conn.commit()
            except psycopg2.Error as e:
                tgt_conn.rollback()
                win.log(f"⚠️  Index '{idx['indexname']}' skipped: {e}", "ERROR")

    return rows_migrated


# ──────────────────────────────────────────────────────────
# Thread pool worker
# ──────────────────────────────────────────────────────────

def _worker_migrate_table(
    table: str,
    from_dsn: str,
    to_dsn: str,
    checkpoint: CheckpointManager,
    report: MigrationReport,
    progress: dict,
    progress_lock: threading.Lock,
    active_tables: list,
    active_lock: threading.Lock,
    counters: dict,
    counters_lock: threading.Lock,
    win: MigrationProgressWindow,
    pause_event: threading.Event,
) -> int:
    """
    Thread-pool worker. Phase 4 additions:
    - Records timing → report.record_success/failure()
    - Updates live table counter via win.update_table_counts()
    - Passes pause_event to table migration loop
    """
    # Register as active
    with active_lock:
        active_tables.append(table)
    win.set_active_tables(list(active_tables))

    # Start timing for report
    timer = report.start_table(table)
    rows_done = 0

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        src_conn = None
        tgt_conn = None
        try:
            src_conn = psycopg2.connect(from_dsn)
            src_conn.autocommit = True
            tgt_conn = psycopg2.connect(to_dsn)
            tgt_conn.autocommit = False

            rows_done = _migrate_single_table(src_conn, tgt_conn, table, win, pause_event)

            duration = timer()
            checkpoint.mark_completed(table)
            report.record_success(table, rows_done, duration)

            with progress_lock:
                progress["rows"] += rows_done

            # Update live counters
            with counters_lock:
                counters["done"] += 1
            win.update_table_counts(
                counters["total"], counters["done"], counters["failed"]
            )

            win.log(
                f"✅ '{table}' — {rows_done:,} rows in {duration:.1f}s "
                f"({rows_done/duration:,.0f} r/s)",
                "SUCCESS"
            )
            break

        except RETRYABLE_ERRORS as e:
            win.log(f"⟳ '{table}' attempt {attempt}/{MAX_RETRY_ATTEMPTS}: {e}", "ERROR")
            if attempt < MAX_RETRY_ATTEMPTS:
                time.sleep(2 ** attempt)
            else:
                duration = timer()
                checkpoint.mark_failed(table, str(e))
                report.record_failure(table, str(e), duration)
                with counters_lock:
                    counters["failed"] += 1
                win.update_table_counts(
                    counters["total"], counters["done"], counters["failed"]
                )
                raise

        except Exception as e:
            duration = timer()
            checkpoint.mark_failed(table, str(e))
            report.record_failure(table, str(e), duration)
            with counters_lock:
                counters["failed"] += 1
            win.update_table_counts(
                counters["total"], counters["done"], counters["failed"]
            )
            raise

        finally:
            for conn in (src_conn, tgt_conn):
                if conn and not conn.closed:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # Deregister from active
    with active_lock:
        if table in active_tables:
            active_tables.remove(table)
    win.set_active_tables(list(active_tables))

    return rows_done


# ──────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────

def run_bulk_migration(
    master_app,
    table_names: list[str],
    from_dsn: str,
    to_dsn: str,
    total_batch_rows: int,
):
    """
    Phase 4 orchestrator: validates → checkpoint → parallel execution
    → live counters → report generation.
    """
    win = MigrationProgressWindow(master_app, total_batch_rows)
    pause_event = win.pause_event  # Shared with Pause/Resume button
    start_time = time.time()

    # ── Pre-flight validation ──
    win.log("🔍 Pre-flight validation...")
    validation = validate_migration(from_dsn, to_dsn, table_names)

    if not validation.is_valid:
        for err in validation.errors:
            win.log(f"❌ {err}", "ERROR")
        messagebox.showerror(
            "Validation Failed",
            "Migration aborted:\n\n" + "\n".join(validation.errors)
        )
        return

    for w in validation.warnings:
        win.log(w, "ERROR")
    win.log("✅ Validation passed.", "SUCCESS")

    # ── Checkpoint ──
    existing_cp = CheckpointManager.find_resumable(table_names, from_dsn, to_dsn)
    if existing_cp:
        done_count = existing_cp.get_completed_count()
        resume = messagebox.askyesno(
            "Resume Migration?",
            f"Found incomplete migration (ID: {existing_cp.migration_id()}).\n"
            f"✅ Completed: {done_count}/{len(table_names)} tables.\n\nResume?"
        )
        checkpoint = existing_cp if resume else CheckpointManager.create_new(
            table_names, from_dsn, to_dsn
        )
        if resume:
            win.log(f"▶️  Resuming {checkpoint.migration_id()} ({done_count} tables already done).")
    else:
        checkpoint = CheckpointManager.create_new(table_names, from_dsn, to_dsn)

    remaining = checkpoint.get_remaining_tables()
    report = MigrationReport(checkpoint.migration_id(), table_names)

    win.after(0, lambda: win.set_session_id(checkpoint.migration_id(), MAX_WORKERS))
    win.log(
        f"📊 Remaining: {len(remaining)}/{len(table_names)} | "
        f"Workers: {MAX_WORKERS} | Chunk: {CHUNK_SIZE:,}"
    )

    # ── Shared state ──
    progress = {"rows": 0}
    progress_lock = threading.Lock()
    active_tables: list[str] = []
    active_lock = threading.Lock()
    counters = {"total": len(remaining), "done": 0, "failed": 0}
    counters_lock = threading.Lock()

    # Initial counter display
    win.after(0, lambda: win.update_table_counts(len(remaining), 0, 0))

    # ── Parallel execution ──
    win.log(f"🚀 Launching {MAX_WORKERS} parallel workers...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="migworker") as executor:
        futures = {
            executor.submit(
                _worker_migrate_table,
                table, from_dsn, to_dsn,
                checkpoint, report,
                progress, progress_lock,
                active_tables, active_lock,
                counters, counters_lock,
                win, pause_event,
            ): table
            for table in remaining
        }

        for future in as_completed(futures):
            table = futures[future]
            try:
                future.result()
            except Exception as e:
                win.log(f"❌ '{table}' → {e}", "ERROR")

            # Update progress bar
            elapsed = time.time() - start_time
            with progress_lock:
                rows_done = progress["rows"]
            speed = rows_done / elapsed if elapsed > 0 else 0
            eta_secs = (total_batch_rows - rows_done) / speed if speed > 0 else 0
            eta = time.strftime("%H:%M:%S", time.gmtime(eta_secs))
            win.after(
                10,
                lambda c=rows_done, s=speed, e=eta, t=table: win.update_status(c, s, e, t)
            )

    # ── Finalize report ──
    report.finalize()
    win.after(0, lambda: win.set_active_tables([]))

    failed = checkpoint.get_failed_tables()
    total_rows = progress["rows"]
    elapsed_total = time.time() - start_time
    avg_speed = total_rows / elapsed_total if elapsed_total > 0 else 0

    # Display report in log
    win.log("─" * 55)
    win.log(f"⏱  Duration: {time.strftime('%H:%M:%S', time.gmtime(elapsed_total))}")
    win.log(f"📦 Rows: {total_rows:,}  |  Avg: {avg_speed:,.0f} rows/s")

    # Auto-save report to disk
    try:
        json_path, csv_path = report.save_all()
        win.log(f"💾 Report: {json_path.name}", "SUCCESS")
        win.log(f"💾 CSV:    {csv_path.name}", "SUCCESS")
    except Exception as e:
        win.log(f"⚠️  Report save failed: {e}", "ERROR")

    # Enable Save Report button in UI
    win.after(0, lambda r=report: win.enable_save_report(r))

    if not failed:
        win.log("✨ ALL TABLES MIGRATED SUCCESSFULLY!", "SUCCESS")
        checkpoint.delete()
    else:
        failed_list = ", ".join(failed.keys())
        win.log(f"⚠️  {len(failed)} failed: {failed_list}", "ERROR")
        win.log("💾 Checkpoint saved — rerun to resume.", "ERROR")
        messagebox.showwarning(
            "Migration Partial",
            f"{len(table_names) - len(failed)} tables succeeded.\n"
            f"{len(failed)} failed:\n{failed_list}\n\nRerun to resume."
        )

    # Show full summary in log
    win.after(200, lambda: win.log(report.summary_text(), "SUCCESS"))
    win.after(1500, master_app.refresh_ui)
