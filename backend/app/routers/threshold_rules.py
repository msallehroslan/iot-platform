"""
app/routers/threshold_rules.py — CRUD for per-device/tenant alarm threshold rules.
FIX 9: replaces hardcoded ALARM_RULES dict in telemetry_service.py
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import ThresholdRule, Device, User
from app.schemas.schemas import ThresholdRuleCreate, ThresholdRuleOut

router = APIRouter(prefix="/threshold-rules", tags=["Threshold Rules"])


@router.get("/", response_model=List[ThresholdRuleOut])
def list_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ThresholdRule).filter(ThresholdRule.tenant_id == current_user.tenant_id).all()


@router.post("/", response_model=ThresholdRuleOut, status_code=201)
def create_rule(
    body: ThresholdRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.device_id:
        device = db.query(Device).filter(Device.id == body.device_id).first()
        if not device or device.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")

    rule = ThresholdRule(
        tenant_id=current_user.tenant_id,
        device_id=body.device_id,
        key=body.key,
        condition=body.condition,
        threshold=body.threshold,
        severity=body.severity,
        alarm_type=body.alarm_type,
        is_active=body.is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=ThresholdRuleOut)
def update_rule(
    rule_id: UUID, body: ThresholdRuleCreate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    rule = db.query(ThresholdRule).filter(
        ThresholdRule.id == rule_id, ThresholdRule.tenant_id == current_user.tenant_id
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.query(ThresholdRule).filter(
        ThresholdRule.id == rule_id, ThresholdRule.tenant_id == current_user.tenant_id
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
