"""
app/routers/user_dashboards.py — Phase 2 multi-dashboard router.

Thin HTTP adapter. All logic in user_dashboard_service.py.

Endpoints:
  GET    /user-dashboards/              list all user's dashboards (sidebar)
  GET    /user-dashboards/default       get default dashboard with widgets (app load)
  POST   /user-dashboards/              create a new dashboard
  GET    /user-dashboards/{id}          get dashboard with widgets
  POST   /user-dashboards/{id}/set-default  set as user's default
  PUT    /user-dashboards/{id}/rename   rename dashboard
  DELETE /user-dashboards/{id}          delete + cascade widgets

  POST   /user-dashboards/{id}/widgets/           add widget
  PUT    /user-dashboards/{id}/widgets/{wid}       update widget
  DELETE /user-dashboards/{id}/widgets/{wid}       delete widget
  PUT    /user-dashboards/{id}/layout              bulk-save positions
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel

from app.core.database import get_db
from app.core.auth_deps import get_current_user_id
from app.services import user_dashboard_service

router = APIRouter(prefix="/user-dashboards", tags=["User Dashboards"])


# ── Request schemas ───────────────────────────────────────────────────────────

class CreateDashboardBody(BaseModel):
    name: str
    description: Optional[str] = None


class RenameBody(BaseModel):
    name: str


class WidgetPosition(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 2
    h: int = 3


class AddWidgetBody(BaseModel):
    widget_type: str
    title: str = "Widget"
    config: Dict[str, Any] = {}
    position: WidgetPosition = WidgetPosition()


class UpdateWidgetBody(BaseModel):
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

@router.get("/")
def list_dashboards(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    List all dashboards for the current user.
    Auto-creates a 'Default Dashboard' if the user has none.
    Used to populate the sidebar.
    """
    return user_dashboard_service.list_dashboards(user_id=user_id, db=db)


@router.get("/default")
def get_default_dashboard(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    Get the user's default dashboard with all widgets.
    Called on app load to show the right dashboard immediately.
    """
    return user_dashboard_service.get_default_dashboard(user_id=user_id, db=db)


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_dashboard(
    body: CreateDashboardBody,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.create_dashboard(
        user_id=user_id,
        name=body.name,
        description=body.description,
        db=db,
    )


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.get_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, db=db
    )


@router.post("/{dashboard_id}/set-default")
def set_default(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Set dashboard as default. Clears default flag from all others."""
    return user_dashboard_service.set_default_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, db=db
    )


@router.put("/{dashboard_id}/rename")
def rename_dashboard(
    dashboard_id: UUID,
    body: RenameBody,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.rename_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, name=body.name, db=db
    )


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Delete dashboard + all widgets. Blocks if it's the user's only dashboard."""
    user_dashboard_service.delete_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, db=db
    )


# ── Widget routes ─────────────────────────────────────────────────────────────

@router.post("/{dashboard_id}/widgets/", status_code=status.HTTP_201_CREATED)
def add_widget(
    dashboard_id: UUID,
    body: AddWidgetBody,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.add_widget(
        dashboard_id=dashboard_id,
        user_id=user_id,
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
    body: UpdateWidgetBody,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.update_widget(
        dashboard_id=dashboard_id,
        widget_id=widget_id,
        user_id=user_id,
        widget_type=body.widget_type,
        title=body.title,
        config=body.config,
        position=body.position.model_dump() if body.position else None,
        db=db,
    )


@router.delete("/{dashboard_id}/widgets/{widget_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_widget(
    dashboard_id: UUID,
    widget_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    user_dashboard_service.delete_widget(
        dashboard_id=dashboard_id,
        widget_id=widget_id,
        user_id=user_id,
        db=db,
    )


@router.put("/{dashboard_id}/layout")
def save_layout(
    dashboard_id: UUID,
    body: LayoutBody,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return user_dashboard_service.save_layout(
        dashboard_id=dashboard_id,
        user_id=user_id,
        layout=[item.model_dump() for item in body.layout],
        db=db,
    )


@router.post("/deduplicate", status_code=200)
def deduplicate_dashboards(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """
    Remove duplicate Default Dashboards created by concurrent requests.
    Keeps the oldest dashboard per user, deletes the rest.
    Called automatically on UserDashboardPage mount.
    """
    return user_dashboard_service.deduplicate_dashboards(user_id, db)
