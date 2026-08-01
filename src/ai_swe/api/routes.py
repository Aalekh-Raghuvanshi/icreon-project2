"""
REST API routes for the AI SWE Agent.

All endpoints are mounted under the `/api` prefix in `app.py`.

Endpoints:
    GET  /api/health
    GET  /api/sessions
    POST /api/sessions
    GET  /api/sessions/{session_id}
    GET  /api/sessions/{session_id}/logs
    GET  /api/sessions/{session_id}/diff
    GET  /api/sessions/{session_id}/test-results
    GET  /api/sessions/{session_id}/pr
    DELETE /api/sessions/{session_id}
    POST /api/repo/clone
    GET  /api/repo/tree
    GET  /api/repo/search
    POST /api/repo/analyze
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ai_swe.api.models import (
    AgentEvent,
    CIResultOut,
    CloneRepoRequest,
    CloneResult,
    HealthResponse,
    LogEntryOut,
    PatchOut,
    PlanOut,
    PlanStepOut,
    RepoTreeNode,
    SessionDetail,
    SessionSummary,
    StartRunRequest,
    TestResultOut,
)
from ai_swe.api.session_store import SessionStore
from ai_swe.api.ws import EventBus
from ai_swe.config import get_settings
from ai_swe.logging_config import read_session_logs
from ai_swe.state import AgentState, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _store() -> SessionStore:
    return SessionStore.get_instance()


def _bus() -> EventBus:
    return EventBus.get_instance()


def _get_session_or_404(session_id: str, store: SessionStore = Depends(_store)) -> AgentState:
    state = store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return state


# ---------------------------------------------------------------------------
# State → API model conversions
# ---------------------------------------------------------------------------


def _state_to_summary(state: AgentState) -> SessionSummary:
    return SessionSummary(
        session_id=state.session_id,
        task=state.task,
        status=state.status.value if hasattr(state.status, "value") else str(state.status),
        repo_path=state.repo_path,
        repo_url=state.repo_url,
        started_at=state.started_at,
        finished_at=state.finished_at,
        progress=state.progress,
        current_agent=state.current_agent,
        estimated_cost_usd=state.estimated_cost_usd,
        total_tokens=state.total_input_tokens + state.total_output_tokens,
        pr_url=state.pr_url,
    )


def _state_to_detail(state: AgentState) -> SessionDetail:
    plan_out = PlanOut(
        summary=state.plan.summary,
        steps=[
            PlanStepOut(
                id=s.id,
                description=s.description,
                done=s.done,
                files_involved=s.files_involved,
                reasoning=s.reasoning,
                risk_level=s.risk_level,
            )
            for s in state.plan.steps
        ],
        files_to_create=state.plan.files_to_create,
        files_to_modify=state.plan.files_to_modify,
        architecture_impact=state.plan.architecture_impact,
        testing_strategy=state.plan.testing_strategy,
    )
    ci = None
    if state.ci_result is not None:
        ci = CIResultOut(passed=state.ci_result.passed, output=state.ci_result.output or "")
    return SessionDetail(
        session_id=state.session_id,
        task=state.task,
        status=state.status.value if hasattr(state.status, "value") else str(state.status),
        repo_path=state.repo_path,
        repo_url=state.repo_url,
        started_at=state.started_at,
        finished_at=state.finished_at,
        elapsed_seconds=state.elapsed_seconds(),
        current_agent=state.current_agent,
        progress=state.progress,
        plan=plan_out,
        patches=[PatchOut(file_path=p.file_path, diff=p.diff, description=p.description) for p in state.patches],
        test_results=[TestResultOut(name=r.name, passed=r.passed, output=r.output) for r in state.test_results],
        logs=[
            LogEntryOut(
                timestamp=e.timestamp,
                agent=e.agent,
                message=e.message,
                level=e.level,
                input_summary=e.input_summary,
                decision=e.decision,
                output_summary=e.output_summary,
                execution_time_ms=e.execution_time_ms,
            )
            for e in state.logs
        ],
        error=state.error,
        fix_attempts=state.fix_attempts,
        ci_result=ci,
        branch_name=state.branch_name,
        pr_url=state.pr_url,
        total_input_tokens=state.total_input_tokens,
        total_output_tokens=state.total_output_tokens,
        estimated_cost_usd=state.estimated_cost_usd,
        retry_count=state.retry_count,
    )


# ---------------------------------------------------------------------------
# Background task: run the pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(
    session_id: str,
    request: StartRunRequest,
    store: SessionStore,
    bus: EventBus,
) -> None:
    """
    Background coroutine: runs the full agent pipeline, publishes WebSocket
    events, and persists state updates along the way.
    """
    from ai_swe.mcp.factory import build_orchestrator
    from ai_swe.orchestrator.graph import run_task

    state = store.get(session_id)
    if state is None:
        return

    settings = get_settings()

    # Progress milestones per stage
    _PROGRESS: dict[str, float] = {
        "planning":  0.10,
        "coding":    0.40,
        "executing": 0.65,
        "reviewing": 0.85,
        "done":      1.00,
        "failed":    1.00,
    }

    async def _publish(event: str, **kwargs: Any) -> None:
        evt = AgentEvent(
            event=event,
            session_id=session_id,
            timestamp=datetime.now(UTC),
            **kwargs,
        )
        await bus.publish(session_id, evt)

    try:
        state.started_at = datetime.now(UTC)
        store.update(state)
        await _publish("progress", progress=0.0, status="pending", message="Starting pipeline…")

        repo_path = Path(request.repo_path)
        orchestrator = build_orchestrator(settings, repo_path)

        async with orchestrator:
            # Patch: intercept state transitions to publish live events.
            # We do this by wrapping run_task and polling state updates.
            # A cleaner approach uses LangGraph callbacks — deferred to a future PR.

            async def _run_with_events() -> AgentState:
                # Start the graph in a background task
                task_coro = asyncio.create_task(run_task(orchestrator, state))
                last_status = state.status

                while not task_coro.done():
                    await asyncio.sleep(0.5)
                    # We can't peek inside LangGraph's running state cheaply,
                    # so we rely on the store being updated by agent hooks.
                    current = store.get(session_id)
                    if current and current.status != last_status:
                        last_status = current.status
                        status_val = (
                            current.status.value
                            if hasattr(current.status, "value")
                            else str(current.status)
                        )
                        progress = _PROGRESS.get(status_val, current.progress)
                        await _publish(
                            "agent_started",
                            agent=current.current_agent,
                            status=status_val,
                            progress=progress,
                            message=f"Agent {current.current_agent or status_val} is running…",
                        )

                return await task_coro

            result = await _run_with_events()

        # Handle open_pr
        if request.open_pr and result.status == TaskStatus.DONE:
            from ai_swe.agents.publisher import finalize_and_open_pr
            async with build_orchestrator(settings, Path(request.repo_path)) as orc:
                result = await finalize_and_open_pr(orc, result, base_branch=request.base_branch)

        result.finished_at = datetime.now(UTC)
        result.progress = 1.0
        store.update(result)

        status_val = result.status.value if hasattr(result.status, "value") else str(result.status)
        await _publish(
            "done",
            status=status_val,
            progress=1.0,
            message="Pipeline finished.",
            data={
                "pr_url": result.pr_url,
                "error": result.error,
            },
        )

    except Exception as exc:
        logger.exception("Pipeline failed for session %s", session_id)
        state_now = store.get(session_id) or state
        state_now.status = TaskStatus.FAILED
        state_now.error = str(exc)
        state_now.finished_at = datetime.now(UTC)
        state_now.progress = 1.0
        store.update(state_now)
        await _publish("error", message=str(exc), status="failed", progress=1.0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(store: SessionStore = Depends(_store)) -> list[SessionSummary]:
    """List all pipeline sessions."""
    return [_state_to_summary(s) for s in store.list_all()]


@router.post("/sessions", response_model=SessionSummary, status_code=202)
async def start_session(
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    store: SessionStore = Depends(_store),
    bus: EventBus = Depends(_bus),
) -> SessionSummary:
    """
    Start a new pipeline run.  Returns immediately with the session_id;
    progress is streamed via the WebSocket at ``/ws/{session_id}``.
    """
    repo_path = Path(request.repo_path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path '{request.repo_path}' is not a directory.")
    if request.open_pr and not request.repo_url:
        raise HTTPException(status_code=400, detail="open_pr=True requires repo_url.")

    state = AgentState(
        task=request.task,
        repo_path=request.repo_path,
        repo_url=request.repo_url,
    )
    store.create(state)

    background_tasks.add_task(_run_pipeline, state.session_id, request, store, bus)
    return _state_to_summary(state)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(state: AgentState = Depends(_get_session_or_404)) -> SessionDetail:
    """Return full session state."""
    return _state_to_detail(state)


@router.get("/sessions/{session_id}/logs")
async def get_session_logs(state: AgentState = Depends(_get_session_or_404)) -> list[dict[str, Any]]:
    """Return structured interaction logs for a session."""
    return read_session_logs(state.session_id)


@router.get("/sessions/{session_id}/diff", response_model=list[PatchOut])
async def get_session_diff(state: AgentState = Depends(_get_session_or_404)) -> list[PatchOut]:
    """Return all code patches (diffs) produced during the session."""
    return [PatchOut(file_path=p.file_path, diff=p.diff, description=p.description) for p in state.patches]


@router.get("/sessions/{session_id}/test-results", response_model=list[TestResultOut])
async def get_session_test_results(state: AgentState = Depends(_get_session_or_404)) -> list[TestResultOut]:
    """Return test results for the session."""
    return [TestResultOut(name=r.name, passed=r.passed, output=r.output) for r in state.test_results]


@router.get("/sessions/{session_id}/pr")
async def get_session_pr(state: AgentState = Depends(_get_session_or_404)) -> dict[str, Any]:
    """Return pull-request metadata for the session."""
    ci = None
    if state.ci_result is not None:
        ci = {"passed": state.ci_result.passed, "output": state.ci_result.output or ""}
    return {
        "branch_name": state.branch_name,
        "pr_url": state.pr_url,
        "ci_result": ci,
        "patches_count": len(state.patches),
        "test_results_summary": {
            "passed": sum(1 for r in state.test_results if r.passed),
            "failed": sum(1 for r in state.test_results if not r.passed),
        },
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(_store),
) -> None:
    """Delete a session from memory and disk."""
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")


# ---------------------------------------------------------------------------
# Repository routes
# ---------------------------------------------------------------------------


@router.post("/repo/clone", response_model=CloneResult)
async def clone_repo(request: CloneRepoRequest) -> CloneResult:
    """Clone a remote repository into the local workspace."""
    from ai_swe.mcp.factory import build_orchestrator
    from ai_swe.mcp.git_tools import clone_repository

    settings = get_settings()
    workdir = settings.workdir.expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    repo_name = request.repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    dest = Path(request.dest_path) if request.dest_path else workdir / repo_name

    orchestrator = build_orchestrator(settings, workdir)
    try:
        async with orchestrator:
            result = await clone_repository(orchestrator, request.repo_url, str(dest))
        return CloneResult(
            success=result.success,
            dest_path=str(dest),
            branch=result.branch,
            commit_hash=result.commit_hash,
            message=result.message if hasattr(result, "message") else "",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/repo/tree", response_model=list[RepoTreeNode])
async def repo_tree(path: str = Query(..., description="Absolute path to the repository root.")) -> list[RepoTreeNode]:
    """Return a flat list of files/dirs in the repository."""
    from ai_swe.mcp.factory import build_orchestrator
    from ai_swe.mcp.filesystem_tools import list_repository_files

    settings = get_settings()
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"'{path}' is not a directory.")

    orchestrator = build_orchestrator(settings, repo_path)
    try:
        async with orchestrator:
            files = await list_repository_files(orchestrator, str(repo_path), exclude_patterns=[".git"])
        return [RepoTreeNode(path=f.path, is_dir=f.is_dir, size_bytes=f.size_bytes) for f in files]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/repo/search")
async def search_files(
    path: str = Query(...),
    q: str = Query(...),
    max_results: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Simple file-name search within a repository tree."""
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"'{path}' is not a directory.")

    results: list[dict[str, Any]] = []
    query = q.lower()
    for p in repo_path.rglob("*"):
        if query in p.name.lower() and not any(part.startswith(".") for part in p.parts):
            results.append({
                "path": str(p.relative_to(repo_path)),
                "is_dir": p.is_dir(),
                "size_bytes": p.stat().st_size if p.is_file() else None,
            })
            if len(results) >= max_results:
                break
    return results


@router.post("/repo/analyze")
async def analyze_repo(path: str = Query(...)) -> dict[str, Any]:
    """Run codebase analysis and return architecture summary."""
    from ai_swe.indexer.analyzer import CodebaseAnalyzer

    repo_path = Path(path)
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"'{path}' is not a directory.")

    try:
        analyzer = CodebaseAnalyzer(repo_path=repo_path, concurrency=8)
        index = await analyzer.analyze_repository()
        return {
            "total_files": index.total_files,
            "languages": index.languages,
            "summary": index.summary if hasattr(index, "summary") else "",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
