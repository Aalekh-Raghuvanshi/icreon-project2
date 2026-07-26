"""
Coder Agent.

Future responsibility: work through `state.plan.steps`, generate code
changes (via an LLM), and record them as `Patch` objects in `state.patches`
-- typically by reading relevant files (Filesystem MCP), drafting a diff, and
optionally writing it back to disk ready for review.

NOT IMPLEMENTED YET (by design -- see `agents/base.py`).
"""

from __future__ import annotations

from ai_swe.agents.base import BaseAgent
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)


class CoderAgent(BaseAgent):
    """Generates code changes to implement the plan. Placeholder implementation."""

    name = "coder"

    async def run(self, state: AgentState) -> AgentState:
        logger.info("[coder] Coding not yet implemented; passing state through.")
        state.add_log(self.name, "Code generation is not implemented yet in this milestone.")
        state.status = TaskStatus.REVIEWING
        return state
