"""
Tests for the Reviewer agent and its supporting modules.

Covers:
  - ReviewOutcome / ErrorReport Pydantic validation (valid & invalid)
  - validate_review() JSON parsing with markdown fences
  - Reviewer prompt construction
  - Retry logic with a mock LLM
  - Full ReviewerAgent.run(): approve-on-pass, needs_fix loop-back with step
    reset, and giving up once max_fix_attempts is reached

`orchestrator=None` throughout, and the LLM is always a mock -- no MCP
server, no network, fully offline.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from ai_swe.agents.reviewer import ReviewerAgent
from ai_swe.agents.reviewer_models import ReviewOutcome, ReviewVerdict, validate_review
from ai_swe.agents.reviewer_prompt import (
    build_retry_prompt,
    build_review_prompt,
    build_system_prompt,
)
from ai_swe.state import AgentState, ErrorReport, Plan, PlanStep, TaskStatus, TestResult

# ═══════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════


def _make_valid_outcome_dict(**overrides) -> dict:
    outcome = {
        "verdict": "needs_fix",
        "errors": [
            {
                "file_path": "util.py",
                "line": 12,
                "error_type": "AssertionError",
                "message": "assert 1 == 2",
                "traceback_excerpt": "util.py:12: AssertionError",
                "suggested_fix": "Fix the off-by-one in add().",
            }
        ],
    }
    outcome.update(overrides)
    return outcome


# ═══════════════════════════════════════════════════════════════════
# 1. Pydantic validation
# ═══════════════════════════════════════════════════════════════════


class TestErrorReportValidation:
    def test_minimal_error_report(self):
        err = ErrorReport(
            error_type="ImportError",
            message="No module named 'foo'",
            traceback_excerpt="ImportError: No module named 'foo'",
        )
        assert err.file_path is None
        assert err.line is None
        assert err.suggested_fix == ""


class TestReviewOutcomeValidation:
    def test_valid_outcome_parses(self):
        outcome = ReviewOutcome.model_validate(_make_valid_outcome_dict())
        assert outcome.verdict == ReviewVerdict.NEEDS_FIX
        assert len(outcome.errors) == 1
        assert outcome.errors[0].file_path == "util.py"

    def test_approve_with_no_errors(self):
        outcome = ReviewOutcome.model_validate({"verdict": "approve", "errors": []})
        assert outcome.verdict == ReviewVerdict.APPROVE
        assert outcome.errors == []

    def test_errors_default_to_empty_list(self):
        outcome = ReviewOutcome.model_validate({"verdict": "approve"})
        assert outcome.errors == []

    def test_invalid_verdict_rejected(self):
        data = _make_valid_outcome_dict()
        data["verdict"] = "maybe"
        with pytest.raises(ValidationError):
            ReviewOutcome.model_validate(data)

    def test_missing_error_type_rejected(self):
        data = _make_valid_outcome_dict()
        del data["errors"][0]["error_type"]
        with pytest.raises(ValidationError):
            ReviewOutcome.model_validate(data)


# ═══════════════════════════════════════════════════════════════════
# 2. validate_review() -- JSON parsing with markdown fences
# ═══════════════════════════════════════════════════════════════════


class TestValidateReview:
    def test_raw_json(self):
        raw = json.dumps(_make_valid_outcome_dict())
        outcome = validate_review(raw)
        assert outcome.verdict == ReviewVerdict.NEEDS_FIX

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(_make_valid_outcome_dict()) + "\n```"
        outcome = validate_review(raw)
        assert len(outcome.errors) == 1

    def test_markdown_fenced_no_lang(self):
        raw = "```\n" + json.dumps({"verdict": "approve", "errors": []}) + "\n```"
        outcome = validate_review(raw)
        assert outcome.verdict == ReviewVerdict.APPROVE

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_review("{bad json}")

    def test_valid_json_invalid_schema_raises(self):
        with pytest.raises(ValidationError):
            validate_review('{"verdict": "maybe"}')


# ═══════════════════════════════════════════════════════════════════
# 3. Prompt construction
# ═══════════════════════════════════════════════════════════════════


class TestPrompts:
    def test_system_prompt_contains_schema(self):
        prompt = build_system_prompt()
        assert "verdict" in prompt
        assert "error_type" in prompt
        assert "Senior Engineer" in prompt

    def test_review_prompt_includes_failing_output(self):
        results = [TestResult(name="pytest", passed=False, output="AssertionError: boom")]
        prompt = build_review_prompt("Add a feature", "Plan summary here", results)
        assert "Add a feature" in prompt
        assert "Plan summary here" in prompt
        assert "AssertionError: boom" in prompt
        assert "pytest" in prompt

    def test_retry_prompt_includes_error(self):
        prompt = build_retry_prompt("Missing field 'verdict'", attempt=2)
        assert "Missing field 'verdict'" in prompt
        assert "attempt 1" in prompt


# ═══════════════════════════════════════════════════════════════════
# 4. Retry logic with mock LLM
# ═══════════════════════════════════════════════════════════════════


class TestReviewerRetry:
    @pytest.mark.asyncio
    async def test_retry_on_invalid_then_valid(self):
        valid_json = json.dumps(_make_valid_outcome_dict())
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(content="not valid json at all {{{"),
                MagicMock(content=valid_json),
            ]
        )

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        results = [TestResult(name="pytest", passed=False, output="boom")]
        outcome = await agent._review_with_retry("task", "summary", results)

        assert outcome.verdict == ReviewVerdict.NEEDS_FIX
        assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        results = [TestResult(name="pytest", passed=False, output="boom")]

        with pytest.raises(ValueError, match="Failed to produce a valid review"):
            await agent._review_with_retry("task", "summary", results)


# ═══════════════════════════════════════════════════════════════════
# 5. Full ReviewerAgent.run()
# ═══════════════════════════════════════════════════════════════════


class TestReviewerRun:
    @pytest.mark.asyncio
    async def test_all_tests_passing_approves_without_llm_call(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock()  # should never be called

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Add a feature",
            test_results=[TestResult(name="pytest", passed=True, output="1 passed")],
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.DONE
        assert result.fix_attempts == 0
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_test_results_approves_without_llm_call(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock()

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(task="Nothing to verify", test_results=[])

        result = await agent.run(state)

        assert result.status == TaskStatus.DONE
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_loops_back_to_coder_and_resets_affected_step(self):
        outcome_json = json.dumps(
            {
                "verdict": "needs_fix",
                "errors": [
                    {
                        "file_path": "util.py",
                        "line": 3,
                        "error_type": "AssertionError",
                        "message": "assert False",
                        "traceback_excerpt": "util.py:3: AssertionError",
                        "suggested_fix": "Fix the assertion.",
                    }
                ],
            }
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=outcome_json))

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Add a feature",
            plan=Plan(
                steps=[
                    PlanStep(id="step-1", description="Touch util.py", files_involved=["util.py"], done=True),
                    PlanStep(id="step-2", description="Touch other.py", files_involved=["other.py"], done=True),
                ]
            ),
            test_results=[TestResult(name="pytest", passed=False, output="AssertionError: assert False")],
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.CODING
        assert result.fix_attempts == 1
        assert len(result.errors) == 1
        assert result.errors[0].file_path == "util.py"
        # Only the step touching the failing file is reset.
        assert result.plan.steps[0].done is False
        assert result.plan.steps[1].done is True

    @pytest.mark.asyncio
    async def test_failure_with_unlocalised_error_resets_all_steps(self):
        outcome_json = json.dumps(
            {
                "verdict": "needs_fix",
                "errors": [
                    {
                        "file_path": None,
                        "line": None,
                        "error_type": "RuntimeError",
                        "message": "something broke",
                        "traceback_excerpt": "RuntimeError: something broke",
                        "suggested_fix": "",
                    }
                ],
            }
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=outcome_json))

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Add a feature",
            plan=Plan(
                steps=[
                    PlanStep(id="step-1", description="Touch util.py", files_involved=["util.py"], done=True),
                    PlanStep(id="step-2", description="Touch other.py", files_involved=["other.py"], done=True),
                ]
            ),
            test_results=[TestResult(name="pytest", passed=False, output="RuntimeError: something broke")],
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.CODING
        assert result.plan.steps[0].done is False
        assert result.plan.steps[1].done is False

    @pytest.mark.asyncio
    async def test_gives_up_after_max_fix_attempts(self):
        outcome_json = json.dumps(_make_valid_outcome_dict())
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=outcome_json))

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Add a feature",
            fix_attempts=3,
            max_fix_attempts=3,
            test_results=[TestResult(name="pytest", passed=False, output="boom")],
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.FAILED
        assert result.error == "Max fix attempts reached"
        assert result.fix_attempts == 3

    @pytest.mark.asyncio
    async def test_review_llm_failure_sets_failed_status(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))

        agent = ReviewerAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Add a feature",
            test_results=[TestResult(name="pytest", passed=False, output="boom")],
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert "Review failed" in result.error
