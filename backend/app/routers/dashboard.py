from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.models import Device, TelemetryData, Alarm, AlarmStatus, DeviceStatus
from app.schemas.schemas import DashboardStats

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    total_devices  = db.query(func.count(Device.id)).scalar()
    active_devices = db.query(func.count(Device.id)).filter(
        Device.status == DeviceStatus.ACTIVE
    ).scalar()
    active_alarms  = db.query(func.count(Alarm.id)).filter(
        Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK])
    ).scalar()

    # Use UTC midnight so the count is timezone-correct on Render and anywhere else
    now_utc     = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    telemetry_today = db.query(func.count(TelemetryData.id)).filter(
        TelemetryData.ts >= today_start
    ).scalar()

    return DashboardStats(
        total_devices=total_devices or 0,
        active_devices=active_devices or 0,
        active_alarms=active_alarms or 0,
        telemetry_today=telemetry_today or 0,
    )
