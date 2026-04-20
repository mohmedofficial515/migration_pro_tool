"""
Table Inspector — Database Query Layer
=======================================
Provides rich metadata and per-column analytics for a single table.
All queries are parametrized and schema-aware.

Functions:
  get_full_table_info(dsn, table, schema)                         -> dict
  get_column_analytics(dsn, table, schema, col_name, col_type)   -> dict
  get_columns_quick_stats(dsn, table, schema)                     -> dict[col, stats]
"""

import logging
import psycopg2
from psycopg2 import extras

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5


def _connect(dsn: str):
    return psycopg2.connect(dsn, connect_timeout=_CONNECT_TIMEOUT)


# ─────────────────────────────────────────────────────────────
# Full table metadata
# ─────────────────────────────────────────────────────────────

def get_full_table_info(dsn: str, table: str, schema: str = "public") -> dict:
    """
    Returns a comprehensive dict with:
      - columns     : list of column dicts (name, type, nullable, default, ordinal)
      - primary_keys: set of column names that are PKs
      - foreign_keys: dict  column → {ref_table, ref_column}
      - unique_cols : set of column names covered by a UNIQUE constraint
      - indexed_cols: dict  column → list of index names
      - stats       : {rows, size_bytes, size_pretty}
    Returns empty structure on failure.
    """
    result = {
        "columns":      [],
        "primary_keys": set(),
        "foreign_keys": {},
        "unique_cols":  set(),
        "indexed_cols": {},
        "stats":        {"rows": 0, "size_bytes": 0, "size_pretty": "—"},
    }

    try:
        with _connect(dsn) as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:

                # ── 1. Columns ──
                cur.execute("""
                    SELECT
                        ordinal_position,
                        column_name,
                        CASE
                            WHEN data_type = 'USER-DEFINED' THEN udt_name
                            ELSE data_type
                        END AS data_type,
                        is_nullable,
                        column_default,
                        character_maximum_length,
                        numeric_precision,
                        numeric_scale
                    FROM information_schema.columns
                    WHERE LOWER(table_schema) = LOWER(%s)
                      AND LOWER(table_name)   = LOWER(%s)
                    ORDER BY ordinal_position;
                """, (schema, table))
                result["columns"] = [dict(r) for r in cur.fetchall()]

                # Fallback via pg_attribute if information_schema returned nothing
                if not result["columns"]:
                    cur.execute("""
                        SELECT
                            a.attnum          AS ordinal_position,
                            a.attname         AS column_name,
                            pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                            CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
                            pg_get_expr(d.adbin, d.adrelid) AS column_default,
                            NULL::int AS character_maximum_length,
                            NULL::int AS numeric_precision,
                            NULL::int AS numeric_scale
                        FROM pg_catalog.pg_attribute   a
                        JOIN pg_catalog.pg_class        c ON c.oid = a.attrelid
                        JOIN pg_catalog.pg_namespace    n ON n.oid = c.relnamespace
                        LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid
                                                         AND d.adnum   = a.attnum
                        WHERE LOWER(n.nspname) = LOWER(%s)
                          AND LOWER(c.relname) = LOWER(%s)
                          AND a.attnum > 0
                          AND NOT a.attisdropped
                        ORDER BY a.attnum;
                    """, (schema, table))
                    result["columns"] = [dict(r) for r in cur.fetchall()]

                # ── 2. Primary Keys ──
                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND LOWER(tc.table_schema) = LOWER(%s)
                      AND LOWER(tc.table_name)   = LOWER(%s);
                """, (schema, table))
                result["primary_keys"] = {r["column_name"] for r in cur.fetchall()}

                # ── 3. Foreign Keys ──
                cur.execute("""
                    SELECT
                        kcu.column_name,
                        ccu.table_name  AS ref_table,
                        ccu.column_name AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                     AND tc.table_schema    = ccu.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND LOWER(tc.table_schema) = LOWER(%s)
                      AND LOWER(tc.table_name)   = LOWER(%s);
                """, (schema, table))
                for r in cur.fetchall():
                    result["foreign_keys"][r["column_name"]] = {
                        "ref_table":  r["ref_table"],
                        "ref_column": r["ref_column"],
                    }

                # ── 4. Unique Constraints ──
                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    WHERE tc.constraint_type = 'UNIQUE'
                      AND LOWER(tc.table_schema) = LOWER(%s)
                      AND LOWER(tc.table_name)   = LOWER(%s);
                """, (schema, table))
                result["unique_cols"] = {r["column_name"] for r in cur.fetchall()}

                # ── 5. Indexes (non-PK) ──
                cur.execute("""
                    SELECT
                        ix.indexname,
                        ix.indexdef
                    FROM pg_indexes ix
                    WHERE LOWER(ix.schemaname) = LOWER(%s)
                      AND LOWER(ix.tablename)  = LOWER(%s)
                      AND ix.indexdef NOT LIKE '%%_pkey%%';
                """, (schema, table))
                for r in cur.fetchall():
                    # Extract column names from indexdef heuristically
                    idx_def  = r["indexdef"]
                    idx_name = r["indexname"]
                    # Columns are inside the last (...)
                    try:
                        cols_part = idx_def[idx_def.rindex("(") + 1: idx_def.rindex(")")]
                        for col in [c.strip().strip('"') for c in cols_part.split(",")]:
                            result["indexed_cols"].setdefault(col, []).append(idx_name)
                    except (ValueError, IndexError):
                        pass

                # ── 6. Table Stats ──
                cur.execute("""
                    SELECT
                        COALESCE(
                            NULLIF(s.n_live_tup, 0),
                            GREATEST(CAST(c.reltuples AS BIGINT), 0),
                            0
                        ) AS rows,
                        pg_total_relation_size(s.relid)                    AS size_bytes,
                        pg_size_pretty(pg_total_relation_size(s.relid))    AS size_pretty
                    FROM pg_stat_user_tables s
                    JOIN pg_class     c ON s.relid    = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE LOWER(n.nspname) = LOWER(%s)
                      AND LOWER(s.relname) = LOWER(%s);
                """, (schema, table))
                row = cur.fetchone()
                if row:
                    result["stats"] = {
                        "rows":        int(row["rows"]),
                        "size_bytes":  int(row["size_bytes"]),
                        "size_pretty": row["size_pretty"],
                    }

    except psycopg2.Error as e:
        logger.error("get_full_table_info failed for %s.%s: %s", schema, table, e)

    return result


# ─────────────────────────────────────────────────────────────
# Per-column analytics  (lazy — called only when column selected)
# ─────────────────────────────────────────────────────────────

_NUMERIC_TYPES = {
    "integer", "bigint", "smallint", "numeric", "decimal",
    "real", "double precision", "serial", "bigserial",
}
_DATE_TYPES = {
    "date", "timestamp", "timestamp without time zone",
    "timestamp with time zone", "timestamptz",
}


def get_column_analytics(
    dsn: str,
    table: str,
    schema: str,
    col_name: str,
    col_type: str,
) -> dict:
    """
    Returns analytics for a single column:
      - null_count   : int
      - distinct_count: int
      - min_val, max_val: str | None  (numeric / date columns only)
      - top_values  : list of {"value": str, "count": int, "pct": float}
                      Top 5 most frequent values
    Heavy query — runs in a background thread in the UI.
    """
    analytics = {
        "null_count":    0,
        "distinct_count": 0,
        "min_val":    None,
        "max_val":    None,
        "top_values": [],
        "error":      None,
    }

    col_type_lower = col_type.lower()
    is_numeric = any(t in col_type_lower for t in _NUMERIC_TYPES)
    is_date    = any(t in col_type_lower for t in _DATE_TYPES)

    try:
        with _connect(dsn) as conn:
            with conn.cursor() as cur:

                # Null + distinct counts
                cur.execute(f"""
                    SELECT
                        COUNT(*) FILTER (WHERE "{col_name}" IS NULL)       AS null_count,
                        COUNT(DISTINCT "{col_name}")                        AS distinct_count
                    FROM "{schema}"."{table}";
                """)
                row = cur.fetchone()
                if row:
                    analytics["null_count"]     = int(row[0])
                    analytics["distinct_count"] = int(row[1])

                # Min / Max  (numeric & date only)
                if is_numeric or is_date:
                    cur.execute(f"""
                        SELECT
                            MIN("{col_name}")::text,
                            MAX("{col_name}")::text
                        FROM "{schema}"."{table}";
                    """)
                    row = cur.fetchone()
                    if row:
                        analytics["min_val"] = row[0]
                        analytics["max_val"] = row[1]

                # Top 5 most frequent values
                cur.execute(f"""
                    SELECT
                        "{col_name}"::text AS val,
                        COUNT(*)           AS cnt
                    FROM "{schema}"."{table}"
                    WHERE "{col_name}" IS NOT NULL
                    GROUP BY "{col_name}"
                    ORDER BY cnt DESC
                    LIMIT 5;
                """)
                total = analytics["distinct_count"] or 1
                rows  = cur.fetchall()
                # Compute total rows for percentage
                cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}";')
                total_rows = cur.fetchone()[0] or 1

                analytics["top_values"] = [
                    {
                        "value": str(r[0]),
                        "count": int(r[1]),
                        "pct":   round(int(r[1]) / total_rows * 100, 1),
                    }
                    for r in rows
                ]

    except psycopg2.Error as e:
        analytics["error"] = str(e)
        logger.error("get_column_analytics failed for %s.%s.%s: %s",
                     schema, table, col_name, e)

    return analytics


# ─────────────────────────────────────────────────────────────
# Batch column quick-stats via pg_stats  (zero full-table scan)
# ─────────────────────────────────────────────────────────────

def get_columns_quick_stats(
    dsn: str,
    table: str,
    schema: str = "public",
) -> dict:
    """
    Returns pre-computed per-column statistics sourced from pg_stats.
    These are updated by PostgreSQL's autovacuum/ANALYZE and require
    NO full table scan — they are metadata reads only.

    Returns: dict mapping column_name → {
        "null_pct"      : float   (0.0 – 100.0),
        "n_distinct"    : int | None  (None if unknown / fractional estimate),
        "distinct_label": str     (human-readable estimate string),
        "avg_width"     : int     (average byte width of the column),
        "sample_values" : list of {"value": str, "pct": float},
        "has_stats"     : bool    (False if ANALYZE has never run on this column),
    }
    Columns not found in pg_stats (e.g. never analyzed) get has_stats=False.
    """
    result: dict = {}

    try:
        with _connect(dsn) as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:

                cur.execute("""
                    SELECT
                        attname               AS column_name,
                        null_frac             AS null_frac,
                        n_distinct            AS n_distinct,
                        most_common_vals      AS mcv,
                        most_common_freqs     AS mcf,
                        avg_width             AS avg_width,
                        correlation           AS correlation
                    FROM pg_stats
                    WHERE LOWER(schemaname) = LOWER(%s)
                      AND LOWER(tablename)  = LOWER(%s)
                    ORDER BY attname;
                """, (schema, table))

                for row in cur.fetchall():
                    col      = row["column_name"]
                    nf       = float(row["null_frac"] or 0)
                    nd       = row["n_distinct"]
                    mcv_raw  = row["mcv"]    # list[str] or None
                    mcf_raw  = row["mcf"]    # list[float] or None
                    avg_w    = int(row["avg_width"] or 0)
                    corr     = row["correlation"]

                    # Human-readable distinct estimate
                    if nd is None:
                        distinct_label = "unknown"
                    elif nd >= 0:
                        distinct_label = f"{int(nd):,} distinct"
                    else:
                        # Negative = fraction of total rows
                        pct = round(abs(float(nd)) * 100, 1)
                        distinct_label = f"~{pct}% of rows"

                    # Sample values from most_common_vals
                    samples: list = []
                    if mcv_raw and mcf_raw:
                        for val, freq in zip(mcv_raw[:5], mcf_raw[:5]):
                            samples.append({
                                "value": str(val) if val is not None else "NULL",
                                "pct":   round(float(freq) * 100, 1),
                            })

                    result[col] = {
                        "null_pct":       round(nf * 100, 1),
                        "n_distinct":     int(nd) if (nd is not None and nd >= 0) else None,
                        "distinct_label": distinct_label,
                        "avg_width":      avg_w,
                        "correlation":    round(float(corr), 3) if corr is not None else None,
                        "sample_values":  samples,
                        "has_stats":      True,
                    }

    except psycopg2.Error as e:
        logger.error("get_columns_quick_stats failed for %s.%s: %s", schema, table, e)

    return result

