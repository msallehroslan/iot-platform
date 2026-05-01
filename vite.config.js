"""
app/core/auth_deps.py — FastAPI dependencies for authentication + RBAC.

Roles:
  TENANT_ADMIN   — full access to everything in their tenant
  TENANT_USER    — read-only within their tenant (no create/delete/update)
  CUSTOMER_USER  — scoped to their customer_id, read-only on assigned devices only

Dependencies:
  get_current_user        → any authenticated user
  get_current_user_id     → returns user UUID string (legacy, unchanged)
  require_admin           → TENANT_ADMIN only
  require_tenant_member   → TENANT_ADMIN or TENANT_USER
  require_device_access   → all roles, but CUSTOMER_USER filtered to their devices
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.core.database import get_db
from app.core.security import decode_token
from app.models.models import User, Device

_bearer = HTTPBearer(auto_error=False)

ROLE_TENANT_ADMIN  = "TENANT_ADMIN"
ROLE_TENANT_USER   = "TENANT_USER"
ROLE_CUSTOMER_USER = "CUSTOMER_USER"

# Roles that belong to a tenant (not customer-scoped)
TENANT_ROLES = {ROLE_TENANT_ADMIN, ROLE_TENANT_USER}


def _resolve_user(credentials: HTTPAuthorizationCredentials, db: Session) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    return _resolve_user(credentials, db)


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> str:
    return str(_resolve_user(credentials, db).id)


def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Only TENANT_ADMIN can proceed."""
    if current_user.role != ROLE_TENANT_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_tenant_member(
    current_user: User = Depends(get_current_user),
) -> User:
    """TENANT_ADMIN or TENANT_USER — not CUSTOMER_USER."""
    if current_user.role not in TENANT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant member access required",
        )
    return current_user


def check_device_access(device: Device, current_user: User) -> bool:
    """
    Returns True if the user can access the device.
    - TENANT_ADMIN / TENANT_USER: any device in their tenant
    - CUSTOMER_USER: only devices assigned to their customer_id
    """
    if device.tenant_id != current_user.tenant_id:
        return False
    if current_user.role == ROLE_CUSTOMER_USER:
        return device.customer_id == current_user.customer_id
    return True


def assert_device_access(device: Optional[Device], current_user: User) -> Device:
    """Raise 404/403 if user cannot access this device."""
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if not check_device_access(device, current_user):
        raise HTTPException(status_code=403, detail="You do not have access to this device")
    return device
