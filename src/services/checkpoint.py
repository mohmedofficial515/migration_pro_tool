"""
Checkpoint Manager
==================
Saves migration progress to a JSON file after each table.
Allows resuming an interrupted migration from the last completed table.
Thread-safe: uses threading.Lock for parallel worker access.

Checkpoint file location: migration_checkpoints/<migration_id>.json
"""

import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path("migration_checkpoints")


def _dsn_hash(dsn: str) -> str:
    """Creates a short, non-reversible fingerprint of a DSN (hides credentials)."""
    return hashlib.sha256(dsn.encode()).hexdigest()[:12]


class CheckpointManager:
    """
    Manages a single migration session's checkpoint file.

    Usage:
        mgr = CheckpointManager.create_new(table_names, from_dsn, to_dsn)
        # or resume:
        mgr = CheckpointManager.find_resumable(table_names, from_dsn, to_dsn)

        mgr.mark_completed("users")
        mgr.mark_failed("orders", "timeout error")
        remaining = mgr.get_remaining_tables()
    """

    def __init__(self, state: dict, filepath: Path):
        self._state = state
        self._filepath = filepath
        self._lock = threading.Lock()  # Protects _state for concurrent worker access

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def create_new(
        cls,
        table_names: list[str],
        from_dsn: str,
        to_dsn: str,
    ) -> "CheckpointManager":
        """Creates a brand-new checkpoint for a migration session."""
        CHECKPOINT_DIR.mkdir(exist_ok=True)
        migration_id = str(uuid.uuid4())[:8]
        state = {
            "migration_id": migration_id,
            "from_dsn_hash": _dsn_hash(from_dsn),
            "to_dsn_hash": _dsn_hash(to_dsn),
            "table_names": list(table_names),
            "completed_tables": [],
            "failed_tables": {},       # table_name → last_error
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        filepath = CHECKPOINT_DIR / f"{migration_id}.json"
        mgr = cls(state, filepath)
        mgr._save()
        logger.info("Checkpoint created: %s", filepath)
        return mgr

    @classmethod
    def find_resumable(
        cls,
        table_names: list[str],
        from_dsn: str,
        to_dsn: str,
    ) -> "CheckpointManager | None":
        """
        Searches checkpoint_dir for an unfinished checkpoint that matches
        the same DSNs and table list. Returns None if nothing found.
        """
        if not CHECKPOINT_DIR.exists():
            return None

        from_hash = _dsn_hash(from_dsn)
        to_hash = _dsn_hash(to_dsn)
        target_tables = set(table_names)

        for cp_file in sorted(CHECKPOINT_DIR.glob("*.json"), reverse=True):
            try:
                with open(cp_file, encoding="utf-8") as f:
                    state = json.load(f)

                # Match same source/target and same table set
                if (
                    state.get("from_dsn_hash") == from_hash
                    and state.get("to_dsn_hash") == to_hash
                    and set(state.get("table_names", [])) == target_tables
                ):
                    remaining = cls._calc_remaining(state)
                    if remaining:  # Only resumable if there's something left
                        logger.info("Resumable checkpoint found: %s", cp_file)
                        return cls(state, cp_file)
            except (json.JSONDecodeError, KeyError):
                continue  # Corrupted checkpoint — skip

        return None

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    def mark_completed(self, table_name: str) -> None:
        """Marks a table as successfully migrated and persists to disk. Thread-safe."""
        with self._lock:
            if table_name not in self._state["completed_tables"]:
                self._state["completed_tables"].append(table_name)
            self._state["failed_tables"].pop(table_name, None)
            self._touch_and_save()

    def mark_failed(self, table_name: str, error: str) -> None:
        """Records a table migration failure. Thread-safe."""
        with self._lock:
            self._state["failed_tables"][table_name] = str(error)[:300]
            self._touch_and_save()

    def get_remaining_tables(self) -> list[str]:
        """Returns tables not yet completed (preserves original order). Thread-safe."""
        with self._lock:
            return self._calc_remaining(self._state)

    def get_completed_count(self) -> int:
        return len(self._state["completed_tables"])

    def get_failed_tables(self) -> dict[str, str]:
        return dict(self._state["failed_tables"])

    def migration_id(self) -> str:
        return self._state["migration_id"]

    def is_done(self) -> bool:
        """True when every table has been completed or explicitly failed."""
        completed = set(self._state["completed_tables"])
        failed = set(self._state["failed_tables"].keys())
        return set(self._state["table_names"]) <= (completed | failed)

    def delete(self) -> None:
        """Removes the checkpoint file after a clean full completion."""
        try:
            self._filepath.unlink(missing_ok=True)
            logger.info("Checkpoint %s deleted after full completion.", self._filepath)
        except OSError as e:
            logger.warning("Could not delete checkpoint: %s", e)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_remaining(state: dict) -> list[str]:
        completed = set(state.get("completed_tables", []))
        all_tables = state.get("table_names", [])
        return [t for t in all_tables if t not in completed]

    def _touch_and_save(self) -> None:
        self._state["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def _save(self) -> None:
        try:
            tmp = self._filepath.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            tmp.replace(self._filepath)  # Atomic write — prevents corrupted checkpoint on crash
        except OSError as e:
            logger.error("Failed to save checkpoint: %s", e)
