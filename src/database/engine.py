import logging
import psycopg2
from psycopg2 import extras, sql

logger = logging.getLogger(__name__)


class DatabaseEngine:

    @staticmethod
    def get_db_info(dsn: str) -> dict | None:
        """Returns version and size of the database. Returns None on failure."""
        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT version();")
                    ver = cur.fetchone()["version"].split(" on ")[0]
                    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
                    size = cur.fetchone()["pg_size_pretty"]
            return {"ver": ver, "size": size}
        except psycopg2.Error as e:
            logger.error("get_db_info failed: %s", e)
            return None

    @staticmethod
    def get_tables_stats(dsn: str) -> list:
        """Returns stats for all user tables. Returns [] on failure."""
        query = """
            SELECT
                t.relname AS name,
                COALESCE(NULLIF(s.n_live_tup, 0), GREATEST(CAST(c.reltuples AS BIGINT), 0), 0) AS rows,
                pg_size_pretty(pg_total_relation_size(t.relid)) AS size,
                pg_total_relation_size(t.relid) AS bytes
            FROM pg_stat_user_tables s
            JOIN pg_class c ON s.relid = c.oid
            JOIN pg_stat_user_tables t ON s.relid = t.relid
            ORDER BY name ASC;
        """
        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(query)
                    data = cur.fetchall()
            return data
        except psycopg2.Error as e:
            logger.error("get_tables_stats failed: %s", e)
            return []

    @staticmethod
    def get_precise_schema(dsn: str, table_name: str) -> list:
        """
        Returns column definitions for the given table.
        Uses parametrized query to prevent SQL Injection.
        """
        query = """
            SELECT
                column_name,
                CASE
                    WHEN data_type = 'USER-DEFINED' THEN udt_name
                    ELSE data_type
                END AS actual_type,
                is_nullable,
                column_default,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            ORDER BY ordinal_position;
        """
        # FIX: Use parametrized query — table_name is passed as a parameter,
        # NOT interpolated into the string. This completely prevents SQL Injection.
        with psycopg2.connect(dsn) as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, (table_name,))
                cols = cur.fetchall()
        return cols

    @staticmethod
    def get_table_constraints(dsn: str, table_name: str) -> dict:
        """
        Returns primary keys, unique constraints, and indexes for a table.
        Used to recreate the full schema on the target DB.
        """
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
            with psycopg2.connect(dsn) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(pk_query, (table_name,))
                    pks = [r["column_name"] for r in cur.fetchall()]
                    cur.execute(idx_query, (table_name,))
                    indexes = cur.fetchall()
            return {"primary_keys": pks, "indexes": indexes}
        except psycopg2.Error as e:
            logger.error("get_table_constraints failed for %s: %s", table_name, e)
            return {"primary_keys": [], "indexes": []}
