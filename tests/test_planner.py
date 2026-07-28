"""
Tests for the Planner agent and its supporting modules.

Covers:
  - ImplementationPlan Pydantic validation (valid & invalid)
  - validate_plan() JSON parsing with markdown fences
  - FileRetriever relevance ranking and file retrieval
  - Retry logic with a mock LLM
  - Plan persistence round-trip (save → load)
  - Plan visualizer smoke test
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_swe.agents.planner_models import (
    ImplementationPlan,
    PlanStepDetail,
    RiskLevel,
    validate_plan,
)
from ai_swe.agents.retrieval import FileRetriever, _tokenize
from ai_swe.agents.plan_persistence import save_plan, load_plan
from ai_swe.agents.planner_prompt import (
    build_planning_prompt,
    build_retry_prompt,
    build_system_prompt,
)
from ai_swe.indexer.models import (
    FileSummary,
    Language,
    RepositoryIndex,
    RepositoryStatistics,
    Symbol,
    SymbolKind,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


def _make_valid_plan_dict(**overrides) -> dict:
    """Return a minimal valid plan as a dict."""
    plan = {
        "task": "Add rate limiting to the API",
        "summary": "Implement token-bucket rate limiting middleware.",
        "architecture_impact": "Adds a new middleware layer.",
        "steps": [
            {
                "step_number": 1,
                "goal": "Create rate limiter config",
                "files_involved": ["src/config.py"],
                "reasoning": "Need a place to configure rate limits.",
                "dependencies": [],
                "expected_outcome": "Config class has rate limit fields.",
                "risk_level": "low",
            },
            {
                "step_number": 2,
                "goal": "Implement rate limiting middleware",
                "files_involved": ["src/middleware.py"],
                "reasoning": "Core feature implementation.",
                "dependencies": [1],
                "expected_outcome": "Middleware intercepts requests and applies limits.",
                "risk_level": "medium",
            },
            {
                "step_number": 3,
                "goal": "Add tests",
                "files_involved": ["tests/test_rate_limit.py"],
                "reasoning": "Verify rate limiting works correctly.",
                "dependencies": [1, 2],
                "expected_outcome": "All rate limiting tests pass.",
                "risk_level": "low",
            },
        ],
        "estimated_complexity": "medium",
        "files_to_create": ["src/middleware.py", "tests/test_rate_limit.py"],
        "files_to_modify": ["src/config.py"],
        "files_to_delete": [],
        "testing_strategy": "Unit tests for the middleware plus integration tests.",
        "risks_and_mitigations": [
            "Risk: Rate limiting may slow down tests → Use mock clock",
        ],
    }
    plan.update(overrides)
    return plan


def _make_index() -> RepositoryIndex:
    """Build a minimal RepositoryIndex for testing."""
    summaries = {
        "src/config.py": FileSummary(
            path="src/config.py",
            language=Language.PYTHON,
            purpose="Application configuration and settings.",
            major_classes=["Settings"],
            important_functions=["get_settings"],
            dependencies=["pydantic"],
            symbols=[
                Symbol(name="Settings", kind=SymbolKind.CLASS, line_start=10, line_end=40),
                Symbol(name="get_settings", kind=SymbolKind.FUNCTION, line_start=43, line_end=50),
                Symbol(name="pydantic", kind=SymbolKind.IMPORT, line_start=1, line_end=1),
            ],
            loc=40,
            total_lines=50,
        ),
        "src/middleware.py": FileSummary(
            path="src/middleware.py",
            language=Language.PYTHON,
            purpose="HTTP middleware stack for request processing.",
            major_classes=["AuthMiddleware"],
            important_functions=["apply_middleware"],
            dependencies=["fastapi", "config"],
            symbols=[
                Symbol(name="AuthMiddleware", kind=SymbolKind.CLASS, line_start=5, line_end=30),
                Symbol(name="apply_middleware", kind=SymbolKind.FUNCTION, line_start=33, line_end=45),
                Symbol(name="fastapi", kind=SymbolKind.IMPORT, line_start=1, line_end=1),
                Symbol(name="config", kind=SymbolKind.IMPORT, line_start=2, line_end=2),
            ],
            loc=35,
            total_lines=45,
        ),
        "src/main.py": FileSummary(
            path="src/main.py",
            language=Language.PYTHON,
            purpose="Application entry point.",
            major_classes=[],
            important_functions=["main"],
            dependencies=["config", "middleware"],
            symbols=[
                Symbol(name="main", kind=SymbolKind.FUNCTION, line_start=5, line_end=20),
                Symbol(name="config", kind=SymbolKind.IMPORT, line_start=1, line_end=1),
                Symbol(name="middleware", kind=SymbolKind.IMPORT, line_start=2, line_end=2),
            ],
            loc=15,
            total_lines=20,
        ),
        "tests/test_auth.py": FileSummary(
            path="tests/test_auth.py",
            language=Language.PYTHON,
            purpose="Tests for authentication middleware.",
            major_classes=[],
            important_functions=["test_auth_middleware"],
            dependencies=["pytest", "middleware"],
            symbols=[
                Symbol(
                    name="test_auth_middleware",
                    kind=SymbolKind.FUNCTION,
                    line_start=5,
                    line_end=20,
                ),
                Symbol(name="pytest", kind=SymbolKind.IMPORT, line_start=1, line_end=1),
                Symbol(name="middleware", kind=SymbolKind.IMPORT, line_start=2, line_end=2),
            ],
            loc=15,
            total_lines=20,
        ),
    }

    return RepositoryIndex(
        repo_path="/fake/repo",
        repo_name="test-repo",
        file_tree=sorted(summaries.keys()),
        summaries=summaries,
        dependency_graph=[],
        statistics=RepositoryStatistics(
            total_files=4,
            total_loc=105,
            total_lines=135,
            language_breakdown={"python": 4},
            language_loc={"python": 105},
        ),
        adjacency={
            "src/main.py": ["src/config.py", "src/middleware.py"],
            "src/middleware.py": ["src/config.py"],
            "tests/test_auth.py": ["src/middleware.py"],
        },
    )


# ═══════════════════════════════════════════════════════════════════
# 1. ImplementationPlan Pydantic validation
# ═══════════════════════════════════════════════════════════════════


class TestImplementationPlanValidation:
    """Test Pydantic validation of ImplementationPlan."""

    def test_valid_plan_parses(self):
        plan = ImplementationPlan.model_validate(_make_valid_plan_dict())
        assert len(plan.steps) == 3
        assert plan.task == "Add rate limiting to the API"
        assert plan.steps[0].risk_level == RiskLevel.LOW
        assert plan.steps[1].risk_level == RiskLevel.MEDIUM

    def test_sequential_step_numbers_required(self):
        data = _make_valid_plan_dict()
        data["steps"][1]["step_number"] = 5  # gap
        with pytest.raises(Exception):  # ValidationError
            ImplementationPlan.model_validate(data)

    def test_dependency_on_future_step_rejected(self):
        data = _make_valid_plan_dict()
        data["steps"][0]["dependencies"] = [2]  # step 1 depends on step 2
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)

    def test_dependency_on_self_rejected(self):
        data = _make_valid_plan_dict()
        data["steps"][1]["dependencies"] = [2]  # step 2 depends on itself
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)

    def test_dependency_on_nonexistent_step_rejected(self):
        data = _make_valid_plan_dict()
        data["steps"][0]["dependencies"] = [99]
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)

    def test_empty_steps_rejected(self):
        data = _make_valid_plan_dict()
        data["steps"] = []
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)

    def test_missing_task_rejected(self):
        data = _make_valid_plan_dict()
        data["task"] = ""
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)

    def test_files_involved_auto_added_to_modify(self):
        """Files in steps but not in plan-level lists get auto-added."""
        data = _make_valid_plan_dict()
        data["steps"][0]["files_involved"] = ["src/new_file.py"]
        data["files_to_modify"] = []  # doesn't include new_file.py
        data["files_to_create"] = []
        plan = ImplementationPlan.model_validate(data)
        assert "src/new_file.py" in plan.files_to_modify

    def test_risk_level_enum(self):
        for level in ("low", "medium", "high", "critical"):
            data = _make_valid_plan_dict()
            data["steps"][0]["risk_level"] = level
            plan = ImplementationPlan.model_validate(data)
            assert plan.steps[0].risk_level == RiskLevel(level)

    def test_invalid_risk_level_rejected(self):
        data = _make_valid_plan_dict()
        data["steps"][0]["risk_level"] = "extreme"
        with pytest.raises(Exception):
            ImplementationPlan.model_validate(data)


# ═══════════════════════════════════════════════════════════════════
# 2. validate_plan() — JSON parsing with markdown fences
# ═══════════════════════════════════════════════════════════════════


class TestValidatePlan:
    """Test the validate_plan() function."""

    def test_raw_json(self):
        raw = json.dumps(_make_valid_plan_dict())
        plan = validate_plan(raw)
        assert plan.task == "Add rate limiting to the API"

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(_make_valid_plan_dict()) + "\n```"
        plan = validate_plan(raw)
        assert len(plan.steps) == 3

    def test_markdown_fenced_no_lang(self):
        raw = "```\n" + json.dumps(_make_valid_plan_dict()) + "\n```"
        plan = validate_plan(raw)
        assert plan.summary == "Implement token-bucket rate limiting middleware."

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_plan("{bad json}")

    def test_valid_json_invalid_schema_raises(self):
        with pytest.raises(Exception):
            validate_plan('{"task": ""}')  # empty task


# ═══════════════════════════════════════════════════════════════════
# 3. FileRetriever
# ═══════════════════════════════════════════════════════════════════


class TestFileRetriever:
    """Test the selective file retrieval layer."""

    def test_find_relevant_files_ranks_by_keyword(self):
        index = _make_index()
        retriever = FileRetriever(index, "/fake/repo")

        # Query about middleware should rank middleware.py high
        result = retriever.find_relevant_files("rate limiting middleware")
        assert "src/middleware.py" in result[:2]

    def test_find_relevant_files_ranks_config(self):
        index = _make_index()
        retriever = FileRetriever(index, "/fake/repo")

        result = retriever.find_relevant_files("application settings configuration")
        assert result[0] == "src/config.py"

    def test_find_relevant_files_returns_limited_results(self):
        index = _make_index()
        retriever = FileRetriever(index, "/fake/repo")

        result = retriever.find_relevant_files("anything", top_n=2)
        assert len(result) <= 2

    def test_find_relevant_files_empty_index(self):
        index = RepositoryIndex(
            repo_path="/fake", repo_name="empty", summaries={}
        )
        retriever = FileRetriever(index, "/fake")
        result = retriever.find_relevant_files("anything")
        assert result == []

    def test_retrieve_files_reads_from_disk(self, tmp_path):
        # Create a test file
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text("x = 1\n", encoding="utf-8")

        index = _make_index()
        retriever = FileRetriever(index, tmp_path)
        contents = retriever.retrieve_files(["src/config.py"])

        assert "src/config.py" in contents
        assert contents["src/config.py"] == "x = 1\n"

    def test_retrieve_files_skips_missing(self, tmp_path):
        index = _make_index()
        retriever = FileRetriever(index, tmp_path)
        contents = retriever.retrieve_files(["does_not_exist.py"])
        assert contents == {}

    def test_get_file_context_includes_header(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text("settings = True\n", encoding="utf-8")

        index = _make_index()
        retriever = FileRetriever(index, tmp_path)
        context = retriever.get_file_context(["src/config.py"])

        assert "FILE: src/config.py" in context
        assert "Language: python" in context
        assert "settings = True" in context

    def test_get_file_context_respects_budget(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text("x" * 100_000, encoding="utf-8")

        index = _make_index()
        retriever = FileRetriever(index, tmp_path, max_content_chars=500)
        context = retriever.get_file_context(["src/config.py"])

        # Should be truncated
        assert len(context) < 100_000

    def test_build_repo_summary(self):
        index = _make_index()
        retriever = FileRetriever(index, "/fake/repo")
        summary = retriever.build_repo_summary()

        assert "test-repo" in summary
        assert "src/config.py" in summary
        assert "python" in summary.lower()


# ═══════════════════════════════════════════════════════════════════
# 4. Tokenizer
# ═══════════════════════════════════════════════════════════════════


class TestTokenize:
    """Test the internal _tokenize helper."""

    def test_basic_tokenization(self):
        tokens = _tokenize("Add rate limiting middleware")
        assert "rate" in tokens
        assert "limiting" in tokens
        assert "middleware" in tokens

    def test_stop_words_removed(self):
        tokens = _tokenize("add the new rate limiting to the API")
        assert "the" not in tokens
        assert "add" not in tokens  # too common, in stop words

    def test_short_tokens_removed(self):
        tokens = _tokenize("a b cd efg")
        assert "efg" in tokens
        assert "cd" not in tokens  # len 2


# ═══════════════════════════════════════════════════════════════════
# 5. Prompt building
# ═══════════════════════════════════════════════════════════════════


class TestPrompts:
    """Test prompt construction."""

    def test_system_prompt_contains_schema(self):
        prompt = build_system_prompt()
        assert "step_number" in prompt
        assert "risk_level" in prompt
        assert "Senior Software Architect" in prompt

    def test_planning_prompt_includes_all_sections(self):
        prompt = build_planning_prompt(
            task="Add caching",
            repo_summary="Repo: test-app\n4 files",
            file_contexts="FILE: config.py\nx = 1",
        )
        assert "Add caching" in prompt
        assert "test-app" in prompt
        assert "config.py" in prompt

    def test_retry_prompt_includes_error(self):
        prompt = build_retry_prompt("Missing field 'task'", attempt=2)
        assert "Missing field 'task'" in prompt
        assert "attempt 1" in prompt


# ═══════════════════════════════════════════════════════════════════
# 6. Plan persistence
# ═══════════════════════════════════════════════════════════════════


class TestPlanPersistence:
    """Test save/load round-trip."""

    def test_save_and_load(self, tmp_path):
        plan = ImplementationPlan.model_validate(_make_valid_plan_dict())
        dest = save_plan(plan, output_path=tmp_path / "plan.json")

        assert dest.is_file()

        loaded = load_plan(dest)
        assert loaded.task == plan.task
        assert len(loaded.steps) == len(plan.steps)
        assert loaded.steps[0].goal == plan.steps[0].goal

    def test_save_to_repo_default(self, tmp_path):
        plan = ImplementationPlan.model_validate(_make_valid_plan_dict())
        dest = save_plan(plan, repo_path=tmp_path)

        assert dest.name == ".ai_swe_plan.json"
        assert dest.parent == tmp_path

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_plan(tmp_path / "nonexistent.json")


# ═══════════════════════════════════════════════════════════════════
# 7. Plan visualizer (smoke test)
# ═══════════════════════════════════════════════════════════════════


class TestPlanVisualizer:
    """Smoke test: the visualizer should not crash."""

    def test_visualize_does_not_crash(self):
        from io import StringIO
        from rich.console import Console

        from ai_swe.agents.plan_visualizer import visualize_plan

        plan = ImplementationPlan.model_validate(_make_valid_plan_dict())
        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)
        visualize_plan(plan, test_console)

        output = buf.getvalue()
        assert "Implementation Plan" in output
        assert "rate limiting" in output.lower()
        assert "Step" in output


# ═══════════════════════════════════════════════════════════════════
# 8. Retry logic with mock LLM
# ═══════════════════════════════════════════════════════════════════


class TestPlannerRetry:
    """Test the Planner's retry-on-invalid-JSON behaviour."""

    @pytest.mark.asyncio
    async def test_retry_on_invalid_then_valid(self):
        """LLM returns bad JSON on attempt 1, valid on attempt 2."""
        from ai_swe.agents.planner import PlannerAgent

        valid_json = json.dumps(_make_valid_plan_dict())

        # Mock LLM: first call returns garbage, second returns valid JSON
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(content="not valid json at all {{{"),
                MagicMock(content=valid_json),
            ]
        )

        agent = PlannerAgent(orchestrator=None, llm=mock_llm)
        plan = await agent._plan_with_retry(
            task="Add rate limiting",
            repo_summary="test repo",
            file_contexts="test files",
        )

        assert plan.task == "Add rate limiting to the API"
        assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        """LLM always returns garbage → should raise after MAX_RETRIES."""
        from ai_swe.agents.planner import PlannerAgent

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content="not json")
        )

        agent = PlannerAgent(orchestrator=None, llm=mock_llm)
        with pytest.raises(ValueError, match="Failed to produce a valid plan"):
            await agent._plan_with_retry(
                task="anything",
                repo_summary="repo",
                file_contexts="files",
            )

    @pytest.mark.asyncio
    async def test_valid_on_first_attempt(self):
        """LLM returns valid JSON on first try — no retries needed."""
        from ai_swe.agents.planner import PlannerAgent

        valid_json = json.dumps(_make_valid_plan_dict())
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content=valid_json)
        )

        agent = PlannerAgent(orchestrator=None, llm=mock_llm)
        plan = await agent._plan_with_retry(
            task="test",
            repo_summary="repo",
            file_contexts="files",
        )

        assert len(plan.steps) == 3
        assert mock_llm.ainvoke.call_count == 1


# ═══════════════════════════════════════════════════════════════════
# 9. State model round-trip
# ═══════════════════════════════════════════════════════════════════


class TestStateModelUpdates:
    """Test the extended PlanStep and Plan models."""

    def test_plan_step_extended_fields_have_defaults(self):
        from ai_swe.state import PlanStep

        # Old-style construction (backwards compat)
        step = PlanStep(id="step-1", description="Do something")
        assert step.files_involved == []
        assert step.reasoning == ""
        assert step.dependencies == []
        assert step.expected_outcome == ""
        assert step.risk_level == "low"

    def test_plan_extended_fields_have_defaults(self):
        from ai_swe.state import Plan

        plan = Plan(summary="Test plan")
        assert plan.architecture_impact == ""
        assert plan.testing_strategy == ""
        assert plan.files_to_create == []
        assert plan.files_to_modify == []

    def test_plan_to_state_mapping(self):
        from ai_swe.agents.planner import PlannerAgent

        impl = ImplementationPlan.model_validate(_make_valid_plan_dict())
        state_plan = PlannerAgent._plan_to_state(impl)

        assert len(state_plan.steps) == 3
        assert state_plan.steps[0].id == "step-1"
        assert state_plan.steps[0].files_involved == ["src/config.py"]
        assert state_plan.steps[1].dependencies == ["step-1"]
        assert state_plan.architecture_impact == "Adds a new middleware layer."
