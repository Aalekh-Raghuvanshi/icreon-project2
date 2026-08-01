"""
Logging configuration for the AI SWE Agent.

We use Python's standard `logging` module (no extra dependency needed) with:
  * A human-friendly, colorized console handler for local development (via `rich`).
  * An optional single-line JSON formatter for production, so logs are easy to
    ship to log aggregators (CloudWatch, Datadog, ELK, etc).
  * A `StructuredInteractionLogger` that persists per-session JSONL files so
    every agent interaction is auditable (powers the Logs & Reasoning UI page).

Call `configure_logging()` once, as early as possible (e.g. at the top of
`main.py` or app startup) before any other module logs anything.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.logging import RichHandler

from ai_swe.config import get_settings


class _JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Include any extra fields passed via `logger.info(..., extra={...})`.
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__ and key != "message":
                payload.setdefault(key, value)
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None, json_output: bool | None = None) -> None:
    """
    Configure the root logger for the whole application.

    Args:
        level: Log level name (e.g. "DEBUG", "INFO"). Falls back to settings.
        json_output: Whether to emit structured JSON logs. Falls back to settings.
    """
    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    resolved_json = settings.log_json if json_output is None else json_output

    root = logging.getLogger()
    root.setLevel(resolved_level)

    # Remove any pre-existing handlers to keep `configure_logging` idempotent
    # (important because CLI entry points and tests may call it more than once).
    for existing_handler in list(root.handlers):
        root.removeHandler(existing_handler)

    handler: logging.Handler
    if resolved_json:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_JSONFormatter())
    else:
        handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    root.addHandler(handler)

    # Quiet down noisy third-party libraries unless we're debugging.
    if resolved_level != "DEBUG":
        for noisy_logger in ("httpx", "httpcore", "asyncio", "mcp"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper around `logging.getLogger`, kept for discoverability."""
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Structured interaction logger (per-session JSONL files)
# ---------------------------------------------------------------------------


class StructuredInteractionLogger:
    """
    Persists one JSONL file per session under ``log_dir/<session_id>/interactions.jsonl``.

    Each line is a JSON object representing a single agent interaction::

        {
            "timestamp": "...",
            "session_id": "...",
            "agent": "planner",
            "message": "...",
            "level": "info",
            "input_summary": "...",
            "decision": "...",
            "output_summary": "...",
            "execution_time_ms": 1234,
            "input_tokens": 500,
            "output_tokens": 200,
            "estimated_cost_usd": 0.0042
        }

    This file is read by the ``/api/sessions/{id}/logs`` endpoint and surfaced
    in the Logs & Reasoning page of the web UI.
    """

    def __init__(self, session_id: str, log_dir: Path | None = None) -> None:
        settings = get_settings()
        self._session_id = session_id
        root = (log_dir or settings.log_dir).expanduser().resolve()
        session_dir = root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._path = session_dir / "interactions.jsonl"
        self._file = self._path.open("a", encoding="utf-8")

    def log(
        self,
        *,
        agent: str,
        message: str,
        level: str = "info",
        input_summary: str | None = None,
        decision: str | None = None,
        output_summary: str | None = None,
        execution_time_ms: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        """Write one interaction record to the JSONL file."""
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self._session_id,
            "agent": agent,
            "message": message,
            "level": level,
        }
        if input_summary is not None:
            record["input_summary"] = input_summary
        if decision is not None:
            record["decision"] = decision
        if output_summary is not None:
            record["output_summary"] = output_summary
        if execution_time_ms is not None:
            record["execution_time_ms"] = execution_time_ms
        if input_tokens:
            record["input_tokens"] = input_tokens
        if output_tokens:
            record["output_tokens"] = output_tokens
        if estimated_cost_usd:
            record["estimated_cost_usd"] = estimated_cost_usd

        self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "StructuredInteractionLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def read_session_logs(session_id: str, log_dir: Path | None = None) -> list[dict[str, Any]]:
    """
    Read all interaction log records for a session from disk.

    Returns an empty list if no log file exists yet.
    """
    settings = get_settings()
    root = (log_dir or settings.log_dir).expanduser().resolve()
    path = root / session_id / "interactions.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records
