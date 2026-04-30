"""
app/core/websocket_manager.py

ConnectionManager — tracks all active WebSocket clients across all devices.

This is the single source of truth for the connection registry.
ws.py imports and uses manager directly; telemetry.py calls manager.broadcast().

Design:
  - One manager instance shared across the process (module-level singleton)
  - device_id → set of WebSocket connections  (not a list — prevents duplicates)
  - Thread-safe for asyncio (single event loop); no locking needed
  - Dead connections removed lazily on failed send
"""
from fastapi import WebSocket
from typing import Dict, Set
import json
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Tracks active WebSocket connections grouped by device_id.
    Supports multiple clients per device (e.g. multiple browser tabs).
    """

    def __init__(self):
        # device_id (str) → set of accepted WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self, device_id: str, websocket: WebSocket) -> None:
        """Register a new accepted WebSocket for a device."""
        self._connections.setdefault(device_id, set()).add(websocket)
        logger.info(
            "WS connect  device=%-36s  total_clients=%d",
            device_id,
            len(self._connections[device_id]),
        )

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket when it closes."""
        sockets = self._connections.get(device_id, set())
        sockets.discard(websocket)
        logger.info(
            "WS disconnect device=%-36s  remaining=%d",
            device_id,
            len(sockets),
        )
        if not sockets:
            self._connections.pop(device_id, None)

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def broadcast(self, device_id: str, values: dict, ts: str) -> None:
        """
        Push a telemetry event to every connected client for a device.

        Called by the telemetry ingest endpoint immediately after DB commit.
        Errors are swallowed per-connection so one dead socket never blocks
        the others, and WS failures never propagate to the HTTP response.

        Message format (matches frontend websocket.js expectations):
        {
            "type":      "telemetry",
            "device_id": "<uuid>",
            "values":    { "temperature": 28.5, "humidity": 70 },
            "ts":        "2025-04-29T10:00:00.000Z"
        }
        """
        sockets = self._connections.get(str(device_id), set())
        if not sockets:
            return

        msg = json.dumps({
            "type":      "telemetry",
            "device_id": str(device_id),
            "values":    values,
            "ts":        ts,
        })

        dead: list[WebSocket] = []
        for ws in list(sockets):        # snapshot to allow mutation
            try:
                await ws.send_text(msg)
            except Exception as exc:
                logger.debug("WS send failed device=%s: %s", device_id, exc)
                dead.append(ws)

        for ws in dead:
            sockets.discard(ws)

    async def broadcast_json(self, device_id: str, payload: dict) -> None:
        """
        Broadcast an arbitrary JSON payload to all clients of a device.
        Lower-level than broadcast() — caller builds the full message dict.
        """
        sockets = self._connections.get(str(device_id), set())
        if not sockets:
            return

        msg  = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in list(sockets):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)

        for ws in dead:
            sockets.discard(ws)

    # ── Inspection ────────────────────────────────────────────────────────────

    def client_count(self, device_id: str) -> int:
        return len(self._connections.get(device_id, set()))

    def total_clients(self) -> int:
        return sum(len(s) for s in self._connections.values())

    def active_devices(self) -> list[str]:
        return [d for d, s in self._connections.items() if s]

    def summary(self) -> dict:
        return {d: len(s) for d, s in self._connections.items() if s}


# Module-level singleton — imported by ws.py and telemetry.py
#
# ⚠️  SINGLE-WORKER ONLY
# This registry lives in process memory. If the app runs with --workers > 1,
# each worker has a separate registry. A telemetry ingest on worker A will
# NOT broadcast to WebSocket clients connected to worker B. The Dockerfile
# CMD enforces --workers 1 to prevent this. Do not override it without
# replacing this registry with a pub/sub backend (Redis, etc.).
manager = ConnectionManager()
