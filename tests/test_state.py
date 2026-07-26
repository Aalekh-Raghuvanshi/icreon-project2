"""Tests for `ai_swe.state` -- the shared Pydantic state models."""

from ai_swe.state import AgentState, FileRecord, Plan, PlanStep, TaskStatus


def test_agent_state_defaults() -> None:
    state = AgentState(task="Fix the bug")
    assert state.status == TaskStatus.PENDING
    assert state.plan == Plan()
    assert state.files == []
    assert state.patches == []
    assert state.test_results == []
    assert state.logs == []
    assert state.error is None


def test_add_log_appends_and_returns_self() -> None:
    state = AgentState(task="Fix the bug")
    result = state.add_log("planner", "started planning")
    assert result is state
    assert len(state.logs) == 1
    assert state.logs[0].agent == "planner"
    assert state.logs[0].message == "started planning"
    assert state.logs[0].level == "info"


def test_plan_with_steps_round_trips_through_json() -> None:
    plan = Plan(summary="Do the thing", steps=[PlanStep(id="step-1", description="Do part one")])
    state = AgentState(task="Fix the bug", plan=plan)

    dumped = state.model_dump_json()
    restored = AgentState.model_validate_json(dumped)

    assert restored.plan.summary == "Do the thing"
    assert restored.plan.steps[0].id == "step-1"
    assert restored.plan.steps[0].done is False


def test_file_record_defaults_to_file_not_directory() -> None:
    record = FileRecord(path="README.md")
    assert record.is_dir is False
    assert record.size_bytes is None
