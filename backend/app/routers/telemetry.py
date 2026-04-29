"""
app/routers/telemetry.py — HTTP telemetry endpoints.

POST /telemetry/ingest/{token}
    Device-to-backend ingestion via device token — intentionally NO JWT auth.
    Devices authenticate with their token, not user credentials.

GET  /telemetry/latest/{device_id}
GET  /telemetry/history/{device_id}
GET  /telemetry/keys/{device_id}
    Read-only query endpoints — require JWT + tenant ownership check.
    A user can only read telemetry for devices in their own tenant.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, TelemetryData, LatestTelemetry, User
from app.schemas.schemas import TelemetryIngest, LatestTelemetryOut, TelemetryDataPoint
from app.services.telemetry_service import ingest_telemetry, DeviceNotFoundError

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


def _get_device_owned(device_id: UUID, current_user: User, db: Session) -> Device:
    """Verify the device exists and belongs to the caller's tenant."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this device")
    return device


# ── Ingest — device token auth, no JWT required ───────────────────────────────

@router.post("/ingest/{token}", status_code=200)
async def ingest_telemetry_http(
    token: str,
    payload: TelemetryIngest,
    db: Session = Depends(get_db),
):
    """
    HTTP telemetry ingestion. Authenticated by device token, not by user JWT.
    Delegates entirely to ingest_telemetry() — identical logic to MQTT path.
    """
    try:
        result = await ingest_telemetry(
            db=db,
            token=token,
            values=payload.values,
            ts=payload.ts,
            source="http",
        )
    except DeviceNotFoundError:
        raise HTTPException(status_code=401, detail="Invalid device token")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "ts": result["ts"]}


# ── Read endpoints — JWT required, tenant-scoped ──────────────────────────────

@router.get("/latest/{device_id}", response_model=List[LatestTelemetryOut])
def get_latest_telemetry(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = _get_device_owned(device_id, current_user, db)

    records = db.query(LatestTelemetry).filter(
        LatestTelemetry.device_id == device_id
    ).all()

    result = []
    for r in records:
        value = (
            r.value_num  if r.value_num  is not None else
            r.value_bool if r.value_bool is not None else
            r.value_json if r.value_json is not None else
            r.value_str
        )
        result.append(LatestTelemetryOut(key=r.key, value=value, ts=r.ts))
    return result


@router.get("/history/{device_id}", response_model=List[TelemetryDataPoint])
def get_telemetry_history(
    device_id: UUID,
    key: str = Query(..., description="Telemetry key to fetch"),
    limit: int = Query(50, ge=1, le=500),
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_device_owned(device_id, current_user, db)

    query = db.query(TelemetryData).filter(
        and_(TelemetryData.device_id == device_id, TelemetryData.key == key)
    )
    if start_ts:
        query = query.filter(TelemetryData.ts >= start_ts)
    if end_ts:
        query = query.filter(TelemetryData.ts <= end_ts)

    records = query.order_by(TelemetryData.ts.desc()).limit(limit).all()
    records.reverse()

    result = []
    for r in records:
        value = (
            r.value_num  if r.value_num  is not None else
            r.value_bool if r.value_bool is not None else
            r.value_json if r.value_json is not None else
            r.value_str
        )
        result.append(TelemetryDataPoint(ts=r.ts, value=value))
    return result


@router.get("/keys/{device_id}")
def get_telemetry_keys(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_device_owned(device_id, current_user, db)

    keys = db.query(LatestTelemetry.key).filter(
        LatestTelemetry.device_id == device_id
    ).distinct().all()
    return {"keys": [k[0] for k in keys]}
