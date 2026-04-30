"""
app/main.py — FastAPI application entry point.

Startup sequence (lifespan):
  1. Create / verify DB tables (with retry for Render cold-start)
  2. Start MQTT client in a background thread
     (connects to broker.hivemq.com by default; configurable via env vars)

Shutdown sequence:
  3. Stop MQTT client gracefully

HTTP endpoints are unchanged; MQTT is an additive layer.
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
from app.core.database import engine, Base
from app.models import models  # registers all ORM models on Base before create_all
from app.routers import auth, devices, telemetry, alarms, customers, dashboard, dashboards, ws, user_dashboards

logger = logging.getLogger(__name__)


# ── DB initialisation ─────────────────────────────────────────────────────────

def create_tables_with_retry(retries: int = 5, delay: int = 3) -> None:
    """Create tables, retrying on transient errors common on Render cold start."""
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
                logger.error("Could not initialise database after %d attempts", retries)
                raise


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    create_tables_with_retry()

    # Start MQTT client in a background thread.
    # We pass the running event loop so paho's on_message callback can
    # submit coroutines (ingest_telemetry) back onto it.
    from app.services.mqtt_client import mqtt_client
    mqtt_client.start(loop=asyncio.get_running_loop())

    yield   # application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    mqtt_client.stop()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="IoT Platform API",
    description=(
        "Production-ready IoT platform with HTTP + MQTT telemetry ingestion, "
        "PostgreSQL persistence, and real-time WebSocket push."
    ),
    version="4.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router,            prefix="/api/v1")
app.include_router(devices.router,         prefix="/api/v1")
app.include_router(telemetry.router,       prefix="/api/v1")
app.include_router(alarms.router,          prefix="/api/v1")
app.include_router(customers.router,       prefix="/api/v1")
app.include_router(dashboard.router,       prefix="/api/v1")
app.include_router(dashboards.router,      prefix="/api/v1")
app.include_router(ws.router,              prefix="/api/v1")
app.include_router(user_dashboards.router, prefix="/api/v1")


# ── System endpoints ──────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root():
    return {
        "status":  "ok",
        "message": "IoT Platform API",
        "version": "4.0.0",
        "docs":    "/docs",
    }


@app.get("/health", tags=["System"])
def health():
    """Liveness probe — returns 200 when the process is up."""
    return {"status": "healthy"}


@app.get("/status", tags=["System"])
def status(user_id: str = Depends(get_current_user_id)):
    """Readiness probe — returns MQTT + WebSocket connection state."""
    from app.services.mqtt_client import mqtt_client
    from app.core.websocket_manager import manager as ws_manager
    return {
        "status": "ok",
        "mqtt":   mqtt_client.status(),
        "websocket": {
            "total_clients":  ws_manager.total_clients(),
            "active_devices": ws_manager.active_devices(),
        },
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )
