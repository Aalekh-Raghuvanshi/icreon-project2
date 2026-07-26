# AI SWE Agent

An AI Software Engineer, orchestrated over the **Model Context Protocol
(MCP)**, built with **LangGraph**, **LangChain**, **FastAPI**, and **Pydantic**.

> **Status: foundation milestone.** This repository currently implements
> project scaffolding, MCP connectivity, shared state, and a multi-agent
> routing skeleton -- it does **not** yet plan tasks or write code. See
> [What's implemented](#whats-implemented-vs-whats-a-placeholder) below.

---

## Architecture

```
                         ┌─────────────────────────┐
                         │      CLI (main.py)      │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │      MCPOrchestrator     │  connects to every MCP
                         │   (mcp/client.py)        │  server, exposes call()
                         └────────────┬────────────┘
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
              ┌────────────┐  ┌─────────────┐  ┌───────────────┐
              │  Git MCP   │  │ GitHub MCP  │  │ Filesystem MCP │
              │  server    │  │  server     │  │    server      │
              └────────────┘  └─────────────┘  └───────────────┘

                 ┌───────────────────────────────────────┐
                 │      Agent orchestrator (LangGraph)    │
                 │        (orchestrator/graph.py)         │
                 │                                         │
                 │  Planner -> Coder -> Reviewer -> Executor │
                 │  routed via shared `AgentState`          │
                 └───────────────────────────────────────┘
```

Two distinct "orchestrators" exist on purpose, and are named to avoid
confusion:

* **`MCPOrchestrator`** (`src/ai_swe/mcp/client.py`) manages connections to
  MCP *servers* (Git, GitHub, Filesystem) and exposes a uniform
  `call(server, tool, arguments)` interface.
* **The agent graph** (`src/ai_swe/orchestrator/graph.py`) is a LangGraph
  `StateGraph` that routes a task between *agents* (Planner, Coder, Reviewer,
  Executor), using the `MCPOrchestrator` as a shared dependency.

## Tech stack

| Concern                | Choice                                            |
|-------------------------|----------------------------------------------------|
| Language                | Python 3.12                                        |
| Web framework            | FastAPI (reserved for a future HTTP API)            |
| Agent orchestration      | LangGraph + LangChain                               |
| Data validation          | Pydantic v2 / pydantic-settings                     |
| Protocol                | Model Context Protocol (`mcp` Python SDK)            |
| Local git operations     | GitPython (available; MCP is used for all agent-driven git ops) |
| Dependency management    | `uv`                                                |
| Containerization         | Docker + docker-compose                             |

## Project layout

```
ai-swe-agent/
├── main.py                       # `python main.py` entry point shim
├── pyproject.toml                # dependencies, tool config
├── uv.lock                       # locked dependency versions
├── .env.example                  # documented configuration template
├── Dockerfile / docker-compose.yml
├── src/ai_swe/
│   ├── config.py                 # Settings (pydantic-settings)
│   ├── logging_config.py         # logging setup (console + JSON)
│   ├── state.py                  # shared AgentState + related Pydantic models
│   ├── mcp/
│   │   ├── client.py             # MCPConnection + MCPOrchestrator
│   │   ├── factory.py            # builds an orchestrator from Settings
│   │   ├── git_tools.py          # clone_repository(), git_status()
│   │   ├── filesystem_tools.py   # list_repository_files()
│   │   └── github_tools.py       # search_repositories(), get_file_contents()
│   ├── agents/
│   │   ├── base.py               # BaseAgent interface
│   │   ├── planner.py            # Planner agent (placeholder)
│   │   ├── coder.py               # Coder agent (placeholder)
│   │   ├── reviewer.py            # Reviewer agent (placeholder)
│   │   └── executor.py            # Execution agent (placeholder)
│   ├── orchestrator/
│   │   └── graph.py               # LangGraph routing between agents
│   └── cli/
│       └── main.py                # Typer CLI: prompt -> clone -> list -> report
└── tests/
    ├── test_state.py
    ├── test_filesystem_tools.py
    └── test_orchestrator_graph.py
```

## Setup

### 1. Prerequisites

* Python 3.12+
* [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
* Node.js 18+ and `npx` (the Git, GitHub, and Filesystem MCP servers used here
  are npm packages, launched as subprocesses via `npx`)
* `git`

### 2. Install dependencies

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

At minimum, set `GITHUB_PERSONAL_ACCESS_TOKEN` if you want the GitHub MCP
server to make real API calls (repo search, file contents, etc.) --
cloning/listing files works even without it.

### 4. Run the CLI

```bash
python main.py
# Enter a GitHub repository URL to clone, ...
```

or non-interactively:

```bash
python main.py --repo-url https://github.com/octocat/Hello-World.git
```

This will:
1. Connect to the Git, GitHub, and Filesystem MCP servers and report how many
   tools each exposes (a live connectivity check).
2. Clone the given repository via the Git MCP server's `git_clone` tool.
3. Recursively list every file/directory in the clone via the Filesystem MCP
   server's `directory_tree` tool.
4. Print a success summary.

### 5. Run tests

```bash
python -m pytest -v
```

### 6. Docker

```bash
docker compose build
docker compose run --rm ai-swe-agent
```

## MCP servers used

| Server       | Package                                        | Notes |
|--------------|--------------------------------------------------|-------|
| Git          | `@cyanheads/git-mcp-server` (npm)                 | The *official* `mcp-server-git` (from `modelcontextprotocol/servers`) deliberately has **no clone tool** -- it only operates on an existing checkout. This community server exposes `git_clone` alongside 27 other git operations, which is what `clone_repository()` calls. |
| GitHub       | `@modelcontextprotocol/server-github` (npm)        | Wraps the GitHub REST API; requires `GITHUB_PERSONAL_ACCESS_TOKEN`. |
| Filesystem   | `@modelcontextprotocol/server-filesystem` (npm)    | Sandboxed to a single allowed directory (the agent's `WORKDIR`); `list_repository_files()` uses its `directory_tree` tool. |

All three are launched over **stdio** (as subprocesses), the standard local
transport for MCP. Commands/args are fully configurable via environment
variables (see `.env.example`) -- e.g. to pin a version, or point at a
locally built server binary instead of resolving one via `npx` each run.

### A note on `npx` in restricted sandboxes

In some sandboxed/CI environments, `npx <package>` can hang because of how it
proxies stdio through an extra child process. If you hit this, resolve the
package once (`npx -y <package> --help` or `npm pack`) and point
`GIT_MCP_COMMAND` / `FILESYSTEM_MCP_COMMAND` / `GITHUB_MCP_COMMAND` directly
at `node /path/to/resolved/dist/index.js` instead -- see the commented-out
overrides in `.env.example`. This was necessary to validate this project in
its build sandbox; a normal developer machine or CI runner should not need it.

## What's implemented vs. what's a placeholder

**Implemented and live-verified today:**
- Connecting to all three MCP servers and listing their tools.
- `clone_repository()` -- verified by actually cloning a public GitHub repo.
- `list_repository_files()` -- verified against the cloned repo's real file tree.
- The shared `AgentState` Pydantic model and its (de)serialization.
- The LangGraph agent routing graph -- verified to route a task through
  Planner -> Coder -> Reviewer -> Executor to completion.

**Deliberately NOT implemented yet** (next milestones):
- Real planning (Planner currently just advances `state.status`).
- Real code generation / patch creation (Coder is a placeholder).
- Real patch review (Reviewer is a placeholder).
- Real patch application / test execution (Executor is a placeholder).

Each placeholder agent lives in its own file under `src/ai_swe/agents/` with
a docstring describing exactly what it will do once implemented.

## Configuration reference

See `.env.example` for the full list of environment variables. All
configuration is read through `ai_swe.config.get_settings()` -- no module
reads `os.environ` directly, which keeps configuration centralized and easy
to override in tests.

## License

MIT
