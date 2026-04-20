"""
chat_history.py
===============
Persistent per-table chat history for the Ollama Chat Window.

Each table gets its own JSON file:
  <project_root>/data/chat_history/<schema>__<table>.json

File format:
  {
    "schema": "public",
    "table":  "Transportation",
    "created_at": "...",
    "updated_at": "...",
    "sessions": [
      {
        "session_id": "...",
        "started_at": "...",
        "label":      "Session 1",
        "messages":   [{"role": "user|assistant", "content": "..."}]
      }
    ]
  }
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path


# Max messages kept per session in the saved file (trim oldest)
_MAX_MSG_PER_SESSION = 200
# Max sessions per table history file
_MAX_SESSIONS = 10
# Summary injected into the AI context from previous sessions
_MAX_HISTORY_MSG_FOR_CONTEXT = 30


class ChatHistory:
    """Manages persistent chat history for a single schema.table pair."""

    def __init__(self, project_root: str, schema: str, table: str) -> None:
        self.schema     = schema
        self.table      = table
        self._path      = self._build_path(project_root, schema, table)
        self._data      = self._load()
        self._session   = self._new_session()
        # Add fresh session to history
        self._data["sessions"].append(self._session)
        self._trim_sessions()

    # ── Path ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_path(root: str, schema: str, table: str) -> Path:
        safe_schema = _safe_name(schema)
        safe_table  = _safe_name(table)
        folder = Path(root) / "data" / "chat_history"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{safe_schema}__{safe_table}.json"

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data.get("sessions"), list):
                        return data
            except Exception:
                pass
        return {
            "schema":     self.schema,
            "table":      self.table,
            "created_at": _now(),
            "updated_at": _now(),
            "sessions":   [],
        }

    def save(self) -> None:
        """Persist current state to disk (call after every AI message)."""
        self._data["updated_at"] = _now()
        # Trim oversized session
        msgs = self._session.get("messages", [])
        if len(msgs) > _MAX_MSG_PER_SESSION:
            self._session["messages"] = msgs[-_MAX_MSG_PER_SESSION:]
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass   # non-critical — never crash the UI

    # ── Session Management ────────────────────────────────────────────────────

    def _new_session(self) -> dict:
        sessions = self._data.get("sessions", [])
        idx = len(sessions) + 1
        return {
            "session_id": str(uuid.uuid4()),
            "started_at": _now(),
            "label":      f"Session {idx}",
            "messages":   [],
        }

    def _trim_sessions(self) -> None:
        """Keep only the last _MAX_SESSIONS sessions."""
        sessions = self._data["sessions"]
        if len(sessions) > _MAX_SESSIONS:
            self._data["sessions"] = sessions[-_MAX_SESSIONS:]

    def start_new_session(self) -> None:
        """Create a brand-new session (New Chat button)."""
        self._session = self._new_session()
        self._data["sessions"].append(self._session)
        self._trim_sessions()

    # ── Message Operations ────────────────────────────────────────────────────

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the current session."""
        self._session["messages"].append({
            "role":       role,
            "content":    content,
            "timestamp":  _now(),
        })

    @property
    def current_messages(self) -> list[dict]:
        """Return messages in current session (for passing to Ollama API)."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self._session.get("messages", [])
        ]

    # ── History Context for AI ────────────────────────────────────────────────

    def build_history_context(self) -> str:
        """
        Build a context block summarizing previous sessions.
        Injected into the system prompt so the AI understands past work.
        """
        all_sessions = self._data.get("sessions", [])
        # Exclude current (last) session — that's the live one
        prev_sessions = all_sessions[:-1]
        if not prev_sessions:
            return ""

        lines = [
            "=" * 60,
            "PREVIOUS WORK HISTORY ON THIS TABLE (READ-ONLY CONTEXT)",
            f"Table: \"{self.schema}\".\"{self.table}\"",
            f"Total past sessions: {len(prev_sessions)}",
            "=" * 60,
        ]

        # Collect last N messages across all previous sessions (newest last)
        all_prev_msgs: list[dict] = []
        for sess in prev_sessions:
            for m in sess.get("messages", []):
                all_prev_msgs.append({
                    "session": sess["label"],
                    "role":    m["role"],
                    "content": m["content"],
                })

        # Take last _MAX_HISTORY_MSG_FOR_CONTEXT
        tail = all_prev_msgs[-_MAX_HISTORY_MSG_FOR_CONTEXT:]

        if tail:
            lines.append("")
            lines.append(f"Last {len(tail)} messages from previous sessions:")
            for m in tail:
                role_label = "USER" if m["role"] == "user" else "AI"
                snippet    = m["content"][:300].replace("\n", " ")
                if len(m["content"]) > 300:
                    snippet += "..."
                lines.append(f"  [{m['session']}] {role_label}: {snippet}")

        lines += [
            "",
            "END OF HISTORY — Current session starts now.",
            "=" * 60,
        ]
        return "\n".join(lines)

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    def total_sessions(self) -> int:
        return len(self._data.get("sessions", []))

    @property
    def has_history(self) -> bool:
        """True if there are previous sessions with messages."""
        sessions = self._data.get("sessions", [])
        # More than 1 session OR current session already has messages
        prev = sessions[:-1] if sessions else []
        return any(s.get("messages") for s in prev)

    @property
    def session_label(self) -> str:
        return self._session.get("label", "Session 1")

    @property
    def updated_at(self) -> str:
        return self._data.get("updated_at", "")

    @property
    def all_session_labels(self) -> list[str]:
        return [s.get("label", "?") for s in self._data.get("sessions", [])]


# ── Utils ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(s: str) -> str:
    """Convert schema/table name to safe filename component."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
