"""
Tests for `ai_swe.mcp.git_tools` -- fully offline, using a `FakeOrchestrator`
that records calls and returns canned JSON instead of talking to a real
Git MCP server subprocess.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_swe.mcp.git_tools import (
    BranchResult,
    CloneResult,
    CommitResult,
    PushResult,
    clone_repository,
    commit_all,
    create_branch,
    git_status,
    push_branch,
)


class FakeOrchestrator:
    """Records every `call()` and returns a canned response keyed by tool name."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((server, tool, arguments))
        return self.responses.get(tool, {"success": True})


class TestCloneRepository:
    @pytest.mark.asyncio
    async def test_clone_success(self) -> None:
        orch = FakeOrchestrator(
            {
                "git_clone": {
                    "success": True,
                    "remoteUrl": "https://github.com/x/y.git",
                    "path": "/tmp/y",
                    "branch": "main",
                    "commitHash": "abc123",
                }
            }
        )
        result = await clone_repository(orch, "https://github.com/x/y.git", "/tmp/y")

        assert isinstance(result, CloneResult)
        assert result.success is True
        assert result.branch == "main"
        assert result.commit_hash == "abc123"
        assert orch.calls == [
            ("git", "git_clone", {"url": "https://github.com/x/y.git", "path": "/tmp/y", "depth": 1})
        ]


class TestGitStatus:
    @pytest.mark.asyncio
    async def test_git_status_returns_raw_text(self) -> None:
        orch = FakeOrchestrator({"git_status": "nothing to commit, working tree clean"})
        result = await git_status(orch, "/tmp/repo")
        assert result == "nothing to commit, working tree clean"


class TestCreateBranch:
    @pytest.mark.asyncio
    async def test_create_branch_success(self) -> None:
        orch = FakeOrchestrator({"git_checkout": {"success": True}})
        result = await create_branch(orch, "/tmp/repo", "feature/rate-limit")

        assert isinstance(result, BranchResult)
        assert result.success is True
        assert result.branch == "feature/rate-limit"
        assert orch.calls == [
            (
                "git",
                "git_checkout",
                {"path": "/tmp/repo", "target": "feature/rate-limit", "createBranch": True},
            )
        ]

    @pytest.mark.asyncio
    async def test_create_branch_reports_failure(self) -> None:
        orch = FakeOrchestrator({"git_checkout": {"success": False, "message": "branch already exists"}})
        result = await create_branch(orch, "/tmp/repo", "feature/rate-limit")
        assert result.success is False


class TestCommitAll:
    @pytest.mark.asyncio
    async def test_commit_all_stages_then_commits(self) -> None:
        orch = FakeOrchestrator(
            {
                "git_add": {"success": True},
                "git_commit": {"success": True, "commitHash": "deadbeef"},
            }
        )
        result = await commit_all(orch, "/tmp/repo", "Add rate limiting")

        assert isinstance(result, CommitResult)
        assert result.success is True
        assert result.commit_hash == "deadbeef"
        assert orch.calls[0] == ("git", "git_add", {"path": "/tmp/repo", "paths": ["."]})
        assert orch.calls[1][0] == "git"
        assert orch.calls[1][1] == "git_commit"
        assert orch.calls[1][2] == {"path": "/tmp/repo", "message": "Add rate limiting"}

    @pytest.mark.asyncio
    async def test_commit_all_reports_failure(self) -> None:
        orch = FakeOrchestrator(
            {
                "git_add": {"success": True},
                "git_commit": {"success": False},
            }
        )
        result = await commit_all(orch, "/tmp/repo", "Add rate limiting")
        assert result.success is False
        assert result.commit_hash is None


class TestPushBranch:
    @pytest.mark.asyncio
    async def test_push_branch_success(self) -> None:
        orch = FakeOrchestrator({"git_push": {"success": True}})
        result = await push_branch(orch, "/tmp/repo", "feature/rate-limit")

        assert isinstance(result, PushResult)
        assert result.success is True
        assert orch.calls == [
            (
                "git",
                "git_push",
                {"path": "/tmp/repo", "branch": "feature/rate-limit", "setUpstream": True},
            )
        ]

    @pytest.mark.asyncio
    async def test_push_branch_reports_failure(self) -> None:
        orch = FakeOrchestrator({"git_push": {"success": False}})
        result = await push_branch(orch, "/tmp/repo", "feature/rate-limit")
        assert result.success is False
