"""
Rich-powered architecture summary reporter.

Renders a beautiful, colour-coded overview of the repository index to the
terminal using the Rich library.  The output is intentionally dense with
information while remaining scannable:

  ┌─ Repository overview panel ─────────────────────────────────────┐
  │  repo name • total files • total LOC • language count            │
  └─────────────────────────────────────────────────────────────────┘
  ┌─ Language breakdown table ──────────────────────────────────────┐
  │  Language │ Files │ % Share │ LOC                               │
  └─────────────────────────────────────────────────────────────────┘
  ┌─ Symbol statistics ─────────────────────────────────────────────┐
  │  Classes │ Functions │ Imports                                   │
  └─────────────────────────────────────────────────────────────────┘
  ┌─ Dependency graph ──────────────────────────────────────────────┐
  │  nodes / edges / isolated files                                  │
  └─────────────────────────────────────────────────────────────────┘
  ┌─ Most connected files ──────────────────────────────────────────┐
  └─────────────────────────────────────────────────────────────────┘
  ┌─ File tree (top N) ─────────────────────────────────────────────┐
  └─────────────────────────────────────────────────────────────────┘

Usage::

    from rich.console import Console
    from ai_swe.indexer.reporter import print_architecture_summary

    console = Console()
    print_architecture_summary(index, console, output_path=Path("..."))
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box

from ai_swe.indexer.models import Language, RepositoryIndex, RepositoryStatistics

# ---------------------------------------------------------------------------
# Language colour palette
# ---------------------------------------------------------------------------

_LANG_COLOURS: dict[str, str] = {
    Language.PYTHON.value: "yellow",
    Language.JAVASCRIPT.value: "bright_yellow",
    Language.TYPESCRIPT.value: "bright_blue",
    Language.JAVA.value: "orange3",
    Language.GO.value: "cyan",
    Language.CPP.value: "bright_magenta",
    Language.UNKNOWN.value: "dim white",
}

_LANG_ICONS: dict[str, str] = {
    Language.PYTHON.value: "🐍",
    Language.JAVASCRIPT.value: "🟨",
    Language.TYPESCRIPT.value: "🔷",
    Language.JAVA.value: "☕",
    Language.GO.value: "🐹",
    Language.CPP.value: "⚙️ ",
    Language.UNKNOWN.value: "📄",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(value: int, total: int, width: int = 20) -> str:
    """Return a Unicode block-character progress bar."""
    if total == 0:
        return " " * width
    filled = round((value / total) * width)
    return "█" * filled + "░" * (width - filled)


def _pct(value: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{value / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def print_architecture_summary(
    index: RepositoryIndex,
    console: Console,
    output_path: Path | None = None,
) -> None:
    """
    Print the complete architecture summary to *console*.

    Args:
        index:       The fully-built :class:`~ai_swe.indexer.models.RepositoryIndex`.
        console:     A Rich ``Console`` instance.
        output_path: Path where the JSON index was saved (printed in footer).
    """
    stats = index.statistics

    # ── Header ──────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule(f"[bold bright_cyan]  🔭 Repository Intelligence: {index.repo_name}  [/]",
                       style="bright_cyan"))
    console.print()

    # ── Overview panel ──────────────────────────────────────────────────────
    overview_lines = [
        f"[bold]Repo:[/bold]       [bright_cyan]{index.repo_name}[/bright_cyan]",
        f"[bold]Path:[/bold]       [dim]{index.repo_path}[/dim]",
        f"[bold]Total files:[/bold] [green]{stats.total_files:,}[/green]",
        f"[bold]Total LOC:[/bold]   [green]{stats.total_loc:,}[/green] "
        f"[dim]({stats.total_lines:,} raw lines)[/dim]",
        f"[bold]Languages:[/bold]  "
        + ", ".join(
            f"[{_LANG_COLOURS.get(l, 'white')}]{_LANG_ICONS.get(l, '')} {l}[/]"
            for l in stats.language_breakdown
            if stats.language_breakdown[l] > 0
        ),
    ]
    console.print(Panel(
        "\n".join(overview_lines),
        title="[bold bright_white]📊 Overview[/]",
        border_style="bright_cyan",
        padding=(0, 2),
    ))
    console.print()

    # ── Language breakdown table ─────────────────────────────────────────────
    lang_table = Table(
        title="[bold]Language Breakdown[/bold]",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold bright_white",
        show_lines=False,
    )
    lang_table.add_column("Language", style="bold", min_width=14)
    lang_table.add_column("Files", justify="right", style="green")
    lang_table.add_column("Share", justify="right")
    lang_table.add_column("LOC", justify="right", style="yellow")
    lang_table.add_column("LOC Share", justify="right")
    lang_table.add_column("Distribution", min_width=22)

    total_files = stats.total_files or 1
    total_loc = stats.total_loc or 1

    for lang_name, file_count in sorted(
        stats.language_breakdown.items(), key=lambda x: x[1], reverse=True
    ):
        if file_count == 0:
            continue
        loc = stats.language_loc.get(lang_name, 0)
        colour = _LANG_COLOURS.get(lang_name, "white")
        icon = _LANG_ICONS.get(lang_name, "")
        bar = _bar(file_count, total_files)
        lang_table.add_row(
            f"[{colour}]{icon} {lang_name}[/]",
            str(file_count),
            _pct(file_count, total_files),
            f"{loc:,}",
            _pct(loc, total_loc),
            f"[{colour}]{bar}[/{colour}]",
        )

    console.print(lang_table)
    console.print()

    # ── Symbol statistics ────────────────────────────────────────────────────
    sym_table = Table(
        title="[bold]Symbol Statistics[/bold]",
        box=box.SIMPLE_HEAVY,
        border_style="bright_blue",
        header_style="bold bright_white",
    )
    sym_table.add_column("Metric", style="bold")
    sym_table.add_column("Count", justify="right", style="bright_green")

    sym_table.add_row("Total Classes", f"{stats.total_classes:,}")
    sym_table.add_row("Total Functions", f"{stats.total_functions:,}")
    sym_table.add_row("Total Imports", f"{stats.total_imports:,}")

    console.print(sym_table)
    console.print()

    # ── Dependency graph stats ───────────────────────────────────────────────
    dep_table = Table(
        title="[bold]Dependency Graph[/bold]",
        box=box.SIMPLE_HEAVY,
        border_style="magenta",
        header_style="bold bright_white",
    )
    dep_table.add_column("Metric", style="bold")
    dep_table.add_column("Value", justify="right", style="bright_magenta")

    dep_table.add_row("Graph nodes", f"{stats.dependency_graph_nodes:,}")
    dep_table.add_row("Graph edges", f"{stats.dependency_graph_edges:,}")

    console.print(dep_table)
    console.print()

    # ── Most connected files ─────────────────────────────────────────────────
    if stats.most_connected_files:
        console.print(Panel(
            _render_connected_files(stats.most_connected_files, index),
            title="[bold]🔗 Most Connected Files[/bold]",
            border_style="yellow",
            padding=(0, 2),
        ))
        console.print()

    # ── File tree (first 40 files) ───────────────────────────────────────────
    tree_lines = _render_file_tree(index.file_tree, max_entries=40)
    console.print(Panel(
        tree_lines,
        title="[bold]📁 File Tree (top 40)[/bold]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()

    # ── Footer ───────────────────────────────────────────────────────────────
    if output_path:
        console.print(
            f"[dim]💾 Index saved →[/dim] [bold green]{output_path}[/bold green]"
        )
    console.print(Rule(style="dim"))
    console.print()


def _render_connected_files(files: list[str], index: RepositoryIndex) -> str:
    lines: list[str] = []
    for i, f in enumerate(files, 1):
        summary = index.summaries.get(f)
        lang = summary.language.value if summary else "unknown"
        colour = _LANG_COLOURS.get(lang, "white")
        icon = _LANG_ICONS.get(lang, "📄")
        adj_count = len(index.adjacency.get(f, []))
        lines.append(
            f"  [dim]{i:>2}.[/dim] [{colour}]{icon}[/{colour}] "
            f"[bold]{f}[/bold]  [dim]→ {adj_count} dep(s)[/dim]"
        )
    return "\n".join(lines)


def _render_file_tree(file_tree: list[str], max_entries: int = 40) -> str:
    """Render a tree-like view of the file list."""
    lines: list[str] = []
    shown = file_tree[:max_entries]
    remaining = len(file_tree) - len(shown)

    for path_str in shown:
        depth = path_str.count("/")
        indent = "  " * depth
        basename = path_str.rsplit("/", 1)[-1]
        dirpart = path_str.rsplit("/", 1)[0] + "/" if "/" in path_str else ""
        lines.append(f"  {indent}[dim]{dirpart}[/dim][bold white]{basename}[/bold white]")

    if remaining > 0:
        lines.append(f"\n  [dim]… and {remaining} more files[/dim]")

    return "\n".join(lines)
