"""
GitHub operations, implemented as thin, typed wrappers around the GitHub MCP
server's tools.

Today's foundation only requires that we can *connect* to this server (see
`MCPOrchestrator.health_check`). The functions below are minimal, real
wrappers around a couple of read-only GitHub tools so the module is
immediately useful and demonstrates the calling convention -- richer GitHub
workflows (opening PRs, pushing commits, etc.) will be added alongside the
Coder/Executor agents in a later milestone, not today.
"""

from __future__ import annotations

from pydantic import BaseModel

from ai_swe.logging_config import get_logger
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.mcp.factory import GITHUB_SERVER

logger = get_logger(__name__)


class RepositorySummary(BaseModel):
    """Minimal summary of a GitHub repository, as returned by `search_repositories`."""

    full_name: str
    description: str | None = None
    stars: int | None = None
    url: str | None = None


async def search_repositories(orchestrator: MCPOrchestrator, query: str) -> list[RepositorySummary]:
    """
    Search public GitHub repositories via the GitHub MCP server.

    Requires `GITHUB_PERSONAL_ACCESS_TOKEN` to be set (see `.env.example`) --
    the GitHub MCP server calls the real GitHub REST API under the hood.
    """
    logger.info("Searching GitHub repositories for query='%s'...", query)
    raw = await orchestrator.call(GITHUB_SERVER, "search_repositories", {"query": query})

    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    summaries: list[RepositorySummary] = []
    for item in items or []:
        summaries.append(
            RepositorySummary(
                full_name=item.get("full_name", ""),
                description=item.get("description"),
                stars=item.get("stargazers_count"),
                url=item.get("html_url"),
            )
        )
    return summaries


async def get_file_contents(
    orchestrator: MCPOrchestrator, owner: str, repo: str, path: str, *, branch: str | None = None
) -> str:
    """Fetch a single file's contents from a GitHub repository via the GitHub MCP server."""
    arguments: dict[str, object] = {"owner": owner, "repo": repo, "path": path}
    if branch:
        arguments["branch"] = branch
    result = await orchestrator.call(GITHUB_SERVER, "get_file_contents", arguments)
    return str(result)
