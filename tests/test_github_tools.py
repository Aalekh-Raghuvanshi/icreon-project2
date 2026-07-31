"""
Tests for `ai_swe.mcp.github_tools` -- fully offline, using a `FakeOrchestrator`
that records calls and returns canned JSON instead of talking to a real
GitHub MCP server subprocess.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_swe.mcp.github_tools import (
    PullRequestResult,
    RepositorySummary,
    build_pr_body,
    get_file_contents,
    open_pull_request,
    search_repositories,
)
from ai_swe.state import AgentState, Patch, Plan, TestResult


class FakeOrchestrator:
    """Records every `call()` and returns a canned response keyed by tool name."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((server, tool, arguments))
        return self.responses.get(tool, {"success": True})


class TestSearchRepositories:
    @pytest.mark.asyncio
    async def test_search_repositories_parses_items(self) -> None:
        orch = FakeOrchestrator(
            {
                "search_repositories": {
                    "items": [
                        {
                            "full_name": "octocat/Hello-World",
                            "description": "My first repo",
                            "stargazers_count": 5,
                            "html_url": "https://github.com/octocat/Hello-World",
                        }
                    ]
                }
            }
        )
        results = await search_repositories(orch, "hello world")

        assert results == [
            RepositorySummary(
                full_name="octocat/Hello-World",
                description="My first repo",
                stars=5,
                url="https://github.com/octocat/Hello-World",
            )
        ]


class TestGetFileContents:
    @pytest.mark.asyncio
    async def test_get_file_contents_returns_raw_text(self) -> None:
        orch = FakeOrchestrator({"get_file_contents": "# Hello World"})
        result = await get_file_contents(orch, "octocat", "Hello-World", "README.md")
        assert result == "# Hello World"


class TestOpenPullRequest:
    @pytest.mark.asyncio
    async def test_open_pull_request_success(self) -> None:
        orch = FakeOrchestrator(
            {"create_pull_request": {"html_url": "https://github.com/o/r/pull/1", "number": 1}}
        )
        result = await open_pull_request(
            orch, "o", "r", head="feature/x", base="main", title="Add rate limiting", body="body text"
        )

        assert isinstance(result, PullRequestResult)
        assert result.success is True
        assert result.url == "https://github.com/o/r/pull/1"
        assert result.number == 1
        assert orch.calls == [
            (
                "github",
                "create_pull_request",
                {
                    "owner": "o",
                    "repo": "r",
                    "title": "Add rate limiting",
                    "head": "feature/x",
                    "base": "main",
                    "body": "body text",
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_open_pull_request_reports_failure_when_no_url(self) -> None:
        orch = FakeOrchestrator({"create_pull_request": {}})
        result = await open_pull_request(
            orch, "o", "r", head="feature/x", base="main", title="t", body="b"
        )
        assert result.success is False
        assert result.url is None


class TestBuildPrBody:
    def test_includes_plan_summary_files_and_tests(self) -> None:
        state = AgentState(
            task="Add rate limiting",
            plan=Plan(summary="Adds a token-bucket limiter."),
            patches=[Patch(file_path="src/limiter.py", diff="...", description="new limiter")],
            test_results=[TestResult(name="pytest suite", passed=True)],
        )
        body = build_pr_body(state)

        assert "Adds a token-bucket limiter." in body
        assert "src/limiter.py" in body
        assert "new limiter" in body
        assert "pytest suite" in body
        assert "PASSED" in body

    def test_falls_back_to_task_and_empty_sections(self) -> None:
        state = AgentState(task="Fix the flaky retry test")
        body = build_pr_body(state)

        assert "Fix the flaky retry test" in body
        assert "No files recorded" in body
        assert "No test results recorded" in body

    def test_includes_failing_tests_and_ci_result(self) -> None:
        from ai_swe.execution.ci import CheckResult, CIResult

        state = AgentState(
            task="x",
            test_results=[TestResult(name="pytest suite", passed=False, output="1 failed")],
            ci_result=CIResult(passed=True, checks=[CheckResult(name="ruff", passed=True, output="")]),
        )
        body = build_pr_body(state)

        assert "FAILED" in body
        assert "ruff" in body
