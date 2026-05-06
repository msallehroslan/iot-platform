"""
Shared imports and helpers for intelligence sub-routers.
"""
from __future__ import annotations

import os
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth_deps import get_current_user, require_admin
from app.models.models import Device, Alarm, TelemetryData, ThresholdRule
from app.services.trend_service import get_device_key_trend, get_all_key_trends
from app.services.data_service import (
    get_latest_telemetry as ds_get_latest,
    get_aggregated_telemetry as ds_get_aggregated,
    get_active_alarms as ds_get_alarms,
    get_baseline_now as ds_get_baseline,
    get_anomaly_summary as ds_get_anomaly,
    get_health_summary as ds_get_health,
    get_unified_intelligence,
    get_key_intelligence,
)

logger = logging.getLogger(__name__)


def _assert_device(device_id: UUID, current_user, db: Session) -> Device:
    q = db.query(Device).filter(
        Device.id == device_id,
        Device.tenant_id == current_user.tenant_id,
    )
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    device = q.first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _scoped_devices(current_user, db: Session):
    q = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    return q
