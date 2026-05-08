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

BASELINE_DAYS     = 30   # days of history to use — full statistical baseline
BASELINE_DAYS_MIN = 1    # minimum days before partial baseline is computed
MIN_SAMPLES_HOUR  = 3    # min points per hour bucket (lowered from 5 — works after ~3 min)


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
    # Use 30-day window if available, fall back to all available data
    # This lets new devices get a partial baseline from day 1
    since_full    = datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)
    since_partial = datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS_MIN)

    # Try full window first
    rows = (
        db.query(TelemetryData.key, TelemetryData.value_num, TelemetryData.ts)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.value_num.isnot(None),
            TelemetryData.ts >= since_full,
        )
        .all()
    )

    # If no 30-day data, use all available history (partial baseline)
    if not rows:
        rows = (
            db.query(TelemetryData.key, TelemetryData.value_num, TelemetryData.ts)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= since_partial,
            )
            .all()
        )
        if rows:
            logger.info("baseline: using partial history for device %s", device_id)

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


def get_daily_comparison(db: Session, device_id: str) -> dict:
    """
    Compare today's average readings against yesterday's averages.
    Returns per-key delta so intelligence can say:
    "temperature avg today: 52°C vs yesterday: 45°C (+15%)"
    Works with any amount of data — no 30-day minimum.
    """
    from app.models.models import TelemetryData
    now      = datetime.now(timezone.utc)
    today_start     = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end   = today_start

    result = {}

    # Get all numeric keys for this device
    keys = (
        db.query(TelemetryData.key)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.value_num.isnot(None),
            TelemetryData.ts >= yesterday_start,
        )
        .distinct()
        .all()
    )

    for (key,) in keys:
        # Today's values
        today_rows = (
            db.query(TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.key == key,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= today_start,
                TelemetryData.ts <= now,
            )
            .all()
        )
        # Yesterday's values
        yesterday_rows = (
            db.query(TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.key == key,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= yesterday_start,
                TelemetryData.ts < yesterday_end,
            )
            .all()
        )

        today_vals = [r.value_num for r in today_rows]
        yest_vals  = [r.value_num for r in yesterday_rows]

        if not today_vals:
            continue

        today_mean = sum(today_vals) / len(today_vals)
        entry = {
            "today_mean":   round(today_mean, 3),
            "today_pts":    len(today_vals),
        }

        if yest_vals:
            yest_mean = sum(yest_vals) / len(yest_vals)
            delta_pct = ((today_mean - yest_mean) / abs(yest_mean) * 100) if yest_mean != 0 else 0
            entry["yesterday_mean"] = round(yest_mean, 3)
            entry["yesterday_pts"]  = len(yest_vals)
            entry["delta_pct"]      = round(delta_pct, 1)
            entry["direction"]      = "up" if delta_pct > 5 else "down" if delta_pct < -5 else "stable"

        result[key] = entry

    return result


def get_baseline_deviation(db: Session, device_id: str, current_values: dict) -> dict:
    """
    Compare current live readings against 30-day baseline for the current hour.
    Returns per-key deviation status:
    {
      "temperature": {
        "value": 52.1,
        "baseline_mean": 45.2,
        "baseline_stddev": 1.3,
        "z_score": 5.3,
        "status": "ABOVE_NORMAL",  # NORMAL / ABOVE_NORMAL / BELOW_NORMAL / NO_BASELINE
        "message": "52.1°C is 5.3σ above 30-day normal (45.2±1.3°C)"
      }
    }
    Works even with partial baseline (< 30 days) if MIN_SAMPLES_HOUR is met.
    """
    current_hour = datetime.now(timezone.utc).hour

    rows = (
        db.query(DeviceBaseline)
        .filter(
            DeviceBaseline.device_id == device_id,
            DeviceBaseline.hour_of_day == current_hour,
        )
        .all()
    )

    baseline_map = {r.key: r for r in rows}
    result = {}

    for key, value in current_values.items():
        if value is None:
            continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue

        if key not in baseline_map:
            result[key] = {"value": val, "status": "NO_BASELINE"}
            continue

        b = baseline_map[key]
        mean   = b.mean
        stddev = b.stddev or 0

        # ── Baseline confidence level ─────────────────────────────────────────
        # sample_count per hour bucket: ~3600 samples/hr at 1Hz
        # < 1 day (~3600 samples):   PROVISIONAL — don't over-alarm
        # 1-3 days (~10800):         LOW
        # 3-7 days (~25200):         MEDIUM
        # > 7 days:                  HIGH
        sc = b.sample_count or 0
        if sc < 3600:
            confidence = "PROVISIONAL"
        elif sc < 10800:
            confidence = "LOW"
        elif sc < 25200:
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"

        if stddev < 1e-6:
            delta = val - mean
            status = "ABOVE_NORMAL" if delta > 0.01 else "BELOW_NORMAL" if delta < -0.01 else "NORMAL"
            z = None
        else:
            z = (val - mean) / stddev
            # Cap display at ±5σ — beyond that it's a clear operating point shift,
            # not a graduated anomaly signal. Reporting '9σ' adds no extra information.
            z_display = max(-5.0, min(5.0, z))
            if abs(z) <= 2.0:
                status = "NORMAL"
            elif z > 2.0:
                status = "ABOVE_NORMAL"
            else:
                status = "BELOW_NORMAL"

        # For PROVISIONAL baselines, very high σ likely means operating point
        # changed since baseline was built — not equipment failure
        operating_point_change = (
            confidence in ("PROVISIONAL", "LOW") and
            z is not None and abs(z) > 3.0
        )
        if operating_point_change:
            status = "OPERATING_POINT_CHANGE"

        z_out = round(z_display if z is not None else None, 2) if z is not None else None
        msg_parts = [f"{val:.2f}"]
        if z is not None:
            sign = "above" if z > 0 else "below"
            z_str = f">{abs(z_display):.1f}" if abs(z) >= 5.0 else f"{abs(z_display):.1f}"
            msg_parts.append(f"is {z_str}σ {sign}")
        else:
            msg_parts.append(f"{'above' if status == 'ABOVE_NORMAL' else 'below' if status == 'BELOW_NORMAL' else 'at'}")
        msg_parts.append(f"baseline ({mean:.3f}±{stddev:.3f})")
        msg_parts.append(f"[{sc} samples, {confidence} confidence]")
        if operating_point_change:
            msg_parts.append("— likely operating point change, not failure")

        result[key] = {
            "value":               val,
            "baseline_mean":       round(mean, 4),
            "baseline_stddev":     round(stddev, 4),
            "z_score":             z_out,
            "status":              status,
            "confidence":          confidence,
            "sample_count":        sc,
            "operating_pt_change": operating_point_change,
            "message":             " ".join(msg_parts),
        }

    return result
