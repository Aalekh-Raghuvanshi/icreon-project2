# AI SWE Agent

> **A production-ready AI Software Engineer** that plans, generates code, runs tests, auto-fixes failures, and opens pull requests — orchestrated through the **Model Context Protocol (MCP)** with a **full-stack web UI**.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61dafb.svg)](https://react.dev)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-purple.svg)](https://langchain-ai.github.io/langgraph/)

---

## Overview

AI SWE Agent is a **multi-agent software engineering system** similar to GitHub Copilot Workspace, Devin, or OpenHands.  Given a natural-language task and a repository, it autonomously:

1. **Analyses** the codebase (AST parsing, dependency graph, semantic summaries)
2. **Plans** a step-by-step implementation strategy using an LLM
3. **Generates** code patches via the `EditEngine`
4. **Executes** the test suite in a sandboxed environment (Docker or local subprocess)
5. **Reviews** failures, extracts structured error reports, and loops back to fix them
6. **Gates** with CI (ruff + mypy) before touching GitHub
7. **Opens** a pull request with an auto-generated body

The entire workflow is **visualised in real time** through a modern React web interface connected via REST and WebSocket.

---

## Features

| Category | Feature |
|---|---|
| **Agents** | Planner, Coder, Executor, Reviewer, Publisher |
| **Orchestration** | LangGraph state graph with conditional routing and auto-fix loop |
| **MCP Integration** | Git, GitHub, Filesystem MCP servers |
| **Production Guardrails** | Timeouts, rate limiting, cost limits, large-repo guards, retry with exponential backoff |
| **Structured Logging** | Per-session JSONL interaction logs, queryable via API |
| **REST API** | Full CRUD for sessions, repo operations, diffs, test results, PRs |
| **WebSocket** | Real-time pipeline event streaming (progress, agent status, logs) |
| **Web UI** | 8-page React/TypeScript/Tailwind/shadcn/React Flow SPA |
| **React Flow** | Animated multi-agent workflow visualizer |
| **Code Diff** | Git-style diff viewer with line-level highlighting |
| **Cost Tracking** | Groq token usage + USD cost estimate (live in dashboard) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Browser (React SPA – port 5173)             │
│                                                           │
│  Dashboard  Repository  Task  Workflow  Diff  Tests  PR  │
│              Logs & Reasoning                             │
└────────────────────┬────────────────────────────────────┘
                     │  REST (HTTP/JSON)  │  WebSocket
                     ▼                   ▼
┌─────────────────────────────────────────────────────────┐
│          FastAPI Server  (port 8000)                     │
│                                                           │
│  /api/sessions  /api/repo  /api/health  /ws/{session}   │
│                                                           │
│  SessionStore  EventBus  BackgroundTasks                 │
└──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│         LangGraph Agent Pipeline                         │
│                                                           │
│  PENDING → [Planner] → CODING → [Coder] → EXECUTING    │
│          → [Executor] → REVIEWING → [Reviewer]          │
│          → DONE | CODING (retry) | FAILED               │
│                                                           │
│  On DONE + --open-pr:                                    │
│  → [Publisher] → CI gate → branch → commit → PR         │
└────────────────────┬────────────────────────────────────┘
                     │  stdio (subprocess)
         ┌───────────┼───────────────┐
         ▼           ▼               ▼
    ┌─────────┐ ┌──────────┐ ┌────────────────┐
    │ Git MCP │ │GitHub MCP│ │ Filesystem MCP │
    │ server  │ │  server  │ │    server      │
    └─────────┘ └──────────┘ └────────────────┘
    (npx @cyanheads/ (npx @modelcontextprotocol/
     git-mcp-server)  server-github + server-filesystem)
```

---

## Project Layout

```
ai-swe-agent/
├── main.py                        # Entry-point shim
├── pyproject.toml                 # Dependencies, tool config
├── Dockerfile / docker-compose.yml
│
├── src/ai_swe/
│   ├── config.py                  # Settings (pydantic-settings) + guardrail config
│   ├── state.py                   # AgentState + all Pydantic models
│   ├── guardrails.py              # NEW: Timeouts, rate limiting, cost tracking, retry
│   ├── logging_config.py          # Console + JSON + StructuredInteractionLogger
│   │
│   ├── api/                       # NEW: FastAPI layer
│   │   ├── app.py                 # Application factory
│   │   ├── routes.py              # REST endpoints
│   │   ├── ws.py                  # WebSocket endpoint + EventBus
│   │   ├── models.py              # API Pydantic models
│   │   └── session_store.py       # In-memory + disk session store
│   │
│   ├── agents/
│   │   ├── planner.py             # LLM-driven implementation planning
│   │   ├── coder.py               # LLM-driven patch generation
│   │   ├── executor.py            # Test suite runner (Sandbox)
│   │   ├── reviewer.py            # Failure triage + auto-fix loop
│   │   └── publisher.py           # CI gate → branch → commit → PR
│   │
│   ├── mcp/
│   │   ├── client.py              # MCPOrchestrator
│   │   ├── factory.py             # Build orchestrator from settings
│   │   ├── git_tools.py           # clone, status, branch, commit, push
│   │   ├── filesystem_tools.py    # list_repository_files
│   │   └── github_tools.py        # search, get_file, open_pull_request
│   │
│   ├── execution/
│   │   ├── sandbox.py             # Docker/local command sandbox
│   │   ├── test_runner.py         # Test framework auto-detection + execution
│   │   └── ci.py                  # ruff + mypy CI gate
│   │
│   ├── orchestrator/
│   │   └── graph.py               # LangGraph routing graph
│   │
│   ├── indexer/                   # Codebase analysis (Tree-sitter + NetworkX)
│   └── cli/
│       └── main.py                # Typer CLI: run / plan / repo / serve
│
├── frontend/                      # NEW: React + TypeScript + Vite SPA
│   ├── src/
│   │   ├── api/
│   │   │   ├── client.ts          # Typed REST client
│   │   │   └── ws.ts              # WebSocket client (auto-reconnect)
│   │   ├── store/
│   │   │   └── session.ts         # Zustand global state
│   │   ├── components/
│   │   │   ├── Layout.tsx         # Sidebar + Outlet
│   │   │   ├── Sidebar.tsx        # Navigation + active session status
│   │   │   └── ProgressBar.tsx    # Reusable progress bar
│   │   └── pages/
│   │       ├── Dashboard.tsx      # KPIs + session list + run detail
│   │       ├── Repository.tsx     # Clone + browse + analyze
│   │       ├── Task.tsx           # Task form + live WS log
│   │       ├── Workflow.tsx       # React Flow agent visualizer
│   │       ├── CodeDiff.tsx       # Git-style diff viewer
│   │       ├── TestResults.tsx    # Test suite results + stack traces
│   │       ├── PullRequest.tsx    # PR details + CI gate + GitHub link
│   │       └── Logs.tsx           # Structured interaction log viewer
│   └── package.json
│
└── tests/                         # pytest test suite (174 tests)
```

---

## Setup

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager
- Node.js 18+ and `npm`
- `git`
- Docker (optional — used for sandboxed test execution)

### 1. Install Python dependencies

```bash
uv venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

### 2. Install Frontend dependencies

```bash
cd frontend
npm install
```

### 3. Configure environment

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Groq API key ([console.groq.com](https://console.groq.com)) |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT for PR creation |
| `LLM_MODEL` | Model name (default: `llama-3.3-70b-versatile`) |
| `WORKDIR` | Local directory for cloned repos (default: `./workspace`) |

Optional guardrail overrides:

| Variable | Default | Description |
|---|---|---|
| `AGENT_TIMEOUT_SECONDS` | `300` | Per-agent step timeout |
| `PIPELINE_TIMEOUT_SECONDS` | `1800` | Whole-pipeline timeout |
| `MAX_COST_USD` | `5.0` | Max Groq API spend per run |
| `RATE_LIMIT_RPM` | `30` | LLM requests per minute |
| `MAX_REPO_SIZE_MB` | `500` | Repo size guard |
| `MAX_FIX_ATTEMPTS` | `3` | Reviewer → Coder retry loops |

---

## Usage

### CLI

```bash
# Clone a repository
python main.py --repo-url https://github.com/fastapi/fastapi

# Analyse codebase
ai-swe repo analyze /path/to/repo

# Generate an implementation plan
ai-swe plan "Add rate limiting" /path/to/repo

# Run the full pipeline
ai-swe run "Add JWT authentication" /path/to/repo

# Run and open a PR on success
ai-swe run "Add JWT authentication" /path/to/repo \
  --open-pr --repo-url https://github.com/me/myrepo --base-branch main
```

### Web Interface

Start both services in separate terminals:

```bash
# Terminal 1: Backend
ai-swe serve --reload

# Terminal 2: Frontend
cd frontend && npm run dev
```

Then open **http://localhost:5173**.

---

## API Documentation

Interactive API docs are available at **http://localhost:8000/api/docs** when the server is running.

### Key Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/sessions` | List all pipeline sessions |
| `POST` | `/api/sessions` | Start a new run (returns session_id; async) |
| `GET` | `/api/sessions/{id}` | Full session state |
| `GET` | `/api/sessions/{id}/logs` | Structured interaction logs (JSONL) |
| `GET` | `/api/sessions/{id}/diff` | Code patches / diffs |
| `GET` | `/api/sessions/{id}/test-results` | Test results |
| `GET` | `/api/sessions/{id}/pr` | Pull request metadata |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `POST` | `/api/repo/clone` | Clone a remote repository |
| `GET` | `/api/repo/tree` | Browse repository file tree |
| `GET` | `/api/repo/search` | Search files by name |
| `POST` | `/api/repo/analyze` | Run codebase analysis |
| `WS` | `/ws/{session_id}` | Real-time pipeline event stream |

### WebSocket Event Types

```json
{ "event": "progress",       "progress": 0.4, "status": "coding" }
{ "event": "agent_started",  "agent": "coder", "message": "Generating patches…" }
{ "event": "agent_finished", "agent": "coder", "status": "executing" }
{ "event": "log",            "agent": "coder", "message": "Patch applied to auth.py" }
{ "event": "done",           "status": "done", "data": { "pr_url": "https://…" } }
{ "event": "error",          "message": "Timeout after 300s" }
```

---

## MCP Servers

| Server | Package | Role |
|---|---|---|
| Git | `@cyanheads/git-mcp-server` | `git_clone`, `git_status`, `git_checkout`, `git_add`, `git_commit`, `git_push` |
| GitHub | `@modelcontextprotocol/server-github` | `search_repositories`, `get_file_contents`, `create_pull_request` |
| Filesystem | `@modelcontextprotocol/server-filesystem` | `directory_tree` — sandboxed to the workspace |

All three launch as stdio subprocesses via `npx`.

---

## Production Guardrails

The `ai_swe.guardrails` module provides:

| Guardrail | Implementation |
|---|---|
| **Timeouts** | `asyncio.wait_for` wrapping each agent step and the whole pipeline |
| **Rate Limiting** | Token-bucket limiter (configurable RPM) |
| **Cost Limiting** | Groq pricing: $0.59/M input, $0.79/M output for `llama-3.3-70b-versatile` |
| **Large Repo Guard** | Abort if > `MAX_REPO_SIZE_MB`; warn and sample if > `MAX_FILES_IN_REPO` |
| **Retry** | Exponential backoff with full jitter, up to `MAX_RETRIES` attempts |
| **Graceful Recovery** | `@graceful_recovery` decorator catches exceptions, logs, returns fallback |

---

## Frontend Design

| Technology | Role |
|---|---|
| React 19 + TypeScript | UI framework |
| Vite 8 | Dev server + bundler |
| Tailwind CSS v4 | Utility-first styling |
| CSS custom properties | Dark-mode design tokens |
| React Router v6 | Client-side routing |
| Zustand | Lightweight global state |
| `@xyflow/react` | Multi-agent workflow visualization |
| `lucide-react` | Icon set |

Design system: dark slate/indigo/violet palette, glassmorphism cards, animated status dots, glowing progress bars, JetBrains Mono for code.

---

## Running Tests

```bash
# Backend
python -m pytest -v                  # 174 tests

# Frontend (type check)
cd frontend && npx tsc -b            # Zero errors

# Frontend (build)
cd frontend && npm run build
```

---

## Docker

```bash
docker compose build
docker compose run --rm ai-swe-agent
```

---

## Cost Estimation

Token costs use Groq's public pricing (as of 2025 Q1):

| Model | Input | Output |
|---|---|---|
| `llama-3.3-70b-versatile` | $0.59 / 1M tokens | $0.79 / 1M tokens |

A typical full pipeline run (plan + code + test + review + PR) costs **$0.05–$0.50** depending on repository size and task complexity.

---

## Future Improvements

- [ ] **Database backend** — SQLite/Postgres for session persistence instead of JSON files
- [ ] **Authentication** — JWT-based auth for multi-user deployments
- [ ] **Streaming LLM output** — Stream tokens to the UI in real time
- [ ] **Docs MCP tool** — Documentation lookup to improve coder accuracy
- [ ] **Multi-repo support** — Run agents across multiple repositories simultaneously
- [ ] **GitHub App** — Trigger runs from PR comments or GitHub Actions
- [ ] **Plugin architecture** — Custom agent steps via a plugin registry
- [ ] **Evaluation suite** — Automated benchmarking against SWE-bench

---

## License

MIT
