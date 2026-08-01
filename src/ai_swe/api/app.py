"""
FastAPI application factory for the AI SWE Agent.

Usage:
    uvicorn ai_swe.api.app:create_app --factory --reload --port 8000

Or via the CLI:
    ai-swe serve
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_swe.api.routes import router
from ai_swe.api.session_store import SessionStore
from ai_swe.api.ws import EventBus, ws_router
from ai_swe.config import get_settings
from ai_swe.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize singletons on startup, clean up on shutdown."""
    configure_logging()
    settings = get_settings()
    settings.workdir.mkdir(parents=True, exist_ok=True)
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    # Initialize singletons eagerly so they're ready before the first request.
    SessionStore.get_instance()
    EventBus.get_instance()

    logger.info("AI SWE Agent API started on %s:%d", settings.api_host, settings.api_port)
    yield
    logger.info("AI SWE Agent API shutting down.")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns a fully configured app instance ready for `uvicorn`.
    """
    settings = get_settings()

    app = FastAPI(
        title="AI SWE Agent API",
        description=(
            "REST + WebSocket API for the AI Software Engineer Agent. "
            "Orchestrates Planner → Coder → Executor → Reviewer → Publisher "
            "over the Model Context Protocol (MCP)."
        ),
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS — allow the Vite dev server and any configured origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount REST routes under /api
    app.include_router(router, prefix="/api")

    # Mount WebSocket routes at the root (ws://host/ws/{session_id})
    app.include_router(ws_router)

    return app


# Allow `uvicorn ai_swe.api.app:app` as well as the factory pattern
app = create_app()
