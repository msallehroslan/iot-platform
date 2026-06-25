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
  GET    /user-dashboards/{id}/preload             batch load all widget data (BUG-02)
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel

from app.core.database import get_db
from app.services.audit import audit
from app.core.auth_deps import get_current_user_id, get_current_user
from app.models.models import User
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
    current_user: User = Depends(get_current_user),
):
    result = user_dashboard_service.create_dashboard(
        user_id=user_id,
        name=body.name,
        description=body.description,
        db=db,
    )
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="user_dashboard.create", resource="user_dashboard",
          resource_id=str(result.get("id", "")),
          detail={"name": body.name}, commit=True)
    return result


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
    current_user: User = Depends(get_current_user),
):
    """Delete dashboard + all widgets. Blocks if it's the user's only dashboard."""
    audit(db, tenant_id=current_user.tenant_id, user=current_user,
          action="user_dashboard.delete", resource="user_dashboard",
          resource_id=str(dashboard_id))
    user_dashboard_service.delete_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, db=db
    )


# ── Preload endpoint — BUG-02 fix ─────────────────────────────────────────────
# Frontend useDashboardRuntime.js calls this on every dashboard load.
# Without it the hook gets 404, falls back to basic REST, and all widgets
# load blank. This endpoint returns telemetry + history + alarms +
# intelligence for every unique device on the dashboard in one request.

@router.get("/{dashboard_id}/preload")
def preload_dashboard(
    dashboard_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    current_user: User = Depends(get_current_user),
):
    """
    Batch preload all widget data for a dashboard in one request.

    Returns:
        {
            "devices": {
                "<device_id>": {
                    "telemetry":    { key: value, ... },
                    "history":      { key: [{ts, value}, ...], ... },
                    "alarms":       [...],
                    "intelligence": { unified response }
                }
            }
        }

    Replaces N individual widget API calls on dashboard load.
    Called by useDashboardRuntime.js hook before WebSocket connects.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    from app.services.data_service import (
        get_latest_telemetry,
        get_active_alarms,
        get_unified_intelligence,
        get_aggregated_telemetry,
    )
    from app.models.models import Device

    # Fetch dashboard and verify ownership
    dash = user_dashboard_service.get_dashboard(
        dashboard_id=dashboard_id, user_id=user_id, db=db
    )

    # Collect unique device IDs from all widget configs
    device_ids: set = set()
    for widget in dash.get("widgets", []):
        did = widget.get("config", {}).get("device_id")
        if did:
            device_ids.add(str(did))

    if not device_ids:
        return {"devices": {}}

    # Verify devices belong to this tenant (security check)
    allowed_ids = {
        str(d.id)
        for d in db.query(Device).filter(
            Device.id.in_(list(device_ids)),
            Device.tenant_id == current_user.tenant_id,
        ).all()
    }

    result: dict = {}

    for device_id in allowed_ids:
        try:
            # 1. Latest telemetry values
            telem  = get_latest_telemetry(db, device_id)
            values = telem.get("values", {})

            # 2. Short history per numeric key — 60 raw points (1h window)
            #    Feeds sparklines and initial chart render without waiting for WS
            history: dict = {}
            numeric_keys = [
                k for k, v in values.items()
                if isinstance(v, (int, float))
            ]
            for key in numeric_keys[:10]:   # cap at 10 keys for response speed
                try:
                    hist = get_aggregated_telemetry(
                        db, device_id, key,
                        hours=1, limit=60, resolution="raw",
                    )
                    pts = hist.get("points", [])
                    if pts:
                        history[key] = pts
                except Exception:
                    pass

            # 3. Active alarms
            alarms_data = get_active_alarms(db, device_id)

            # 4. Unified intelligence — health, risk, anomalies, baseline status
            device_obj = db.query(Device).filter(Device.id == device_id).first()
            intel = get_unified_intelligence(db, device_id, device=device_obj)

            result[device_id] = {
                "telemetry":    values,
                "history":      history,
                "alarms":       alarms_data.get("alarms", []),
                "intelligence": intel,
            }

        except Exception as exc:
            # Non-fatal — return empty slot so other devices still load
            _logger.warning("preload failed for device %s: %s", device_id, exc)
            result[device_id] = {
                "telemetry":    {},
                "history":      {},
                "alarms":       [],
                "intelligence": None,
            }

    return {"devices": result}


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
