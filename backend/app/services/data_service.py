"""
app/services/data_service.py — Shared Data Access Layer

The single source of truth for reading device data across all routers.
Routers call these functions — they never query the DB directly.

Functions:
    get_latest_telemetry(db, device_id)         → current values from latest_telemetry
    get_aggregated_telemetry(db, device_id, ..) → bucketed history from telemetry_data
    get_active_alarms(db, device_id)            → active alarm list
    get_baseline_now(db, device_id)             → current-hour baselines per key
    get_anomaly_summary(db, device_id)          → anomaly counts + most anomalous key
    get_health_summary(db, device_id)           → latest health score row
    get_unified_intelligence(db, device_id)     → merged status/reason/risk/recommendation

Architecture:
    Widget → API router → data_service → Redis (HIT) → return
                                       ↓ MISS
                                       DB → cache → return

All public functions return plain dicts — no SQLAlchemy models leak out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.models import (
    Device, LatestTelemetry, TelemetryData,
    Alarm, AlarmStatus, AlarmSeverity,
    DeviceBaseline, AnomalyScore, DeviceHealthScore,
)
from app.services.trend_service import get_all_key_trends
from app.services.cache_service import (
    cache,
    TTL_LATEST, TTL_ALARMS, TTL_UNIFIED,
    TTL_ANOMALY, TTL_HEALTH, TTL_BASELINE,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _coerce_value(row: LatestTelemetry) -> float | str | bool | None:
    """Return the most meaningful typed value from a LatestTelemetry row."""
    if row.value_num is not None:
        return row.value_num
    if row.value_bool is not None:
        return row.value_bool
    if row.value_str is not None:
        return row.value_str
    if row.value_json is not None:
        return row.value_json
    return None


def _severity_rank(sev: str) -> int:
    return {"CRITICAL": 4, "MAJOR": 3, "MINOR": 2, "WARNING": 1}.get(sev.upper(), 0)


# ── Layer 1: latest_telemetry (real-time state) ───────────────────────────────

def get_latest_telemetry(db: Session, device_id: str) -> dict:
    """
    Current key→value snapshot for a device from latest_telemetry table.
    Cached in Redis for TTL_LATEST seconds. Invalidated on ingest.

    Returns:
        {
            "device_id": str,
            "values": {"temperature": 36.5, "humidity": 72.1, ...},
            "ts": "2025-01-01T12:00:00+00:00"   # most recent timestamp
        }
    """
    return _sync_cache(
        key   = f"iot:latest:{device_id}",
        fetch = lambda: _fetch_latest_telemetry(db, device_id),
        ttl   = TTL_LATEST,
    )



def _sync_cache(key: str, fetch, ttl: int):
    """
    Synchronous cache wrapper for data_service functions.

    data_service is called from synchronous FastAPI route handlers
    (which run in a thread pool, not the event loop).  We cannot
    await inside them, but we can grab the running event loop and
    schedule the coroutine as a thread-safe future.

    Falls through to direct DB fetch on any error (cache is additive).
    """
    if not cache.enabled:
        return fetch()
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            fut = asyncio.run_coroutine_threadsafe(
                cache.get_or_set(key=key, fetch=fetch, ttl=ttl),
                loop,
            )
            return fut.result(timeout=2)
    except Exception as exc:
        logger.debug("cache sync_cache fallback: %s", exc)
    return fetch()


def _fetch_latest_telemetry(db: Session, device_id: str) -> dict:
    """Direct DB fetch — called by cache or directly when cache unavailable."""
    rows = (
        db.query(LatestTelemetry)
        .filter(LatestTelemetry.device_id == device_id)
        .all()
    )

    values: dict = {}
    latest_ts: Optional[datetime] = None

    for row in rows:
        values[row.key] = _coerce_value(row)
        if row.ts and (latest_ts is None or row.ts > latest_ts):
            latest_ts = row.ts

    return {
        "device_id": device_id,
        "values": values,
        "ts": latest_ts.isoformat() if latest_ts else None,
        "key_count": len(values),
    }


# ── Layer 2: telemetry_data (historical) ─────────────────────────────────────

def get_aggregated_telemetry(
    db: Session,
    device_id: str,
    key: str,
    hours: int = 24,
    limit: int = 200,
    resolution: str = "raw",   # raw | 5min | 1h | 1d
) -> dict:
    """
    Historical telemetry for a single key with optional downsampling.

    resolution="raw"  → raw rows up to `limit`
    resolution="5min" → 5-minute AVG buckets
    resolution="1h"   → hourly AVG buckets
    resolution="1d"   → daily AVG buckets

    Returns:
        {
            "device_id": str,
            "key": str,
            "resolution": str,
            "points": [{"ts": "...", "value": 36.5}, ...]
        }
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    TRUNC_MAP = {
        "5min": "5 minutes",
        "1h":   "hour",
        "1d":   "day",
    }

    if resolution in TRUNC_MAP:
        trunc = TRUNC_MAP[resolution]
        # PostgreSQL date_trunc bucketed aggregation
        bucket = sqlfunc.date_trunc(trunc, TelemetryData.ts).label("bucket")
        rows = (
            db.query(
                bucket,
                sqlfunc.avg(TelemetryData.value_num).label("avg_value"),
                sqlfunc.min(TelemetryData.value_num).label("min_value"),
                sqlfunc.max(TelemetryData.value_num).label("max_value"),
                sqlfunc.count(TelemetryData.id).label("sample_count"),
            )
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.key == key,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= since,
            )
            .group_by(bucket)
            .order_by(bucket.asc())
            .limit(limit)
            .all()
        )
        points = [
            {
                "ts":           r.bucket.isoformat(),
                "value":        round(r.avg_value, 4) if r.avg_value is not None else None,
                "min":          round(r.min_value, 4) if r.min_value is not None else None,
                "max":          round(r.max_value, 4) if r.max_value is not None else None,
                "sample_count": r.sample_count,
            }
            for r in rows
        ]
    else:
        # raw mode
        rows = (
            db.query(TelemetryData.ts, TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.key == key,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= since,
            )
            .order_by(TelemetryData.ts.desc())
            .limit(limit)
            .all()
        )
        points = [
            {"ts": r.ts.isoformat(), "value": round(r.value_num, 4)}
            for r in reversed(rows)
        ]

    return {
        "device_id":  device_id,
        "key":        key,
        "resolution": resolution,
        "hours":      hours,
        "points":     points,
        "point_count": len(points),
    }


# ── Layer 3: alarms ───────────────────────────────────────────────────────────

def get_active_alarms(db: Session, device_id: str) -> dict:
    """
    Active (unacked + acked) alarms for a device.
    Cached for TTL_ALARMS seconds. Invalidated on alarm ack/clear.
    """
    return _sync_cache(
        key   = f"iot:alarms:{device_id}",
        fetch = lambda: _fetch_active_alarms(db, device_id),
        ttl   = TTL_ALARMS,
    )


def _fetch_active_alarms(db: Session, device_id: str) -> dict:
    """Direct DB fetch."""
    rows = (
        db.query(Alarm)
        .filter(
            Alarm.device_id == device_id,
            Alarm.status.in_([
                AlarmStatus.ACTIVE_UNACK,
                AlarmStatus.ACTIVE_ACK,
            ]),
        )
        .order_by(Alarm.created_at.desc())
        .all()
    )

    alarms = [
        {
            "id":         str(row.id),
            "alarm_type": row.alarm_type,
            "severity":   row.severity.value if hasattr(row.severity, "value") else str(row.severity),
            "status":     row.status.value   if hasattr(row.status,   "value") else str(row.status),
            "start_ts":   row.start_ts.isoformat() if row.start_ts else None,
            "details":    row.details,
        }
        for row in rows
    ]

    highest = max(
        (a["severity"] for a in alarms),
        key=_severity_rank,
        default=None,
    )

    return {
        "count":            len(alarms),
        "highest_severity": highest,
        "alarms":           alarms,
    }


# ── Layer 4: baselines (current hour) ────────────────────────────────────────

def get_baseline_now(db: Session, device_id: str) -> dict:
    """
    Baseline stats for the current hour of day, keyed by telemetry key.
    Cached for TTL_BASELINE seconds — baselines are updated nightly.
    """
    current_hour = datetime.now(timezone.utc).hour
    return _sync_cache(
        key   = f"iot:baseline:{device_id}:{current_hour}",
        fetch = lambda: _fetch_baseline_now(db, device_id),
        ttl   = TTL_BASELINE,
    )


def _fetch_baseline_now(db: Session, device_id: str) -> dict:
    """Direct DB fetch."""
    current_hour = datetime.now(timezone.utc).hour

    rows = (
        db.query(DeviceBaseline)
        .filter(
            DeviceBaseline.device_id == device_id,
            DeviceBaseline.hour_of_day == current_hour,
        )
        .all()
    )

    if not rows:
        return {
            "status":       "learning",
            "current_hour": current_hour,
            "message":      "Needs 30 days of data to establish baseline",
            "keys":         {},
        }

    keys = {}
    for row in rows:
        keys[row.key] = {
            "mean":   round(row.mean, 4),
            "stddev": round(row.stddev, 4),
            "min":    round(row.min_val, 4) if row.min_val is not None else None,
            "max":    round(row.max_val, 4) if row.max_val is not None else None,
            "upper":  round(row.suggested_upper, 4) if row.suggested_upper is not None else None,
            "lower":  round(row.suggested_lower, 4) if row.suggested_lower is not None else None,
            "samples": row.sample_count,
        }

    return {
        "status":       "active",
        "current_hour": current_hour,
        "keys":         keys,
    }


# ── Layer 5: anomaly summary ──────────────────────────────────────────────────

def get_anomaly_summary(db: Session, device_id: str, hours: int = 24) -> dict:
    """
    Anomaly activity summary for a device over the past N hours.
    Cached for TTL_ANOMALY seconds.
    """
    return _sync_cache(
        key   = f"iot:anomaly:{device_id}:{hours}",
        fetch = lambda: _fetch_anomaly_summary(db, device_id, hours),
        ttl   = TTL_ANOMALY,
    )


def _fetch_anomaly_summary(db: Session, device_id: str, hours: int = 24) -> dict:
    """Direct DB fetch."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Count anomalies
    total = (
        db.query(AnomalyScore)
        .filter(
            AnomalyScore.device_id == device_id,
            AnomalyScore.ts >= since,
            AnomalyScore.is_anomaly == True,
        )
        .count()
    )

    # Most anomalous key
    key_row = (
        db.query(
            AnomalyScore.key,
            sqlfunc.count(AnomalyScore.id).label("cnt"),
        )
        .filter(
            AnomalyScore.device_id == device_id,
            AnomalyScore.ts >= since,
            AnomalyScore.is_anomaly == True,
        )
        .group_by(AnomalyScore.key)
        .order_by(sqlfunc.count(AnomalyScore.id).desc())
        .first()
    )

    # Recent anomalies (last 5)
    recent_rows = (
        db.query(AnomalyScore)
        .filter(
            AnomalyScore.device_id == device_id,
            AnomalyScore.ts >= since,
            AnomalyScore.is_anomaly == True,
        )
        .order_by(AnomalyScore.ts.desc())
        .limit(5)
        .all()
    )

    recent = [
        {
            "key":     r.key,
            "ts":      r.ts.isoformat(),
            "value":   r.value,
            "z_score": r.z_score,
            "mean":    r.baseline_mean,
        }
        for r in recent_rows
    ]

    # Determine scoring status from sample count
    sample_count = (
        db.query(TelemetryData)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.value_num.isnot(None),
            TelemetryData.ts >= datetime.now(timezone.utc) - timedelta(hours=2),
        )
        .count()
    )

    return {
        "status":             "learning" if sample_count < 20 else "active",
        "anomaly_count":      total,
        "most_anomalous_key": key_row[0] if key_row else None,
        "recent_anomalies":   recent,
        "hours_checked":      hours,
    }


# ── Layer 6: health summary ───────────────────────────────────────────────────

def get_health_summary(db: Session, device_id: str) -> dict:
    """
    Latest health score row for a device.
    Cached for TTL_HEALTH seconds — health is recomputed hourly.
    """
    return _sync_cache(
        key   = f"iot:health:{device_id}",
        fetch = lambda: _fetch_health_summary(db, device_id),
        ttl   = TTL_HEALTH,
    )


def _fetch_health_summary(db: Session, device_id: str) -> dict:
    """Direct DB fetch."""
    row = (
        db.query(DeviceHealthScore)
        .filter(DeviceHealthScore.device_id == device_id)
        .order_by(DeviceHealthScore.scored_at.desc())
        .first()
    )

    if not row:
        return {
            "health_score":          None,
            "health_label":          "UNKNOWN",
            "maintenance_due":       False,
            "maintenance_reason":    None,
            "predicted_failure_hrs": None,
            "components":            {},
            "scored_at":             None,
        }

    return {
        "health_score":          row.health_score,
        "health_label":          row.health_label,
        "maintenance_due":       row.maintenance_due,
        "maintenance_reason":    row.maintenance_reason,
        "predicted_failure_hrs": row.predicted_failure_hrs,
        "components": {
            "uptime":    row.uptime_score,
            "alarm":     row.alarm_score,
            "stability": row.stability_score,
            "freshness": row.freshness_score,
        },
        "scored_at": row.scored_at.isoformat(),
    }


# ── Unified Intelligence — the key function ───────────────────────────────────

def get_unified_intelligence(
    db: Session,
    device_id: str,
    device: Optional[Device] = None,
) -> dict:
    """
    Merge all intelligence layers into one structured response.
    This is the single call all widgets can use instead of hitting
    3–4 separate endpoints.

    Returns:
        {
            "device_id":      str,
            "device_name":    str,
            "status":         "HEALTHY" | "WARNING" | "CRITICAL" | "OFFLINE",
            "risk":           "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
            "reason":         "temperature 36°C above baseline mean 28.5°C",
            "recommendation": "Check cooling system — temperature rising trend",
            "confidence":     "high" | "medium" | "low",

            "telemetry":  { values, ts, key_count },
            "alarms":     { count, highest_severity, alarms },
            "baseline":   { status, current_hour, keys },
            "anomaly":    { status, anomaly_count, most_anomalous_key },
            "health":     { health_score, health_label, maintenance_due, ... },
            "trends":     { key: trend_direction, ... },

            "context_flags": {
                "has_baseline":  bool,
                "has_anomalies": bool,
                "is_offline":    bool,
                "stale_data":    bool,
            },
            "generated_at": "..."
        }
    """
    # Each sub-function is individually cached — unified gets its own
    # shorter TTL so the merged status/reason stays fresh
    def _build_unified():
        _telem    = get_latest_telemetry(db, device_id)
        _alarms   = get_active_alarms(db, device_id)
        _baseline = get_baseline_now(db, device_id)
        _anomaly  = get_anomaly_summary(db, device_id, hours=24)
        _health   = get_health_summary(db, device_id)
        return _telem, _alarms, _baseline, _anomaly, _health

    # Since sub-functions are already cached, calling _build_unified
    # is cheap on cache hit. We still wrap unified itself for the
    # merged status/reason/recommendation fields.
    # NOTE: unified is NOT cached here because it depends on device object
    # (last_seen_at) which changes on every ingest — sub-function caches suffice.
    telemetry = get_latest_telemetry(db, device_id)
    alarms    = get_active_alarms(db, device_id)
    baseline  = get_baseline_now(db, device_id)
    anomaly   = get_anomaly_summary(db, device_id, hours=24)
    health    = get_health_summary(db, device_id)

    # Trends (already exists in trend_service)
    try:
        raw_trends = get_all_key_trends(db, device_id, minutes=30)
        trends = {k: v.get("trend", "UNKNOWN") for k, v in raw_trends.items()}
    except Exception:
        trends = {}

    # Determine device offline / stale status
    is_offline  = False
    stale_data  = False
    device_name = device_id  # fallback

    if device:
        device_name = device.name
        status_val  = device.status.value if hasattr(device.status, "value") else str(device.status)
        is_offline  = status_val not in ("ACTIVE",)
        if device.last_seen_at:
            age_mins = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds() / 60
            stale_data = age_mins > 10

    # ── Build status ─────────────────────────────────────────────────────────
    status = _determine_status(alarms, health, is_offline, stale_data)

    # ── Build risk level ──────────────────────────────────────────────────────
    risk = _determine_risk(status, alarms, anomaly, health)

    # ── Build human reason ────────────────────────────────────────────────────
    reason = _build_reason(
        status, alarms, anomaly, trends,
        telemetry, baseline, stale_data, is_offline,
    )

    # ── Build recommendation ──────────────────────────────────────────────────
    recommendation = _build_recommendation(status, alarms, anomaly, health, trends)

    # ── Confidence based on data richness ────────────────────────────────────
    has_baseline  = baseline.get("status") == "active" and bool(baseline.get("keys"))
    has_anomalies = anomaly.get("anomaly_count", 0) > 0
    has_health    = health.get("health_score") is not None

    confidence = "high" if (has_baseline and has_health) else "medium" if has_health else "low"

    return {
        "device_id":      device_id,
        "device_name":    device_name,
        "status":         status,
        "risk":           risk,
        "reason":         reason,
        "recommendation": recommendation,
        "confidence":     confidence,

        "telemetry": telemetry,
        "alarms":    alarms,
        "baseline":  baseline,
        "anomaly":   anomaly,
        "health":    health,
        "trends":    trends,

        "context_flags": {
            "has_baseline":  has_baseline,
            "has_anomalies": has_anomalies,
            "is_offline":    is_offline,
            "stale_data":    stale_data,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Private: status / risk / reason / recommendation builders ─────────────────

def _determine_status(
    alarms: dict,
    health: dict,
    is_offline: bool,
    stale_data: bool,
) -> str:
    if is_offline:
        return "OFFLINE"

    highest_sev = alarms.get("highest_severity")
    if highest_sev in ("CRITICAL", "MAJOR"):
        return "CRITICAL"

    health_label = health.get("health_label", "UNKNOWN")
    if health_label == "CRITICAL":
        return "CRITICAL"

    if stale_data or alarms.get("count", 0) > 0 or health_label == "WARNING":
        return "WARNING"

    return "HEALTHY"


def _determine_risk(
    status: str,
    alarms: dict,
    anomaly: dict,
    health: dict,
) -> str:
    if status == "CRITICAL":
        return "CRITICAL"

    score = health.get("health_score")
    anomaly_count = anomaly.get("anomaly_count", 0)

    if status == "OFFLINE":
        return "HIGH"

    if status == "WARNING":
        if (score is not None and score < 50) or anomaly_count > 5:
            return "HIGH"
        return "MEDIUM"

    if anomaly_count > 2:
        return "MEDIUM"

    return "LOW"


def _build_reason(
    status: str,
    alarms: dict,
    anomaly: dict,
    trends: dict,
    telemetry: dict,
    baseline: dict,
    stale_data: bool,
    is_offline: bool,
) -> str:
    if is_offline:
        return "Device is offline — no telemetry received"

    if stale_data:
        return "Device data is stale — last reading over 10 minutes ago"

    parts = []

    # Alarms take priority in the reason
    if alarms.get("count", 0) > 0:
        sev = alarms["highest_severity"]
        types = list({a["alarm_type"] for a in alarms["alarms"][:2]})
        parts.append(f"{alarms['count']} active alarm(s): {', '.join(types)} [{sev}]")

    # Anomalies
    if anomaly.get("anomaly_count", 0) > 0:
        key = anomaly.get("most_anomalous_key")
        val_info = ""
        # Add current value vs baseline if we have it
        if key and baseline.get("status") == "active":
            b = baseline["keys"].get(key)
            v = telemetry["values"].get(key)
            if b and v is not None:
                val_info = f" — current {v:.1f}, baseline mean {b['mean']:.1f}"
        parts.append(f"Anomaly detected in {key or 'telemetry'}{val_info}")

    # Significant trends
    bad_trends = [k for k, t in trends.items() if t in ("SPIKE", "DROP", "VOLATILE")]
    rising     = [k for k, t in trends.items() if t == "RISING"]
    if bad_trends:
        parts.append(f"Unstable trend in: {', '.join(bad_trends[:2])}")
    elif rising:
        parts.append(f"Rising trend in: {', '.join(rising[:2])}")

    if parts:
        return "; ".join(parts)

    # All clear
    return "All parameters within normal operating range"


def _build_recommendation(
    status: str,
    alarms: dict,
    anomaly: dict,
    health: dict,
    trends: dict,
) -> str:
    if status == "OFFLINE":
        return "Check device power and network connectivity"

    if status == "CRITICAL":
        if alarms.get("highest_severity") == "CRITICAL":
            types = list({a["alarm_type"] for a in alarms["alarms"][:1]})
            return f"Acknowledge and investigate: {types[0] if types else 'critical alarm'}"
        return "Immediate inspection required — device in critical state"

    if health.get("maintenance_due"):
        reason = health.get("maintenance_reason", "")
        if reason:
            return f"Schedule maintenance — {reason.split(';')[0]}"
        return "Schedule preventive maintenance — health score below threshold"

    if anomaly.get("anomaly_count", 0) > 3:
        key = anomaly.get("most_anomalous_key", "sensor")
        return f"Inspect {key} sensor — repeated anomalies detected"

    bad_trends = [k for k, t in trends.items() if t in ("SPIKE", "DROP", "VOLATILE")]
    if bad_trends:
        return f"Monitor {bad_trends[0]} closely — unstable readings"

    rising = [k for k, t in trends.items() if t == "RISING"]
    if rising:
        return f"Watch {rising[0]} — rising trend, may approach threshold"

    return "No action required — device operating normally"
