"""
app/routers/devices.py — Device CRUD endpoints.
FIX 6: token only returned on create + regenerate (DeviceWithToken).
FIX 15: list endpoint returns PaginatedDevices envelope.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
import uuid

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import Device, DeviceStatus, User, Tenant
from app.core.config import settings
from app.schemas.schemas import (
    DeviceCreate, DeviceUpdate, DeviceOut, DeviceWithToken,
    PaginatedDevices, ProvisionRequest, ProvisionResponse, ProvisioningKeyOut,
)

router = APIRouter(prefix="/devices", tags=["Devices"])


def _get_device_owned(device_id: UUID, current_user: User, db: Session) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have access to this device")
    return device


@router.get("/", response_model=PaginatedDevices)
def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if search:
        query = query.filter(Device.name.ilike(f"%{search}%"))
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedDevices(total=total, page=page, page_size=page_size, items=items)


@router.post("/", response_model=DeviceWithToken, status_code=201)
def create_device(
    device_in: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = Device(
        name=device_in.name,
        device_type=device_in.device_type,
        label=device_in.label,
        description=device_in.description,
        additional_info=device_in.additional_info,
        tenant_id=current_user.tenant_id,
        customer_id=device_in.customer_id,
        token=str(uuid.uuid4()),
        status=DeviceStatus.INACTIVE,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.get("/provisioning-key", response_model=ProvisioningKeyOut)
def get_provisioning_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import secrets as _secrets
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant.provisioning_key:
        tenant.provisioning_key = _secrets.token_hex(16)
        db.commit()
        db.refresh(tenant)
    base_url = settings.BASE_URL if hasattr(settings, "BASE_URL") else ""
    return ProvisioningKeyOut(
        provisioning_key=tenant.provisioning_key,
        provision_endpoint=f"{base_url}/api/v1/devices/provision",
    )


@router.post("/provision", response_model=ProvisionResponse, status_code=201)
def provision_device(body: ProvisionRequest, db: Session = Depends(get_db)):
    if not body.provision_key or not body.provision_key.strip():
        raise HTTPException(status_code=401, detail="Provision key is required")
    if not body.device_name or not body.device_name.strip():
        raise HTTPException(status_code=400, detail="device_name is required")

    tenant = db.query(Tenant).filter(Tenant.provisioning_key == body.provision_key.strip()).first()
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid provisioning key")

    existing = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.name == body.device_name.strip(),
    ).first()
    if existing:
        return ProvisionResponse(
            device_id=str(existing.id), name=existing.name,
            token=existing.token, status=existing.status.value,
        )

    device = Device(
        name=body.device_name.strip(),
        device_type=body.device_type or "DEFAULT",
        label=body.label,
        tenant_id=tenant.id,
        token=str(uuid.uuid4()),
        status=DeviceStatus.INACTIVE,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return ProvisionResponse(
        device_id=str(device.id), name=device.name,
        token=device.token, status=device.status.value,
    )


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _get_device_owned(device_id, current_user, db)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: UUID, device_in: DeviceUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    device = _get_device_owned(device_id, current_user, db)
    for field, value in device_in.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return device


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    device = _get_device_owned(device_id, current_user, db)
    db.delete(device)
    db.commit()


@router.post("/{device_id}/token/regenerate", response_model=DeviceWithToken)
def regenerate_token(device_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    device = _get_device_owned(device_id, current_user, db)
    device.token = str(uuid.uuid4())
    db.commit()
    db.refresh(device)
    return device
