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
from sqlalchemy import and_, func
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, TelemetryData, LatestTelemetry, TelemetryKey, User
from app.schemas.schemas import TelemetryIngest, LatestTelemetryOut, TelemetryDataPoint, TelemetryKeyOut, TelemetryKeyUpdate
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


# ── Telemetry Key Metadata ────────────────────────────────────────────────────

@router.get("/metadata/{device_id}", response_model=List[TelemetryKeyOut])
def get_telemetry_metadata(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return metadata (label, unit, data_type) for every telemetry key
    seen on this device. Auto-populated on first ingest; editable via PUT.
    """
    _get_device_owned(device_id, current_user, db)

    rows = (
        db.query(TelemetryKey)
        .filter(TelemetryKey.device_id == device_id)
        .order_by(TelemetryKey.key)
        .all()
    )
    return [
        TelemetryKeyOut(
            key=r.key,
            label=r.label,
            unit=r.unit,
            data_type=r.data_type or "number",
        )
        for r in rows
    ]


@router.put("/metadata/{device_id}/{key}", response_model=TelemetryKeyOut)
def update_telemetry_metadata(
    device_id: UUID,
    key: str,
    body: TelemetryKeyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the label, unit, or data_type for a specific telemetry key.
    Creates the metadata row if it doesn't exist yet.
    """
    _get_device_owned(device_id, current_user, db)

    row = db.query(TelemetryKey).filter(
        TelemetryKey.device_id == device_id,
        TelemetryKey.key == key,
    ).first()

    if not row:
        # Create it if missing (e.g. key not yet ingested)
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


# ── Telemetry Aggregation ────────────────────────────────────────────────────

# Map window string → timedelta
_WINDOWS = {
    "1m":  timedelta(minutes=1),
    "5m":  timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
}

# Map function string → SQLAlchemy aggregate function
_AGG_FUNCS = {
    "avg":   func.avg,
    "min":   func.min,
    "max":   func.max,
    "sum":   func.sum,
    "count": func.count,
}


@router.get("/aggregate/{device_id}")
def aggregate_telemetry(
    device_id: UUID,
    key:      str   = Query(..., description="Telemetry key to aggregate"),
    window:   str   = Query("1h",  description="Time window: 1m 5m 15m 30m 1h 6h 12h 24h 7d"),
    function: str   = Query("avg", description="Aggregation: avg min max sum count"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compute a time-windowed aggregation over numeric telemetry.

    Examples:
      GET /aggregate/{id}?key=glucose&window=1h&function=avg
      GET /aggregate/{id}?key=glucose&window=24h&function=min
      GET /aggregate/{id}?key=glucose&window=5m&function=max

    Only keys with numeric values (stored in value_num) are supported.
    Non-numeric keys will return result: null.

    Returns:
      {
        "device_id": "...",
        "key":       "glucose",
        "window":    "1h",
        "function":  "avg",
        "result":    142.3,
        "count":     12,
        "from_ts":   "2026-04-29T19:00:00Z",
        "to_ts":     "2026-04-29T20:00:00Z"
      }
    """
    # Validate device ownership
    _get_device_owned(device_id, current_user, db)

    # Validate window
    if window not in _WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid window '{window}'. Valid: {', '.join(_WINDOWS.keys())}",
        )

    # Validate function
    fn_name = function.lower()
    if fn_name not in _AGG_FUNCS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid function '{function}'. Valid: {', '.join(_AGG_FUNCS.keys())}",
        )

    # Compute time range
    to_ts   = datetime.now(timezone.utc)
    from_ts = to_ts - _WINDOWS[window]

    # Build base filter
    base_filter = and_(
        TelemetryData.device_id == device_id,
        TelemetryData.key       == key,
        TelemetryData.ts        >= from_ts,
        TelemetryData.ts        <= to_ts,
        TelemetryData.value_num.isnot(None),  # numeric only
    )

    # Always fetch count regardless of function
    count_result = (
        db.query(func.count(TelemetryData.id))
        .filter(base_filter)
        .scalar()
    ) or 0

    # Compute the requested aggregation
    agg_fn  = _AGG_FUNCS[fn_name]
    target  = TelemetryData.id if fn_name == "count" else TelemetryData.value_num
    result  = (
        db.query(agg_fn(target))
        .filter(base_filter)
        .scalar()
    )

    # Round floats to 4 decimal places for clean output
    if result is not None and isinstance(result, float):
        result = round(result, 4)

    return {
        "device_id": str(device_id),
        "key":       key,
        "window":    window,
        "function":  fn_name,
        "result":    result,
        "count":     count_result,
        "from_ts":   from_ts.isoformat(),
        "to_ts":     to_ts.isoformat(),
    }
