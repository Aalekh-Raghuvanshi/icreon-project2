"""
Command-line interface for the AI SWE Agent.

Available commands:

  Default command (backwards-compatible):
    python main.py [--repo-url URL]
    ai-swe [--repo-url URL]

      Ask for a GitHub repository URL, clone it via MCP servers, list its
      files, and report success.

  Repository understanding command:
    ai-swe repo analyze [REPO_PATH] [--output PATH] [--no-save]

      Walk a local repository, parse its source code with Tree-sitter,
      build a dependency graph, generate semantic file summaries, persist
      the index as JSON, and print a beautiful architecture summary.

  Planning command (new):
    ai-swe plan "Add rate limiting" [REPO_PATH] [--output PATH] [--no-save]

      Analyse the codebase and produce a structured implementation plan
      using an LLM (Claude).  Visualises the plan in the terminal and
      saves it as JSON.

  Full pipeline command:
    ai-swe run "Add rate limiting" [REPO_PATH] [--open-pr] [--repo-url URL]

      Run the complete agent pipeline (Planner -> Coder -> Executor ->
      Reviewer) against a local repository checkout, rendering the live
      agent log and test results.  With `--open-pr`, a successful run is
      followed by a CI gate (lint + type-check) and, if that passes, a
      pull request opened via the Publisher.

Run with:

    python main.py
    # or, once installed:
    ai-swe
    ai-swe repo analyze .
    ai-swe plan "Add rate limiting" .
    ai-swe run "Add rate limiting" .
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from ai_swe.state import AgentState

from ai_swe.config import get_settings
from ai_swe.logging_config import configure_logging, get_logger
from ai_swe.mcp.factory import build_orchestrator
from ai_swe.mcp.filesystem_tools import list_repository_files
from ai_swe.mcp.git_tools import clone_repository

app = typer.Typer(add_completion=False, help="AI SWE Agent — foundation CLI.")
console = Console()
logger = get_logger(__name__)

# ── Sub-app: repo ────────────────────────────────────────────────────────────

repo_app = typer.Typer(
    name="repo",
    help="Repository understanding commands.",
    add_completion=False,
)
app.add_typer(repo_app)


# ── Command: plan ─────────────────────────────────────────────────────────────


@app.command("plan")
def plan_command(
    task: str = typer.Argument(
        ...,
        help="Natural-language description of the task to plan, e.g. 'Add rate limiting'.",
    ),
    repo_path: Path = typer.Argument(
        Path("."),
        help="Local path to the repository (default: current directory).",
        show_default=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination for the plan JSON file. "
        "Defaults to <REPO_PATH>/.ai_swe_plan.json.",
    ),
    no_save: bool = typer.Option(
        False,
        "--no-save",
        help="Skip writing the plan JSON; only display the visualisation.",
    ),
) -> None:
    """
    Generate an AI-powered implementation plan for a task.

    Analyses the repository, selectively retrieves relevant files, and uses
    an LLM (Claude) to produce a structured, step-by-step implementation
    roadmap.

    Examples:

        ai-swe plan "Add rate limiting" .
        ai-swe plan "Refactor auth module" /path/to/project --output plan.json
        ai-swe plan "Add unit tests for user service" --no-save
    """
    configure_logging()

    # Lazy imports to keep --help fast
    from ai_swe.agents.plan_persistence import save_plan
    from ai_swe.agents.plan_visualizer import visualize_plan
    from ai_swe.agents.planner import generate_plan

    repo_path = repo_path.expanduser().resolve()

    if not repo_path.is_dir():
        console.print(f"[bold red]Error:[/bold red] {repo_path} is not a directory.")
        raise typer.Exit(code=1)

    console.print()
    console.print(
        f"[bold bright_cyan]🧠 Planning:[/] [white]{task}[/]"
    )
    console.print(
        f"[dim]   Repository: {repo_path}[/]"
    )
    console.print()

    async def _run() -> None:
        plan = await generate_plan(task, repo_path)

        # Visualise
        visualize_plan(plan, console)

        # Save
        if not no_save:
            dest = save_plan(plan, repo_path=repo_path, output_path=output)
            console.print(
                f"[bold green]✓[/bold green] Plan saved → [cyan]{dest}[/cyan]"
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Planning failed.")
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

_LOG_LEVEL_STYLES = {"info": "white", "warning": "yellow", "error": "bold red"}


def _render_agent_log(state: AgentState, console: Console) -> None:
    console.print()
    table = Table(
        title="[bold bright_yellow]🪵 Agent Log[/]",
        show_header=True,
        header_style="bold bright_white on dark_blue",
        border_style="bright_blue",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Agent", style="bold cyan", width=12)
    table.add_column("Level", width=8, justify="center")
    table.add_column("Message", style="white", ratio=1)

    for entry in state.logs:
        style = _LOG_LEVEL_STYLES.get(entry.level, "white")
        table.add_row(entry.agent, Text(entry.level.upper(), style=style), Text(entry.message, style=style))

    console.print(table)


def _render_test_results(state: AgentState, console: Console) -> None:
    if not state.test_results:
        return

    console.print()
    table = Table(
        title="[bold bright_green]🧪 Test Results[/]",
        show_header=True,
        header_style="bold bright_white on dark_green",
        border_style="green",
        expand=True,
    )
    table.add_column("Suite", style="white", ratio=3)
    table.add_column("Result", width=10, justify="center")

    for result in state.test_results:
        status = Text("✅ PASS", style="bold green") if result.passed else Text("❌ FAIL", style="bold red")
        table.add_row(result.name, status)

    console.print(table)


def _render_final_status(state: AgentState, console: Console) -> None:
    from ai_swe.state import TaskStatus

    if state.status == TaskStatus.DONE:
        style, emoji, label = "green", "✅", "DONE"
    elif state.status == TaskStatus.FAILED:
        style, emoji, label = "red", "❌", "FAILED"
    else:
        style, emoji, label = "yellow", "⚠️ ", state.status.value.upper()

    content = Text()
    content.append(f"{emoji} Status: ", style=f"bold {style}")
    content.append(label, style=style)
    if state.error:
        content.append(f"\n\nError: {state.error}", style="red")
    if state.ci_result is not None:
        content.append(
            f"\n\nCI gate: {'✅ PASSED' if state.ci_result.passed else '❌ FAILED'}",
            style="green" if state.ci_result.passed else "red",
        )
    if state.pr_url:
        content.append(f"\n\nPull request: {state.pr_url}", style="bold cyan")

    console.print()
    console.print(Panel(content, title="[bold]Run Summary[/]", border_style=style, padding=(1, 2)))


@app.command("run")
def run_command(
    task: str = typer.Argument(
        ..., help="Natural-language description of the task to accomplish, e.g. 'Add rate limiting'."
    ),
    repo_path: Path = typer.Argument(
        Path("."),
        help="Local path to the repository to work on (default: current directory).",
        show_default=True,
    ),
    open_pr: bool = typer.Option(
        False,
        "--open-pr",
        help="On a successful (DONE) run, run the CI gate (lint + type-check) and, if it passes, "
        "open a pull request.",
    ),
    repo_url: str | None = typer.Option(
        None,
        "--repo-url",
        help="GitHub repository URL (owner/repo), required with --open-pr so the Publisher knows "
        "where to open the pull request.",
    ),
    base_branch: str = typer.Option(
        "main", "--base-branch", help="Base branch to open the pull request against."
    ),
) -> None:
    """
    Run the full agent pipeline end to end: Planner -> Coder -> Executor -> Reviewer.

    Renders the live agent log and test results as the pipeline completes.
    With `--open-pr`, a run that reaches DONE is followed by a CI gate
    (`ruff check` + `mypy`, via the Publisher) and, if that passes, a pull
    request opened against `--base-branch`.

    Examples:

        ai-swe run "Add input validation to the signup form" .
        ai-swe run "Fix the flaky retry test" . --open-pr --repo-url https://github.com/me/myrepo
    """
    configure_logging()

    from ai_swe.agents.publisher import finalize_and_open_pr
    from ai_swe.orchestrator.graph import run_task
    from ai_swe.state import AgentState, TaskStatus

    repo_path = repo_path.expanduser().resolve()

    if not repo_path.is_dir():
        console.print(f"[bold red]Error:[/bold red] {repo_path} is not a directory.")
        raise typer.Exit(code=1)

    if open_pr and not repo_url:
        console.print("[bold red]Error:[/bold red] --open-pr requires --repo-url.")
        raise typer.Exit(code=1)

    console.print()
    console.print(f"[bold bright_cyan]🚀 Running:[/] [white]{task}[/]")
    console.print(f"[dim]   Repository: {repo_path}[/]")
    console.print()

    async def _run() -> AgentState:
        settings = get_settings()
        orchestrator = build_orchestrator(settings, repo_path)

        async with orchestrator:
            state = AgentState(task=task, repo_path=str(repo_path), repo_url=repo_url)
            result = await run_task(orchestrator, state)

            _render_agent_log(result, console)
            _render_test_results(result, console)

            if open_pr:
                if result.status != TaskStatus.DONE:
                    console.print(
                        "\n[yellow]Skipping PR: pipeline did not reach DONE.[/yellow]"
                    )
                else:
                    console.print("\n[bold cyan]Running CI gate and opening pull request...[/bold cyan]")
                    result = await finalize_and_open_pr(orchestrator, result, base_branch=base_branch)

            _render_final_status(result, console)
            return result

    try:
        final_state = asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Run failed.")
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    if final_state.status == TaskStatus.FAILED:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# repo analyze
# ---------------------------------------------------------------------------


@repo_app.command("analyze")
def repo_analyze(
    repo_path: Path = typer.Argument(
        Path("."),
        help="Local path to the repository to analyse (default: current directory).",
        show_default=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination for the JSON index file. "
        "Defaults to <REPO_PATH>/.ai_swe_index.json.",
    ),
    no_save: bool = typer.Option(
        False,
        "--no-save",
        help="Skip writing the JSON index; only display the architecture summary.",
    ),
    concurrency: int = typer.Option(
        8,
        "--concurrency",
        "-c",
        help="Max files parsed concurrently.",
    ),
) -> None:
    """
    Analyse a local repository: parse AST symbols, build a dependency graph,
    generate semantic file summaries, and display a beautiful architecture
    summary.

    Example:

        ai-swe repo analyze .
        ai-swe repo analyze /path/to/myproject --output /tmp/index.json
    """
    configure_logging()

    # Lazy import so the MCP path is not loaded on every --help
    from ai_swe.indexer.analyzer import CodebaseAnalyzer
    from ai_swe.indexer.persistence import save_index
    from ai_swe.indexer.reporter import print_architecture_summary

    repo_path = repo_path.expanduser().resolve()

    if not repo_path.is_dir():
        console.print(f"[bold red]Error:[/bold red] {repo_path} is not a directory.")
        raise typer.Exit(code=1)

    # Determine output path
    index_path: Path | None = None
    if not no_save:
        index_path = (output or repo_path / ".ai_swe_index.json").expanduser().resolve()

    async def _run() -> None:
        analyzer = CodebaseAnalyzer(
            repo_path=repo_path,
            concurrency=concurrency,
            console=console,
        )
        index = await analyzer.analyze_repository()

        # Print architecture summary
        print_architecture_summary(index, console, output_path=index_path)

        # Persist JSON
        if index_path is not None:
            save_index(index, index_path)
            console.print(
                f"[bold green]✓[/bold green] Index saved → [cyan]{index_path}[/cyan]"
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("repo analyze failed.")
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Original default command (backwards-compatible)
# ---------------------------------------------------------------------------


def _repo_dest_path(workdir: Path, repo_url: str) -> Path:
    """Derive a destination directory name from a repo URL, e.g. '.../foo/bar.git' -> 'bar'."""
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".git")
    return workdir / (name or "repository")


async def _run_clone(repo_url: str) -> None:
    """The actual async workflow: connect -> clone -> list files -> report."""
    settings = get_settings()
    # Resolve to an absolute path up front. Both MCP servers are scoped to an
    # absolute allowed-directory (see `mcp.factory.build_orchestrator`), and
    # every path we hand them needs to agree with that scope -- a relative
    # path gets re-resolved *against* the server's allowed directory, which
    # would silently produce the wrong location (e.g. `<allowed>/<relative>`
    # instead of the intended path).
    workdir = settings.workdir.expanduser().resolve()
    dest_path = _repo_dest_path(workdir, repo_url)

    orchestrator = build_orchestrator(settings, workdir)

    console.print("[bold cyan]Connecting to MCP servers (git, github, filesystem)...[/bold cyan]")
    async with orchestrator:
        health = await orchestrator.health_check()
        for server_name, outcome in health.items():
            if isinstance(outcome, list):
                console.print(f"  [green]✓[/green] {server_name}: {len(outcome)} tools available")
            else:
                console.print(f"  [red]✗[/red] {server_name}: {outcome}")

        console.print(f"\n[bold cyan]Cloning[/bold cyan] {repo_url} -> {dest_path}")
        clone_result = await clone_repository(orchestrator, repo_url, str(dest_path))

        if not clone_result.success:
            console.print("[bold red]Clone failed.[/bold red]")
            raise typer.Exit(code=1)

        console.print(
            f"  [green]✓[/green] Cloned branch '{clone_result.branch}' "
            f"at commit {clone_result.commit_hash}"
        )

        console.print(f"\n[bold cyan]Listing files[/bold cyan] under {dest_path}")
        files = await list_repository_files(orchestrator, str(dest_path), exclude_patterns=[".git"])

        table = Table(title=f"Repository contents: {dest_path.name}")
        table.add_column("Type", style="magenta", width=6)
        table.add_column("Path", style="white")
        for record in sorted(files, key=lambda f: f.path):
            table.add_row("DIR" if record.is_dir else "FILE", record.path)
        console.print(table)

        console.print(
            f"\n[bold green]✓ Success:[/bold green] cloned and indexed "
            f"{sum(not f.is_dir for f in files)} files "
            f"({sum(f.is_dir for f in files)} directories) from {repo_url}."
        )


@app.command()
def main(
    repo_url: str = typer.Option(
        None, "--repo-url", "-r", help="GitHub repository URL to clone (skips the interactive prompt)."
    ),
) -> None:
    """Ask for a GitHub URL, clone it, list its files, and report success."""
    configure_logging()

    if not repo_url:
        repo_url = typer.prompt("Enter a GitHub repository URL to clone")

    try:
        asyncio.run(_run_clone(repo_url))
    except Exception as exc:
        logger.exception("Task failed.")
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    app()


# ---------------------------------------------------------------------------
# serve (FastAPI + uvicorn)
# ---------------------------------------------------------------------------


@app.command("serve")
def serve_command(
    host: str = typer.Option(None, "--host", "-H", help="Host to bind (default: from settings)."),
    port: int = typer.Option(None, "--port", "-p", help="Port to bind (default: from settings)."),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn auto-reload (dev mode)."),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level."),
) -> None:
    """
    Start the FastAPI + WebSocket API server.

    The server exposes REST endpoints under /api and a WebSocket endpoint
    at /ws/{session_id} for real-time agent event streaming.

    Examples:
        ai-swe serve
        ai-swe serve --port 9000 --reload
    """
    import uvicorn  # type: ignore[import]

    configure_logging()
    settings = get_settings()

    resolved_host = host or settings.api_host
    resolved_port = port or settings.api_port

    console.print(
        f"[bold bright_cyan]🚀 Starting API server[/] at "
        f"[bold white]http://{resolved_host}:{resolved_port}[/]"
    )
    console.print(f"   [dim]Docs → http://{resolved_host}:{resolved_port}/api/docs[/]")
    console.print()

    uvicorn.run(
        "ai_swe.api.app:app",
        host=resolved_host,
        port=resolved_port,
        reload=reload,
        log_level=log_level,
    )

