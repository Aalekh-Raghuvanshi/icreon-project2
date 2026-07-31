"""
Execution Agent.

By the time this agent runs, `state.patches` have already been written to
disk directly by the Coder agent (via `EditEngine`) -- there is nothing left
to "apply". This agent's job is verification: run the project's test suite
inside a `Sandbox` (Docker when available, a local subprocess otherwise) and
record the outcome as `TestResult`s on `state.test_results`.

If no recognised test framework is found in the repo, that's treated as
"nothing to verify" rather than a failure -- the pipeline still completes.
A failing test suite sets `state.status = FAILED` so the graph stops there
instead of reporting success on broken code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_swe.agents.base import BaseAgent
from ai_swe.execution.sandbox import Sandbox, get_sandbox
from ai_swe.execution.test_runner import run_tests
from ai_swe.logging_config import get_logger
from ai_swe.state import AgentState, TaskStatus

logger = get_logger(__name__)


class ExecutionAgent(BaseAgent):
    """Runs the project's test suite in a sandbox to verify the Coder's patches."""

    name = "executor"

    def __init__(self, orchestrator: Any = None, *, sandbox: Sandbox | None = None) -> None:
        """
        Args:
            orchestrator: The shared `MCPOrchestrator` (may be `None`; this
                agent doesn't currently need it, since patches are already on
                disk by the time it runs).
            sandbox: An optional pre-built `Sandbox`. If `None`, one is
                created lazily via `get_sandbox()` on first use (Docker
                preferred, falling back to a local subprocess).
        """
        self.orchestrator: Any = None
        if orchestrator is not None:
            super().__init__(orchestrator)
        self._sandbox = sandbox

    async def _get_sandbox(self) -> Sandbox:
        if self._sandbox is None:
            self._sandbox = await get_sandbox()
        return self._sandbox

    async def run(self, state: AgentState) -> AgentState:
        logger.info("[executor] Running test suite to verify changes.")
        state.status = TaskStatus.EXECUTING

        repo_path = Path(state.repo_path) if state.repo_path else Path(".")
        repo_path = repo_path.expanduser().resolve()

        try:
            sandbox = await self._get_sandbox()
            results = await run_tests(repo_path, sandbox)

            if not results:
                state.add_log(self.name, "No recognised test framework found; nothing to verify.")
                state.status = TaskStatus.DONE
                logger.info("[executor] No test framework detected; treating as verified.")
                return state

            state.test_results.extend(results)
            failed = [r for r in results if not r.passed]

            for r in results:
                state.add_log(
                    self.name,
                    f"{r.name}: {'PASSED' if r.passed else 'FAILED'}",
                    level="info" if r.passed else "error",
                )

            if failed:
                state.status = TaskStatus.FAILED
                state.error = f"{len(failed)} test run(s) failed."
                logger.warning("[executor] %d test run(s) failed.", len(failed))
            else:
                state.status = TaskStatus.DONE
                logger.info("[executor] All test runs passed.")

        except Exception as exc:
            logger.exception("[executor] Execution failed")
            state.status = TaskStatus.FAILED
            state.error = f"Execution failed: {exc}"
            state.add_log(self.name, f"Execution failed: {exc}", level="error")

        return state
