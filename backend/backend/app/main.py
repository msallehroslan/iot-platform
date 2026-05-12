"""
app/main.py — FastAPI application entry point.
FIX 7:  MQTT broker configured via env vars (no public broker default warning)
FIX 11: APScheduler runs telemetry retention purge daily
FIX 13: /health does a real DB ping, returns 503 if Postgres is down
"""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio
import time
import logging

from app.core.config import settings
from app.core.auth_deps import get_current_user_id
from app.core.database import engine, Base, get_db
from app.models import models
from app.routers import intelligence, intelligence_data
from app.routers import (
    auth, devices, telemetry, alarms, customers,
    dashboard, dashboards, ws, user_dashboards,
)
from app.routers import threshold_rules, rpc, widget_templates, metrics, api_keys, observability
from app.routers import widgets
from app.services.cache_service import cache as _cache_service

logger = logging.getLogger(__name__)


def create_tables_with_retry(retries: int = 5, delay: int = 3) -> None:
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables OK")
            return
        except Exception as exc:
            logger.warning("DB init attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(delay)
            else:
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables_with_retry()

    # Phase 4: Redis manager startup (no-op if REDIS_URL not set)
    # Phase 11: start Redis cache service
    await _cache_service.setup(settings.REDIS_URL)

    from app.core.websocket_manager import manager as _ws_manager
    if hasattr(_ws_manager, "startup"):
        await _ws_manager.startup()
        logger.info("RedisManager started")

    from app.services.mqtt_client import mqtt_client
    mqtt_client.start(loop=asyncio.get_running_loop())

    # FIX 11: daily telemetry retention purge + ingest_metrics cleanup
    from app.services.telemetry_service import purge_old_telemetry
    from app.core.database import SessionLocal
    import asyncio as _asyncio

    async def _daily_purge():
        while True:
            await _asyncio.sleep(86400)  # 24h
            db = SessionLocal()
            try:
                purge_old_telemetry(db)
                # Purge ingest_metrics older than 2 hours (only need last 1 min window)
                from app.models.models import IngestMetric
                from datetime import datetime as _dt, timezone as _tz, timedelta
                cutoff = _dt.now(_tz.utc) - timedelta(hours=2)
                db.query(IngestMetric).filter(IngestMetric.ts < cutoff).delete(synchronize_session=False)
                db.commit()
            except Exception as exc:
                logger.error("Telemetry purge failed: %s", exc)
            finally:
                db.close()

    # FIX 8: offline detection — check every 2 minutes, not every 60s
    from app.models.models import Device, DeviceStatus
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import update as sa_update

    async def _offline_check():
        while True:
            await _asyncio.sleep(120)  # every 2 min — less pool pressure
            db = SessionLocal()
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                # Single UPDATE instead of SELECT + loop
                result = db.execute(
                    sa_update(Device)
                    .where(Device.status == DeviceStatus.ACTIVE)
                    .where(Device.last_seen_at < cutoff)
                    .values(status=DeviceStatus.INACTIVE)
                )
                if result.rowcount:
                    db.commit()
                    logger.info("Marked %d device(s) INACTIVE", result.rowcount)
                else:
                    db.rollback()
            except Exception as exc:
                logger.error("Offline check failed: %s", exc)
                try: db.rollback()
                except: pass
            finally:
                db.close()

    # Phase 7: Nightly baseline update
    async def _nightly_baseline():
        while True:
            await _asyncio.sleep(86400)  # every 24h
            db = SessionLocal()
            try:
                from app.services.baseline_service import update_all_baselines
                result = update_all_baselines(db)
                logger.info("Nightly baseline update: %s", result)
            except Exception as exc:
                logger.error("Nightly baseline failed: %s", exc)
            finally:
                db.close()

    # Phase 7: Hourly health scoring
    async def _hourly_health():
        while True:
            await _asyncio.sleep(3600)  # every 1h
            db = SessionLocal()
            try:
                from app.services.health_service import score_all_devices
                result = score_all_devices(db)
                logger.info("Hourly health score: %s", result)
            except Exception as exc:
                logger.error("Hourly health score failed: %s", exc)
            finally:
                db.close()

    # Phase 11: Scheduled RPC dispatcher — checks due commands every 5s.
    # It runs immediately on startup, then sleeps, so due commands do not wait 30s.
    async def _scheduled_rpc_dispatcher():
        while True:
            db = SessionLocal()
            try:
                from app.services.scheduled_rpc_service import dispatch_due_commands
                logger.info("Scheduled RPC dispatcher tick")
                n = await dispatch_due_commands(db)
                if n:
                    logger.info("Scheduled RPC dispatcher fired %d command(s)", n)
            except Exception as exc:
                logger.error("Scheduled RPC dispatcher error: %s", exc)
                try: db.rollback()
                except: pass
            finally:
                db.close()
            await _asyncio.sleep(5)

    purge_task      = _asyncio.create_task(_daily_purge())
    offline_task    = _asyncio.create_task(_offline_check())
    baseline_task   = _asyncio.create_task(_nightly_baseline())
    health_task     = _asyncio.create_task(_hourly_health())
    sched_rpc_task  = _asyncio.create_task(_scheduled_rpc_dispatcher())

    yield

    purge_task.cancel()
    offline_task.cancel()
    baseline_task.cancel()
    health_task.cancel()
    sched_rpc_task.cancel()

    # Phase 4: Redis manager shutdown
    if hasattr(_ws_manager, "shutdown"):
        await _ws_manager.shutdown()
    mqtt_client.stop()
    # Phase 11: cache teardown
    await _cache_service.teardown()


app = FastAPI(
    title="IoT Platform API",
    description="Production IoT platform with HTTP + MQTT ingestion, PostgreSQL, real-time WebSocket.",
    version="5.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Request tracing middleware ────────────────────────────────────────────────
import time, uuid as _uuid

@app.middleware("http")
async def request_tracing_middleware(request, call_next):
    request_id = str(_uuid.uuid4())[:8]
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    # Log slow requests
    if duration_ms > 2000:
        logger.warning("slow_request request_id=%s method=%s path=%s duration_ms=%d status=%d",
                       request_id, request.method, request.url.path, duration_ms, response.status_code)
    elif request.method != "GET" or "/telemetry/" in request.url.path:
        logger.info("request request_id=%s method=%s path=%s duration_ms=%d status=%d",
                    request_id, request.method, request.url.path, duration_ms, response.status_code)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,              prefix="/api/v1")
app.include_router(devices.router,           prefix="/api/v1")
app.include_router(telemetry.router,         prefix="/api/v1")
app.include_router(alarms.router,            prefix="/api/v1")
app.include_router(customers.router,         prefix="/api/v1")
app.include_router(dashboard.router,         prefix="/api/v1")
app.include_router(dashboards.router,        prefix="/api/v1")
app.include_router(ws.router,                prefix="/api/v1")
app.include_router(user_dashboards.router,   prefix="/api/v1")
app.include_router(threshold_rules.router,   prefix="/api/v1")
app.include_router(rpc.router,               prefix="/api/v1")
app.include_router(widget_templates.router,  prefix="/api/v1")
app.include_router(metrics.router,           prefix="/api/v1")
app.include_router(api_keys.router,          prefix="/api/v1")
app.include_router(observability.router,     prefix="/api/v1")
app.include_router(intelligence.router,      prefix="/api/v1")
app.include_router(intelligence_data.router_data, prefix="/api/v1")
app.include_router(widgets.router,           prefix="/api/v1")



@app.get("/", tags=["System"])
def root():
    return {"status": "ok", "message": "IoT Platform API", "version": "5.0.0", "docs": "/docs"}


@app.get("/health", tags=["System"])
def health():
    """FIX 13: real DB ping — returns 503 if Postgres is unreachable."""
    from sqlalchemy import text
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "db": "ok"}
    except Exception as exc:
        logger.error("Health check DB ping failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unhealthy", "db": str(exc)})
    finally:
        db.close()


@app.get("/status", tags=["System"])
def status(user_id: str = Depends(get_current_user_id)):
    from app.services.mqtt_client import mqtt_client
    from app.core.websocket_manager import manager as ws_manager
    return {
        "status": "ok",
        "mqtt": mqtt_client.status(),
        "websocket": {
            "total_clients": ws_manager.total_clients(),
            "active_devices": ws_manager.active_devices(),
        },
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "type": type(exc).__name__})
