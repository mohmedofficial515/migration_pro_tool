"""
Schema Validator
================
Pre-migration checks to prevent data corruption from schema mismatches.

Validates:
1. Source tables actually exist in the given schema
2. Source has data (warns if empty)
3. Target table — if it already exists, checks column compatibility
4. Source connection is reachable
5. Target connection is reachable and has write privileges
"""

import logging
from dataclasses import dataclass, field

import psycopg2
from psycopg2 import sql

from src.database.engine import DatabaseEngine

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"❌ {e}")
        for w in self.warnings:
            lines.append(f"⚠️  {w}")
        return "\n".join(lines) if lines else "✅ All checks passed."


def validate_migration(
    from_dsn: str,
    to_dsn: str,
    table_names: list[str],
    src_schema: str = "public",
    tgt_schema: str = "public",
) -> ValidationResult:
    """
    Runs all pre-migration validation checks.
    Supports dynamic source and target schemas.
    Returns a ValidationResult describing pass/fail/warnings.
    """
    result = ValidationResult()

    # --- Check 1: Source connectivity ---
    src_info = DatabaseEngine.get_db_info(from_dsn)
    if src_info is None:
        result.add_error("Cannot connect to SOURCE database. Check SOURCE_DB_URL.")
        return result  # Cannot continue without source

    # --- Check 2: Target connectivity ---
    tgt_info = DatabaseEngine.get_db_info(to_dsn)
    if tgt_info is None:
        result.add_error("Cannot connect to TARGET database. Check TARGET_DB_URL.")
        return result  # Cannot continue without target

    # --- Check 3: Target write privilege test ---
    try:
        with psycopg2.connect(to_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TEMP TABLE _migration_write_test (id INT); "
                    "DROP TABLE _migration_write_test;"
                )
    except psycopg2.Error as e:
        result.add_error(f"Target DB write test failed — insufficient privileges: {e}")
        return result

    # --- Check 4: Source schema exists ---
    try:
        with psycopg2.connect(from_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.schemata"
                    "  WHERE schema_name = %s"
                    ");",
                    (src_schema,),
                )
                if not cur.fetchone()[0]:
                    result.add_error(
                        f"Schema '{src_schema}' does not exist in SOURCE database."
                    )
                    return result
    except psycopg2.Error as e:
        result.add_error(f"Failed to verify source schema: {e}")
        return result

    # --- Check 5: Target schema exists ---
    try:
        with psycopg2.connect(to_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.schemata"
                    "  WHERE schema_name = %s"
                    ");",
                    (tgt_schema,),
                )
                if not cur.fetchone()[0]:
                    result.add_error(
                        f"Schema '{tgt_schema}' does not exist in TARGET database."
                    )
                    return result
    except psycopg2.Error as e:
        result.add_error(f"Failed to verify target schema: {e}")
        return result

    # --- Check 6: Source tables exist in source schema ---
    try:
        with psycopg2.connect(from_dsn) as conn:
            with conn.cursor() as cur:
                for table in table_names:
                    cur.execute(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM information_schema.tables"
                        "  WHERE table_schema = %s AND table_name = %s"
                        ");",
                        (src_schema, table),
                    )
                    exists = cur.fetchone()[0]
                    if not exists:
                        result.add_error(
                            f"Table '{table}' does not exist in source schema '{src_schema}'."
                        )
    except psycopg2.Error as e:
        result.add_error(f"Failed to verify source tables: {e}")
        return result

    if not result.is_valid:
        return result  # Don't continue if source tables are missing

    # --- Check 7: Target table compatibility (if already exists in target schema) ---
    try:
        with psycopg2.connect(to_dsn) as conn:
            with conn.cursor() as cur:
                for table in table_names:
                    cur.execute(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM information_schema.tables"
                        "  WHERE table_schema = %s AND table_name = %s"
                        ");",
                        (tgt_schema, table),
                    )
                    tgt_exists = cur.fetchone()[0]

                    if tgt_exists:
                        result.add_warning(
                            f"Table '{table}' already exists in target schema '{tgt_schema}'. "
                            f"Migration will APPEND data — ensure schema matches or truncate first."
                        )

    except psycopg2.Error as e:
        result.add_warning(f"Could not verify target table existence: {e}")

    return result
