"""
app/services/telemetry_service.py

Shared telemetry ingestion logic — called by BOTH:
  - app/routers/telemetry.py   (HTTP POST /ingest/{token})
  - app/services/mqtt_client.py (MQTT message on iot/{token}/telemetry)

NOTHING in this file is HTTP-specific: no Request, no Response, no HTTPException.
Errors are surfaced as plain Python exceptions so each caller handles them
in the way appropriate for its transport layer.

Public surface:
    ingest_telemetry(db, token, values, ts=None) -> dict
        Returns  {"device_id": str, "ts": str, "keys_saved": int}
        Raises   DeviceNotFoundError  if token is unknown
        Raises   ValueError           on bad input
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.models import (
    Alarm, AlarmSeverity, AlarmStatus,
    Device, DeviceStatus,
    LatestTelemetry, TelemetryData, TelemetryKey,
)

import uuid
logger = logging.getLogger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DeviceNotFoundError(Exception):
    """Raised when no device matches the supplied token."""


# ── Alarm rules (single source of truth) ─────────────────────────────────────

ALARM_RULES: Dict[str, list] = {
    "temperature": [
        {"threshold": 80, "severity": AlarmSeverity.CRITICAL, "type": "High Temperature Critical"},
        {"threshold": 60, "severity": AlarmSeverity.WARNING,  "type": "High Temperature Warning"},
    ],
    "humidity": [
        {"threshold": 90, "severity": AlarmSeverity.WARNING, "type": "High Humidity Warning"},
    ],
    "voltage": [
        {"threshold": 250, "severity": AlarmSeverity.CRITICAL, "type": "Overvoltage Critical"},
        {"threshold": 230, "severity": AlarmSeverity.WARNING,  "type": "High Voltage Warning"},
    ],
}


# ── Value coercion ────────────────────────────────────────────────────────────

def _coerce_value(value: Any):
    """
    Return (val_str, val_num, val_bool, val_json) with only one field set.
    Mirrors the logic that was previously inlined in the HTTP route.
    """
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


# ── Alarm check ───────────────────────────────────────────────────────────────

def _check_alarm_rules(db: Session, device: Device, key: str, value_num: Optional[float]) -> None:
    if value_num is None or key not in ALARM_RULES:
        return

    for rule in ALARM_RULES[key]:
        if value_num >= rule["threshold"]:
            exists = db.query(Alarm).filter(
                and_(
                    Alarm.device_id == device.id,
                    Alarm.alarm_type == rule["type"],
                    Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
                )
            ).first()
            if not exists:
                alarm = Alarm(
                    device_id=device.id,
                    alarm_type=rule["type"],
                    severity=rule["severity"],
                    status=AlarmStatus.ACTIVE_UNACK,
                    details={
                        "key":       key,
                        "value":     value_num,
                        "threshold": rule["threshold"],
                        "message":   (
                            f"{key} value {value_num} exceeded "
                            f"threshold {rule['threshold']}"
                        ),
                    },
                )
                db.add(alarm)
            break   # only raise the highest-priority rule that triggered


# ── Main ingest function ──────────────────────────────────────────────────────

async def ingest_telemetry(
    db: Session,
    token: str,
    values: Dict[str, Any],
    ts: Optional[datetime] = None,
    source: str = "http",   # "http" | "mqtt" — for logging only
) -> dict:
    """
    Core telemetry pipeline: validate device, persist data, check alarms,
    broadcast via WebSocket.

    Args:
        db:      SQLAlchemy session — caller is responsible for lifecycle
        token:   Device authentication token
        values:  Dict of key → value from the device payload
        ts:      Optional timestamp; defaults to UTC now
        source:  Ingestion source label for log messages

    Returns:
        {"device_id": str, "ts": str, "keys_saved": int}

    Raises:
        DeviceNotFoundError  if no device with that token exists
    """
    if not values:
        raise ValueError("values dict must not be empty")

    # ── 1. Device lookup & validation ─────────────────────────────────────────
    device: Device | None = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise DeviceNotFoundError(f"No device found for token={token!r}")

    # ── 2. Mark device as active on first seen data ────────────────────────────
    if device.status != DeviceStatus.ACTIVE:
        device.status = DeviceStatus.ACTIVE

    # ── 3. Timestamp ──────────────────────────────────────────────────────────
    ts = ts or datetime.now(timezone.utc)

    # ── 4. Persist each telemetry key ─────────────────────────────────────────
    keys_saved     = 0
    coerced_values = {}   # collects coerced values for WS broadcast
    for key, raw_value in values.items():
        val_str, val_num, val_bool, val_json = _coerce_value(raw_value)
        coerced_values[key] = (
            val_num  if val_num  is not None else
            val_bool if val_bool is not None else
            val_json if val_json is not None else
            val_str
        )

        # Append time-series record
        db.add(TelemetryData(
            device_id=device.id,
            key=key,
            value_str=val_str,
            value_num=val_num,
            value_bool=val_bool,
            value_json=val_json,
            ts=ts,
        ))

        # Upsert latest telemetry — atomic INSERT ... ON CONFLICT DO UPDATE.
        # This is race-condition-safe: concurrent HTTP + MQTT ingest for the same
        # device+key will never create duplicate rows, even without a SELECT first.
        # Requires the UniqueConstraint("device_id","key") defined in models.py.
        upsert_stmt = (
            pg_insert(LatestTelemetry)
            .values(
                id=uuid.uuid4(),
                device_id=device.id,
                key=key,
                value_str=val_str,
                value_num=val_num,
                value_bool=val_bool,
                value_json=val_json,
                ts=ts,
            )
            .on_conflict_do_update(
                constraint="uq_latest_telemetry_device_key",
                set_={
                    "value_str":  val_str,
                    "value_num":  val_num,
                    "value_bool": val_bool,
                    "value_json": val_json,
                    "ts":         ts,
                },
            )
        )
        db.execute(upsert_stmt)

        # ── 5. Alarm rules ────────────────────────────────────────────────────
        _check_alarm_rules(db, device, key, val_num)
        keys_saved += 1

        # ── Auto-create metadata row for this key (INSERT IGNORE) ────────
        # Uses INSERT ON CONFLICT DO NOTHING so it only fires once per
        # (device, key) pair — zero overhead on subsequent ingests.
        # data_type is inferred from the coerced value.
        inferred_type = (
            "boolean" if val_bool is not None else
            "number"  if val_num  is not None else
            "string"
        )
        meta_stmt = (
            pg_insert(TelemetryKey)
            .values(
                id=uuid.uuid4(),
                device_id=device.id,
                key=key,
                data_type=inferred_type,
            )
            .on_conflict_do_nothing(
                constraint="uq_telemetry_keys_device_key"
            )
        )
        db.execute(meta_stmt)

    # ── 6. Commit everything in one transaction ───────────────────────────────
    db.commit()
    logger.debug(
        "ingest ok  source=%-4s  device=%s  keys=%d  ts=%s",
        source, device.id, keys_saved, ts.isoformat(),
    )

    # ── 7. Broadcast to WebSocket clients (non-fatal) ─────────────────────────
    try:
        from app.core.websocket_manager import manager as ws_manager
        # Broadcast coerced values (always numeric where possible)
        # so widgets receive the same type the DB stores
        await ws_manager.broadcast(
            device_id=str(device.id),
            values=coerced_values,
            ts=ts.isoformat(),
        )
    except Exception as exc:
        logger.warning("WS broadcast failed device=%s: %s", device.id, exc)

    return {
        "device_id":  str(device.id),
        "ts":         ts.isoformat(),
        "keys_saved": keys_saved,
    }
