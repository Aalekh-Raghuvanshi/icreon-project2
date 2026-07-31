"""
Edit application engine for the Coder agent.

Takes the `FileEdit`s produced by the Coder's LLM call and actually applies
them to a repository checkout:

  * When an `MCPOrchestrator` is supplied, writes go through the Filesystem
    MCP server (`write_file` for full-content edits, `edit_file` for
    search/replace edits) -- consistent with the rest of the codebase's rule
    that filesystem access is always mediated by MCP.
  * When no orchestrator is supplied, writes go directly through `pathlib`.
    This keeps the engine unit-testable offline, without spawning the
    Filesystem MCP server subprocess.

Safety
------
Before the first edit to any file, `EditEngine` records that file's original
content (or `None` if it didn't exist).  After applying an edit, the new
content is verified for syntax (Python via `ast.parse`; JS/TS via the
tree-sitter grammars already declared in `pyproject.toml`).  If any edit in
a changeset produces invalid syntax, or otherwise fails to apply, the whole
changeset is rolled back via `rollback()` so a partially-applied, broken
change never lingers on disk.
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path
from typing import Any

from tree_sitter import Parser

from ai_swe.agents.coder_models import EditAction, FileEdit, SearchReplaceBlock
from ai_swe.indexer.models import Language
from ai_swe.indexer.parser import _get_ts_language
from ai_swe.logging_config import get_logger
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.mcp.factory import FILESYSTEM_SERVER
from ai_swe.state import Patch

logger = get_logger(__name__)

# Extensions verified via tree-sitter, mapped to their `Language` enum member.
_TS_EXTENSIONS: dict[str, Language] = {
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
}


class EditApplyError(RuntimeError):
    """Raised when a `FileEdit` cannot be applied (e.g. search text not found)."""


class SyntaxVerificationError(RuntimeError):
    """Raised when an edit's resulting file content fails syntax verification."""


def _apply_search_replace(content: str, blocks: list[SearchReplaceBlock]) -> str:
    """Apply each search/replace block to `content` in order, single-match."""
    for block in blocks:
        if block.search not in content:
            raise EditApplyError(f"search text not found: {block.search!r}")
        content = content.replace(block.search, block.replace, 1)
    return content


def _verify_syntax(rel_path: str, content: str) -> None:
    """
    Best-effort syntax verification for `content`, based on file extension.

    Python is verified strictly (a `SyntaxError` always fails the edit).
    JS/TS are verified via tree-sitter's error-recovery parser; if the
    grammar can't be loaded, verification is skipped rather than failing
    edits that we simply can't check.
    """
    suffix = Path(rel_path).suffix.lower()

    if suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise SyntaxVerificationError(f"{rel_path}: invalid Python syntax: {exc}") from exc
        return

    lang = _TS_EXTENSIONS.get(suffix)
    if lang is None:
        return

    ts_lang = _get_ts_language(lang)
    if ts_lang is None:
        logger.warning("Skipping syntax verification for %s: grammar unavailable", rel_path)
        return

    parser = Parser(ts_lang)
    tree = parser.parse(content.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxVerificationError(f"{rel_path}: tree-sitter detected a syntax error")


def _make_diff(rel_path: str, before: str | None, after: str | None) -> str:
    """Produce a unified diff between `before` and `after` for `rel_path`."""
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{rel_path}" if before is not None else "/dev/null",
        tofile=f"b/{rel_path}" if after is not None else "/dev/null",
        lineterm="",
    )
    return "\n".join(diff)


class EditEngine:
    """
    Applies a step's `FileEdit`s to a repository checkout, with backup and
    rollback support.

    Args:
        repo_path:    Root directory the edits are applied relative to.
        orchestrator: A connected `MCPOrchestrator` with the Filesystem
            server registered. If `None`, edits are applied directly via
            `pathlib` (used in tests and any offline/standalone mode).
    """

    def __init__(self, repo_path: str | Path, orchestrator: MCPOrchestrator | None = None) -> None:
        self.repo_path = Path(repo_path)
        self.orchestrator = orchestrator
        # rel_path -> original content, or None if the file didn't exist yet.
        self._backups: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Low-level file I/O (MCP-mediated, or direct pathlib fallback)
    # ------------------------------------------------------------------

    async def _read_current(self, rel_path: str) -> str | None:
        abs_path = self.repo_path / rel_path
        if self.orchestrator is not None:
            try:
                content: Any = await self.orchestrator.call(
                    FILESYSTEM_SERVER, "read_file", {"path": str(abs_path)}
                )
            except Exception:  # noqa: BLE001
                return None
            return content if isinstance(content, str) else str(content)

        if abs_path.is_file():
            return abs_path.read_text(encoding="utf-8")
        return None

    async def _write_full(self, rel_path: str, content: str) -> None:
        abs_path = self.repo_path / rel_path
        if self.orchestrator is not None:
            await self.orchestrator.call(
                FILESYSTEM_SERVER, "write_file", {"path": str(abs_path), "content": content}
            )
            return

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")

    async def _write_search_replace(self, rel_path: str, blocks: list[SearchReplaceBlock], content: str) -> None:
        """Apply `blocks` via the MCP `edit_file` tool, or write `content` directly offline."""
        abs_path = self.repo_path / rel_path
        if self.orchestrator is not None:
            mcp_edits = [{"oldText": b.search, "newText": b.replace} for b in blocks]
            await self.orchestrator.call(
                FILESYSTEM_SERVER, "edit_file", {"path": str(abs_path), "edits": mcp_edits}
            )
            return

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")

    async def _delete_file(self, rel_path: str) -> None:
        abs_path = self.repo_path / rel_path
        if self.orchestrator is not None:
            logger.warning(
                "Filesystem MCP server has no delete tool; deleting %s directly.", rel_path
            )
        if abs_path.is_file():
            abs_path.unlink()

    # ------------------------------------------------------------------
    # Single-edit application
    # ------------------------------------------------------------------

    async def _apply_one(self, edit: FileEdit) -> Patch:
        rel_path = edit.file_path
        original = await self._read_current(rel_path)
        if rel_path not in self._backups:
            self._backups[rel_path] = original

        if edit.action == EditAction.DELETE:
            await self._delete_file(rel_path)
            diff = _make_diff(rel_path, original, None)
            return Patch(file_path=rel_path, diff=diff, description=edit.description)

        if edit.new_content is not None:
            new_content = edit.new_content
            _verify_syntax(rel_path, new_content)
            await self._write_full(rel_path, new_content)
        else:
            new_content = _apply_search_replace(original or "", edit.search_replace)
            _verify_syntax(rel_path, new_content)
            await self._write_search_replace(rel_path, edit.search_replace, new_content)

        diff = _make_diff(rel_path, original, new_content)
        return Patch(file_path=rel_path, diff=diff, description=edit.description)

    # ------------------------------------------------------------------
    # Changeset application + rollback
    # ------------------------------------------------------------------

    async def apply_changeset(self, edits: list[FileEdit]) -> list[Patch]:
        """
        Apply every edit in `edits`, in order.

        If any edit fails to apply or fails syntax verification, every file
        touched so far in this call is rolled back to its pre-changeset
        content before the exception is re-raised.
        """
        patches: list[Patch] = []
        try:
            for edit in edits:
                patches.append(await self._apply_one(edit))
            return patches
        except Exception:
            await self.rollback()
            raise

    async def rollback(self) -> None:
        """Restore every file touched so far to its original content."""
        for rel_path, original in self._backups.items():
            abs_path = self.repo_path / rel_path
            if original is None:
                if abs_path.is_file():
                    abs_path.unlink()
            else:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(original, encoding="utf-8")
        self._backups.clear()
