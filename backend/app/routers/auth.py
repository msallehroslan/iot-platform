"""
app/routers/auth.py — Authentication endpoints.

POST /auth/register        — create account + tenant
POST /auth/login           — returns access + refresh JWT (refresh stored in DB)
POST /auth/refresh         — rotate refresh token (old revoked, new issued)
POST /auth/logout          — revoke current refresh token
POST /auth/forgot-password — DB-backed reset token (single-use, 30min TTL)
POST /auth/reset-password  — consume reset token, set new password
POST /auth/seed-demo       — create demo account
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid
import secrets
import logging
from uuid import UUID
from datetime import datetime, timezone, timedelta

from app.core.database import get_db
from app.core.security import (
    verify_password, get_password_hash,
    create_access_token, create_refresh_token, decode_token,
)
from app.models.models import User, Tenant, RefreshToken, PasswordReset
from app.schemas.schemas import UserCreate, UserOut, LoginRequest, TokenResponse
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)

_RESET_TOKEN_TTL_MINUTES = 30
_REFRESH_TOKEN_TTL_DAYS  = settings.REFRESH_TOKEN_EXPIRE_DAYS


# ── Request schemas ───────────────────────────────────────────────────────────

class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _store_refresh_token(db: Session, user_id: uuid.UUID, raw_token: str) -> None:
    """Persist a new refresh token to DB."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_TTL_DAYS)
    db.add(RefreshToken(
        user_id=user_id,
        token=raw_token,
        revoked=False,
        expires_at=expires_at,
    ))


def _revoke_refresh_token(db: Session, raw_token: str) -> bool:
    """Mark a refresh token as revoked. Returns True if found."""
    row = db.query(RefreshToken).filter(
        RefreshToken.token == raw_token,
        RefreshToken.revoked == False,
    ).first()
    if not row:
        return False
    row.revoked = True
    return True


def _validate_refresh_token(db: Session, raw_token: str) -> RefreshToken:
    """
    Verify the token exists in DB, is not revoked, and is not expired.
    Raises 401 on any failure.
    """
    row = db.query(RefreshToken).filter(
        RefreshToken.token == raw_token,
    ).first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if row.revoked:
        # Token reuse detected — could be a stolen token replay attack
        # Revoke all tokens for this user as a precaution
        logger.warning("Refresh token reuse detected for user=%s — revoking all tokens", row.user_id)
        db.query(RefreshToken).filter(
            RefreshToken.user_id == row.user_id,
            RefreshToken.revoked == False,
        ).update({"revoked": True})
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token already used — please log in again")
    if row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")
    return row


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=201)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # Password complexity
    pwd = user_in.password
    if len(pwd) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not any(c.isupper() for c in pwd) or not any(c.islower() for c in pwd):
        raise HTTPException(status_code=400, detail="Password must contain uppercase and lowercase letters")
    if not any(c.isdigit() for c in pwd):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")

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
    audit(db, tenant_id=user.tenant_id, user=user,
          action="user.register", resource="user", resource_id=str(user.id),
          detail={"email": user.email, "role": user.role}, commit=True)
    return user


@router.post("/login", response_model=TokenResponse)
def login(credentials: LoginRequest, request: Request, db: Session = Depends(get_db)):
    from app.models.models import RateLimit
    import hashlib

    # Rate limit: 5 failed login attempts per IP per 15 minutes
    client_ip = request.client.host if request.client else "unknown"
    ip_token = f"login_fail:{hashlib.md5(client_ip.encode()).hexdigest()[:16]}"
    window_start = datetime.now(timezone.utc) - timedelta(minutes=15)
    fail_row = db.query(RateLimit).filter(
        RateLimit.token == ip_token,
        RateLimit.window_start >= window_start,
    ).first()
    if fail_row and fail_row.request_count >= 5:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again in 15 minutes.")

    user = db.query(User).filter(User.email == credentials.email).first()
    if not user or not verify_password(credentials.password, user.hashed_password):
        # Track failed attempt
        if fail_row:
            fail_row.request_count += 1
        else:
            db.add(RateLimit(token=ip_token, request_count=1, window_start=datetime.now(timezone.utc)))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    # Clear failed attempts on success
    if fail_row:
        db.delete(fail_row)

    payload = {"sub": str(user.id), "email": user.email, "role": user.role}
    raw_refresh = create_refresh_token(payload)

    # Persist refresh token to DB
    _store_refresh_token(db, user.id, raw_refresh)
    db.commit()

    return {
        "access_token":  create_access_token(payload),
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/refresh")
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """
    Token rotation:
      1. Validate JWT signature + type
      2. Verify token exists in DB, not revoked, not expired
      3. Revoke old token
      4. Issue new access + refresh token pair
      5. Store new refresh token in DB
    A reused (already-revoked) token triggers full session revocation.
    """
    # Step 1: validate JWT signature
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Step 2+3: validate DB record + revoke old token
    row = _validate_refresh_token(db, body.refresh_token)
    row.revoked = True

    # Step 4+5: issue new token pair
    new_payload = {
        "sub":   payload["sub"],
        "email": payload.get("email", ""),
        "role":  payload.get("role", ""),
    }
    new_refresh = create_refresh_token(new_payload)
    _store_refresh_token(db, row.user_id, new_refresh)
    db.commit()

    return {
        "access_token":  create_access_token(new_payload),
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    """Revoke the supplied refresh token. Silent success even if token not found."""
    _revoke_refresh_token(db, body.refresh_token)
    db.commit()
    return {"message": "Logged out successfully"}


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Generate a DB-backed reset token (single-use, 30-minute TTL).
    Survives server restarts. Any prior unused tokens for this email are
    invalidated to prevent token accumulation.
    """
    user = db.query(User).filter(User.email == body.email).first()
    # Always return 200 — never leak whether email exists
    if user:
        # Invalidate any existing unused tokens for this email
        db.query(PasswordReset).filter(
            PasswordReset.email == body.email,
            PasswordReset.used == False,
        ).update({"used": True})

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_TTL_MINUTES)
        db.add(PasswordReset(
            email=body.email,
            token=token,
            used=False,
            expires_at=expires_at,
        ))
        db.commit()
        # TODO: send email with reset link containing token
        logger.info("Password reset token for %s: %s", body.email, token)

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Consume a DB-backed reset token.
    Token must exist, be unused, and not expired.
    """
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    row = db.query(PasswordReset).filter(
        PasswordReset.token == body.reset_token,
    ).first()

    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    if row.used:
        raise HTTPException(status_code=400, detail="Reset token already used")
    if row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset token has expired")

    user = db.query(User).filter(User.email == row.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = get_password_hash(body.new_password)
    row.used = True  # mark consumed — cannot be reused
    db.commit()
    return {"message": "Password updated successfully. You can now log in."}


@router.post("/seed-demo")
def seed_demo(db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == "demo@triaxisai.com").first()
    if existing:
        return {"message": "Demo user already exists", "email": "demo@triaxisai.com"}

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
    return {"message": "Demo user created", "email": "demo@triaxisai.com", "note": "Default password set — change immediately"}


# ── User management (TENANT_ADMIN only) ──────────────────────────────────────

from app.services.audit import audit
from app.core.auth_deps import require_admin, get_current_user
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
    if body.role not in ("TENANT_ADMIN", "TENANT_USER", "CUSTOMER_USER"):
        raise HTTPException(status_code=400, detail="Invalid role")
    user = db.query(User).filter(User.id == user_id, User.tenant_id == current_user.tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    old_role = user.role
    user.role = body.role
    user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="user.role_change", resource="user", resource_id=str(user.id),
          detail={"email": user.email, "old_role": old_role, "new_role": body.role}, commit=True)
    return user


@router.delete("/users/{user_id}", status_code=204, tags=["Users"])
def delete_user(user_id: UUID, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id, User.tenant_id == current_user.tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="user.delete", resource="user", resource_id=str(user.id),
          detail={"email": user.email, "role": user.role})
    # Revoke all active refresh tokens for this user
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked == False,
    ).update({"revoked": True})
    db.delete(user)
    db.commit()


@router.post("/users/invite", response_model=UserOut, status_code=201, tags=["Users"])
def invite_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    role = body.role if body.role in ("TENANT_ADMIN", "TENANT_USER") else "TENANT_USER"
    user = User(
        email=body.email,
        hashed_password=get_password_hash(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=role,
        tenant_id=current_user.tenant_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="user.invite", resource="user", resource_id=str(user.id),
          detail={"email": user.email, "role": user.role}, commit=True)
    return user
