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

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return overview stats scoped to the authenticated user's tenant.
    Each user only sees counts for their own devices, alarms, and telemetry.
    """
    tid = current_user.tenant_id

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

    return DashboardStats(
        total_devices=total_devices or 0,
        active_devices=active_devices or 0,
        active_alarms=active_alarms or 0,
        telemetry_today=telemetry_today or 0,
    )
