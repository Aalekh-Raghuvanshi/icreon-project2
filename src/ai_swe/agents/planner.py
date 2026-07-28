"""
Planner Agent — the brain of the planning phase.

Reads ``state.task`` and the repository's pre-built index, selectively
retrieves only the files relevant to the task, then calls an LLM (Claude)
to produce a structured :class:`~ai_swe.agents.planner_models.ImplementationPlan`.

Key behaviours
--------------
* **Selective retrieval** — the Planner never requests the full repository.
  It uses :class:`~ai_swe.agents.retrieval.FileRetriever` to fetch only
  the files it needs, ranked by a keyword + dependency-graph heuristic.
* **Prompt engineering** — the LLM is instructed to think like a senior
  software architect, considering dependencies, risk, and backwards
  compatibility.
* **Retry handling** — up to 3 attempts if the LLM returns invalid JSON or
  the plan fails Pydantic validation.  Each retry includes the previous
  error message so the LLM can self-correct.
* **Structured output** — the raw LLM response is parsed and validated
  against the ``ImplementationPlan`` schema.

The agent works in two modes:

1. **Pipeline mode** — ``PlannerAgent.run(state)`` integrates with the
   LangGraph orchestrator, reading from / writing to ``AgentState``.
2. **Standalone mode** — ``generate_plan(task, repo_path)`` is a
   convenience function for the CLI, returning an ``ImplementationPlan``
   directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_swe.agents.base import BaseAgent
from ai_swe.agents.planner_models import ImplementationPlan, validate_plan
from ai_swe.agents.planner_prompt import (
    build_planning_prompt,
    build_retry_prompt,
    build_system_prompt,
)
from ai_swe.agents.retrieval import FileRetriever
from ai_swe.config import get_settings
from ai_swe.indexer.models import RepositoryIndex
from ai_swe.indexer.persistence import load_index
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, Plan, PlanStep, TaskStatus

logger = get_logger(__name__)

# Default number of retry attempts when the LLM produces invalid output.
MAX_RETRIES = 3

# Maximum relevant files to retrieve and inject into the prompt.
MAX_RELEVANT_FILES = 15


class PlannerAgent(BaseAgent):
    """
    Produces a structured implementation plan for the given task.

    Requires a ``RepositoryIndex`` — if one has already been built (via
    ``ai-swe repo analyze``), it is loaded from disk; otherwise the
    analyzer is run on-the-fly.
    """

    name = "planner"

    def __init__(
        self,
        orchestrator: Any = None,
        *,
        llm: Any | None = None,
        index: RepositoryIndex | None = None,
    ) -> None:
        """
        Args:
            orchestrator: The shared ``MCPOrchestrator`` (may be ``None``
                when using standalone mode).
            llm:   An optional pre-configured LLM.  If ``None``, one is
                   created from ``Settings`` on first use.
            index: An optional pre-loaded ``RepositoryIndex``.  If ``None``,
                   it is loaded/built when ``run()`` or ``generate_plan()``
                   is called.
        """
        if orchestrator is not None:
            super().__init__(orchestrator)
        self._llm = llm
        self._index = index

    # ------------------------------------------------------------------
    # LLM initialization
    # ------------------------------------------------------------------

    def _get_llm(self) -> Any:
        """Lazily initialise the LLM client."""
        if self._llm is not None:
            return self._llm

        settings = get_settings()
        api_key = settings.groq_api_key
        model = settings.llm_model

        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set.  The Planner agent requires an "
                "LLM API key.  Set it in your .env file."
            )

        from langchain_groq import ChatGroq

        self._llm = ChatGroq(
            model=model,
            api_key=api_key,
            max_tokens=8192,
            temperature=0.0,  # deterministic planning
        )
        logger.info("Initialised LLM: model=%s", model)
        return self._llm

    # ------------------------------------------------------------------
    # Index loading
    # ------------------------------------------------------------------

    def _load_or_build_index(self, repo_path: Path) -> RepositoryIndex:
        """Load the index from disk, or build it on-the-fly."""
        if self._index is not None:
            return self._index

        index_path = repo_path / ".ai_swe_index.json"
        if index_path.is_file():
            logger.info("Loading existing repository index from %s", index_path)
            self._index = load_index(index_path)
            return self._index

        logger.info("No index found; analysing repository on-the-fly…")
        from ai_swe.indexer.analyzer import CodebaseAnalyzer

        analyzer = CodebaseAnalyzer(repo_path)
        self._index = asyncio.get_event_loop().run_until_complete(
            analyzer.analyze_repository()
        )
        return self._index

    async def _load_or_build_index_async(self, repo_path: Path) -> RepositoryIndex:
        """Async version of index loading/building."""
        if self._index is not None:
            return self._index

        index_path = repo_path / ".ai_swe_index.json"
        if index_path.is_file():
            logger.info("Loading existing repository index from %s", index_path)
            self._index = load_index(index_path)
            return self._index

        logger.info("No index found; analysing repository on-the-fly…")
        from ai_swe.indexer.analyzer import CodebaseAnalyzer

        analyzer = CodebaseAnalyzer(repo_path)
        self._index = await analyzer.analyze_repository()
        return self._index

    # ------------------------------------------------------------------
    # Core planning logic
    # ------------------------------------------------------------------

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM and return the raw text response."""
        llm = self._get_llm()

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = await llm.ainvoke(messages)
        return response.content

    async def _plan_with_retry(
        self,
        task: str,
        repo_summary: str,
        file_contexts: str,
    ) -> ImplementationPlan:
        """
        Call the LLM and parse the response, retrying on validation failure.

        Up to ``MAX_RETRIES`` attempts.  Each retry includes the previous
        error in the prompt so the LLM can self-correct.
        """
        system_prompt = build_system_prompt()
        user_prompt = build_planning_prompt(task, repo_summary, file_contexts)

        last_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Planning attempt %d/%d", attempt, MAX_RETRIES)

            if attempt > 1 and last_error:
                # Append the retry prompt to help the LLM self-correct
                user_prompt = (
                    user_prompt + "\n\n" + build_retry_prompt(last_error, attempt)
                )

            try:
                raw_response = await self._call_llm(system_prompt, user_prompt)
                logger.debug(
                    "Raw LLM response (attempt %d, %d chars): %s…",
                    attempt,
                    len(raw_response),
                    raw_response[:200],
                )

                plan = validate_plan(raw_response)
                logger.info(
                    "Plan validated successfully on attempt %d: %d steps",
                    attempt,
                    len(plan.steps),
                )
                return plan

            except (ValueError, Exception) as exc:
                last_error = str(exc)
                logger.warning(
                    "Attempt %d failed: %s",
                    attempt,
                    last_error[:300],
                )
                if attempt == MAX_RETRIES:
                    raise ValueError(
                        f"Failed to produce a valid plan after {MAX_RETRIES} "
                        f"attempts.  Last error: {last_error}"
                    ) from exc

        # Should be unreachable
        raise RuntimeError("Exhausted retries without returning or raising.")

    # ------------------------------------------------------------------
    # Convert ImplementationPlan → pipeline state models
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_to_state(impl_plan: ImplementationPlan) -> Plan:
        """Map the rich ``ImplementationPlan`` to the lightweight ``Plan``."""
        steps = [
            PlanStep(
                id=f"step-{s.step_number}",
                description=s.goal,
                files_involved=s.files_involved,
                reasoning=s.reasoning,
                dependencies=[f"step-{d}" for d in s.dependencies],
                expected_outcome=s.expected_outcome,
                risk_level=s.risk_level.value,
            )
            for s in impl_plan.steps
        ]
        return Plan(
            summary=impl_plan.summary,
            steps=steps,
            architecture_impact=impl_plan.architecture_impact,
            testing_strategy=impl_plan.testing_strategy,
            files_to_create=impl_plan.files_to_create,
            files_to_modify=impl_plan.files_to_modify,
        )

    # ------------------------------------------------------------------
    # Pipeline mode: run(state) → state
    # ------------------------------------------------------------------

    async def run(self, state: AgentState) -> AgentState:
        """
        Execute the Planner within the LangGraph pipeline.

        1. Load/build the ``RepositoryIndex``.
        2. Retrieve relevant files.
        3. Call the LLM with retry.
        4. Update ``state.plan`` and advance ``state.status`` to CODING.
        """
        logger.info("[planner] Starting planning for task: %s", state.task[:100])
        state.add_log(self.name, f"Planning task: {state.task[:100]}")
        state.status = TaskStatus.PLANNING

        repo_path = Path(state.repo_path) if state.repo_path else Path(".")
        repo_path = repo_path.expanduser().resolve()

        try:
            # 1. Load index
            index = await self._load_or_build_index_async(repo_path)
            state.add_log(
                self.name,
                f"Repository indexed: {index.statistics.total_files} files, "
                f"{index.statistics.total_loc:,} LOC",
            )

            # 2. Selective retrieval
            retriever = FileRetriever(index, repo_path)
            repo_summary = retriever.build_repo_summary()
            relevant_files = retriever.find_relevant_files(
                state.task, top_n=MAX_RELEVANT_FILES
            )
            state.add_log(
                self.name,
                f"Retrieved {len(relevant_files)} relevant files: "
                + ", ".join(relevant_files[:5])
                + ("…" if len(relevant_files) > 5 else ""),
            )
            file_contexts = retriever.get_file_context(relevant_files)

            # 3. Plan with retry
            impl_plan = await self._plan_with_retry(
                state.task, repo_summary, file_contexts
            )

            # 4. Update state
            state.plan = self._plan_to_state(impl_plan)
            state.status = TaskStatus.CODING
            state.add_log(
                self.name,
                f"Plan produced: {len(impl_plan.steps)} steps, "
                f"complexity={impl_plan.estimated_complexity}",
            )
            logger.info(
                "[planner] Planning complete: %d steps", len(impl_plan.steps)
            )

        except Exception as exc:
            logger.exception("[planner] Planning failed: %s", exc)
            state.status = TaskStatus.FAILED
            state.error = f"Planning failed: {exc}"
            state.add_log(self.name, f"Planning failed: {exc}", level="error")

        return state


# ---------------------------------------------------------------------------
# Standalone convenience function
# ---------------------------------------------------------------------------


async def generate_plan(
    task: str,
    repo_path: str | Path = ".",
    *,
    llm: Any | None = None,
    index: RepositoryIndex | None = None,
) -> ImplementationPlan:
    """
    Generate an implementation plan for *task* against the repository at
    *repo_path*.

    This is the main entry point for the CLI ``ai-swe plan`` command.
    It does NOT require the full LangGraph pipeline or MCP servers.

    Args:
        task:      Natural-language task description.
        repo_path: Path to the local repository root.
        llm:       Optional pre-configured LLM (for testing).
        index:     Optional pre-loaded ``RepositoryIndex``.

    Returns:
        A validated :class:`ImplementationPlan`.

    Example::

        plan = await generate_plan("Add rate limiting", "./my-api")
        print(plan.summary)
        for step in plan.steps:
            print(f"  {step.step_number}. {step.goal}")
    """
    repo_path = Path(repo_path).expanduser().resolve()

    agent = PlannerAgent(orchestrator=None, llm=llm, index=index)

    # Load or build index
    idx = await agent._load_or_build_index_async(repo_path)

    # Selective retrieval
    retriever = FileRetriever(idx, repo_path)
    repo_summary = retriever.build_repo_summary()
    relevant_files = retriever.find_relevant_files(task, top_n=MAX_RELEVANT_FILES)

    logger.info(
        "Retrieved %d relevant files for task: %s",
        len(relevant_files),
        task[:80],
    )

    file_contexts = retriever.get_file_context(relevant_files)

    # Plan with retry
    plan = await agent._plan_with_retry(task, repo_summary, file_contexts)
    return plan
