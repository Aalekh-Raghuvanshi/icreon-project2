"""
JSON persistence for implementation plans.

Plans are saved as pretty-printed JSON for easy inspection and version
control.  The default location is ``<repo_root>/.ai_swe_plan.json``.

Usage::

    from pathlib import Path
    from ai_swe.agents.plan_persistence import save_plan, load_plan

    save_plan(plan, Path("./my_repo"))
    loaded = load_plan(Path("./my_repo/.ai_swe_plan.json"))
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai_swe.agents.planner_models import ImplementationPlan

logger = logging.getLogger(__name__)

# Default filename, written into the repository root
DEFAULT_PLAN_FILENAME = ".ai_swe_plan.json"


def _resolve_output_path(
    output_path: Path | None = None,
    repo_path: Path | None = None,
) -> Path:
    """
    Determine where to write the plan JSON.

    Priority:
      1. Explicit ``output_path`` if provided.
      2. ``<repo_path>/<DEFAULT_PLAN_FILENAME>`` if ``repo_path`` is given.
      3. ``./DEFAULT_PLAN_FILENAME`` as a last resort.
    """
    if output_path is not None:
        return output_path.expanduser().resolve()
    if repo_path is not None:
        return (repo_path / DEFAULT_PLAN_FILENAME).expanduser().resolve()
    return Path(DEFAULT_PLAN_FILENAME).resolve()


def save_plan(
    plan: ImplementationPlan,
    repo_path: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    Serialise *plan* to JSON and write to disk.

    Args:
        plan:        The validated ``ImplementationPlan`` to persist.
        repo_path:   Repository root (used to derive the default path).
        output_path: Explicit destination path (overrides ``repo_path``).

    Returns:
        The absolute path where the plan was written.
    """
    dest = _resolve_output_path(output_path, repo_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically via a temp file
    tmp = dest.with_suffix(".json.tmp")
    try:
        json_str = plan.model_dump_json(indent=2)
        tmp.write_text(json_str, encoding="utf-8")
        tmp.replace(dest)
        logger.info("Plan saved to %s (%d bytes)", dest, dest.stat().st_size)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return dest


def load_plan(path: Path) -> ImplementationPlan:
    """
    Load an :class:`ImplementationPlan` from a JSON file.

    Args:
        path: Path to the JSON file (e.g. ``.ai_swe_plan.json``).

    Returns:
        A validated ``ImplementationPlan`` instance.

    Raises:
        FileNotFoundError: If *path* does not exist.
        pydantic.ValidationError: If the JSON does not match the schema.
    """
    path = path.expanduser().resolve()
    raw = path.read_text(encoding="utf-8")
    return ImplementationPlan.model_validate_json(raw)


def plan_exists(
    repo_path: Path | None = None,
    output_path: Path | None = None,
) -> bool:
    """Return True if a plan file already exists at the resolved path."""
    dest = _resolve_output_path(output_path, repo_path)
    return dest.is_file()
