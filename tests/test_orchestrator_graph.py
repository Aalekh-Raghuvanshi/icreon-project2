"""
Tests for `ai_swe.orchestrator.graph` -- verifies routing logic without
needing live MCP server connections or a real LLM.

The Planner and Coder agents each call an LLM in their normal (non-placeholder)
form, so running the graph end-to-end requires stubbing those calls out.
`graph.build_agent_graph()` constructs its agents internally (it only takes an
`MCPOrchestrator`, not pre-built agents or an `llm=`), so the constructor-arg
injection used in `test_planner.py` (`PlannerAgent(orchestrator=None,
llm=mock_llm)`) isn't available here. Instead we monkeypatch each agent's
`_call_llm` -- the same seam, one level lower -- to return canned,
schema-valid JSON. This keeps the test fully offline and deterministic: no
GROQ_API_KEY, no network access, no dependency on real LLM output.

The Executor and Reviewer are exercised for real (not stubbed): the tmp_path
repo has no test-framework marker files (no pyproject.toml, package.json,
...), so the Executor's `run_tests()` finds nothing to run and hands off to
the Reviewer with an empty `test_results` -- which the Reviewer treats as
"no failures", approving the run without ever needing to call its own LLM.
This exercises the real Planner -> Coder -> Executor -> Reviewer -> DONE
path end to end.
"""

from __future__ import annotations

import json

import pytest

from ai_swe.agents.coder import CoderAgent
from ai_swe.agents.planner import PlannerAgent
from ai_swe.orchestrator.graph import run_task
from ai_swe.state import AgentState, TaskStatus


def _fake_plan_json() -> str:
    """A minimal, schema-valid `ImplementationPlan` JSON string (see `planner_models.py`)."""
    return json.dumps(
        {
            "task": "stub task",
            "summary": "Add a single dummy test file.",
            "architecture_impact": "None -- purely additive test file.",
            "steps": [
                {
                    "step_number": 1,
                    "goal": "Create a dummy test file",
                    "files_involved": ["tests/test_dummy.py"],
                    "reasoning": "Need at least one test to exercise the suite.",
                    "dependencies": [],
                    "expected_outcome": "tests/test_dummy.py exists and passes.",
                    "risk_level": "low",
                }
            ],
            "estimated_complexity": "low",
            "files_to_create": ["tests/test_dummy.py"],
            "files_to_modify": [],
            "files_to_delete": [],
            "testing_strategy": "Run pytest.",
            "risks_and_mitigations": [],
        }
    )


def _fake_changeset_json() -> str:
    """A minimal, schema-valid `StepChangeSet` JSON string (see `coder_models.py`)."""
    return json.dumps(
        {
            "step_id": "step-1",
            "rationale": "Add a trivial passing test.",
            "edits": [
                {
                    "file_path": "tests/test_dummy.py",
                    "action": "create",
                    "new_content": "def test_dummy():\n    assert True\n",
                    "search_replace": [],
                    "description": "Add dummy test",
                }
            ],
        }
    )


@pytest.fixture(autouse=True)
def _stub_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Replace `PlannerAgent._call_llm` and `CoderAgent._call_llm` with stubs
    that return canned JSON, so the graph never constructs a real `ChatGroq`
    client or needs `GROQ_API_KEY`. The Reviewer only calls its LLM when it
    sees a failing test, which doesn't happen in this module's tests (see
    module docstring), so it needs no stub here.
    """

    async def fake_planner_call_llm(self: PlannerAgent, system_prompt: str, user_prompt: str) -> str:
        return _fake_plan_json()

    async def fake_coder_call_llm(self: CoderAgent, system_prompt: str, user_prompt: str) -> str:
        return _fake_changeset_json()

    monkeypatch.setattr(PlannerAgent, "_call_llm", fake_planner_call_llm)
    monkeypatch.setattr(CoderAgent, "_call_llm", fake_coder_call_llm)


@pytest.mark.asyncio
async def test_graph_routes_through_all_agents_to_done(tmp_path) -> None:
    result = await run_task(
        orchestrator=None,
        state=AgentState(task="Add tests", repo_path=str(tmp_path)),
    )

    assert result.status == TaskStatus.DONE
    # Planner/Coder each log several entries per run (progress messages), so
    # dedupe by first occurrence to get the order agents actually ran in.
    agents_that_ran = list(dict.fromkeys(entry.agent for entry in result.logs))
    assert agents_that_ran == ["planner", "coder", "executor", "reviewer"]


@pytest.mark.asyncio
async def test_graph_preserves_original_task_description(tmp_path) -> None:
    result = await run_task(
        orchestrator=None,
        state=AgentState(task="Refactor the parser", repo_path=str(tmp_path)),
    )
    assert result.task == "Refactor the parser"
