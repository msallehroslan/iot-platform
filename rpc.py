"""
app/routers/api_keys.py — API Key management.

Long-lived keys for server-to-server integrations.
Key is shown ONCE on creation and hashed before storage (SHA-256).
Authenticate with: Authorization: ApiKey <raw_key>

GET    /api-keys/          — list your tenant's keys (no raw keys)
POST   /api-keys/          — create key → returns raw key once
DELETE /api-keys/{id}      — revoke key
"""
import hashlib
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.core.auth_deps import require_admin
from app.models.models import ApiKey, User
from app.services.audit import audit

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


class ApiKeyCreate(BaseModel):
    name: str
    expires_days: Optional[int] = None  # None = never expires


class ApiKeyOut(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ApiKeyCreated(ApiKeyOut):
    raw_key: str  # Only returned once — not stored


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@router.get("/", response_model=List[ApiKeyOut])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return (
        db.query(ApiKey)
        .filter(ApiKey.tenant_id == current_user.tenant_id, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
        .all()
    )


@router.post("/", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    body: ApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    raw_key   = f"tsk_{secrets.token_urlsafe(32)}"  # "tsk" = TriAxis secret key
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_days)
        if body.expires_days else None
    )
    key = ApiKey(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        name=body.name,
        key_hash=_hash_key(raw_key),
        key_prefix=raw_key[:8],
        is_active=True,
        expires_at=expires_at,
    )
    db.add(key)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="api_key.create", resource="api_key", resource_id=str(key.id),
          detail={"name": body.name})
    db.commit()
    db.refresh(key)
    return ApiKeyCreated(
        id=key.id, name=key.name, key_prefix=key.key_prefix,
        is_active=key.is_active, last_used_at=key.last_used_at,
        expires_at=key.expires_at, created_at=key.created_at,
        raw_key=raw_key,
    )


@router.delete("/{key_id}", status_code=204)
def revoke_api_key(
    key_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    key = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.tenant_id == current_user.tenant_id,
    ).first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    key.is_active = False
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="api_key.revoke", resource="api_key", resource_id=str(key.id))
    db.commit()
