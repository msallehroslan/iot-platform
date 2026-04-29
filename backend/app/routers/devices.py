"""
app/routers/devices.py — Device CRUD endpoints.

All routes require a valid JWT. Ownership is enforced via tenant_id:
  - list / get / update / delete / regenerate_token:
      device.tenant_id must equal current_user.tenant_id
  - create:
      device.tenant_id is set to current_user.tenant_id automatically
      (the frontend never needs to supply it)

This ensures users can only see and modify devices in their own tenant.

Telemetry ingest (POST /telemetry/ingest/{token}) is intentionally
unauthenticated — it uses device tokens, not JWTs, by design.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import uuid

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, DeviceStatus, User
from app.schemas.schemas import DeviceCreate, DeviceUpdate, DeviceOut

router = APIRouter(prefix="/devices", tags=["Devices"])


def _get_device_owned(device_id: UUID, current_user: User, db: Session) -> Device:
    """
    Fetch a device and verify it belongs to the caller's tenant.
    Raises 404 if not found, 403 if found but owned by a different tenant.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this device")
    return device


@router.get("/", response_model=List[DeviceOut])
def list_devices(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List devices belonging to the authenticated user's tenant."""
    query = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if search:
        query = query.filter(Device.name.ilike(f"%{search}%"))
    return query.offset(skip).limit(limit).all()


@router.post("/", response_model=DeviceOut, status_code=201)
def create_device(
    device_in: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a device and assign it to the authenticated user's tenant.
    The caller never needs to supply tenant_id — it is set from the JWT.
    """
    device = Device(
        name=device_in.name,
        device_type=device_in.device_type,
        label=device_in.label,
        description=device_in.description,
        additional_info=device_in.additional_info,
        # Always use the JWT tenant — never trust frontend-supplied tenant_id
        tenant_id=current_user.tenant_id,
        customer_id=device_in.customer_id,
        token=str(uuid.uuid4()),
        status=DeviceStatus.INACTIVE,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_device_owned(device_id, current_user, db)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: UUID,
    device_in: DeviceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = _get_device_owned(device_id, current_user, db)
    update_data = device_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return device


@router.delete("/{device_id}", status_code=204)
def delete_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = _get_device_owned(device_id, current_user, db)
    db.delete(device)
    db.commit()


@router.post("/{device_id}/token/regenerate", response_model=DeviceOut)
def regenerate_token(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = _get_device_owned(device_id, current_user, db)
    device.token = str(uuid.uuid4())
    db.commit()
    db.refresh(device)
    return device
