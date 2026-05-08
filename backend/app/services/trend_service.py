"""
app/services/trend_service.py — Intelligence Layer: Trend Detection

Analyses recent telemetry history to classify trends:
  RISING    — consistently increasing values
  FALLING   — consistently decreasing values  
  STABLE    — values within normal variance band
  SPIKE     — sudden sharp increase then return
  DROP      — sudden sharp decrease then return
  VOLATILE  — high variance, no clear direction
  UNKNOWN   — insufficient data

Called on-demand via GET /api/v1/intelligence/trend/{device_id}/{key}
Also used by the alarm engine to add trend context to alarm details.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.models.models import TelemetryData, Device

logger = logging.getLogger(__name__)


TREND_RISING   = "RISING"
TREND_FALLING  = "FALLING"
TREND_STABLE   = "STABLE"
TREND_SPIKE    = "SPIKE"
TREND_DROP     = "DROP"
TREND_VOLATILE = "VOLATILE"
TREND_UNKNOWN  = "UNKNOWN"


def detect_trend(
    values: list[float],
    spike_z: float = 2.5,
    stable_pct: float = 0.05,
    min_points: int = 5,
) -> dict:
    """
    Analyse a list of recent values (oldest first) and return trend info.

    Returns:
    {
        "trend":     "RISING" | "FALLING" | "STABLE" | "SPIKE" | "DROP" | "VOLATILE" | "UNKNOWN",
        "direction": +1 | -1 | 0,
        "confidence": 0.0–1.0,
        "change_pct": float,   # % change from first to last
        "mean":      float,
        "std":       float,
        "min":       float,
        "max":       float,
        "points":    int,
    }
    """
    if len(values) < min_points:
        return _unknown(values)

    import statistics

    mean = statistics.mean(values)
    std  = statistics.stdev(values) if len(values) > 1 else 0.0
    mn   = min(values)
    mx   = max(values)
    first = values[0]
    last  = values[-1]
    rng   = mx - mn or 1e-9

    change_pct = ((last - first) / abs(first)) * 100 if first != 0 else 0.0

    # ── Spike/Drop detection ─────────────────────────────────────────────────
    # Require at least 2 consecutive interior z-score breaches before declaring
    # SPIKE/DROP. This prevents single startup transients or noise spikes from
    # causing HEALTHY → WARNING flicker in Fleet Intelligence.
    if std > 0:
        z_scores = [(v - mean) / std for v in values]
        interior = z_scores[1:-1]  # exclude first and last

        # Find max consecutive breach of spike_z in the interior
        max_consec_breach = 0
        consec = 0
        breach_direction = 0
        for zval in interior:
            if abs(zval) > spike_z:
                consec += 1
                if consec > max_consec_breach:
                    max_consec_breach = consec
                    breach_direction = 1 if zval > 0 else -1
            else:
                consec = 0

        # Only flag SPIKE/DROP if 2+ consecutive interior points breach threshold
        if max_consec_breach >= 2:
            if breach_direction > 0:
                return _result(TREND_SPIKE, +1, min(max_consec_breach / 5, 1.0),
                               change_pct, mean, std, mn, mx, len(values))
            else:
                return _result(TREND_DROP, -1, min(max_consec_breach / 5, 1.0),
                               change_pct, mean, std, mn, mx, len(values))

    # ── Stable detection ─────────────────────────────────────────────────────
    # Values stay within stable_pct of mean
    band = abs(mean) * stable_pct if mean != 0 else stable_pct
    if std <= band:
        return _result(TREND_STABLE, 0, 0.9, change_pct, mean, std, mn, mx, len(values))

    # ── Trend detection via linear regression slope ──────────────────────────
    n = len(values)
    x_mean = (n - 1) / 2
    numerator   = sum((i - x_mean) * (v - mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    slope = numerator / denominator if denominator else 0

    # Normalise slope relative to value range
    norm_slope = slope / rng

    # Confidence: how consistently does each step move in slope direction?
    diffs = [values[i+1] - values[i] for i in range(len(values)-1)]
    if diffs:
        consistent = sum(1 for d in diffs if (d > 0) == (slope > 0)) / len(diffs)
    else:
        consistent = 0.5

    if norm_slope > 0.02 and consistent > 0.6:
        return _result(TREND_RISING, +1, consistent, change_pct, mean, std, mn, mx, n)
    elif norm_slope < -0.02 and consistent > 0.6:
        return _result(TREND_FALLING, -1, consistent, change_pct, mean, std, mn, mx, n)
    elif std / rng > 0.4:
        return _result(TREND_VOLATILE, 0, 0.5, change_pct, mean, std, mn, mx, n)
    else:
        return _result(TREND_STABLE, 0, 0.7, change_pct, mean, std, mn, mx, n)


def get_device_key_trend(
    db: Session,
    device_id: str,
    key: str,
    minutes: int = 30,
    max_points: int = 50,
) -> dict:
    """
    Fetch recent telemetry for a device/key and return trend analysis.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    rows = (
        db.query(TelemetryData.value_num, TelemetryData.ts)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.key == key,
            TelemetryData.ts >= since,
            TelemetryData.value_num.isnot(None),
        )
        .order_by(TelemetryData.ts.asc())
        .limit(max_points)
        .all()
    )

    if not rows:
        return {**_unknown([]), "key": key, "device_id": device_id, "window_minutes": minutes}

    values = [float(r.value_num) for r in rows]
    result = detect_trend(values)
    result["key"]            = key
    result["device_id"]      = device_id
    result["window_minutes"] = minutes
    result["latest_value"]   = values[-1] if values else None
    result["latest_ts"]      = rows[-1].ts.isoformat() if rows else None
    return result


def get_all_key_trends(
    db: Session,
    device_id: str,
    minutes: int = 30,
) -> dict[str, dict]:
    """
    Return trend for every key the device has sent in the last `minutes`.
    Returns: { "temperature": {...}, "humidity": {...}, ... }

    For long windows (> 60 min), uses a larger max_points sample to ensure
    meaningful trend detection over the full day.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    # Get distinct keys with recent data
    keys = (
        db.query(TelemetryData.key)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.ts >= since,
            TelemetryData.value_num.isnot(None),
        )
        .distinct()
        .all()
    )

    # Scale max_points with window size so daily trends have enough resolution
    # 30min → 50pts, 3h → 100pts, 24h → 200pts (evenly sampled)
    if minutes <= 60:
        max_pts = 50
    elif minutes <= 360:
        max_pts = 100
    else:
        max_pts = 200

    return {
        row.key: get_device_key_trend(db, device_id, row.key, minutes, max_points=max_pts)
        for row in keys
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _result(trend, direction, confidence, change_pct, mean, std, mn, mx, points):
    return {
        "trend":      trend,
        "direction":  direction,
        "confidence": round(confidence, 2),
        "change_pct": round(change_pct, 2),
        "mean":       round(mean, 3),
        "std":        round(std, 3),
        "min":        round(mn, 3),
        "max":        round(mx, 3),
        "points":     points,
    }


def _unknown(values):
    return {
        "trend":      TREND_UNKNOWN,
        "direction":  0,
        "confidence": 0.0,
        "change_pct": 0.0,
        "mean":       0.0,
        "std":        0.0,
        "min":        0.0,
        "max":        0.0,
        "points":     len(values),
    }
