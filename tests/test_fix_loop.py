"""
End-to-end tests for the Reviewer <-> Coder auto-fix loop through the full
LangGraph orchestrator (`ai_swe.orchestrator.graph.run_task`).

The Planner, Coder, and Reviewer each call an LLM in their normal form, so we
monkeypatch `_call_llm` on each (the same seam used in
`test_orchestrator_graph.py`) to keep the run fully offline. The Executor is
made to fail-then-pass (or fail forever) by monkeypatching
`ai_swe.agents.executor.run_tests` directly, rather than by touching a real
sandbox/subprocess -- this is the one piece of real, non-LLM work the
Executor does, and controlling it directly is the simplest way to drive the
loop deterministically.
"""

from __future__ import annotations

import json

import pytest

from ai_swe.agents.coder import CoderAgent
from ai_swe.agents.planner import PlannerAgent
from ai_swe.agents.reviewer import ReviewerAgent
from ai_swe.orchestrator.graph import run_task
from ai_swe.state import AgentState, TaskStatus, TestResult


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


def _fake_review_json() -> str:
    """A schema-valid `ReviewOutcome` JSON string reporting one failure in the dummy test file."""
    return json.dumps(
        {
            "verdict": "needs_fix",
            "errors": [
                {
                    "file_path": "tests/test_dummy.py",
                    "line": 2,
                    "error_type": "AssertionError",
                    "message": "assert False",
                    "traceback_excerpt": "tests/test_dummy.py:2: AssertionError",
                    "suggested_fix": "Fix the failing assertion.",
                }
            ],
        }
    )


@pytest.fixture(autouse=True)
def _stub_planner_and_coder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_planner_call_llm(self: PlannerAgent, system_prompt: str, user_prompt: str) -> str:
        return _fake_plan_json()

    async def fake_coder_call_llm(self: CoderAgent, system_prompt: str, user_prompt: str) -> str:
        return _fake_changeset_json()

    monkeypatch.setattr(PlannerAgent, "_call_llm", fake_planner_call_llm)
    monkeypatch.setattr(CoderAgent, "_call_llm", fake_coder_call_llm)


@pytest.mark.asyncio
async def test_fix_loop_recovers_after_one_failure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Executor fails the first run, passes the second -- the graph should
    loop Coder -> Executor -> Reviewer exactly once before reaching DONE."""

    run_count = {"n": 0}

    async def fake_run_tests(repo_path, sandbox):
        run_count["n"] += 1
        if run_count["n"] == 1:
            return [TestResult(name="pytest", passed=False, output="AssertionError: assert False")]
        return [TestResult(name="pytest", passed=True, output="1 passed")]

    monkeypatch.setattr("ai_swe.agents.executor.run_tests", fake_run_tests)

    review_calls = {"n": 0}

    async def fake_reviewer_call_llm(self: ReviewerAgent, system_prompt: str, user_prompt: str) -> str:
        review_calls["n"] += 1
        return _fake_review_json()

    monkeypatch.setattr(ReviewerAgent, "_call_llm", fake_reviewer_call_llm)

    result = await run_task(
        orchestrator=None,
        state=AgentState(task="Add tests", repo_path=str(tmp_path)),
    )

    assert result.status == TaskStatus.DONE
    assert result.fix_attempts == 1
    # The graph looped Coder -> Executor -> Reviewer exactly once: the
    # Executor ran twice (fail, then pass) and the Reviewer's LLM was only
    # consulted for the one failing pass (it approves the second, passing
    # pass without needing to call the LLM at all).
    assert run_count["n"] == 2
    assert review_calls["n"] == 1
    assert result.test_results == [TestResult(name="pytest", passed=True, output="1 passed")]
    assert result.errors == []

    agents_that_ran = list(dict.fromkeys(entry.agent for entry in result.logs))
    assert agents_that_ran == ["planner", "coder", "executor", "reviewer"]


@pytest.mark.asyncio
async def test_fix_loop_gives_up_after_max_attempts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Executor fails every time -- the graph should retry up to
    `max_fix_attempts` times and then terminate at FAILED."""

    async def fake_run_tests(repo_path, sandbox):
        return [TestResult(name="pytest", passed=False, output="AssertionError: assert False")]

    monkeypatch.setattr("ai_swe.agents.executor.run_tests", fake_run_tests)

    async def fake_reviewer_call_llm(self: ReviewerAgent, system_prompt: str, user_prompt: str) -> str:
        return _fake_review_json()

    monkeypatch.setattr(ReviewerAgent, "_call_llm", fake_reviewer_call_llm)

    state = AgentState(task="Add tests", repo_path=str(tmp_path))
    result = await run_task(orchestrator=None, state=state)

    assert result.status == TaskStatus.FAILED
    assert result.error == "Max fix attempts reached"
    assert result.fix_attempts == result.max_fix_attempts
