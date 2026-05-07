"""
app/core/realtime_coordinator.py — Realtime Telemetry Coordinator

Single async coordinator that sits between telemetry ingest and WebSocket
broadcast. Eliminates the three runtime amplification problems identified in
the architecture review:

  1. Per-ingest WS broadcast  → batched flush every FLUSH_INTERVAL_MS
  2. Per-ingest cache invalidation → deferred, coalesced per flush cycle
  3. Per-key anomaly DB queries → accumulated via Welford online algorithm

Architecture:

  ingest_telemetry()
      └─ coordinator.add_telemetry(device_id, values, ts, tenant_id)
              │  (returns immediately — no DB, no WS, no cache work)
              ▼
      [telemetry_buffer: {device_id: {key: value}}]  ← merge, not append
      [dirty_devices: set()]
      [intelligence_dirty: set()]

  flush loop (every 250 ms):
      1. snapshot buffer → clear buffer atomically
      2. broadcast ONE WS packet per device (batched=True)
      3. invalidate cache for dirty devices (coalesced, not per-ingest)
      4. trigger TAAT intelligence event hook if intelligence is dirty

Event hooks (Phase 6/7 foundation):
  _on_intelligence_event(device_id, tenant_id, values)
      → writes incident memory when anomaly threshold met
      → future: triggers TAAT proactive reasoning

Welford online statistics:
  Per (device_id, key): maintains running (n, mean, M2) in memory.
  Anomaly Z-score computed without a DB round-trip.
  Positive score written to DB only when is_anomaly=True or sample_n % 10 == 0.
  This eliminates the 5× DB query per pump ingest seen in the original code.

Lifecycle:
  coordinator.start()  — called from FastAPI lifespan startup
  coordinator.stop()   — called from FastAPI lifespan shutdown
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple
from uuid import UUID

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FLUSH_INTERVAL_MS       = 250        # WS batch flush interval
ANOMALY_THRESHOLD       = 3.0        # |z| > 3.0 → anomaly
MIN_SAMPLES_ZSCORE      = 20         # Welford: min samples before scoring
ANOMALY_SCORE_EVERY_N   = 10         # write non-anomaly scores every Nth sample
INTELLIGENCE_DIRTY_TTL  = 5.0        # seconds between intelligence events per device


# ── Welford online statistics accumulator ────────────────────────────────────

class WelfordAccumulator:
    """
    Knuth/Welford online mean + variance.
    Constant memory, O(1) update, no DB needed.
    """
    __slots__ = ("n", "mean", "M2")

    def __init__(self) -> None:
        self.n:    int   = 0
        self.mean: float = 0.0
        self.M2:   float = 0.0

    def update(self, value: float) -> None:
        self.n += 1
        delta  = value - self.mean
        self.mean += delta / self.n
        delta2 = value - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self) -> float:
        return self.M2 / (self.n - 1) if self.n >= 2 else 0.0

    @property
    def stddev(self) -> float:
        v = self.variance
        return math.sqrt(v) if v > 0 else 0.0

    def z_score(self, value: float) -> Optional[float]:
        if self.n < MIN_SAMPLES_ZSCORE:
            return None
        sd = self.stddev
        if sd < 1e-9:
            return None
        return (value - self.mean) / sd


# ── RealtimeCoordinator ───────────────────────────────────────────────────────

class RealtimeCoordinator:
    """
    Central realtime orchestration layer for TriAxis Nexus.

    All methods are async-safe and non-blocking from the caller's perspective.
    The coordinator owns its own asyncio loop interaction — callers just await
    add_telemetry() and return.
    """

    def __init__(self) -> None:
        # Telemetry buffer: device_id → {key: latest_value}
        # Values are MERGED (last write wins), never appended — bounded memory.
        self._buffer:     Dict[str, Dict[str, Any]] = {}
        self._buffer_ts:  Dict[str, str]             = {}   # device_id → latest ts ISO
        self._buffer_tid: Dict[str, str]             = {}   # device_id → tenant_id str

        # Dirty sets for deferred work
        self._dirty_devices:       Set[str] = set()   # cache invalidation pending
        self._intelligence_dirty:  Set[str] = set()   # TAAT event pending

        # Welford accumulators: (device_id, key) → WelfordAccumulator
        self._stats: Dict[Tuple[str, str], WelfordAccumulator] = {}

        # Intelligence event throttle: device_id → last_event_time (monotonic)
        self._intel_event_ts: Dict[str, float] = {}

        # Background flush task
        self._flush_task:   Optional[asyncio.Task] = None
        self._running:      bool = False
        self._lock:         asyncio.Lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background flush loop. Called once at app startup."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="realtime_coordinator_flush"
        )
        logger.info(
            "RealtimeCoordinator started (flush_interval=%dms anomaly_threshold=%.1f)",
            FLUSH_INTERVAL_MS,
            ANOMALY_THRESHOLD,
        )

    async def stop(self) -> None:
        """Graceful shutdown — flush remaining buffer before cancelling task."""
        self._running = False
        if self._flush_task and not self._flush_task.done():
            try:
                # One final flush so we don't lose buffered telemetry
                await self._flush()
            except Exception as exc:
                logger.warning("RealtimeCoordinator final flush error: %s", exc)
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        logger.info("RealtimeCoordinator stopped")

    # ── Ingest interface ──────────────────────────────────────────────────────

    async def add_telemetry(
        self,
        device_id: str,
        values:    Dict[str, Any],
        ts:        datetime,
        tenant_id: Optional[str] = None,
    ) -> None:
        """
        Buffer incoming telemetry for a device.

        Called from telemetry_service.ingest_telemetry() INSTEAD of the
        direct ws_manager.broadcast() call. Returns immediately — all I/O
        is deferred to the flush cycle.

        Also runs Welford in-memory anomaly detection (no DB query).
        Anomaly events are queued for writing to DB in the flush cycle.
        """
        device_id = str(device_id)
        ts_iso    = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        # Numeric values only for Welford scoring
        numeric_values: Dict[str, float] = {}
        for k, v in values.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_values[k] = float(v)
            elif isinstance(v, str):
                try:
                    numeric_values[k] = float(v)
                except ValueError:
                    pass

        # Run Welford scoring before acquiring lock (pure math, no I/O)
        anomalies_detected: Dict[str, float] = {}  # key → z_score
        for key, value in numeric_values.items():
            acc_key = (device_id, key)
            if acc_key not in self._stats:
                self._stats[acc_key] = WelfordAccumulator()
            acc = self._stats[acc_key]
            z   = acc.z_score(value)   # score BEFORE updating mean
            acc.update(value)
            if z is not None and abs(z) > ANOMALY_THRESHOLD:
                anomalies_detected[key] = z

        async with self._lock:
            # Merge values into buffer (last-write-wins per key)
            if device_id not in self._buffer:
                self._buffer[device_id]     = {}
                self._buffer_ts[device_id]  = ts_iso
                self._buffer_tid[device_id] = str(tenant_id) if tenant_id else ""
            self._buffer[device_id].update(values)
            # Keep the most recent timestamp
            if ts_iso > self._buffer_ts[device_id]:
                self._buffer_ts[device_id] = ts_iso

            # Mark for deferred cache invalidation
            self._dirty_devices.add(device_id)

            # Mark for intelligence event if anomalies were found
            if anomalies_detected:
                self._intelligence_dirty.add(device_id)

        # Log anomalies (outside lock)
        for key, z in anomalies_detected.items():
            logger.info(
                "coordinator.anomaly device=%s key=%s z=%.2f",
                device_id, key, z,
            )

        # Notify IntelligenceCoordinator — marks device dirty for snapshot refresh
        # Non-blocking: create_task() returns immediately, never delays ingest path
        try:
            from app.core.intelligence_coordinator import intelligence_coordinator as _intel
            asyncio.create_task(
                _intel.notify_telemetry(device_id, values, tenant_id or ""),
                name=f"intel_notify_{device_id[:8]}",
            )
        except Exception:
            pass  # IntelligenceCoordinator failure never blocks ingest

    # ── Flush loop ────────────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        """Background task — runs forever, flushes every FLUSH_INTERVAL_MS."""
        interval = FLUSH_INTERVAL_MS / 1000.0
        while self._running:
            sleep_start = time.monotonic()
            try:
                await self._flush()
            except Exception as exc:
                logger.error("RealtimeCoordinator flush error: %s", exc)
            elapsed = time.monotonic() - sleep_start
            remaining = interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _flush(self) -> None:
        """
        Atomic snapshot → broadcast → cache invalidate → intelligence events.
        All three phases run from a single consistent snapshot of the buffer.
        """
        async with self._lock:
            if not self._buffer:
                return
            # Atomic snapshot — swap buffer with empty dict
            snapshot          = self._buffer
            snapshot_ts       = self._buffer_ts
            snapshot_tid      = self._buffer_tid
            dirty             = self._dirty_devices
            intel_dirty       = self._intelligence_dirty
            self._buffer          = {}
            self._buffer_ts       = {}
            self._buffer_tid      = {}
            self._dirty_devices   = set()
            self._intelligence_dirty = set()

        # Phase 1: Broadcast batched WS packets (one per device)
        await self._broadcast_telemetry_batch(snapshot, snapshot_ts)

        # Phase 2: Coalesced cache invalidation (one call per device, not per key)
        await self._invalidate_dirty_devices(dirty)

        # Phase 3: Intelligence event hooks for anomaly-flagged devices
        if intel_dirty:
            await self._fire_intelligence_events(intel_dirty, snapshot, snapshot_tid)

    # ── WS broadcast ─────────────────────────────────────────────────────────

    async def _broadcast_telemetry_batch(
        self,
        snapshot:    Dict[str, Dict[str, Any]],
        snapshot_ts: Dict[str, str],
    ) -> None:
        """
        Send one batched WebSocket message per device.
        The `batched: true` flag lets the frontend know this is a coordinator
        flush (multiple readings may be merged).
        """
        try:
            from app.core.websocket_manager import manager as ws_manager
        except Exception as exc:
            logger.warning("coordinator: cannot import ws_manager: %s", exc)
            return

        for device_id, values in snapshot.items():
            try:
                await ws_manager.broadcast_json(device_id, {
                    "type":      "telemetry",
                    "device_id": device_id,
                    "values":    values,
                    "ts":        snapshot_ts.get(device_id, datetime.now(timezone.utc).isoformat()),
                    "batched":   True,
                })
            except Exception as exc:
                logger.debug(
                    "coordinator: ws broadcast failed device=%s: %s", device_id, exc
                )

    # ── Cache invalidation ────────────────────────────────────────────────────

    async def _invalidate_dirty_devices(self, dirty: Set[str]) -> None:
        """
        Invalidate Redis cache for all devices that received telemetry this cycle.
        One call per device per flush interval — not one call per ingest.
        """
        if not dirty:
            return
        try:
            from app.services.cache_service import cache as _cache
            if not _cache.enabled:
                return
            for device_id in dirty:
                try:
                    await _cache.invalidate_device(device_id)
                except Exception as exc:
                    logger.debug(
                        "coordinator: cache invalidation failed device=%s: %s",
                        device_id, exc,
                    )
        except Exception as exc:
            logger.debug("coordinator: cache service unavailable: %s", exc)

    # ── Intelligence event hooks (Phase 6/7 foundation) ──────────────────────

    async def _fire_intelligence_events(
        self,
        intel_dirty: Set[str],
        snapshot:    Dict[str, Dict[str, Any]],
        snapshot_tid: Dict[str, str],
    ) -> None:
        """
        Fire intelligence events for devices with anomalies detected this cycle.

        Current implementation: write incident memory to TAAT.
        Future: trigger proactive TAAT reasoning chain.

        Throttled: at most once per INTELLIGENCE_DIRTY_TTL seconds per device.
        """
        now = time.monotonic()
        for device_id in intel_dirty:
            last = self._intel_event_ts.get(device_id, 0.0)
            if now - last < INTELLIGENCE_DIRTY_TTL:
                continue
            self._intel_event_ts[device_id] = now

            tenant_id = snapshot_tid.get(device_id, "")
            values    = snapshot.get(device_id, {})

            # Non-blocking — don't let intelligence failures affect telemetry
            asyncio.create_task(
                self._on_intelligence_event(device_id, tenant_id, values),
                name=f"intel_event_{device_id[:8]}",
            )

    async def _on_intelligence_event(
        self,
        device_id: str,
        tenant_id: str,
        values:    Dict[str, Any],
    ) -> None:
        """
        Operational intelligence event handler.

        Phase 6 (now): write anomaly incident to TAAT operational memory.
        Phase 7 (future): trigger TAAT proactive RCA reasoning chain.
        """
        if not tenant_id:
            return

        try:
            from app.core.database import SessionLocal
            from app.services.taat_memory_service import record_incident, MTYPE_INCIDENT
            from app.models.models import Device

            db = SessionLocal()
            try:
                # Resolve device name for human-readable memory
                device = db.query(Device).filter(Device.id == device_id).first()
                device_name = device.name if device else device_id[:8]

                # Identify which keys triggered the anomaly via current Welford state
                anomalous_keys = []
                for key, value in values.items():
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        continue
                    acc = self._stats.get((device_id, key))
                    if acc is None:
                        continue
                    z = acc.z_score(float(value))
                    if z is not None and abs(z) > ANOMALY_THRESHOLD:
                        anomalous_keys.append(f"{key}={value:.2f} (z={z:.1f})")

                if not anomalous_keys:
                    return

                description = f"anomaly detected — {', '.join(anomalous_keys)}"
                record_incident(
                    db        = db,
                    tenant_id = UUID(tenant_id),
                    device_name = device_name,
                    description = description,
                )
                logger.info(
                    "coordinator.intel_event device=%s desc=%s",
                    device_id[:8], description,
                )
            finally:
                db.close()

        except Exception as exc:
            logger.debug(
                "coordinator: intelligence event failed device=%s: %s",
                device_id[:8], exc,
            )

    # ── Observability ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return current coordinator stats for /status endpoint."""
        return {
            "running":             self._running,
            "buffered_devices":    len(self._buffer),
            "dirty_devices":       len(self._dirty_devices),
            "intelligence_dirty":  len(self._intelligence_dirty),
            "welford_accumulators": len(self._stats),
            "flush_interval_ms":   FLUSH_INTERVAL_MS,
        }


# ── Module-level singleton ────────────────────────────────────────────────────
# Import and use everywhere:
#   from app.core.realtime_coordinator import coordinator

coordinator = RealtimeCoordinator()
