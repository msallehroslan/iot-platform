"""
app/routers/rpc.py — Device RPC (Remote Procedure Call) endpoints.

POST /rpc/{device_id}          — send command from dashboard to device
GET  /rpc/{device_id}          — list command history (dashboard)
GET  /rpc/pending/{token}      — device polls for pending commands (device token auth)
POST /rpc/ack/{token}/{cmd_id} — device ACKs command with result
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List, Optional, Any, Dict
from uuid import UUID
from datetime import datetime, timezone

from app.core.database import get_db
from app.services.audit import audit
from app.core.auth_deps import get_current_user, assert_device_access, require_admin
from app.models.models import Device, User, RpcCommand, RpcCommandStatus
from app.schemas.schemas import RpcCommandCreate, RpcCommandOut

router = APIRouter(prefix="/rpc", tags=["RPC"])


# ── Static routes before /{device_id} ────────────────────────────────────────

@router.get("/pending/{token}")
def get_pending_commands(token: str, db: Session = Depends(get_db)):
    """
    HTTP polling for devices that cannot use WebSocket.
    Authenticated by device token. Returns and marks pending commands as SENT.
    """
    device = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")

    pending = db.query(RpcCommand).filter(
        and_(
            RpcCommand.device_id == device.id,
            RpcCommand.status == RpcCommandStatus.PENDING,
        )
    ).all()

    for cmd in pending:
        cmd.status = RpcCommandStatus.SENT
        cmd.sent_at = datetime.now(timezone.utc)
    db.commit()

    return [{"id": str(c.id), "method": c.method, "params": c.params} for c in pending]


@router.post("/ack/{token}/{cmd_id}")
def ack_rpc_command(
    token: str,
    cmd_id: UUID,
    result: Dict[str, Any] = {},
    db: Session = Depends(get_db),
):
    """Device acknowledges execution with optional result payload."""
    device = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")

    cmd = db.query(RpcCommand).filter(
        and_(RpcCommand.id == cmd_id, RpcCommand.device_id == device.id)
    ).first()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    cmd.status = RpcCommandStatus.COMPLETED
    cmd.result = result
    cmd.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


# ── Dynamic routes ────────────────────────────────────────────────────────────

@router.post("/{device_id}", response_model=RpcCommandOut, status_code=201)
async def send_rpc_command(
    device_id: UUID,
    body: RpcCommandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Send a command to a device. Stored in DB + broadcast via WebSocket."""
    device = db.query(Device).filter(Device.id == device_id).first()
    assert_device_access(device, current_user)

    cmd = RpcCommand(
        device_id=device_id,
        method=body.method,
        params=body.params or {},
        status=RpcCommandStatus.PENDING,
        created_by=str(current_user.id),
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="rpc.send", resource="rpc_command", resource_id=str(cmd.id),
          detail={"device_id": str(device_id), "method": body.method, "params": body.params}, commit=True)

    # Push to device via WebSocket immediately if connected
    try:
        from app.core.websocket_manager import manager as ws_manager
        await ws_manager.broadcast_json(str(device_id), {
            "type":   "rpc",
            "cmd_id": str(cmd.id),
            "method": cmd.method,
            "params": cmd.params,
        })
    except Exception:
        pass  # WS failure never blocks command creation

    return cmd


@router.get("/{device_id}", response_model=List[RpcCommandOut])
def list_rpc_commands(
    device_id: UUID,
    status: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """List RPC command history for a device."""
    device = db.query(Device).filter(Device.id == device_id).first()
    assert_device_access(device, current_user)
    q = db.query(RpcCommand).filter(RpcCommand.device_id == device_id)
    if status:
        q = q.filter(RpcCommand.status == status)
    return q.order_by(RpcCommand.created_at.desc()).limit(limit).all()
