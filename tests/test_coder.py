"""
Tests for the Coder agent and its supporting modules.

Covers:
  - StepChangeSet / FileEdit Pydantic validation (valid & invalid)
  - validate_changeset() JSON parsing with markdown fences
  - Coder prompt construction
  - Retry logic with a mock LLM
  - Full CoderAgent.run() against a tiny temp repo (files modified, patches
    populated, step marked done, status advances to REVIEWING)
  - A deliberately broken edit fails the run and leaves files untouched

`orchestrator=None` throughout, and the LLM is always a mock -- no MCP
server, no network, fully offline.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from ai_swe.agents.coder_models import (
    EditAction,
    FileEdit,
    StepChangeSet,
    validate_changeset,
)
from ai_swe.agents.coder_prompt import (
    build_coding_prompt,
    build_retry_prompt,
    build_system_prompt,
)
from ai_swe.state import AgentState, Plan, PlanStep, TaskStatus

# ═══════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════


def _make_valid_changeset_dict(**overrides) -> dict:
    changeset = {
        "step_id": "step-1",
        "rationale": "Add a greet() function to util.py as required by the step.",
        "edits": [
            {
                "file_path": "util.py",
                "action": "modify",
                "search_replace": [
                    {"search": "x = 1", "replace": "x = 1\n\ndef greet():\n    return 'hi'"}
                ],
                "description": "Add greet() helper.",
            }
        ],
    }
    changeset.update(overrides)
    return changeset


# ═══════════════════════════════════════════════════════════════════
# 1. Pydantic validation
# ═══════════════════════════════════════════════════════════════════


class TestFileEditValidation:
    def test_create_requires_content_or_search_replace(self):
        with pytest.raises(ValidationError):
            FileEdit(file_path="new.py", action=EditAction.CREATE)

    def test_create_with_new_content_ok(self):
        edit = FileEdit(file_path="new.py", action=EditAction.CREATE, new_content="x = 1\n")
        assert edit.new_content == "x = 1\n"

    def test_delete_requires_nothing(self):
        edit = FileEdit(file_path="gone.py", action=EditAction.DELETE)
        assert edit.action == EditAction.DELETE

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            FileEdit(file_path="x.py", action="rewrite", new_content="x = 1\n")


class TestStepChangeSetValidation:
    def test_valid_changeset_parses(self):
        changeset = StepChangeSet.model_validate(_make_valid_changeset_dict())
        assert changeset.step_id == "step-1"
        assert len(changeset.edits) == 1

    def test_empty_edits_rejected(self):
        data = _make_valid_changeset_dict()
        data["edits"] = []
        with pytest.raises(ValidationError):
            StepChangeSet.model_validate(data)

    def test_missing_rationale_rejected(self):
        data = _make_valid_changeset_dict()
        data["rationale"] = ""
        with pytest.raises(ValidationError):
            StepChangeSet.model_validate(data)


class TestValidateChangeset:
    def test_raw_json(self):
        raw = json.dumps(_make_valid_changeset_dict())
        changeset = validate_changeset(raw)
        assert changeset.step_id == "step-1"

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(_make_valid_changeset_dict()) + "\n```"
        changeset = validate_changeset(raw)
        assert len(changeset.edits) == 1

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_changeset("{bad json}")

    def test_valid_json_invalid_schema_raises(self):
        with pytest.raises(ValidationError):
            validate_changeset('{"step_id": ""}')


# ═══════════════════════════════════════════════════════════════════
# 2. Prompt construction
# ═══════════════════════════════════════════════════════════════════


class TestPrompts:
    def test_system_prompt_contains_schema(self):
        prompt = build_system_prompt()
        assert "step_id" in prompt
        assert "search_replace" in prompt
        assert "Senior Software Engineer" in prompt

    def test_coding_prompt_includes_step_details(self):
        step = PlanStep(
            id="step-1",
            description="Add a greet() helper",
            files_involved=["util.py"],
            reasoning="Needed by callers",
            expected_outcome="util.py exposes greet()",
        )
        prompt = build_coding_prompt(step, "Overall plan summary", "FILE: util.py\nx = 1")
        assert "Add a greet() helper" in prompt
        assert "Overall plan summary" in prompt
        assert "util.py" in prompt

    def test_retry_prompt_includes_error(self):
        prompt = build_retry_prompt("Missing field 'edits'", attempt=2)
        assert "Missing field 'edits'" in prompt
        assert "attempt 1" in prompt


# ═══════════════════════════════════════════════════════════════════
# 3. Retry logic with mock LLM
# ═══════════════════════════════════════════════════════════════════


class TestCoderRetry:
    @pytest.mark.asyncio
    async def test_retry_on_invalid_then_valid(self):
        from ai_swe.agents.coder import CoderAgent

        valid_json = json.dumps(_make_valid_changeset_dict())
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(content="not valid json at all {{{"),
                MagicMock(content=valid_json),
            ]
        )

        agent = CoderAgent(orchestrator=None, llm=mock_llm)
        step = PlanStep(id="step-1", description="Add greet()", files_involved=["util.py"])
        changeset = await agent._code_step_with_retry(step, "summary", "FILE: util.py\nx = 1")

        assert changeset.step_id == "step-1"
        assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        from ai_swe.agents.coder import CoderAgent

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))

        agent = CoderAgent(orchestrator=None, llm=mock_llm)
        step = PlanStep(id="step-1", description="Add greet()")

        with pytest.raises(ValueError, match="Failed to produce a valid change set"):
            await agent._code_step_with_retry(step, "summary", "")


# ═══════════════════════════════════════════════════════════════════
# 4. Full CoderAgent.run() against a tiny temp repo
# ═══════════════════════════════════════════════════════════════════


class TestCoderRun:
    @pytest.mark.asyncio
    async def test_run_applies_edits_and_advances_status(self, tmp_path):
        from ai_swe.agents.coder import CoderAgent

        (tmp_path / "util.py").write_text("x = 1\n", encoding="utf-8")

        changeset_dict = _make_valid_changeset_dict()
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(changeset_dict)))

        agent = CoderAgent(orchestrator=None, llm=mock_llm)

        state = AgentState(
            task="Add a greet() helper to util.py",
            repo_path=str(tmp_path),
            plan=Plan(
                summary="Add a greet() helper",
                steps=[
                    PlanStep(
                        id="step-1",
                        description="Add greet() to util.py",
                        files_involved=["util.py"],
                    )
                ],
            ),
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.REVIEWING
        assert result.error is None
        assert result.plan.steps[0].done is True
        assert len(result.patches) == 1
        assert result.patches[0].file_path == "util.py"

        new_content = (tmp_path / "util.py").read_text(encoding="utf-8")
        assert "def greet():" in new_content

    @pytest.mark.asyncio
    async def test_run_skips_steps_already_done(self, tmp_path):
        from ai_swe.agents.coder import CoderAgent

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock()  # should never be called

        agent = CoderAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Nothing left to do",
            repo_path=str(tmp_path),
            plan=Plan(steps=[PlanStep(id="step-1", description="Already done", done=True)]),
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.REVIEWING
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_marks_failed_and_leaves_files_untouched_on_broken_edit(self, tmp_path):
        from ai_swe.agents.coder import CoderAgent

        (tmp_path / "util.py").write_text("x = 1\n", encoding="utf-8")
        original = (tmp_path / "util.py").read_text(encoding="utf-8")

        broken_changeset = {
            "step_id": "step-1",
            "rationale": "Deliberately broken edit for testing rollback.",
            "edits": [
                {
                    "file_path": "util.py",
                    "action": "modify",
                    "new_content": "def f(:\n    pass\n",
                    "description": "Broken syntax.",
                }
            ],
        }
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(broken_changeset)))

        agent = CoderAgent(orchestrator=None, llm=mock_llm)
        state = AgentState(
            task="Break util.py",
            repo_path=str(tmp_path),
            plan=Plan(steps=[PlanStep(id="step-1", description="Break it", files_involved=["util.py"])]),
        )

        result = await agent.run(state)

        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert "Coding failed" in result.error
        assert result.plan.steps[0].done is False
        assert (tmp_path / "util.py").read_text(encoding="utf-8") == original
