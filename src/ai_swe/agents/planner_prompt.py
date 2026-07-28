"""
Prompt templates for the Planner agent.

The Planner uses a **senior software architect** persona to analyse a
codebase and produce a structured implementation plan.  The prompts are
separated from the agent logic so they can be iterated on independently.

Prompt structure
-----------------
1. **System prompt** — defines the persona, constraints, and output format.
2. **Planning prompt** — injects the task description, repository summary,
   and selectively-retrieved file contexts.
3. **Retry prompt** — sent when a previous attempt produced invalid JSON,
   including the error message so the LLM can self-correct.
"""

from __future__ import annotations

from ai_swe.agents.planner_models import ImplementationPlan

# ---------------------------------------------------------------------------
# System prompt — "You are a senior software architect"
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """\
You are a **Senior Software Architect** with 15+ years of experience building
and evolving production systems.  Your job is to analyse a codebase and a task
description, then produce a precise, step-by-step implementation plan.

## How you think

1. **Understand the codebase first.**  Read the repository structure, file
   purposes, dependency graph, and any file contents provided to you.
   Identify patterns, conventions, and architectural decisions already made.

2. **Map the impact.**  Before proposing changes, trace how the task affects
   the existing architecture.  Which modules are touched?  Which interfaces
   change?  Are there downstream consumers that break?

3. **Break the work into atomic steps.**  Each step should be independently
   implementable and testable.  Order them so that dependencies are built
   before dependents.

4. **Be honest about risk.**  Mark steps that touch critical paths, break
   interfaces, or require migration as HIGH or CRITICAL risk.  Low-risk
   cosmetic or additive steps should be LOW.

5. **Explain your reasoning.**  For every step, explain *why* it is necessary
   — not just *what* it does.  Justify your ordering.

## Constraints

- Do NOT propose unnecessary refactoring.  Change only what the task requires.
- Respect the existing code style, naming conventions, and architecture.
- If the task is ambiguous, state your assumptions explicitly.
- Consider backwards compatibility — can existing tests still pass after
  each step?
- Suggest tests for new or changed behaviour.

## Output format

You MUST respond with a **single JSON object** (no markdown fences, no prose
before or after) that matches this exact schema:

{schema}

Respond ONLY with the JSON object.  No explanations, no markdown, no preamble.
"""

# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------


def build_planning_prompt(
    task: str,
    repo_summary: str,
    file_contexts: str,
) -> str:
    """
    Construct the full user prompt for the Planner LLM call.

    Args:
        task:           Natural-language task description.
        repo_summary:   Compact repository summary (file tree + annotations).
        file_contexts:  Formatted contents of selectively-retrieved files.

    Returns:
        The assembled user prompt string.
    """
    return f"""\
## Task

{task}

## Repository Overview

{repo_summary}

## Relevant File Contents

The following files were selected as most relevant to this task.  Use them
to understand the existing implementation, patterns, and interfaces.

{file_contexts}

## Your Job

Produce an implementation plan for the task above.  Remember:
- Steps must be ordered so dependencies come first.
- Each step must be atomic and independently testable.
- Assign honest risk levels.
- Output ONLY a single JSON object matching the schema.
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
the ImplementationPlan schema.  Common issues:
- Missing required fields (task, summary, steps with goal/reasoning/expected_outcome)
- Step numbers not sequential (must be 1, 2, 3, …)
- Dependencies referencing future steps (must reference earlier step numbers)
- JSON syntax errors (trailing commas, unescaped quotes)

Respond ONLY with the corrected JSON.
"""


# ---------------------------------------------------------------------------
# Schema string for injection into system prompt
# ---------------------------------------------------------------------------


def get_schema_string() -> str:
    """Return a pretty-printed JSON schema for ``ImplementationPlan``."""
    schema = ImplementationPlan.model_json_schema()
    import json

    return json.dumps(schema, indent=2)


def build_system_prompt() -> str:
    """Build the complete system prompt with the schema injected."""
    return PLANNER_SYSTEM_PROMPT.format(schema=get_schema_string())
