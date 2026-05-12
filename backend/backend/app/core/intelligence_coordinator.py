"""
app/core/intelligence_coordinator.py — Intelligence Coordinator

Sits alongside RealtimeCoordinator. Consumes telemetry events from the
realtime layer, maintains per-device intelligence snapshots, and drives
the intelligence pipeline asynchronously — completely off the hot ingest path.

Architecture:

    RealtimeCoordinator.add_telemetry()
        └→ intelligence_coordinator.notify_telemetry(device_id, values, tenant_id)
               │  (non-blocking — returns immediately)
               ▼
        [_dirty: {device_id: DirtyEntry}]   ← tracks what changed and when

    _intelligence_loop() every INTEL_FLUSH_INTERVAL_MS:
        For each dirty device:
          1. Recompute intelligence snapshot (health + anomaly + trends + unified)
          2. Write snapshot to Redis (iot:snapshot:{device_id})
          3. Detect operational changes (status transitions, risk escalations)
          4. Write incident/degradation memories to TAAT AgentMemory
          5. Clear dirty flag

    Snapshot structure (Redis key iot:snapshot:{device_id}, TTL SNAPSHOT_TTL_S):
    {
        "device_id":       str,
        "status":          "HEALTHY|WARNING|CRITICAL|OFFLINE",
        "risk":            "LOW|MEDIUM|HIGH|CRITICAL",
        "health_score":    float,
        "anomaly_count":   int,
        "top_anomaly_key": str|None,
        "trend_summary":   {key: "RISING|FALLING|STABLE|..."},
        "recommendation":  str,
        "degradation_rate": float,     # health score drop per hour (positive = degrading)
        "maintenance_due":  bool,
        "confidence":       str,
        "updated_at":       str,       # ISO UTC
    }

Causal reasoning (Priority 8):
    _assess_causal_degradation() correlates telemetry patterns deterministically:
        - velocity + temperature rising together → lubrication/bearing degradation
        - health score drop velocity tracked over 10-minute rolling window
        - anomaly persistence (same key anomalous multiple cycles) → escalation
    LLM narrates; this function produces the facts.

Design principles:
    - Never blocks ingest path
    - All DB/Redis I/O in background tasks
    - Graceful degradation if Redis unavailable
    - No coupling to TAAT planner/executor
    - Writes only to: Redis snapshot + AgentMemory table
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

INTEL_FLUSH_INTERVAL_MS  = 30_000   # recompute intelligence every 30s — reduces DB pressure on free tier
SNAPSHOT_TTL_S           = 120      # Redis snapshot TTL — refreshed every flush cycle
SNAPSHOT_KEY             = "iot:snapshot:{device_id}"
DEGRADATION_WINDOW_S     = 600      # 10-min window for degradation velocity
ANOMALY_PERSIST_THRESHOLD = 3       # same key anomalous N cycles → escalation memory
MEMORY_COOLDOWN_S        = 300      # min seconds between memory writes per device

# Causal correlation keys for pump telemetry — extensible
VELOCITY_KEYS  = {"motor_de_velocity", "motor_nde_velocity", "pump_de_velocity", "pump_nde_velocity"}
THERMAL_KEYS   = {"temperature"}


# ── Dirty entry ───────────────────────────────────────────────────────────────

@dataclass
class DirtyEntry:
    device_id:  str
    tenant_id:  str
    values:     Dict[str, Any]  = field(default_factory=dict)
    marked_at:  float           = field(default_factory=time.monotonic)


# ── IntelligenceCoordinator ───────────────────────────────────────────────────

class IntelligenceCoordinator:
    """
    Background intelligence engine. Decouples intelligence computation from
    the realtime telemetry path entirely.
    """

    def __init__(self) -> None:
        self._dirty:       Dict[str, DirtyEntry]  = {}     # device_id → DirtyEntry
        self._lock:        asyncio.Lock            = asyncio.Lock()
        self._task:        Optional[asyncio.Task]  = None
        self._running:     bool                    = False

        # Previous snapshot state for change detection
        self._prev_status:    Dict[str, str]   = {}   # device_id → last status
        self._prev_health:    Dict[str, float] = {}   # device_id → last health_score
        self._health_history: Dict[str, list]  = {}   # device_id → [(ts, score), ...]
        self._anomaly_persist:Dict[str, Dict[str, int]] = {}  # device_id → {key: count}
        self._last_memory_write: Dict[str, float] = {}  # device_id → monotonic ts

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._intelligence_loop(), name="intelligence_coordinator_loop"
        )
        logger.info(
            "IntelligenceCoordinator started (flush_interval=%dms snapshot_ttl=%ds)",
            INTEL_FLUSH_INTERVAL_MS, SNAPSHOT_TTL_S,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("IntelligenceCoordinator stopped")

    # ── Ingest interface ──────────────────────────────────────────────────────

    async def notify_telemetry(
        self,
        device_id: str,
        values:    Dict[str, Any],
        tenant_id: str,
    ) -> None:
        """
        Called by RealtimeCoordinator on every add_telemetry().
        Non-blocking — just marks the device dirty.
        """
        async with self._lock:
            if device_id in self._dirty:
                self._dirty[device_id].values.update(values)
            else:
                self._dirty[device_id] = DirtyEntry(
                    device_id=device_id,
                    tenant_id=tenant_id,
                    values=dict(values),
                )

    # ── Intelligence loop ─────────────────────────────────────────────────────

    async def _intelligence_loop(self) -> None:
        interval = INTEL_FLUSH_INTERVAL_MS / 1000.0
        # First run: wait one full interval so startup DB pressure is avoided
        await asyncio.sleep(interval)
        while self._running:
            t0 = time.monotonic()
            try:
                await self._process_dirty()
            except Exception as exc:
                logger.error("IntelligenceCoordinator loop error: %s", exc)
            elapsed = time.monotonic() - t0
            remaining = interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _process_dirty(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._dirty)
            self._dirty = {}

        # Process sequentially — each opens a DB session.
        # Concurrent tasks would exhaust the connection pool on free tier.
        for device_id, entry in snapshot.items():
            try:
                await self._update_device_intelligence(entry)
            except Exception as exc:
                logger.debug("intel_coordinator: device %s failed: %s", device_id[:8], exc)

    # ── Device intelligence update ─────────────────────────────────────────────

    async def _update_device_intelligence(self, entry: DirtyEntry) -> None:
        """
        Recompute intelligence snapshot for one device.
        Runs as a background task — never blocks the coordinator loop.
        """
        device_id = entry.device_id
        tenant_id = entry.tenant_id

        try:
            from app.core.database import SessionLocal
            from app.services.data_service import get_unified_intelligence
            from app.models.models import Device

            db = SessionLocal()
            try:
                device = db.query(Device).filter(Device.id == device_id).first()
                if not device:
                    return

                # Build unified intelligence (sub-functions are cached — fast on hit)
                intel = get_unified_intelligence(db, device_id, device=device)

                # Build snapshot
                health  = intel.get("health", {})
                anomaly = intel.get("anomaly", {})
                trends  = intel.get("trends", {})

                health_score  = health.get("health_score") or 0.0
                anomaly_count = anomaly.get("anomaly_count", 0)
                top_anomaly   = anomaly.get("most_anomalous_key")

                # Degradation velocity — health drop rate over rolling window
                degradation_rate = self._compute_degradation_rate(device_id, health_score)

                # Causal assessment (deterministic)
                causal = _assess_causal_degradation(entry.values, trends, health_score)

                snapshot = {
                    "device_id":       device_id,
                    "status":          intel.get("status", "UNKNOWN"),
                    "risk":            intel.get("risk", "LOW"),
                    "health_score":    round(health_score, 2),
                    "anomaly_count":   anomaly_count,
                    "top_anomaly_key": top_anomaly,
                    "trend_summary":   trends,
                    "recommendation":  intel.get("recommendation", ""),
                    "degradation_rate": round(degradation_rate, 4),
                    "maintenance_due": health.get("maintenance_due", False),
                    "confidence":      intel.get("confidence", "low"),
                    "causal_signals":  causal,
                    "updated_at":      datetime.now(timezone.utc).isoformat(),
                }

                # Write to Redis
                await self._write_snapshot(device_id, snapshot)

                # Detect operational changes → write TAAT memories
                await self._detect_and_record_changes(
                    db, device_id, tenant_id, snapshot,
                    anomaly, trends, entry.values, causal,
                )

            finally:
                db.close()

        except Exception as exc:
            logger.debug(
                "intel_coordinator: update failed device=%s: %s",
                device_id[:8], exc,
            )

    # ── Snapshot persistence ──────────────────────────────────────────────────

    async def _write_snapshot(self, device_id: str, snapshot: dict) -> None:
        """Write snapshot to Redis. Non-fatal if Redis unavailable."""
        try:
            from app.services.cache_service import cache
            if not cache.enabled or not cache._client:
                return
            key = SNAPSHOT_KEY.format(device_id=device_id)
            await cache._client.setex(
                key,
                SNAPSHOT_TTL_S,
                json.dumps(snapshot, default=str),
            )
        except Exception as exc:
            logger.debug("intel_coordinator: snapshot write failed: %s", exc)

    # ── Degradation velocity ──────────────────────────────────────────────────

    def _compute_degradation_rate(self, device_id: str, current_score: float) -> float:
        """
        Track health score history and compute drop rate (points per hour).
        Positive = degrading, negative = recovering.
        """
        now = time.monotonic()
        if device_id not in self._health_history:
            self._health_history[device_id] = []

        history = self._health_history[device_id]
        history.append((now, current_score))

        # Prune entries outside rolling window
        cutoff = now - DEGRADATION_WINDOW_S
        history[:] = [(t, s) for t, s in history if t >= cutoff]
        self._health_history[device_id] = history

        if len(history) < 2:
            return 0.0

        oldest_t, oldest_s = history[0]
        newest_t, newest_s = history[-1]
        elapsed_hours = (newest_t - oldest_t) / 3600.0

        if elapsed_hours < 1e-6:
            return 0.0

        # Positive = score dropped (degrading), negative = score rose (recovering)
        return (oldest_s - newest_s) / elapsed_hours

    # ── Operational change detection → TAAT memory ───────────────────────────

    async def _detect_and_record_changes(
        self,
        db,
        device_id:  str,
        tenant_id:  str,
        snapshot:   dict,
        anomaly:    dict,
        trends:     dict,
        values:     dict,
        causal:     dict,
    ) -> None:
        """
        Detect meaningful operational changes and write TAAT incident memories.
        Throttled: at most one memory write per MEMORY_COOLDOWN_S per device.
        """
        now        = time.monotonic()
        last_write = self._last_memory_write.get(device_id, 0.0)
        if now - last_write < MEMORY_COOLDOWN_S:
            return

        prev_status = self._prev_status.get(device_id)
        curr_status = snapshot["status"]
        health      = snapshot["health_score"]
        degradation = snapshot["degradation_rate"]

        memory_lines = []

        # 1. Status escalation
        STATUS_RANK = {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2, "OFFLINE": 3}
        if (prev_status and
                STATUS_RANK.get(curr_status, 0) > STATUS_RANK.get(prev_status, 0)):
            memory_lines.append(
                f"status escalated {prev_status} → {curr_status} "
                f"(health={health:.0f})"
            )

        # 2. Rapid degradation
        if degradation > 5.0:  # >5 health points/hour
            memory_lines.append(
                f"rapid degradation detected: health dropping {degradation:.1f} "
                f"pts/hr (current={health:.0f})"
            )

        # 3. Anomaly persistence — same key anomalous multiple cycles
        top_key = snapshot.get("top_anomaly_key")
        if top_key:
            persist = self._anomaly_persist.setdefault(device_id, {})
            persist[top_key] = persist.get(top_key, 0) + 1
            if persist[top_key] >= ANOMALY_PERSIST_THRESHOLD:
                memory_lines.append(
                    f"persistent anomaly on {top_key} — flagged {persist[top_key]} "
                    f"consecutive cycles"
                )
                persist[top_key] = 0  # reset after recording
        else:
            # Reset persistence counters when no anomaly
            self._anomaly_persist.pop(device_id, None)

        # 4. Causal signals
        if causal.get("pattern"):
            memory_lines.append(f"causal pattern: {causal['pattern']}")

        # Update state regardless
        self._prev_status[device_id] = curr_status
        self._prev_health[device_id] = health

        if not memory_lines:
            return

        # Write to TAAT AgentMemory
        try:
            from uuid import UUID as _UUID
            from app.services.taat_memory_service import save_memory, MTYPE_INCIDENT
            from app.models.models import Device

            device = db.query(Device).filter(Device.id == device_id).first()
            device_name = device.name if device else device_id[:8]

            content = f"{device_name}: " + "; ".join(memory_lines)
            save_memory(
                db,
                tenant_id   = _UUID(tenant_id),
                memory_type = MTYPE_INCIDENT,
                content     = content,
            )
            self._last_memory_write[device_id] = now
            logger.info(
                "intel_coordinator.memory device=%s content=%s",
                device_id[:8], content[:80],
            )
        except Exception as exc:
            logger.debug("intel_coordinator: memory write failed: %s", exc)

    # ── Snapshot read API ─────────────────────────────────────────────────────

    async def get_snapshot(self, device_id: str) -> Optional[dict]:
        """
        Read snapshot from Redis. Returns None if not cached yet.
        Used by dashboard preload and TAAT context builder.
        """
        try:
            from app.services.cache_service import cache
            if not cache.enabled or not cache._client:
                return None
            key = SNAPSHOT_KEY.format(device_id=device_id)
            raw = await cache._client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    # ── Observability ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "running":           self._running,
            "dirty_queue":       len(self._dirty),
            "tracked_devices":   len(self._health_history),
            "flush_interval_ms": INTEL_FLUSH_INTERVAL_MS,
        }


# ── Causal degradation assessment (Priority 8) ───────────────────────────────

def _assess_causal_degradation(
    values: Dict[str, Any],
    trends: Dict[str, str],
    health_score: float,
) -> dict:
    """
    Deterministic causal reasoning for industrial pump telemetry.
    Correlates velocity + thermal signals to identify degradation patterns.

    LLM narrates; this function produces the facts.

    Returns:
        {
            "pattern":     str | None,   # identified causal pattern
            "signals":     list[str],    # contributing observations
            "severity":    "LOW|MEDIUM|HIGH",
            "confidence":  float,        # 0.0–1.0
        }
    """
    signals    = []
    pattern    = None
    severity   = "LOW"
    confidence = 0.0

    # Extract numeric values
    numeric: Dict[str, float] = {}
    for k, v in values.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            numeric[k] = float(v)

    # ── Signal 1: Velocity rising ─────────────────────────────────────────────
    velocity_rising = [
        k for k in VELOCITY_KEYS
        if trends.get(k) in ("RISING", "VOLATILE", "SPIKE")
    ]
    if velocity_rising:
        signals.append(f"velocity rising: {', '.join(velocity_rising)}")

    # ── Signal 2: Temperature rising ─────────────────────────────────────────
    thermal_rising = [
        k for k in THERMAL_KEYS
        if trends.get(k) in ("RISING", "SPIKE")
    ]
    if thermal_rising:
        signals.append(f"temperature rising: {', '.join(thermal_rising)}")

    # ── Signal 3: Velocity asymmetry (DE vs NDE imbalance) ────────────────────
    motor_de  = numeric.get("motor_de_velocity")
    motor_nde = numeric.get("motor_nde_velocity")
    pump_de   = numeric.get("pump_de_velocity")
    pump_nde  = numeric.get("pump_nde_velocity")

    if motor_de is not None and motor_nde is not None and motor_nde > 1:
        imbalance = abs(motor_de - motor_nde) / motor_nde
        if imbalance > 0.15:
            signals.append(
                f"motor bearing asymmetry {imbalance:.0%} "
                f"(DE={motor_de:.0f} NDE={motor_nde:.0f})"
            )

    if pump_de is not None and pump_nde is not None and pump_nde > 1:
        imbalance = abs(pump_de - pump_nde) / pump_nde
        if imbalance > 0.15:
            signals.append(
                f"pump bearing asymmetry {imbalance:.0%} "
                f"(DE={pump_de:.0f} NDE={pump_nde:.0f})"
            )

    # ── Causal pattern matching ───────────────────────────────────────────────
    # Pattern: velocity + temperature both rising → bearing/lubrication degradation
    if velocity_rising and thermal_rising:
        pattern    = "bearing/lubrication degradation — velocity and temperature co-rising"
        severity   = "HIGH" if health_score < 60 else "MEDIUM"
        confidence = 0.82

    # Pattern: velocity volatile + health declining → mechanical looseness
    elif velocity_rising and health_score < 70:
        pattern    = "mechanical degradation — elevated vibration with health decline"
        severity   = "MEDIUM"
        confidence = 0.65

    # Pattern: temperature alone rising + health low → thermal stress
    elif thermal_rising and health_score < 65:
        pattern    = "thermal stress — temperature rising without vibration increase"
        severity   = "MEDIUM"
        confidence = 0.60

    # Pattern: bearing asymmetry detected
    elif any("asymmetry" in s for s in signals):
        pattern    = "bearing wear — DE/NDE velocity imbalance detected"
        severity   = "MEDIUM"
        confidence = 0.70

    return {
        "pattern":    pattern,
        "signals":    signals,
        "severity":   severity,
        "confidence": round(confidence, 2),
    }


# ── Module-level singleton ────────────────────────────────────────────────────

intelligence_coordinator = IntelligenceCoordinator()
