"""
Execution Agent.

Future responsibility: apply approved `state.patches` to the repository and
run the project's test suite (or other verification commands), recording
outcomes as `TestResult`s in `state.test_results`.

NOT IMPLEMENTED YET (by design -- see `agents/base.py`).
"""

from __future__ import annotations

from ai_swe.agents.base import BaseAgent
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)


class ExecutionAgent(BaseAgent):
    """Applies patches and runs tests. Placeholder implementation."""

    name = "executor"

    async def run(self, state: AgentState) -> AgentState:
        logger.info("[executor] Execution not yet implemented; passing state through.")
        state.add_log(self.name, "Patch execution/testing is not implemented yet in this milestone.")
        state.status = TaskStatus.DONE
        return state
