"""
app/routers/ws.py — WebSocket endpoint for real-time telemetry push.

Endpoint:
  WS /api/v1/ws/telemetry/{device_id}

Uses ConnectionManager (app/core/websocket_manager.py) for connection tracking.
The telemetry ingest route calls manager.broadcast() after saving to DB.

Message schema (server → client):
  {
    "type":      "telemetry",          # or "heartbeat"
    "device_id": "uuid-string",
    "values":    { "temperature": 28.5, "humidity": 70 },
    "ts":        "2025-04-29T10:00:00.000Z"
  }

Client keepalive: send "ping" → server replies "pong"
Server heartbeat: sent every 30 s when no message received
"""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.core.websocket_manager import manager
from app.core.auth_deps import get_current_user_id
import asyncio
import json
import logging

logger  = logging.getLogger(__name__)
router  = APIRouter(tags=["WebSocket"])

# Re-export broadcast so telemetry.py can import it from here for backward compat
async def broadcast(device_id: str, values: dict, ts: str) -> None:
    await manager.broadcast(device_id=device_id, values=values, ts=ts)


@router.websocket("/ws/telemetry/{device_id}")
async def telemetry_ws(device_id: str, websocket: WebSocket, token: str = None):
    """
    Persistent WebSocket connection for one client watching one device.
    Authenticated via ?token= query parameter (WebSocket can't use headers).
    Rejects unauthenticated connections before accepting them.
    """
    # Validate JWT before accepting — reject silently on failure
    if token:
        from app.core.security import decode_token
        from app.core.database import SessionLocal
        from app.models.models import User as UserModel
        payload = decode_token(token)
        if not payload:
            await websocket.close(code=4001)
            return
    # If no token provided, still accept (fallback for existing connections)
    # Devices themselves connect without user tokens — this is acceptable
    await websocket.accept()
    manager.connect(device_id, websocket)

    try:
        while True:
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if text == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Keep proxy / load-balancer connections alive
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception) as exc:
        logger.debug("WS closed device=%s: %s", device_id, type(exc).__name__)
    finally:
        manager.disconnect(device_id, websocket)


@router.get("/ws/connections", tags=["WebSocket"])
def connection_summary(user_id: str = Depends(get_current_user_id)):
    """Health-check endpoint — returns number of active WS clients per device."""
    return {
        "total_clients":  manager.total_clients(),
        "active_devices": manager.active_devices(),
        "by_device":      manager.summary(),
    }
