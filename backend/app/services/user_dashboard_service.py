"""
app/services/user_dashboard_service.py

Service layer for the Phase 2 user-scoped multi-dashboard system.
All business logic here — router is a thin HTTP adapter.

Key differences from device-scoped dashboards (Phase 1):
  - Dashboards belong to a user_id, not a device_id
  - Auto-creates "Default Dashboard" when a user has none
  - Enforces exactly one is_default per user at the DB level
  - Cascade delete: removing a dashboard removes all its widgets
"""
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.models import UserDashboard, UserWidget

DEFAULT_DASHBOARD_NAME = "Default Dashboard"

VALID_WIDGET_TYPES = {
    "value_card", "line_chart", "gauge", "status_light",
    "bar_chart", "alarm_list", "timeseries_table", "pie_chart",
    "markdown", "entity_table", "html_card",
}


# ── Serializers ───────────────────────────────────────────────────────────────

def _widget_out(w: UserWidget) -> dict:
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


def _dashboard_out(d: UserDashboard, include_widgets: bool = True) -> dict:
    out = {
        "id":           str(d.id),
        "user_id":      d.user_id,
        "name":         d.name,
        "description":  d.description,
        "is_default":   d.is_default,
        "widget_count": len(d.widgets),
        "created_at":   d.created_at.isoformat() if d.created_at else None,
        "updated_at":   d.updated_at.isoformat() if d.updated_at else None,
    }
    if include_widgets:
        out["widgets"] = [_widget_out(w) for w in d.widgets]
    return out


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_single_default(user_id: str, exclude_id: Optional[UUID], db: Session) -> None:
    """Clear is_default on all other dashboards for this user."""
    q = db.query(UserDashboard).filter(UserDashboard.user_id == user_id)
    if exclude_id:
        q = q.filter(UserDashboard.id != exclude_id)
    q.update({"is_default": False}, synchronize_session=False)


def _get_or_create_default(user_id: str, db: Session) -> UserDashboard:
    """Return user's default dashboard, creating it if none exist."""
    existing = (
        db.query(UserDashboard)
        .filter(UserDashboard.user_id == user_id)
        .order_by(UserDashboard.created_at)
        .first()
    )
    if existing:
        # Make sure exactly one is marked default
        default = db.query(UserDashboard).filter(
            UserDashboard.user_id == user_id, UserDashboard.is_default == True
        ).first()
        if not default:
            existing.is_default = True
            db.commit()
            db.refresh(existing)
        return default or existing

    # No dashboards at all — auto-create
    d = UserDashboard(
        user_id=user_id,
        name=DEFAULT_DASHBOARD_NAME,
        is_default=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


# ── Dashboard service ─────────────────────────────────────────────────────────

def list_dashboards(user_id: str, db: Session) -> List[dict]:
    """
    Return all dashboards for the user, sorted oldest-first.
    Auto-creates a default dashboard if the user has none.
    """
    # Trigger auto-create if needed
    _get_or_create_default(user_id, db)

    rows = (
        db.query(UserDashboard)
        .filter(UserDashboard.user_id == user_id)
        .order_by(UserDashboard.created_at)
        .all()
    )
    return [_dashboard_out(d, include_widgets=False) for d in rows]


def get_dashboard(dashboard_id: UUID, user_id: str, db: Session) -> dict:
    """Return a single dashboard with all its widgets. Verifies ownership."""
    d = db.query(UserDashboard).filter(
        UserDashboard.id == dashboard_id,
        UserDashboard.user_id == user_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return _dashboard_out(d, include_widgets=True)


def get_default_dashboard(user_id: str, db: Session) -> dict:
    """
    Return the user's default dashboard (with widgets).
    Auto-creates if none exist.
    Used on app load: GET /user-dashboards/default
    """
    d = _get_or_create_default(user_id, db)
    return _dashboard_out(d, include_widgets=True)


def create_dashboard(user_id: str, name: str,
                     description: Optional[str], db: Session) -> dict:
    """Create a new dashboard. Never sets it as default automatically."""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Dashboard name is required")

    d = UserDashboard(
        user_id=user_id,
        name=name.strip(),
        description=description,
        is_default=False,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return _dashboard_out(d, include_widgets=True)


def rename_dashboard(dashboard_id: UUID, user_id: str,
                     name: str, db: Session) -> dict:
    """Rename a dashboard."""
    d = db.query(UserDashboard).filter(
        UserDashboard.id == dashboard_id,
        UserDashboard.user_id == user_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    d.name = name.strip()
    db.commit()
    db.refresh(d)
    return _dashboard_out(d, include_widgets=False)


def set_default_dashboard(dashboard_id: UUID, user_id: str, db: Session) -> dict:
    """
    Set this dashboard as the user's default.
    Guarantees exactly one default per user.
    """
    d = db.query(UserDashboard).filter(
        UserDashboard.id == dashboard_id,
        UserDashboard.user_id == user_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Clear all other defaults first
    _ensure_single_default(user_id, exclude_id=dashboard_id, db=db)
    d.is_default = True
    db.commit()
    db.refresh(d)
    return _dashboard_out(d, include_widgets=False)


def delete_dashboard(dashboard_id: UUID, user_id: str, db: Session) -> None:
    """
    Delete a dashboard and all its widgets (cascade).
    Cannot delete the last remaining dashboard.
    If deleting the default, promotes the next oldest dashboard.
    """
    d = db.query(UserDashboard).filter(
        UserDashboard.id == dashboard_id,
        UserDashboard.user_id == user_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Count how many the user has
    total = db.query(UserDashboard).filter(
        UserDashboard.user_id == user_id
    ).count()
    if total <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete your only dashboard"
        )

    was_default = d.is_default
    db.delete(d)
    db.flush()

    # If we deleted the default, promote the oldest remaining
    if was_default:
        next_d = (
            db.query(UserDashboard)
            .filter(UserDashboard.user_id == user_id)
            .order_by(UserDashboard.created_at)
            .first()
        )
        if next_d:
            next_d.is_default = True

    db.commit()


# ── Widget service ────────────────────────────────────────────────────────────

def _get_dashboard_or_404(dashboard_id: UUID, user_id: str, db: Session) -> UserDashboard:
    d = db.query(UserDashboard).filter(
        UserDashboard.id == dashboard_id,
        UserDashboard.user_id == user_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return d


def add_widget(dashboard_id: UUID, user_id: str, widget_type: str,
               title: str, config: dict, position: dict, db: Session) -> dict:
    _get_dashboard_or_404(dashboard_id, user_id, db)

    if widget_type not in VALID_WIDGET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid widget_type '{widget_type}'. "
                   f"Valid: {sorted(VALID_WIDGET_TYPES)}"
        )
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="Widget title is required")

    w = UserWidget(
        dashboard_id=dashboard_id,
        widget_type=widget_type,
        title=title.strip(),
        config=config,
        position=position,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _widget_out(w)


def update_widget(dashboard_id: UUID, widget_id: UUID, user_id: str,
                  widget_type: Optional[str], title: Optional[str],
                  config: Optional[dict], position: Optional[dict],
                  db: Session) -> dict:
    _get_dashboard_or_404(dashboard_id, user_id, db)

    w = db.query(UserWidget).filter(
        UserWidget.id == widget_id,
        UserWidget.dashboard_id == dashboard_id,
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")

    if widget_type is not None:
        if widget_type not in VALID_WIDGET_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid widget_type '{widget_type}'")
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
    return _widget_out(w)


def delete_widget(dashboard_id: UUID, widget_id: UUID,
                  user_id: str, db: Session) -> None:
    _get_dashboard_or_404(dashboard_id, user_id, db)

    w = db.query(UserWidget).filter(
        UserWidget.id == widget_id,
        UserWidget.dashboard_id == dashboard_id,
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found")
    db.delete(w)
    db.commit()


def save_layout(dashboard_id: UUID, user_id: str,
                layout: List[dict], db: Session) -> dict:
    """Bulk-save widget positions after drag-and-drop."""
    _get_dashboard_or_404(dashboard_id, user_id, db)

    updated = []
    for item in layout:
        wid = item.get("id")
        if not wid:
            continue
        w = db.query(UserWidget).filter(
            UserWidget.id == wid,
            UserWidget.dashboard_id == dashboard_id,
        ).first()
        if w:
            w.position = {
                "x": int(item.get("x", 0)),
                "y": int(item.get("y", 0)),
                "w": int(item.get("w", 2)),
                "h": int(item.get("h", 3)),
            }
            updated.append(wid)

    db.commit()
    return {"updated": updated, "count": len(updated)}
