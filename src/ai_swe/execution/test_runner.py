"""
Test-suite auto-detection and execution for the Execution agent.

Given a repository checkout, `detect_test_framework()` inspects marker files
to decide which test framework is in play (pytest, npm/jest, go test, Maven,
Gradle), and `run_tests()` builds the right command, runs it inside a
`Sandbox`, and wraps the outcome in `TestResult`s.

This is a coarse, suite-level verification (one `TestResult` per run of the
whole suite) rather than a per-test-case breakdown -- parsing every
framework's structured output (pytest's `--junit-xml`, Jest's `--json`, ...)
is a reasonable future improvement, but the exit code plus a best-effort
summary line is enough to gate the pipeline on pass/fail today.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ai_swe.execution.sandbox import Sandbox
from ai_swe.logging_config import get_logger
from ai_swe.state import TestResult

logger = get_logger(__name__)

# Default timeout for a full test-suite run -- generous because installing
# deps / cold-starting interpreters inside a fresh sandbox can be slow.
DEFAULT_TEST_TIMEOUT = 600.0

# Combined stdout+stderr is truncated to this many characters (kept from the
# tail, where failure output usually lives) before being stored on the
# `TestResult`.
MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class TestFramework:
    """A detected test framework: its name and how to invoke it."""

    name: str
    command: list[str]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_test_framework(repo_path: str | Path) -> TestFramework | None:
    """
    Inspect `repo_path` for marker files and return the first matching test
    framework, in priority order. Returns `None` if nothing recognisable is
    found (callers should treat that as "no tests to run", not an error).
    """
    repo = Path(repo_path)

    if (
        (repo / "pyproject.toml").is_file()
        or (repo / "setup.py").is_file()
        or (repo / "pytest.ini").is_file()
    ):
        return TestFramework(name="pytest", command=["python", "-m", "pytest"])

    package_json = repo / "package.json"
    if package_json.is_file():
        scripts: dict[str, str] = {}
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                scripts = data.get("scripts", {}) or {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to parse %s: %s", package_json, exc)

        if "test" in scripts:
            return TestFramework(name="npm", command=["npm", "test", "--silent"])
        return TestFramework(name="jest", command=["npx", "--yes", "jest"])

    if (repo / "go.mod").is_file():
        return TestFramework(name="go", command=["go", "test", "./..."])

    if (repo / "pom.xml").is_file():
        return TestFramework(name="maven", command=["mvn", "-q", "test"])

    if (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file():
        return TestFramework(name="gradle", command=["./gradlew", "test"])

    return None


# ---------------------------------------------------------------------------
# Best-effort summary extraction
# ---------------------------------------------------------------------------

_PYTEST_SUMMARY_RE = re.compile(r"=+\s*(.+ in [\d.]+s(?: \(\d+ warning[s]?\))?)\s*=+")
_JEST_SUMMARY_RE = re.compile(r"Tests:\s+(.+)")
_MAVEN_SUMMARY_RE = re.compile(r"Tests run: \d+.*")


def _summarize(framework_name: str, output: str) -> str:
    """Best-effort one-line summary pulled from a framework's own output."""
    if framework_name == "pytest":
        match = _PYTEST_SUMMARY_RE.search(output)
        if match:
            return match.group(1).strip()
    elif framework_name in ("jest", "npm"):
        match = _JEST_SUMMARY_RE.search(output)
        if match:
            return match.group(1).strip()
    elif framework_name == "go":
        result_lines = [line for line in output.splitlines() if line.startswith(("ok", "FAIL", "---"))]
        if result_lines:
            return "; ".join(result_lines[-3:])
    elif framework_name == "maven":
        match = _MAVEN_SUMMARY_RE.search(output)
        if match:
            return match.group(0).strip()

    # Fall back to the last non-empty line of output, if any.
    stripped = output.strip()
    return stripped.splitlines()[-1] if stripped else ""


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:] + f"\n... (truncated to last {limit} chars)"


# ---------------------------------------------------------------------------
# Running the suite
# ---------------------------------------------------------------------------


async def run_tests(
    repo_path: str | Path,
    sandbox: Sandbox,
    *,
    timeout: float = DEFAULT_TEST_TIMEOUT,
) -> list[TestResult]:
    """
    Auto-detect the test framework used in `repo_path` and run it via `sandbox`.

    Returns a single-element list with one `TestResult` for the whole suite
    run (`passed` reflects the command's exit code; `output` carries a
    best-effort one-line summary plus truncated raw output). Returns an
    empty list if no recognised test framework is found.
    """
    framework = detect_test_framework(repo_path)
    if framework is None:
        logger.warning("No recognised test framework found in %s; skipping tests.", repo_path)
        return []

    logger.info("Detected test framework: %s (%s)", framework.name, " ".join(framework.command))
    result = await sandbox.run(framework.command, cwd=repo_path, timeout=timeout)

    combined_output = (result.stdout + "\n" + result.stderr).strip()
    summary = _summarize(framework.name, combined_output)

    label = f"{framework.name} suite"
    if summary:
        label = f"{label}: {summary}"
    if result.timed_out:
        label = f"{label} (timed out)"

    return [
        TestResult(
            name=label,
            passed=result.success,
            output=_truncate(combined_output),
        )
    ]
