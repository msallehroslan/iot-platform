"""
app/routers/auth.py — Authentication endpoints.

POST /auth/register       — create account + tenant
POST /auth/login          — returns access + refresh JWT
POST /auth/refresh        — exchange refresh token for new access token
POST /auth/seed-demo      — create demo account
POST /auth/reset-password — requires valid reset_token from /auth/forgot-password
POST /auth/forgot-password — generates a signed reset token (logs it; wire up SMTP for email)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid
import secrets
import logging
from uuid import UUID

from app.core.database import get_db
from app.core.security import (
    verify_password, get_password_hash,
    create_access_token, create_refresh_token, decode_token,
)
from app.models.models import User, Tenant
from app.schemas.schemas import UserCreate, UserOut, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)

# In-memory reset token store  {token: email}
# For production replace with a DB table with expiry timestamps
_reset_tokens: dict[str, str] = {}


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", response_model=UserOut, status_code=201)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    tenant = Tenant(
        name=f"{user_in.email}'s Organization",
        provisioning_key=secrets.token_hex(16),
    )
    db.add(tenant)
    db.flush()

    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        role=user_in.role,
        tenant_id=tenant.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == credentials.email).first()
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    payload = {"sub": str(user.id), "email": user.email, "role": user.role}
    return {
        "access_token":  create_access_token(payload),
        "refresh_token": create_refresh_token(payload),
        "token_type": "bearer",
        "user": user,
    }


@router.post("/refresh")
def refresh_token(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token."""
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    new_access = create_access_token({
        "sub":   payload["sub"],
        "email": payload.get("email", ""),
        "role":  payload.get("role", ""),
    })
    return {"access_token": new_access, "token_type": "bearer"}


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Generate a signed reset token.
    In production, email this token to the user. Here we log it so you can
    wire up SMTP (SendGrid, SES, etc.) without breaking the flow.
    """
    user = db.query(User).filter(User.email == body.email).first()
    # Always return 200 — never confirm whether email exists (enumeration attack)
    if user:
        token = secrets.token_urlsafe(32)
        _reset_tokens[token] = body.email
        # TODO: send email with reset link containing token
        logger.info("Password reset token for %s: %s", body.email, token)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using a token from /forgot-password."""
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    email = _reset_tokens.pop(body.reset_token, None)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = get_password_hash(body.new_password)
    db.commit()
    return {"message": "Password updated successfully. You can now log in."}


@router.post("/seed-demo")
def seed_demo(db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == "demo@triaxisai.com").first()
    if existing:
        return {"message": "Demo user already exists", "email": "demo@triaxisai.com", "password": "demo1234"}

    tenant = Tenant(name="TriAxis Demo", provisioning_key=secrets.token_hex(16))
    db.add(tenant)
    db.flush()

    user = User(
        email="demo@triaxisai.com",
        hashed_password=get_password_hash("demo1234"),
        first_name="Demo", last_name="User",
        role="TENANT_ADMIN", tenant_id=tenant.id,
    )
    db.add(user)
    db.commit()
    return {"message": "Demo user created", "email": "demo@triaxisai.com", "password": "demo1234"}


# ── User management (TENANT_ADMIN only) ──────────────────────────────────────

from app.core.auth_deps import require_admin, get_current_user
from app.models.models import Device as DeviceModel
from typing import List
from pydantic import BaseModel as _BaseModel

class UserUpdateRole(_BaseModel):
    role: str
    is_active: bool = True

@router.get("/users", response_model=List[UserOut], tags=["Users"])
def list_users(db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """List all users in the tenant."""
    return db.query(User).filter(User.tenant_id == current_user.tenant_id).all()

@router.put("/users/{user_id}/role", response_model=UserOut, tags=["Users"])
def update_user_role(
    user_id: UUID,
    body: UserUpdateRole,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Change a user's role. TENANT_ADMIN only."""
    from uuid import UUID as _UUID
    if body.role not in ("TENANT_ADMIN", "TENANT_USER", "CUSTOMER_USER"):
        raise HTTPException(status_code=400, detail="Invalid role")
    user = db.query(User).filter(User.id == user_id, User.tenant_id == current_user.tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    user.role = body.role
    user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return user

@router.delete("/users/{user_id}", status_code=204, tags=["Users"])
def delete_user(user_id, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Remove a user from the tenant. TENANT_ADMIN only."""
    user = db.query(User).filter(User.id == user_id, User.tenant_id == current_user.tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db.delete(user)
    db.commit()

@router.post("/users/invite", response_model=UserOut, status_code=201, tags=["Users"])
def invite_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    TENANT_ADMIN creates a new user directly inside their tenant.
    The new user gets TENANT_USER role by default (never TENANT_ADMIN unless specified).
    No new tenant is created — the user joins the admin's tenant.
    """
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Force role to TENANT_USER unless admin explicitly sets it
    role = body.role if body.role in ("TENANT_ADMIN", "TENANT_USER") else "TENANT_USER"

    user = User(
        email=body.email,
        hashed_password=get_password_hash(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=role,
        tenant_id=current_user.tenant_id,  # same tenant as admin
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
