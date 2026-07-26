"""
Agent orchestrator: a LangGraph `StateGraph` that routes a task through the
Planner -> Coder -> Reviewer -> Executor agents.

This is distinct from `ai_swe.mcp.client.MCPOrchestrator` (which manages MCP
*server* connections). This module is the *agent* orchestrator: it decides
which agent runs next based on the shared `AgentState`.

Routing is driven entirely by `state.status` (a `TaskStatus`), which each
agent advances when it finishes its (currently placeholder) work:

    PENDING -> [planner] -> CODING -> [coder] -> REVIEWING
            -> [reviewer] -> EXECUTING -> [executor] -> DONE

If any agent sets `state.status = FAILED`, the graph short-circuits straight
to END regardless of which node just ran -- this is the hook future
milestones will use for real error handling (e.g. a failing test suite
should stop the pipeline rather than march on to "done").

Today, every agent is a placeholder (see `ai_swe/agents/*.py`), so running
this graph end-to-end simply demonstrates that the routing works; it does
not perform any real planning or code changes yet.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai_swe.agents.coder import CoderAgent
from ai_swe.agents.executor import ExecutionAgent
from ai_swe.agents.planner import PlannerAgent
from ai_swe.agents.reviewer import ReviewerAgent
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.state import AgentState, TaskStatus

# Node names, used both when registering nodes and when routing between them.
PLANNER = "planner"
CODER = "coder"
REVIEWER = "reviewer"
EXECUTOR = "executor"


def _route_after(
    current_status_on_success: TaskStatus, next_node: str
) -> Callable[[AgentState], str]:
    """
    Build a routing function for use with `add_conditional_edges`.

    Returns `next_node` if the agent that just ran left the state in
    `current_status_on_success`; routes to END on `FAILED` (or any other
    unexpected status), so a failure anywhere in the pipeline stops the run
    instead of silently continuing.
    """

    def _route(state: AgentState) -> str:
        if state.status == TaskStatus.FAILED:
            return END
        if state.status == current_status_on_success:
            return next_node
        return END

    return _route


def build_agent_graph(orchestrator: MCPOrchestrator) -> CompiledStateGraph[AgentState]:
    """
    Build and compile the multi-agent routing graph.

    Args:
        orchestrator: A (connected) `MCPOrchestrator`, injected into every
            agent so they can call Git/GitHub/Filesystem tools as needed.

    Returns:
        A compiled LangGraph graph, ready for `.ainvoke(AgentState(...))`.
    """
    planner = PlannerAgent(orchestrator)
    coder = CoderAgent(orchestrator)
    reviewer = ReviewerAgent(orchestrator)
    executor = ExecutionAgent(orchestrator)

    builder = StateGraph(AgentState)
    builder.add_node(PLANNER, planner.run)
    builder.add_node(CODER, coder.run)
    builder.add_node(REVIEWER, reviewer.run)
    builder.add_node(EXECUTOR, executor.run)

    builder.set_entry_point(PLANNER)
    builder.add_conditional_edges(
        PLANNER, _route_after(TaskStatus.CODING, CODER), {CODER: CODER, END: END}
    )
    builder.add_conditional_edges(
        CODER, _route_after(TaskStatus.REVIEWING, REVIEWER), {REVIEWER: REVIEWER, END: END}
    )
    builder.add_conditional_edges(
        REVIEWER, _route_after(TaskStatus.EXECUTING, EXECUTOR), {EXECUTOR: EXECUTOR, END: END}
    )
    builder.add_edge(EXECUTOR, END)

    return builder.compile()


async def run_task(orchestrator: MCPOrchestrator, state: AgentState) -> AgentState:
    """
    Convenience helper: build the graph and run it once for a given initial state.

    LangGraph returns a plain `dict` from `ainvoke` (not the original Pydantic
    instance), so we re-validate it back into an `AgentState` here to give
    callers a properly typed object.
    """
    graph = build_agent_graph(orchestrator)
    result = await graph.ainvoke(state)
    return AgentState.model_validate(result)
