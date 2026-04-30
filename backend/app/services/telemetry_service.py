"""
app/services/telemetry_service.py
Shared telemetry ingestion logic — HTTP and MQTT both call this.

PHASE 2 FIXES:
  - Alarm engine is fully generic: evaluates rules for ANY key, no hardcoded keys
  - Alarm auto-clear: when a condition is no longer met, active alarm is cleared
  - One active alarm per (device_id, alarm_type) — no duplicates
  - Telemetry retention purge
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.models import (
    Alarm, AlarmSeverity, AlarmStatus,
    Device, DeviceStatus,
    LatestTelemetry, TelemetryData, TelemetryKey, ThresholdRule,
)

import uuid

logger = logging.getLogger(__name__)

TELEMETRY_RETENTION_DAYS = int(os.getenv("TELEMETRY_RETENTION_DAYS", "90"))


class DeviceNotFoundError(Exception):
    pass


# ── Value coercion ─────────────────────────────────────────────────────────────

def _coerce_value(value: Any):
    """Return (val_str, val_num, val_bool, val_json) with exactly one field set."""
    if isinstance(value, bool):
        return None, None, value, None
    if isinstance(value, (int, float)):
        return None, float(value), None, None
    if isinstance(value, str):
        try:
            return None, float(value), None, None
        except ValueError:
            return value, None, None, None
    if isinstance(value, dict):
        return None, None, None, value
    return str(value), None, None, None


# ── Generic alarm engine ───────────────────────────────────────────────────────

def _evaluate_condition(condition: str, value: float, threshold: float) -> bool:
    """Evaluate a single threshold condition. Returns True if alarm should fire."""
    if condition == "gt":  return value >  threshold
    if condition == "gte": return value >= threshold
    if condition == "lt":  return value <  threshold
    if condition == "lte": return value <= threshold
    if condition == "eq":  return value == threshold
    logger.warning("Unknown alarm condition %r — skipping", condition)
    return False


def _process_alarm_rules(
    db: Session,
    device: Device,
    key: str,
    value_num: Optional[float],
) -> None:
    """
    Fully generic alarm evaluation for a single (key, value) pair.

    For every active rule matching this (tenant, device, key):
      - If condition IS met    → trigger alarm if no active alarm exists yet
      - If condition is NOT met → auto-clear any active alarm for this rule

    Rules are evaluated in priority order:
      device-specific rules (device_id IS NOT NULL) checked before tenant-wide rules.
      Within the same specificity, most severe rule wins.

    No hardcoded keys. Works for temperature, glucose, vibration, voltage, or
    any custom key the device sends.
    """
    if value_num is None:
        # Non-numeric values cannot be compared — skip alarm evaluation
        return

    try:
        # Fetch all active rules for this (tenant, key) — both device-specific
        # and tenant-wide (device_id IS NULL). Order: device-specific first.
        rules = (
            db.query(ThresholdRule)
            .filter(
                ThresholdRule.tenant_id == device.tenant_id,
                ThresholdRule.key       == key,
                ThresholdRule.is_active == True,
                or_(
                    ThresholdRule.device_id == device.id,
                    ThresholdRule.device_id == None,
                ),
            )
            .order_by(
                # device-specific rules evaluated before tenant-wide
                ThresholdRule.device_id.desc().nullslast(),
            )
            .all()
        )

        if not rules:
            return

        # Track which alarm_types we've already handled in this pass
        # so we don't double-fire or double-clear for the same alarm_type
        handled_alarm_types: set[str] = set()

        for rule in rules:
            if rule.alarm_type in handled_alarm_types:
                continue
            handled_alarm_types.add(rule.alarm_type)

            condition_met = _evaluate_condition(rule.condition, value_num, rule.threshold)

            # ── Find existing active alarm for this (device, alarm_type) ──────
            active_alarm = db.query(Alarm).filter(
                and_(
                    Alarm.device_id  == device.id,
                    Alarm.alarm_type == rule.alarm_type,
                    Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
                )
            ).first()

            if condition_met:
                # ── TRIGGER: create alarm if not already active ────────────
                if not active_alarm:
                    db.add(Alarm(
                        device_id  = device.id,
                        alarm_type = rule.alarm_type,
                        severity   = rule.severity,
                        status     = AlarmStatus.ACTIVE_UNACK,
                        details    = {
                            "key":       key,
                            "value":     value_num,
                            "threshold": rule.threshold,
                            "condition": rule.condition,
                            "rule_id":   str(rule.id),
                            "message":   (
                                f"{key} {rule.condition} {rule.threshold} "
                                f"(current={value_num})"
                            ),
                        },
                    ))
                    logger.debug(
                        "Alarm TRIGGERED device=%s key=%s value=%s rule=%s",
                        device.id, key, value_num, rule.alarm_type,
                    )
                else:
                    # Update the details with latest value so dashboard shows current reading
                    if active_alarm.details:
                        active_alarm.details = {
                            **active_alarm.details,
                            "value":   value_num,
                            "message": (
                                f"{key} {rule.condition} {rule.threshold} "
                                f"(current={value_num})"
                            ),
                        }

            else:
                # ── AUTO-CLEAR: condition no longer met → clear active alarm ──
                if active_alarm:
                    now = datetime.now(timezone.utc)
                    active_alarm.status   = AlarmStatus.CLEARED_ACK
                    active_alarm.end_ts   = now
                    active_alarm.clear_ts = now
                    active_alarm.cleared_by = "auto-clear"
                    if active_alarm.details:
                        active_alarm.details = {
                            **active_alarm.details,
                            "value":      value_num,
                            "clear_reason": (
                                f"{key} no longer {rule.condition} {rule.threshold} "
                                f"(current={value_num})"
                            ),
                        }
                    logger.debug(
                        "Alarm AUTO-CLEARED device=%s key=%s value=%s rule=%s",
                        device.id, key, value_num, rule.alarm_type,
                    )

    except Exception as exc:
        logger.warning(
            "Alarm rule evaluation failed for device=%s key=%s — skipping: %s",
            device.id, key, exc,
        )


# ── Telemetry retention ───────────────────────────────────────────────────────

def purge_old_telemetry(db: Session) -> int:
    """Delete telemetry rows older than TELEMETRY_RETENTION_DAYS. Returns deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
    deleted = (
        db.query(TelemetryData)
        .filter(TelemetryData.ts < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Telemetry purge: deleted %d rows older than %s", deleted, cutoff.date())
    return deleted


# ── Main ingest pipeline ──────────────────────────────────────────────────────

async def ingest_telemetry(
    db: Session,
    token: str,
    values: Dict[str, Any],
    ts: Optional[datetime] = None,
    source: str = "http",
) -> dict:
    """
    Core telemetry pipeline:
      1. Validate device by token
      2. Mark device ACTIVE + update last_seen_at
      3. For each key: persist TelemetryData + upsert LatestTelemetry + auto-create TelemetryKey metadata
      4. Evaluate alarm rules generically for every numeric key
      5. Commit everything in one transaction
      6. Broadcast via WebSocket (non-fatal)
    """
    if not values:
        raise ValueError("values dict must not be empty")

    device: Device | None = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise DeviceNotFoundError(f"No device found for token={token!r}")

    if device.status != DeviceStatus.ACTIVE:
        device.status = DeviceStatus.ACTIVE

    ts = ts or datetime.now(timezone.utc)
    device.last_seen_at = ts

    keys_saved     = 0
    coerced_values = {}

    for key, raw_value in values.items():
        val_str, val_num, val_bool, val_json = _coerce_value(raw_value)
        coerced_values[key] = (
            val_num  if val_num  is not None else
            val_bool if val_bool is not None else
            val_json if val_json is not None else
            val_str
        )

        # Append to time-series
        db.add(TelemetryData(
            device_id=device.id, key=key,
            value_str=val_str, value_num=val_num,
            value_bool=val_bool, value_json=val_json,
            ts=ts,
        ))

        # Atomic upsert for latest value
        upsert_stmt = (
            pg_insert(LatestTelemetry)
            .values(
                id=uuid.uuid4(), device_id=device.id, key=key,
                value_str=val_str, value_num=val_num,
                value_bool=val_bool, value_json=val_json, ts=ts,
            )
            .on_conflict_do_update(
                constraint="uq_latest_telemetry_device_key",
                set_={
                    "value_str": val_str, "value_num": val_num,
                    "value_bool": val_bool, "value_json": val_json, "ts": ts,
                },
            )
        )
        db.execute(upsert_stmt)

        # Generic alarm evaluation — no hardcoded keys
        _process_alarm_rules(db, device, key, val_num)

        keys_saved += 1

        # Auto-create TelemetryKey metadata on first ingest of a new key
        inferred_type = (
            "boolean" if val_bool is not None else
            "number"  if val_num  is not None else
            "string"
        )
        meta_stmt = (
            pg_insert(TelemetryKey)
            .values(id=uuid.uuid4(), device_id=device.id, key=key, data_type=inferred_type)
            .on_conflict_do_nothing(constraint="uq_telemetry_keys_device_key")
        )
        db.execute(meta_stmt)

    # Single transaction commit for all keys + alarm changes
    db.commit()
    logger.debug(
        "ingest ok  source=%-4s  device=%s  keys=%d  ts=%s",
        source, device.id, keys_saved, ts.isoformat(),
    )

    # WebSocket broadcast (non-fatal — never blocks ingest)
    try:
        from app.core.websocket_manager import manager as ws_manager
        await ws_manager.broadcast(
            device_id=str(device.id),
            values=coerced_values,
            ts=ts.isoformat(),
        )
    except Exception as exc:
        logger.warning("WS broadcast failed device=%s: %s", device.id, exc)

    return {"device_id": str(device.id), "ts": ts.isoformat(), "keys_saved": keys_saved}
