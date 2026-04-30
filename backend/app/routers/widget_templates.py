"""
app/routers/widget_templates.py — Reusable widget config templates.

Save a widget config as a named template, apply it to any dashboard.
Templates are tenant-scoped; public templates visible to all tenant users.

GET  /widget-templates/          — list all templates for tenant
POST /widget-templates/          — save new template
GET  /widget-templates/{id}      — get single template
DELETE /widget-templates/{id}    — delete template
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import get_current_user
from app.models.models import WidgetTemplate, User
from app.schemas.schemas import WidgetTemplateCreate, WidgetTemplateOut, validate_widget_config

router = APIRouter(prefix="/widget-templates", tags=["Widget Templates"])


@router.get("/", response_model=List[WidgetTemplateOut])
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(WidgetTemplate)
        .filter(WidgetTemplate.tenant_id == current_user.tenant_id)
        .order_by(WidgetTemplate.created_at.desc())
        .all()
    )


@router.post("/", response_model=WidgetTemplateOut, status_code=201)
def create_template(
    body: WidgetTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    errors = validate_widget_config(body.widget_type, body.config)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    tmpl = WidgetTemplate(
        tenant_id=current_user.tenant_id,
        created_by=str(current_user.id),
        name=body.name,
        widget_type=body.widget_type,
        config=body.config,
        is_public=body.is_public,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl


@router.get("/{template_id}", response_model=WidgetTemplateOut)
def get_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tmpl = db.query(WidgetTemplate).filter(
        WidgetTemplate.id == template_id,
        WidgetTemplate.tenant_id == current_user.tenant_id,
    ).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tmpl


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tmpl = db.query(WidgetTemplate).filter(
        WidgetTemplate.id == template_id,
        WidgetTemplate.tenant_id == current_user.tenant_id,
        WidgetTemplate.created_by == str(current_user.id),
    ).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found or not yours")
    db.delete(tmpl)
    db.commit()
