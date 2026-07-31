"""
Publisher Agent -- finalizes a `DONE` task by running a CI gate, then
committing, pushing, and opening a pull request.

This step is intentionally **not** part of the default LangGraph pipeline
(Planner -> Coder -> Executor -> Reviewer, see `orchestrator/graph.py`) --
opening a pull request is a significant, externally-visible side effect that
should only happen when a caller explicitly opts in (see the `ai-swe run
--open-pr` CLI flag). `finalize_and_open_pr` is invoked directly, after
`run_task()` has already produced a `DONE` state.

Flow:
  1. Refuse to run unless `state.status == TaskStatus.DONE` (this is a
     finalize step, not part of the auto-fix loop).
  2. Run the CI gate (`ai_swe.execution.ci.run_ci_checks`) inside a
     `Sandbox` -- lint (`ruff`) and type-check (`mypy`). The result is
     recorded on `state.ci_result` regardless of outcome. If it fails, stop
     here: `state.status = FAILED`, no branch/commit/push/PR.
  3. Create a feature branch, commit all working-tree changes, and push it.
  4. Open a pull request with an auto-generated body (plan summary, files
     changed, test results -- see `mcp.github_tools.build_pr_body`).

Every step is logged onto `state.logs` under the `"publisher"` agent name,
and any failure along the way sets `state.status = FAILED` with a
human-readable `state.error` rather than raising -- callers get a single,
consistent way to check the outcome (`state.status`, `state.error`,
`state.pr_url`).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ai_swe.agents.base import BaseAgent
from ai_swe.execution.ci import run_ci_checks
from ai_swe.execution.sandbox import Sandbox, get_sandbox
from ai_swe.logging_config import get_logger
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.mcp.git_tools import commit_all, create_branch, push_branch
from ai_swe.mcp.github_tools import build_pr_body, open_pull_request
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)

AGENT_NAME = "publisher"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _branch_name(task: str) -> str:
    """Derive a short, git-safe feature branch name from a task description."""
    slug = _SLUG_RE.sub("-", task.lower()).strip("-")[:40] or "task"
    return f"ai-swe/{slug}-{uuid.uuid4().hex[:8]}"


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """
    Extract `(owner, repo)` from a GitHub HTTP(S) or SSH URL, e.g.
    `https://github.com/octocat/Hello-World.git` or
    `git@github.com:octocat/Hello-World.git` -> `("octocat", "Hello-World")`.
    """
    cleaned = repo_url.rstrip("/").removesuffix(".git")
    path = cleaned.partition(":")[2] if cleaned.startswith("git@") else cleaned.split("://", 1)[-1].split("/", 1)[-1]

    parts = path.rsplit("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Could not parse owner/repo from repo URL: {repo_url!r}")
    owner, repo = parts
    return owner, repo


async def finalize_and_open_pr(
    orchestrator: MCPOrchestrator,
    state: AgentState,
    *,
    sandbox: Sandbox | None = None,
    base_branch: str = "main",
) -> AgentState:
    """
    Run the CI gate and, if it passes, commit/push/open a PR for `state`.

    No-op (aside from a log entry) unless `state.status == TaskStatus.DONE`.
    Mutates and returns `state`; never raises for an expected failure mode
    (missing repo info, failing CI, a failed git/GitHub call) -- those are
    all reported via `state.status` / `state.error`.
    """
    if state.status != TaskStatus.DONE:
        state.add_log(
            AGENT_NAME,
            f"Refusing to finalize: status is '{state.status.value}', not done.",
            level="error",
        )
        return state

    if not state.repo_path or not state.repo_url:
        state.status = TaskStatus.FAILED
        state.error = "Publisher requires both repo_path and repo_url on state."
        state.add_log(AGENT_NAME, state.error, level="error")
        return state

    repo_path = Path(state.repo_path)

    # --- 1. CI gate --------------------------------------------------------
    logger.info("[%s] Running CI gate (ruff + mypy).", AGENT_NAME)
    state.add_log(AGENT_NAME, "Running CI gate (ruff + mypy).")

    active_sandbox = sandbox if sandbox is not None else await get_sandbox()
    ci_result = await run_ci_checks(active_sandbox, repo_path)
    state.ci_result = ci_result

    for check in ci_result.checks:
        state.add_log(
            AGENT_NAME,
            f"CI check '{check.name}': {'PASSED' if check.passed else 'FAILED'}",
            level="info" if check.passed else "error",
        )

    if not ci_result.passed:
        failed_names = ", ".join(check.name for check in ci_result.checks if not check.passed)
        state.status = TaskStatus.FAILED
        state.error = f"CI checks failed: {failed_names}"
        state.add_log(AGENT_NAME, state.error, level="error")
        logger.warning("[%s] CI gate failed (%s); not opening a PR.", AGENT_NAME, failed_names)
        return state

    # --- 2. Branch, commit, push --------------------------------------------
    branch = _branch_name(state.task)
    logger.info("[%s] Creating branch '%s'.", AGENT_NAME, branch)
    branch_result = await create_branch(orchestrator, str(repo_path), branch)
    if not branch_result.success:
        state.status = TaskStatus.FAILED
        state.error = f"Failed to create branch '{branch}'."
        state.add_log(AGENT_NAME, state.error, level="error")
        return state
    state.branch_name = branch
    state.add_log(AGENT_NAME, f"Created branch '{branch}'.")

    commit_message = state.task if not state.plan.summary else f"{state.task}\n\n{state.plan.summary}"
    commit_result = await commit_all(orchestrator, str(repo_path), commit_message)
    if not commit_result.success:
        state.status = TaskStatus.FAILED
        state.error = "Failed to commit changes."
        state.add_log(AGENT_NAME, state.error, level="error")
        return state
    state.add_log(AGENT_NAME, f"Committed changes ({commit_result.commit_hash or 'no hash reported'}).")

    push_result = await push_branch(orchestrator, str(repo_path), branch)
    if not push_result.success:
        state.status = TaskStatus.FAILED
        state.error = f"Failed to push branch '{branch}'."
        state.add_log(AGENT_NAME, state.error, level="error")
        return state
    state.add_log(AGENT_NAME, f"Pushed branch '{branch}'.")

    # --- 3. Open the pull request --------------------------------------------
    try:
        owner, repo = _parse_owner_repo(state.repo_url)
    except ValueError as exc:
        state.status = TaskStatus.FAILED
        state.error = str(exc)
        state.add_log(AGENT_NAME, state.error, level="error")
        return state

    title = state.task if len(state.task) <= 72 else state.task[:69] + "..."
    body = build_pr_body(state)
    pr_result = await open_pull_request(
        orchestrator, owner, repo, head=branch, base=base_branch, title=title, body=body
    )
    if not pr_result.success:
        state.status = TaskStatus.FAILED
        state.error = "Failed to open pull request."
        state.add_log(AGENT_NAME, state.error, level="error")
        return state

    state.pr_url = pr_result.url
    state.add_log(AGENT_NAME, f"Opened pull request: {pr_result.url}")
    logger.info("[%s] Opened pull request: %s", AGENT_NAME, pr_result.url)
    return state


class PublisherAgent(BaseAgent):
    """`BaseAgent` wrapper around `finalize_and_open_pr`, for pipeline-style use."""

    name = AGENT_NAME

    def __init__(
        self,
        orchestrator: MCPOrchestrator,
        *,
        sandbox: Sandbox | None = None,
        base_branch: str = "main",
    ) -> None:
        super().__init__(orchestrator)
        self._sandbox = sandbox
        self._base_branch = base_branch

    async def run(self, state: AgentState) -> AgentState:
        return await finalize_and_open_pr(
            self.orchestrator, state, sandbox=self._sandbox, base_branch=self._base_branch
        )
