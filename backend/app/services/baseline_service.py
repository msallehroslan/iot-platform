"""
app/services/baseline_service.py — Baseline Learning + Adaptive Thresholds

Computes per-device, per-key, per-hour-of-day statistical baselines.
Run nightly by main.py background task.

Baseline = mean ± 3*stddev over last 30 days of data, grouped by hour-of-day.
This captures daily patterns (e.g. temperature spikes at noon).

Also suggests adaptive thresholds for ThresholdRule:
  suggested_upper = mean + 3*stddev
  suggested_lower = mean - 3*stddev
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.models import TelemetryData, DeviceBaseline, Device, TelemetryKey

logger = logging.getLogger(__name__)

BASELINE_DAYS     = 30   # days of history to use
MIN_SAMPLES_HOUR  = 5    # min points per hour bucket to compute baseline


def _stats(values: list[float]) -> tuple[float, float, float, float]:
    """Return (mean, stddev, min, max)."""
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    n    = len(values)
    mean = sum(values) / n
    mn   = min(values)
    mx   = max(values)
    if n < 2:
        return mean, 0.0, mn, mx
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance), mn, mx


def update_baselines_for_device(db: Session, device_id: str) -> int:
    """
    Rebuild all baselines for a device from last BASELINE_DAYS days.
    Returns number of baseline rows upserted.
    """
    since = datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)

    # Load all numeric telemetry for this device grouped in memory
    rows = (
        db.query(TelemetryData.key, TelemetryData.value_num, TelemetryData.ts)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.value_num.isnot(None),
            TelemetryData.ts >= since,
        )
        .all()
    )

    if not rows:
        logger.debug("baseline: no data for device %s", device_id)
        return 0

    # Group by key → hour_of_day → values
    buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for key, val, ts in rows:
        hour = ts.hour
        buckets[key][hour].append(val)

    upserted = 0
    for key, hour_map in buckets.items():
        for hour, values in hour_map.items():
            if len(values) < MIN_SAMPLES_HOUR:
                continue

            mean, stddev, mn, mx = _stats(values)
            suggested_upper = mean + 3 * stddev if stddev > 0 else None
            suggested_lower = mean - 3 * stddev if stddev > 0 else None

            # Upsert baseline
            existing = (
                db.query(DeviceBaseline)
                .filter(
                    DeviceBaseline.device_id == device_id,
                    DeviceBaseline.key == key,
                    DeviceBaseline.hour_of_day == hour,
                )
                .first()
            )

            if existing:
                existing.mean            = round(mean, 4)
                existing.stddev          = round(stddev, 4)
                existing.min_val         = round(mn, 4)
                existing.max_val         = round(mx, 4)
                existing.sample_count    = len(values)
                existing.suggested_upper = round(suggested_upper, 4) if suggested_upper else None
                existing.suggested_lower = round(suggested_lower, 4) if suggested_lower else None
                existing.updated_at      = datetime.now(timezone.utc)
            else:
                db.add(DeviceBaseline(
                    device_id       = device_id,
                    key             = key,
                    hour_of_day     = hour,
                    mean            = round(mean, 4),
                    stddev          = round(stddev, 4),
                    min_val         = round(mn, 4),
                    max_val         = round(mx, 4),
                    sample_count    = len(values),
                    suggested_upper = round(suggested_upper, 4) if suggested_upper else None,
                    suggested_lower = round(suggested_lower, 4) if suggested_lower else None,
                ))

            upserted += 1

    db.commit()
    logger.info("baseline: upserted %d rows for device %s", upserted, device_id)
    return upserted


def update_all_baselines(db: Session) -> dict:
    """Update baselines for all active devices. Called nightly."""
    devices = db.query(Device).filter(Device.status == "ACTIVE").all()
    total_rows = 0
    total_devices = 0
    for device in devices:
        try:
            n = update_baselines_for_device(db, str(device.id))
            if n > 0:
                total_rows += n
                total_devices += 1
        except Exception as exc:
            logger.error("baseline update failed for device %s: %s", device.id, exc)

    return {"devices_updated": total_devices, "baseline_rows": total_rows}


def get_baseline_for_device(db: Session, device_id: str, current_hour: Optional[int] = None) -> dict:
    """
    Get current baselines for a device.
    If current_hour provided, returns hour-specific baseline, else all hours.
    """
    q = db.query(DeviceBaseline).filter(DeviceBaseline.device_id == device_id)
    if current_hour is not None:
        q = q.filter(DeviceBaseline.hour_of_day == current_hour)

    rows = q.all()

    if not rows:
        return {"status": "learning", "message": f"Needs {BASELINE_DAYS} days of data"}

    result = {}
    for r in rows:
        if r.key not in result:
            result[r.key] = {}
        result[r.key][f"hour_{r.hour_of_day}"] = {
            "mean":             r.mean,
            "stddev":           r.stddev,
            "min":              r.min_val,
            "max":              r.max_val,
            "samples":          r.sample_count,
            "suggested_upper":  r.suggested_upper,
            "suggested_lower":  r.suggested_lower,
            "updated_at":       r.updated_at.isoformat() if r.updated_at else None,
        }

    return {"status": "active", "baselines": result}


def get_threshold_suggestions(db: Session, device_id: str) -> list[dict]:
    """
    Return suggested threshold values for each key based on learned baselines.
    Used in Rule Chains UI to suggest adaptive thresholds.
    """
    hour = datetime.now(timezone.utc).hour
    rows = (
        db.query(DeviceBaseline)
        .filter(
            DeviceBaseline.device_id == device_id,
            DeviceBaseline.hour_of_day == hour,
            DeviceBaseline.suggested_upper.isnot(None),
        )
        .all()
    )

    suggestions = []
    for r in rows:
        suggestions.append({
            "key":              r.key,
            "suggested_upper":  r.suggested_upper,
            "suggested_lower":  r.suggested_lower,
            "current_mean":     r.mean,
            "current_stddev":   r.stddev,
            "based_on_samples": r.sample_count,
            "hour_of_day":      r.hour_of_day,
        })

    return suggestions
