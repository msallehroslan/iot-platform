"""
app/routers/telemetry.py
FIX 5:  rate limiting on ingest (100 req/min per token)
FIX 12: payload validation enforced in schema
FIX 14: bulk history endpoint POST /telemetry/history/{device_id}/bulk
FIX 13: health endpoint (in main.py)
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from typing import List, Optional, Dict
from datetime import datetime, timedelta, timezone
from uuid import UUID
import time
from collections import defaultdict

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, TelemetryData, LatestTelemetry, TelemetryKey, User
from app.schemas.schemas import (
    TelemetryIngest, LatestTelemetryOut, TelemetryDataPoint,
    TelemetryKeyOut, TelemetryKeyUpdate, BulkHistoryRequest, BulkHistoryResponse,
)
from app.services.telemetry_service import ingest_telemetry, DeviceNotFoundError

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

# ── FIX 5: Simple in-process rate limiter (100 req/min per token) ─────────────
# For multi-worker deployments replace with Redis-backed slowapi
_rate_store: Dict[str, list] = defaultdict(list)
_RATE_LIMIT = 100
_RATE_WINDOW = 60  # seconds


def _check_rate_limit(token: str):
    now = time.time()
    window_start = now - _RATE_WINDOW
    timestamps = _rate_store[token]
    # Remove old entries
    _rate_store[token] = [t for t in timestamps if t > window_start]
    if len(_rate_store[token]) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded: 100 requests/minute per device")
    _rate_store[token].append(now)


def _get_device_owned(device_id: UUID, current_user: User, db: Session) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this device")
    return device


@router.post("/ingest/{token}", status_code=200)
async def ingest_telemetry_http(token: str, payload: TelemetryIngest, db: Session = Depends(get_db)):
    _check_rate_limit(token)
    try:
        result = await ingest_telemetry(db=db, token=token, values=payload.values, ts=payload.ts, source="http")
    except DeviceNotFoundError:
        raise HTTPException(status_code=401, detail="Invalid device token")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "ts": result["ts"]}


@router.get("/latest/{device_id}", response_model=List[LatestTelemetryOut])
def get_latest_telemetry(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_device_owned(device_id, current_user, db)
    records = db.query(LatestTelemetry).filter(LatestTelemetry.device_id == device_id).all()
    result = []
    for r in records:
        value = (r.value_num if r.value_num is not None else
                 r.value_bool if r.value_bool is not None else
                 r.value_json if r.value_json is not None else r.value_str)
        result.append(LatestTelemetryOut(key=r.key, value=value, ts=r.ts))
    return result


@router.get("/history/{device_id}", response_model=List[TelemetryDataPoint])
def get_telemetry_history(
    device_id: UUID,
    key: str = Query(...),
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
    records = query.order_by(TelemetryData.ts.asc()).limit(limit).all()
    result = []
    for r in records:
        value = (r.value_num if r.value_num is not None else
                 r.value_bool if r.value_bool is not None else
                 r.value_json if r.value_json is not None else r.value_str)
        result.append(TelemetryDataPoint(ts=r.ts, value=value))
    return result


# FIX 14: bulk history — replaces N serial requests from frontend
@router.post("/history/{device_id}/bulk", response_model=BulkHistoryResponse)
def get_bulk_history(
    device_id: UUID,
    body: BulkHistoryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch history for multiple keys in one request. Replaces N serial GET calls."""
    _get_device_owned(device_id, current_user, db)
    data: Dict[str, List[TelemetryDataPoint]] = {}
    for key in body.keys:
        records = (
            db.query(TelemetryData)
            .filter(and_(TelemetryData.device_id == device_id, TelemetryData.key == key))
            .order_by(TelemetryData.ts.asc())
            .limit(body.limit)
            .all()
        )
        pts = []
        for r in records:
            value = (r.value_num if r.value_num is not None else
                     r.value_bool if r.value_bool is not None else
                     r.value_json if r.value_json is not None else r.value_str)
            pts.append(TelemetryDataPoint(ts=r.ts, value=value))
        data[key] = pts
    return BulkHistoryResponse(data=data)


@router.get("/keys/{device_id}")
def get_telemetry_keys(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_device_owned(device_id, current_user, db)
    keys = db.query(LatestTelemetry.key).filter(LatestTelemetry.device_id == device_id).distinct().all()
    return {"keys": [k[0] for k in keys]}


@router.get("/metadata/{device_id}", response_model=List[TelemetryKeyOut])
def get_telemetry_metadata(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_device_owned(device_id, current_user, db)
    rows = db.query(TelemetryKey).filter(TelemetryKey.device_id == device_id).order_by(TelemetryKey.key).all()
    return [TelemetryKeyOut(key=r.key, label=r.label, unit=r.unit, data_type=r.data_type or "number") for r in rows]


@router.put("/metadata/{device_id}/{key}", response_model=TelemetryKeyOut)
def update_telemetry_metadata(
    device_id: UUID, key: str, body: TelemetryKeyUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _get_device_owned(device_id, current_user, db)
    row = db.query(TelemetryKey).filter(TelemetryKey.device_id == device_id, TelemetryKey.key == key).first()
    if not row:
        row = TelemetryKey(device_id=device_id, key=key, data_type="number")
        db.add(row)
    if body.label     is not None: row.label     = body.label
    if body.unit      is not None: row.unit       = body.unit
    if body.data_type is not None:
        if body.data_type not in ("number", "string", "boolean"):
            raise HTTPException(status_code=400, detail="data_type must be number, string, or boolean")
        row.data_type = body.data_type
    db.commit()
    db.refresh(row)
    return TelemetryKeyOut(key=row.key, label=row.label, unit=row.unit, data_type=row.data_type)


_WINDOWS = {
    "1m": timedelta(minutes=1), "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15), "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1), "6h": timedelta(hours=6),
    "12h": timedelta(hours=12), "24h": timedelta(hours=24), "7d": timedelta(days=7),
}
_AGG_FUNCS = {"avg": func.avg, "min": func.min, "max": func.max, "sum": func.sum, "count": func.count}


@router.get("/aggregate/{device_id}")
def aggregate_telemetry(
    device_id: UUID,
    key: str = Query(...),
    window: str = Query("1h"),
    function: str = Query("avg"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_device_owned(device_id, current_user, db)
    if window not in _WINDOWS:
        raise HTTPException(status_code=400, detail=f"Invalid window. Valid: {', '.join(_WINDOWS)}")
    fn_name = function.lower()
    if fn_name not in _AGG_FUNCS:
        raise HTTPException(status_code=400, detail=f"Invalid function. Valid: {', '.join(_AGG_FUNCS)}")

    to_ts = datetime.now(timezone.utc)
    from_ts = to_ts - _WINDOWS[window]
    base_filter = and_(
        TelemetryData.device_id == device_id, TelemetryData.key == key,
        TelemetryData.ts >= from_ts, TelemetryData.ts <= to_ts,
        TelemetryData.value_num.isnot(None),
    )
    count_result = db.query(func.count(TelemetryData.id)).filter(base_filter).scalar() or 0
    target = TelemetryData.id if fn_name == "count" else TelemetryData.value_num
    result = db.query(_AGG_FUNCS[fn_name](target)).filter(base_filter).scalar()
    if result is not None and isinstance(result, float):
        result = round(result, 4)
    return {"device_id": str(device_id), "key": key, "window": window, "function": fn_name,
            "result": result, "count": count_result,
            "from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()}
