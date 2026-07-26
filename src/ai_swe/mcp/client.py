"""
MCP connection management.

This module contains two layers:

  * `MCPConnection` -- wraps a single MCP server subprocess (stdio transport)
    behind an `AsyncExitStack`, giving us a clean `connect()` / `close()`
    lifecycle and a `call_tool()` helper that returns plain Python data
    instead of raw MCP content blocks.

  * `MCPOrchestrator` -- holds one `MCPConnection` per named server (git,
    github, filesystem, ...), connects to all of them, and exposes a single
    `call(server, tool, arguments)` entry point plus discovery helpers. This
    is the "connect to every MCP server" orchestrator referenced in the
    project requirements; the *agent* orchestrator (LangGraph) is a separate,
    higher-level concept built on top of this one.

All MCP servers we use here communicate over stdio (the server is launched
as a subprocess and we talk to it via its stdin/stdout), which is the
standard local-development transport for MCP.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ai_swe.config import MCPServerSettings
from ai_swe.logging_config import get_logger

logger = get_logger(__name__)


class MCPToolError(RuntimeError):
    """Raised when an MCP tool call fails or returns an error result."""


class MCPConnection:
    """
    A single, long-lived connection to one MCP server over stdio.

    Usage:
        conn = MCPConnection("git", settings)
        await conn.connect()
        result = await conn.call_tool("git_clone", {"url": ..., "path": ...})
        await conn.close()

    Or, preferably, use it as an async context manager so cleanup always
    happens even if an exception is raised:

        async with MCPConnection("git", settings) as conn:
            ...
    """

    def __init__(self, name: str, settings: MCPServerSettings) -> None:
        self.name = name
        self._settings = settings
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        """Launch the server subprocess and perform the MCP initialize handshake."""
        logger.info("Connecting to MCP server '%s' (%s)...", self.name, self._settings.command)
        params = StdioServerParameters(
            command=self._settings.command,
            args=self._settings.args,
            env=self._settings.env or None,
        )
        read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
        session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        self._session = session
        logger.info("Connected to MCP server '%s'.", self.name)

    async def close(self) -> None:
        """Terminate the server subprocess and release all resources."""
        await self._exit_stack.aclose()
        self._session = None
        logger.info("Closed MCP server '%s'.", self.name)

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected. Call connect() first.")
        return self._session

    async def list_tool_names(self) -> list[str]:
        """Return the names of every tool this server exposes."""
        result = await self.session.list_tools()
        return [tool.name for tool in result.tools]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Call a tool on this MCP server and return its result as plain Python data.

        MCP tool results are a list of "content blocks" (usually a single text
        block containing JSON or plain text). We unwrap that here so callers
        don't need to know about MCP's wire format -- they just get a dict,
        list, or string back.
        """
        logger.debug("Calling tool '%s.%s' with arguments=%s", self.name, tool_name, arguments)
        result = await self.session.call_tool(tool_name, arguments)

        texts: list[str] = [block.text for block in result.content if hasattr(block, "text")]
        combined = "\n".join(texts)

        if getattr(result, "isError", False):
            raise MCPToolError(f"Tool '{tool_name}' on server '{self.name}' failed: {combined}")

        # Most MCP servers return JSON-encoded text; fall back to raw text
        # if it doesn't parse (e.g. plain-text output from `git_status`).
        try:
            return json.loads(combined)
        except json.JSONDecodeError:
            return combined


class MCPOrchestrator:
    """
    Owns connections to every MCP server the agent needs and exposes a single,
    uniform interface for calling tools on any of them.

    This is intentionally simple: it is a thin registry + lifecycle manager,
    not a place for business logic. Business logic (e.g. "what does cloning a
    repo actually involve") lives in `mcp/git_tools.py`,
    `mcp/filesystem_tools.py`, etc., which call into this orchestrator.
    """

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}

    def register(self, name: str, settings: MCPServerSettings) -> None:
        """Register a server configuration under `name` (does not connect yet)."""
        if name in self._connections:
            raise ValueError(f"An MCP server named '{name}' is already registered.")
        self._connections[name] = MCPConnection(name, settings)

    async def connect_all(self) -> None:
        """Connect to every registered MCP server."""
        for connection in self._connections.values():
            await connection.connect()

    async def close_all(self) -> None:
        """Disconnect from every registered MCP server, in reverse order."""
        for connection in reversed(list(self._connections.values())):
            await connection.close()

    def get(self, name: str) -> MCPConnection:
        """Fetch a registered connection by name (must already be connected)."""
        if name not in self._connections:
            raise KeyError(
                f"No MCP server registered under '{name}'. "
                f"Registered servers: {list(self._connections)}"
            )
        return self._connections[name]

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        """Convenience helper: `orchestrator.call('git', 'git_clone', {...})`."""
        return await self.get(server).call_tool(tool, arguments)

    async def health_check(self) -> dict[str, list[str] | str]:
        """
        Verify every registered server responds to `list_tools`.

        Returns a dict mapping server name -> list of tool names (on success)
        or an error string (on failure). Used by the CLI/tests to confirm all
        MCP servers are reachable before doing real work.
        """
        report: dict[str, list[str] | str] = {}
        for name, connection in self._connections.items():
            try:
                report[name] = await connection.list_tool_names()
            except Exception as exc:  # noqa: BLE001 - we want to report *any* failure
                report[name] = f"ERROR: {exc}"
        return report

    async def __aenter__(self) -> Self:
        await self.connect_all()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_all()
