"""
WebSocket endpoint for real-time agent event streaming.

Each connected client subscribes to a session_id channel.  As the pipeline
runs (in a background asyncio task), it calls `EventBus.publish(session_id,
event)` and all connected WebSocket clients for that session receive the
JSON-serialized event.

The EventBus is a simple in-memory pub/sub — no broker needed for the MVP.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_swe.api.models import AgentEvent

logger = logging.getLogger(__name__)

ws_router = APIRouter()


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------


class EventBus:
    """
    In-process pub/sub bus for WebSocket events.

    Producers (background pipeline tasks) call `publish()`; consumers
    (WebSocket handlers) call `subscribe()` / `unsubscribe()`.
    """

    def __init__(self) -> None:
        # session_id -> set of asyncio.Queue instances (one per connected WS client)
        self._subscribers: dict[str, set[asyncio.Queue[Any]]] = defaultdict(set)

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        self._subscribers[session_id].add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[Any]) -> None:
        self._subscribers[session_id].discard(q)
        if not self._subscribers[session_id]:
            del self._subscribers[session_id]

    async def publish(self, session_id: str, event: AgentEvent) -> None:
        """Publish an event to all subscribers of *session_id*."""
        payload = event.model_dump_json()
        dead: list[asyncio.Queue[Any]] = []
        for q in list(self._subscribers.get(session_id, set())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest item and retry once
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
        for q in dead:
            self.unsubscribe(session_id, q)

    # Singleton
    _instance: "EventBus | None" = None

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------


@ws_router.websocket("/ws/{session_id}")
async def websocket_session(websocket: WebSocket, session_id: str) -> None:
    """
    WebSocket endpoint: ``ws://host/ws/<session_id>``

    The client connects, receives a stream of JSON-encoded ``AgentEvent``
    objects, and should close the connection after receiving a ``done`` or
    ``error`` event (or when the server closes it).
    """
    bus = EventBus.get_instance()
    await websocket.accept()
    logger.info("WebSocket client connected for session %s", session_id)

    q = bus.subscribe(session_id)
    try:
        while True:
            # Wait for an event from the pipeline, with a 30-second ping
            # interval to keep the connection alive through proxies.
            try:
                payload = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(payload)
                # If the event is terminal, close cleanly.
                import json as _json
                evt = _json.loads(payload)
                if evt.get("event") in ("done", "error"):
                    await websocket.close()
                    break
            except asyncio.TimeoutError:
                # Send a ping/keep-alive message
                try:
                    await websocket.send_text('{"event":"ping"}')
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from session %s", session_id)
    except Exception as exc:
        logger.warning("WebSocket error for session %s: %s", session_id, exc)
    finally:
        bus.unsubscribe(session_id, q)
