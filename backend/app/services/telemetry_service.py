"""
app/services/telemetry_service.py
Shared telemetry ingestion logic — HTTP and MQTT both call this.

PHASE 3 HARDENING:
  FIX 1 — Duplicate alarm prevention: DB-level guard using with_for_update()
  FIX 2 — Correct auto-clear lifecycle: CLEARED_UNACK vs CLEARED_ACK based on ack_ts
  FIX 3 — Explicit rule precedence: device rules OR tenant rules, never mixed
  FIX 5 — One DB query per ingest (load all rules upfront, group by key in memory)
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.models import (
    Alarm, AlarmStatus,
    Device, DeviceStatus,
    LatestTelemetry, TelemetryData, TelemetryKey, ThresholdRule, IngestMetric,
    RpcCommand,
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


# ── Condition evaluator ───────────────────────────────────────────────────────

def _evaluate_condition(condition: str, value: float, threshold: float) -> bool:
    """Evaluate a threshold condition. Returns True if alarm should fire."""
    if condition == "gt":  return value >  threshold
    if condition == "gte": return value >= threshold
    if condition == "lt":  return value <  threshold
    if condition == "lte": return value <= threshold
    if condition == "eq":  return value == threshold
    logger.warning("Unknown alarm condition %r — skipping", condition)
    return False


# ── Rule loader (FIX 3 + FIX 5) ──────────────────────────────────────────────

def _load_rules_for_device(db: Session, device: Device) -> Dict[str, List[ThresholdRule]]:
    """
    FIX 3 + FIX 5: Load ALL active rules for this device in ONE query,
    then apply explicit precedence in memory:

      If device-specific rules exist for a key → use ONLY those (ignore tenant-wide)
      If no device-specific rules for a key → fall back to tenant-wide rules

    This is explicit and predictable — no mixed evaluation, no ordering dependency.

    Returns: { key: [rules_to_apply, ...] }
    """
    all_rules = (
        db.query(ThresholdRule)
        .filter(
            ThresholdRule.tenant_id == device.tenant_id,
            ThresholdRule.is_active == True,
            or_(
                ThresholdRule.device_id == device.id,
                ThresholdRule.device_id == None,
            ),
        )
        .all()
    )

    if not all_rules:
        return {}

    # Split into device-specific vs tenant-wide, grouped by key
    device_rules: Dict[str, List[ThresholdRule]] = defaultdict(list)
    tenant_rules: Dict[str, List[ThresholdRule]] = defaultdict(list)

    for rule in all_rules:
        if rule.device_id is not None:
            device_rules[rule.key].append(rule)
        else:
            tenant_rules[rule.key].append(rule)

    # FIX 3: Explicit precedence — per key, use device rules if any exist,
    # otherwise fall back to tenant-wide. Never mix both for the same key.
    result: Dict[str, List[ThresholdRule]] = {}
    all_keys = set(device_rules.keys()) | set(tenant_rules.keys())
    for key in all_keys:
        if key in device_rules:
            result[key] = device_rules[key]   # device-specific wins
        else:
            result[key] = tenant_rules[key]   # tenant-wide fallback

    return result


def _fire_auto_rpc(db: Session, device: Device, rule: "ThresholdRule", clearing: bool = False) -> None:
    """
    Intelligence Layer: Auto RPC on alarm.
    When an alarm fires, automatically send an RPC command to the device.
    When alarm clears (if auto_rpc_clear=True), send inverse params.
    """
    if not rule.auto_rpc_method:
        return

    params = rule.auto_rpc_params or {}

    # If clearing and auto_rpc_clear is set, invert boolean params
    if clearing and rule.auto_rpc_clear:
        params = {k: (not v if isinstance(v, bool) else v) for k, v in params.items()}

    try:
        cmd = RpcCommand(
            device_id  = device.id,
            method     = rule.auto_rpc_method,
            params     = params,
            created_by = "auto-rule",
            status     = "PENDING",
        )
        db.add(cmd)
        logger.info(
            "auto_rpc.queued device=%s method=%s params=%s clearing=%s",
            device.id, rule.auto_rpc_method, params, clearing
        )
    except Exception as exc:
        logger.error("auto_rpc.failed device=%s error=%s", device.id, exc)



# ── Alarm engine (FIX 1 + FIX 2) ─────────────────────────────────────────────

def _process_alarm_for_rule(
    db: Session,
    device: Device,
    key: str,
    value_num: float,
    rule: ThresholdRule,
) -> None:
    """
    Evaluate one rule against one (key, value) and trigger or clear as needed.

    FIX 1 — Duplicate prevention:
      Uses SELECT ... FOR UPDATE to lock the row within the transaction.
      This prevents two concurrent ingest requests from both reading
      "no active alarm" and both inserting one. The second will block
      until the first commits, then find the existing alarm.
      Combined with the DB partial unique index on
        (device_id, alarm_type) WHERE status IN ('ACTIVE_UNACK','ACTIVE_ACK')
      this gives two layers of protection.

    FIX 2 — Correct clear lifecycle:
      CLEARED_UNACK when the alarm was never acknowledged (ack_ts is None)
      CLEARED_ACK   when the alarm was acknowledged before clearing
    """
    condition_met = _evaluate_condition(rule.condition, value_num, rule.threshold)

    # FIX 1: Lock existing active alarm row for this (device, alarm_type)
    # with_for_update() issues SELECT ... FOR UPDATE — safe under concurrency.
    # On single-worker Render this is low overhead but correct.
    active_alarm = (
        db.query(Alarm)
        .filter(
            Alarm.device_id  == device.id,
            Alarm.alarm_type == rule.alarm_type,
            Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
        )
        .with_for_update(skip_locked=False)
        .first()
    )

    if condition_met:
        if not active_alarm:
            # No active alarm — create one
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
                    "message":   f"{key} {rule.condition} {rule.threshold} (current={value_num})",
                },
            ))
            logger.info(
                "alarm.triggered device=%s key=%s value=%s alarm_type=%s",
                device.id, key, value_num, rule.alarm_type,
            )
            # Intelligence: fire auto RPC if configured on this rule
            _fire_auto_rpc(db, device, rule, clearing=False)
        else:
            # Active alarm exists — update details with latest reading only
            active_alarm.details = {
                **(active_alarm.details or {}),
                "value":   value_num,
                "message": f"{key} {rule.condition} {rule.threshold} (current={value_num})",
            }

    else:
        # FIX 2: Condition no longer met — auto-clear with correct status
        if active_alarm:
            now = datetime.now(timezone.utc)
            # CLEARED_ACK if previously acknowledged, CLEARED_UNACK if not
            active_alarm.status = (
                AlarmStatus.CLEARED_ACK
                if active_alarm.ack_ts is not None
                else AlarmStatus.CLEARED_UNACK
            )
            active_alarm.end_ts     = now
            active_alarm.clear_ts   = now
            active_alarm.cleared_by = "auto-clear"
            # Intelligence: fire inverse auto RPC on clear if configured
            _fire_auto_rpc(db, device, rule, clearing=True)
            active_alarm.details    = {
                **(active_alarm.details or {}),
                "value":        value_num,
                "clear_reason": (
                    f"{key} no longer {rule.condition} {rule.threshold} "
                    f"(current={value_num})"
                ),
            }
            logger.info(
                "alarm.cleared device=%s key=%s value=%s alarm_type=%s status=%s",
                device.id, key, value_num, rule.alarm_type, active_alarm.status,
            )


def _evaluate_all_alarms(
    db: Session,
    device: Device,
    numeric_values: Dict[str, float],
    rules_by_key: Dict[str, List[ThresholdRule]],
) -> None:
    """
    Evaluate alarm rules for all numeric keys received in this ingest.
    Rules are pre-loaded (FIX 5) — no per-key DB queries here.

    handled_alarm_types deduplicates across keys in this ingest cycle
    so the same alarm_type isn't triggered/cleared twice.
    """
    handled_alarm_types: set[str] = set()

    for key, value_num in numeric_values.items():
        key_rules = rules_by_key.get(key, [])
        for rule in key_rules:
            if rule.alarm_type in handled_alarm_types:
                continue
            handled_alarm_types.add(rule.alarm_type)
            _process_alarm_for_rule(db, device, key, value_num, rule)


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
      3. Load ALL alarm rules in one query (FIX 5)
      4. For each key: persist + upsert latest + collect numeric values
      5. Evaluate all alarm rules in memory (FIX 1+2+3)
      6. Commit everything in one transaction
      7. Broadcast via WebSocket (non-fatal)
    """
    if not values:
        raise ValueError("values dict must not be empty")

    device: Device | None = db.query(Device).filter(Device.token == token).first()
    if not device:
        raise DeviceNotFoundError(f"No device found for token={token!r}")

    # Phase 4: tenant-level ingest rate quota (non-blocking — catches sustained abuse)
    try:
        from app.services.audit import check_tenant_ingest_rate
        check_tenant_ingest_rate(db, device.tenant_id)
    except Exception as quota_exc:
        # Re-raise HTTPException (quota hit), swallow others
        from fastapi import HTTPException
        if isinstance(quota_exc, HTTPException):
            raise
        logger.warning("Quota check error (non-fatal): %s", quota_exc)

    if device.status != DeviceStatus.ACTIVE:
        device.status = DeviceStatus.ACTIVE

    ts = ts or datetime.now(timezone.utc)
    device.last_seen_at = ts

    # FIX 5: Load ALL rules for this device in ONE query before the key loop
    rules_by_key = _load_rules_for_device(db, device)

    keys_saved     = 0
    coerced_values = {}
    numeric_values: Dict[str, float] = {}  # collect for alarm evaluation

    for key, raw_value in values.items():
        val_str, val_num, val_bool, val_json = _coerce_value(raw_value)
        coerced_values[key] = (
            val_num  if val_num  is not None else
            val_bool if val_bool is not None else
            val_json if val_json is not None else
            val_str
        )

        if val_num is not None:
            numeric_values[key] = val_num

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
        try:
            db.execute(upsert_stmt)
        except Exception as e:
            db.rollback()
            if "ForeignKeyViolation" in str(e) or "foreign key" in str(e).lower():
                logger.warning(
                    "telemetry.rejected token=%s device no longer exists", token
                )
                return {"status": "rejected", "reason": "device_deleted", "keys_saved": 0}
            raise

        keys_saved += 1

        # Auto-create TelemetryKey metadata row on first ingest of a new key
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

    # FIX 1+2+3: Evaluate all alarms after all keys are processed,
    # using pre-loaded rules and explicit precedence logic
    if numeric_values and rules_by_key:
        try:
            _evaluate_all_alarms(db, device, numeric_values, rules_by_key)
        except Exception as exc:
            # Non-fatal: alarm evaluation failure must not block ingest
            logger.error(
                "Alarm evaluation failed device=%s — ingest continues: %s",
                device.id, exc,
            )

    # Single transaction commit
    db.commit()
    logger.info(
        "telemetry.ingest source=%s device=%s tenant=%s keys=%d ts=%s",
        source, device.id, device.tenant_id, keys_saved, ts.isoformat(),
    )
    # Write ingest metric row for /metrics observability endpoint
    try:
        db.add(IngestMetric(
            tenant_id=device.tenant_id,
            device_id=device.id,
            key_count=keys_saved,
        ))
        db.commit()
    except Exception:
        pass  # metric write failure never blocks ingest

    # Phase 7: Anomaly scoring (non-fatal, fire-and-forget per key)
    if numeric_values:
        try:
            from app.services.anomaly_service import score_telemetry_point
            for key, value_num in numeric_values.items():
                score_telemetry_point(db, str(device.id), key, value_num, ts)
            db.commit()
        except Exception as exc:
            logger.debug("anomaly scoring skipped: %s", exc)
            try: db.rollback()
            except: pass

    # Pump Digital Twin — forward reading to twin server (non-fatal)
    if numeric_values:
        try:
            import httpx
            info = device.additional_info or {}
            twin_payload = {
                "timestamp":     ts.isoformat(),
                "T_in":          values.get(info.get("key_temp_inlet",   ""), None),
                "T_out":         values.get(info.get("key_temp_outlet",  ""), None),
                "P_in":          values.get(info.get("key_pressure_in",  ""), None),
                "P_out":         values.get(info.get("key_pressure_out", ""), None),
                "flow_rate":     values.get(info.get("key_flow",         ""), None),
                "motor_power":   values.get(info.get("key_motor_power",  ""), None),
                "rpm":           values.get(info.get("key_speed",        ""), None),
                "vib_nde":       values.get(info.get("key_vib_nde",      ""), None),
                "temp_nde":      values.get(info.get("key_temp_nde",     ""), None),
                "vib_de_motor":  values.get(info.get("key_vib_de",       ""), None),
                "temp_de_motor": values.get(info.get("key_temp_de",      ""), None),
                "vib_de_pump":   values.get(info.get("key_vib_de_pump",  ""), None),
                "temp_de_pump":  values.get(info.get("key_temp_de_pump", ""), None),
                "vib_pp":        values.get(info.get("key_vib_pp",       ""), None),
            }
            # Remove None values — only send keys that exist in this reading
            twin_payload = {k: v for k, v in twin_payload.items() if v is not None}
            if len(twin_payload) > 1:  # more than just timestamp
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.post("http://localhost:8001/ingest", json=twin_payload)
        except Exception as exc:
            logger.debug("twin server ingest skipped: %s", exc)

    # Phase 11: Invalidate Redis cache for this device (non-fatal)
    try:
        from app.services.cache_service import cache as _cache
        await _cache.invalidate_device(str(device.id))
    except Exception as exc:
        logger.debug("cache invalidation skipped: %s", exc)

    # WebSocket broadcast (non-fatal)
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
