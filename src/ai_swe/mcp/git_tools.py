"""
Git operations, implemented as thin, typed wrappers around the Git MCP
server's tools.

We deliberately keep this module free of any direct `git` or `GitPython`
calls -- every actual git operation must go *through* the MCP server, so that
all repository access is mediated, auditable, and swappable (e.g. sandboxed
in a container) independent of the agent's own process.
"""

from __future__ import annotations

from pydantic import BaseModel

from ai_swe.logging_config import get_logger
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.mcp.factory import GIT_SERVER

logger = get_logger(__name__)


class CloneResult(BaseModel):
    """Structured result of a `clone_repository` call."""

    success: bool
    remote_url: str
    local_path: str
    branch: str | None = None
    commit_hash: str | None = None


async def clone_repository(
    orchestrator: MCPOrchestrator,
    repo_url: str,
    dest_path: str,
    *,
    branch: str | None = None,
    depth: int | None = 1,
) -> CloneResult:
    """
    Clone a git repository via the Git MCP server's `git_clone` tool.

    Args:
        orchestrator: A connected `MCPOrchestrator` with the Git server registered.
        repo_url: HTTP(S) or SSH URL of the repository to clone.
        dest_path: Local filesystem path to clone into. Must be inside the
            directory the Git MCP server was scoped to (see `GIT_BASE_DIR`).
        branch: Specific branch to clone; defaults to the remote's HEAD branch.
        depth: Shallow-clone depth. `1` (the default) is usually sufficient
            for an agent that only needs the latest state of the repo, and is
            much faster than a full clone for large repositories.

    Returns:
        A `CloneResult` describing the outcome (success flag, resolved
        branch, and the commit hash checked out).

    Raises:
        MCPToolError: If the underlying `git_clone` tool call fails (e.g.
            invalid URL, network error, or authentication failure).
    """
    logger.info("Cloning '%s' into '%s' (branch=%s, depth=%s)...", repo_url, dest_path, branch, depth)

    arguments: dict[str, object] = {"url": repo_url, "path": dest_path}
    if branch:
        arguments["branch"] = branch
    if depth:
        arguments["depth"] = depth

    raw_result = await orchestrator.call(GIT_SERVER, "git_clone", arguments)

    # `raw_result` is the JSON payload returned by the git_clone tool, e.g.:
    #   {"success": true, "remoteUrl": "...", "path": "...", "branch": "...", "commitHash": "..."}
    result = CloneResult(
        success=bool(raw_result.get("success", False)),
        remote_url=raw_result.get("remoteUrl", repo_url),
        local_path=raw_result.get("path", dest_path),
        branch=raw_result.get("branch"),
        commit_hash=raw_result.get("commitHash"),
    )
    logger.info(
        "Clone finished: success=%s branch=%s commit=%s", result.success, result.branch, result.commit_hash
    )
    return result


async def git_status(orchestrator: MCPOrchestrator, repo_path: str) -> str:
    """Return the `git status` output for the repository at `repo_path`."""
    result = await orchestrator.call(GIT_SERVER, "git_status", {"path": repo_path})
    return str(result)


class BranchResult(BaseModel):
    """Structured result of a `create_branch` call."""

    success: bool
    branch: str
    output: str | None = None


async def create_branch(orchestrator: MCPOrchestrator, repo_path: str, branch: str) -> BranchResult:
    """
    Create and switch to a new branch via the Git MCP server's `git_checkout`
    tool (`createBranch: true` -- equivalent to `git checkout -b <branch>`).
    """
    logger.info("Creating branch '%s' in '%s'...", branch, repo_path)
    raw_result = await orchestrator.call(
        GIT_SERVER,
        "git_checkout",
        {"path": repo_path, "target": branch, "createBranch": True},
    )

    success = bool(raw_result.get("success", True)) if isinstance(raw_result, dict) else True
    result = BranchResult(success=success, branch=branch, output=str(raw_result))
    logger.info("Branch creation finished: success=%s branch=%s", result.success, result.branch)
    return result


class CommitResult(BaseModel):
    """Structured result of a `commit_all` call."""

    success: bool
    commit_hash: str | None = None
    output: str | None = None


async def commit_all(orchestrator: MCPOrchestrator, repo_path: str, message: str) -> CommitResult:
    """
    Stage every change in the working tree and commit it, via the Git MCP
    server's `git_add` (all paths) followed by `git_commit`.
    """
    logger.info("Staging all changes in '%s'...", repo_path)
    await orchestrator.call(GIT_SERVER, "git_add", {"path": repo_path, "paths": ["."]})

    logger.info("Committing changes in '%s'...", repo_path)
    raw_result = await orchestrator.call(
        GIT_SERVER, "git_commit", {"path": repo_path, "message": message}
    )

    if isinstance(raw_result, dict):
        success = bool(raw_result.get("success", True))
        commit_hash = raw_result.get("commitHash")
    else:
        success = True
        commit_hash = None

    result = CommitResult(success=success, commit_hash=commit_hash, output=str(raw_result))
    logger.info("Commit finished: success=%s commit=%s", result.success, result.commit_hash)
    return result


class PushResult(BaseModel):
    """Structured result of a `push_branch` call."""

    success: bool
    output: str | None = None


async def push_branch(orchestrator: MCPOrchestrator, repo_path: str, branch: str) -> PushResult:
    """
    Push `branch` to its remote (`origin`) via the Git MCP server's
    `git_push` tool, setting up upstream tracking (equivalent to
    `git push -u origin <branch>`).
    """
    logger.info("Pushing branch '%s' from '%s'...", branch, repo_path)
    raw_result = await orchestrator.call(
        GIT_SERVER,
        "git_push",
        {"path": repo_path, "branch": branch, "setUpstream": True},
    )

    success = bool(raw_result.get("success", True)) if isinstance(raw_result, dict) else True
    result = PushResult(success=success, output=str(raw_result))
    logger.info("Push finished: success=%s branch=%s", result.success, branch)
    return result
