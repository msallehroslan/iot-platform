"""
app/routers/widgets.py — Widget Data Abstraction Layer (Phase 10 #3)

Each widget type gets its own endpoint that returns exactly the data shape
that widget needs — no more, no less. Routers never touch the DB directly;
all reads go through data_service functions.

Architecture enforced here:
    Widget → GET /api/v1/widgets/data/{device_id}?type=<widget_type>
           → widgets router
           → data_service.*()
           → DB  (Redis cache slot lives in data_service — add later)

Endpoints
─────────
GET /widgets/data/{device_id}           → auto-dispatch by ?type=
GET /widgets/data/{device_id}/gauge     → latest value + baseline for gauge
GET /widgets/data/{device_id}/value_card  → latest value + baseline + anomaly + trend
GET /widgets/data/{device_id}/line_chart  → history points (resolution-aware)
GET /widgets/data/{device_id}/bar_chart   → same shape as line_chart
GET /widgets/data/{device_id}/status_light → device status + last_seen
GET /widgets/data/{device_id}/alarm_list  → active alarms for device
GET /widgets/data/{device_id}/entity_table → all latest key-value pairs
GET /widgets/data/{device_id}/trend_indicator → trend direction + confidence
GET /widgets/data/{device_id}/health_score    → health score + components
GET /widgets/data/{device_id}/anomaly_score   → anomaly z-scores per key
GET /widgets/data/{device_id}/baseline        → baseline ranges per key

RBAC
────
All endpoints require JWT. CUSTOMER_USER is scoped to their customer's devices.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, LatestTelemetry
from app.services.data_service import (
    get_latest_telemetry,
    get_aggregated_telemetry,
    get_active_alarms,
    get_baseline_now,
    get_anomaly_summary,
    get_health_summary,
    get_unified_intelligence,
)
from app.services.trend_service import get_device_key_trend, get_all_key_trends

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widgets", tags=["Widgets"])

# ── RBAC helper ───────────────────────────────────────────────────────────────

def _assert_device(device_id: UUID, current_user, db: Session) -> Device:
    """Return device if the current user has access, else 404/403."""
    q = db.query(Device).filter(
        Device.id == device_id,
        Device.tenant_id == current_user.tenant_id,
    )
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    device = q.first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


# ── Widget type → handler map ─────────────────────────────────────────────────
#
# Each handler receives (db, device_id, device, **kwargs) and returns a dict.
# Adding a new widget type = add one entry here + one function below.
# Nothing else changes.

def _gauge_data(db, device_id, device, key: str = "", **_):
    latest   = get_latest_telemetry(db, device_id)
    baseline = get_baseline_now(db, device_id)
    value    = latest["values"].get(key)
    b_key    = baseline["keys"].get(key) if baseline.get("status") == "active" else None
    return {
        "widget_type":   "gauge",
        "device_id":     device_id,
        "key":           key,
        "value":         value,
        "ts":            latest["ts"],
        "baseline":      b_key,
        "baseline_status": baseline.get("status", "learning"),
    }


def _value_card_data(db, device_id, device, key: str = "", **_):
    latest   = get_latest_telemetry(db, device_id)
    baseline = get_baseline_now(db, device_id)
    anomaly  = get_anomaly_summary(db, device_id, hours=24)
    value    = latest["values"].get(key)

    trend_data = {}
    if key:
        try:
            trend_data = get_device_key_trend(db, device_id, key, minutes=30)
        except Exception:
            pass

    b_key = baseline["keys"].get(key) if baseline.get("status") == "active" else None

    return {
        "widget_type":        "value_card",
        "device_id":          device_id,
        "key":                key,
        "value":              value,
        "ts":                 latest["ts"],
        "baseline":           b_key,
        "baseline_status":    baseline.get("status", "learning"),
        "trend":              trend_data.get("trend", "UNKNOWN"),
        "trend_change_pct":   trend_data.get("change_pct", 0),
        "trend_confidence":   trend_data.get("confidence", 0),
        "anomaly_count":      anomaly.get("anomaly_count", 0),
        "most_anomalous_key": anomaly.get("most_anomalous_key"),
    }


def _line_chart_data(
    db, device_id, device,
    key: str = "", hours: float = 24, limit: int = 200,
    resolution: str = "raw", **_
):
    return {
        "widget_type": "line_chart",
        **get_aggregated_telemetry(
            db, device_id, key,
            hours=hours, limit=limit, resolution=resolution,
        ),
    }


def _bar_chart_data(
    db, device_id, device,
    key: str = "", hours: float = 24, limit: int = 200,
    resolution: str = "raw", **_
):
    result = get_aggregated_telemetry(
        db, device_id, key,
        hours=hours, limit=limit, resolution=resolution,
    )
    return {"widget_type": "bar_chart", **result}


def _status_light_data(db, device_id, device, key: str = "", **_):
    latest = get_latest_telemetry(db, device_id)
    OFFLINE_THRESHOLD_MINS = 5

    is_offline = True
    age_mins   = None
    if device.last_seen_at:
        age_mins   = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds() / 60
        is_offline = age_mins > OFFLINE_THRESHOLD_MINS

    device_status = "ONLINE" if not is_offline else "OFFLINE"
    if not device.last_seen_at:
        device_status = "UNKNOWN"

    key_value = latest["values"].get(key) if key else None

    return {
        "widget_type":   "status_light",
        "device_id":     device_id,
        "device_status": device_status,
        "last_seen_at":  device.last_seen_at.isoformat() if device.last_seen_at else None,
        "age_mins":      round(age_mins, 1) if age_mins is not None else None,
        "key":           key or None,
        "key_value":     key_value,
        "all_values":    latest["values"],
        "ts":            latest["ts"],
    }


def _alarm_list_data(db, device_id, device, **_):
    alarms = get_active_alarms(db, device_id)
    return {
        "widget_type": "alarm_list",
        "device_id":   device_id,
        **alarms,
    }


def _entity_table_data(db, device_id, device, **_):
    latest = get_latest_telemetry(db, device_id)
    return {
        "widget_type": "entity_table",
        "device_id":   device_id,
        "values":      latest["values"],
        "ts":          latest["ts"],
        "key_count":   latest["key_count"],
    }


def _trend_indicator_data(db, device_id, device, key: str = "", minutes: int = 30, **_):
    if not key:
        raise HTTPException(status_code=400, detail="key param required for trend_indicator")
    try:
        trend = get_device_key_trend(db, device_id, key, minutes=minutes)
    except Exception as exc:
        logger.warning("trend_indicator failed device=%s key=%s: %s", device_id, key, exc)
        trend = {"trend": "UNKNOWN", "confidence": 0, "change_pct": 0, "points": 0}

    latest = get_latest_telemetry(db, device_id)
    return {
        "widget_type":  "trend_indicator",
        "device_id":    device_id,
        "key":          key,
        "trend":        trend.get("trend", "UNKNOWN"),
        "confidence":   trend.get("confidence", 0),
        "change_pct":   trend.get("change_pct", 0),
        "points":       trend.get("points", 0),
        "latest_value": latest["values"].get(key),
        "ts":           latest["ts"],
        "minutes":      minutes,
    }


def _health_score_data(db, device_id, device, **_):
    health = get_health_summary(db, device_id)
    return {
        "widget_type": "health_score",
        "device_id":   device_id,
        **health,
    }


def _anomaly_score_data(db, device_id, device, key: str = "", hours: float = 24, **_):
    anomaly = get_anomaly_summary(db, device_id, hours=hours)
    result = {
        "widget_type": "anomaly_score",
        "device_id":   device_id,
        **anomaly,
    }
    if key:
        result["key_filter"] = key
        result["recent_anomalies"] = [
            a for a in anomaly.get("recent_anomalies", [])
            if a["key"] == key
        ]
    return result


def _baseline_data(db, device_id, device, key: str = "", **_):
    baseline = get_baseline_now(db, device_id)
    if key and baseline.get("status") == "active":
        filtered_keys = {k: v for k, v in baseline.get("keys", {}).items() if k == key}
        baseline = {**baseline, "keys": filtered_keys}
    return {
        "widget_type": "baseline",
        "device_id":   device_id,
        **baseline,
    }


def _multi_axis_chart_data(
    db, device_id, device,
    keys: str = "", hours: float = 24, limit: int = 200,
    resolution: str = "raw", **_
):
    """Multi-axis: fetch history for multiple comma-separated keys."""
    key_list = [k.strip() for k in keys.split(",") if k.strip()] if keys else []
    if not key_list:
        # Fall back to all known keys (up to 6)
        rows = (
            db.query(LatestTelemetry.key)
            .filter(LatestTelemetry.device_id == device_id)
            .limit(6)
            .all()
        )
        key_list = [r[0] for r in rows]

    series = {}
    for k in key_list:
        series[k] = get_aggregated_telemetry(
            db, device_id, k,
            hours=hours, limit=limit, resolution=resolution,
        )

    return {
        "widget_type": "multi_axis_chart",
        "device_id":   device_id,
        "keys":        key_list,
        "series":      series,
        "resolution":  resolution,
        "hours":       hours,
    }


def _timeseries_table_data(
    db, device_id, device,
    key: str = "", hours: float = 1, limit: int = 50, **_
):
    result = get_aggregated_telemetry(
        db, device_id, key,
        hours=hours, limit=limit, resolution="raw",
    )
    # Return newest first for table display
    result["points"] = list(reversed(result.get("points", [])))
    return {"widget_type": "timeseries_table", **result}


def _pie_chart_data(db, device_id, device, keys: str = "", **_):
    latest   = get_latest_telemetry(db, device_id)
    key_list = [k.strip() for k in keys.split(",") if k.strip()] if keys else []
    values   = latest["values"]

    if key_list:
        slices = {k: values.get(k) for k in key_list if k in values}
    else:
        # All numeric keys
        slices = {k: v for k, v in values.items() if isinstance(v, (int, float))}

    return {
        "widget_type": "pie_chart",
        "device_id":   device_id,
        "slices":      slices,
        "ts":          latest["ts"],
    }


def _taat_insight_data(db, device_id, device, key: str = "", **_):
    """
    TAAT Insight Card — full per-key KeyIntelligence enrichment.
    Returns status + reason + risk + recommended_action for one key.
    Falls back to device-level unified if no key specified.
    """
    from app.services.data_service import get_key_intelligence, get_unified_intelligence

    if key:
        ki = get_key_intelligence(db, device_id, key, device=device)
        return {
            "widget_type": "taat_insight",
            "device_id":   device_id,
            "device_name": device.name if device else device_id,
            "mode":        "key",
            **ki,
        }

    # No key — return device-level summary
    unified = get_unified_intelligence(db, device_id, device=device)
    return {
        "widget_type":        "taat_insight",
        "device_id":          device_id,
        "device_name":        device.name if device else device_id,
        "mode":               "device",
        "status":             unified.get("status"),
        "risk":               unified.get("risk"),
        "reason":             unified.get("reason"),
        "recommended_action": unified.get("recommendation"),
        "health_score":       unified.get("health", {}).get("health_score"),
        "anomaly_count":      unified.get("anomaly", {}).get("anomaly_count", 0),
        "most_anomalous_key": unified.get("anomaly", {}).get("most_anomalous_key"),
        "alarms":             unified.get("alarms", {}).get("alarms", [])[:3],
    }


# Registry: widget_type → handler function
_WIDGET_HANDLERS = {
    "gauge":             _gauge_data,
    "value_card":        _value_card_data,
    "line_chart":        _line_chart_data,
    "bar_chart":         _bar_chart_data,
    "multi_axis_chart":  _multi_axis_chart_data,
    "timeseries_table":  _timeseries_table_data,
    "pie_chart":         _pie_chart_data,
    "status_light":      _status_light_data,
    "alarm_list":        _alarm_list_data,
    "entity_table":      _entity_table_data,
    "trend_indicator":   _trend_indicator_data,
    "health_score":      _health_score_data,
    "anomaly_score":     _anomaly_score_data,
    "baseline":          _baseline_data,
    "taat_insight":     _taat_insight_data,
}


# ── Main dispatch endpoint ────────────────────────────────────────────────────

@router.get("/data/{device_id}")
def get_widget_data(
    device_id: UUID,
    type:       str           = Query(..., description="Widget type — gauge, value_card, line_chart, …"),
    key:        str           = Query("",  description="Primary telemetry key"),
    keys:       str           = Query("",  description="Comma-separated keys (multi_axis_chart, pie_chart)"),
    hours:      float         = Query(24,  description="History window in hours"),
    limit:      int           = Query(200, description="Max data points"),
    resolution: str           = Query("raw", description="raw | 5min | 1h | 1d"),
    minutes:    int           = Query(30,  description="Trend window in minutes"),
    db:         Session       = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    """
    Phase 10 #3 — Widget Data Abstraction Layer.

    Single entry point for ALL widget data needs. The ?type= param routes to
    the correct handler which calls data_service functions — never the DB directly.

    Supported types:
        gauge, value_card, line_chart, bar_chart, multi_axis_chart,
        timeseries_table, pie_chart, status_light, alarm_list, entity_table,
        trend_indicator, health_score, anomaly_score, baseline

    Every response includes widget_type and device_id for easy client-side routing.

    Adding a new widget type requires only:
        1. Write a handler function _mytype_data(db, device_id, device, **kwargs)
        2. Add one entry to _WIDGET_HANDLERS
        Nothing else changes.
    """
    handler = _WIDGET_HANDLERS.get(type)
    if not handler:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown widget type '{type}'. Valid: {sorted(_WIDGET_HANDLERS)}",
        )

    device = _assert_device(device_id, current_user, db)

    return handler(
        db=db,
        device_id=str(device_id),
        device=device,
        key=key,
        keys=keys,
        hours=hours,
        limit=limit,
        resolution=resolution,
        minutes=minutes,
    )


# ── Named convenience endpoints (one per widget type) ────────────────────────
# These are thin wrappers — they exist so frontend code can use a clean URL
# like /widgets/data/{id}/gauge instead of /widgets/data/{id}?type=gauge.
# All logic still lives in the handler functions above.

@router.get("/data/{device_id}/gauge")
def widget_gauge(
    device_id: UUID,
    key: str           = Query(""),
    db: Session        = Depends(get_db),
    current_user       = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _gauge_data(db, str(device_id), device, key=key)


@router.get("/data/{device_id}/value_card")
def widget_value_card(
    device_id: UUID,
    key: str           = Query(""),
    db: Session        = Depends(get_db),
    current_user       = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _value_card_data(db, str(device_id), device, key=key)


@router.get("/data/{device_id}/line_chart")
def widget_line_chart(
    device_id:  UUID,
    key:        str = Query(""),
    hours:      float = Query(24),
    limit:      int = Query(200),
    resolution: str = Query("raw"),
    db:         Session = Depends(get_db),
    current_user        = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _line_chart_data(db, str(device_id), device, key=key, hours=hours, limit=limit, resolution=resolution)


@router.get("/data/{device_id}/bar_chart")
def widget_bar_chart(
    device_id:  UUID,
    key:        str = Query(""),
    hours:      float = Query(24),
    limit:      int = Query(200),
    resolution: str = Query("raw"),
    db:         Session = Depends(get_db),
    current_user        = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _bar_chart_data(db, str(device_id), device, key=key, hours=hours, limit=limit, resolution=resolution)


@router.get("/data/{device_id}/multi_axis_chart")
def widget_multi_axis(
    device_id:  UUID,
    keys:       str = Query(""),
    hours:      float = Query(24),
    limit:      int = Query(200),
    resolution: str = Query("raw"),
    db:         Session = Depends(get_db),
    current_user        = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _multi_axis_chart_data(db, str(device_id), device, keys=keys, hours=hours, limit=limit, resolution=resolution)


@router.get("/data/{device_id}/status_light")
def widget_status_light(
    device_id: UUID,
    key: str           = Query(""),
    db: Session        = Depends(get_db),
    current_user       = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _status_light_data(db, str(device_id), device, key=key)


@router.get("/data/{device_id}/alarm_list")
def widget_alarm_list(
    device_id: UUID,
    db: Session  = Depends(get_db),
    current_user = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _alarm_list_data(db, str(device_id), device)


@router.get("/data/{device_id}/entity_table")
def widget_entity_table(
    device_id: UUID,
    db: Session  = Depends(get_db),
    current_user = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _entity_table_data(db, str(device_id), device)


@router.get("/data/{device_id}/trend_indicator")
def widget_trend_indicator(
    device_id: UUID,
    key:     str = Query(...),
    minutes: int = Query(30),
    db:      Session = Depends(get_db),
    current_user     = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _trend_indicator_data(db, str(device_id), device, key=key, minutes=minutes)


@router.get("/data/{device_id}/health_score")
def widget_health_score(
    device_id: UUID,
    db: Session  = Depends(get_db),
    current_user = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _health_score_data(db, str(device_id), device)


@router.get("/data/{device_id}/anomaly_score")
def widget_anomaly_score(
    device_id: UUID,
    key:   str = Query(""),
    hours: float = Query(24),
    db:    Session = Depends(get_db),
    current_user   = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _anomaly_score_data(db, str(device_id), device, key=key, hours=hours)


@router.get("/data/{device_id}/baseline")
def widget_baseline(
    device_id: UUID,
    key: str           = Query(""),
    db: Session        = Depends(get_db),
    current_user       = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _baseline_data(db, str(device_id), device, key=key)


@router.get("/data/{device_id}/timeseries_table")
def widget_timeseries_table(
    device_id: UUID,
    key:   str = Query(""),
    hours: float = Query(1),
    limit: int = Query(50),
    db:    Session = Depends(get_db),
    current_user   = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _timeseries_table_data(db, str(device_id), device, key=key, hours=hours, limit=limit)


@router.get("/data/{device_id}/pie_chart")
def widget_pie_chart(
    device_id: UUID,
    keys: str          = Query(""),
    db: Session        = Depends(get_db),
    current_user       = Depends(get_current_user),
):
    device = _assert_device(device_id, current_user, db)
    return _pie_chart_data(db, str(device_id), device, keys=keys)


# ── Widget types catalogue ────────────────────────────────────────────────────

@router.get("/types")
def list_widget_types():
    """Return all supported widget types and their required query params."""
    return {
        "widget_types": [
            {"type": "gauge",            "params": ["key"],                        "data_sources": ["latest_telemetry", "device_baselines"]},
            {"type": "value_card",       "params": ["key"],                        "data_sources": ["latest_telemetry", "device_baselines", "anomaly_scores", "trend"]},
            {"type": "line_chart",       "params": ["key", "hours", "resolution"], "data_sources": ["telemetry_data"]},
            {"type": "bar_chart",        "params": ["key", "hours", "resolution"], "data_sources": ["telemetry_data"]},
            {"type": "multi_axis_chart", "params": ["keys", "hours"],              "data_sources": ["telemetry_data"]},
            {"type": "timeseries_table", "params": ["key", "hours", "limit"],      "data_sources": ["telemetry_data"]},
            {"type": "pie_chart",        "params": ["keys"],                       "data_sources": ["latest_telemetry"]},
            {"type": "status_light",     "params": ["key"],                        "data_sources": ["latest_telemetry", "devices"]},
            {"type": "alarm_list",       "params": [],                             "data_sources": ["alarms"]},
            {"type": "entity_table",     "params": [],                             "data_sources": ["latest_telemetry"]},
            {"type": "trend_indicator",  "params": ["key", "minutes"],             "data_sources": ["telemetry_data"]},
            {"type": "health_score",     "params": [],                             "data_sources": ["device_health_scores"]},
            {"type": "anomaly_score",    "params": ["key", "hours"],               "data_sources": ["anomaly_scores"]},
            {"type": "baseline",         "params": ["key"],                        "data_sources": ["device_baselines"]},
        ]
    }
