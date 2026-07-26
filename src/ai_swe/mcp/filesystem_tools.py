"""
Filesystem operations, implemented as thin, typed wrappers around the
Filesystem MCP server's tools.

As with `git_tools.py`, every filesystem read here goes through the MCP
server rather than calling `os`/`pathlib` directly. The Filesystem MCP
server is launched scoped to a single allowed directory (see
`mcp.factory.build_orchestrator`), which is what actually enforces the
sandbox boundary -- this module just talks to it.
"""

from __future__ import annotations

from typing import Any

from ai_swe.logging_config import get_logger
from ai_swe.mcp.client import MCPOrchestrator
from ai_swe.mcp.factory import FILESYSTEM_SERVER
from ai_swe.state import FileRecord

logger = get_logger(__name__)


def _flatten_tree(node: dict[str, Any], parent_path: str, out: list[FileRecord]) -> None:
    """
    Recursively flatten the nested JSON structure returned by the
    Filesystem MCP server's `directory_tree` tool into a flat list of
    `FileRecord`s with paths relative to the repository root.

    Each node looks like:
        {"name": "src", "type": "directory", "children": [...]}
        {"name": "main.py", "type": "file"}
    """
    path = f"{parent_path}/{node['name']}" if parent_path else str(node["name"])
    is_dir = node.get("type") == "directory"
    out.append(FileRecord(path=path, is_dir=is_dir))
    children = node.get("children") or []
    for child in children:
        _flatten_tree(child, path, out)


async def list_repository_files(
    orchestrator: MCPOrchestrator,
    repo_path: str,
    *,
    exclude_patterns: list[str] | None = None,
) -> list[FileRecord]:
    """
    Recursively list every file and directory under `repo_path` via the
    Filesystem MCP server's `directory_tree` tool.

    Args:
        orchestrator: A connected `MCPOrchestrator` with the Filesystem server registered.
        repo_path: Root directory to list (must be inside the server's allowed directory).
        exclude_patterns: Optional glob patterns to exclude (e.g. `[".git", "node_modules"]`).

    Returns:
        A flat list of `FileRecord`s (files and directories) with paths
        relative to `repo_path`.
    """
    logger.info("Listing repository files under '%s'...", repo_path)
    arguments: dict[str, object] = {"path": repo_path}
    if exclude_patterns:
        arguments["excludePatterns"] = exclude_patterns

    tree = await orchestrator.call(FILESYSTEM_SERVER, "directory_tree", arguments)

    records: list[FileRecord] = []
    for node in tree:
        _flatten_tree(node, "", records)

    logger.info("Found %d files/directories under '%s'.", len(records), repo_path)
    return records
