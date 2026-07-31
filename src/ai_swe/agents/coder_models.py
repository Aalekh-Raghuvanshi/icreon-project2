"""
Pydantic v2 models for the Coder agent's structured output.

The Coder produces a :class:`StepChangeSet` for a single plan step — the set
of file edits needed to implement that step, plus a short rationale.  Each
:class:`FileEdit` either replaces a file's full content (for new files or
full rewrites) or applies one or more search/replace blocks (for small,
targeted edits to existing files).

Validation rules
-----------------
* ``action="delete"`` requires neither ``new_content`` nor ``search_replace``.
* ``action`` of ``create``/``modify`` requires exactly one of ``new_content``
  or ``search_replace`` (a non-empty list).
* A ``StepChangeSet`` must contain at least one edit.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EditAction(str, Enum):
    """The kind of change a single `FileEdit` makes to a file."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


# ---------------------------------------------------------------------------
# Search/replace block
# ---------------------------------------------------------------------------


class SearchReplaceBlock(BaseModel):
    """A single find-and-replace edit applied to an existing file's content."""

    search: str = Field(min_length=1, description="Exact existing text to find.")
    replace: str = Field(description="Text to replace the matched `search` text with.")


# ---------------------------------------------------------------------------
# File-level edit
# ---------------------------------------------------------------------------


class FileEdit(BaseModel):
    """A single proposed change to one file."""

    file_path: str = Field(description="Path of the file to change, relative to repo root.")
    action: EditAction = Field(description="Whether this file is being created, modified, or deleted.")
    new_content: str | None = Field(
        default=None,
        description="Full replacement content, used for new files or full-file rewrites.",
    )
    search_replace: list[SearchReplaceBlock] = Field(
        default_factory=list,
        description="Targeted find/replace edits, used for small changes to existing files.",
    )
    description: str | None = Field(default=None, description="Why this file is being changed.")

    @model_validator(mode="after")
    def _validate_edit_shape(self) -> FileEdit:
        if self.action == EditAction.DELETE:
            return self

        has_full_content = self.new_content is not None
        has_search_replace = len(self.search_replace) > 0
        if not has_full_content and not has_search_replace:
            raise ValueError(
                f"FileEdit for '{self.file_path}' (action={self.action.value}) must set "
                "either `new_content` or a non-empty `search_replace` list."
            )
        return self


# ---------------------------------------------------------------------------
# Top-level change set
# ---------------------------------------------------------------------------


class StepChangeSet(BaseModel):
    """
    The complete set of file edits produced by the Coder agent for a single
    plan step.
    """

    step_id: str = Field(min_length=1, description="ID of the plan step this change set implements.")
    rationale: str = Field(min_length=1, description="Why these changes implement the step.")
    edits: list[FileEdit] = Field(min_length=1, description="Ordered file edits to apply.")


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


def validate_changeset(raw: str) -> StepChangeSet:
    """
    Parse and validate a raw JSON string (possibly markdown-fenced) into a
    :class:`StepChangeSet`.

    Args:
        raw: Raw text from the LLM response.

    Returns:
        A fully validated ``StepChangeSet``.

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

    return StepChangeSet.model_validate(data)
