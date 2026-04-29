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
from app.core.config import settings
from app.models.models import Tenant
from app.schemas.schemas import (
    DeviceCreate, DeviceUpdate, DeviceOut,
    ProvisionRequest, ProvisionResponse, ProvisioningKeyOut,
)

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


# ── Device Provisioning ───────────────────────────────────────────────────────

@router.get("/provisioning-key", response_model=ProvisioningKeyOut)
def get_provisioning_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the authenticated user's tenant provisioning key.
    This key is used by devices (e.g. ESP32) to self-register without a user JWT.
    The key is read-only — it is generated once when the tenant is created.
    """
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Auto-generate key for old tenants that existed before this feature
    if not tenant.provisioning_key:
        import secrets
        tenant.provisioning_key = secrets.token_hex(16)
        db.commit()
        db.refresh(tenant)

    base_url = settings.BASE_URL if hasattr(settings, "BASE_URL") else ""
    return ProvisioningKeyOut(
        provisioning_key=tenant.provisioning_key,
        provision_endpoint=f"{base_url}/api/v1/devices/provision",
    )


@router.post("/provision", response_model=ProvisionResponse, status_code=201)
def provision_device(
    body: ProvisionRequest,
    db: Session = Depends(get_db),
):
    """
    ThingsBoard-style device self-registration.
    No user JWT required — the device sends its provisioning key.

    Flow:
      1. Validate provision_key → find the owning tenant
      2. If a device with the same name already exists in that tenant → return it (idempotent)
      3. Otherwise create a new device with a fresh token and ACTIVE status
      4. Return device_id, name, token, status

    The device can then use the returned token for all future telemetry ingestion:
      POST /api/v1/telemetry/ingest/{token}

    Security:
      - Invalid provision_key → 401
      - Tenant isolation is automatic — the key maps to exactly one tenant
      - Never exposes data from other tenants
    """
    if not body.provision_key or not body.provision_key.strip():
        raise HTTPException(status_code=401, detail="Provision key is required")
    if not body.device_name or not body.device_name.strip():
        raise HTTPException(status_code=400, detail="device_name is required")

    # Validate the provision key — find the owning tenant
    tenant = db.query(Tenant).filter(
        Tenant.provisioning_key == body.provision_key.strip()
    ).first()
    if not tenant:
        raise HTTPException(
            status_code=401,
            detail="Invalid provisioning key",
        )

    # Idempotent: if a device with this name already exists in the tenant, return it
    existing = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.name == body.device_name.strip(),
    ).first()
    if existing:
        return ProvisionResponse(
            device_id=str(existing.id),
            name=existing.name,
            token=existing.token,
            status=existing.status.value,
        )

    # Create new device under this tenant
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
        device_id=str(device.id),
        name=device.name,
        token=device.token,
        status=device.status.value,
    )
