"""
Selective file retrieval layer for the Planner agent.

The Planner should **never** request the entire repository.  Instead it uses
the pre-built :class:`~ai_swe.indexer.models.RepositoryIndex` to understand
the codebase structure, then asks for only the files it needs.  This module
implements that retrieval.

Design
------
``FileRetriever`` is initialised with a ``RepositoryIndex`` and the path to
the local checkout.  It exposes three public methods:

* ``find_relevant_files(query)`` — rank repo files by relevance to a
  natural-language task description, using keyword matching against file
  summaries and dependency-graph expansion.
* ``retrieve_files(paths)``     — read only the requested files from disk.
* ``get_file_context(paths)``   — produce a formatted string of file
  summaries + truncated contents, ready for prompt injection.

No embedding model or vector store is used — relevance is computed purely
from the index's per-file summaries, symbol names, and dependency edges.
This keeps the retrieval deterministic, fast, and offline-capable.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ai_swe.indexer.models import FileSummary, RepositoryIndex

logger = logging.getLogger(__name__)

# Maximum total characters of file content we'll include in a single
# retrieval batch.  This keeps the LLM prompt within reasonable token budgets.
DEFAULT_MAX_CONTENT_CHARS = 50_000

# Maximum characters of a single file to include before truncation.
MAX_SINGLE_FILE_CHARS = 8_000


class FileRetriever:
    """
    Selective file retrieval backed by a :class:`RepositoryIndex`.

    Args:
        index:     A pre-built repository index.
        repo_path: Absolute path to the local checkout.
        max_content_chars: Total content budget across all retrieved files.
    """

    def __init__(
        self,
        index: RepositoryIndex,
        repo_path: str | Path,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        self.index = index
        self.repo_path = Path(repo_path).expanduser().resolve()
        self.max_content_chars = max_content_chars

    # ------------------------------------------------------------------
    # 1. Relevance ranking
    # ------------------------------------------------------------------

    def find_relevant_files(
        self,
        query: str,
        *,
        top_n: int = 15,
        expand_deps: bool = True,
    ) -> list[str]:
        """
        Rank repository files by relevance to *query* and return the top *N*.

        Scoring heuristic (all additive, max-normalised):
          1. **Keyword hits in purpose/path** — how many query tokens appear
             in the file's purpose string or relative path.
          2. **Symbol name hits** — query tokens that match function/class
             names in the file.
          3. **Dependency proximity** — files that are imported by, or import,
             already-high-scoring files get a score boost.

        Args:
            query:       Natural-language task description.
            top_n:       Maximum number of files to return.
            expand_deps: If True, boost neighbours in the dependency graph.

        Returns:
            A list of relative file paths, best-match first.
        """
        if not self.index.summaries:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return list(self.index.summaries.keys())[:top_n]

        scores: dict[str, float] = {}

        for rel_path, summary in self.index.summaries.items():
            score = 0.0

            # a) Path + purpose keyword matching
            searchable = f"{rel_path} {summary.purpose}".lower()
            hits = sum(1 for t in tokens if t in searchable)
            score += hits * 2.0

            # b) Symbol name matching
            symbol_text = " ".join(
                s.name.lower() for s in summary.symbols
            )
            sym_hits = sum(1 for t in tokens if t in symbol_text)
            score += sym_hits * 1.5

            # c) Class / function name matching (higher weight)
            class_fn_text = " ".join(
                n.lower()
                for n in summary.major_classes + summary.important_functions
            )
            cf_hits = sum(1 for t in tokens if t in class_fn_text)
            score += cf_hits * 3.0

            # d) Dependency name matching
            dep_text = " ".join(d.lower() for d in summary.dependencies)
            dep_hits = sum(1 for t in tokens if t in dep_text)
            score += dep_hits * 1.0

            scores[rel_path] = score

        # Expand scores via dependency graph
        if expand_deps and self.index.adjacency:
            boosted = dict(scores)
            for src_file, targets in self.index.adjacency.items():
                src_score = scores.get(src_file, 0.0)
                if src_score > 0:
                    for tgt in targets:
                        if tgt in boosted:
                            boosted[tgt] += src_score * 0.3

            # Also boost files that *import* a high-scoring file
            reverse_adj: dict[str, list[str]] = {}
            for src, targets in self.index.adjacency.items():
                for tgt in targets:
                    reverse_adj.setdefault(tgt, []).append(src)

            for tgt_file, importers in reverse_adj.items():
                tgt_score = scores.get(tgt_file, 0.0)
                if tgt_score > 0:
                    for imp in importers:
                        if imp in boosted:
                            boosted[imp] += tgt_score * 0.2

            scores = boosted

        # Sort by score descending, then alphabetically for ties
        ranked = sorted(
            scores.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )

        # Filter out zero-score files unless we have fewer than top_n results
        result = [path for path, score in ranked if score > 0]
        if len(result) < top_n:
            # Pad with remaining files alphabetically
            seen = set(result)
            for path, _ in ranked:
                if path not in seen:
                    result.append(path)
                    seen.add(path)
                if len(result) >= top_n:
                    break

        return result[:top_n]

    # ------------------------------------------------------------------
    # 2. File content retrieval
    # ------------------------------------------------------------------

    def retrieve_files(self, file_paths: list[str]) -> dict[str, str]:
        """
        Read only the requested files from disk.

        Args:
            file_paths: Relative paths (as they appear in the index).

        Returns:
            A dict mapping relative path → file content (UTF-8).
            Files that cannot be read are silently skipped with a warning.
        """
        contents: dict[str, str] = {}
        for rel_path in file_paths:
            abs_path = self.repo_path / rel_path
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
                contents[rel_path] = text
                logger.debug("Retrieved %s (%d chars)", rel_path, len(text))
            except OSError as exc:
                logger.warning("Could not read %s: %s", rel_path, exc)
        return contents

    # ------------------------------------------------------------------
    # 3. Context building for the Planner prompt
    # ------------------------------------------------------------------

    def get_file_context(self, file_paths: list[str]) -> str:
        """
        Produce a formatted context string for prompt injection.

        Each file gets:
          * A header with path, language, purpose, and key symbols.
          * The file's content (truncated if it exceeds the per-file limit).

        The total output is capped at ``max_content_chars``.

        Args:
            file_paths: Relative paths to include in the context.

        Returns:
            A formatted multi-file context string.
        """
        contents = self.retrieve_files(file_paths)
        budget = self.max_content_chars
        sections: list[str] = []

        for rel_path in file_paths:
            if budget <= 0:
                sections.append(
                    f"\n--- {rel_path} ---\n[SKIPPED: content budget exhausted]\n"
                )
                continue

            summary = self.index.summaries.get(rel_path)
            header_parts = [f"\n{'=' * 60}", f"FILE: {rel_path}"]
            if summary:
                header_parts.append(f"Language: {summary.language.value}")
                header_parts.append(f"Purpose: {summary.purpose}")
                if summary.major_classes:
                    header_parts.append(
                        f"Classes: {', '.join(summary.major_classes)}"
                    )
                if summary.important_functions:
                    header_parts.append(
                        f"Functions: {', '.join(summary.important_functions[:10])}"
                    )
                if summary.dependencies:
                    header_parts.append(
                        f"Imports: {', '.join(summary.dependencies[:10])}"
                    )
            header_parts.append("=" * 60)
            header = "\n".join(header_parts)

            raw = contents.get(rel_path, "[file not found on disk]")

            # Truncate individual file if too long
            if len(raw) > MAX_SINGLE_FILE_CHARS:
                raw = raw[:MAX_SINGLE_FILE_CHARS] + "\n\n... [truncated — file continues] ...\n"

            section = f"{header}\n{raw}\n"

            if len(section) > budget:
                # Truncate this section to fit the remaining budget
                section = section[:budget] + "\n... [budget exhausted] ...\n"

            sections.append(section)
            budget -= len(section)

        return "".join(sections)

    # ------------------------------------------------------------------
    # 4. Repository summary (compact, for prompt header)
    # ------------------------------------------------------------------

    def build_repo_summary(self) -> str:
        """
        Build a compact repository summary from the index.

        Includes the file tree, language breakdown, statistics, and
        per-file purpose annotations — but **not** file contents.
        """
        idx = self.index
        stats = idx.statistics
        lines: list[str] = [
            f"Repository: {idx.repo_name}",
            f"Total files: {stats.total_files}",
            f"Total LOC: {stats.total_loc:,}",
            "",
            "Language breakdown:",
        ]

        for lang, count in sorted(
            stats.language_breakdown.items(), key=lambda kv: -kv[1]
        ):
            loc = stats.language_loc.get(lang, 0)
            lines.append(f"  {lang}: {count} files, {loc:,} LOC")

        lines.append("")
        lines.append("File tree (with purpose annotations):")

        for rel_path in idx.file_tree:
            summary = idx.summaries.get(rel_path)
            purpose = summary.purpose[:80] if summary else "—"
            lines.append(f"  {rel_path}  →  {purpose}")

        if idx.statistics.most_connected_files:
            lines.append("")
            lines.append("Most connected files (by import degree):")
            for f in idx.statistics.most_connected_files[:5]:
                lines.append(f"  • {f}")

        if idx.statistics.top_level_packages:
            lines.append("")
            lines.append(
                f"Top-level packages: {', '.join(idx.statistics.top_level_packages)}"
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Words to ignore when tokenizing a query
_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might must can could "
    "and or but not no nor so for yet both either neither "
    "in on at to from by with of as into through during "
    "i we you he she it they me him her us them my our your his its their "
    "this that these those what which who whom whose when where why how "
    "all each every some any few more most other such "
    "add create implement build make update modify change fix remove delete "
    "new".split()
)


def _tokenize(text: str) -> list[str]:
    """
    Split *text* into lowercase tokens, filtering out stop words and
    very short tokens.
    """
    raw = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
    return [t for t in raw if t not in _STOP_WORDS and len(t) > 2]
