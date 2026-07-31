"""
Pydantic v2 models for the Reviewer agent's structured output.

The Reviewer triages a failing test run and produces a :class:`ReviewOutcome`
-- a verdict (``approve`` or ``needs_fix``) plus a list of structured
:class:`~ai_swe.state.ErrorReport` objects, one per distinct failure, each
precise enough for the Coder agent to act on without re-reading the raw
test output.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import BaseModel, Field

from ai_swe.state import ErrorReport

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReviewVerdict(str, Enum):
    """The Reviewer's overall judgement of the test run."""

    APPROVE = "approve"
    NEEDS_FIX = "needs_fix"


# ---------------------------------------------------------------------------
# Top-level review outcome
# ---------------------------------------------------------------------------


class ReviewOutcome(BaseModel):
    """The complete, validated result of a Reviewer pass over a test run."""

    verdict: ReviewVerdict = Field(description="Overall judgement: approve or needs_fix.")
    errors: list[ErrorReport] = Field(
        default_factory=list,
        description="Structured errors parsed from the failing test output.",
    )


# ---------------------------------------------------------------------------
# JSON parsing + validation helper
# ---------------------------------------------------------------------------

# Matches a JSON object/array that may be wrapped in ```json ... ``` fences
_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _extract_json(text: str) -> str:
    """
    Extract a JSON block from ``text``, stripping optional markdown fences.

    Falls back to returning the original text if no fences are found.
    """
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def validate_review(raw: str) -> ReviewOutcome:
    """
    Parse and validate a raw JSON string (possibly markdown-fenced) into a
    :class:`ReviewOutcome`.

    Args:
        raw: Raw text from the LLM response.

    Returns:
        A fully validated ``ReviewOutcome``.

    Raises:
        ValueError: If the JSON cannot be parsed or fails validation.
    """
    json_str = _extract_json(raw)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM response is not valid JSON: {exc}\n"
            f"Raw (first 500 chars): {json_str[:500]}"
        ) from exc

    return ReviewOutcome.model_validate(data)
