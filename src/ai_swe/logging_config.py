"""
Logging configuration for the AI SWE Agent.

We use Python's standard `logging` module (no extra dependency needed) with:
  * A human-friendly, colorized console handler for local development (via `rich`).
  * An optional single-line JSON formatter for production, so logs are easy to
    ship to log aggregators (CloudWatch, Datadog, ELK, etc).

Call `configure_logging()` once, as early as possible (e.g. at the top of
`main.py` or app startup) before any other module logs anything.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
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
