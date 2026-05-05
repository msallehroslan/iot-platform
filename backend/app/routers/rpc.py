"""
app/routers/rpc.py — Device RPC endpoints (Phase 11: now uses rpc_service)

All command creation, dispatch and ACK goes through rpc_service.
This router is now a thin HTTP layer — no business logic lives here.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Any, Dict
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user, require_admin
from app.models.models import User
from app.schemas.schemas import RpcCommandCreate, RpcCommandOut
from app.services.rpc_service import (
    send_command,
    get_command_history,
    get_pending_for_device,
    acknowledge_command,
)

router = APIRouter(prefix="/rpc", tags=["RPC"])


# ── Device-facing endpoints (token auth) ─────────────────────────────────────

@router.get("/pending/{token}")
def get_pending_commands(token: str, db: Session = Depends(get_db)):
    """
    HTTP polling for devices that cannot use WebSocket.
    Authenticated by device token. Returns and marks pending commands as SENT.
    """
    cmds = get_pending_for_device(db, token)
    return [{"id": str(c.id), "method": c.method, "params": c.params} for c in cmds]


@router.post("/ack/{token}/{cmd_id}")
def ack_rpc_command(
    token: str,
    cmd_id: UUID,
    result: Dict[str, Any] = {},
    db: Session = Depends(get_db),
):
    """Device acknowledges execution with optional result payload."""
    cmd = acknowledge_command(db, token, cmd_id, result)
    return {"status": "ok", "cmd_id": str(cmd.id)}


# ── Dashboard endpoints (JWT auth) ────────────────────────────────────────────

@router.post("/{device_id}", response_model=RpcCommandOut, status_code=201)
async def send_rpc_command(
    device_id: UUID,
    body: RpcCommandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Send a command to a device.
    Validates, logs to audit trail, and dispatches via WebSocket.
    Queued in DB for HTTP polling fallback if device is offline.
    """
    return await send_command(
        db,
        device_id    = device_id,
        method       = body.method,
        params       = body.params or {},
        current_user = current_user,
        source       = "dashboard",
    )


@router.get("/{device_id}", response_model=List[RpcCommandOut])
def list_rpc_commands(
    device_id: UUID,
    status: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    List RPC command history for a device.
    Stale PENDING commands are auto-marked TIMEOUT before returning.
    """
    return get_command_history(db, device_id, current_user, status=status, limit=limit)
