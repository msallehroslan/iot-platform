"""
app/routers/metrics.py — Platform observability endpoint.

GET /metrics — tenant-scoped platform metrics (TENANT_ADMIN only)

Returns:
  - active_devices (seen in last 5 min)
  - total_devices
  - active_ws_clients
  - ingest_rate_per_min (DB-counted from ingest_metrics table)
  - total_alarms_active
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta

from app.core.database import get_db
from app.core.auth_deps import require_admin
from app.models.models import Device, DeviceStatus, Alarm, AlarmStatus, IngestMetric, User
from app.schemas.schemas import PlatformMetrics

router = APIRouter(prefix="/metrics", tags=["Observability"])


@router.get("/", response_model=PlatformMetrics)
def get_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    five_min_ago = now - timedelta(minutes=5)
    one_min_ago  = now - timedelta(minutes=1)
    tenant_id = current_user.tenant_id

    # Active devices (sent telemetry in last 5 min)
    active_devices = db.query(func.count(Device.id)).filter(
        Device.tenant_id == tenant_id,
        Device.last_seen_at >= five_min_ago,
    ).scalar() or 0

    total_devices = db.query(func.count(Device.id)).filter(
        Device.tenant_id == tenant_id
    ).scalar() or 0

    # Active alarms
    total_alarms_active = db.query(func.count(Alarm.id)).join(
        Device, Alarm.device_id == Device.id
    ).filter(
        Device.tenant_id == tenant_id,
        Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
    ).scalar() or 0

    # Ingest rate — sum of key_count rows in last 60 seconds
    ingest_rate = db.query(func.sum(IngestMetric.key_count)).filter(
        IngestMetric.tenant_id == tenant_id,
        IngestMetric.ts >= one_min_ago,
    ).scalar() or 0

    # Active WebSocket connections
    try:
        from app.core.websocket_manager import manager
        active_ws = manager.total_clients()
    except Exception:
        active_ws = 0

    return PlatformMetrics(
        active_devices=active_devices,
        active_ws_clients=active_ws,
        ingest_rate_per_min=int(ingest_rate),
        total_devices=total_devices,
        total_alarms_active=total_alarms_active,
        tenant_id=str(tenant_id),
        ts=now,
    )
