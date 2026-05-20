"""
app/routers/devices.py — Device CRUD endpoints.

IMPORTANT: Static routes (/provisioning-key, /provision) must be defined
BEFORE the dynamic route (/{device_id}) to prevent FastAPI from matching
the literal string as a UUID path parameter.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
import uuid

from app.core.database import get_db
from app.core.auth_deps import (
    get_current_user, require_admin, require_tenant_member,
    assert_device_access,
)
from app.models.models import Device, DeviceStatus, User, Tenant
from app.core.config import settings
from app.services.audit import audit, check_quota
from app.schemas.schemas import (
    DeviceCreate, DeviceUpdate, DeviceOut, DeviceWithToken,
    PaginatedDevices, ProvisionRequest, ProvisionResponse, ProvisioningKeyOut,
)

router = APIRouter(prefix="/devices", tags=["Devices"])


def _fetch_device(device_id: UUID, db: Session) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


# ── Static routes first — MUST come before /{device_id} ─────────────────────

@router.get("/", response_model=PaginatedDevices)
def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if current_user.role == "CUSTOMER_USER":
        query = query.filter(Device.customer_id == current_user.customer_id)
    if search:
        query = query.filter(Device.name.ilike(f"%{search}%"))
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedDevices(total=total, page=page, page_size=page_size, items=items)


@router.get("/provisioning-key", response_model=ProvisioningKeyOut)
def get_provisioning_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
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
    """No auth — device self-registration via provision key."""
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


@router.post("/", response_model=DeviceWithToken, status_code=201)
def create_device(
    device_in: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    check_quota(db, current_user.tenant_id, "devices")
    device = Device(
        name=device_in.name,
        device_type=device_in.device_type,
        label=device_in.label,
        description=device_in.description,
        additional_info=device_in.additional_info,
        tenant_id=current_user.tenant_id,
        customer_id=device_in.customer_id,
        latitude=device_in.latitude,
        longitude=device_in.longitude,
        token=str(uuid.uuid4()),
        status=DeviceStatus.INACTIVE,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="device.create", resource="device", resource_id=str(device.id),
          detail={"name": device.name, "type": device.device_type}, commit=True)
    return device


# ── Dynamic routes — after all static routes ─────────────────────────────────

@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return assert_device_access(_fetch_device(device_id, db), current_user)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: UUID,
    device_in: DeviceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = assert_device_access(_fetch_device(device_id, db), current_user)
    for field, value in device_in.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="device.update", resource="device", resource_id=str(device_id),
          detail={"name": device.name}, commit=True)
    return device


@router.delete("/{device_id}", status_code=204)
def delete_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = assert_device_access(_fetch_device(device_id, db), current_user)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="device.delete", resource="device", resource_id=str(device_id),
          detail={"name": device.name})
    from sqlalchemy import text
    db.execute(text("DELETE FROM devices WHERE id = :id"), {"id": str(device_id)})
    db.commit()


@router.post("/{device_id}/token/regenerate", response_model=DeviceWithToken)
def regenerate_token(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = assert_device_access(_fetch_device(device_id, db), current_user)
    device.token = str(uuid.uuid4())
    db.commit()
    db.refresh(device)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="device.token_regenerate", resource="device", resource_id=str(device_id), commit=True)
    return device
