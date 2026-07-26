"""
Planner Agent.

Future responsibility: read `state.task` and the repository's file listing,
and produce a `Plan` (ordered `PlanStep`s) describing how to accomplish the
task -- typically by prompting an LLM with the task description and relevant
repository context.

NOT IMPLEMENTED YET (by design -- see `agents/base.py`). This placeholder
only advances the pipeline's status so the orchestrator graph can be wired
and tested end-to-end today.
"""

from __future__ import annotations

from ai_swe.agents.base import BaseAgent
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)


class PlannerAgent(BaseAgent):
    """Produces a plan of action for the task. Placeholder implementation."""

    name = "planner"

    async def run(self, state: AgentState) -> AgentState:
        logger.info("[planner] Planning not yet implemented; passing state through.")
        state.add_log(self.name, "Planning is not implemented yet in this milestone.")
        state.status = TaskStatus.CODING  # Advance the pipeline for future milestones.
        return state
