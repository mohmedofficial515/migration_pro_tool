import psycopg2
from psycopg2 import extras

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
            SELECT 
                t.relname AS name, 
                COALESCE(NULLIF(s.n_live_tup, 0), GREATEST(CAST(c.reltuples AS BIGINT), 0), 0) AS rows,
                pg_size_pretty(pg_total_relation_size(t.relid)) AS size,
                pg_total_relation_size(t.relid) as bytes
            FROM pg_stat_user_tables s
            JOIN pg_class c ON s.relid = c.oid
            JOIN pg_stat_user_tables t ON s.relid = t.relid
            ORDER BY name ASC;
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
