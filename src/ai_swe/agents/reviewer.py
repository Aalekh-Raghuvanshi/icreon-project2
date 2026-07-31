"""
Reviewer Agent -- triages the Executor's test results and drives the
auto-fix loop.

By the time this agent runs, the Executor has already run the project's test
suite and recorded the outcome as ``TestResult``s on ``state.test_results``.
The Reviewer's job is twofold:

1. **Approve** -- if every test passed (or there was nothing to test), the
   task is done.
2. **Triage a failure** -- if any test failed, call an LLM (Groq) to parse
   the failing output into structured :class:`~ai_swe.state.ErrorReport`
   objects (see ``reviewer_models.py``), then either send the state back to
   the Coder for another attempt (bounded by ``state.max_fix_attempts``) or
   give up and mark the task ``FAILED``.

This mirrors the Planner/Coder agents' pattern exactly: structured LLM
output, prompt engineering via a dedicated persona, and retry-with-feedback
on invalid JSON (see ``reviewer_prompt.py`` / ``reviewer_models.py``).
"""

from __future__ import annotations

from typing import Any

from ai_swe.agents.base import BaseAgent
from ai_swe.agents.reviewer_models import ReviewOutcome, validate_review
from ai_swe.agents.reviewer_prompt import (
    build_retry_prompt,
    build_review_prompt,
    build_system_prompt,
)
from ai_swe.config import get_settings
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, ErrorReport, PlanStep, TaskStatus, TestResult

logger = get_logger(__name__)

# Default number of retry attempts when the LLM produces invalid output.
MAX_RETRIES = 3


class ReviewerAgent(BaseAgent):
    """Triages failing test runs and drives the bounded Coder auto-fix loop."""

    name = "reviewer"

    def __init__(self, orchestrator: Any = None, *, llm: Any | None = None) -> None:
        """
        Args:
            orchestrator: The shared ``MCPOrchestrator`` (may be ``None``
                for standalone/offline use).
            llm: An optional pre-configured LLM. If ``None``, one is created
                from ``Settings`` on first use.
        """
        self.orchestrator: Any = None
        if orchestrator is not None:
            super().__init__(orchestrator)
        self._llm = llm

    # ------------------------------------------------------------------
    # LLM initialization (identical pattern to PlannerAgent/CoderAgent)
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
                "GROQ_API_KEY is not set.  The Reviewer agent requires an "
                "LLM API key.  Set it in your .env file."
            )

        from langchain_groq import ChatGroq
        from pydantic import SecretStr

        self._llm = ChatGroq(
            model=model,
            api_key=SecretStr(api_key),
            max_tokens=8192,
            temperature=0.0,  # deterministic triage
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
    # Core review logic
    # ------------------------------------------------------------------

    async def _review_with_retry(
        self,
        task: str,
        plan_summary: str | None,
        failing_results: list[TestResult],
    ) -> ReviewOutcome:
        """
        Call the LLM and parse the response, retrying on validation failure.

        Up to ``MAX_RETRIES`` attempts.  Each retry includes the previous
        error in the prompt so the LLM can self-correct.
        """
        system_prompt = build_system_prompt()
        user_prompt = build_review_prompt(task, plan_summary, failing_results)

        last_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Review attempt %d/%d", attempt, MAX_RETRIES)

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

                outcome = validate_review(raw_response)
                logger.info(
                    "Review validated on attempt %d: verdict=%s, %d error(s)",
                    attempt,
                    outcome.verdict.value,
                    len(outcome.errors),
                )
                return outcome

            except (ValueError, Exception) as exc:
                last_error = str(exc)
                logger.warning("Attempt %d failed: %s", attempt, last_error[:300])
                if attempt == MAX_RETRIES:
                    raise ValueError(
                        f"Failed to produce a valid review after {MAX_RETRIES} "
                        f"attempts.  Last error: {last_error}"
                    ) from exc

        # Should be unreachable
        raise RuntimeError("Exhausted retries without returning or raising.")

    @staticmethod
    def _reset_affected_steps(steps: list[PlanStep], errors: list[ErrorReport]) -> None:
        """
        Mark the plan steps responsible for ``errors`` as not-done, so the
        Coder redoes only the affected work rather than the whole plan.

        Matches by file path overlap between each error and each step's
        ``files_involved``. If no error carries a file path we can match
        against any step (e.g. the LLM couldn't localise the failure), every
        step is reset as a conservative fallback.
        """
        affected_files = {e.file_path for e in errors if e.file_path}

        if not affected_files:
            for step in steps:
                step.done = False
            return

        any_matched = False
        for step in steps:
            if affected_files & set(step.files_involved):
                step.done = False
                any_matched = True

        if not any_matched:
            for step in steps:
                step.done = False

    # ------------------------------------------------------------------
    # Pipeline mode: run(state) -> state
    # ------------------------------------------------------------------

    async def run(self, state: AgentState) -> AgentState:
        """
        Execute the Reviewer within the LangGraph pipeline.

        1. If every ``TestResult`` on ``state`` passed (or there were none),
           approve: ``state.status = DONE``.
        2. Otherwise, call the LLM to triage the failing output into
           structured ``ErrorReport``s, store them on ``state.errors``, and
           either loop back to the Coder (``state.status = CODING``,
           ``state.fix_attempts`` incremented, affected steps reset) or give
           up (``state.status = FAILED``) once ``max_fix_attempts`` is
           reached.
        """
        logger.info("[reviewer] Reviewing test results for task: %s", state.task[:100])
        state.status = TaskStatus.REVIEWING

        failing = [r for r in state.test_results if not r.passed]

        if not failing:
            state.add_log(self.name, "All tests passed; approving.")
            state.status = TaskStatus.DONE
            logger.info("[reviewer] Approved: all tests passed.")
            return state

        state.add_log(
            self.name,
            f"{len(failing)} failing test run(s) found; triaging.",
            level="warning",
        )

        try:
            outcome = await self._review_with_retry(state.task, state.plan.summary, failing)
        except Exception as exc:
            logger.exception("[reviewer] Review failed")
            state.status = TaskStatus.FAILED
            state.error = f"Review failed: {exc}"
            state.add_log(self.name, f"Review failed: {exc}", level="error")
            return state

        state.errors = outcome.errors
        for err in outcome.errors:
            state.add_log(
                self.name,
                f"[{err.error_type}] {err.file_path or '?'}:{err.line if err.line is not None else '?'} "
                f"-- {err.message}",
                level="error",
            )

        if state.fix_attempts < state.max_fix_attempts:
            state.fix_attempts += 1
            self._reset_affected_steps(state.plan.steps, outcome.errors)
            state.status = TaskStatus.CODING
            state.add_log(
                self.name,
                f"Sending back to Coder for fix attempt "
                f"{state.fix_attempts}/{state.max_fix_attempts}.",
            )
            logger.info(
                "[reviewer] Looping back to Coder (attempt %d/%d).",
                state.fix_attempts,
                state.max_fix_attempts,
            )
        else:
            state.status = TaskStatus.FAILED
            state.error = "Max fix attempts reached"
            state.add_log(self.name, "Max fix attempts reached; giving up.", level="error")
            logger.warning("[reviewer] Max fix attempts (%d) reached; giving up.", state.max_fix_attempts)

        return state
