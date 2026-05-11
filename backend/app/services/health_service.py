"""
app/services/health_service.py — Device Health Scoring + Predictive Maintenance

Computes a composite 0–100 health score per device, updated hourly.
Tracks 4 components:
  - uptime_score:    % of last 24h the device was online
  - alarm_score:     penalised per active alarm by severity
  - stability_score: penalised for VOLATILE/SPIKE/DROP trends
  - freshness_score: penalised for stale last_seen_at

Maintenance prediction:
  - Triggered when health_score < 60 or predicted_failure_hrs < 48
  - Reason built from which component dragged the score lowest
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.models import (
    Device, DeviceStatus, Alarm, AlarmSeverity, AlarmStatus,
    DeviceHealthScore,
)
from app.services.trend_service import get_all_key_trends

logger = logging.getLogger(__name__)

# Score weights (must sum to 1.0)
WEIGHT_UPTIME    = 0.30
WEIGHT_ALARM     = 0.35
WEIGHT_STABILITY = 0.20
WEIGHT_FRESHNESS = 0.15

# Alarm severity penalties (deducted from alarm_score per alarm)
SEVERITY_PENALTY = {
    "CRITICAL":      40,
    "MAJOR":         25,
    "MINOR":         10,
    "WARNING":        5,
    "INDETERMINATE":  2,
}

# Maintenance threshold
MAINTENANCE_THRESHOLD = 60.0


def _uptime_score(device: Device, db: Session) -> float:
    """
    Score based on device online/offline pattern last 24h.
    Simple heuristic: ACTIVE = 100, INACTIVE based on last_seen age.
    """
    if device.status == DeviceStatus.DISABLED:
        return 0.0
    if not device.last_seen_at:
        return 20.0   # never seen
    age_mins = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds() / 60
    if age_mins < 5:
        return 100.0
    if age_mins < 15:
        return 85.0
    if age_mins < 60:
        return 60.0
    if age_mins < 360:
        return 30.0
    return 10.0


def _alarm_score(device_id: str, db: Session) -> float:
    """Score penalised for each active alarm by severity."""
    active_alarms = (
        db.query(Alarm)
        .filter(
            Alarm.device_id == device_id,
            Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
        )
        .all()
    )
    score = 100.0
    for alarm in active_alarms:
        sev = alarm.severity.value if hasattr(alarm.severity, "value") else str(alarm.severity)
        score -= SEVERITY_PENALTY.get(sev, 5)
    return max(0.0, score)


def _stability_score(device_id: str, db: Session) -> float:
    """Score penalised for volatile/spike/drop trends."""
    try:
        trends = get_all_key_trends(db, device_id, minutes=60)
    except Exception:
        return 80.0  # default if trend service fails

    if not trends:
        return 80.0  # no data yet

    score = 100.0
    for key, t in trends.items():
        trend = t.get("trend", "UNKNOWN")
        if trend == "VOLATILE":
            score -= 20
        elif trend in ("SPIKE", "DROP"):
            score -= 15
        elif trend in ("RISING", "FALLING"):
            change_pct = abs(t.get("change_pct", 0))
            if change_pct > 50:
                score -= 10
            elif change_pct > 20:
                score -= 5

    return max(0.0, score)


def _freshness_score(device: Device) -> float:
    """Score based on how recently data was received."""
    if not device.last_seen_at:
        return 0.0
    age_mins = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds() / 60
    if age_mins < 2:
        return 100.0
    if age_mins < 10:
        return 90.0
    if age_mins < 30:
        return 70.0
    if age_mins < 120:
        return 40.0
    return 10.0


def _maintenance_reason(uptime: float, alarm: float, stability: float, freshness: float) -> str:
    """Build human-readable maintenance reason from lowest scoring component."""
    reasons = []
    if uptime < 50:
        reasons.append("Device connectivity issues — frequent offline periods")
    if alarm < 40:
        reasons.append("Multiple active critical/major alarms require attention")
    if stability < 40:
        reasons.append("High telemetry volatility — sensor or hardware instability")
    if freshness < 30:
        reasons.append("Device not reporting data — possible hardware failure")
    return "; ".join(reasons) if reasons else "Health score below maintenance threshold"


def _predict_failure_hours(health_score: float, trend_direction: str = "stable") -> Optional[float]:
    """
    Simple linear extrapolation: if health is declining, estimate hours to failure.
    Returns None if no prediction possible.
    """
    if health_score >= 80:
        return None   # healthy — no prediction needed
    if health_score < 20:
        return 2.0    # imminent
    # Rough heuristic: hours proportional to remaining health
    # A score of 60 → ~48h, 40 → ~24h, 20 → ~8h
    return round((health_score / 60.0) * 48.0, 1)


def score_device(db: Session, device: Device) -> DeviceHealthScore:
    """
    Compute and persist a health score for a device.
    Returns the DeviceHealthScore object (not yet committed).
    """
    device_id = str(device.id)

    uptime    = _uptime_score(device, db)
    alarm     = _alarm_score(device_id, db)
    stability = _stability_score(device_id, db)
    freshness = _freshness_score(device)

    composite = (
        uptime    * WEIGHT_UPTIME +
        alarm     * WEIGHT_ALARM +
        stability * WEIGHT_STABILITY +
        freshness * WEIGHT_FRESHNESS
    )
    composite = round(composite, 2)

    if composite >= 80:
        label = "HEALTHY"
    elif composite >= 60:
        label = "WARNING"
    elif composite >= 30:
        label = "CRITICAL"
    else:
        label = "CRITICAL"

    maintenance_due = composite < MAINTENANCE_THRESHOLD
    reason          = _maintenance_reason(uptime, alarm, stability, freshness) if maintenance_due else None
    failure_hrs     = _predict_failure_hours(composite) if maintenance_due else None

    score = DeviceHealthScore(
        device_id             = device_id,
        scored_at             = datetime.now(timezone.utc),
        uptime_score          = round(uptime, 2),
        alarm_score           = round(alarm, 2),
        stability_score       = round(stability, 2),
        freshness_score       = round(freshness, 2),
        health_score          = composite,
        health_label          = label,
        maintenance_due       = maintenance_due,
        maintenance_reason    = reason,
        predicted_failure_hrs = failure_hrs,
    )
    db.add(score)
    return score


def score_all_devices(db: Session) -> dict:
    """Score all active devices. Called hourly by main.py."""
    devices = db.query(Device).filter(Device.status != DeviceStatus.DISABLED).all()
    scored = 0
    maintenance_alerts = 0
    for device in devices:
        try:
            s = score_device(db, device)
            if s.maintenance_due:
                maintenance_alerts += 1
            scored += 1
        except Exception as exc:
            logger.error("health_score failed for device %s: %s", device.id, exc)
    try:
        db.commit()
    except Exception as exc:
        logger.error("health score commit failed: %s", exc)
        db.rollback()
    logger.info("health scored %d devices, %d maintenance alerts", scored, maintenance_alerts)
    return {"scored": scored, "maintenance_alerts": maintenance_alerts}


def get_latest_health(db: Session, device_id: str) -> Optional[dict]:
    """Get the most recent health score for a device."""
    row = (
        db.query(DeviceHealthScore)
        .filter(DeviceHealthScore.device_id == device_id)
        .order_by(DeviceHealthScore.scored_at.desc())
        .first()
    )
    if not row:
        return None
    return {
        "health_score":          row.health_score,
        "health_label":          row.health_label,
        "uptime_score":          row.uptime_score,
        "alarm_score":           row.alarm_score,
        "stability_score":       row.stability_score,
        "freshness_score":       row.freshness_score,
        "maintenance_due":       row.maintenance_due,
        "maintenance_reason":    row.maintenance_reason,
        "predicted_failure_hrs": row.predicted_failure_hrs,
        "scored_at":             row.scored_at.isoformat(),
    }


def get_fleet_health(db: Session, tenant_id: str) -> list[dict]:
    """Get latest health score for every device in a tenant."""
    devices = (
        db.query(Device)
        .filter(Device.tenant_id == tenant_id)
        .all()
    )
    result = []
    for device in devices:
        health = get_latest_health(db, str(device.id))
        result.append({
            "device_id":   str(device.id),
            "device_name": device.name,
            "device_type": device.device_type,
            "status":      device.status.value,
            "health":      health or {"health_score": None, "health_label": "UNKNOWN", "maintenance_due": False},
        })
    # Sort by health_score ascending (worst first)
    result.sort(key=lambda x: x["health"].get("health_score") or 100)
    return result
