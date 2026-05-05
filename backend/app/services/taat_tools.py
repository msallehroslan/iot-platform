"""
app/services/taat_tools.py — TAAT v2 Tool Registry

14 tool functions that wrap data_service + rpc_service + DB.
These are called by the planner — never directly from the router.

TAAT and widgets share the same data source (data_service) so they
can never disagree: if TAAT says "WARNING", the widget shows "WARNING".

Tools:
    READ (8):
        get_devices()           → device list with status
        get_latest_telemetry()  → current key→value snapshot
        get_active_alarms()     → active alarm list
        get_device_health()     → health score + label
        get_anomalies()         → anomaly scores per key
        get_baseline()          → baseline ranges for current hour
        get_rpc_history()       → recent commands + results
        get_audit_log()         → recent audit trail

    WRITE (6):
        create_rule()           → create threshold rule
        update_rule()           → update threshold rule
        delete_rule()           → delete threshold rule(s)
        send_rpc()              → send RPC command via rpc_service
        ack_alarm()             → acknowledge alarm(s)
        clear_alarm()           → clear alarm(s)
        generate_report()       → daily summary (read-only)
        save_memory()           → write to agent_memory

Risk levels (used by safety guard):
    LOW    → execute immediately
    MEDIUM → execute if TENANT_ADMIN or TENANT_USER
    HIGH   → require explicit confirmation
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.data_service import (
    get_latest_telemetry,
    get_active_alarms,
    get_health_summary,
    get_anomaly_summary,
    get_baseline_now,
    get_key_intelligence,
)

logger = logging.getLogger(__name__)

# ── Risk classification ───────────────────────────────────────────────────────

TOOL_RISK = {
    # Read tools — always LOW
    "get_devices":          "LOW",
    "get_latest_telemetry": "LOW",
    "get_active_alarms":    "LOW",
    "get_device_health":    "LOW",
    "get_anomalies":        "LOW",
    "get_baseline":         "LOW",
    "get_rpc_history":      "LOW",
    "get_audit_log":        "LOW",
    "generate_report":      "LOW",
    "get_memory":           "LOW",
    # Write tools
    "create_rule":          "MEDIUM",
    "update_rule":          "MEDIUM",
    "delete_rule":          "MEDIUM",  # single delete; delete_all escalates to HIGH below
    "ack_alarm":            "MEDIUM",
    "clear_alarm":          "MEDIUM",
    "send_rpc":             "MEDIUM",   # escalates to HIGH for bulk/all-devices
    "save_memory":          "LOW",
}

HIGH_RISK_PATTERNS = [
    "delete all",
    "clear all",
    "turn off all",
    "disable all",
    "remove all",
    "restart all",
]


def assess_risk(tool_name: str, params: dict, message: str = "") -> str:
    """
    Determine risk level for a tool call.
    Escalates to HIGH for bulk/destructive patterns.
    """
    base = TOOL_RISK.get(tool_name, "MEDIUM")
    if base == "HIGH":
        return "HIGH"
    msg_lower = message.lower()
    if any(p in msg_lower for p in HIGH_RISK_PATTERNS):
        return "HIGH"
    if tool_name == "delete_rule" and params.get("delete_all"):
        return "HIGH"
    if tool_name == "send_rpc" and not params.get("device_name"):
        return "HIGH"   # no specific device = could be broadcast
    return base


# ── READ tools ────────────────────────────────────────────────────────────────

def tool_get_devices(db: Session, current_user) -> dict:
    """Return all devices scoped to this tenant/customer."""
    from app.models.models import Device
    q = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    devices = q.limit(50).all()
    return {
        "count": len(devices),
        "devices": [
            {
                "id":     str(d.id),
                "name":   d.name,
                "type":   d.device_type,
                "status": d.status.value if hasattr(d.status, "value") else str(d.status),
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            }
            for d in devices
        ],
    }


def tool_get_latest_telemetry(db: Session, device_id: str) -> dict:
    """Return current key→value snapshot for a device."""
    return get_latest_telemetry(db, device_id)


def tool_get_active_alarms(db: Session, device_id: str) -> dict:
    """Return active alarms for a device."""
    return get_active_alarms(db, device_id)


def tool_get_device_health(db: Session, device_id: str) -> dict:
    """Return health score and component breakdown."""
    return get_health_summary(db, device_id)


def tool_get_anomalies(db: Session, device_id: str, hours: int = 24) -> dict:
    """Return anomaly scores and most anomalous key."""
    return get_anomaly_summary(db, device_id, hours=hours)


def tool_get_baseline(db: Session, device_id: str, key: Optional[str] = None) -> dict:
    """Return current-hour baselines, optionally filtered to one key."""
    baseline = get_baseline_now(db, device_id)
    if key and baseline.get("status") == "active":
        filtered = {k: v for k, v in baseline.get("keys", {}).items() if k == key}
        return {**baseline, "keys": filtered}
    return baseline


def tool_get_key_intelligence(
    db: Session, device_id: str, key: str, device=None
) -> dict:
    """Return full per-key enriched intelligence (Gap 1 standard schema)."""
    return get_key_intelligence(db, device_id, key, device=device)


def tool_get_rpc_history(db: Session, device_id: str, limit: int = 10) -> dict:
    """Return recent RPC command history for a device."""
    from app.models.models import RpcCommand
    cmds = (
        db.query(RpcCommand)
        .filter(RpcCommand.device_id == device_id)
        .order_by(RpcCommand.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(cmds),
        "commands": [
            {
                "id":           str(c.id),
                "method":       c.method,
                "params":       c.params,
                "status":       c.status if isinstance(c.status, str) else c.status.value,
                "result":       c.result,
                "created_at":   c.created_at.isoformat() if c.created_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            }
            for c in cmds
        ],
    }


def tool_get_audit_log(db: Session, current_user, limit: int = 20) -> dict:
    """Return recent audit trail for this tenant."""
    from app.models.models import AuditLog
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == current_user.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(rows),
        "entries": [
            {
                "action":     r.action,
                "resource":   r.resource,
                "user_email": r.user_email,
                "detail":     r.detail,
                "ts":         r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def tool_get_memory(db: Session, current_user, memory_type: Optional[str] = None) -> dict:
    """Read agent memory for this tenant."""
    try:
        from app.models.models import AgentMemory
        q = db.query(AgentMemory).filter(
            AgentMemory.tenant_id == current_user.tenant_id
        )
        if memory_type:
            q = q.filter(AgentMemory.memory_type == memory_type)
        rows = q.order_by(AgentMemory.created_at.desc()).limit(50).all()
        return {
            "count": len(rows),
            "memories": [
                {
                    "id":          str(r.id),
                    "type":        r.memory_type,
                    "content":     r.content,
                    "created_at":  r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    except Exception:
        return {"count": 0, "memories": []}


# ── WRITE tools ───────────────────────────────────────────────────────────────

async def tool_send_rpc(
    db: Session, current_user, device_name: str, method: str, params: dict
) -> dict:
    """Send RPC command via rpc_service (handles validation + audit + WS)."""
    from app.services.rpc_service import send_command_by_device_name
    from app.models.models import Device

    devices = db.query(Device).filter(
        Device.tenant_id == current_user.tenant_id
    ).all()

    result = await send_command_by_device_name(
        db,
        devices     = devices,
        device_name = device_name,
        method      = method,
        params      = params,
        current_user= current_user,
        source      = "taat_v2",
    )
    if result:
        return {"success": True, **result}
    return {"success": False, "reason": f"Device '{device_name}' not found or command failed"}


def tool_ack_alarm(db: Session, current_user, device_id: Optional[str] = None,
                   severity: Optional[str] = None) -> dict:
    """Acknowledge active alarms, optionally filtered by device/severity."""
    from app.models.models import Alarm, AlarmStatus, Device
    from app.services.audit import audit

    device_ids = _resolve_device_ids(db, current_user, device_id)
    q = db.query(Alarm).filter(
        Alarm.device_id.in_(device_ids),
        Alarm.status == AlarmStatus.ACTIVE_UNACK,
    )
    if severity:
        q = q.filter(Alarm.severity == severity.upper())
    alarms = q.all()
    now = datetime.now(timezone.utc)
    for a in alarms:
        a.status = AlarmStatus.ACTIVE_ACK
        a.ack_ts = now
        a.ack_by = str(current_user.id)
    if alarms:
        db.commit()
        audit(db, tenant_id=current_user.tenant_id, user=current_user,
              action="alarm.ack_bulk", resource="alarm",
              detail={"count": len(alarms), "source": "taat_v2"}, commit=True)
    return {"success": True, "count": len(alarms), "action": "acknowledged"}


def tool_clear_alarm(db: Session, current_user, device_id: Optional[str] = None,
                     severity: Optional[str] = None) -> dict:
    """Clear active alarms, optionally filtered by device/severity."""
    from app.models.models import Alarm, AlarmStatus
    from app.services.audit import audit

    device_ids = _resolve_device_ids(db, current_user, device_id)
    q = db.query(Alarm).filter(
        Alarm.device_id.in_(device_ids),
        Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
    )
    if severity:
        q = q.filter(Alarm.severity == severity.upper())
    alarms = q.all()
    now = datetime.now(timezone.utc)
    for a in alarms:
        a.status = AlarmStatus.CLEARED_ACK if a.ack_ts else AlarmStatus.CLEARED_UNACK
        a.clear_ts = now
        a.cleared_by = str(current_user.id)
    if alarms:
        db.commit()
        audit(db, tenant_id=current_user.tenant_id, user=current_user,
              action="alarm.clear_bulk", resource="alarm",
              detail={"count": len(alarms), "source": "taat_v2"}, commit=True)
    return {"success": True, "count": len(alarms), "action": "cleared"}


def tool_create_rule(
    db: Session, current_user, devices: list,
    key: str, condition: str, threshold: float,
    severity: str = "WARNING", device_name: Optional[str] = None,
    alarm_type: Optional[str] = None,
) -> dict:
    """Create a threshold rule."""
    from app.models.models import ThresholdRule, AlarmSeverity
    from app.services.audit import audit

    device_id = None
    if device_name:
        matched = next(
            (d for d in devices if d["name"].lower() == device_name.lower()), None
        ) or next(
            (d for d in devices if device_name.lower() in d["name"].lower()), None
        )
        if matched:
            device_id = matched["id"]

    try:
        sev = AlarmSeverity[severity.upper()]
    except KeyError:
        sev = AlarmSeverity.WARNING

    rule = ThresholdRule(
        tenant_id  = current_user.tenant_id,
        device_id  = device_id,
        key        = key,
        condition  = condition,
        threshold  = threshold,
        severity   = sev,
        alarm_type = alarm_type or f"{key.replace('_',' ').title()} Alarm",
        is_active  = True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="rule.create", resource="threshold_rule", resource_id=str(rule.id),
          detail={"key": key, "threshold": threshold, "source": "taat_v2"}, commit=True)

    return {
        "success":    True,
        "rule_id":    str(rule.id),
        "device":     device_name or "all devices",
        "key":        key,
        "condition":  condition,
        "threshold":  threshold,
        "severity":   severity.upper(),
        "alarm_type": rule.alarm_type,
    }


def tool_delete_rule(
    db: Session, current_user,
    key: Optional[str] = None,
    delete_all: bool = False,
) -> dict:
    """Delete threshold rule(s)."""
    from app.models.models import ThresholdRule
    from app.services.audit import audit

    if delete_all:
        rules = db.query(ThresholdRule).filter(
            ThresholdRule.tenant_id == current_user.tenant_id
        ).all()
        count = len(rules)
        for r in rules:
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="rule.delete", resource="threshold_rule",
                  resource_id=str(r.id), detail={"source": "taat_v2_bulk"})
            db.delete(r)
        db.commit()
        return {"success": True, "deleted": count, "scope": "all"}

    if key:
        rule = db.query(ThresholdRule).filter(
            ThresholdRule.tenant_id == current_user.tenant_id,
            ThresholdRule.key == key,
        ).first()
        if rule:
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="rule.delete", resource="threshold_rule",
                  resource_id=str(rule.id), detail={"key": key, "source": "taat_v2"})
            db.delete(rule)
            db.commit()
            return {"success": True, "deleted": 1, "key": key}

    return {"success": False, "reason": "No matching rule found"}


def tool_save_memory(
    db: Session, current_user,
    memory_type: str, content: str,
    user_specific: bool = False,
) -> dict:
    """Write a memory entry for this tenant."""
    try:
        from app.models.models import AgentMemory
        mem = AgentMemory(
            tenant_id   = current_user.tenant_id,
            user_id     = current_user.id if user_specific else None,
            memory_type = memory_type,
            content     = content,
        )
        db.add(mem)
        db.commit()
        return {"success": True, "memory_type": memory_type}
    except Exception as exc:
        logger.warning("save_memory failed: %s", exc)
        db.rollback()
        return {"success": False, "reason": str(exc)}


# ── Helper ────────────────────────────────────────────────────────────────────

def _resolve_device_ids(db: Session, current_user, device_id: Optional[str]) -> list:
    """Return list of device UUIDs this user can act on."""
    from app.models.models import Device
    if device_id:
        return [device_id]
    q = db.query(Device.id).filter(Device.tenant_id == current_user.tenant_id)
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    return [str(r[0]) for r in q.all()]
