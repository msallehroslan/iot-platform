"""
app/services/anomaly_service.py — Anomaly Detection

Uses Z-score against a rolling 2-hour baseline to flag anomalous readings.
Called from telemetry_service on every numeric ingest.

Behaviour:
  - Needs MIN_SAMPLES points before scoring (returns None until then)
  - |z_score| > ANOMALY_THRESHOLD (default 3.0) → is_anomaly = True
  - Scores written to anomaly_scores table
  - Anomaly triggers are visible in intelligence endpoints
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.models import TelemetryData, AnomalyScore, DeviceBaseline

logger = logging.getLogger(__name__)

# Minimum data points needed before scoring
MIN_SAMPLES       = 20
# Z-score threshold — 3.0 = 99.7% confidence
ANOMALY_THRESHOLD = 3.0
# Rolling window for live Z-score (minutes)
ROLLING_WINDOW_MIN = 120


def _rolling_stats(values: list[float]) -> tuple[float, float]:
    """Return (mean, stddev) of a list. stddev=0 if len<2."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance)


def score_telemetry_point(
    db: Session,
    device_id: str,
    key: str,
    value: float,
    ts: datetime,
) -> Optional[AnomalyScore]:
    """
    Score a single telemetry point against recent history.
    Returns AnomalyScore (committed) or None if not enough data yet.
    """
    try:
        since = ts - timedelta(minutes=ROLLING_WINDOW_MIN)

        # Fetch recent values for this device/key (excluding current point)
        rows = (
            db.query(TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.key == key,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts >= since,
                TelemetryData.ts < ts,
            )
            .order_by(TelemetryData.ts.desc())
            .limit(200)
            .all()
        )

        values = [r.value_num for r in rows]

        if len(values) < MIN_SAMPLES:
            # Not enough history yet — still learning
            return None

        mean, stddev = _rolling_stats(values)

        if stddev < 1e-9:
            # All values identical — Z-score undefined, skip
            return None

        z_score   = (value - mean) / stddev
        is_anomaly = abs(z_score) > ANOMALY_THRESHOLD

        score = AnomalyScore(
            device_id       = device_id,
            key             = key,
            ts              = ts,
            value           = value,
            z_score         = round(z_score, 4),
            is_anomaly      = is_anomaly,
            baseline_mean   = round(mean, 4),
            baseline_stddev = round(stddev, 4),
        )
        db.add(score)
        db.flush()   # get ID without committing — caller commits

        if is_anomaly:
            logger.info(
                "anomaly detected device=%s key=%s value=%.3f z=%.2f",
                device_id, key, value, z_score,
            )

        return score

    except Exception as exc:
        logger.error("anomaly_score failed device=%s key=%s: %s", device_id, key, exc)
        return None


def get_anomalies(
    db: Session,
    device_id: str,
    key: Optional[str] = None,
    hours: int = 24,
    only_anomalies: bool = True,
) -> list[dict]:
    """
    Fetch recent anomaly scores for a device.
    Returns list of dicts sorted newest-first.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = db.query(AnomalyScore).filter(
        AnomalyScore.device_id == device_id,
        AnomalyScore.ts >= since,
    )
    if key:
        q = q.filter(AnomalyScore.key == key)
    if only_anomalies:
        q = q.filter(AnomalyScore.is_anomaly == True)

    rows = q.order_by(AnomalyScore.ts.desc()).limit(100).all()

    return [
        {
            "key":             r.key,
            "ts":              r.ts.isoformat(),
            "value":           r.value,
            "z_score":         r.z_score,
            "is_anomaly":      r.is_anomaly,
            "baseline_mean":   r.baseline_mean,
            "baseline_stddev": r.baseline_stddev,
        }
        for r in rows
    ]


def get_anomaly_summary(db: Session, device_id: str, hours: int = 24) -> dict:
    """
    Summary of anomaly activity for a device — used in intelligence endpoints.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
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
    from sqlalchemy import func as sqlfunc
    key_counts = (
        db.query(AnomalyScore.key, sqlfunc.count(AnomalyScore.id).label("cnt"))
        .filter(
            AnomalyScore.device_id == device_id,
            AnomalyScore.ts >= since,
            AnomalyScore.is_anomaly == True,
        )
        .group_by(AnomalyScore.key)
        .order_by(sqlfunc.count(AnomalyScore.id).desc())
        .first()
    )

    # Check if we have enough data to score at all
    sample_count = (
        db.query(TelemetryData)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.value_num.isnot(None),
            TelemetryData.ts >= datetime.now(timezone.utc) - timedelta(hours=2),
        )
        .count()
    )

    status = "learning" if sample_count < MIN_SAMPLES else "active"

    return {
        "status":         status,
        "anomaly_count":  total,
        "most_anomalous_key": key_counts[0] if key_counts else None,
        "hours_checked":  hours,
        "min_samples_needed": MIN_SAMPLES,
        "samples_available":  sample_count,
    }
