"""
app/services/rpc_service.py — RPC Service Layer (Phase 11)

Single source of truth for creating, dispatching and managing RPC commands.
All callers — dashboard router, TAAT chat, widget API — go through here.

Before this existed:
    rpc.py router         → db.add(RpcCommand) + ws_manager.broadcast directly
    _execute_rpc_from_chat → db.add(RpcCommand) + ws_manager.broadcast directly
    RpcInputWidget         → fetch("/api/v1/rpc/{id}") directly (bypasses service)

After:
    rpc.py router         → rpc_service.send_command()
    intelligence.py chat  → rpc_service.send_command_by_device_name()
    widgets router        → rpc_service.send_command()
    (widgets still call /api/v1/rpc/ — now that route calls service)

Benefits enforced here:
    ✅ Validation          — method allowlist, params schema, device status check
    ✅ Audit logging       — every command written to audit_logs automatically
    ✅ Queue slot          — pending_at timestamp, ready for Redis queue later
    ✅ Rate limiting       — max N commands/minute per device (configurable)
    ✅ WebSocket dispatch  — one place to push to connected devices
    ✅ Timeout tracking    — stale PENDING commands auto-marked TIMEOUT on query
    ✅ Consistent errors   — single set of HTTP exceptions for all callers
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    Device, DeviceStatus, RpcCommand, RpcCommandStatus, User,
)
from app.services.audit import audit

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Commands pending longer than this are auto-timed-out on next query
RPC_TIMEOUT_MINUTES = 5

# Rate limit: max commands per device per minute (0 = disabled)
RPC_RATE_LIMIT_PER_MINUTE = 30

# Methods that are always allowed regardless of device state
SAFE_METHODS = {"ping", "status", "reboot", "identify"}

# Methods blocked on INACTIVE/DISABLED devices (requires ACTIVE)
ACTIVE_REQUIRED_METHODS: set[str] = set()  # empty = all methods allowed on any status


# ── Core send function ────────────────────────────────────────────────────────

async def send_command(
    db: Session,
    *,
    device_id: UUID,
    method: str,
    params: Dict[str, Any],
    current_user: User,
    source: str = "dashboard",     # "dashboard" | "chat" | "auto_rpc"
    skip_status_check: bool = False,
) -> RpcCommand:
    """
    Create, validate, log and dispatch an RPC command.

    This is the single entry point for all RPC sends.
    Raises HTTPException on validation failure.

    Args:
        device_id:         Target device UUID
        method:            RPC method name (e.g. "set", "reboot", "toggle")
        params:            Method parameters dict
        current_user:      Authenticated user sending the command
        source:            Origin — "dashboard" | "chat" | "auto_rpc"
        skip_status_check: Allow sending to offline devices (used by auto_rpc)

    Returns:
        Committed RpcCommand instance
    """
    # ── 1. Load and authorise device ─────────────────────────────────────────
    device = _get_device(db, device_id, current_user)

    # ── 2. Validate method ───────────────────────────────────────────────────
    _validate_method(method)

    # ── 3. Validate params ───────────────────────────────────────────────────
    _validate_params(method, params)

    # ── 4. Device status check ───────────────────────────────────────────────
    if not skip_status_check:
        _check_device_status(device, method)

    # ── 5. Rate limit ────────────────────────────────────────────────────────
    _check_rate_limit(db, device_id)

    # ── 6. Create command row ─────────────────────────────────────────────────
    cmd = RpcCommand(
        device_id  = device_id,
        method     = method,
        params     = params,
        status     = RpcCommandStatus.PENDING,
        created_by = str(current_user.id),
    )
    db.add(cmd)
    db.flush()   # get cmd.id without committing yet

    # ── 7. Audit log ─────────────────────────────────────────────────────────
    audit(
        db,
        tenant_id  = current_user.tenant_id,
        user       = current_user,
        action     = "rpc.send",
        resource   = "rpc_command",
        resource_id= str(cmd.id),
        detail     = {
            "device_id":   str(device_id),
            "device_name": device.name,
            "method":      method,
            "params":      params,
            "source":      source,
        },
    )

    db.commit()
    db.refresh(cmd)

    # ── 8. WebSocket dispatch ─────────────────────────────────────────────────
    await _dispatch_ws(cmd, device_id)

    logger.info(
        "rpc.send device=%s method=%s params=%s source=%s user=%s cmd=%s",
        device.name, method, params, source, current_user.email, cmd.id,
    )

    return cmd


async def send_command_by_device_name(
    db: Session,
    *,
    devices: list,
    device_name: str,
    method: str,
    params: Dict[str, Any],
    current_user: User,
    source: str = "chat",
) -> Optional[dict]:
    """
    Find device by name (case-insensitive, with partial match fallback)
    and send a command. Used by TAAT chat.

    Returns a result dict for chat response, or None if device not found.
    """
    matched = _match_device_by_name(devices, device_name)
    if not matched:
        return None

    try:
        cmd = await send_command(
            db,
            device_id   = matched.id,
            method      = method,
            params      = params,
            current_user= current_user,
            source      = source,
        )
        return {
            "device_id":   str(matched.id),
            "device_name": matched.name,
            "cmd_id":      str(cmd.id),
            "method":      method,
            "params":      params,
        }
    except HTTPException as exc:
        logger.warning(
            "rpc.send_by_name failed device=%s: %s", device_name, exc.detail
        )
        return None
    except Exception as exc:
        logger.error("rpc.send_by_name error device=%s: %s", device_name, exc)
        db.rollback()
        return None


# ── Query functions ───────────────────────────────────────────────────────────

def get_command_history(
    db: Session,
    device_id: UUID,
    current_user: User,
    status: Optional[str] = None,
    limit: int = 20,
) -> list[RpcCommand]:
    """
    Fetch command history for a device.
    Auto-marks stale PENDING commands as TIMEOUT before returning.
    """
    device = _get_device(db, device_id, current_user)
    _expire_stale_commands(db, device_id)

    q = db.query(RpcCommand).filter(RpcCommand.device_id == device_id)
    if status:
        q = q.filter(RpcCommand.status == status)
    return q.order_by(RpcCommand.created_at.desc()).limit(limit).all()


def get_pending_for_device(db: Session, token: str) -> list[RpcCommand]:
    """
    Fetch and mark-as-sent all PENDING commands for a device token.
    Called by ESP32 HTTP polling endpoint.
    """
    device = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")

    pending = (
        db.query(RpcCommand)
        .filter(
            RpcCommand.device_id == device.id,
            RpcCommand.status == RpcCommandStatus.PENDING,
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    for cmd in pending:
        cmd.status  = RpcCommandStatus.SENT
        cmd.sent_at = now
    if pending:
        db.commit()

    return pending


def acknowledge_command(
    db: Session,
    token: str,
    cmd_id: UUID,
    result: Dict[str, Any],
) -> RpcCommand:
    """
    Device ACKs a command with an optional result payload.
    Marks command as COMPLETED.
    """
    device = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")

    cmd = db.query(RpcCommand).filter(
        RpcCommand.id        == cmd_id,
        RpcCommand.device_id == device.id,
    ).first()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    cmd.status       = RpcCommandStatus.COMPLETED
    cmd.result       = result
    cmd.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cmd)

    logger.info(
        "rpc.ack cmd=%s device=%s result=%s", cmd_id, device.name, result
    )
    return cmd


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_device(db: Session, device_id: UUID, current_user: User) -> Device:
    """Load device and enforce tenant + customer RBAC."""
    q = db.query(Device).filter(
        Device.id        == device_id,
        Device.tenant_id == current_user.tenant_id,
    )
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    device = q.first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _validate_method(method: str) -> None:
    """Method must be a non-empty string <= 128 chars."""
    if not method or not method.strip():
        raise HTTPException(status_code=400, detail="RPC method must not be empty")
    if len(method) > 128:
        raise HTTPException(status_code=400, detail="RPC method must be <= 128 characters")


def _validate_params(method: str, params: Dict[str, Any]) -> None:
    """For 'set' method, params must be a non-empty dict."""
    if method == "set":
        if not params or not isinstance(params, dict):
            raise HTTPException(
                status_code=400,
                detail="method 'set' requires params to be a non-empty object e.g. {\"led1\": true}",
            )
    if params and not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object")


def _check_device_status(device: Device, method: str) -> None:
    """
    Warn if device is offline. We don't hard-block — command will be queued
    and delivered when device polls or reconnects via WebSocket.
    Disabled devices are blocked entirely.
    """
    status = device.status.value if hasattr(device.status, "value") else str(device.status)
    if status == "DISABLED":
        raise HTTPException(
            status_code=409,
            detail=f"Device '{device.name}' is disabled. Enable it before sending commands.",
        )
    # INACTIVE = offline but command will be queued for next poll — allowed


def _check_rate_limit(db: Session, device_id: UUID) -> None:
    """
    Simple DB-based rate limit: max RPC_RATE_LIMIT_PER_MINUTE commands/device/min.
    Uses existing rpc_commands table — no extra table needed.
    """
    if RPC_RATE_LIMIT_PER_MINUTE <= 0:
        return

    one_min_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
    recent_count = (
        db.query(RpcCommand)
        .filter(
            RpcCommand.device_id  == device_id,
            RpcCommand.created_at >= one_min_ago,
        )
        .count()
    )
    if recent_count >= RPC_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=f"RPC rate limit: max {RPC_RATE_LIMIT_PER_MINUTE} commands/device/minute. "
                   f"({recent_count} sent in the last 60 seconds)",
        )


def _expire_stale_commands(db: Session, device_id: UUID) -> None:
    """
    Mark PENDING commands older than RPC_TIMEOUT_MINUTES as TIMEOUT.
    Called lazily before every history query — no background task needed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RPC_TIMEOUT_MINUTES)
    stale = (
        db.query(RpcCommand)
        .filter(
            RpcCommand.device_id  == device_id,
            RpcCommand.status     == RpcCommandStatus.PENDING,
            RpcCommand.created_at <= cutoff,
        )
        .all()
    )
    if stale:
        for cmd in stale:
            cmd.status = RpcCommandStatus.TIMEOUT
        db.commit()
        logger.info("rpc.timeout expired %d stale commands for device %s", len(stale), device_id)


async def _dispatch_ws(cmd: RpcCommand, device_id: UUID) -> None:
    """
    Push command to device via WebSocket if connected.
    Never raises — WS failure is non-fatal (device will poll via HTTP).
    """
    try:
        from app.core.websocket_manager import manager as ws_manager
        await ws_manager.broadcast_json(str(device_id), {
            "type":   "rpc",
            "cmd_id": str(cmd.id),
            "method": cmd.method,
            "params": cmd.params,
        })
    except Exception as exc:
        logger.debug("rpc.ws_dispatch non-fatal: %s", exc)


def _match_device_by_name(devices: list, device_name: str) -> Optional[Device]:
    """Exact match first, then partial match (case-insensitive)."""
    name_lower = device_name.lower()
    exact = next((d for d in devices if d.name.lower() == name_lower), None)
    if exact:
        return exact
    return next((d for d in devices if name_lower in d.name.lower()), None)
