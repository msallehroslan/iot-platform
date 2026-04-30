"""
app/services/telemetry_service.py
Shared telemetry ingestion logic — HTTP and MQTT both call this.
FIX 8:  updates device.last_seen_at on every ingest
FIX 9:  alarm rules loaded from threshold_rules DB table (not hardcoded dict)
FIX 11: telemetry retention — rows older than TELEMETRY_RETENTION_DAYS are purged
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import and_
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


def _coerce_value(value: Any):
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


def _check_alarm_rules(db: Session, device: Device, key: str, value_num: Optional[float]) -> None:
    """FIX 9: Load rules from DB instead of hardcoded dict."""
    if value_num is None:
        return

    rules = db.query(ThresholdRule).filter(
        ThresholdRule.tenant_id == device.tenant_id,
        ThresholdRule.key == key,
        ThresholdRule.is_active == True,
        (ThresholdRule.device_id == device.id) | (ThresholdRule.device_id == None),
    ).order_by(ThresholdRule.device_id.desc()).all()  # device-specific rules first

    for rule in rules:
        triggered = False
        v, t = value_num, rule.threshold
        if rule.condition == "gt":  triggered = v >  t
        elif rule.condition == "gte": triggered = v >= t
        elif rule.condition == "lt":  triggered = v <  t
        elif rule.condition == "lte": triggered = v <= t
        elif rule.condition == "eq":  triggered = v == t

        if triggered:
            exists = db.query(Alarm).filter(
                and_(
                    Alarm.device_id == device.id,
                    Alarm.alarm_type == rule.alarm_type,
                    Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
                )
            ).first()
            if not exists:
                db.add(Alarm(
                    device_id=device.id,
                    alarm_type=rule.alarm_type,
                    severity=rule.severity,
                    status=AlarmStatus.ACTIVE_UNACK,
                    details={
                        "key": key, "value": value_num,
                        "threshold": rule.threshold, "condition": rule.condition,
                        "message": f"{key} {rule.condition} {rule.threshold} (value={value_num})",
                    },
                ))
            break  # highest-priority matching rule wins


def purge_old_telemetry(db: Session) -> int:
    """FIX 11: Delete telemetry rows older than TELEMETRY_RETENTION_DAYS. Returns deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
    deleted = db.query(TelemetryData).filter(TelemetryData.ts < cutoff).delete(synchronize_session=False)
    db.commit()
    logger.info("Telemetry purge: deleted %d rows older than %s", deleted, cutoff.date())
    return deleted


async def ingest_telemetry(
    db: Session,
    token: str,
    values: Dict[str, Any],
    ts: Optional[datetime] = None,
    source: str = "http",
) -> dict:
    if not values:
        raise ValueError("values dict must not be empty")

    device: Device | None = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise DeviceNotFoundError(f"No device found for token={token!r}")

    if device.status != DeviceStatus.ACTIVE:
        device.status = DeviceStatus.ACTIVE

    ts = ts or datetime.now(timezone.utc)

    # FIX 8: update last_seen_at on every ingest
    device.last_seen_at = ts

    keys_saved = 0
    coerced_values = {}

    for key, raw_value in values.items():
        val_str, val_num, val_bool, val_json = _coerce_value(raw_value)
        coerced_values[key] = (
            val_num  if val_num  is not None else
            val_bool if val_bool is not None else
            val_json if val_json is not None else
            val_str
        )

        db.add(TelemetryData(
            device_id=device.id, key=key,
            value_str=val_str, value_num=val_num,
            value_bool=val_bool, value_json=val_json,
            ts=ts,
        ))

        upsert_stmt = (
            pg_insert(LatestTelemetry)
            .values(id=uuid.uuid4(), device_id=device.id, key=key,
                    value_str=val_str, value_num=val_num,
                    value_bool=val_bool, value_json=val_json, ts=ts)
            .on_conflict_do_update(
                constraint="uq_latest_telemetry_device_key",
                set_={"value_str": val_str, "value_num": val_num,
                      "value_bool": val_bool, "value_json": val_json, "ts": ts},
            )
        )
        db.execute(upsert_stmt)

        _check_alarm_rules(db, device, key, val_num)
        keys_saved += 1

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

    db.commit()
    logger.debug("ingest ok  source=%-4s  device=%s  keys=%d  ts=%s",
                 source, device.id, keys_saved, ts.isoformat())

    try:
        from app.core.websocket_manager import manager as ws_manager
        await ws_manager.broadcast(device_id=str(device.id), values=coerced_values, ts=ts.isoformat())
    except Exception as exc:
        logger.warning("WS broadcast failed device=%s: %s", device.id, exc)

    return {"device_id": str(device.id), "ts": ts.isoformat(), "keys_saved": keys_saved}
