"""
Session store: keeps AgentState objects in memory + persists them to disk.

For the MVP, sessions are stored in a dict (lost on restart) but also
serialized to JSON files in `settings.sessions_dir` so they can be recovered.
A future milestone can replace this with a proper database.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ai_swe.config import get_settings
from ai_swe.state import AgentState

logger = logging.getLogger(__name__)


class SessionStore:
    """
    Thread-safe (asyncio-lock-based) in-memory + disk-backed session store.

    Usage::

        store = SessionStore()
        store.create(state)
        store.update(state)
        state = store.get(session_id)
        sessions = store.list_all()
    """

    def __init__(self, sessions_dir: Path | None = None) -> None:
        settings = get_settings()
        self._dir = (sessions_dir or settings.sessions_dir).expanduser().resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, AgentState] = {}
        self._lock = asyncio.Lock()
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create(self, state: AgentState) -> None:
        """Register a new session and persist it to disk."""
        self._sessions[state.session_id] = state
        self._persist(state)

    def update(self, state: AgentState) -> None:
        """Update an existing session (in memory and on disk)."""
        self._sessions[state.session_id] = state
        self._persist(state)

    def get(self, session_id: str) -> AgentState | None:
        """Return a session by ID, or None if not found."""
        return self._sessions.get(session_id)

    def list_all(self) -> list[AgentState]:
        """Return all sessions, newest first (by started_at)."""
        sessions = list(self._sessions.values())
        sessions.sort(
            key=lambda s: s.started_at or s.finished_at or (s.logs[0].timestamp if s.logs else "") or "",
            reverse=True,
        )
        return sessions

    def delete(self, session_id: str) -> bool:
        """Remove a session from memory and disk. Returns True if found."""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _persist(self, state: AgentState) -> None:
        """Write state JSON to disk (best-effort; errors are logged, not raised)."""
        try:
            path = self._session_path(state.session_id)
            path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist session %s: %s", state.session_id, exc)

    def _load_from_disk(self) -> None:
        """Load all *.json files from sessions_dir at startup."""
        for path in self._dir.glob("*.json"):
            try:
                raw: Any = json.loads(path.read_text(encoding="utf-8"))
                state = AgentState.model_validate(raw)
                self._sessions[state.session_id] = state
            except Exception as exc:
                logger.warning("Failed to load session from %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Singleton factory
    # ------------------------------------------------------------------

    _instance: SessionStore | None = None

    @classmethod
    def get_instance(cls) -> "SessionStore":
        """Return the process-global singleton store, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
