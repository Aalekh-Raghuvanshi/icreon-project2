"""
Prompt templates for the Reviewer agent.

The Reviewer triages a **failing test run**, using a **senior engineer**
persona.  As with the Planner and Coder, prompts are kept separate from the
agent logic so they can be iterated on independently.

Prompt structure
-----------------
1. **System prompt** — defines the persona, constraints, and output format.
2. **Review prompt** — injects the task, plan summary, and the failing
   ``TestResult`` output to triage.
3. **Retry prompt** — sent when a previous attempt produced invalid JSON,
   including the error message so the LLM can self-correct.
"""

from __future__ import annotations

import json

from ai_swe.agents.reviewer_models import ReviewOutcome
from ai_swe.state import TestResult

# ---------------------------------------------------------------------------
# System prompt — "You are a senior engineer triaging a failing test run"
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """\
You are a **Senior Engineer** triaging a failing test run.  Your job is to
parse the stack traces and logs from a failing test suite into precise,
structured errors, and suggest the minimal fix for each one.

## How you work

1. **Read the raw test output carefully.**  Identify each distinct failure
   -- a single test run can contain multiple independent errors.

2. **Extract precise, structured detail for each failure.**  Identify the
   file and line the failure originates from when the traceback makes that
   determinable, the error type (e.g. `AssertionError`, `ImportError`,
   `TypeError`), and a concise human-readable message.

3. **Suggest the minimal fix.**  Describe the smallest change that would
   resolve the failure -- do not propose unrelated refactoring or rewrites.

4. **Judge the run as a whole.**  If every test passed, the verdict is
   `approve`.  If any test failed, the verdict is `needs_fix` and every
   failure must be represented in `errors`.

## Constraints

- Do NOT invent errors that aren't evidenced by the provided output.
- Do NOT suggest fixes broader than what's needed to make the failing
  test(s) pass.
- If the file/line cannot be determined from the output, leave them null
  rather than guessing.

## Output format

You MUST respond with a **single JSON object** (no markdown fences, no prose
before or after) that matches this exact schema:

{schema}

Respond ONLY with the JSON object.  No explanations, no markdown, no preamble.
"""


def get_schema_string() -> str:
    """Return a pretty-printed JSON schema for ``ReviewOutcome``."""
    schema = ReviewOutcome.model_json_schema()
    return json.dumps(schema, indent=2)


def build_system_prompt() -> str:
    """Build the complete system prompt with the schema injected."""
    return REVIEWER_SYSTEM_PROMPT.format(schema=get_schema_string())


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------


def build_review_prompt(
    task: str,
    plan_summary: str | None,
    failing_results: list[TestResult],
) -> str:
    """
    Construct the full user prompt for the Reviewer LLM call.

    Args:
        task:            Natural-language description of the overall task.
        plan_summary:    One-paragraph summary of the overall plan, for context.
        failing_results: The failing ``TestResult`` entries to triage.

    Returns:
        The assembled user prompt string.
    """
    results_block = "\n\n".join(
        f"### {r.name}\n```\n{(r.output or '').strip()}\n```" for r in failing_results
    )

    return f"""\
## Task

{task}

## Plan Summary

{plan_summary or "(no plan summary provided)"}

## Failing Test Output

{results_block}

## Your Job

Triage the failing test output above.  Output a single JSON object matching
the ReviewOutcome schema: `verdict` is `needs_fix` (since at least one test
failed), and `errors` contains one structured ErrorReport per distinct
failure, each with the minimal suggested fix.
"""


# ---------------------------------------------------------------------------
# Retry prompt
# ---------------------------------------------------------------------------


def build_retry_prompt(previous_error: str, attempt: int) -> str:
    """
    Build a follow-up prompt when the previous LLM response was invalid.

    Args:
        previous_error: The validation error message from the failed parse.
        attempt:        The current attempt number (2-based, since 1 was first try).

    Returns:
        A prompt instructing the LLM to fix its output.
    """
    return f"""\
Your previous response (attempt {attempt - 1}) could not be parsed.

**Error:**
{previous_error}

Please fix the issue and respond again with ONLY a valid JSON object matching
the ReviewOutcome schema.  Common issues:
- Missing required fields (verdict, and error_type/message/traceback_excerpt
  on each ErrorReport)
- `verdict` not one of "approve" / "needs_fix"
- JSON syntax errors (trailing commas, unescaped quotes)

Respond ONLY with the corrected JSON.
"""
