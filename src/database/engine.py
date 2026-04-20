import logging
import psycopg2
from psycopg2 import extras, sql

logger = logging.getLogger(__name__)

# Connection timeout in seconds — prevents terminal from freezing when
# a DB host is unreachable (e.g. localhost:5432 not running).
_CONNECT_TIMEOUT = 5


def _connect(dsn: str) -> psycopg2.extensions.connection:
    """
    Wraps psycopg2.connect with a hard connect_timeout.
    Raises psycopg2.OperationalError within 5 s if host is unreachable.
    """
    return psycopg2.connect(dsn, connect_timeout=_CONNECT_TIMEOUT)


class DatabaseEngine:

    @staticmethod
    def get_db_info(dsn: str) -> dict | None:
        """Returns version and size of the database. Returns None on failure."""
        try:
            with _connect(dsn) as conn:
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
    def get_schemas(dsn: str) -> list[str]:
        """
        Returns all non-system schemas in the database sorted alphabetically.
        Falls back to ['public'] on failure.
        """
        query = """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND schema_name NOT LIKE 'pg_temp_%'
              AND schema_name NOT LIKE 'pg_toast_temp_%'
            ORDER BY schema_name;
        """
        try:
            with _connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    return [r[0] for r in cur.fetchall()] or ["public"]
        except psycopg2.Error as e:
            logger.error("get_schemas failed: %s", e)
            return ["public"]

    @staticmethod
    def get_tables_stats(dsn: str, schema: str = "public") -> list:
        """
        Returns stats for all user tables in the given schema.
        Returns [] on failure.
        """
        query = """
            SELECT DISTINCT ON (s.relname)
                s.relname AS name,
                COALESCE(NULLIF(s.n_live_tup, 0), GREATEST(CAST(c.reltuples AS BIGINT), 0), 0) AS rows,
                pg_size_pretty(pg_total_relation_size(s.relid)) AS size,
                pg_total_relation_size(s.relid) AS bytes
            FROM pg_stat_user_tables s
            JOIN pg_class c ON s.relid = c.oid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
            ORDER BY s.relname ASC;
        """
        try:
            with _connect(dsn) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(query, (schema,))
                    data = cur.fetchall()
            return data
        except psycopg2.Error as e:
            logger.error("get_tables_stats failed: %s", e)
            return []

    @staticmethod
    def get_precise_schema(dsn: str, table_name: str, schema: str = "public") -> list:
        """
        Returns column definitions for the given table in the given schema.
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
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position;
        """
        with _connect(dsn) as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, (schema, table_name))
                cols = cur.fetchall()
        return cols

    @staticmethod
    def get_table_constraints(dsn: str, table_name: str, schema: str = "public") -> dict:
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
            with _connect(dsn) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(pk_query, (schema, table_name))
                    pks = [r["column_name"] for r in cur.fetchall()]
                    cur.execute(idx_query, (schema, table_name))
                    indexes = cur.fetchall()
            return {"primary_keys": pks, "indexes": indexes}
        except psycopg2.Error as e:
            logger.error("get_table_constraints failed for %s: %s", table_name, e)
            return {"primary_keys": [], "indexes": []}
