"""
app/routers/dashboards.py — Device-scoped Dashboard & Widget router.

Every endpoint requires a valid JWT (Depends(get_current_user)).
Ownership is enforced in the service layer via user.tenant_id:
  device.tenant_id must equal user.tenant_id
  dashboard.device → device → tenant must equal user.tenant

Endpoints (URLs unchanged):
  GET    /dashboards/?device_id=           list dashboards for a device
  POST   /dashboards/                      create dashboard
  GET    /dashboards/{id}                  get dashboard with widgets
  PUT    /dashboards/{id}                  rename / update
  DELETE /dashboards/{id}                  delete (cascades to widgets)

  GET    /dashboards/{id}/widgets/         list widgets
  POST   /dashboards/{id}/widgets/         add widget
  PUT    /dashboards/{id}/widgets/{wid}    update widget
  DELETE /dashboards/{id}/widgets/{wid}    delete widget
  PUT    /dashboards/{id}/layout           bulk-save positions after drag/resize

Error codes:
  401 — missing or invalid JWT
  403 — JWT valid but caller's tenant does not own this device/dashboard
  404 — resource does not exist
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel

from app.core.database import get_db
from app.core.auth_deps import get_current_user, require_admin
from app.models.models import User
from app.services import dashboard_service

router = APIRouter(prefix="/dashboards", tags=["Dashboards"])


# ── Request bodies (unchanged) ────────────────────────────────────────────────

class DashboardCreateBody(BaseModel):
    device_id: UUID
    name: str = "My Dashboard"
    description: Optional[str] = None
    is_default: bool = False


class DashboardUpdateBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None


class WidgetPosition(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 2
    h: int = 3


class WidgetCreateBody(BaseModel):
    widget_type: str
    title: str = "Widget"
    config: Dict[str, Any] = {}
    position: WidgetPosition = WidgetPosition()


class WidgetUpdateBody(BaseModel):
    widget_type: Optional[str] = None
    title: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    position: Optional[WidgetPosition] = None


class LayoutItem(BaseModel):
    id: str
    x: int
    y: int
    w: int
    h: int


class LayoutBody(BaseModel):
    layout: List[LayoutItem]


# ── Dashboard routes ──────────────────────────────────────────────────────────
# Every route injects `current_user: User = Depends(get_current_user)`.
# The user's tenant_id is extracted and passed to the service so it can
# verify device.tenant_id == user.tenant_id before any data is returned.

@router.get("/")
def list_dashboards(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.list_dashboards(
        device_id=device_id,
        tenant_id=current_user.tenant_id,
        db=db,
    )


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_dashboard(
    body: DashboardCreateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.create_dashboard(
        device_id=body.device_id,
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        is_default=body.is_default,
        db=db,
    )


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.get_dashboard(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        db=db,
    )


@router.put("/{dashboard_id}")
def update_dashboard(
    dashboard_id: UUID,
    body: DashboardUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.update_dashboard(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        is_default=body.is_default,
        db=db,
    )


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dashboard_service.delete_dashboard(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        db=db,
    )


# ── Widget routes ─────────────────────────────────────────────────────────────

@router.get("/{dashboard_id}/widgets/")
def list_widgets(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.list_widgets(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        db=db,
    )


@router.post("/{dashboard_id}/widgets/", status_code=status.HTTP_201_CREATED)
def add_widget(
    dashboard_id: UUID,
    body: WidgetCreateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.add_widget(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        widget_type=body.widget_type,
        title=body.title,
        config=body.config,
        position=body.position.model_dump(),
        db=db,
    )


@router.put("/{dashboard_id}/widgets/{widget_id}")
def update_widget(
    dashboard_id: UUID,
    widget_id: UUID,
    body: WidgetUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return dashboard_service.update_widget(
        dashboard_id=dashboard_id,
        widget_id=widget_id,
        tenant_id=current_user.tenant_id,
        widget_type=body.widget_type,
        title=body.title,
        config=body.config,
        position=body.position.model_dump() if body.position else None,
        db=db,
    )


@router.delete(
    "/{dashboard_id}/widgets/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_widget(
    dashboard_id: UUID,
    widget_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dashboard_service.delete_widget(
        dashboard_id=dashboard_id,
        widget_id=widget_id,
        tenant_id=current_user.tenant_id,
        db=db,
    )


@router.put("/{dashboard_id}/layout")
def save_layout(
    dashboard_id: UUID,
    body: LayoutBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk-save widget grid positions after drag-and-drop or resize."""
    return dashboard_service.save_layout(
        dashboard_id=dashboard_id,
        tenant_id=current_user.tenant_id,
        layout=[item.model_dump() for item in body.layout],
        db=db,
    )
