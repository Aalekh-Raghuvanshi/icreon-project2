"""
Tests for `ai_swe.orchestrator.graph` -- verifies routing logic without
needing live MCP server connections (agents don't touch the orchestrator in
their current placeholder form, so `None` is a safe stand-in).
"""

import pytest

from ai_swe.orchestrator.graph import run_task
from ai_swe.state import AgentState, TaskStatus


@pytest.mark.asyncio
async def test_graph_routes_through_all_agents_to_done() -> None:
    result = await run_task(orchestrator=None, state=AgentState(task="Add tests"))

    assert result.status == TaskStatus.DONE
    agents_that_ran = [entry.agent for entry in result.logs]
    assert agents_that_ran == ["planner", "coder", "reviewer", "executor"]


@pytest.mark.asyncio
async def test_graph_preserves_original_task_description() -> None:
    result = await run_task(orchestrator=None, state=AgentState(task="Refactor the parser"))
    assert result.task == "Refactor the parser"
