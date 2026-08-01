"""
Production guardrails for the AI SWE Agent.

This module provides defensive wrappers and helpers that protect the pipeline
from runaway costs, timeouts, rate-limit violations, and unbounded repository
sizes.  All limits are configurable via `Settings`.

Usage pattern (in agent code):
    guardrails = Guardrails.from_settings()
    result = await guardrails.run_with_timeout(my_coro(), label="planner")
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from functools import wraps
from typing import Any, Callable, TypeVar

from ai_swe.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Cost Tracker
# ---------------------------------------------------------------------------

# Groq public pricing per 1 million tokens (USD).
# Prices sourced from https://console.groq.com/settings/limits
_GROQ_MODELS: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant":    {"input": 0.05, "output": 0.08},
    "mixtral-8x7b-32768":       {"input": 0.24, "output": 0.24},
    "gemma2-9b-it":             {"input": 0.20, "output": 0.20},
}
_FALLBACK_PRICING = {"input": 0.59, "output": 0.79}


class CostLimitExceeded(RuntimeError):
    """Raised when the estimated cost exceeds `Settings.max_cost_usd`."""


class CostTracker:
    """
    Accumulates token usage across the pipeline and estimates USD cost
    using Groq's public pricing.

    Thread-safe for async use (no shared mutable state between coroutines).
    """

    def __init__(self, model: str, max_cost_usd: float) -> None:
        pricing = _GROQ_MODELS.get(model, _FALLBACK_PRICING)
        self._input_price_per_token = pricing["input"] / 1_000_000
        self._output_price_per_token = pricing["output"] / 1_000_000
        self._max_cost_usd = max_cost_usd
        self._input_tokens = 0
        self._output_tokens = 0

    # ------------------------------------------------------------------
    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage from a single LLM call and raise if limit exceeded."""
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        cost = self.estimated_cost_usd
        logger.debug(
            "Token usage: +%d input / +%d output | total cost: $%.4f",
            input_tokens, output_tokens, cost,
        )
        if cost > self._max_cost_usd:
            raise CostLimitExceeded(
                f"Estimated cost ${cost:.4f} exceeds limit ${self._max_cost_usd:.2f}. "
                "Aborting pipeline to prevent runaway spending."
            )

    @property
    def total_input_tokens(self) -> int:
        return self._input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._output_tokens

    @property
    def total_tokens(self) -> int:
        return self._input_tokens + self._output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self._input_tokens * self._input_price_per_token
            + self._output_tokens * self._output_price_per_token
        )


# ---------------------------------------------------------------------------
# Rate Limiter (token-bucket, async-safe)
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Token-bucket rate limiter for LLM API calls.

    Configured with `rpm` (requests per minute).  Callers `await` the
    `acquire()` coroutine before each API call; it sleeps the minimum
    time required to stay within the configured rate.
    """

    def __init__(self, rpm: int) -> None:
        self._rpm = rpm
        self._interval = 60.0 / max(rpm, 1)
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        """Block until a request slot is available."""
        now = time.monotonic()
        # Evict timestamps older than one minute.
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._rpm:
            # Sleep until the oldest timestamp falls out of the 60-second window.
            sleep_for = 60.0 - (now - self._timestamps[0]) + 0.01
            if sleep_for > 0:
                logger.debug("Rate limit: sleeping %.2fs (rpm=%d)", sleep_for, self._rpm)
                await asyncio.sleep(sleep_for)

        self._timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# Timeout guard
# ---------------------------------------------------------------------------

class AgentTimeoutError(asyncio.TimeoutError):
    """Raised when an agent step exceeds its configured timeout."""


async def run_with_timeout(coro: Any, timeout: float, label: str = "agent") -> Any:
    """
    Run *coro* with an asyncio timeout.

    Raises `AgentTimeoutError` (a subclass of `asyncio.TimeoutError`) if the
    coroutine does not complete within *timeout* seconds.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise AgentTimeoutError(
            f"{label} timed out after {timeout:.0f}s"
        ) from exc


# ---------------------------------------------------------------------------
# Retry handler (exponential back-off with full jitter)
# ---------------------------------------------------------------------------

class MaxRetriesExceeded(RuntimeError):
    """Raised when all retry attempts are exhausted."""


async def with_retry(
    coro_factory: Callable[[], Any],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple[type[Exception], ...] = (Exception,),
    label: str = "operation",
) -> Any:
    """
    Execute `coro_factory()` up to `max_retries` times on failure.

    Uses exponential back-off with full jitter:
        delay = random(0, min(max_delay, base_delay * 2^attempt))

    Args:
        coro_factory: A zero-argument callable that returns a fresh coroutine
                      each time (needed because a coroutine can only be awaited once).
        retryable: Exception types that should trigger a retry; others propagate immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except retryable as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            cap = min(max_delay, base_delay * math.pow(2, attempt))
            delay = random.uniform(0, cap)  # full jitter
            logger.warning(
                "%s failed (attempt %d/%d, retrying in %.2fs): %s",
                label, attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)

    raise MaxRetriesExceeded(
        f"{label} failed after {max_retries} retries. Last error: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Large-repository guard
# ---------------------------------------------------------------------------

class LargeRepoError(ValueError):
    """Raised when a repository exceeds the configured size limits."""


def check_repo_size(file_count: int, size_mb: float, settings: Settings | None = None) -> None:
    """
    Check repository size against configured limits.

    Raises `LargeRepoError` if the repository is too large to process safely.
    """
    cfg = settings or get_settings()
    if size_mb > cfg.max_repo_size_mb:
        raise LargeRepoError(
            f"Repository is {size_mb:.1f} MB, which exceeds the limit of "
            f"{cfg.max_repo_size_mb} MB. Consider using a shallower clone or "
            "increasing MAX_REPO_SIZE_MB in your .env."
        )
    if file_count > cfg.max_files_in_repo:
        logger.warning(
            "Repository has %d files (limit %d). Deep analysis will be skipped; "
            "the agent will use a sampled subset of files.",
            file_count, cfg.max_files_in_repo,
        )


# ---------------------------------------------------------------------------
# Graceful error recovery decorator
# ---------------------------------------------------------------------------

def graceful_recovery(
    fallback: Any = None,
    *,
    log_level: str = "error",
    reraise: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator that catches all exceptions from an async function, logs them,
    and either returns `fallback` or re-raises (if `reraise=True`).

    Example:
        @graceful_recovery(fallback=[], reraise=False)
        async def list_files(...): ...
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                log = getattr(logger, log_level, logger.error)
                log("Unhandled error in %s: %s", fn.__qualname__, exc, exc_info=True)
                if reraise:
                    raise
                return fallback
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Convenience facade
# ---------------------------------------------------------------------------

class Guardrails:
    """
    A single object that bundles all guardrail helpers for a pipeline run.

    Constructed once at the start of a run and passed around (or stored in
    a context variable) so all agents share the same cost budget, rate limiter,
    and timeout settings.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.cost_tracker = CostTracker(
            model=self._settings.llm_model,
            max_cost_usd=self._settings.max_cost_usd,
        )
        self.rate_limiter = RateLimiter(rpm=self._settings.rate_limit_rpm)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Guardrails":
        return cls(settings)

    async def acquire_rate_slot(self) -> None:
        """Wait for a rate-limit slot before making an LLM API call."""
        await self.rate_limiter.acquire()

    def record_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage; raises `CostLimitExceeded` if over budget."""
        self.cost_tracker.record_usage(input_tokens, output_tokens)

    async def run_with_agent_timeout(self, coro: Any, label: str = "agent") -> Any:
        """Run a coroutine with the per-agent timeout from settings."""
        return await run_with_timeout(
            coro,
            timeout=float(self._settings.agent_timeout_seconds),
            label=label,
        )

    async def run_with_pipeline_timeout(self, coro: Any) -> Any:
        """Run a coroutine with the whole-pipeline timeout from settings."""
        return await run_with_timeout(
            coro,
            timeout=float(self._settings.pipeline_timeout_seconds),
            label="pipeline",
        )

    @property
    def estimated_cost_usd(self) -> float:
        return self.cost_tracker.estimated_cost_usd

    @property
    def total_tokens(self) -> int:
        return self.cost_tracker.total_tokens
