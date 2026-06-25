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



def tool_get_telemetry_history(
    db: Session,
    device_id: str,
    key: str,
    hours: float = 48,
    resolution: str = "1h",
) -> dict:
    """
    Fetch aggregated telemetry history for a key over a time window.
    Used for today-vs-yesterday comparisons and trend analysis.
    Default: 48h at 1h resolution — covers today + yesterday.
    """
    from app.services.data_service import get_aggregated_telemetry
    from datetime import datetime, timezone, timedelta

    data = get_aggregated_telemetry(db, device_id, key, hours=hours, limit=500, resolution=resolution)
    points = data.get("points", [])

    if not points:
        return {"device_id": device_id, "key": key, "today": [], "yesterday": [], "comparison": "no data"}

    # Split into today and yesterday buckets
    now   = datetime.now(timezone.utc)
    today_start     = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    today_pts     = []
    yesterday_pts = []

    for p in points:
        try:
            ts = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
            if ts >= today_start:
                today_pts.append(p)
            elif ts >= yesterday_start:
                yesterday_pts.append(p)
        except Exception:
            continue

    # Compute daily averages for comparison
    def _avg(pts):
        vals = [p["value"] for p in pts if p.get("value") is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    today_avg     = _avg(today_pts)
    yesterday_avg = _avg(yesterday_pts)

    if today_avg is not None and yesterday_avg is not None:
        delta     = round(today_avg - yesterday_avg, 3)
        delta_pct = round((delta / yesterday_avg) * 100, 1) if yesterday_avg != 0 else None
        comparison = f"today avg {today_avg} vs yesterday avg {yesterday_avg} (delta: {delta:+.3f}, {delta_pct:+.1f}%)" if delta_pct is not None else f"today avg {today_avg} vs yesterday avg {yesterday_avg}"
    elif today_avg is not None:
        comparison = f"today avg {today_avg} — no yesterday data yet"
    elif yesterday_avg is not None:
        comparison = f"yesterday avg {yesterday_avg} — no data yet today"
    else:
        comparison = "insufficient data for comparison"

    return {
        "device_id":     device_id,
        "key":           key,
        "hours":         hours,
        "resolution":    resolution,
        "today":         today_pts,
        "yesterday":     yesterday_pts,
        "today_avg":     today_avg,
        "yesterday_avg": yesterday_avg,
        "comparison":    comparison,
        "all_points":    points,
    }


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


# ── Pump Analysis: DE/NDE Asymmetry + Efficiency ──────────────────────────────

def tool_get_pump_analysis(db: Session, device_id: str) -> dict:
    """
    Compute two structured analyses for pump-motor devices:

    1. DE/NDE Asymmetry Detection
       Compares Drive End vs Non-Drive End velocity readings across
       motor and pump. Determines fault type deterministically from
       the ratio and direction of change — does not rely on LLM inference.

    2. Pump Efficiency Estimation
       Calculates hydraulic and overall efficiency from available telemetry.
       Gracefully degrades when sensors are missing:
         flow + diff_pressure + power  → full hydraulic + overall efficiency
         flow + diff_pressure          → hydraulic efficiency only
         power + current trend         → electrical load efficiency proxy
         none of the above             → BEP deviation from velocity trend only

    Returns structured dict injected into TAAT context before system prompt.
    """
    from app.services.data_service import get_latest_telemetry, get_baseline_now
    from app.services.trend_service import get_device_key_trend

    telemetry = get_latest_telemetry(db, device_id)
    baseline  = get_baseline_now(db, device_id)
    values    = telemetry.get("values", {})
    b_keys    = baseline.get("keys", {}) if baseline.get("status") == "active" else {}

    result = {
        "device_id":    device_id,
        "asymmetry":    _compute_de_nde_asymmetry(values, b_keys),
        "efficiency":   _compute_pump_efficiency(values, b_keys, db, device_id),
    }
    return result


def _compute_de_nde_asymmetry(values: dict, b_keys: dict) -> dict:
    """
    Deterministically detect DE/NDE asymmetry patterns.
    Returns fault_type, severity, confidence, and recommended action.

    Fault classification matrix:
      Motor-DE drop + Motor-NDE stable           → drive_end_bearing_or_misalignment
      Motor-DE drop + Pump-DE rise               → coupling_slip
      Motor-DE drop + Pump-DE drop (symmetric)   → load_change_normal
      Pump-DE rise + Pump-NDE stable             → pump_side_bearing_or_imbalance
      All stable                                 → no_fault
    """
    def _get(key_patterns):
        """Find first matching key value from telemetry."""
        for k, v in values.items():
            kl = k.lower()
            if any(p in kl for p in key_patterns):
                try:
                    return k, float(v)
                except (TypeError, ValueError):
                    pass
        return None, None

    def _baseline_mean(key):
        b = b_keys.get(key, {})
        return b.get("mean") if b else None

    # Resolve actual key names and values
    m_de_key, m_de_val = _get(["motor_de_velocity", "motor_de_speed", "motor_de"])
    m_nde_key, m_nde_val = _get(["motor_nde_velocity", "motor_nde_speed", "motor_nde"])
    p_de_key, p_de_val = _get(["pump_de_velocity", "pump_de_speed", "pump_de"])
    p_nde_key, p_nde_val = _get(["pump_nde_velocity", "pump_nde_speed", "pump_nde"])

    available = {k: v for k, v in [
        ("motor_de", (m_de_key, m_de_val)),
        ("motor_nde", (m_nde_key, m_nde_val)),
        ("pump_de", (p_de_key, p_de_val)),
        ("pump_nde", (p_nde_key, p_nde_val)),
    ] if v[0] is not None}

    if len(available) < 2:
        return {
            "available":   False,
            "reason":      "Insufficient DE/NDE keys in telemetry",
            "keys_found":  list(available.keys()),
        }

    # Compute deviations from baseline mean (% change)
    def _dev(key_name, val, actual_key):
        mean = _baseline_mean(actual_key) if actual_key else None
        if mean and mean != 0:
            return (val - mean) / mean * 100
        return 0.0

    m_de_dev  = _dev("motor_de",  m_de_val,  m_de_key)  if m_de_val  is not None else None
    m_nde_dev = _dev("motor_nde", m_nde_val, m_nde_key) if m_nde_val is not None else None
    p_de_dev  = _dev("pump_de",   p_de_val,  p_de_key)  if p_de_val  is not None else None
    p_nde_dev = _dev("pump_nde",  p_nde_val, p_nde_key) if p_nde_val is not None else None

    # Asymmetry threshold — >15% divergence between DE and NDE is significant
    ASYM_THRESHOLD = 15.0
    COUPLING_THRESHOLD = 10.0

    fault_type   = "no_fault"
    severity     = "LOW"
    confidence   = "low"
    description  = "DE/NDE readings are symmetric — no asymmetry detected"
    action       = "Continue normal monitoring"
    ratio        = None

    # ── Case 1: Motor-DE drop + Pump-DE rise = coupling slip ─────────────────
    if (m_de_dev is not None and p_de_dev is not None
            and m_de_dev < -COUPLING_THRESHOLD and p_de_dev > COUPLING_THRESHOLD):
        fault_type  = "coupling_slip"
        severity    = "CRITICAL"
        confidence  = "high"
        ratio       = round(abs(p_de_dev - m_de_dev), 1)
        description = (
            f"COUPLING SLIP: Motor-DE deviating {m_de_dev:+.1f}% while Pump-DE "
            f"deviating {p_de_dev:+.1f}% from baseline. "
            f"Motor is spinning but not transferring torque to pump shaft."
        )
        action = (
            "Shut down and inspect coupling immediately. "
            "Check for worn jaw inserts, broken spider element, or loose keyway."
        )

    # ── Case 2: Motor-DE drop + Motor-NDE stable = drive-end fault ───────────
    elif (m_de_dev is not None and m_nde_dev is not None
          and abs(m_de_dev - m_nde_dev) > ASYM_THRESHOLD
          and m_de_dev < m_nde_dev):
        fault_type  = "drive_end_bearing_or_misalignment"
        severity    = "HIGH" if abs(m_de_dev) > 25 else "MEDIUM"
        confidence  = "high"
        ratio       = round(abs(m_de_dev - m_nde_dev), 1)
        description = (
            f"DRIVE-END ASYMMETRY: Motor-DE {m_de_dev:+.1f}% vs Motor-NDE "
            f"{m_nde_dev:+.1f}% from baseline (divergence={ratio}%). "
            f"Fault is localised at the drive end — bearing wear, coupling misalignment, or shaft issue."
        )
        action = (
            "Inspect drive-end bearing condition and coupling alignment. "
            "Check for vibration sidebands at 1× and 2× shaft frequency. "
            "Schedule bearing replacement if ISO 10816 Zone C exceeded."
        )

    # ── Case 3: Pump-DE + Pump-NDE diverging = pump-side fault ───────────────
    elif (p_de_dev is not None and p_nde_dev is not None
          and abs(p_de_dev - p_nde_dev) > ASYM_THRESHOLD):
        fault_type  = "pump_side_bearing_or_imbalance"
        severity    = "HIGH" if abs(p_de_dev - p_nde_dev) > 30 else "MEDIUM"
        confidence  = "high"
        ratio       = round(abs(p_de_dev - p_nde_dev), 1)
        description = (
            f"PUMP-SIDE ASYMMETRY: Pump-DE {p_de_dev:+.1f}% vs Pump-NDE "
            f"{p_nde_dev:+.1f}% from baseline (divergence={ratio}%). "
            f"Indicates impeller imbalance or pump-side bearing fault."
        )
        action = (
            "Inspect pump impeller for erosion, cavitation damage, or debris. "
            "Check pump-side bearing condition. "
            "Verify seal condition — pump asymmetry often precedes seal failure."
        )

    # ── Case 4: All drop together = normal load change ────────────────────────
    elif (m_de_dev is not None and m_nde_dev is not None
          and abs(m_de_dev - m_nde_dev) <= ASYM_THRESHOLD
          and m_de_dev < -COUPLING_THRESHOLD):
        fault_type  = "load_change_normal"
        severity    = "LOW"
        confidence  = "medium"
        description = (
            f"SYMMETRIC REDUCTION: Motor-DE {m_de_dev:+.1f}% and Motor-NDE "
            f"{m_nde_dev:+.1f}% declining proportionally — consistent with "
            f"load reduction or speed setpoint change, not a mechanical fault."
        )
        action = "Confirm with operator if a setpoint change or valve adjustment was made."

    return {
        "available":   True,
        "fault_type":  fault_type,
        "severity":    severity,
        "confidence":  confidence,
        "description": description,
        "action":      action,
        "ratio_pct":   ratio,
        "readings": {
            k: {"value": v[1], "key_name": v[0], "deviation_pct": d}
            for k, v, d in [
                ("motor_de",  (m_de_key,  m_de_val),  m_de_dev),
                ("motor_nde", (m_nde_key, m_nde_val), m_nde_dev),
                ("pump_de",   (p_de_key,  p_de_val),  p_de_dev),
                ("pump_nde",  (p_nde_key, p_nde_val), p_nde_dev),
            ] if v[0] is not None
        },
    }


def _compute_pump_efficiency(
    values: dict,
    b_keys: dict,
    db,
    device_id: str,
) -> dict:
    """
    Compute pump efficiency with graceful degradation based on available sensors.

    Tier 1 (full):    flow + diff_pressure + power     → hydraulic + overall efficiency
    Tier 2 (partial): flow + diff_pressure              → hydraulic efficiency only
    Tier 3 (proxy):   power + baseline comparison       → electrical load deviation
    Tier 4 (minimal): velocity trend only               → BEP deviation estimate

    Efficiency formulas:
      Hydraulic power (W) = flow_m3s × diff_pressure_pa × fluid_density
      Hydraulic efficiency = hydraulic_power / shaft_power × 100
      Overall efficiency   = hydraulic_power / electrical_power × 100
    """
    import math

    def _find_val(*patterns):
        for k, v in values.items():
            kl = k.lower()
            if any(p in kl for p in patterns):
                try:
                    return k, float(v)
                except (TypeError, ValueError):
                    pass
        return None, None

    FLUID_DENSITY = 1000.0  # kg/m³ water (standard)
    GRAVITY       = 9.81    # m/s²

    # Discover sensors
    flow_key, flow_val         = _find_val("flow_rate", "flow_m3", "volumetric_flow", "flow_lpm", "flow")
    press_in_key, press_in     = _find_val("inlet_pressure", "suction_pressure", "pressure_in", "p_in")
    press_out_key, press_out   = _find_val("outlet_pressure", "discharge_pressure", "pressure_out", "p_out")
    diff_key, diff_press       = _find_val("diff_pressure", "differential_pressure", "delta_pressure")
    power_key, power_kw        = _find_val("motor_power", "power_kw", "active_power", "shaft_power")
    current_key, current_a     = _find_val("current", "motor_current", "amps", "phase_current")
    voltage_key, voltage_v     = _find_val("voltage", "motor_voltage", "volts")
    speed_key, speed_rpm       = _find_val("motor_rpm", "pump_rpm", "speed_rpm", "motor_speed_rpm")

    # Derive differential pressure if not directly available
    if diff_press is None and press_in is not None and press_out is not None:
        diff_press = press_out - press_in

    # Derive power from current × voltage if direct power not available
    if power_kw is None and current_a is not None and voltage_v is not None:
        power_kw = (current_a * voltage_v * 1.732 * 0.85) / 1000  # 3-phase, PF=0.85 assumed

    tier = "none"
    hydraulic_eff  = None
    overall_eff    = None
    hydraulic_kw   = None
    bep_deviation  = None
    efficiency_status = "UNKNOWN"
    efficiency_note   = ""
    sensors_used      = []

    # ── Tier 1 / 2: Flow + Differential Pressure ─────────────────────────────
    if flow_val is not None and diff_press is not None:
        # Normalise flow to m³/s
        flow_m3s = flow_val
        if flow_key and ("lpm" in flow_key.lower() or "l_min" in flow_key.lower()):
            flow_m3s = flow_val / 60000  # L/min → m³/s
        elif flow_key and ("m3h" in flow_key.lower() or "m3_h" in flow_key.lower()):
            flow_m3s = flow_val / 3600   # m³/h → m³/s
        elif flow_val > 10:              # heuristic: large values likely L/min
            flow_m3s = flow_val / 60000

        # Normalise pressure to Pa
        diff_pa = diff_press
        if diff_press < 50:              # heuristic: small values likely bar
            diff_pa = diff_press * 100000
        elif diff_press < 2000:          # likely kPa
            diff_pa = diff_press * 1000

        hydraulic_kw = (flow_m3s * diff_pa) / 1000  # W → kW
        sensors_used += [flow_key, diff_key or f"{press_in_key}→{press_out_key}"]

        if power_kw and power_kw > 0:
            tier = "full"
            hydraulic_eff = round(min((hydraulic_kw / power_kw) * 100, 100), 1)
            overall_eff   = hydraulic_eff  # same when shaft ≈ electrical for motor+pump

            # Efficiency status thresholds (centrifugal pump typical)
            if hydraulic_eff >= 75:
                efficiency_status = "GOOD"
                efficiency_note   = f"Efficiency {hydraulic_eff}% — operating near BEP"
            elif hydraulic_eff >= 60:
                efficiency_status = "ACCEPTABLE"
                efficiency_note   = f"Efficiency {hydraulic_eff}% — moderate losses, check wear rings"
            elif hydraulic_eff >= 45:
                efficiency_status = "DEGRADED"
                efficiency_note   = f"Efficiency {hydraulic_eff}% — significant losses, inspect impeller and wear rings"
            else:
                efficiency_status = "CRITICAL"
                efficiency_note   = f"Efficiency {hydraulic_eff}% — severe degradation, immediate inspection needed"

            sensors_used.append(power_key)
        else:
            tier = "partial"
            efficiency_note = (
                f"Hydraulic power = {hydraulic_kw:.2f} kW. "
                f"No motor power sensor — overall efficiency cannot be computed. "
                f"Add motor_power or current+voltage keys for full efficiency."
            )
            efficiency_status = "PARTIAL"

    # ── Tier 3: Power + Baseline comparison ──────────────────────────────────
    elif power_kw is not None:
        tier = "proxy"
        b_power = b_keys.get(power_key, {}).get("mean") if power_key else None
        if b_power and b_power > 0:
            load_dev = ((power_kw - b_power) / b_power) * 100
            if load_dev > 15:
                efficiency_status = "DEGRADED"
                efficiency_note   = (
                    f"Motor consuming {load_dev:+.1f}% more power than baseline "
                    f"({power_kw:.1f}kW vs baseline {b_power:.1f}kW). "
                    f"Possible impeller wear, increased friction, or off-BEP operation."
                )
            elif load_dev < -15:
                efficiency_status = "WARNING"
                efficiency_note   = (
                    f"Motor consuming {load_dev:+.1f}% less power than baseline. "
                    f"Check for reduced load — possible partial blockage or valve throttling."
                )
            else:
                efficiency_status = "NORMAL"
                efficiency_note   = f"Motor power {power_kw:.1f}kW within {load_dev:+.1f}% of baseline — normal load."
        else:
            efficiency_status = "NO_BASELINE"
            efficiency_note   = f"Motor power={power_kw:.1f}kW detected but no baseline established yet. Continue monitoring."
        sensors_used.append(power_key)

    # ── Tier 4: Velocity trend → BEP deviation estimate ──────────────────────
    else:
        tier = "minimal"
        # Use motor DE velocity as proxy for speed, compare to baseline
        m_de_vals = [(k, float(v)) for k, v in values.items()
                     if "motor_de" in k.lower() or "pump_de" in k.lower()
                     and isinstance(v, (int, float))]
        if m_de_vals and b_keys:
            key, val = m_de_vals[0]
            b = b_keys.get(key, {})
            b_mean = b.get("mean")
            if b_mean and b_mean > 0:
                bep_deviation = round(((val - b_mean) / b_mean) * 100, 1)
                if abs(bep_deviation) > 20:
                    efficiency_status = "OFF_BEP"
                    efficiency_note   = (
                        f"Velocity {bep_deviation:+.1f}% from baseline mean — "
                        f"likely operating off Best Efficiency Point. "
                        f"Add flow_rate and pressure sensors for accurate efficiency."
                    )
                else:
                    efficiency_status = "NEAR_BEP"
                    efficiency_note   = (
                        f"Velocity {bep_deviation:+.1f}% from baseline — "
                        f"estimated near BEP. Add flow_rate and pressure sensors for accuracy."
                    )
        if not efficiency_note:
            efficiency_note = (
                "No flow, pressure, or power sensors detected. "
                "Add flow_rate + differential_pressure + motor_power for efficiency monitoring."
            )
        efficiency_status = efficiency_status or "NO_SENSORS"

    return {
        "tier":             tier,
        "hydraulic_kw":     round(hydraulic_kw, 3) if hydraulic_kw is not None else None,
        "hydraulic_eff_pct": hydraulic_eff,
        "overall_eff_pct":  overall_eff,
        "bep_deviation_pct": bep_deviation,
        "status":           efficiency_status,
        "note":             efficiency_note,
        "sensors_used":     [s for s in sensors_used if s],
        "sensors_missing":  _missing_efficiency_sensors(flow_key, diff_press, power_kw),
    }


def _missing_efficiency_sensors(flow_key, diff_press, power_kw) -> list:
    missing = []
    if flow_key is None:
        missing.append("flow_rate (m³/s, L/min, or m³/h)")
    if diff_press is None:
        missing.append("differential_pressure (bar, kPa, or Pa) or inlet+outlet pressure")
    if power_kw is None:
        missing.append("motor_power (kW) or current+voltage")
    return missing
