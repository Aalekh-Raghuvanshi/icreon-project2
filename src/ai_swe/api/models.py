"""
Pydantic request/response models for the FastAPI API layer.

These are deliberately separate from `ai_swe.state.AgentState` so the API
surface stays stable even as the internal agent state evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    """POST /api/sessions — start a new agent pipeline run."""

    task: str = Field(description="Natural-language task to accomplish, e.g. 'Add JWT auth'.")
    repo_path: str = Field(description="Absolute path to a locally-checked-out repository.")
    repo_url: str | None = Field(
        default=None,
        description="GitHub repository URL. Required only when open_pr=True.",
    )
    open_pr: bool = Field(default=False, description="Open a pull request after a successful run.")
    base_branch: str = Field(default="main", description="Base branch for the pull request.")


class CloneRepoRequest(BaseModel):
    """POST /api/repo/clone — clone a remote repository."""

    repo_url: str = Field(description="GitHub repository URL to clone.")
    dest_path: str | None = Field(
        default=None,
        description="Destination directory.  Defaults to workdir/<repo-name>.",
    )


class SearchFilesRequest(BaseModel):
    """GET /api/repo/search — search files in a cloned repository."""

    path: str = Field(description="Absolute path to the repository root.")
    query: str = Field(description="Search query string.")
    max_results: int = Field(default=50, ge=1, le=500)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    """Lightweight summary returned by GET /api/sessions."""

    session_id: str
    task: str
    status: str
    repo_path: str | None = None
    repo_url: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    current_agent: str | None = None
    estimated_cost_usd: float = 0.0
    total_tokens: int = 0
    pr_url: str | None = None


class PlanStepOut(BaseModel):
    id: str
    description: str
    done: bool
    files_involved: list[str] = []
    reasoning: str = ""
    risk_level: str = "low"


class PlanOut(BaseModel):
    summary: str | None = None
    steps: list[PlanStepOut] = []
    files_to_create: list[str] = []
    files_to_modify: list[str] = []
    architecture_impact: str = ""
    testing_strategy: str = ""


class PatchOut(BaseModel):
    file_path: str
    diff: str
    description: str | None = None


class TestResultOut(BaseModel):
    name: str
    passed: bool
    output: str | None = None


class LogEntryOut(BaseModel):
    timestamp: datetime
    agent: str
    message: str
    level: str
    input_summary: str | None = None
    decision: str | None = None
    output_summary: str | None = None
    execution_time_ms: int | None = None


class CIResultOut(BaseModel):
    passed: bool
    output: str = ""


class SessionDetail(BaseModel):
    """Full session state returned by GET /api/sessions/{id}."""

    session_id: str
    task: str
    status: str
    repo_path: str | None = None
    repo_url: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None
    current_agent: str | None = None
    progress: float = 0.0
    plan: PlanOut = Field(default_factory=PlanOut)
    patches: list[PatchOut] = []
    test_results: list[TestResultOut] = []
    logs: list[LogEntryOut] = []
    error: str | None = None
    fix_attempts: int = 0
    ci_result: CIResultOut | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    retry_count: int = 0


class CloneResult(BaseModel):
    success: bool
    dest_path: str
    branch: str | None = None
    commit_hash: str | None = None
    message: str = ""


class RepoTreeNode(BaseModel):
    path: str
    is_dir: bool
    size_bytes: int | None = None
    children: list["RepoTreeNode"] = []


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    mcp_servers: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# WebSocket event models
# ---------------------------------------------------------------------------


class AgentEvent(BaseModel):
    """A single event emitted over the WebSocket during a pipeline run."""

    event: str  # progress | agent_started | agent_finished | log | error | done
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    # Payload fields (all optional, present depending on event type)
    agent: str | None = None
    message: str | None = None
    status: str | None = None
    progress: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
