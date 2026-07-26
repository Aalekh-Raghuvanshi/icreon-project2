"""
JSON persistence for the repository index.

Usage::

    from pathlib import Path
    from ai_swe.indexer.persistence import save_index, load_index

    save_index(index, Path(".ai_swe_index.json"))
    loaded = load_index(Path(".ai_swe_index.json"))
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_swe.indexer.models import RepositoryIndex

logger = logging.getLogger(__name__)


def save_index(index: RepositoryIndex, output_path: Path) -> None:
    """
    Serialise *index* to JSON and write to *output_path*.

    The file is written atomically via a temporary sibling file to avoid
    leaving a half-written index if interrupted.

    Args:
        index:       The :class:`~ai_swe.indexer.models.RepositoryIndex` to save.
        output_path: Destination file path (will be created or overwritten).
    """
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file first, then rename (atomic on POSIX)
    tmp = output_path.with_suffix(".json.tmp")
    try:
        json_str = index.model_dump_json(indent=2)
        tmp.write_text(json_str, encoding="utf-8")
        tmp.replace(output_path)
        logger.info("Index saved to %s (%d bytes)", output_path, output_path.stat().st_size)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_index(path: Path) -> RepositoryIndex:
    """
    Load a :class:`~ai_swe.indexer.models.RepositoryIndex` from JSON.

    Args:
        path: Path to the JSON file previously written by :func:`save_index`.

    Returns:
        A validated ``RepositoryIndex`` instance.

    Raises:
        FileNotFoundError: If *path* does not exist.
        pydantic.ValidationError: If the JSON does not match the schema.
    """
    path = path.expanduser().resolve()
    raw = path.read_text(encoding="utf-8")
    return RepositoryIndex.model_validate_json(raw)


def index_exists(path: Path) -> bool:
    """Return True if an index file already exists at *path*."""
    return path.expanduser().resolve().is_file()
