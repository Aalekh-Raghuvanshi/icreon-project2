"""
Centralized configuration management for the AI SWE Agent.

All runtime configuration is loaded from environment variables (optionally via a
`.env` file) using `pydantic-settings`. This gives us:
  * A single source of truth for configuration.
  * Automatic type validation/coercion (fail fast on bad config).
  * Easy overriding in Docker / CI via plain environment variables.

Nothing else in the codebase should read `os.environ` directly -- always go
through `get_settings()` so configuration stays centralized and testable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerSettings(BaseSettings):
    """
    Launch configuration for a single MCP server.

    Every MCP server we talk to is a subprocess launched over stdio, so all we
    need is the command, its arguments, and any extra environment variables it
    requires (e.g. API tokens). Keeping this as its own model means each
    server's settings can be overridden independently via env vars, e.g.:

        GIT_MCP_COMMAND=npx
        GIT_MCP_ARGS=["-y", "@cyanheads/git-mcp-server@2.15.1"]
    """

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class Settings(BaseSettings):
    """
    Application-wide settings, populated from environment variables / `.env`.

    Field names map to env vars of the same name (case-insensitive), e.g.
    `workdir` <- `WORKDIR`, `log_level` <- `LOG_LEVEL`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "ai-swe-agent"
    environment: str = "development"  # development | staging | production
    log_level: str = "INFO"
    log_json: bool = False  # emit structured JSON logs (useful in production)

    # Root directory under which repositories are cloned / worked on.
    workdir: Path = Path("./workspace")

    # --- LLM provider --------------------------------------------------------
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-6"

    # --- GitHub --------------------------------------------------------------
    github_personal_access_token: str | None = Field(
        default=None, description="Used by the GitHub MCP server for API calls."
    )

    # --- MCP: Git server -------------------------------------------------------
    # Default assumes `@cyanheads/git-mcp-server`, a community MCP server that
    # exposes a full git toolset *including* `git_clone` (the reference
    # `mcp-server-git` from modelcontextprotocol/servers deliberately omits
    # clone -- it only operates on an already-checked-out repository).
    git_mcp_command: str = "npx"
    git_mcp_args: list[str] = Field(
        default_factory=lambda: ["-y", "@cyanheads/git-mcp-server@2.15.1"]
    )
    git_mcp_username: str = "ai-swe-agent"
    git_mcp_email: str = "ai-swe-agent@example.com"

    # --- MCP: GitHub server ------------------------------------------------
    github_mcp_command: str = "npx"
    github_mcp_args: list[str] = Field(
        default_factory=lambda: ["-y", "@modelcontextprotocol/server-github"]
    )

    # --- MCP: Filesystem server ----------------------------------------------
    filesystem_mcp_command: str = "npx"
    filesystem_mcp_args: list[str] = Field(
        default_factory=lambda: ["-y", "@modelcontextprotocol/server-filesystem"]
    )

    def git_mcp_settings(self, allowed_dir: str | Path) -> MCPServerSettings:
        """Build launch settings for the Git MCP server, scoped to `allowed_dir`."""
        return MCPServerSettings(
            command=self.git_mcp_command,
            args=self.git_mcp_args,
            env={
                "MCP_TRANSPORT_TYPE": "stdio",
                "MCP_LOG_LEVEL": "error",
                "GIT_BASE_DIR": str(allowed_dir),
                "GIT_USERNAME": self.git_mcp_username,
                "GIT_EMAIL": self.git_mcp_email,
            },
        )

    def github_mcp_settings(self) -> MCPServerSettings:
        """Build launch settings for the GitHub MCP server."""
        env = {}
        if self.github_personal_access_token:
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = self.github_personal_access_token
        return MCPServerSettings(command=self.github_mcp_command, args=self.github_mcp_args, env=env)

    def filesystem_mcp_settings(self, allowed_dir: str | Path) -> MCPServerSettings:
        """Build launch settings for the Filesystem MCP server, scoped to `allowed_dir`."""
        return MCPServerSettings(
            command=self.filesystem_mcp_command,
            args=[*self.filesystem_mcp_args, str(allowed_dir)],
            env={},
        )


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached, process-wide `Settings` instance.

    `lru_cache` ensures we parse the environment exactly once per process,
    while still allowing tests to override it via `get_settings.cache_clear()`.
    """
    return Settings()
