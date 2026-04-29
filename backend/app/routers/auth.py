"""
app/routers/auth.py — Authentication endpoints.

POST /auth/register       — create account + tenant
POST /auth/login          — returns JWT
POST /auth/seed-demo      — create demo account
POST /auth/reset-password — Option 1: directly reset password by email + new password
                            No email verification, no token. Simple and functional.
                            Anyone who knows the email can reset it — suitable for
                            internal tools and PoC deployments.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.models.models import User, Tenant
from app.schemas.schemas import UserCreate, UserOut, LoginRequest, TokenResponse
import uuid
import secrets

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Request schemas ───────────────────────────────────────────────────────────

class ResetPasswordRequest(BaseModel):
    email: str
    new_password: str


# ── Routes ────────────────────────────────────────────────────────────────────

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

    token = create_access_token({
        "sub":   str(user.id),
        "email": user.email,
        "role":  user.role,
    })
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.post("/seed-demo")
def seed_demo(db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == "demo@iotplatform.com").first()
    if existing:
        return {
            "message":  "Demo user already exists",
            "email":    "demo@iotplatform.com",
            "password": "demo1234",
        }

    tenant = Tenant(
        name="Demo Organization",
        provisioning_key=secrets.token_hex(16),
    )
    db.add(tenant)
    db.flush()

    user = User(
        email="demo@iotplatform.com",
        hashed_password=get_password_hash("demo1234"),
        first_name="Demo",
        last_name="User",
        role="TENANT_ADMIN",
        tenant_id=tenant.id,
    )
    db.add(user)
    db.commit()
    return {
        "message":  "Demo user created",
        "email":    "demo@iotplatform.com",
        "password": "demo1234",
    }


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Reset password directly — no email link, no token, no waiting.

    User provides their registered email + desired new password.
    If the email exists, password is updated immediately.
    If the email does not exist, returns 404.

    Minimum 8 characters enforced.
    """
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="No account found with that email address",
        )

    user.hashed_password = get_password_hash(body.new_password)
    db.commit()

    return {"message": "Password updated successfully. You can now log in."}
