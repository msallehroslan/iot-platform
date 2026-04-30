"""
app/routers/alarms.py — Alarm management endpoints.

All routes require a valid JWT. Ownership enforced via tenant:
  - list/get/ack/clear/delete: alarm.device.tenant_id must equal user.tenant_id
  - create: device.tenant_id must equal user.tenant_id

The auto-alarm rules (temperature, humidity, voltage thresholds) fire from
telemetry_service.py during ingest — they do NOT use this router. This router
is for the frontend UI to read, acknowledge, and clear alarms.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_
from typing import List, Optional
from datetime import datetime, timezone
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user, require_admin, require_tenant_member
from app.models.models import Alarm, Device, AlarmStatus, User
from app.schemas.schemas import AlarmCreate, AlarmOut, AlarmWithDevice

router = APIRouter(prefix="/alarms", tags=["Alarms"])


def _get_alarm_owned(alarm_id: UUID, current_user: User, db: Session) -> Alarm:
    """
    Fetch an alarm and verify its device belongs to the caller's tenant.
    Uses a join so we only hit the DB once.
    """
    alarm = (
        db.query(Alarm)
        .options(joinedload(Alarm.device))
        .filter(Alarm.id == alarm_id)
        .first()
    )
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarm not found")
    if not alarm.device or alarm.device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this alarm")
    return alarm


@router.get("/", response_model=List[AlarmWithDevice])
def list_alarms(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    device_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List alarms scoped to the authenticated user's tenant."""
    # Join through Device to enforce tenant ownership at the query level
    query = (
        db.query(Alarm)
        .join(Device, Alarm.device_id == Device.id)
        .options(joinedload(Alarm.device))
        .filter(Device.tenant_id == current_user.tenant_id)
    )
    if status:
        query = query.filter(Alarm.status == status)
    if severity:
        query = query.filter(Alarm.severity == severity)
    if device_id:
        query = query.filter(Alarm.device_id == device_id)

    alarms = query.order_by(Alarm.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for alarm in alarms:
        alarm_dict = {
            "id":          alarm.id,
            "device_id":   alarm.device_id,
            "alarm_type":  alarm.alarm_type,
            "severity":    alarm.severity,
            "status":      alarm.status,
            "details":     alarm.details,
            "propagate":   alarm.propagate,
            "start_ts":    alarm.start_ts,
            "end_ts":      alarm.end_ts,
            "ack_ts":      alarm.ack_ts,
            "clear_ts":    alarm.clear_ts,
            "ack_by":      alarm.ack_by,
            "cleared_by":  alarm.cleared_by,
            "created_at":  alarm.created_at,
            "device_name": alarm.device.name if alarm.device else None,
        }
        result.append(AlarmWithDevice(**alarm_dict))
    return result


@router.post("/", response_model=AlarmOut, status_code=201)
def create_alarm(
    alarm_in: AlarmCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Manually create an alarm. Device must belong to user's tenant."""
    device = db.query(Device).filter(Device.id == alarm_in.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this device")

    alarm = Alarm(
        device_id=alarm_in.device_id,
        alarm_type=alarm_in.alarm_type,
        severity=alarm_in.severity,
        details=alarm_in.details,
        propagate=alarm_in.propagate,
        status=AlarmStatus.ACTIVE_UNACK,
    )
    db.add(alarm)
    db.commit()
    db.refresh(alarm)
    return alarm


@router.get("/{alarm_id}", response_model=AlarmWithDevice)
def get_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alarm = _get_alarm_owned(alarm_id, current_user, db)
    return AlarmWithDevice(
        **{**alarm.__dict__, "device_name": alarm.device.name if alarm.device else None}
    )


@router.post("/{alarm_id}/ack", response_model=AlarmOut)
def acknowledge_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_member),
):
    alarm = _get_alarm_owned(alarm_id, current_user, db)

    if alarm.status == AlarmStatus.ACTIVE_UNACK:
        alarm.status = AlarmStatus.ACTIVE_ACK
    elif alarm.status == AlarmStatus.CLEARED_UNACK:
        alarm.status = AlarmStatus.CLEARED_ACK
    else:
        raise HTTPException(status_code=400, detail="Alarm already acknowledged")

    alarm.ack_ts = datetime.now(timezone.utc)
    alarm.ack_by = current_user.email   # record who acknowledged it
    db.commit()
    db.refresh(alarm)
    return alarm


@router.post("/{alarm_id}/clear", response_model=AlarmOut)
def clear_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_member),
):
    alarm = _get_alarm_owned(alarm_id, current_user, db)

    if alarm.status in [AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]:
        alarm.status     = AlarmStatus.CLEARED_ACK if alarm.ack_ts else AlarmStatus.CLEARED_UNACK
        alarm.clear_ts   = datetime.now(timezone.utc)
        alarm.end_ts     = datetime.now(timezone.utc)
        alarm.cleared_by = current_user.email   # record who cleared it
    else:
        raise HTTPException(status_code=400, detail="Alarm already cleared")

    db.commit()
    db.refresh(alarm)
    return alarm


@router.delete("/{alarm_id}", status_code=204)
def delete_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    alarm = _get_alarm_owned(alarm_id, current_user, db)
    db.delete(alarm)
    db.commit()
