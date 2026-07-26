"""
Shared base class for all agents in the pipeline.

Every agent (Planner, Coder, Reviewer, Executor) is a small, single-purpose
callable that takes the current `AgentState` and returns an updated one.
Implementing them as classes (rather than bare functions) gives each agent a
place to hold dependencies it needs (an `MCPOrchestrator`, an LLM client,
etc.) without reaching into globals.

IMPORTANT: Per the current project milestone, only the *foundation* is being
built. Every concrete agent below is intentionally a placeholder: it logs
that it ran, advances `state.status`, and returns immediately without doing
any real planning, coding, review, or execution. This lets us wire up and
test the full routing graph today, and swap in real logic (LLM calls, patch
generation, sandboxed test execution, ...) in future milestones without
touching the graph shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.state import AgentState


class BaseAgent(ABC):
    """Common interface every agent in the pipeline implements."""

    #: Human-readable name used in log entries (e.g. "planner", "coder").
    name: str = "base-agent"

    def __init__(self, orchestrator: MCPOrchestrator) -> None:
        """
        Args:
            orchestrator: The shared `MCPOrchestrator`, giving every agent
                access to Git/GitHub/Filesystem tools without needing its own
                connections.
        """
        self.orchestrator = orchestrator

    @abstractmethod
    async def run(self, state: AgentState) -> AgentState:
        """
        Execute this agent's work against `state` and return the updated state.

        Implementations should treat `state` as input and either mutate it in
        place and return it, or return a new instance -- LangGraph will merge
        whichever is returned back into the graph's state.
        """
        raise NotImplementedError
