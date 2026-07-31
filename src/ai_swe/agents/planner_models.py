"""
Pydantic v2 models for the Planner agent's structured output.

The Planner produces an :class:`ImplementationPlan` — a file-level roadmap
that describes *what* to change, *why*, in what *order*, and the *risk* of
each step.  Every field is typed, validated, and JSON-serialisable so the
plan can be persisted, visualised, and consumed by downstream agents.

Validation rules
-----------------
* Step numbers must be sequential starting at 1.
* Step dependencies must reference existing, earlier step numbers (no cycles).
* ``files_involved`` on every step must be a subset of the union of
  ``files_to_create``, ``files_to_modify``, and ``files_to_delete`` at the
  plan level.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Risk classification for a single implementation step."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Step-level detail
# ---------------------------------------------------------------------------


class PlanStepDetail(BaseModel):
    """A single, atomic step in the implementation plan."""

    step_number: int = Field(ge=1, description="Ordinal position (1-based).")
    goal: str = Field(min_length=1, description="What this step achieves.")
    files_involved: list[str] = Field(
        default_factory=list,
        description="Relative file paths touched by this step.",
    )
    reasoning: str = Field(
        min_length=1,
        description="Why this step is necessary (architectural justification).",
    )
    dependencies: list[int] = Field(
        default_factory=list,
        description="Step numbers that must complete before this one.",
    )
    expected_outcome: str = Field(
        min_length=1,
        description="What should be true after this step is done.",
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="Risk classification: low / medium / high / critical.",
    )


# ---------------------------------------------------------------------------
# Top-level plan
# ---------------------------------------------------------------------------


class ImplementationPlan(BaseModel):
    """
    A complete, validated implementation plan produced by the Planner agent.

    This is the *rich* plan — it carries far more detail than the lightweight
    ``Plan`` / ``PlanStep`` models in ``state.py`` which are threaded through
    the LangGraph pipeline.  The mapping from ``ImplementationPlan`` →
    ``Plan`` is handled by the Planner agent itself.
    """

    task: str = Field(min_length=1, description="Original task description.")
    summary: str = Field(
        min_length=1,
        description="One-paragraph plan summary.",
    )
    architecture_impact: str = Field(
        default="",
        description="How this change affects the overall codebase architecture.",
    )
    steps: list[PlanStepDetail] = Field(
        min_length=1,
        description="Ordered implementation steps.",
    )
    estimated_complexity: str = Field(
        default="medium",
        description="Overall complexity assessment (low / medium / high).",
    )
    files_to_create: list[str] = Field(
        default_factory=list,
        description="New files that will be created.",
    )
    files_to_modify: list[str] = Field(
        default_factory=list,
        description="Existing files that will be modified.",
    )
    files_to_delete: list[str] = Field(
        default_factory=list,
        description="Files that will be removed.",
    )
    testing_strategy: str = Field(
        default="",
        description="How to verify the changes work.",
    )
    risks_and_mitigations: list[str] = Field(
        default_factory=list,
        description="Key risks paired with mitigations.",
    )

    # ------------------------------------------------------------------
    # Cross-field validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_plan_integrity(self) -> ImplementationPlan:
        """Validate step numbering, dependencies, and file consistency."""
        errors: list[str] = []

        # 1. Step numbers must be sequential 1..N
        expected = list(range(1, len(self.steps) + 1))
        actual = [s.step_number for s in self.steps]
        if actual != expected:
            errors.append(
                f"Step numbers must be sequential 1..{len(self.steps)}, "
                f"got {actual}."
            )

        # 2. Dependencies must reference earlier steps (no forward / self refs)
        valid_step_numbers = set(expected)
        for step in self.steps:
            for dep in step.dependencies:
                if dep not in valid_step_numbers:
                    errors.append(
                        f"Step {step.step_number} depends on non-existent step {dep}."
                    )
                elif dep >= step.step_number:
                    errors.append(
                        f"Step {step.step_number} depends on step {dep} "
                        f"which is not an earlier step (would create a cycle)."
                    )

        # 3. Files mentioned in steps should be in the plan-level file lists
        #    (warning-level: we fix this automatically rather than failing)
        plan_files = set(
            self.files_to_create + self.files_to_modify + self.files_to_delete
        )
        for step in self.steps:
            for f in step.files_involved:
                if f and f not in plan_files:
                    # Auto-add to files_to_modify (best guess)
                    self.files_to_modify.append(f)
                    plan_files.add(f)

        if errors:
            raise ValueError(
                "Plan validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
            )

        return self


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
    # Try the raw text as-is (might already be valid JSON)
    return text.strip()


def validate_plan(raw: str) -> ImplementationPlan:
    """
    Parse and validate a raw JSON string (possibly markdown-fenced) into an
    :class:`ImplementationPlan`.

    Args:
        raw: Raw text from the LLM response.

    Returns:
        A fully validated ``ImplementationPlan``.

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

    return ImplementationPlan.model_validate(data)
