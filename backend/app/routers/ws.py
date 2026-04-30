"""
app/routers/ws.py — WebSocket endpoint for real-time telemetry push.

FIX 4: Authentication is REQUIRED. Connections without a valid JWT are
rejected with close code 4001 before accept(). Tenant ownership of the
device_id is verified — wrong tenant gets close code 4003.
"""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.core.websocket_manager import manager
from app.core.auth_deps import get_current_user_id
import asyncio
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])


async def broadcast(device_id: str, values: dict, ts: str) -> None:
    await manager.broadcast(device_id=device_id, values=values, ts=ts)


@router.websocket("/ws/telemetry/{device_id}")
async def telemetry_ws(device_id: str, websocket: WebSocket, token: str = None):
    from app.core.security import decode_token
    from app.core.database import SessionLocal
    from app.models.models import User as UserModel, Device

    # ── Require token — reject before accept ──────────────────────────────────
    if not token:
        await websocket.close(code=4001)
        return

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001)
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001)
        return

    # ── Verify user is active and owns the device ─────────────────────────────
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(
            UserModel.id == user_id,
            UserModel.is_active == True,
        ).first()
        if not user:
            await websocket.close(code=4001)
            return

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device or device.tenant_id != user.tenant_id:
            await websocket.close(code=4003)
            return
    finally:
        db.close()

    # ── Accept and register ───────────────────────────────────────────────────
    await websocket.accept()
    manager.connect(device_id, websocket)

    try:
        while True:
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if text == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception) as exc:
        logger.debug("WS closed device=%s: %s", device_id, type(exc).__name__)
    finally:
        manager.disconnect(device_id, websocket)


@router.get("/ws/connections", tags=["WebSocket"])
def connection_summary(user_id: str = Depends(get_current_user_id)):
    return {
        "total_clients":  manager.total_clients(),
        "active_devices": manager.active_devices(),
        "by_device":      manager.summary(),
    }
