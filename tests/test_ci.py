"""
Tests for `ai_swe.execution.ci.run_ci_checks`.

A `ScriptedSandbox` (returning a canned `CommandResult` keyed by the
command's binary name) is injected directly, so these tests never spawn a
real subprocess, need Docker, or need `ruff`/`mypy` actually installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_swe.execution.ci import CheckResult, CIResult, run_ci_checks
from ai_swe.execution.sandbox import CommandResult, Sandbox


class ScriptedSandbox(Sandbox):
    """Returns a canned `CommandResult` based on `command[0]`, recording every call."""

    def __init__(self, results: dict[str, CommandResult]) -> None:
        self.results = results
        self.commands: list[list[str]] = []

    async def run(self, command: list[str], cwd: str | Path, timeout: float = 300.0) -> CommandResult:
        self.commands.append(command)
        return self.results[command[0]]


class TestRunCiChecks:
    @pytest.mark.asyncio
    async def test_both_checks_pass(self, tmp_path: Path) -> None:
        sandbox = ScriptedSandbox(
            {
                "ruff": CommandResult(exit_code=0, stdout="All checks passed!", stderr="", duration=0.1),
                "mypy": CommandResult(exit_code=0, stdout="Success: no issues found", stderr="", duration=0.2),
            }
        )

        result = await run_ci_checks(sandbox, tmp_path)

        assert isinstance(result, CIResult)
        assert result.passed is True
        assert [c.name for c in result.checks] == ["ruff", "mypy"]
        assert all(isinstance(c, CheckResult) and c.passed for c in result.checks)
        assert sandbox.commands == [["ruff", "check", "."], ["mypy", "."]]

    @pytest.mark.asyncio
    async def test_lint_failure_fails_the_gate(self, tmp_path: Path) -> None:
        sandbox = ScriptedSandbox(
            {
                "ruff": CommandResult(
                    exit_code=1, stdout="", stderr="file.py:1:1: F401 unused import", duration=0.1
                ),
                "mypy": CommandResult(exit_code=0, stdout="Success", stderr="", duration=0.2),
            }
        )

        result = await run_ci_checks(sandbox, tmp_path)

        assert result.passed is False
        ruff_check = next(c for c in result.checks if c.name == "ruff")
        mypy_check = next(c for c in result.checks if c.name == "mypy")
        assert ruff_check.passed is False
        assert "F401" in ruff_check.output
        assert mypy_check.passed is True

    @pytest.mark.asyncio
    async def test_both_checks_run_even_if_the_first_fails(self, tmp_path: Path) -> None:
        sandbox = ScriptedSandbox(
            {
                "ruff": CommandResult(exit_code=1, stdout="", stderr="boom", duration=0.1),
                "mypy": CommandResult(exit_code=1, stdout="", stderr="type error", duration=0.1),
            }
        )

        result = await run_ci_checks(sandbox, tmp_path)

        assert len(sandbox.commands) == 2
        assert result.passed is False
        assert all(not c.passed for c in result.checks)

    @pytest.mark.asyncio
    async def test_output_is_truncated(self, tmp_path: Path) -> None:
        long_output = "x" * 10_000
        sandbox = ScriptedSandbox(
            {
                "ruff": CommandResult(exit_code=0, stdout=long_output, stderr="", duration=0.1),
                "mypy": CommandResult(exit_code=0, stdout="ok", stderr="", duration=0.1),
            }
        )

        result = await run_ci_checks(sandbox, tmp_path)

        ruff_check = next(c for c in result.checks if c.name == "ruff")
        assert len(ruff_check.output) < len(long_output)
        assert "truncated" in ruff_check.output
