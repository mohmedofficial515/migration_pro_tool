"""
Migration Service — Phase 5: Schema-Aware + Copy/Move
======================================================
New in Phase 5:
- src_schema / tgt_schema parameters propagate through the entire call chain
- Qualified table names ("schema"."table") in CREATE TABLE, COPY, SELECT
- move_mode=True → after successful migration, DROP TABLE from source schema
- Preserved all Phase 4 features: MigrationReport, Pause/Resume, live counters,
  ThreadPoolExecutor parallel workers, retry, checkpoint
"""

import io
import csv
import json
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

# Import TableConfig (defined in table_inspector to avoid circular imports)
# We use TYPE_CHECKING to keep it clean
try:
    from src.ui.components.table_inspector import TableConfig
except ImportError:
    TableConfig = None  # type: ignore

logger = logging.getLogger(__name__)

CHUNK_SIZE         = int(os.getenv("CHUNK_SIZE", "10000"))
MAX_WORKERS        = int(os.getenv("MAX_WORKERS", "4"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))


# ──────────────────────────────────────────────────────────
# Schema helpers (reuse an existing open connection)
# ──────────────────────────────────────────────────────────

def _fetch_columns_on_conn(conn, table_name: str, schema: str = "public") -> list:
    query = """
        SELECT
            column_name,
            CASE WHEN data_type = 'USER-DEFINED' THEN udt_name ELSE data_type END AS actual_type,
            is_nullable,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position;
    """
    with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
        cur.execute(query, (schema, table_name))
        return cur.fetchall()


def _fetch_constraints_on_conn(conn, table_name: str, schema: str = "public") -> dict:
    pk_query = """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position;
    """
    idx_query = """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s
          AND tablename = %s
          AND indexdef NOT LIKE '%%_pkey%%';
    """
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(pk_query, (schema, table_name))
            pks = [r["column_name"] for r in cur.fetchall()]
            cur.execute(idx_query, (schema, table_name))
            indexes = cur.fetchall()
        return {"primary_keys": pks, "indexes": list(indexes)}
    except psycopg2.Error as e:
        logger.warning("Constraint fetch failed for %s.%s: %s", schema, table_name, e)
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


def _copy_chunk(
    tgt_conn,
    schema: str,
    table: str,
    col_names_quoted: str,
    rows: list,
) -> None:
    """Fast COPY FROM STDIN — used when target table is freshly created."""
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter="\t", lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL, escapechar="\\"
    )
    
    processed_rows = []
    for row in rows:
        new_row = []
        for v in row:
            if v is None:
                new_row.append("")
            elif isinstance(v, (dict, list)):
                new_row.append(json.dumps(v))
            else:
                new_row.append(v)
        processed_rows.append(new_row)
        
    writer.writerows(processed_rows)
    buf.seek(0)
    with tgt_conn.cursor() as cur:
        cur.copy_expert(
            f'COPY "{schema}"."{table}" ({col_names_quoted}) '
            f"FROM STDIN WITH (FORMAT CSV, DELIMITER '\t', NULL '', QUOTE '\"')",
            buf,
        )


def _upsert_chunk(
    tgt_conn,
    schema: str,
    table: str,
    col_names: list[str],
    pk_cols: list[str],
    rows: list,
) -> tuple[int, int]:
    """
    INSERT ... ON CONFLICT (pk_cols) DO UPDATE SET col=EXCLUDED.col
    Returns (inserted_new, updated_existing) counts.
    """
    if not rows:
        return 0, 0

    # Build: INSERT INTO "schema"."table" (c1,c2,...)
    #        VALUES (%s,%s,...)
    #        ON CONFLICT (pk1,pk2) DO UPDATE SET c1=EXCLUDED.c1, c2=EXCLUDED.c2
    #        WHERE NOT ("schema"."table" IS NOT DISTINCT FROM EXCLUDED)
    quoted_cols   = ", ".join(f'"{c}"' for c in col_names)
    placeholders  = ", ".join(["%s"] * len(col_names))
    conflict_cols = ", ".join(f'"{c}"' for c in pk_cols)
    # Only update non-PK columns
    update_cols   = [c for c in col_names if c not in pk_cols]

    if update_cols:
        update_set = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
        on_conflict = f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_set}"
    else:
        # Only PK columns — nothing to update, just skip duplicates
        on_conflict = f"ON CONFLICT ({conflict_cols}) DO NOTHING"

    # Use xmax to detect if it was an update (xmax != 0) or insert (xmax = 0)
    stmt = (
        f'INSERT INTO "{schema}"."{table}" ({quoted_cols}) '
        f"VALUES ({placeholders}) "
        f"{on_conflict} "
        f'RETURNING (xmax = 0) AS is_insert'
    )

    # Process rows to adapt dict/list to Json
    processed_rows = []
    for row in rows:
        new_row = []
        for v in row:
            if isinstance(v, (dict, list)):
                new_row.append(extras.Json(v))
            else:
                new_row.append(v)
        processed_rows.append(tuple(new_row))

    inserted = 0
    updated  = 0
    with tgt_conn.cursor() as cur:
        for row in processed_rows:
            cur.execute(stmt, row)
            result = cur.fetchone()
            if result and result[0]:
                inserted += 1
            else:
                updated += 1

    return inserted, updated


def _check_target_table_exists(
    tgt_conn, schema: str, table: str
) -> bool:
    """Return True if the table already exists in the target schema."""
    with tgt_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        return cur.fetchone() is not None


# ──────────────────────────────────────────────────────────
# Core per-table migration (schema-aware)
# ──────────────────────────────────────────────────────────

def _migrate_single_table(
    src_conn: psycopg2.extensions.connection,
    tgt_conn: psycopg2.extensions.connection,
    table: str,
    win: MigrationProgressWindow,
    pause_event: threading.Event,
    src_schema: str = "public",
    tgt_schema: str = "public",
    target_name: str | None = None,
    column_renames: dict | None = None,
    selected_columns: list | None = None,
    progress: dict | None = None,
    progress_lock: threading.Lock | None = None,
    total_batch_rows: int = 0,
    is_retry: bool = False,
) -> int:
    """
    Migrates one table from src_schema.table → tgt_schema.target_name.
    - target_name:       override the table name in the target schema
    - column_renames:    dict {original_col → new_col} applied only in target DDL
    - selected_columns:  list of source column names to include (None = all)
    - is_retry:          True if this is a retry attempt from the outer loop
    """
    target_name      = target_name or table
    column_renames   = column_renames or {}
    selected_columns = selected_columns or None  # None means all

    win.log(f"📋 {src_schema}.{table} ➜ {tgt_schema}.{target_name}")
    cols = _fetch_columns_on_conn(src_conn, table, src_schema)
    if not cols:
        win.log(f"⚠️  No columns in '{src_schema}.{table}' — skipping.", "ERROR")
        return 0

    # Filter to selected columns only (preserve original ordering)
    if selected_columns is not None:
        sel_set = set(selected_columns)
        cols = [c for c in cols if c["column_name"] in sel_set]
        if not cols:
            win.log(f"⚠️  No selected columns remain for '{table}' — skipping.", "ERROR")
            return 0
        win.log(f"🔍  Migrating {len(cols)} of {len(selected_columns)} selected columns.")

    constraints = _fetch_constraints_on_conn(src_conn, table, src_schema)

    # Build column DDL — apply renames for target column names
    col_defs = []
    for c in cols:
        orig_name = c["column_name"]
        new_name  = column_renames.get(orig_name, orig_name)
        # Temporarily override column_name for DDL builder
        col_copy = dict(c)
        col_copy["column_name"] = new_name
        col_defs.append(_build_column_def(col_copy))

    # COPY uses target column names (positional insert, same order)
    col_names_quoted = ", ".join(
        f'"{column_renames.get(c["column_name"], c["column_name"])}"'
        for c in cols
    )
    sp_name = f"sp_{target_name[:28].replace('-','_').replace(' ','_')}"

    # ── Create target schema if not exists ──
    with tgt_conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(tgt_schema))
        )

    # ── Create table + PK in target schema (uses target_name + renamed columns) ──
    with tgt_conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {sp_name}")
        try:
            cur.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {}.{} ({})"
                ).format(
                    sql.Identifier(tgt_schema),
                    sql.Identifier(target_name),
                    sql.SQL(", ".join(col_defs)),
                )
            )
            if constraints["primary_keys"]:
                # PK columns also need renaming if they were renamed
                pk_cols = ", ".join(
                    f'"{column_renames.get(p, p)}"'
                    for p in constraints["primary_keys"]
                )
                try:
                    cur.execute(
                        sql.SQL(
                            "ALTER TABLE {}.{} ADD PRIMARY KEY ({})"
                        ).format(
                            sql.Identifier(tgt_schema),
                            sql.Identifier(target_name),
                            sql.SQL(pk_cols),
                        )
                    )
                except psycopg2.Error:
                    tgt_conn.rollback()
                    cur.execute(f"SAVEPOINT {sp_name}")
                    win.log(f"⚠️  PK already exists on '{tgt_schema}.{target_name}' — skipped.", "ERROR")
        except psycopg2.Error as e:
            cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            raise RuntimeError(f"Schema DDL failed for '{tgt_schema}.{target_name}': {e}") from e

    rows_migrated = 0
    upsert_inserted = 0
    upsert_updated  = 0

    # Detect if target already exists → switch to UPSERT mode
    tgt_exists   = _check_target_table_exists(tgt_conn, tgt_schema, target_name)
    pk_cols_tgt  = [
        column_renames.get(p, p) for p in constraints["primary_keys"]
    ]  # renamed PK columns for the target side
    use_upsert   = tgt_exists and bool(pk_cols_tgt)

    if tgt_exists:
        if use_upsert:
            win.log(
                f"🔄 [MERGE] '{tgt_schema}.{target_name}' exists — "
                f"using UPSERT (ON CONFLICT DO UPDATE) on PK: {pk_cols_tgt}"
            )
        else:
            win.log(
                f"⚠️  [APPEND] '{tgt_schema}.{target_name}' exists but has no PK — "
                f"appending all rows (duplicates possible).",
                "ERROR",
            )

    # ── Intelligent Retry / Duplicates Prevention ──
    # If the connection drops during a No-PK table, restarting will append rows again.
    # We truncate it here before selecting from source to ensure a clean slate.
    if is_retry and not use_upsert:
        win.log(f"🧹 Retrying table without PK — Truncating '{tgt_schema}.{target_name}' to prevent duplicates.", "ERROR")
        try:
            with tgt_conn.cursor() as cur:
                cur.execute(
                    sql.SQL("TRUNCATE TABLE {}.{}").format(
                        sql.Identifier(tgt_schema), sql.Identifier(target_name)
                    )
                )
            tgt_conn.commit()
            win.log("✅ Truncate successful. Resuming clean copy.")
        except psycopg2.Error as e:
            tgt_conn.rollback()
            win.log(f"⚠️  Truncate failed: {e}", "ERROR")

    # Target column names list (for UPSERT mode)
    tgt_col_names = [
        column_renames.get(c["column_name"], c["column_name"]) for c in cols
    ]

    cursor_name = f"cur_{table[:28].replace('-','_').replace(' ','_')}"

    with src_conn.cursor(name=cursor_name) as s_cur:
        s_cur.itersize = CHUNK_SIZE
        if selected_columns is not None:
            # SELECT only chosen columns by name
            src_col_list = sql.SQL(", ").join(
                sql.Identifier(c["column_name"]) for c in cols
            )
            s_cur.execute(
                sql.SQL("SELECT {} FROM {}.{}").format(
                    src_col_list,
                    sql.Identifier(src_schema),
                    sql.Identifier(table),
                )
            )
        else:
            s_cur.execute(
                sql.SQL("SELECT * FROM {}.{}").format(
                    sql.Identifier(src_schema),
                    sql.Identifier(table),
                )
            )

        _worker_start = time.time()  # local timer for per-chunk speed calc
        while True:
            rows = s_cur.fetchmany(CHUNK_SIZE)
            if not rows:
                break

            # ── Pause check (between chunks) ──
            if not pause_event.is_set():
                win.log(f"⏸ '{table}' waiting (paused)...")
                pause_event.wait()
                win.log(f"▶️  '{table}' resumed.")

            # Retry chunk on transient errors
            for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                try:
                    # Detect dead connections immediately to avoid wasting time retrying a dead socket
                    if tgt_conn.closed != 0 or src_conn.closed != 0:
                        raise psycopg2.OperationalError("Connection structurally closed.")

                    if use_upsert:
                        ins, upd = _upsert_chunk(
                            tgt_conn, tgt_schema, target_name,
                            tgt_col_names, pk_cols_tgt, list(rows)
                        )
                        upsert_inserted += ins
                        upsert_updated  += upd
                    else:
                        _copy_chunk(tgt_conn, tgt_schema, target_name, col_names_quoted, rows)
                    tgt_conn.commit()
                    break
                except RETRYABLE_ERRORS as e:
                    if attempt == MAX_RETRY_ATTEMPTS:
                        raise
                    tgt_conn.rollback()
                    win.log(f"⟳ Chunk retry {attempt}/{MAX_RETRY_ATTEMPTS} '{table}': {e}", "ERROR")
                    time.sleep(2 ** attempt)

            rows_migrated += len(rows)

            # ── Real-time progress update per chunk ──────────────────
            with progress_lock:
                progress["rows"] += len(rows)
                rows_so_far = progress["rows"]
            elapsed_now = time.time() - _worker_start
            speed_now   = rows_so_far / elapsed_now if elapsed_now > 0 else 0
            _remaining  = max(0, int(total_batch_rows) - int(rows_so_far))
            eta_secs    = _remaining / speed_now if speed_now > 0 else 0
            eta_now     = time.strftime("%H:%M:%S", time.gmtime(eta_secs))
            win.after(
                0,
                lambda c=rows_so_far, s=speed_now, e=eta_now, t=table:
                    win.update_status(c, s, e, t)
            )

    # ── Apply indexes in target schema ──
    with tgt_conn.cursor() as cur:
        for idx in constraints["indexes"]:
            try:
                orig_idx_name = idx["indexname"]
                idx_def = idx["indexdef"]

                # 1. Replace source schema with target schema
                idx_def = idx_def.replace(
                    f'"{src_schema}".', f'"{tgt_schema}".'
                ).replace(
                    f' {src_schema}.', f' {tgt_schema}.'
                )

                # 2. Replace original table name with actual target table name
                #    (critical when copying to a renamed table like Transportation125)
                if target_name and target_name != table:
                    idx_def = idx_def.replace(
                        f'"{table}"', f'"{target_name}"'
                    ).replace(
                        f' {table} ', f' {target_name} '
                    )
                    # Rename the index itself to avoid conflicts with original
                    new_idx_name = f"{orig_idx_name}_{target_name[:20]}"
                    idx_def = idx_def.replace(
                        f"INDEX {orig_idx_name} ON",
                        f"INDEX IF NOT EXISTS {new_idx_name} ON",
                    ).replace(
                        f"INDEX CONCURRENTLY {orig_idx_name} ON",
                        f"INDEX CONCURRENTLY IF NOT EXISTS {new_idx_name} ON",
                    )

                cur.execute(idx_def)
                tgt_conn.commit()
                win.log(f"✅  Index '{new_idx_name if target_name and target_name != table else orig_idx_name}' created.", "SUCCESS")
            except psycopg2.Error as e:
                tgt_conn.rollback()
                win.log(f"⚠️  Index '{idx['indexname']}' skipped: {e}", "ERROR")

    if use_upsert:
        win.log(
            f"📊 [MERGE SUMMARY] '{tgt_schema}.{target_name}' — "
            f"➕ {upsert_inserted:,} new rows inserted,  "
            f"✏️  {upsert_updated:,} existing rows updated.",
            "SUCCESS",
        )
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
    src_schema: str = "public",
    tgt_schema: str = "public",
    move_mode: bool = False,
    target_name: str | None = None,
    column_renames: dict | None = None,
    selected_columns: list | None = None,
    total_batch_rows: int = 0,
) -> int:
    """
    Thread-pool worker.
    - Supports src_schema / tgt_schema for schema-aware migration
    - target_name / column_renames from Inspector overrides
    - move_mode=True → DROP TABLE from source after successful migration
    """
    display_name = target_name or table

    # Register as active
    with active_lock:
        active_tables.append(display_name)
    win.set_active_tables(list(active_tables))

    timer     = report.start_table(table)
    rows_done = 0

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        src_conn = None
        tgt_conn = None
        try:
            src_conn = psycopg2.connect(from_dsn)
            src_conn.autocommit = False   # Named cursor requires a transaction (autocommit=False)
            tgt_conn = psycopg2.connect(to_dsn)
            tgt_conn.autocommit = False

            rows_done = _migrate_single_table(
                src_conn, tgt_conn, table, win, pause_event,
                src_schema       = src_schema,
                tgt_schema       = tgt_schema,
                target_name      = target_name,
                column_renames   = column_renames,
                selected_columns = selected_columns,
                progress         = progress,
                progress_lock    = progress_lock,
                total_batch_rows = total_batch_rows,
                is_retry         = (attempt > 1),
            )

            duration = timer()
            checkpoint.mark_completed(table)
            report.record_success(table, rows_done, duration)

            # progress["rows"] is already updated per-chunk inside _migrate_single_table

            # Update live counters
            with counters_lock:
                counters["done"] += 1
            win.update_table_counts(
                counters["total"], counters["done"], counters["failed"]
            )

            win.log(
                f"✅ '{display_name}' — {rows_done:,} rows in {duration:.1f}s "
                f"({rows_done/duration if duration > 0 else 0:,.0f} r/s)",
                "SUCCESS",
            )

            # ── Auto-create Workspace (_tmp) — structure only, NO data copy ──
            # Rationale: copying all data here would double storage (8 GB → 16 GB).
            # The workspace is created as an empty shell with full schema + indexes.
            # AI chat populates it lazily (via _run_sql_on_copy) only when a
            # WRITE/DANGER query is requested — and only if the workspace is still empty.
            try:
                tgt_conn.autocommit = True
                tmp_name = f"{display_name}_tmp"
                with tgt_conn.cursor() as cur:
                    # Drop any stale workspace from a previous session
                    cur.execute(
                        sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                            sql.Identifier(tgt_schema),
                            sql.Identifier(tmp_name),
                        )
                    )
                    # Create structure + indexes (INCLUDING ALL) — zero rows
                    cur.execute(
                        sql.SQL("CREATE TABLE {}.{} (LIKE {}.{} INCLUDING ALL)").format(
                            sql.Identifier(tgt_schema),
                            sql.Identifier(tmp_name),
                            sql.Identifier(tgt_schema),
                            sql.Identifier(display_name),
                        )
                    )
                win.log(
                    f"🟢 Workspace '{tgt_schema}.{tmp_name}' ready (structure-only, empty).",
                    "SUCCESS",
                )
                tgt_conn.autocommit = False
            except psycopg2.Error as e:
                win.log(f"⚠️  Workspace init failed for '{display_name}': {e}", "ERROR")

            # ── Move mode: drop source table after successful copy ──
            if move_mode:
                try:
                    src_conn.autocommit = True   # DDL needs autocommit; set before opening cursor
                    with src_conn.cursor() as cur:
                        cur.execute(
                            sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                                sql.Identifier(src_schema),
                                sql.Identifier(table),
                            )
                        )
                    win.log(f"✂️  '{src_schema}.{table}' dropped (Move mode).", "SUCCESS")
                except psycopg2.Error as e:
                    win.log(f"⚠️  Drop failed for '{src_schema}.{table}': {e}", "ERROR")

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
        if display_name in active_tables:
            active_tables.remove(display_name)
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
    total_batch_rows: int | float,
    src_schema: str = "public",
    tgt_schema: str = "public",
    move_mode: bool = False,
    table_configs: list | None = None,
):
    """
    Phase 5+ orchestrator:
    validates → checkpoint → parallel execution → live counters → report.
    Supports src_schema/tgt_schema, move_mode, and per-table inspector configs
    (target_name override + column renames from TableInspectorFlow).
    """
    # Build a fast lookup: original_table_name → TableConfig
    cfg_map: dict = {}
    if table_configs:
        for cfg in table_configs:
            cfg_map[cfg.original_name] = cfg
    win = MigrationProgressWindow(master_app, total_batch_rows)
    pause_event = win.pause_event
    start_time  = time.time()

    # Count skipped tables (excluded by user in Inspector)
    skipped_names = [
        cfg.original_name for cfg in (table_configs or []) if cfg.skipped
    ]

    mode_label = "✂️ MOVE" if move_mode else "🔁 COPY"
    win.log(
        f"{mode_label} | {src_schema} ➜ {tgt_schema} | "
        f"{len(table_names)} tables"
        + (f"  |  ⏭ {len(skipped_names)} skipped" if skipped_names else "")
    )

    # Log each skipped table explicitly in the progress window
    if skipped_names:
        win.log(f"⏭  Skipped tables ({len(skipped_names)}):")
        for name in skipped_names:
            win.log(f"       • {name}", "ERROR")

    # ── Pre-flight validation ──
    win.log("🔍 Pre-flight validation...")
    validation = validate_migration(
        from_dsn, to_dsn, table_names,
        src_schema=src_schema,
        tgt_schema=tgt_schema,
    )

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
    report    = MigrationReport(checkpoint.migration_id(), table_names)

    win.after(0, lambda: win.set_session_id(checkpoint.migration_id(), MAX_WORKERS))
    win.log(
        f"📊 Remaining: {len(remaining)}/{len(table_names)} | "
        f"Workers: {MAX_WORKERS} | Chunk: {CHUNK_SIZE:,}"
    )

    # ── Shared state ──
    progress       = {"rows": 0}
    progress_lock  = threading.Lock()
    active_tables: list[str] = []
    active_lock    = threading.Lock()
    counters       = {"total": len(remaining), "done": 0, "failed": 0}
    counters_lock  = threading.Lock()
    # Shared start-time reference accessible inside worker closures
    _start_ref     = [start_time]

    total_tables   = len(remaining)
    win.after(0, lambda: win.update_table_counts(total_tables, 0, 0))

    # ── Parallel execution ──
    win.log(f"🚀 Launching {MAX_WORKERS} parallel workers...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="migworker") as executor:
        futures = {}
        for table in remaining:
            cfg = cfg_map.get(table)
            tgt_name        = cfg.target_name      if cfg else None
            col_renames     = cfg.column_renames   if cfg else None
            sel_cols        = cfg.selected_columns if cfg else None

            futures[executor.submit(
                _worker_migrate_table,
                table, from_dsn, to_dsn,
                checkpoint, report,
                progress, progress_lock,
                active_tables, active_lock,
                counters, counters_lock,
                win, pause_event,
                src_schema, tgt_schema, move_mode,
                tgt_name, col_renames, sel_cols,
                total_batch_rows,
            )] = table

        for future in as_completed(futures):
            table = futures[future]
            try:
                future.result()
            except Exception as e:
                win.log(f"❌ '{table}' → {e}", "ERROR")

            # ── Update table-based progress bar on each table completion ──
            with counters_lock:
                done_so_far   = counters["done"]
                failed_so_far = counters["failed"]
            elapsed = time.time() - start_time
            with progress_lock:
                rows_done = progress["rows"]
            speed    = rows_done / elapsed if elapsed > 0 else 0
            _remaining_rows = max(0, int(total_batch_rows) - int(rows_done))
            eta_secs = _remaining_rows / speed if speed > 0 else 0
            eta      = time.strftime("%H:%M:%S", time.gmtime(eta_secs))
            win.after(
                0,
                lambda c=rows_done, s=speed, e=eta, t=table: win.update_status(c, s, e, t)
            )
            win.after(
                0,
                lambda tot=total_tables, d=done_so_far, f=failed_so_far:
                    win.update_table_counts(tot, d, f)
            )

    # ── Finalize report ──
    report.finalize()
    win.after(0, lambda: win.set_active_tables([]))

    failed        = checkpoint.get_failed_tables()
    total_rows    = progress["rows"]
    elapsed_total = time.time() - start_time
    avg_speed     = total_rows / elapsed_total if elapsed_total > 0 else 0

    win.log("─" * 55)
    win.log(f"⏱  Duration: {time.strftime('%H:%M:%S', time.gmtime(elapsed_total))}")
    win.log(f"📦 Rows: {total_rows:,}  |  Avg: {avg_speed:,.0f} rows/s")

    try:
        json_path, csv_path = report.save_all()
        win.log(f"💾 Report: {json_path.name}", "SUCCESS")
        win.log(f"💾 CSV:    {csv_path.name}", "SUCCESS")
    except Exception as e:
        win.log(f"⚠️  Report save failed: {e}", "ERROR")

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

    win.after(200, lambda: win.log(report.summary_text(), "SUCCESS"))
    # Auto-reconnect: refresh the main panels 2s after migration finishes
    # so connection counters and table lists update automatically
    win.after(2000, _safe_refresh(master_app))


def _safe_refresh(app):
    """Returns a lambda that safely calls refresh_ui even if the app was closed."""
    def _do():
        try:
            if app.winfo_exists():
                app.refresh_ui()
        except Exception:
            pass
    return _do
