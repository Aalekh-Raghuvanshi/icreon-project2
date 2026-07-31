"""
Shared state definitions for the AI SWE Agent.

`AgentState` is the single object passed between every node of the LangGraph
orchestrator (Planner -> Coder -> Executor -> Reviewer, with the Reviewer
able to loop back to the Coder for bounded auto-fix retries). Every agent
reads what it needs from the state and returns an updated copy, so the whole
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

    # --- Extended fields (populated by the real Planner agent) ---------------
    files_involved: list[str] = Field(
        default_factory=list,
        description="Relative file paths touched by this step.",
    )
    reasoning: str = Field(
        default="",
        description="Why this step is necessary.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="IDs of steps that must complete before this one.",
    )
    expected_outcome: str = Field(
        default="",
        description="What should be true after this step completes.",
    )
    risk_level: str = Field(
        default="low",
        description="Risk classification: low / medium / high / critical.",
    )


class Plan(BaseModel):
    """The overall plan for accomplishing the task, made up of ordered steps."""

    summary: str | None = Field(default=None, description="One-paragraph summary of the plan.")
    steps: list[PlanStep] = Field(default_factory=list)

    # --- Extended fields (populated by the real Planner agent) ---------------
    architecture_impact: str = Field(
        default="",
        description="How this plan affects the overall codebase architecture.",
    )
    testing_strategy: str = Field(
        default="",
        description="How to verify the changes.",
    )
    files_to_create: list[str] = Field(
        default_factory=list,
        description="New files that will be created.",
    )
    files_to_modify: list[str] = Field(
        default_factory=list,
        description="Existing files that will be modified.",
    )


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


class ErrorReport(BaseModel):
    """A single structured error extracted from a failing test run by the Reviewer."""

    file_path: str | None = Field(
        default=None, description="File the error originates from, if known."
    )
    line: int | None = Field(
        default=None, description="Line number the error originates from, if known."
    )
    error_type: str = Field(description="Kind of error, e.g. AssertionError, ImportError.")
    message: str = Field(description="Human-readable error message.")
    traceback_excerpt: str = Field(description="Relevant excerpt of the traceback/log output.")
    suggested_fix: str = Field(
        default="", description="A minimal suggested fix, if the reviewer identified one."
    )


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

    # --- Auto-fix loop (Reviewer <-> Coder) -------------------------------------
    fix_attempts: int = Field(
        default=0, description="Number of Reviewer-triggered fix attempts made so far."
    )
    max_fix_attempts: int = Field(
        default=3, description="Maximum number of Reviewer-triggered fix attempts allowed."
    )
    errors: list[ErrorReport] = Field(
        default_factory=list,
        description="Structured errors produced by the Reviewer from the latest failing test run.",
    )

    def add_log(self, agent: str, message: str, level: str = "info") -> AgentState:
        """Append a log entry and return `self` (for convenient chaining)."""
        self.logs.append(LogEntry(agent=agent, message=message, level=level))
        return self

    model_config = {
        "use_enum_values": False,
    }
