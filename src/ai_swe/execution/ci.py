"""
CI/CD gate for the Publisher step.

By the time this module runs, the Reviewer has already approved the task
(``state.status == DONE`` -- the project's own test suite passes). This is a
*separate*, stricter gate that runs immediately before a pull request is
opened: static lint (``ruff check``) and type-checking (``mypy``), both
executed inside a `Sandbox` exactly like the Execution agent's test runs, so
a misbehaving check can't touch the host running the agent.

This is deliberately not wired into the auto-fix loop (Reviewer <-> Coder,
see `orchestrator/graph.py`) -- a CI failure here does not trigger another
Coder attempt, it simply blocks the Publisher from opening a PR. Callers
that want CI failures to feed back into the fix loop can inspect
`CIResult.checks` and re-drive the pipeline themselves.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ai_swe.execution.sandbox import Sandbox
from ai_swe.logging_config import get_logger

logger = get_logger(__name__)

# Default timeout for a single CI check.
DEFAULT_CI_TIMEOUT = 300.0

# Combined stdout+stderr is truncated to this many characters (kept from the
# tail, where failure output usually lives) before being stored on a
# `CheckResult` -- mirrors `execution/test_runner.py`'s `MAX_OUTPUT_CHARS`.
MAX_OUTPUT_CHARS = 4000

# The checks the gate runs, in order, as (name, command) pairs.
_CI_CHECKS: list[tuple[str, list[str]]] = [
    ("ruff", ["ruff", "check", "."]),
    ("mypy", ["mypy", "."]),
]


class CheckResult(BaseModel):
    """The outcome of a single CI check (e.g. `ruff check .`)."""

    name: str = Field(description="Short name of the check, e.g. 'ruff' or 'mypy'.")
    passed: bool
    output: str = Field(default="", description="Captured stdout/stderr, truncated.")


class CIResult(BaseModel):
    """The aggregate outcome of the CI gate: pass only if every check passes."""

    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:] + f"\n... (truncated to last {limit} chars)"


async def run_ci_checks(
    sandbox: Sandbox,
    repo_path: str | Path,
    *,
    timeout: float = DEFAULT_CI_TIMEOUT,
) -> CIResult:
    """
    Run lint (`ruff check`) and type-check (`mypy`) inside `sandbox`.

    Runs every configured check regardless of earlier failures, so a caller
    always gets the full picture (not just the first failing check).

    Returns:
        A `CIResult` with `passed = all(check.passed for check in checks)`.
    """
    repo_path = Path(repo_path)
    checks: list[CheckResult] = []

    for name, command in _CI_CHECKS:
        logger.info("Running CI check '%s': %s", name, " ".join(command))
        result = await sandbox.run(command, cwd=repo_path, timeout=timeout)
        output = _truncate((result.stdout + "\n" + result.stderr).strip())
        checks.append(CheckResult(name=name, passed=result.success, output=output))
        logger.info("CI check '%s': %s", name, "PASSED" if result.success else "FAILED")

    passed = all(check.passed for check in checks)
    return CIResult(passed=passed, checks=checks)
