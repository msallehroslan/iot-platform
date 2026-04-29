from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.models.models import User, Tenant
from app.schemas.schemas import UserCreate, UserOut, LoginRequest, TokenResponse
import uuid

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create default tenant for this user
    tenant = Tenant(name=f"{user_in.email}'s Organization")
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

    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.post("/seed-demo")
def seed_demo(db: Session = Depends(get_db)):
    """Seed a demo user for quick testing"""
    existing = db.query(User).filter(User.email == "demo@iotplatform.com").first()
    if existing:
        return {"message": "Demo user already exists", "email": "demo@iotplatform.com", "password": "demo1234"}

    tenant = Tenant(name="Demo Organization")
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
    return {"message": "Demo user created", "email": "demo@iotplatform.com", "password": "demo1234"}
