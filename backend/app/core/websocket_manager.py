"""
app/core/websocket_manager.py — Phase 4 WebSocket Manager

Two implementations sharing one interface:

  InProcessManager  — single-worker (current default, no Redis needed)
  RedisManager      — multi-worker via Redis pub/sub

Selection is automatic:
  - REDIS_URL set   → RedisManager (horizontal scaling ready)
  - REDIS_URL unset → InProcessManager (backward compatible, zero config)

Interface (both classes implement):
  connect(device_id, ws)        — register a client
  disconnect(device_id, ws)     — unregister a client
  broadcast(device_id, values, ts)     — push telemetry event
  broadcast_json(device_id, payload)   — push arbitrary JSON
  total_clients() → int
  active_devices() → int
  summary() → dict

Redis pub/sub architecture:
  Instance A ingest → publishes to Redis channel "device:{id}"
  Instance B (has WS clients) → subscribed → pushes to its local clients
  No duplicate sends: each instance only pushes to ITS OWN connected clients.
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


# ── In-process manager (single-worker, zero dependencies) ────────────────────

class InProcessManager:
    """Default manager. Works correctly with --workers 1 (Render free tier)."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    def connect(self, device_id: str, ws: WebSocket) -> None:
        device_id = str(device_id)
        self._connections.setdefault(device_id, set()).add(ws)
        logger.info("ws.connect device=%s total=%d", device_id, self.total_clients())

    def disconnect(self, device_id: str, ws: WebSocket) -> None:
        device_id = str(device_id)
        sockets = self._connections.get(device_id, set())
        sockets.discard(ws)
        if not sockets:
            self._connections.pop(device_id, None)
        logger.info("ws.disconnect device=%s total=%d", device_id, self.total_clients())

    async def broadcast(self, device_id: str, values: dict, ts: str) -> None:
        await self.broadcast_json(str(device_id), {
            "type": "telemetry", "device_id": str(device_id),
            "values": values, "ts": ts,
        })

    async def broadcast_json(self, device_id: str, payload: dict) -> None:
        sockets = self._connections.get(str(device_id), set())
        if not sockets:
            return
        msg  = json.dumps(payload)
        dead = []
        for ws in list(sockets):
            try:
                await ws.send_text(msg)
            except Exception as exc:
                logger.debug("ws.send_failed device=%s: %s", device_id, exc)
                dead.append(ws)
        for ws in dead:
            sockets.discard(ws)

    def total_clients(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def active_devices(self) -> int:
        return len(self._connections)

    def summary(self) -> dict:
        return {k: len(v) for k, v in self._connections.items()}


# ── Redis-backed manager (multi-worker) ──────────────────────────────────────

class RedisManager:
    """
    Multi-worker WebSocket manager via Redis pub/sub.

    Each worker instance subscribes to device channels in Redis.
    On ingest, any worker publishes to Redis — all workers with connected
    clients for that device receive the message and push to their local sockets.

    Setup: set REDIS_URL=redis://your-redis-host:6379
    """

    CHANNEL_PREFIX = "device:"

    def __init__(self, redis_url: str):
        self._redis_url   = redis_url
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._pub          = None   # redis publish client
        self._sub          = None   # redis subscribe client
        self._listener_task = None

    async def startup(self) -> None:
        """Called once at app startup. Connects to Redis and starts listener."""
        import redis.asyncio as aioredis
        self._pub = aioredis.from_url(self._redis_url, decode_responses=True)
        self._sub = aioredis.from_url(self._redis_url, decode_responses=True)
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("RedisManager started redis_url=%s", self._redis_url)

    async def shutdown(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self._pub:
            await self._pub.aclose()
        if self._sub:
            await self._sub.aclose()

    async def _listen(self) -> None:
        """
        Subscribes to a pattern channel and pushes received messages
        to local WebSocket clients. Runs for the lifetime of the process.

        Reconnect logic: if the Redis connection drops, waits with
        exponential backoff (1s → 2s → 4s → … capped at 30s) then
        re-subscribes. Stops only when the manager is shut down.
        """
        backoff = 1.0
        while True:
            try:
                # Re-create sub client on each reconnect attempt so we get
                # a fresh connection rather than reusing a broken socket.
                import redis.asyncio as aioredis
                self._sub = aioredis.from_url(
                    self._redis_url, decode_responses=True
                )
                pubsub = self._sub.pubsub()
                await pubsub.psubscribe(f"{self.CHANNEL_PREFIX}*")
                logger.info("RedisManager: listening on %s*", self.CHANNEL_PREFIX)
                backoff = 1.0  # reset backoff on successful connect

                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    try:
                        channel   = message["channel"]
                        device_id = channel[len(self.CHANNEL_PREFIX):]
                        sockets   = self._connections.get(device_id, set())
                        if not sockets:
                            continue
                        msg  = message["data"]
                        dead = []
                        for ws in list(sockets):
                            try:
                                await ws.send_text(msg)
                            except Exception:
                                dead.append(ws)
                        for ws in dead:
                            sockets.discard(ws)
                    except Exception as exc:
                        logger.warning("RedisManager listener error: %s", exc)

            except asyncio.CancelledError:
                # Shutdown requested — exit cleanly
                logger.info("RedisManager listener cancelled")
                return
            except Exception as exc:
                logger.warning(
                    "RedisManager connection lost: %s — reconnecting in %.0fs",
                    exc, backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, 30.0)

    def connect(self, device_id: str, ws: WebSocket) -> None:
        device_id = str(device_id)
        self._connections.setdefault(device_id, set()).add(ws)
        logger.info("ws.connect device=%s total=%d", device_id, self.total_clients())

    def disconnect(self, device_id: str, ws: WebSocket) -> None:
        device_id = str(device_id)
        sockets = self._connections.get(device_id, set())
        sockets.discard(ws)
        if not sockets:
            self._connections.pop(device_id, None)
        logger.info("ws.disconnect device=%s total=%d", device_id, self.total_clients())

    async def broadcast(self, device_id: str, values: dict, ts: str) -> None:
        await self.broadcast_json(str(device_id), {
            "type": "telemetry", "device_id": str(device_id),
            "values": values, "ts": ts,
        })

    async def broadcast_json(self, device_id: str, payload: dict) -> None:
        if not self._pub:
            return
        try:
            await self._pub.publish(
                f"{self.CHANNEL_PREFIX}{device_id}",
                json.dumps(payload),
            )
        except Exception as exc:
            logger.warning("Redis publish failed device=%s: %s", device_id, exc)

    def total_clients(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def active_devices(self) -> int:
        return len(self._connections)

    def summary(self) -> dict:
        return {k: len(v) for k, v in self._connections.items()}


# ── Factory: pick manager based on config ────────────────────────────────────

def create_manager():
    """
    Returns the appropriate manager based on REDIS_URL env var.
    Called once at module load — result stored as module-level singleton.
    """
    from app.core.config import settings
    if settings.redis_enabled:
        logger.info("WebSocket: Redis manager (REDIS_URL configured)")
        return RedisManager(settings.REDIS_URL)
    else:
        logger.info("WebSocket: In-process manager (no REDIS_URL)")
        return InProcessManager()


manager = create_manager()
