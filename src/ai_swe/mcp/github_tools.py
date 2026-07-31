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
from ai_swe.state import AgentState

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


class PullRequestResult(BaseModel):
    """Structured result of an `open_pull_request` call."""

    success: bool
    url: str | None = None
    number: int | None = None


async def open_pull_request(
    orchestrator: MCPOrchestrator,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> PullRequestResult:
    """
    Open a pull request via the GitHub MCP server's `create_pull_request` tool.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        head: Branch containing the changes (the Publisher's feature branch).
        base: Branch the changes should be merged into (e.g. `main`).
        title: Pull request title.
        body: Pull request description (see `build_pr_body`).
    """
    logger.info("Opening pull request %s/%s: '%s' -> '%s' (%s)...", owner, repo, head, base, title)
    raw_result = await orchestrator.call(
        GITHUB_SERVER,
        "create_pull_request",
        {"owner": owner, "repo": repo, "title": title, "head": head, "base": base, "body": body},
    )

    if isinstance(raw_result, dict):
        url = raw_result.get("html_url")
        number = raw_result.get("number")
        success = bool(url or raw_result.get("success", False))
    else:
        url = None
        number = None
        success = False

    result = PullRequestResult(success=success, url=url, number=number)
    logger.info("Pull request opened: success=%s url=%s", result.success, result.url)
    return result


def build_pr_body(state: AgentState) -> str:
    """
    Auto-generate a pull request description from `state`: the plan summary,
    the list of files changed (`state.patches`), and the test results
    (`state.test_results`).
    """
    lines: list[str] = ["## Summary", "", state.plan.summary or state.task, ""]

    lines.append("## Files changed")
    if state.patches:
        for patch in state.patches:
            entry = f"- `{patch.file_path}`"
            if patch.description:
                entry += f" — {patch.description}"
            lines.append(entry)
    else:
        lines.append("_No files recorded._")
    lines.append("")

    lines.append("## Test results")
    if state.test_results:
        for test in state.test_results:
            status = "✅ PASSED" if test.passed else "❌ FAILED"
            lines.append(f"- **{test.name}**: {status}")
    else:
        lines.append("_No test results recorded._")

    if state.ci_result is not None:
        lines.append("")
        lines.append("## CI checks")
        for check in state.ci_result.checks:
            status = "✅ PASSED" if check.passed else "❌ FAILED"
            lines.append(f"- **{check.name}**: {status}")

    lines.append("")
    lines.append("---")
    lines.append("_Opened automatically by the AI SWE Agent's Publisher._")

    return "\n".join(lines)
