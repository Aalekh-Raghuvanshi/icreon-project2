"""
Shared state definitions for the AI SWE Agent.

`AgentState` is the single object passed between every node of the LangGraph
orchestrator (Planner -> Coder -> Reviewer -> Executor). Every agent reads
what it needs from the state and returns an updated copy, so the whole
pipeline is transparent and replayable -- at any point we can serialize
`AgentState` to JSON and inspect exactly what has happened so far.

All models are Pydantic v2 `BaseModel`s so we get:
  * Runtime validation (bad data fails loudly, not silently).
  * Free JSON (de)serialization, useful for logging/checkpointing.
  * Editor/typing support everywhere the state is used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """High-level lifecycle status of a task moving through the agent pipeline."""

    PENDING = "pending"
    PLANNING = "planning"
    CODING = "coding"
    REVIEWING = "reviewing"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


class PlanStep(BaseModel):
    """A single, atomic step of a plan produced by the Planner agent."""

    id: str = Field(description="Stable identifier for this step, e.g. 'step-1'.")
    description: str = Field(description="Human-readable description of the work to do.")
    done: bool = Field(default=False, description="Whether this step has been completed.")


class Plan(BaseModel):
    """The overall plan for accomplishing the task, made up of ordered steps."""

    summary: str | None = Field(default=None, description="One-paragraph summary of the plan.")
    steps: list[PlanStep] = Field(default_factory=list)


class FileRecord(BaseModel):
    """Metadata about a single file or directory discovered in the repository."""

    path: str = Field(description="Path relative to the repository root.")
    is_dir: bool = Field(default=False)
    size_bytes: int | None = Field(default=None, description="File size, if known.")


class Patch(BaseModel):
    """A proposed (or applied) code change produced by the Coder agent."""

    file_path: str = Field(description="Path of the file being changed, relative to repo root.")
    diff: str = Field(description="Unified diff text representing the change.")
    description: str | None = Field(default=None, description="Why this change was made.")


class TestResult(BaseModel):
    """The outcome of running a single test or test suite via the Execution agent."""

    name: str = Field(description="Name of the test or command that was run.")
    passed: bool
    output: str | None = Field(default=None, description="Captured stdout/stderr, truncated.")


class LogEntry(BaseModel):
    """A single structured log line recorded by an agent during task execution."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent: str = Field(description="Name of the agent/component that produced this entry.")
    message: str
    level: str = Field(default="info")


class AgentState(BaseModel):
    """
    The single shared state object threaded through the entire agent graph.

    This is intentionally a plain, serializable Pydantic model (not a
    TypedDict) so it can be validated, logged, and persisted as-is. LangGraph
    natively supports Pydantic models as a graph's state schema.
    """

    # --- What we're trying to do -------------------------------------------
    task: str = Field(description="Natural-language description of the task to accomplish.")

    # --- Repository context --------------------------------------------------
    repo_url: str | None = Field(default=None, description="Source URL of the repository.")
    repo_path: str | None = Field(default=None, description="Local filesystem path to the clone.")

    # --- Working data, populated as agents run --------------------------------
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    plan: Plan = Field(default_factory=Plan)
    files: list[FileRecord] = Field(default_factory=list)
    patches: list[Patch] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    logs: list[LogEntry] = Field(default_factory=list)

    # --- Failure tracking ------------------------------------------------------
    error: str | None = Field(default=None, description="Set when `status` is FAILED.")

    def add_log(self, agent: str, message: str, level: str = "info") -> AgentState:
        """Append a log entry and return `self` (for convenient chaining)."""
        self.logs.append(LogEntry(agent=agent, message=message, level=level))
        return self

    model_config = {
        "use_enum_values": False,
    }
