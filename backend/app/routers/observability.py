"""
app/routers/observability.py — System-level observability.

GET /system/health    — public health check (DB ping)
GET /system/metrics   — admin: CPU, memory, DB pool, WS connections
GET /system/audit     — admin: recent audit log entries
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from typing import List

from app.core.database import get_db, engine
from app.core.auth_deps import require_admin
from app.models.models import AuditLog, User

router = APIRouter(prefix="/system", tags=["Observability"])
logger = logging.getLogger(__name__)

# Track startup time for uptime calculation
_START_TIME = time.time()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Public endpoint — used by load balancer and uptime monitors."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "uptime_seconds": int(time.time() - _START_TIME)}
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "degraded", "error": str(exc)})


@router.get("/metrics")
def system_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    System-level metrics for the admin panel.
    Includes CPU, memory, DB pool, WebSocket connections, Redis status.
    """
    # ── Process metrics ───────────────────────────────────────────────────────
    try:
        import psutil, os
        proc       = psutil.Process(os.getpid())
        cpu_pct    = proc.cpu_percent(interval=0.1)
        mem_mb     = proc.memory_info().rss / 1024 / 1024
        sys_cpu    = psutil.cpu_percent(interval=0.1)
        sys_mem    = psutil.virtual_memory()
    except Exception:
        cpu_pct = mem_mb = sys_cpu = 0
        sys_mem = None

    # ── DB pool ───────────────────────────────────────────────────────────────
    pool_status = {}
    try:
        pool = engine.pool
        pool_status = {
            "pool_size":       pool.size(),
            "checked_out":     pool.checkedout(),
            "overflow":        pool.overflow(),
            "checked_in":      pool.checkedin(),
        }
    except Exception:
        pass

    # ── DB latency ────────────────────────────────────────────────────────────
    db_latency_ms = None
    try:
        t0 = time.perf_counter()
        db.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    except Exception:
        pass

    # ── WebSocket ─────────────────────────────────────────────────────────────
    ws_info = {}
    try:
        from app.core.websocket_manager import manager
        ws_info = {
            "total_clients":  manager.total_clients(),
            "active_devices": manager.active_devices(),
            "backend":        "redis" if hasattr(manager, "_redis_url") else "in_process",
        }
    except Exception:
        pass

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_status = "not_configured"
    try:
        from app.core.config import settings
        if settings.redis_enabled:
            import redis as _redis
            r = _redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            r.ping()
            redis_status = "ok"
    except Exception as exc:
        redis_status = f"error: {exc}"

    return {
        "ts":            datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - _START_TIME),
        "process": {
            "cpu_pct":    cpu_pct,
            "mem_mb":     round(mem_mb, 1),
        },
        "system": {
            "cpu_pct":    sys_cpu,
            "mem_pct":    sys_mem.percent if sys_mem else None,
            "mem_used_gb": round(sys_mem.used/1024**3, 2) if sys_mem else None,
        },
        "database":   {**pool_status, "latency_ms": db_latency_ms},
        "websocket":  ws_info,
        "redis":      redis_status,
    }


@router.get("/audit")
def get_audit_log(
    limit:  int      = Query(50, ge=1, le=200),
    action: str      = Query(None),
    db:     Session  = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Recent audit log entries for the tenant."""
    q = db.query(AuditLog).filter(
        AuditLog.tenant_id == current_user.tenant_id
    )
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id":          str(r.id),
            "action":      r.action,
            "resource":    r.resource,
            "resource_id": r.resource_id,
            "user_email":  r.user_email,
            "detail":      r.detail,
            "created_at":  r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
