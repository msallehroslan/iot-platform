"""
app/core/websocket_manager.py

ConnectionManager — in-process WebSocket registry for single-worker deployment.

⚠️  SINGLE-WORKER ONLY
This registry lives in process memory. CMD enforces --workers 1.

🔮  MULTI-WORKER UPGRADE PATH (Phase 4)
To scale beyond 1 worker, replace this manager with a Redis pub/sub backend:
  1. On connect:   subscribe to Redis channel  f"device:{device_id}"
  2. On broadcast: publish to Redis channel    f"device:{device_id}"
  3. Each worker reads its own Redis subscription and pushes to its local WS clients
  The interface (connect/disconnect/broadcast/broadcast_json) stays identical —
  only this file changes. No changes needed in ws.py, rpc.py, or telemetry_service.py.

  Suggested library: redis-py with asyncio support (aioredis).
"""
from fastapi import WebSocket
from typing import Dict, Set
import json
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Tracks active WebSocket connections grouped by device_id.
    Supports multiple clients per device (multiple browser tabs).
    """

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self, device_id: str, websocket: WebSocket) -> None:
        device_id = str(device_id)
        if device_id not in self._connections:
            self._connections[device_id] = set()
        self._connections[device_id].add(websocket)
        logger.info(
            "ws.connect device=%s clients_for_device=%d total_clients=%d",
            device_id,
            len(self._connections[device_id]),
            self.total_clients(),
        )

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        device_id = str(device_id)
        if device_id in self._connections:
            self._connections[device_id].discard(websocket)
            if not self._connections[device_id]:
                del self._connections[device_id]
        logger.info(
            "ws.disconnect device=%s total_clients=%d",
            device_id,
            self.total_clients(),
        )

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def broadcast(self, device_id: str, values: dict, ts: str) -> None:
        """
        Push a telemetry event to every connected client for a device.
        Message format matches frontend websocket.js expectations.
        Dead connections are removed lazily.
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

        dead = []
        for ws in list(sockets):
            try:
                await ws.send_text(msg)
            except Exception as exc:
                logger.debug("ws.send_failed device=%s: %s", device_id, exc)
                dead.append(ws)

        for ws in dead:
            sockets.discard(ws)

    async def broadcast_json(self, device_id: str, payload: dict) -> None:
        """
        Broadcast an arbitrary JSON payload to all clients of a device.
        Used for RPC push notifications, system alerts, etc.
        """
        sockets = self._connections.get(str(device_id), set())
        if not sockets:
            return

        msg = json.dumps(payload)
        dead = []
        for ws in list(sockets):
            try:
                await ws.send_text(msg)
            except Exception as exc:
                logger.debug("ws.broadcast_json_failed device=%s: %s", device_id, exc)
                dead.append(ws)

        for ws in dead:
            sockets.discard(ws)

    # ── Status ────────────────────────────────────────────────────────────────

    def total_clients(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def active_devices(self) -> int:
        return len(self._connections)

    def summary(self) -> dict:
        return {k: len(v) for k, v in self._connections.items()}


# ── Module-level singleton ────────────────────────────────────────────────────
# ⚠️  Replace this with a Redis-backed implementation for multi-worker scaling.
# The interface above stays the same — just swap the class.
manager = ConnectionManager()
