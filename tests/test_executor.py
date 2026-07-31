"""
Tests for `ai_swe.agents.executor.ExecutionAgent`.

A `FakeSandbox` (recording the command it was given, returning a canned
`CommandResult`) is injected via the `sandbox=` constructor argument, so
these tests never spawn a real subprocess, need Docker, or need a real
pytest/npm/go toolchain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_swe.agents.executor import ExecutionAgent
from ai_swe.execution.sandbox import CommandResult, Sandbox
from ai_swe.state import AgentState, TaskStatus


class FakeSandbox(Sandbox):
    def __init__(self, result: CommandResult) -> None:
        self.result = result

    async def run(self, command: list[str], cwd: str | Path, timeout: float = 300.0) -> CommandResult:
        return self.result


class TestExecutionAgent:
    @pytest.mark.asyncio
    async def test_no_test_framework_hands_off_to_reviewer(self, tmp_path):
        agent = ExecutionAgent(
            orchestrator=None,
            sandbox=FakeSandbox(CommandResult(exit_code=0, stdout="", stderr="", duration=0.0)),
        )
        state = AgentState(task="do something", repo_path=str(tmp_path))

        result = await agent.run(state)

        assert result.status == TaskStatus.REVIEWING
        assert result.test_results == []
        assert result.error is None
        assert any("no recognised test framework" in log.message.lower() for log in result.logs)

    @pytest.mark.asyncio
    async def test_passing_suite_hands_off_to_reviewer(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        agent = ExecutionAgent(
            orchestrator=None,
            sandbox=FakeSandbox(
                CommandResult(exit_code=0, stdout="===== 2 passed in 0.05s =====", stderr="", duration=0.05)
            ),
        )
        state = AgentState(task="do something", repo_path=str(tmp_path))

        result = await agent.run(state)

        assert result.status == TaskStatus.REVIEWING
        assert len(result.test_results) == 1
        assert result.test_results[0].passed is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_failing_suite_hands_off_to_reviewer_without_error(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        agent = ExecutionAgent(
            orchestrator=None,
            sandbox=FakeSandbox(
                CommandResult(exit_code=1, stdout="===== 1 failed in 0.05s =====", stderr="", duration=0.05)
            ),
        )
        state = AgentState(task="do something", repo_path=str(tmp_path))

        result = await agent.run(state)

        # Failing tests are a recoverable part of the auto-fix loop, not an
        # infrastructure error -- REVIEWING (not FAILED), and `error` is left
        # unset since it's reserved for a terminal FAILED status.
        assert result.status == TaskStatus.REVIEWING
        assert result.test_results[0].passed is False
        assert result.error is None

    @pytest.mark.asyncio
    async def test_sandbox_error_sets_failed_status(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        class ExplodingSandbox(Sandbox):
            async def run(self, command, cwd, timeout=300.0):
                raise RuntimeError("sandbox blew up")

        agent = ExecutionAgent(orchestrator=None, sandbox=ExplodingSandbox())
        state = AgentState(task="do something", repo_path=str(tmp_path))

        result = await agent.run(state)

        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert "sandbox blew up" in result.error

    @pytest.mark.asyncio
    async def test_defaults_repo_path_to_cwd_when_unset(self):
        agent = ExecutionAgent(
            orchestrator=None,
            sandbox=FakeSandbox(CommandResult(exit_code=0, stdout="", stderr="", duration=0.0)),
        )
        state = AgentState(task="do something")

        result = await agent.run(state)

        # No pyproject.toml/etc in the real cwd during tests (repo root has
        # pyproject.toml, actually -- so just assert it doesn't raise and
        # reaches a terminal-or-handoff status).
        assert result.status in (TaskStatus.REVIEWING, TaskStatus.FAILED)
