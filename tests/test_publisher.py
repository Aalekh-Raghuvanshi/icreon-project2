"""
Tests for `ai_swe.agents.publisher` -- fully offline, using a `FakeOrchestrator`
(records calls, returns canned JSON) and simple `Sandbox` stubs. No real
subprocess, Docker, network, or GitHub call is ever made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_swe.agents.publisher import (
    PublisherAgent,
    _branch_name,
    _parse_owner_repo,
    finalize_and_open_pr,
)
from ai_swe.execution.sandbox import CommandResult, Sandbox
from ai_swe.state import AgentState, Patch, Plan, TaskStatus, TestResult


class FakeOrchestrator:
    """Records every `call()` and returns a canned response keyed by tool name."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((server, tool, arguments))
        return self.responses.get(tool, {"success": True})


class PassingSandbox(Sandbox):
    """Every command 'succeeds' -- used to exercise a clean CI gate."""

    async def run(self, command: list[str], cwd: str | Path, timeout: float = 300.0) -> CommandResult:
        return CommandResult(exit_code=0, stdout="ok", stderr="", duration=0.01)


class FailingLintSandbox(Sandbox):
    """`ruff check` fails; every other command succeeds."""

    async def run(self, command: list[str], cwd: str | Path, timeout: float = 300.0) -> CommandResult:
        if command[0] == "ruff":
            return CommandResult(exit_code=1, stdout="", stderr="lint error", duration=0.01)
        return CommandResult(exit_code=0, stdout="ok", stderr="", duration=0.01)


def _done_state(tmp_path: Path) -> AgentState:
    return AgentState(
        task="Add rate limiting",
        repo_path=str(tmp_path),
        repo_url="https://github.com/me/myrepo.git",
        status=TaskStatus.DONE,
        plan=Plan(summary="Adds a token-bucket limiter."),
        patches=[Patch(file_path="src/limiter.py", diff="...", description="new limiter")],
        test_results=[TestResult(name="pytest suite", passed=True)],
    )


class TestBranchNameAndUrlParsing:
    def test_branch_name_is_slugified_and_unique(self) -> None:
        first = _branch_name("Add rate limiting!!")
        second = _branch_name("Add rate limiting!!")

        assert first.startswith("ai-swe/add-rate-limiting")
        assert first != second  # unique suffix each call

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/octocat/Hello-World.git", ("octocat", "Hello-World")),
            ("https://github.com/octocat/Hello-World", ("octocat", "Hello-World")),
            ("git@github.com:octocat/Hello-World.git", ("octocat", "Hello-World")),
        ],
    )
    def test_parse_owner_repo(self, url: str, expected: tuple[str, str]) -> None:
        assert _parse_owner_repo(url) == expected

    def test_parse_owner_repo_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            _parse_owner_repo("not-a-url")


class TestFinalizeAndOpenPr:
    @pytest.mark.asyncio
    async def test_refuses_when_not_done(self) -> None:
        orch = FakeOrchestrator()
        state = AgentState(task="x", status=TaskStatus.CODING)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.CODING
        assert orch.calls == []
        assert any("refusing to finalize" in log.message.lower() for log in result.logs)

    @pytest.mark.asyncio
    async def test_requires_repo_path_and_repo_url(self) -> None:
        orch = FakeOrchestrator()
        state = AgentState(task="x", status=TaskStatus.DONE)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.FAILED
        assert "repo_path" in (result.error or "")
        assert orch.calls == []

    @pytest.mark.asyncio
    async def test_ci_failure_stops_before_any_git_call(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator()
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=FailingLintSandbox())

        assert result.status == TaskStatus.FAILED
        assert result.ci_result is not None
        assert result.ci_result.passed is False
        assert "ruff" in (result.error or "")
        assert orch.calls == []  # no branch/commit/push/PR attempted
        assert result.pr_url is None

    @pytest.mark.asyncio
    async def test_branch_failure_stops_before_commit(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(responses={"git_checkout": {"success": False}})
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.FAILED
        assert [call[1] for call in orch.calls] == ["git_checkout"]

    @pytest.mark.asyncio
    async def test_commit_failure_stops_before_push(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": False},
            }
        )
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.FAILED
        assert [call[1] for call in orch.calls] == ["git_checkout", "git_add", "git_commit"]

    @pytest.mark.asyncio
    async def test_push_failure_stops_before_pr(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "abc"},
                "git_push": {"success": False},
            }
        )
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.FAILED
        assert [call[1] for call in orch.calls] == ["git_checkout", "git_add", "git_commit", "git_push"]

    @pytest.mark.asyncio
    async def test_happy_path_opens_pr(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "abc123"},
                "git_push": {"success": True},
                "create_pull_request": {
                    "html_url": "https://github.com/me/myrepo/pull/7",
                    "number": 7,
                },
            }
        )
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.DONE
        assert result.ci_result is not None and result.ci_result.passed is True
        assert result.branch_name is not None and result.branch_name.startswith("ai-swe/")
        assert result.pr_url == "https://github.com/me/myrepo/pull/7"

        tool_calls = [call[1] for call in orch.calls]
        assert tool_calls == ["git_checkout", "git_add", "git_commit", "git_push", "create_pull_request"]

        pr_call_args = orch.calls[-1][2]
        assert pr_call_args["base"] == "main"
        assert pr_call_args["head"] == result.branch_name
        assert pr_call_args["owner"] == "me"
        assert pr_call_args["repo"] == "myrepo"

    @pytest.mark.asyncio
    async def test_pr_open_failure(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "abc123"},
                "git_push": {"success": True},
                "create_pull_request": {},
            }
        )
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox())

        assert result.status == TaskStatus.FAILED
        assert result.pr_url is None

    @pytest.mark.asyncio
    async def test_custom_base_branch_is_used(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "abc123"},
                "git_push": {"success": True},
                "create_pull_request": {"html_url": "https://github.com/me/myrepo/pull/1", "number": 1},
            }
        )
        state = _done_state(tmp_path)

        result = await finalize_and_open_pr(orch, state, sandbox=PassingSandbox(), base_branch="develop")

        assert result.status == TaskStatus.DONE
        pr_call_args = orch.calls[-1][2]
        assert pr_call_args["base"] == "develop"


class TestPublisherAgent:
    @pytest.mark.asyncio
    async def test_run_delegates_to_finalize_and_open_pr(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator(
            responses={
                "git_checkout": {"success": True},
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "abc123"},
                "git_push": {"success": True},
                "create_pull_request": {"html_url": "https://github.com/me/myrepo/pull/1", "number": 1},
            }
        )
        agent = PublisherAgent(orch, sandbox=PassingSandbox())
        state = _done_state(tmp_path)

        result = await agent.run(state)

        assert result.pr_url == "https://github.com/me/myrepo/pull/1"

    @pytest.mark.asyncio
    async def test_run_does_not_open_pr_when_not_done(self, tmp_path: Path) -> None:
        orch = FakeOrchestrator()
        agent = PublisherAgent(orch, sandbox=PassingSandbox())
        state = AgentState(task="x", repo_path=str(tmp_path), status=TaskStatus.CODING)

        result = await agent.run(state)

        assert result.pr_url is None
        assert orch.calls == []
