"""
Reviewer Agent.

Future responsibility: critique `state.patches` for correctness, style, and
alignment with `state.plan` -- typically via an LLM "self-review" pass --
and either approve them for execution or send the state back to the Coder
agent with feedback logged.

NOT IMPLEMENTED YET (by design -- see `agents/base.py`).
"""

from __future__ import annotations

from ai_swe.agents.base import BaseAgent
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)


class ReviewerAgent(BaseAgent):
    """Reviews proposed patches before execution. Placeholder implementation."""

    name = "reviewer"

    async def run(self, state: AgentState) -> AgentState:
        logger.info("[reviewer] Review not yet implemented; passing state through.")
        state.add_log(self.name, "Patch review is not implemented yet in this milestone.")
        state.status = TaskStatus.EXECUTING
        return state
