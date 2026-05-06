"""
app/services/cache_service.py — Redis Cache Layer (Phase 11)

Transparent read-through / write-through cache that sits between
data_service functions and the DB.

Architecture:
    data_service.get_X() → cache_service.get_or_set("key", db_fetch_fn, ttl)
                         → Redis HIT  → return cached dict (no DB query)
                         → Redis MISS → call db_fetch_fn() → cache → return

Graceful degradation:
    If Redis is unavailable (REDIS_URL not set, or connection failure),
    every call falls through to the DB transparently — zero breakage.
    The cache is purely additive: removing it changes performance, not behaviour.

Cache keys (all prefixed "iot:"):
    iot:latest:{device_id}            TTL  10s  — invalidated on ingest
    iot:alarms:{device_id}            TTL  15s
    iot:unified:{device_id}           TTL  30s  — invalidated on ingest
    iot:anomaly:{device_id}:{hours}   TTL  60s
    iot:health:{device_id}            TTL 120s
    iot:baseline:{device_id}          TTL 300s  — updated nightly

Invalidation:
    cache_service.invalidate_device(device_id) — called after ingest
    Deletes: latest + unified (the two most time-sensitive keys)

Usage:
    from app.services.cache_service import cache

    result = await cache.get_or_set(
        key    = f"iot:latest:{device_id}",
        fetch  = lambda: get_latest_telemetry(db, device_id),
        ttl    = 10,
    )
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── TTL constants (seconds) ───────────────────────────────────────────────────
TTL_LATEST     = 10    # latest_telemetry — short, invalidated on ingest
TTL_ALARMS     = 15    # active_alarms — users expect near-real-time alarm updates
TTL_UNIFIED    = 30    # unified_intelligence — combines many sources
TTL_ANOMALY    = 60    # anomaly_summary — z-scores don't change per-second
TTL_HEALTH     = 120   # health_score — recomputed hourly
TTL_BASELINE   = 300   # baseline — updated nightly, very stable


class CacheService:
    """
    Async Redis cache with automatic JSON serialisation and graceful fallback.

    Always instantiate once at module level (singleton).
    Call .setup() once at app startup.
    """

    def __init__(self):
        self._client = None      # redis.asyncio client, set by setup()
        self._enabled = False    # True once Redis is confirmed reachable

    async def setup(self, redis_url: Optional[str]) -> None:
        """
        Connect to Redis. Call once at FastAPI startup.
        Silently disables caching if redis_url is None or connection fails.
        """
        if not redis_url:
            logger.info("cache: REDIS_URL not set — caching disabled, all reads hit DB")
            return

        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                retry_on_timeout=False,
            )
            # Verify connection
            await client.ping()
            self._client  = client
            self._enabled = True
            logger.info("cache: Redis connected — caching enabled (%s)", redis_url)
        except Exception as exc:
            logger.warning(
                "cache: Redis connection failed url=%s error=%s type=%s — falling back to DB-only mode",
                redis_url, exc, type(exc).__name__,
            )
            self._client  = None
            self._enabled = False

    async def teardown(self) -> None:
        """Close Redis connection at app shutdown."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass

    # ── Core get/set ──────────────────────────────────────────────────────────

    async def get_or_set(
        self,
        key: str,
        fetch: Callable[[], Any],
        ttl: int,
    ) -> Any:
        """
        Read-through cache.

        1. Try Redis GET → deserialise JSON → return (cache hit)
        2. On miss: call fetch() → serialise → Redis SET with TTL → return
        3. On any Redis error: call fetch() and return (transparent fallback)

        Args:
            key:   Redis key string (include all discriminators)
            fetch: Zero-argument callable returning a JSON-serialisable dict
            ttl:   Expiry in seconds
        """
        if not self._enabled or not self._client:
            return fetch()

        # ── Cache read ────────────────────────────────────────────────────────
        try:
            raw = await self._client.get(key)
            if raw is not None:
                logger.debug("cache HIT  %s", key)
                return json.loads(raw)
        except Exception as exc:
            logger.warning("cache GET error (%s) — falling through to DB", exc)

        # ── Cache miss → DB fetch ─────────────────────────────────────────────
        logger.debug("cache MISS %s", key)
        result = fetch()

        # ── Cache write ───────────────────────────────────────────────────────
        try:
            await self._client.setex(key, ttl, json.dumps(result, default=str))
        except Exception as exc:
            logger.warning("cache SET error (%s) — result returned without caching", exc)

        return result

    async def delete(self, key: str) -> None:
        """Delete a single cache key. Non-fatal."""
        if not self._enabled or not self._client:
            return
        try:
            await self._client.delete(key)
            logger.debug("cache DEL  %s", key)
        except Exception as exc:
            logger.warning("cache DEL error (%s)", exc)

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a glob pattern (e.g. "iot:latest:*").
        Returns number of keys deleted. Non-fatal.
        """
        if not self._enabled or not self._client:
            return 0
        try:
            keys = await self._client.keys(pattern)
            if keys:
                await self._client.delete(*keys)
                logger.debug("cache DEL pattern=%s count=%d", pattern, len(keys))
            return len(keys)
        except Exception as exc:
            logger.warning("cache DEL pattern error (%s)", exc)
            return 0

    # ── Invalidation helpers ──────────────────────────────────────────────────

    async def invalidate_device(self, device_id: str) -> None:
        """
        Called after telemetry ingest for a device.
        Invalidates the two most time-sensitive keys so next read is fresh.
        """
        await self.delete(f"iot:latest:{device_id}")
        await self.delete(f"iot:unified:{device_id}")

    async def invalidate_alarms(self, device_id: str) -> None:
        """Called after alarm ack/clear/create for a device."""
        await self.delete(f"iot:alarms:{device_id}")
        await self.delete(f"iot:unified:{device_id}")

    async def invalidate_health(self, device_id: str) -> None:
        """Called after health score recompute."""
        await self.delete(f"iot:health:{device_id}")
        await self.delete(f"iot:unified:{device_id}")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Returns True if Redis is reachable."""
        if not self._enabled or not self._client:
            return False
        try:
            await self._client.ping()
            return True
        except Exception:
            return False

    async def stats(self) -> dict:
        """
        Return cache statistics for the system metrics page.
        Returns empty dict if Redis not available.
        """
        if not self._enabled or not self._client:
            return {"enabled": False, "status": "disabled"}

        try:
            info = await self._client.info("stats")
            key_count = await self._client.dbsize()
            return {
                "enabled":          True,
                "status":           "ok",
                "key_count":        key_count,
                "hits":             info.get("keyspace_hits", 0),
                "misses":           info.get("keyspace_misses", 0),
                "hit_rate_pct":     _hit_rate(
                    info.get("keyspace_hits", 0),
                    info.get("keyspace_misses", 0),
                ),
                "evicted_keys":     info.get("evicted_keys", 0),
                "used_memory_human": (await self._client.info("memory")).get(
                    "used_memory_human", "—"
                ),
            }
        except Exception as exc:
            return {"enabled": True, "status": f"error: {exc}"}

    @property
    def enabled(self) -> bool:
        return self._enabled


def _hit_rate(hits: int, misses: int) -> float:
    total = hits + misses
    if total == 0:
        return 0.0
    return round(hits / total * 100, 1)


# ── Module-level singleton ────────────────────────────────────────────────────
# Import and use this everywhere:
#   from app.services.cache_service import cache

cache = CacheService()
