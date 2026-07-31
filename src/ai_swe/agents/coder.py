"""
Coder Agent -- implements a validated plan step by step.

Reads ``state.plan.steps`` in order and, for each step not yet marked
``done``, retrieves the current contents of the files that step touches,
calls an LLM (Groq) to produce a structured
:class:`~ai_swe.agents.coder_models.StepChangeSet`, and applies it to disk
via :class:`~ai_swe.agents.edit_engine.EditEngine`.

This mirrors the Planner agent's pattern exactly:

* **Structured output** -- the raw LLM response is parsed and validated
  against the ``StepChangeSet`` schema (see ``coder_models.py``).
* **Prompt engineering** -- a senior-engineer persona instructed to change
  only what the step requires (see ``coder_prompt.py``).
* **Retry handling** -- up to ``MAX_RETRIES`` attempts if the LLM returns
  invalid JSON, the change set fails Pydantic validation, *or* the edits
  fail to apply (e.g. ``SyntaxVerificationError`` from ``EditEngine``).
  Each retry feeds the previous error back into the prompt so the LLM can
  self-correct -- a syntax mistake fails the attempt, not the whole run.
* **Safe application** -- edits are applied (and, on any failure, rolled
  back) by ``EditEngine``, which also verifies the resulting file syntax.

The agent works in two modes, like the Planner:

1. **Pipeline mode** -- ``CoderAgent.run(state)`` integrates with the
   LangGraph orchestrator, reading from / writing to ``AgentState``.
2. **Standalone mode** -- ``CoderAgent`` can be constructed with
   ``orchestrator=None`` for offline use/testing; file edits then go
   through ``pathlib`` directly instead of the Filesystem MCP server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_swe.agents.base import BaseAgent
from ai_swe.agents.coder_models import StepChangeSet, validate_changeset
from ai_swe.agents.coder_prompt import (
    build_coding_prompt,
    build_retry_prompt,
    build_system_prompt,
)
from ai_swe.agents.edit_engine import EditEngine
from ai_swe.agents.retrieval import FileRetriever
from ai_swe.config import get_settings
from ai_swe.indexer.models import RepositoryIndex
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, ErrorReport, Patch, PlanStep, TaskStatus

logger = get_logger(__name__)

# Default number of retry attempts when the LLM produces invalid output.
MAX_RETRIES = 3


class CoderAgent(BaseAgent):
    """
    Implements each step of ``state.plan`` by generating and applying file
    edits.
    """

    name = "coder"

    def __init__(self, orchestrator: Any = None, *, llm: Any | None = None) -> None:
        """
        Args:
            orchestrator: The shared ``MCPOrchestrator`` (may be ``None``
                for standalone/offline use, in which case edits are applied
                directly via ``pathlib`` instead of the Filesystem MCP server).
            llm: An optional pre-configured LLM. If ``None``, one is created
                from ``Settings`` on first use.
        """
        self.orchestrator: Any = None
        if orchestrator is not None:
            super().__init__(orchestrator)
        self._llm = llm

    # ------------------------------------------------------------------
    # LLM initialization (identical pattern to PlannerAgent)
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
                "GROQ_API_KEY is not set.  The Coder agent requires an "
                "LLM API key.  Set it in your .env file."
            )

        from langchain_groq import ChatGroq
        from pydantic import SecretStr

        self._llm = ChatGroq(
            model=model,
            api_key=SecretStr(api_key),
            max_tokens=8192,
            temperature=0.0,  # deterministic code generation
        )
        logger.info("Initialised LLM: model=%s", model)
        return self._llm

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM and return the raw text response."""
        llm = self._get_llm()

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = await llm.ainvoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)

    # ------------------------------------------------------------------
    # Core coding logic
    # ------------------------------------------------------------------

    @staticmethod
    def _errors_for_step(step: PlanStep, errors: list[ErrorReport]) -> list[ErrorReport]:
        """
        Narrow ``errors`` (from a prior Reviewer fix-loop pass) down to the
        ones relevant to ``step``, matching on file path overlap with
        ``step.files_involved``. Falls back to the full list when no error
        can be localised to this step, so the Coder still sees the failure
        context even if the match is imprecise.
        """
        if not errors:
            return []
        matched = [e for e in errors if e.file_path and e.file_path in step.files_involved]
        return matched if matched else list(errors)

    async def _code_step_with_retry(
        self,
        step: PlanStep,
        plan_summary: str | None,
        file_contents: str,
        engine: EditEngine,
        errors: list[ErrorReport] | None = None,
    ) -> tuple[StepChangeSet, list[Patch]]:
        """
        Call the LLM, parse the response, and apply the resulting edits,
        retrying the whole cycle on failure.

        Up to ``MAX_RETRIES`` attempts.  A failure at *either* stage --
        invalid JSON / a Pydantic validation error from ``validate_changeset``,
        or an application error (``EditApplyError``, ``SyntaxVerificationError``,
        etc.) raised by ``engine.apply_changeset`` -- consumes one attempt.
        Each retry includes the previous error in the prompt so the LLM can
        self-correct instead of failing the whole run over a single bad edit.
        ``errors`` (from a prior Reviewer fix-loop pass) are injected into
        every attempt's prompt so the LLM fixes the actual failure instead of
        redoing the step from scratch.
        """
        system_prompt = build_system_prompt()
        user_prompt = build_coding_prompt(step, plan_summary, file_contents, errors=errors)

        last_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Coding step %s: attempt %d/%d", step.id, attempt, MAX_RETRIES)

            if attempt > 1 and last_error:
                user_prompt = user_prompt + "\n\n" + build_retry_prompt(last_error, attempt)

            try:
                raw_response = await self._call_llm(system_prompt, user_prompt)
                logger.debug(
                    "Raw LLM response (attempt %d, %d chars): %s...",
                    attempt,
                    len(raw_response),
                    raw_response[:200],
                )

                changeset = validate_changeset(raw_response)
                logger.info(
                    "Change set validated successfully on attempt %d: %d edit(s)",
                    attempt,
                    len(changeset.edits),
                )

                patches = await engine.apply_changeset(changeset.edits)
                return changeset, patches

            except Exception as exc:
                last_error = str(exc)
                logger.warning("Attempt %d failed: %s", attempt, last_error[:300])
                if attempt == MAX_RETRIES:
                    raise ValueError(
                        f"Failed to code and apply step {step.id} after "
                        f"{MAX_RETRIES} attempts.  Last error: {last_error}"
                    ) from exc

        # Should be unreachable
        raise RuntimeError("Exhausted retries without returning or raising.")

    # ------------------------------------------------------------------
    # Pipeline mode: run(state) -> state
    # ------------------------------------------------------------------

    async def run(self, state: AgentState) -> AgentState:
        """
        Execute the Coder within the LangGraph pipeline.

        For each not-yet-done step in ``state.plan.steps``, in order:

        1. Retrieve the current contents of the step's ``files_involved``.
        2. Call the LLM to get a validated ``StepChangeSet`` and apply it via
           ``EditEngine`` (backed up and rolled back atomically on failure),
           retrying the full cycle -- including edit application and syntax
           verification -- up to ``MAX_RETRIES`` times, injecting any
           ``state.errors`` from a prior Reviewer fix-loop pass that are
           relevant to this step.
        3. Append the resulting ``Patch`` objects to ``state.patches`` and
           mark the step ``done``.

        On success, advances ``state.status`` to ``EXECUTING`` (so the
        Executor re-verifies the change) and clears ``state.errors``, since
        they've now been addressed pending re-verification. On any failure,
        sets ``state.status = FAILED`` and records ``state.error``.
        """
        logger.info("[coder] Starting coding for task: %s", state.task[:100])
        state.add_log(self.name, f"Coding task: {state.task[:100]}")
        state.status = TaskStatus.CODING

        repo_path = Path(state.repo_path) if state.repo_path else Path(".")
        repo_path = repo_path.expanduser().resolve()

        # A minimal, summary-less index just to reuse FileRetriever's file
        # reading + context-formatting helpers -- the Coder already knows
        # exactly which files it needs (`step.files_involved`), so it has
        # no need for the Planner's relevance-ranking search.
        index = RepositoryIndex(repo_path=str(repo_path), repo_name=repo_path.name)
        retriever = FileRetriever(index, repo_path)
        engine = EditEngine(repo_path, self.orchestrator)

        try:
            steps_done = 0
            for step in state.plan.steps:
                if step.done:
                    continue

                file_contents = (
                    retriever.get_file_context(step.files_involved)
                    if step.files_involved
                    else ""
                )
                relevant_errors = self._errors_for_step(step, state.errors)

                changeset, patches = await self._code_step_with_retry(
                    step, state.plan.summary, file_contents, engine, errors=relevant_errors
                )

                for patch in patches:
                    if patch.description is None:
                        patch.description = changeset.rationale
                state.patches.extend(patches)

                step.done = True
                steps_done += 1
                state.add_log(
                    self.name,
                    f"Step {step.id} complete: {len(patches)} file(s) changed "
                    f"({changeset.rationale[:100]})",
                )

            state.errors = []
            state.status = TaskStatus.EXECUTING
            state.add_log(
                self.name,
                f"Coding complete: {steps_done} step(s) implemented, "
                f"{len(state.patches)} total patch(es).",
            )
            logger.info("[coder] Coding complete: %d step(s) implemented", steps_done)

        except Exception as exc:
            logger.exception("[coder] Coding failed")
            state.status = TaskStatus.FAILED
            state.error = f"Coding failed: {exc}"
            state.add_log(self.name, f"Coding failed: {exc}", level="error")

        return state
