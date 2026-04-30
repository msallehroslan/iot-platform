"""
app/routers/customers.py — TENANT_ADMIN only (manages customers + their users).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
import secrets

from app.core.database import get_db
from app.core.auth_deps import require_admin
from app.core.security import get_password_hash
from app.models.models import Customer, User
from app.schemas.schemas import CustomerCreate, CustomerOut, UserOut
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/customers", tags=["Customers"])


class CustomerUserCreate(BaseModel):
    email: str
    password: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


@router.get("/", response_model=List[CustomerOut])
def list_customers(db: Session = Depends(get_db), current_user=Depends(require_admin)):
    return db.query(Customer).filter(Customer.tenant_id == current_user.tenant_id).all()


@router.post("/", response_model=CustomerOut, status_code=201)
def create_customer(
    customer_in: CustomerCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    customer = Customer(
        name=customer_in.name,
        email=customer_in.email,
        phone=customer_in.phone,
        address=customer_in.address,
        city=customer_in.city,
        country=customer_in.country,
        tenant_id=current_user.tenant_id,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: UUID, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.tenant_id == current_user.tenant_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return c


@router.delete("/{customer_id}", status_code=204)
def delete_customer(customer_id: UUID, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.tenant_id == current_user.tenant_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(c)
    db.commit()


@router.post("/{customer_id}/users", response_model=UserOut, status_code=201)
def create_customer_user(
    customer_id: UUID,
    body: CustomerUserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Create a CUSTOMER_USER scoped to this customer.
    They can only see devices assigned to this customer.
    """
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.tenant_id == current_user.tenant_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")

    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=get_password_hash(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role="CUSTOMER_USER",
        tenant_id=current_user.tenant_id,
        customer_id=customer_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/{customer_id}/users", response_model=List[UserOut])
def list_customer_users(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.tenant_id == current_user.tenant_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return db.query(User).filter(User.customer_id == customer_id).all()
