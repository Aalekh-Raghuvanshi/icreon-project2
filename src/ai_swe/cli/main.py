"""
Command-line interface for the AI SWE Agent foundation.

Today's CLI does exactly what this milestone requires and nothing more:

    1. Ask the user for a GitHub repository URL.
    2. Connect to the Git and Filesystem MCP servers.
    3. Clone the repository (via `clone_repository`, Git MCP).
    4. List every file in the cloned repository (via `list_repository_files`,
       Filesystem MCP).
    5. Print a success summary.

Run it with:

    python main.py
    # or, once installed:
    ai-swe
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ai_swe.config import get_settings
from ai_swe.logging_config import configure_logging, get_logger
from ai_swe.mcp.factory import build_orchestrator
from ai_swe.mcp.filesystem_tools import list_repository_files
from ai_swe.mcp.git_tools import clone_repository

app = typer.Typer(add_completion=False, help="AI SWE Agent -- foundation CLI.")
console = Console()
logger = get_logger(__name__)


def _repo_dest_path(workdir: Path, repo_url: str) -> Path:
    """Derive a destination directory name from a repo URL, e.g. '.../foo/bar.git' -> 'bar'."""
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".git")
    return workdir / (name or "repository")


async def _run(repo_url: str) -> None:
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
        asyncio.run(_run(repo_url))
    except Exception as exc:
        logger.exception("Task failed.")
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    app()
