"""
Prompt templates for the Coder agent.

The Coder implements a **single plan step** at a time, using a **senior
software engineer** persona.  As with the Planner, prompts are kept separate
from the agent logic so they can be iterated on independently.

Prompt structure
-----------------
1. **System prompt** — defines the persona, constraints, and output format.
2. **Coding prompt** — injects the current plan step, the overall plan
   summary, and the current contents of the files that step touches.
3. **Retry prompt** — sent when a previous attempt produced invalid JSON,
   including the error message so the LLM can self-correct.
"""

from __future__ import annotations

import json

from ai_swe.agents.coder_models import StepChangeSet
from ai_swe.state import ErrorReport, PlanStep

# ---------------------------------------------------------------------------
# System prompt — "You are a senior software engineer"
# ---------------------------------------------------------------------------

CODER_SYSTEM_PROMPT = """\
You are a **Senior Software Engineer** implementing **ONE step** of an
already-approved implementation plan.  You do not decide *what* to build --
that decision was made by the plan.  Your job is to turn this one step into
correct, minimal code changes.

## How you work

1. **Change only what this step requires.**  Do not implement other steps,
   do not refactor unrelated code, and do not add features the step doesn't
   ask for.

2. **Respect existing style.**  Match the codebase's existing naming
   conventions, indentation, import style, and patterns.  Read the provided
   file contents carefully before proposing changes.

3. **Prefer full-file rewrites for small files.**  If the file you are
   editing is small (roughly under 100 lines), set `action="modify"` and
   return the **complete new file content** in `new_content` -- every line,
   not just the changed ones.  A full rewrite is far less likely to fail
   than a `search_replace` block, since there's no exact-text match to get
   wrong.  Only fall back to `search_replace` for **large** files, where
   reproducing the entire file would be wasteful -- and even then, each
   `search` block must match the existing text byte-for-byte.

4. **Never invent file contents you cannot see.**  If a `search_replace`
   block's `search` text does not appear verbatim in the file contents you
   were given, the edit will fail to apply. Only reference text that is
   actually present in the file context provided to you.  The same applies
   to `new_content`: base it on the actual current contents shown to you,
   not on a guess.

5. **Every edit must be justified.**  Set `description` on each `FileEdit`
   explaining briefly why that file needs to change for this step.

## Constraints

- Do NOT touch files outside the step's `files_involved` unless creating a
  new file the step explicitly requires.
- Do NOT add unrelated refactoring, formatting changes, or comments.
- Preserve exact indentation and surrounding code when using `search_replace`.
- If a file is being deleted, set `action` to `"delete"` and omit both
  `new_content` and `search_replace`.

## Output format

You MUST respond with a **single JSON object** (no markdown fences, no prose
before or after) that matches this exact schema:

{schema}

For Python files, `new_content` (and any `search_replace.replace` text) MUST
be **complete, syntactically valid Python** with correct, consistent
indentation -- it is parsed with `ast.parse` and the whole edit is rejected
if it fails to parse.  Double-check indentation, colons, and matching
brackets/parens/quotes before responding.

Respond ONLY with the JSON object.  No explanations, no markdown, no preamble.
"""


def get_schema_string() -> str:
    """Return a pretty-printed JSON schema for ``StepChangeSet``."""
    schema = StepChangeSet.model_json_schema()
    return json.dumps(schema, indent=2)


def build_system_prompt() -> str:
    """Build the complete system prompt with the schema injected."""
    return CODER_SYSTEM_PROMPT.format(schema=get_schema_string())


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------


def _format_errors_section(errors: list[ErrorReport] | None) -> str:
    """Render prior fix-loop errors as a prompt section, or "" if there are none."""
    if not errors:
        return ""

    entries = []
    for e in errors:
        loc = f"{e.file_path or '?'}:{e.line if e.line is not None else '?'}"
        entries.append(
            f"- [{e.error_type}] {loc}: {e.message}\n"
            f"  Traceback: {e.traceback_excerpt}\n"
            f"  Suggested fix: {e.suggested_fix or '(none provided)'}"
        )

    return f"""

## Previous Attempt Failed -- Fix These Errors

The last implementation of this step caused the test suite to fail with the
errors below.  Do NOT redo the whole step from scratch -- make the minimal
change needed to fix these specific errors while keeping the rest of the
step's work intact.

{chr(10).join(entries)}
"""


def build_coding_prompt(
    step: PlanStep,
    plan_summary: str | None,
    file_contents: str,
    errors: list[ErrorReport] | None = None,
) -> str:
    """
    Construct the full user prompt for the Coder LLM call.

    Args:
        step:          The plan step to implement.
        plan_summary:  One-paragraph summary of the overall plan, for context.
        file_contents: Formatted contents of the step's `files_involved`.
        errors:        Structured errors from a previous fix-loop attempt at
                        this step (see `reviewer.py`), if this is a retry.

    Returns:
        The assembled user prompt string.
    """
    deps = ", ".join(step.dependencies) if step.dependencies else "none"
    files = ", ".join(step.files_involved) if step.files_involved else (
        "(none specified -- infer the right file(s) from the step goal)"
    )

    return f"""\
## Plan Summary

{plan_summary or "(no plan summary provided)"}

## Current Step ({step.id})

Goal: {step.description}
Reasoning: {step.reasoning or "(not specified)"}
Expected outcome: {step.expected_outcome or "(not specified)"}
Risk level: {step.risk_level}
Depends on: {deps}
Files involved: {files}

## Current File Contents

The following are the current, on-disk contents of the files this step
involves.  Use them to write edits that apply cleanly.

{file_contents or "(no existing files retrieved -- these are likely new files)"}
{_format_errors_section(errors)}
## Your Job

Implement ONLY this step.  Output a single JSON object matching the
StepChangeSet schema, with one FileEdit per file you touch:
- For small files (under ~100 lines) or new files, use `new_content` with
  the complete, valid, fully-indented new file content.
- Use `search_replace` only for large existing files, where a full rewrite
  would be wasteful.
- Set `action` to "create", "modify", or "delete" as appropriate.
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
the StepChangeSet schema.  Common issues:
- Missing required fields (step_id, rationale, edits)
- A FileEdit missing both `new_content` and `search_replace`
- A `search_replace` block whose `search` text doesn't match the file exactly
- JSON syntax errors (trailing commas, unescaped quotes)

Respond ONLY with the corrected JSON.
"""
