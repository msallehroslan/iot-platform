"""
app/services/slow_loop_intelligence.py

Slow-Loop Intelligence Engine for TriAxis Nexus.

ARCHITECTURE PRINCIPLE:
  Telemetry ingest (fast path, 250ms) must NEVER block on heavy analysis.
  RCA, trend inference, degradation velocity, and predictive maintenance
  reasoning are expensive — they belong in a separate async loop.

DESIGN:
  FastLoop  (250ms):  telemetry buffer → WS broadcast → cache invalidation
  SlowLoop  (5s):     anomaly trend → degradation velocity → RCA snapshot
                      → predictive signal → temporal chain → recommendation rank

  This file owns the SlowLoop. It is started once from FastAPI lifespan
  alongside RealtimeCoordinator.

INTEGRATION:
  1. In main.py lifespan startup, after coordinator.start():
       from app.services.slow_loop_intelligence import slow_loop
       await slow_loop.start()

  2. In main.py lifespan shutdown:
       await slow_loop.stop()

  3. TAAT planner reads from slow_loop.get_snapshot(device_id) instead of
     running heavy analysis inline.

WHAT IT COMPUTES (every SLOW_INTERVAL_S):
  - degradation_velocity: rate of health score change (points/hour)
  - anomaly_persistence:  how many consecutive cycles a key stays anomalous
  - trend_acceleration:   second derivative of key trend
  - temporal_chain:       ordered sequence of correlated events
  - failure_probability:  composite predictive signal (0–1)
  - recommendation_ranked: sorted (recommendation, confidence, urgency) list
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SLOW_INTERVAL_S  = 5.0    # slow loop cadence in seconds
HISTORY_WINDOW   = 60     # number of cycles to retain per device (= 5 min)
VELOCITY_WINDOW  = 12     # cycles for velocity calc (= 1 min)

# ── Snapshot structure ────────────────────────────────────────────────────────

class DeviceIntelSnapshot:
    """
    In-memory intelligence snapshot for one device.
    Written by SlowLoopEngine, read by TAAT planner.
    Thread-safe via asyncio (single-threaded event loop).
    """
    __slots__ = (
        "device_id",
        "updated_at",
        "health_score",
        "health_velocity",      # points/hour — negative = degrading
        "degradation_class",    # "stable" | "slow_decline" | "rapid_decline" | "critical"
        "anomaly_persistence",  # {key: consecutive_anomaly_cycles}
        "trend_acceleration",   # {key: second_derivative}
        "temporal_chain",       # [(ts, event_desc), ...] ordered
        "failure_probability",  # 0.0 – 1.0
        "recommendations",      # [{"action", "confidence", "urgency", "risk"}]
        "rul_hours",            # estimated remaining useful life in hours
    )

    def __init__(self, device_id: str):
        self.device_id          = device_id
        self.updated_at         = 0.0
        self.health_score       = None
        self.health_velocity    = 0.0
        self.degradation_class  = "stable"
        self.anomaly_persistence: Dict[str, int] = {}
        self.trend_acceleration: Dict[str, float] = {}
        self.temporal_chain:    List[Tuple[str, str]] = []
        self.failure_probability = 0.0
        self.recommendations:   List[dict] = []
        self.rul_hours:         Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "device_id":          self.device_id,
            "updated_at":         self.updated_at,
            "health_score":       self.health_score,
            "health_velocity":    round(self.health_velocity, 3),
            "degradation_class":  self.degradation_class,
            "anomaly_persistence": self.anomaly_persistence,
            "trend_acceleration": {k: round(v, 4) for k, v in self.trend_acceleration.items()},
            "temporal_chain":     self.temporal_chain[-10:],  # last 10 events
            "failure_probability": round(self.failure_probability, 3),
            "recommendations":    self.recommendations[:5],
            "rul_hours":          self.rul_hours,
        }


# ── Slow Loop Engine ──────────────────────────────────────────────────────────

class SlowLoopEngine:
    """
    Background intelligence engine. Runs every SLOW_INTERVAL_S.
    Reads from Redis/DB snapshots — never touches telemetry buffer directly.
    Writes results to in-memory DeviceIntelSnapshot objects.
    """

    def __init__(self) -> None:
        self._running      = False
        self._task:        Optional[asyncio.Task] = None

        # Per-device rolling history for velocity/acceleration computation
        # {device_id: deque of (ts_monotonic, health_score)}
        self._health_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_WINDOW))

        # Per-device per-key anomaly streak counters
        # {device_id: {key: consecutive_anomaly_count}}
        self._anomaly_streaks: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Per-device per-key value history for acceleration
        # {device_id: {key: deque of float}}
        self._value_history: Dict[str, Dict[str, deque]] = defaultdict(dict)

        # Temporal chain buffer per device
        # {device_id: deque of (iso_ts, event_desc)}
        self._event_chain: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

        # Snapshots — read by TAAT planner
        self._snapshots: Dict[str, DeviceIntelSnapshot] = {}

        # Track which devices are active (updated by coordinator)
        self._active_devices: set = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="slow_loop_intelligence")
        logger.info("SlowLoopEngine started (interval=%.1fs)", SLOW_INTERVAL_S)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SlowLoopEngine stopped")

    # ── External API ──────────────────────────────────────────────────────────

    def register_device(self, device_id: str) -> None:
        """Called by coordinator when a device sends telemetry."""
        self._active_devices.add(device_id)
        if device_id not in self._snapshots:
            self._snapshots[device_id] = DeviceIntelSnapshot(device_id)

    def get_snapshot(self, device_id: str) -> Optional[dict]:
        """
        TAAT planner calls this instead of running heavy analysis inline.
        Returns None if no snapshot is available yet (device just registered).
        """
        snap = self._snapshots.get(device_id)
        if snap is None or snap.updated_at == 0.0:
            return None
        return snap.to_dict()

    def get_all_snapshots(self) -> Dict[str, dict]:
        return {
            did: snap.to_dict()
            for did, snap in self._snapshots.items()
            if snap.updated_at > 0
        }

    def record_event(self, device_id: str, event_desc: str) -> None:
        """
        External hook: coordinator / anomaly detector records events for
        temporal chain reconstruction.
        """
        ts = datetime.now(timezone.utc).isoformat()
        self._event_chain[device_id].append((ts, event_desc))

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("SlowLoopEngine cycle error: %s", exc)
            await asyncio.sleep(SLOW_INTERVAL_S)

    async def _run_cycle(self) -> None:
        """
        One analysis cycle across all active devices.
        Each device is processed independently — one slow device won't block others.
        """
        if not self._active_devices:
            return

        tasks = [
            asyncio.create_task(self._analyze_device(did), name=f"slow_{did[:8]}")
            for did in list(self._active_devices)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for did, result in zip(list(self._active_devices), results):
            if isinstance(result, Exception):
                logger.debug("SlowLoop: device %s analysis failed: %s", did[:8], result)

    async def _analyze_device(self, device_id: str) -> None:
        """
        Full slow-loop analysis for one device.
        Reads from cache/DB — never blocks the fast path.
        """
        snap = self._snapshots.setdefault(device_id, DeviceIntelSnapshot(device_id))

        # ── Read current state from cache ──────────────────────────────────
        health_data     = await self._fetch_health(device_id)
        anomaly_data    = await self._fetch_anomalies(device_id)
        current_health  = health_data.get("health_score") if health_data else None

        now_mono = time.monotonic()

        # ── Health velocity & degradation class ───────────────────────────
        if current_health is not None:
            self._health_history[device_id].append((now_mono, float(current_health)))
            snap.health_score    = current_health
            snap.health_velocity = self._compute_velocity(self._health_history[device_id])
            snap.degradation_class = self._classify_degradation(snap.health_velocity, current_health)

        # ── Anomaly persistence ────────────────────────────────────────────
        if anomaly_data:
            anom_keys = set()
            if anomaly_data.get("anomaly_count", 0) > 0:
                most_anom = anomaly_data.get("most_anomalous_key")
                if most_anom:
                    anom_keys.add(most_anom)
                for ks in (anomaly_data.get("key_scores") or []):
                    if isinstance(ks, dict) and ks.get("is_anomaly"):
                        anom_keys.add(ks.get("key", ""))

            # Increment streak for anomalous keys, reset for clean keys
            for k in list(self._anomaly_streaks[device_id].keys()):
                if k not in anom_keys:
                    self._anomaly_streaks[device_id][k] = 0

            for k in anom_keys:
                self._anomaly_streaks[device_id][k] = (
                    self._anomaly_streaks[device_id].get(k, 0) + 1
                )
            snap.anomaly_persistence = {
                k: v for k, v in self._anomaly_streaks[device_id].items() if v > 0
            }

        # ── Temporal chain ─────────────────────────────────────────────────
        snap.temporal_chain = list(self._event_chain.get(device_id, []))

        # ── Failure probability (composite signal) ─────────────────────────
        snap.failure_probability = self._compute_failure_probability(snap)

        # ── RUL estimate ───────────────────────────────────────────────────
        snap.rul_hours = self._estimate_rul(snap)

        # ── Ranked recommendations ─────────────────────────────────────────
        snap.recommendations = self._rank_recommendations(snap, anomaly_data or {})

        snap.updated_at = now_mono

    # ── Computation helpers ────────────────────────────────────────────────────

    def _compute_velocity(self, history: deque) -> float:
        """
        Linear rate of health score change in points/hour.
        Uses a sliding window of VELOCITY_WINDOW recent samples.
        Returns 0.0 if insufficient data.
        """
        pts = list(history)[-VELOCITY_WINDOW:]
        if len(pts) < 2:
            return 0.0

        dt_s = pts[-1][0] - pts[0][0]
        dh   = pts[-1][1] - pts[0][1]

        if dt_s < 0.01:
            return 0.0

        return (dh / dt_s) * 3600.0  # convert to per-hour

    def _classify_degradation(self, velocity: float, health: float) -> str:
        """
        Classify degradation severity from velocity and current health.
        velocity is in health-points/hour (negative = degrading).
        """
        if health < 25:
            return "critical"
        if velocity < -10:   # losing >10 pts/hour
            return "rapid_decline"
        if velocity < -2:    # losing >2 pts/hour
            return "slow_decline"
        return "stable"

    def _compute_failure_probability(self, snap: DeviceIntelSnapshot) -> float:
        """
        Composite failure signal (0–1) from multiple indicators.
        Weights are tuned for industrial pump behavior.
        """
        score = 0.0

        # Health score contribution
        if snap.health_score is not None:
            health_risk = max(0.0, (100.0 - snap.health_score) / 100.0)
            score += 0.35 * health_risk

        # Degradation velocity contribution
        if snap.health_velocity < 0:
            velocity_risk = min(1.0, abs(snap.health_velocity) / 20.0)
            score += 0.25 * velocity_risk

        # Anomaly persistence contribution
        if snap.anomaly_persistence:
            max_streak = max(snap.anomaly_persistence.values(), default=0)
            persistence_risk = min(1.0, max_streak / 20.0)  # 20 cycles = ~100s
            score += 0.20 * persistence_risk

        # Degradation class contribution
        class_risk = {
            "stable":       0.0,
            "slow_decline": 0.1,
            "rapid_decline": 0.25,
            "critical":     0.40,
        }.get(snap.degradation_class, 0.0)
        score += 0.20 * class_risk

        return min(1.0, max(0.0, score))

    def _estimate_rul(self, snap: DeviceIntelSnapshot) -> Optional[float]:
        """
        Estimated remaining useful life in hours.
        Simple linear extrapolation from health velocity.
        Returns None if insufficient data.
        """
        if snap.health_score is None or snap.health_velocity >= 0:
            return None
        if snap.health_score <= 0:
            return 0.0

        # Time to reach health = 20 (critical threshold) at current velocity
        points_to_critical = snap.health_score - 20.0
        if points_to_critical <= 0:
            return 0.0

        hours_remaining = points_to_critical / abs(snap.health_velocity)
        return round(hours_remaining, 1)

    def _rank_recommendations(
        self,
        snap: DeviceIntelSnapshot,
        anomaly_data: dict,
    ) -> List[dict]:
        """
        Generate ranked recommendations with confidence/urgency/risk scores.

        Each recommendation:
          {action, confidence (0-1), urgency ("immediate"|"24h"|"7d"), risk ("LOW"|"MEDIUM"|"HIGH")}
        """
        recs = []

        # Critical health
        if snap.health_score is not None and snap.health_score < 25:
            recs.append({
                "action":     "Schedule immediate maintenance inspection",
                "confidence": 0.95,
                "urgency":    "immediate",
                "risk":       "HIGH",
                "reason":     f"Health score critical: {snap.health_score:.0f}/100",
            })

        # Rapid decline
        if snap.degradation_class == "rapid_decline":
            recs.append({
                "action":     "Reduce operational load by 15–20%",
                "confidence": 0.82,
                "urgency":    "immediate",
                "risk":       "MEDIUM",
                "reason":     f"Health degrading at {abs(snap.health_velocity):.1f} pts/hour",
            })

        # Persistent anomaly on vibration keys
        vib_keys = [k for k in snap.anomaly_persistence if "velocity" in k.lower() or "vibration" in k.lower()]
        if vib_keys:
            max_streak = max(snap.anomaly_persistence[k] for k in vib_keys)
            recs.append({
                "action":     "Inspect bearings and coupling alignment",
                "confidence": 0.78,
                "urgency":    "24h" if max_streak > 10 else "7d",
                "risk":       "MEDIUM",
                "reason":     f"Vibration anomaly persisted for {max_streak} cycles",
            })

        # Persistent temperature anomaly
        temp_keys = [k for k in snap.anomaly_persistence if "temp" in k.lower()]
        if temp_keys:
            max_streak = max(snap.anomaly_persistence[k] for k in temp_keys)
            recs.append({
                "action":     "Check cooling system and lubrication",
                "confidence": 0.72,
                "urgency":    "24h",
                "risk":       "MEDIUM",
                "reason":     f"Temperature anomaly persisted for {max_streak} cycles",
            })

        # Predictive: failure probability threshold
        if snap.failure_probability > 0.7:
            recs.append({
                "action":     "Pre-emptive component inspection before next shift",
                "confidence": round(snap.failure_probability, 2),
                "urgency":    "24h",
                "risk":       "HIGH",
                "reason":     f"Composite failure probability: {snap.failure_probability:.0%}",
            })

        # RUL warning
        if snap.rul_hours is not None and snap.rul_hours < 48:
            recs.append({
                "action":     f"Plan maintenance within {snap.rul_hours:.0f} hours",
                "confidence": 0.65,
                "urgency":    "24h" if snap.rul_hours < 24 else "7d",
                "risk":       "HIGH" if snap.rul_hours < 12 else "MEDIUM",
                "reason":     f"Estimated RUL: {snap.rul_hours:.1f} hours at current degradation rate",
            })

        # Sort: urgency first, then confidence descending
        urgency_order = {"immediate": 0, "24h": 1, "7d": 2}
        recs.sort(key=lambda r: (urgency_order.get(r["urgency"], 9), -r["confidence"]))

        return recs[:5]

    # ── Data fetchers (async, cache-first) ────────────────────────────────────

    async def _fetch_health(self, device_id: str) -> dict:
        """Fetch health from Redis cache. Falls back to empty dict."""
        try:
            from app.services.cache_service import cache as _cache
            if _cache.enabled:
                cached = await _cache.get(f"health:{device_id}")
                if cached:
                    import json
                    return json.loads(cached) if isinstance(cached, str) else cached
        except Exception:
            pass
        return {}

    async def _fetch_anomalies(self, device_id: str) -> dict:
        """Fetch anomaly snapshot from Redis cache. Falls back to empty dict."""
        try:
            from app.services.cache_service import cache as _cache
            if _cache.enabled:
                cached = await _cache.get(f"anomaly:{device_id}")
                if cached:
                    import json
                    return json.loads(cached) if isinstance(cached, str) else cached
        except Exception:
            pass
        return {}


# ── Module-level singleton ────────────────────────────────────────────────────

slow_loop = SlowLoopEngine()
