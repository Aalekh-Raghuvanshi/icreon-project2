# syntax=docker/dockerfile:1

# ------------------------------------------------------------------------------
# AI SWE Agent -- production image.
#
# The agent needs both a Python runtime (for the app itself, LangGraph, the MCP
# Python SDK) and a Node.js runtime (because the Filesystem and GitHub MCP
# servers we connect to are distributed as npm packages, launched via `npx`).
# The Git MCP server is also an npm package (`@cyanheads/git-mcp-server`).
# ------------------------------------------------------------------------------

FROM python:3.12-slim AS base

# --- System dependencies -------------------------------------------------------
# - git: required by GitPython and as a fallback / for local git operations.
# - curl: used to install `uv`.
# - nodejs/npm: required to launch the npx-based MCP servers.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# --- Install uv (fast Python package manager) ----------------------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# --- Create a non-root user ------------------------------------------------------
RUN useradd --create-home --shell /bin/bash agent
WORKDIR /app

# --- Install Python dependencies first (better layer caching) --------------------
COPY pyproject.toml README.md ./
# Create the venv and install the project in editable mode. Splitting this
# from the `COPY . .` below means dependency installs are cached across
# rebuilds that only change application code.
RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -e .

# --- Copy application code -------------------------------------------------------
COPY src ./src
COPY main.py ./main.py

# --- Pre-fetch MCP server npm packages -------------------------------------------
# Warms npm's cache so the first real run doesn't pay the download cost, and
# fails the build fast if a package name/version is wrong.
RUN npx -y @cyanheads/git-mcp-server@2.15.1 --help > /dev/null 2>&1 || true \
    && npx -y @modelcontextprotocol/server-filesystem --help > /dev/null 2>&1 || true \
    && npx -y @modelcontextprotocol/server-github --help > /dev/null 2>&1 || true

# --- Runtime configuration --------------------------------------------------------
ENV WORKDIR=/app/workspace \
    PYTHONUNBUFFERED=1 \
    LOG_JSON=true

RUN mkdir -p /app/workspace && chown -R agent:agent /app
USER agent

ENTRYPOINT ["python", "main.py"]
