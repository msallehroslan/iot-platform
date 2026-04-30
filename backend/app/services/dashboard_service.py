"""
app/services/dashboard_service.py
Dashboard & Widget service layer — owns all business logic.

Security model
──────────────
Device-scoped dashboards are protected by TENANT ownership:
  - Every mutating operation receives the caller's tenant_id
  - The tenant_id is compared against device.tenant_id before any data
    is read or written
  - If they do not match → 403 Forbidden (not 404, to avoid leaking
    information about which IDs exist)

Ownership chain:
  JWT sub  →  User.tenant_id  →  Device.tenant_id  →  Dashboard.device_id

This check lives HERE (service layer), not in the router, so it is
enforced regardless of how the service is called.
"""
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.models import Dashboard, Widget, Device


# ── Serializers ───────────────────────────────────────────────────────────────

def serialize_widget(w: Widget) -> dict:
    return {
        "id":           str(w.id),
        "dashboard_id": str(w.dashboard_id),
        "widget_type":  w.widget_type,
        "title":        w.title,
        "config":       w.config or {},
        "position":     w.position or {"x": 0, "y": 0, "w": 2, "h": 3},
        "created_at":   w.created_at.isoformat() if w.created_at else None,
        "updated_at":   w.updated_at.isoformat() if w.updated_at else None,
    }


def serialize_dashboard(d: Dashboard, include_widgets: bool = True) -> dict:
    out = {
        "id":           str(d.id),
        "device_id":    str(d.device_id),
        "name":         d.name,
        "description":  d.description,
        "is_default":   d.is_default,
        "widget_count": len(d.widgets),
        "created_at":   d.created_at.isoformat() if d.created_at else None,
        "updated_at":   d.updated_at.isoformat() if d.updated_at else None,
    }
    if include_widgets:
        out["widgets"] = [serialize_widget(w) for w in d.widgets]
    return out


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_device_for_tenant(device_id: UUID, tenant_id: UUID, db: Session) -> Device:
    """
    Fetch a device and verify it belongs to the caller's tenant.

    Raises:
        404  if the device does not exist (safe: no tenant information leaked)
        403  if the device exists but belongs to a different tenant
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )
    if device.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this device",
        )
    return device


def _get_dashboard_for_tenant(
    dashboard_id: UUID,
    tenant_id: UUID,
    db: Session,
) -> Dashboard:
    """
    Fetch a dashboard and verify its device belongs to the caller's tenant.

    Raises:
        404  if the dashboard does not exist
        403  if the dashboard's device belongs to a different tenant
    """
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if not d:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found",
        )
    # Walk the ownership chain: dashboard → device → tenant
    _get_device_for_tenant(d.device_id, tenant_id, db)
    return d


# ── Dashboard service ─────────────────────────────────────────────────────────

def list_dashboards(device_id: UUID, tenant_id: UUID, db: Session) -> List[dict]:
    """Return all dashboards for a device, scoped to the caller's tenant."""
    _get_device_for_tenant(device_id, tenant_id, db)
    rows = (
        db.query(Dashboard)
        .filter(Dashboard.device_id == device_id)
        .order_by(Dashboard.created_at)
        .all()
    )
    return [serialize_dashboard(d, include_widgets=False) for d in rows]


def create_dashboard(
    device_id: UUID,
    tenant_id: UUID,
    name: str,
    description: Optional[str],
    is_default: bool,
    db: Session,
) -> dict:
    """Create a dashboard for a device after verifying tenant ownership."""
    _get_device_for_tenant(device_id, tenant_id, db)
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Dashboard name is required")

    if is_default:
        db.query(Dashboard).filter(Dashboard.device_id == device_id).update(
            {"is_default": False}
        )

    d = Dashboard(
        device_id=device_id,
        name=name.strip(),
        description=description,
        is_default=is_default,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return serialize_dashboard(d)


def get_dashboard(dashboard_id: UUID, tenant_id: UUID, db: Session) -> dict:
    """Fetch a single dashboard with widgets, verifying tenant ownership."""
    d = _get_dashboard_for_tenant(dashboard_id, tenant_id, db)
    return serialize_dashboard(d)


def update_dashboard(
    dashboard_id: UUID,
    tenant_id: UUID,
    name: Optional[str],
    description: Optional[str],
    is_default: Optional[bool],
    db: Session,
) -> dict:
    """Rename / update a dashboard after verifying tenant ownership."""
    d = _get_dashboard_for_tenant(dashboard_id, tenant_id, db)

    if name is not None:
        if not name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        d.name = name.strip()
    if description is not None:
        d.description = description
    if is_default is not None:
        if is_default:
            db.query(Dashboard).filter(
                Dashboard.device_id == d.device_id,
                Dashboard.id != d.id,
            ).update({"is_default": False})
        d.is_default = is_default

    db.commit()
    db.refresh(d)
    return serialize_dashboard(d)


def delete_dashboard(dashboard_id: UUID, tenant_id: UUID, db: Session) -> None:
    """Delete a dashboard (cascades to widgets) after verifying tenant ownership."""
    d = _get_dashboard_for_tenant(dashboard_id, tenant_id, db)
    db.delete(d)
    db.commit()


# ── Widget service ────────────────────────────────────────────────────────────

VALID_WIDGET_TYPES = {
    "value_card", "line_chart", "gauge", "status_light",
    "bar_chart", "alarm_list", "timeseries_table", "pie_chart",
    "markdown", "entity_table", "html_card",
}


def list_widgets(dashboard_id: UUID, tenant_id: UUID, db: Session) -> List[dict]:
    """List all widgets for a dashboard, verifying tenant ownership."""
    d = _get_dashboard_for_tenant(dashboard_id, tenant_id, db)
    return [serialize_widget(w) for w in d.widgets]


def add_widget(
    dashboard_id: UUID,
    tenant_id: UUID,
    widget_type: str,
    title: str,
    config: dict,
    position: dict,
    db: Session,
) -> dict:
    """Add a widget to a dashboard after verifying tenant ownership."""
    d = _get_dashboard_for_tenant(dashboard_id, tenant_id, db)
    if widget_type not in VALID_WIDGET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid widget_type '{widget_type}'. "
                   f"Valid: {sorted(VALID_WIDGET_TYPES)}",
        )
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="Widget title is required")

    w = Widget(
        dashboard_id=dashboard_id,
        widget_type=widget_type,
        title=title.strip(),
        config=config,
        position=position,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return serialize_widget(w)


def update_widget(
    dashboard_id: UUID,
    widget_id: UUID,
    tenant_id: UUID,
    title: Optional[str],
    config: Optional[dict],
    position: Optional[dict],
    widget_type: Optional[str],
    db: Session,
) -> dict:
    """Update a widget after verifying tenant ownership of its dashboard."""
    # Ownership check: dashboard → device → tenant
    _get_dashboard_for_tenant(dashboard_id, tenant_id, db)

    w = db.query(Widget).filter(
        Widget.id == widget_id,
        Widget.dashboard_id == dashboard_id,
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")

    if widget_type is not None:
        if widget_type not in VALID_WIDGET_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid widget_type '{widget_type}'",
            )
        w.widget_type = widget_type
    if title is not None:
        if not title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        w.title = title.strip()
    if config is not None:
        w.config = config
    if position is not None:
        w.position = position

    db.commit()
    db.refresh(w)
    return serialize_widget(w)


def delete_widget(
    dashboard_id: UUID,
    widget_id: UUID,
    tenant_id: UUID,
    db: Session,
) -> None:
    """Delete a widget after verifying tenant ownership of its dashboard."""
    _get_dashboard_for_tenant(dashboard_id, tenant_id, db)

    w = db.query(Widget).filter(
        Widget.id == widget_id,
        Widget.dashboard_id == dashboard_id,
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")

    db.delete(w)
    db.commit()


def save_layout(
    dashboard_id: UUID,
    tenant_id: UUID,
    layout: List[dict],
    db: Session,
) -> dict:
    """
    Bulk-update widget positions after drag/resize.
    Verifies tenant ownership before any writes.
    layout = [{id, x, y, w, h}, ...]
    """
    _get_dashboard_for_tenant(dashboard_id, tenant_id, db)

    updated_ids = []
    for item in layout:
        wid = item.get("id")
        if not wid:
            continue
        widget = db.query(Widget).filter(
            Widget.id == wid,
            Widget.dashboard_id == dashboard_id,
        ).first()
        if widget:
            widget.position = {
                "x": int(item.get("x", 0)),
                "y": int(item.get("y", 0)),
                "w": int(item.get("w", 2)),
                "h": int(item.get("h", 3)),
            }
            updated_ids.append(wid)

    db.commit()
    return {"updated": updated_ids, "count": len(updated_ids)}
