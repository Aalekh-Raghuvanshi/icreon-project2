# AI SWE Agent

An AI Software Engineer, orchestrated over the **Model Context Protocol
(MCP)**, built with **LangGraph**, **LangChain**, **FastAPI**, and **Pydantic**.

> **Status: Day 7.** Planner, Coder, Executor, Reviewer, and Publisher are
> all implemented -- the agent can plan a task, write patches, run the test
> suite in a sandbox, auto-fix failures, gate on lint/type-check, and open a
> pull request. See
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
                 │  Planner -> Coder -> Executor -> Reviewer │
                 │  (loops Reviewer -> Coder on failure)     │
                 │  routed via shared `AgentState`          │
                 └───────────────────┬───────────────────┘
                                     │ state.status == DONE, opt-in (--open-pr)
                                     ▼
                 ┌───────────────────────────────────────┐
                 │        Publisher (agents/publisher.py) │
                 │  CI gate (ruff + mypy) -> branch ->     │
                 │  commit -> push -> open pull request     │
                 └───────────────────────────────────────┘
```

Two distinct "orchestrators" exist on purpose, and are named to avoid
confusion:

* **`MCPOrchestrator`** (`src/ai_swe/mcp/client.py`) manages connections to
  MCP *servers* (Git, GitHub, Filesystem) and exposes a uniform
  `call(server, tool, arguments)` interface.
* **The agent graph** (`src/ai_swe/orchestrator/graph.py`) is a LangGraph
  `StateGraph` that routes a task between *agents* (Planner, Coder, Executor,
  Reviewer), using the `MCPOrchestrator` as a shared dependency.

The Publisher is deliberately **not** a node in that graph -- opening a pull
request is a significant, externally-visible side effect, so it only runs
when a caller explicitly opts in (`ai-swe run --open-pr`), after the graph
has already reached `DONE`.

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
│   │   ├── git_tools.py          # clone_repository(), git_status(), create_branch(),
│   │   │                         # commit_all(), push_branch()
│   │   ├── filesystem_tools.py   # list_repository_files()
│   │   └── github_tools.py       # search_repositories(), get_file_contents(),
│   │                              # open_pull_request(), build_pr_body()
│   ├── execution/
│   │   ├── sandbox.py             # Docker/local command sandbox
│   │   ├── test_runner.py         # test-framework auto-detection + execution
│   │   └── ci.py                  # CI gate: run_ci_checks() (ruff + mypy)
│   ├── agents/
│   │   ├── base.py               # BaseAgent interface
│   │   ├── planner.py            # Planner agent (LLM-driven implementation plan)
│   │   ├── coder.py               # Coder agent (LLM-driven patch generation)
│   │   ├── executor.py            # Execution agent (runs tests in a Sandbox)
│   │   ├── reviewer.py            # Reviewer agent (triages failures, auto-fix loop)
│   │   └── publisher.py           # Publisher agent (CI gate -> branch -> commit ->
│   │                              # push -> open PR; opt-in, not in the graph)
│   ├── orchestrator/
│   │   └── graph.py               # LangGraph routing between agents
│   └── cli/
│       └── main.py                # Typer CLI: run / plan / repo analyze / clone
└── tests/
    ├── test_state.py
    ├── test_filesystem_tools.py
    ├── test_orchestrator_graph.py
    ├── test_ci.py
    └── test_publisher.py
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

#### `ai-swe run` -- the full pipeline

```bash
ai-swe run "Add input validation to the signup form" /path/to/repo
```

Runs the complete agent pipeline (Planner -> Coder -> Executor -> Reviewer,
with the Reviewer able to loop back to the Coder for bounded auto-fix
attempts) against an already-checked-out local repository, and renders the
live agent log and test results with `rich` as it goes.

To also open a pull request once the pipeline reaches `DONE`, add `--open-pr`
and `--repo-url` (needed so the Publisher knows which GitHub repo to open the
PR against):

```bash
ai-swe run "Add input validation to the signup form" /path/to/repo \
  --open-pr --repo-url https://github.com/me/myrepo --base-branch main
```

With `--open-pr`, a successful run is followed by:
1. A CI gate (`ruff check .` and `mypy .`, run inside a `Sandbox`) -- if
   either check fails, the Publisher stops here, records the failure on
   `state.ci_result` / `state.error`, and does **not** open a PR.
2. A feature branch, committing every working-tree change, and pushing it.
3. A pull request, with an auto-generated body built from the plan summary,
   the files changed (`state.patches`), and the test results
   (`state.test_results`).

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
| Git          | `@cyanheads/git-mcp-server` (npm)                 | The *official* `mcp-server-git` (from `modelcontextprotocol/servers`) deliberately has **no clone tool** -- it only operates on an existing checkout. This community server exposes `git_clone` alongside 27 other git operations. `clone_repository()` calls `git_clone`; `create_branch()` calls `git_checkout` (`createBranch: true`); `commit_all()` calls `git_add` + `git_commit`; `push_branch()` calls `git_push` (`setUpstream: true`). |
| GitHub       | `@modelcontextprotocol/server-github` (npm)        | Wraps the GitHub REST API; requires `GITHUB_PERSONAL_ACCESS_TOKEN`. `open_pull_request()` calls its `create_pull_request` tool. |
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

**Implemented (Day 1-7):**
- Connecting to all three MCP servers and listing their tools.
- `clone_repository()` -- verified by actually cloning a public GitHub repo.
- `list_repository_files()` -- verified against the cloned repo's real file tree.
- The shared `AgentState` Pydantic model and its (de)serialization.
- The LangGraph agent routing graph, Planner -> Coder -> Executor -> Reviewer,
  with the Reviewer looping back to the Coder for bounded auto-fix attempts.
- **Planner** -- LLM-driven implementation planning (`agents/planner.py`).
- **Coder** -- LLM-driven patch generation via `EditEngine` (`agents/coder.py`).
- **Executor** -- auto-detects and runs the project's test suite in a
  `Sandbox` (Docker, falling back to a local subprocess) (`agents/executor.py`).
- **Reviewer** -- triages failing test output into structured `ErrorReport`s
  and drives the Coder auto-fix loop (`agents/reviewer.py`).
- **Publisher** -- runs a CI gate (`ruff check` + `mypy`, `execution/ci.py`)
  and, if it passes, creates a branch, commits, pushes, and opens a pull
  request with an auto-generated body (`agents/publisher.py`). Opt-in via
  `ai-swe run --open-pr`; not part of the default graph.

**Deliberately NOT implemented yet** (next milestones):
- A documentation-lookup MCP tool the Coder can call when unsure of a
  library's API (no readily-available docs MCP server was wired up for Day
  7; the Coder still relies on repository context + its own knowledge).
- A FastAPI HTTP surface (FastAPI is a dependency, reserved for a future
  milestone; today's interface is the CLI).

## Configuration reference

See `.env.example` for the full list of environment variables. All
configuration is read through `ai_swe.config.get_settings()` -- no module
reads `os.environ` directly, which keeps configuration centralized and easy
to override in tests.

## License

MIT
