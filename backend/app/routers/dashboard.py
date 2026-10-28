"""
app/routers/dashboard.py — Overview stats endpoint.

Requires JWT — stats are scoped to the authenticated user's tenant.
Users only see counts for their own devices, alarms, and telemetry.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, TelemetryData, Alarm, AlarmStatus, DeviceStatus, User
from app.schemas.schemas import DashboardStats
from app.services.data_service import _sync_cache
from app.services.cache_service import TTL_DASH_STATS

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _fetch_stats(db: Session, tid) -> dict:
    # Only count devices belonging to this tenant
    total_devices = db.query(func.count(Device.id)).filter(
        Device.tenant_id == tid
    ).scalar()

    active_devices = db.query(func.count(Device.id)).filter(
        Device.tenant_id == tid,
        Device.status == DeviceStatus.ACTIVE,
    ).scalar()

    # Alarms scoped via device → tenant
    active_alarms = (
        db.query(func.count(Alarm.id))
        .join(Device, Alarm.device_id == Device.id)
        .filter(
            Device.tenant_id == tid,
            Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
        )
        .scalar()
    )

    # Telemetry today scoped via device → tenant
    now_utc     = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    telemetry_today = (
        db.query(func.count(TelemetryData.id))
        .join(Device, TelemetryData.device_id == Device.id)
        .filter(
            Device.tenant_id == tid,
            TelemetryData.ts >= today_start,
        )
        .scalar()
    )

    return {
        "total_devices":   total_devices or 0,
        "active_devices":  active_devices or 0,
        "active_alarms":   active_alarms or 0,
        "telemetry_today": telemetry_today or 0,
    }


@router.get("/stats", response_model=DashboardStats)
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return overview stats scoped to the authenticated user's tenant.
    Each user only sees counts for their own devices, alarms, and telemetry.

    Cached per-tenant for TTL_DASH_STATS seconds — this endpoint was
    previously uncached and ran 4 full-table-ish COUNT queries on every
    call, which under many concurrent dashboard users (e.g. 100 users
    polling at once) exhausted the DB connection pool
    (sqlalchemy.exc.TimeoutError: QueuePool limit ... reached) long
    before the pool-size increase alone would have helped — all those
    users share the exact same tenant-scoped counts anyway, so a short
    cache collapses N identical queries into 1.
    """
    tid = current_user.tenant_id
    stats = _sync_cache(
        key   = f"iot:dashstats:{tid}",
        fetch = lambda: _fetch_stats(db, tid),
        ttl   = TTL_DASH_STATS,
    )
    return stats
