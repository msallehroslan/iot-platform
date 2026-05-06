"""
app/services/scheduled_rpc_service.py — Scheduled RPC (Phase 11)

Clean rewrite. The old version stored scheduled time in the result JSON field
and had no background runner. This version:

  - Uses dedicated columns: scheduled_for + repeat_interval_hours
  - Status flow: SCHEDULED → PENDING (when dispatcher fires) → SENT/COMPLETED/FAILED
  - One-shot and repeating schedules both supported
  - Background dispatcher runs every 30s via main.py asyncio task
  - All writes go through rpc_service.send_command() — validation + audit included
  - TAAT chat calls schedule_by_device_name() for natural language scheduling

Natural language examples handled by TAAT:
    "turn off pump at midnight"        → one-shot at 00:00 UTC tonight
    "restart sensor every 6 hours"     → repeat_interval_hours=6
    "set led1 to false tomorrow at 9"  → one-shot at 09:00 UTC tomorrow
    "cancel scheduled commands"        → cancel all SCHEDULED for device
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    Device, RpcCommand, RpcCommandStatus,
)
from app.services.audit import audit

logger = logging.getLogger(__name__)

# Human-entered schedule times are interpreted in this timezone, then stored in UTC.
# Malaysia users expect "9:39am" to mean Malaysia time, not 09:39 UTC.
SCHEDULE_TIMEZONE = os.getenv("SCHEDULE_TIMEZONE", "Asia/Kuala_Lumpur")


def _schedule_tz():
    try:
        return ZoneInfo(SCHEDULE_TIMEZONE)
    except Exception:
        logger.warning("Invalid SCHEDULE_TIMEZONE=%s; falling back to UTC", SCHEDULE_TIMEZONE)
        return timezone.utc


def _to_utc(dt: datetime) -> datetime:
    """Normalise any datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_schedule_tz())
    return dt.astimezone(timezone.utc)


def _now_local() -> datetime:
    return datetime.now(_schedule_tz())


# ── Schedule CRUD ─────────────────────────────────────────────────────────────

def schedule_command(
    db: Session,
    *,
    device_id: UUID,
    method: str,
    params: dict,
    scheduled_for: datetime,
    repeat_interval_hours: Optional[float] = None,
    current_user,
    source: str = "chat",
) -> RpcCommand:
    """
    Create a SCHEDULED RpcCommand.
    The background dispatcher fires it when scheduled_for is reached.
    """
    # Validate device access
    device = _get_device(db, device_id, current_user)

    # Interpret naive human-entered times in SCHEDULE_TIMEZONE, then store UTC.
    # This prevents "9:39am" in Malaysia from being stored as 09:39 UTC.
    scheduled_for = _to_utc(scheduled_for)
    now = datetime.now(timezone.utc)

    # Validate scheduled time is in the future
    if scheduled_for <= now:
        raise HTTPException(
            status_code=400,
            detail=f"scheduled_for must be in the future (got {scheduled_for.isoformat()})"
        )

    # Validate repeat interval
    if repeat_interval_hours is not None:
        if repeat_interval_hours < 0.5:
            raise HTTPException(status_code=400, detail="repeat_interval_hours must be >= 0.5")
        if repeat_interval_hours > 8760:  # 1 year
            raise HTTPException(status_code=400, detail="repeat_interval_hours must be <= 8760")

    cmd = RpcCommand(
        device_id             = device_id,
        method                = method,
        params                = params,
        status                = RpcCommandStatus.SCHEDULED,
        created_by            = str(current_user.id),
        scheduled_for         = scheduled_for,
        repeat_interval_hours = repeat_interval_hours,
    )
    db.add(cmd)

    audit(
        db,
        tenant_id   = current_user.tenant_id,
        user        = current_user,
        action      = "rpc.schedule",
        resource    = "rpc_command",
        detail      = {
            "device_id":     str(device_id),
            "device_name":   device.name,
            "method":        method,
            "params":        params,
            "scheduled_for": scheduled_for.isoformat(),
            "repeat_hours":  repeat_interval_hours,
            "source":        source,
        },
    )

    db.commit()
    db.refresh(cmd)

    logger.info(
        "rpc.scheduled device=%s method=%s at=%s repeat=%sh cmd=%s",
        device.name, method, scheduled_for.isoformat(), repeat_interval_hours, cmd.id,
    )
    return cmd


async def schedule_by_device_name(
    db: Session,
    *,
    devices: list,
    device_name: str,
    method: str,
    params: dict,
    scheduled_for: datetime,
    repeat_interval_hours: Optional[float] = None,
    current_user,
    source: str = "taat_chat",
) -> Optional[dict]:
    """
    Find device by name and schedule a command. Used by TAAT chat.
    Returns result dict or None if device not found.
    """
    matched = _match_device_by_name(devices, device_name)
    if not matched:
        return None

    try:
        cmd = schedule_command(
            db,
            device_id             = matched.id,
            method                = method,
            params                = params,
            scheduled_for         = scheduled_for,
            repeat_interval_hours = repeat_interval_hours,
            current_user          = current_user,
            source                = source,
        )
        label = _humanise_schedule(scheduled_for, repeat_interval_hours)
        return {
            "cmd_id":        str(cmd.id),
            "device_id":     str(matched.id),
            "device_name":   matched.name,
            "method":        method,
            "params":        params,
            "scheduled_for": scheduled_for.isoformat(),
            "repeat_interval_hours": repeat_interval_hours,
            "human_label":   label,
            "is_scheduled":  True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("schedule_by_device_name failed: %s", exc)
        db.rollback()
        return None


def cancel_scheduled(
    db: Session,
    *,
    device_id: Optional[UUID] = None,
    cmd_id: Optional[UUID] = None,
    current_user,
) -> dict:
    """
    Cancel scheduled command(s).
    - cmd_id set → cancel one command
    - device_id set → cancel all SCHEDULED for that device
    - neither set → cancel all SCHEDULED for tenant
    """
    q = db.query(RpcCommand).filter(RpcCommand.status == RpcCommandStatus.SCHEDULED)

    if cmd_id:
        q = q.filter(RpcCommand.id == cmd_id)
    elif device_id:
        q = q.filter(RpcCommand.device_id == device_id)
    else:
        # Scope to tenant via devices
        from app.models.models import Device
        device_ids = [
            r[0] for r in db.query(Device.id)
            .filter(Device.tenant_id == current_user.tenant_id).all()
        ]
        q = q.filter(RpcCommand.device_id.in_(device_ids))

    cmds = q.all()
    count = len(cmds)
    for cmd in cmds:
        cmd.status = RpcCommandStatus.CANCELLED
        audit(
            db,
            tenant_id   = current_user.tenant_id,
            user        = current_user,
            action      = "rpc.cancel",
            resource    = "rpc_command",
            resource_id = str(cmd.id),
            detail      = {"source": "taat_chat"},
        )
    if cmds:
        db.commit()

    logger.info("rpc.cancelled %d scheduled commands by %s", count, current_user.email)
    return {"cancelled": count, "is_schedule_cancel": True}


def list_scheduled(
    db: Session,
    current_user,
    device_id: Optional[UUID] = None,
) -> dict:
    """List all SCHEDULED commands for the tenant."""
    from app.models.models import Device
    device_ids = [
        r[0] for r in db.query(Device.id)
        .filter(Device.tenant_id == current_user.tenant_id).all()
    ]

    q = db.query(RpcCommand).filter(
        RpcCommand.device_id.in_(device_ids),
        RpcCommand.status == RpcCommandStatus.SCHEDULED,
    ).order_by(RpcCommand.scheduled_for.asc())

    if device_id:
        q = q.filter(RpcCommand.device_id == device_id)

    cmds = q.limit(50).all()

    # Build device name lookup
    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    dev_map = {str(d.id): d.name for d in devices}

    rows = []
    for c in cmds:
        rows.append({
            "cmd_id":        str(c.id),
            "device_id":     str(c.device_id),
            "device_name":   dev_map.get(str(c.device_id), "?"),
            "method":        c.method,
            "params":        c.params,
            "scheduled_for": c.scheduled_for.isoformat() if c.scheduled_for else None,
            "repeat_interval_hours": c.repeat_interval_hours,
            "human_label":   _humanise_schedule(c.scheduled_for, c.repeat_interval_hours) if c.scheduled_for else "",
        })

    return {"count": len(rows), "scheduled": rows, "is_schedule_list": True}


# ── Background dispatcher ─────────────────────────────────────────────────────

async def dispatch_due_commands(db: Session) -> int:
    """
    Fire all SCHEDULED commands whose scheduled_for <= now.

    Important behaviour:
    - Scheduled rows stay as the original command history.
    - When due, the same row is promoted to PENDING.
    - WebSocket dispatch is attempted immediately.
    - HTTP polling still works as fallback: /rpc/pending/{token} will pick up PENDING.
    - Repeating commands create a fresh SCHEDULED row for the next fire time.
    """
    from app.core.websocket_manager import manager as ws_manager

    now = datetime.now(timezone.utc)
    logger.debug("scheduled_rpc.dispatch tick now=%s", now.isoformat())

    candidates = (
        db.query(RpcCommand)
        .filter(RpcCommand.status == RpcCommandStatus.SCHEDULED)
        .all()
    )

    due: list[RpcCommand] = []
    for cmd in candidates:
        if not cmd.scheduled_for:
            continue
        scheduled_for = _to_utc(cmd.scheduled_for)
        if scheduled_for <= now:
            due.append(cmd)

    if not due:
        logger.debug("scheduled_rpc.dispatch no due commands candidates=%d", len(candidates))
        return 0

    logger.info("scheduled_rpc.dispatch due=%d candidates=%d", len(due), len(candidates))

    dispatched = 0
    for cmd in due:
        try:
            device = db.query(Device).filter(Device.id == cmd.device_id).first()
            dev_name = device.name if device else str(cmd.device_id)

            # Promote the original scheduled command. Do not create a second duplicate command.
            cmd.status = RpcCommandStatus.PENDING
            cmd.sent_at = datetime.now(timezone.utc)

            # Flush immediately so polling endpoint sees update
            db.flush()

            # Immediate websocket delivery. Non-fatal because HTTP polling will deliver PENDING.
            try:
                await ws_manager.broadcast_json(str(cmd.device_id), {
                    "type": "rpc",
                    "cmd_id": str(cmd.id),
                    "method": cmd.method,
                    "params": cmd.params,
                    "source": "scheduled_rpc",
                })
                logger.info("scheduled_rpc.ws_dispatched cmd=%s device=%s", cmd.id, dev_name)
            except Exception as ws_exc:
                logger.debug("scheduled_rpc.ws_dispatch skipped cmd=%s: %s", cmd.id, ws_exc)

            # Repeating command: create the next SCHEDULED occurrence.
            if cmd.repeat_interval_hours:
                next_fire = now + timedelta(hours=cmd.repeat_interval_hours)
                next_cmd = RpcCommand(
                    device_id=cmd.device_id,
                    method=cmd.method,
                    params=cmd.params,
                    status=RpcCommandStatus.SCHEDULED,
                    created_by=cmd.created_by,
                    scheduled_for=next_fire,
                    repeat_interval_hours=cmd.repeat_interval_hours,
                )
                db.add(next_cmd)
                logger.info(
                    "scheduled_rpc.repeat_created original=%s next=%s device=%s repeat=%.2fh",
                    cmd.id, next_fire.isoformat(), dev_name, cmd.repeat_interval_hours,
                )

            # Memory event so TAAT report can show RECENT SCHEDULED ACTIONS EXECUTED.
            try:
                from app.services.taat_memory_service import save_memory
                if device and device.tenant_id:
                    fired_at_local = now.astimezone(_schedule_tz()).strftime("%Y-%m-%d %H:%M %Z")
                    content = (
                        f"SCHEDULED_DISPATCH: {cmd.method} {cmd.params} fired on {dev_name} "
                        f"at {fired_at_local} (cmd_id={cmd.id})"
                    )
                    save_memory(
                        db,
                        tenant_id=device.tenant_id,
                        memory_type="scheduled_dispatch",
                        content=content,
                        commit=False,
                    )
            except Exception as mem_exc:
                logger.debug("scheduled_rpc.memory skipped cmd=%s: %s", cmd.id, mem_exc)

            dispatched += 1
            logger.info(
                "scheduled_rpc.dispatched cmd=%s device=%s method=%s params=%s",
                cmd.id, dev_name, cmd.method, cmd.params,
            )

            # Commit immediately so ESP polling can fetch command
            try:
                db.commit()
            except Exception as commit_exc:
                logger.error(
                    "scheduled_rpc.partial_commit_failed cmd=%s: %s",
                    cmd.id,
                    commit_exc,
                )
                db.rollback()

        except Exception as exc:
            logger.error("scheduled_rpc.dispatch failed cmd=%s: %s", getattr(cmd, "id", "?"), exc)

    if dispatched:
        try:
            db.commit()
        except Exception as exc:
            logger.error("scheduled_rpc.dispatch commit failed: %s", exc)
            db.rollback()
            return 0

    return dispatched


# ── Time parsing helpers ──────────────────────────────────────────────────────

def parse_schedule_time(
    time_str: str,
    repeat_hours: Optional[float] = None,
) -> datetime:
    """
    Parse natural-language time strings into UTC datetime.

    Examples:
        "midnight"          → tonight 00:00 UTC
        "9am" / "09:00"    → today at 09:00 UTC (or tomorrow if past)
        "tomorrow at 9am"  → tomorrow 09:00 UTC
        "in 2 hours"       → now + 2h
        "+30m"             → now + 30min
        ISO string         → parsed directly
    """
    import re

    # Use local scheduling timezone for human phrases, then convert to UTC before returning.
    now_local = _now_local()
    s   = time_str.strip().lower()

    # ISO datetime — just parse
    try:
        dt = datetime.fromisoformat(s.replace("z", "+00:00"))
        return _to_utc(dt)
    except ValueError:
        pass

    # "in X hours/minutes"
    m = re.match(r"in\s+(\d+(?:\.\d+)?)\s*(h(?:ours?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)", s)
    if m:
        val, unit = float(m.group(1)), m.group(2)
        delta = timedelta(hours=val) if unit.startswith("h") else timedelta(minutes=val)
        return datetime.now(timezone.utc) + delta

    # "+Xh" / "+Xm"
    m = re.match(r"\+(\d+(?:\.\d+)?)(h|m)", s)
    if m:
        val, unit = float(m.group(1)), m.group(2)
        return datetime.now(timezone.utc) + (timedelta(hours=val) if unit == "h" else timedelta(minutes=val))

    # "midnight" / "noon"
    if "midnight" in s:
        base = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        target = base if base > now_local else base + timedelta(days=1)
        return _to_utc(target)
    if "noon" in s:
        base = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
        target = base if base > now_local else base + timedelta(days=1)
        return _to_utc(target)

    # "tomorrow at HH:MM" / "tomorrow at Xam"
    is_tomorrow = "tomorrow" in s
    s_clean = s.replace("tomorrow", "").replace("at", "").strip()

    # Parse time: "9am", "9:30pm", "21:00", "9:00"
    hour, minute = None, 0
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s_clean)
    if m:
        hour   = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        ampm   = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

    if hour is not None:
        base = (now_local + timedelta(days=1)) if is_tomorrow else now_local
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # If time is in the past today, roll to tomorrow
        if target <= now_local and not is_tomorrow:
            target += timedelta(days=1)
        return _to_utc(target)

    # Default: 1 hour from now
    logger.warning("Could not parse schedule time '%s', defaulting to +1h", time_str)
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _humanise_schedule(
    scheduled_for: Optional[datetime],
    repeat_hours: Optional[float],
) -> str:
    """Return a human-readable label like 'tonight at 00:00 · every 6h'."""
    if not scheduled_for:
        return ""
    scheduled_for = _to_utc(scheduled_for)
    now   = datetime.now(timezone.utc)
    delta = scheduled_for - now
    hours = delta.total_seconds() / 3600

    if hours < 1:
        time_part = f"in {int(delta.total_seconds() / 60)} min"
    elif hours < 24:
        time_part = scheduled_for.astimezone(_schedule_tz()).strftime("%H:%M %Z today")
    else:
        time_part = scheduled_for.astimezone(_schedule_tz()).strftime("%d %b %H:%M %Z")

    if repeat_hours:
        h = int(repeat_hours) if repeat_hours == int(repeat_hours) else repeat_hours
        return f"{time_part} · every {h}h"
    return time_part


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_device(db: Session, device_id: UUID, current_user) -> Device:
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


def _match_device_by_name(devices: list, name: str) -> Optional[Device]:
    name_lower = name.lower()
    return (
        next((d for d in devices if d.name.lower() == name_lower), None) or
        next((d for d in devices if name_lower in d.name.lower()), None)
    )
