"""
Migration Report
================
Tracks per-table migration results (rows, duration, speed, errors).
Generates human-readable summary + JSON + CSV exports.

Output directory: migration_reports/
"""

import csv
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("migration_reports")


@dataclass
class TableResult:
    table_name: str
    status: str            # "success" | "failed" | "skipped"
    rows_migrated: int = 0
    duration_seconds: float = 0.0
    error: str | None = None

    @property
    def rows_per_second(self) -> float:
        return self.rows_migrated / self.duration_seconds if self.duration_seconds > 0 else 0.0


class MigrationReport:
    """
    Collects per-table results during migration and persists them on completion.

    Usage (in worker):
        timer = report.start_table("users")
        ...
        report.record_success("users", rows=50000, duration=timer())

    Usage (after migration):
        report.finalize()
        json_path = report.save_json()
        csv_path = report.save_csv()
        print(report.summary_text())
    """

    def __init__(self, session_id: str, table_names: list[str]):
        self.session_id = session_id
        self.table_names = list(table_names)
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self._results: dict[str, TableResult] = {}
        self._saved_paths: list[Path] = []

    # ─────────────────────────────────────────────────────
    # Recording API (thread-safe: each table written once)
    # ─────────────────────────────────────────────────────

    def start_table(self, table_name: str):
        """
        Returns a timer callable. Call it later to get elapsed seconds.
        Usage:
            timer = report.start_table("users")
            ... do work ...
            elapsed = timer()
        """
        t0 = time.monotonic()
        return lambda: time.monotonic() - t0

    def record_success(self, table: str, rows: int, duration: float) -> None:
        self._results[table] = TableResult(
            table_name=table, status="success",
            rows_migrated=rows, duration_seconds=round(duration, 3)
        )

    def record_failure(self, table: str, error: str, duration: float) -> None:
        self._results[table] = TableResult(
            table_name=table, status="failed",
            duration_seconds=round(duration, 3),
            error=str(error)[:500]
        )

    def finalize(self) -> None:
        """Call once after all tables are processed."""
        self.finished_at = datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────
    # Computed properties
    # ─────────────────────────────────────────────────────

    @property
    def total_rows(self) -> int:
        return sum(r.rows_migrated for r in self._results.values())

    @property
    def total_duration(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    @property
    def success_count(self) -> int:
        return sum(1 for r in self._results.values() if r.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self._results.values() if r.status == "failed")

    @property
    def avg_speed(self) -> float:
        return self.total_rows / self.total_duration if self.total_duration > 0 else 0.0

    # ─────────────────────────────────────────────────────
    # Summary text
    # ─────────────────────────────────────────────────────

    def summary_text(self) -> str:
        dur = time.strftime("%H:%M:%S", time.gmtime(self.total_duration))
        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            f"  MIGRATION REPORT — Session: {self.session_id}",
            "╠══════════════════════════════════════════════════════════════╣",
            f"  Started:   {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"  Finished:  {self.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC') if self.finished_at else 'in progress'}",
            f"  Duration:  {dur}",
            "──────────────────────────────────────────────────────────────",
            f"  Tables:    {len(self.table_names):,} total",
            f"  ✅ Success: {self.success_count:,}",
            f"  ❌ Failed:  {self.failed_count:,}",
            f"  📦 Rows:    {self.total_rows:,}",
            f"  🚀 Speed:   {self.avg_speed:,.0f} rows/s (avg)",
            "──────────────────────────────────────────────────────────────",
        ]

        # Failed tables
        failed = [r for r in self._results.values() if r.status == "failed"]
        if failed:
            lines.append("  FAILED TABLES:")
            for r in failed:
                lines.append(f"    ❌ {r.table_name}")
                lines.append(f"       Error: {r.error}")
            lines.append("")

        # Top 10 largest tables by row count
        success_sorted = sorted(
            [r for r in self._results.values() if r.status == "success"],
            key=lambda x: x.rows_migrated, reverse=True
        )
        if success_sorted:
            lines.append("  TOP TABLES BY SIZE:")
            for r in success_sorted[:10]:
                lines.append(
                    f"    ✅ {r.table_name[:40]:<40} "
                    f"{r.rows_migrated:>10,} rows  "
                    f"{r.duration_seconds:>7.1f}s  "
                    f"{r.rows_per_second:>9,.0f} r/s"
                )

        lines.append("╚══════════════════════════════════════════════════════════════╝")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────

    def save_json(self, output_dir: Path = REPORTS_DIR) -> Path:
        """Saves full report as JSON. Returns the file path."""
        output_dir.mkdir(exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"report_{self.session_id}_{ts}.json"

        data = {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_duration_seconds": round(self.total_duration, 2),
            "summary": {
                "total_tables": len(self.table_names),
                "success": self.success_count,
                "failed": self.failed_count,
                "total_rows": self.total_rows,
                "avg_speed_rows_per_sec": round(self.avg_speed, 0),
            },
            "tables": [
                {
                    "table": r.table_name,
                    "status": r.status,
                    "rows_migrated": r.rows_migrated,
                    "duration_seconds": r.duration_seconds,
                    "rows_per_second": round(r.rows_per_second, 0),
                    "error": r.error,
                }
                for r in self._results.values()
            ],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._saved_paths.append(filepath)
        logger.info("Report saved: %s", filepath)
        return filepath

    def save_csv(self, output_dir: Path = REPORTS_DIR) -> Path:
        """Saves per-table results as CSV. Returns the file path."""
        output_dir.mkdir(exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"report_{self.session_id}_{ts}.csv"

        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "table_name", "status", "rows_migrated",
                "duration_seconds", "rows_per_second", "error"
            ])
            for r in self._results.values():
                writer.writerow([
                    r.table_name, r.status, r.rows_migrated,
                    round(r.duration_seconds, 2),
                    round(r.rows_per_second, 0),
                    r.error or "",
                ])

        self._saved_paths.append(filepath)
        logger.info("CSV report saved: %s", filepath)
        return filepath

    def save_all(self) -> tuple[Path, Path]:
        """Saves both JSON and CSV reports. Returns (json_path, csv_path)."""
        return self.save_json(), self.save_csv()
