"""
Dependency graph builder.

Constructs a directed graph (using NetworkX) where:
  - **Nodes** are relative file paths within the repository.
  - **Edges** represent import / dependency relationships between files.

External packages (e.g. ``fastapi``, ``os``) that cannot be resolved to a
local file are kept as nodes too but are marked as ``external=True``.

Usage::

    from ai_swe.indexer.graph import build_dependency_graph

    nx_graph, edges = build_dependency_graph(summaries, repo_root)
    print(nx_graph.number_of_nodes(), nx_graph.number_of_edges())
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import networkx as nx

from ai_swe.indexer.models import DependencyEdge, FileSummary, Language, SymbolKind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-name → file resolution helpers
# ---------------------------------------------------------------------------


def _build_file_index(summaries: dict[str, FileSummary]) -> dict[str, str]:
    """
    Build a lookup: possible module import name → relative file path.

    Works for Python (``ai_swe.config`` → ``src/ai_swe/config.py``) by
    building aliases from each file's relative path.
    """
    index: dict[str, str] = {}
    for rel_path in summaries:
        p = PurePosixPath(rel_path)
        # Drop extension
        no_ext = str(p.with_suffix(""))
        # Slash → dot (Python style)
        dot_style = no_ext.replace("/", ".")
        index[rel_path] = rel_path  # identity
        index[no_ext] = rel_path  # "src/ai_swe/config"
        index[dot_style] = rel_path  # "src.ai_swe.config"
        # Also try without leading "src/" prefix
        if no_ext.startswith("src/"):
            trimmed = no_ext[4:]
            index[trimmed] = rel_path
            index[trimmed.replace("/", ".")] = rel_path
        # Basename only (for simple relative imports)
        index[p.stem] = rel_path
    return index


def _resolve_import(
    dep_name: str,
    file_index: dict[str, str],
    importing_file: str,
    language: Language,
) -> str | None:
    """
    Try to map *dep_name* (a raw import string) to a relative file path.

    Returns the relative path if found, else None (external dep).
    """
    candidates = [
        dep_name,
        dep_name.replace(".", "/"),
        dep_name.lstrip("."),
    ]

    # Relative import in Python: "from .utils import X" → dep_name starts with "."
    if dep_name.startswith("."):
        parent = str(PurePosixPath(importing_file).parent)
        rel = dep_name.lstrip(".")
        if parent != ".":
            candidates.append(f"{parent}/{rel}")
            candidates.append(f"{parent}/{rel.replace('.', '/')}")

    for candidate in candidates:
        if candidate in file_index:
            return file_index[candidate]

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_dependency_graph(
    summaries: dict[str, FileSummary],
    repo_root: Path | None = None,
) -> tuple[nx.DiGraph, list[DependencyEdge]]:
    """
    Build a directed dependency graph from file summaries.

    Args:
        summaries:  Per-file summaries keyed by relative path.
        repo_root:  Repository root (unused currently, reserved for future
                    relative-import resolution improvements).

    Returns:
        A tuple ``(nx.DiGraph, list[DependencyEdge])``.

        In the DiGraph:
          - Each node has attribute ``external`` (bool).
          - Each edge has attribute ``kind`` (str).
    """
    G: nx.DiGraph = nx.DiGraph()

    # Seed all known files as nodes
    for rel_path in summaries:
        G.add_node(rel_path, external=False)

    file_index = _build_file_index(summaries)
    edges: list[DependencyEdge] = []

    for rel_path, summary in summaries.items():
        for sym in summary.symbols:
            if sym.kind not in (SymbolKind.IMPORT, SymbolKind.EXPORT):
                continue

            dep_name = sym.name.strip()
            if not dep_name:
                continue

            target = _resolve_import(dep_name, file_index, rel_path, summary.language)

            if target is None:
                # External dependency — add as a node if not present
                if dep_name not in G:
                    G.add_node(dep_name, external=True)
                target = dep_name

            kind = "import" if sym.kind == SymbolKind.IMPORT else "export"

            # Avoid self-loops
            if target == rel_path:
                continue

            # Avoid duplicate edges (keep first occurrence)
            if not G.has_edge(rel_path, target):
                G.add_edge(rel_path, target, kind=kind)
                edges.append(
                    DependencyEdge(source_file=rel_path, target=target, kind=kind)
                )

    logger.debug(
        "Dependency graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
    )
    return G, edges


def most_connected_files(G: nx.DiGraph, top_n: int = 10) -> list[str]:
    """Return the *top_n* file nodes ranked by total degree (in + out)."""
    internal_nodes = [n for n, d in G.nodes(data=True) if not d.get("external", True)]
    if not internal_nodes:
        return []
    by_degree = sorted(
        internal_nodes,
        key=lambda n: G.in_degree(n) + G.out_degree(n),
        reverse=True,
    )
    return by_degree[:top_n]


def build_adjacency(edges: list[DependencyEdge]) -> dict[str, list[str]]:
    """Build a plain dict adjacency list from the edge list."""
    adj: dict[str, list[str]] = {}
    for edge in edges:
        adj.setdefault(edge.source_file, []).append(edge.target)
    return adj
