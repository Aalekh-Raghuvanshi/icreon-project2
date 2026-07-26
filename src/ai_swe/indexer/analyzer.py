"""
CodebaseAnalyzer — the main orchestrator for repository understanding.

Coordinates the full indexing pipeline:

  1. Walk repository (``walker.py``)
  2. Detect language per file (``language.py``)
  3. Parse AST symbols concurrently (``parser.py``)
  4. Generate semantic summaries (``summarizer.py``)
  5. Build dependency graph (``graph.py``)
  6. Compute aggregate statistics
  7. Return a fully-populated ``RepositoryIndex``

Usage::

    import asyncio
    from pathlib import Path
    from ai_swe.indexer.analyzer import CodebaseAnalyzer

    analyzer = CodebaseAnalyzer("/path/to/repo")
    index = asyncio.run(analyzer.analyze_repository())

    print(f"Files: {index.statistics.total_files}")
    print(f"LOC: {index.statistics.total_loc}")
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ai_swe.indexer.graph import build_adjacency, build_dependency_graph, most_connected_files
from ai_swe.indexer.language import detect_language
from ai_swe.indexer.models import (
    FileSummary,
    Language,
    RepositoryIndex,
    RepositoryStatistics,
    Symbol,
    SymbolKind,
)
from ai_swe.indexer.parser import parse_file
from ai_swe.indexer.summarizer import summarize_file
from ai_swe.indexer.walker import collect_files

logger = logging.getLogger(__name__)

# Maximum number of files to parse concurrently.
# Tree-sitter is a sync C extension; we use asyncio.to_thread to avoid
# blocking the event loop.
_DEFAULT_CONCURRENCY = 8


class CodebaseAnalyzer:
    """
    Analyses a repository and returns a complete :class:`RepositoryIndex`.

    Args:
        repo_path:   Path to the local repository root.
        concurrency: Maximum number of files parsed concurrently (default 8).
        console:     Optional Rich ``Console`` for progress display.
                     Pass ``None`` (or omit) for silent operation.
    """

    def __init__(
        self,
        repo_path: str | Path,
        concurrency: int = _DEFAULT_CONCURRENCY,
        console: Console | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).expanduser().resolve()
        self.concurrency = concurrency
        self._console = console

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_progress(self) -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )

    @staticmethod
    def _parse_one(path: Path, language: Language) -> list[Symbol]:
        """Sync helper called from a thread pool."""
        return parse_file(path, language)

    # ------------------------------------------------------------------
    # Statistics builder
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_statistics(
        summaries: dict[str, FileSummary],
        top_connected: list[str],
        n_nodes: int,
        n_edges: int,
        repo_root: Path,
    ) -> RepositoryStatistics:
        lang_breakdown: dict[str, int] = {}
        lang_loc: dict[str, int] = {}
        total_loc = 0
        total_lines = 0
        total_functions = 0
        total_classes = 0
        total_imports = 0

        for summary in summaries.values():
            lang_name = summary.language.value
            lang_breakdown[lang_name] = lang_breakdown.get(lang_name, 0) + 1
            lang_loc[lang_name] = lang_loc.get(lang_name, 0) + summary.loc
            total_loc += summary.loc
            total_lines += summary.total_lines
            for sym in summary.symbols:
                if sym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                    total_functions += 1
                elif sym.kind == SymbolKind.CLASS:
                    total_classes += 1
                elif sym.kind == SymbolKind.IMPORT:
                    total_imports += 1

        # Top-level packages — first component of relative paths that appear
        # more than once and look like packages (have sub-files)
        package_counts: dict[str, int] = {}
        for rel in summaries:
            parts = Path(rel).parts
            if len(parts) > 1:
                package_counts[parts[0]] = package_counts.get(parts[0], 0) + 1
        top_packages = sorted(package_counts, key=lambda k: package_counts[k], reverse=True)[:10]

        return RepositoryStatistics(
            total_files=len(summaries),
            total_loc=total_loc,
            total_lines=total_lines,
            language_breakdown=lang_breakdown,
            language_loc=lang_loc,
            total_functions=total_functions,
            total_classes=total_classes,
            total_imports=total_imports,
            most_connected_files=top_connected,
            top_level_packages=top_packages,
            dependency_graph_nodes=n_nodes,
            dependency_graph_edges=n_edges,
        )

    # ------------------------------------------------------------------
    # Main public method
    # ------------------------------------------------------------------

    async def analyze_repository(self) -> RepositoryIndex:
        """
        Run the full indexing pipeline and return a :class:`RepositoryIndex`.

        Returns:
            A complete index containing:
              - ``file_tree``       — sorted list of relative paths
              - ``summaries``       — per-file ``FileSummary`` keyed by relative path
              - ``dependency_graph``— list of ``DependencyEdge``
              - ``statistics``      — aggregate ``RepositoryStatistics``
              - ``adjacency``       — plain-dict adjacency list
        """
        if not self.repo_path.is_dir():
            raise NotADirectoryError(f"Repository path does not exist: {self.repo_path}")

        repo_name = self.repo_path.name

        # ── Step 1: Walk ────────────────────────────────────────────────────
        if self._console:
            self._console.print(
                f"\n[bold bright_cyan]🔍 Walking repository:[/] {self.repo_path}"
            )

        all_files = collect_files(self.repo_path)
        total = len(all_files)

        if self._console:
            self._console.print(f"   Found [bold green]{total}[/bold green] source files\n")

        if total == 0:
            return RepositoryIndex(
                repo_path=str(self.repo_path),
                repo_name=repo_name,
            )

        # ── Step 2: Language detection ──────────────────────────────────────
        languages: dict[Path, Language] = {f: detect_language(f) for f in all_files}

        # ── Step 3: Parse symbols (concurrent) ─────────────────────────────
        semaphore = asyncio.Semaphore(self.concurrency)
        all_symbols: dict[Path, list[Symbol]] = {}

        async def _parse_bounded(path: Path, lang: Language) -> tuple[Path, list[Symbol]]:
            async with semaphore:
                syms = await asyncio.to_thread(self._parse_one, path, lang)
            return path, syms

        with self._make_progress() as progress:
            parse_task = progress.add_task(
                "[cyan]Parsing AST symbols…", total=total
            )
            tasks = [_parse_bounded(f, languages[f]) for f in all_files]
            for coro in asyncio.as_completed(tasks):
                path, syms = await coro
                all_symbols[path] = syms
                progress.advance(parse_task)

        # ── Step 4: Summarize files ─────────────────────────────────────────
        summaries: dict[str, FileSummary] = {}

        with self._make_progress() as progress:
            sum_task = progress.add_task(
                "[yellow]Generating summaries…", total=total
            )
            for filepath in all_files:
                rel = str(filepath.relative_to(self.repo_path))
                # Normalise to forward slashes on Windows too
                rel = rel.replace("\\", "/")
                summary = summarize_file(
                    path=filepath,
                    relative_path=rel,
                    language=languages[filepath],
                    symbols=all_symbols.get(filepath, []),
                )
                summaries[rel] = summary
                progress.advance(sum_task)

        # ── Step 5: Build dependency graph ──────────────────────────────────
        if self._console:
            self._console.print("[bold magenta]🕸  Building dependency graph…[/]")

        G, edges = await asyncio.to_thread(
            build_dependency_graph, summaries, self.repo_path
        )
        top_connected = most_connected_files(G, top_n=10)
        adjacency = build_adjacency(edges)

        # ── Step 6: Compute statistics ──────────────────────────────────────
        stats = self._compute_statistics(
            summaries,
            top_connected,
            G.number_of_nodes(),
            G.number_of_edges(),
            self.repo_path,
        )

        # ── Step 7: Assemble index ──────────────────────────────────────────
        file_tree = sorted(summaries.keys())

        index = RepositoryIndex(
            repo_path=str(self.repo_path),
            repo_name=repo_name,
            file_tree=file_tree,
            summaries=summaries,
            dependency_graph=edges,
            statistics=stats,
            adjacency=adjacency,
        )

        logger.info(
            "Analysis complete: %d files, %d LOC, %d symbols",
            stats.total_files,
            stats.total_loc,
            stats.total_functions + stats.total_classes,
        )

        return index
