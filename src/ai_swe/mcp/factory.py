"""
Factory for building a fully-configured `MCPOrchestrator`.

This is the single place that knows *which* MCP servers the agent depends on
(Git, GitHub, Filesystem) and how to translate application `Settings` into
concrete server launch configs. Keeping this separate from `MCPOrchestrator`
itself keeps that class generic and reusable.
"""

from __future__ import annotations

from pathlib import Path

from ai_swe.config import Settings
from ai_swe.mcp.client import MCPOrchestrator

# Canonical server names, used as keys everywhere (config, CLI, tests).
GIT_SERVER = "git"
GITHUB_SERVER = "github"
FILESYSTEM_SERVER = "filesystem"


def build_orchestrator(settings: Settings, workdir: str | Path) -> MCPOrchestrator:
    """
    Construct an `MCPOrchestrator` with the Git, GitHub, and Filesystem MCP
    servers registered (but not yet connected -- call `connect_all()` or use
    it as an async context manager).

    Args:
        settings: Application settings (see `ai_swe.config.Settings`).
        workdir: Root directory the Git and Filesystem servers are allowed to
            operate in. Scoping servers to a specific directory is an
            important security boundary -- it prevents a misbehaving or
            compromised MCP server (or a prompt-injected tool call) from
            touching files outside the agent's workspace.
    """
    # Resolve to an absolute path: the Git MCP server's GIT_BASE_DIR
    # validation requires it, and it's simply safer for a sandbox boundary
    # to be unambiguous regardless of the process's current working directory.
    workdir = Path(workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    orchestrator = MCPOrchestrator()
    orchestrator.register(GIT_SERVER, settings.git_mcp_settings(workdir))
    orchestrator.register(GITHUB_SERVER, settings.github_mcp_settings())
    orchestrator.register(FILESYSTEM_SERVER, settings.filesystem_mcp_settings(workdir))
    return orchestrator
