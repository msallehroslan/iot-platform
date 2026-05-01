"""
app/services/audit.py — Audit log writer and quota enforcer.

Usage:
    from app.services.audit import audit, check_quota

    # Write an audit entry
    audit(db, user=current_user, action="device.create", resource="device",
          resource_id=str(device.id), detail={"name": device.name})

    # Enforce quota before creating a device
    check_quota(db, tenant_id, "devices")  # raises 429 if limit exceeded
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import AuditLog, TenantQuota, Device, Dashboard, User
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Audit writer ──────────────────────────────────────────────────────────────

def audit(
    db: Session,
    *,
    tenant_id: UUID,
    action: str,
    resource: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    user: Optional[User] = None,
    user_id: Optional[UUID] = None,
    user_email: Optional[str] = None,
    ip_address: Optional[str] = None,
    commit: bool = False,
) -> None:
    """
    Append an audit log entry. Non-fatal — never raises on write failure.
    Set commit=True only when writing standalone (not inside a larger transaction).
    """
    try:
        uid    = user.id    if user else user_id
        email  = user.email if user else user_email
        entry = AuditLog(
            tenant_id=tenant_id,
            user_id=uid,
            user_email=email,
            action=action,
            resource=resource,
            resource_id=resource_id,
            detail=detail,
        )
        db.add(entry)
        if commit:
            db.commit()
        logger.info(
            "audit action=%s resource=%s/%s user=%s tenant=%s",
            action, resource, resource_id, email, tenant_id,
        )
    except Exception as exc:
        logger.warning("Audit write failed (non-fatal): %s", exc)


def audit_from_request(
    db: Session,
    request: Request,
    *,
    tenant_id: UUID,
    action: str,
    resource: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    user: Optional[User] = None,
    commit: bool = False,
) -> None:
    """Convenience wrapper that extracts IP from request."""
    ip = request.client.host if request.client else None
    audit(
        db,
        tenant_id=tenant_id,
        action=action,
        resource=resource,
        resource_id=resource_id,
        detail=detail,
        user=user,
        ip_address=ip,
        commit=commit,
    )


# ── Quota enforcer ────────────────────────────────────────────────────────────

def _get_quota(db: Session, tenant_id: UUID) -> TenantQuota:
    """Fetch tenant quota row, or return a synthetic default."""
    row = db.query(TenantQuota).filter(TenantQuota.tenant_id == tenant_id).first()
    if row:
        return row
    # Return defaults without DB row (free tier)
    defaults = TenantQuota(tenant_id=tenant_id)
    return defaults


def check_quota(db: Session, tenant_id: UUID, resource: str) -> None:
    """
    Enforce resource quotas before creation.

    resource: "devices" | "dashboards"

    Raises HTTP 429 if the tenant is at their limit.
    """
    quota = _get_quota(db, tenant_id)

    if resource == "devices":
        limit   = quota.max_devices or settings.DEFAULT_MAX_DEVICES
        current = db.query(func.count(Device.id)).filter(
            Device.tenant_id == tenant_id
        ).scalar() or 0
        if current >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Device limit reached ({current}/{limit}). "
                       f"Upgrade your plan to add more devices.",
            )

    elif resource == "dashboards":
        limit   = quota.max_dashboards or settings.DEFAULT_MAX_DASHBOARDS
        from app.models.models import Dashboard
        current = db.query(func.count(Dashboard.id)).join(
            Device, Dashboard.device_id == Device.id
        ).filter(Device.tenant_id == tenant_id).scalar() or 0
        if current >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Dashboard limit reached ({current}/{limit}).",
            )


def check_tenant_ingest_rate(db: Session, tenant_id: UUID) -> None:
    """
    Enforce per-tenant ingest rate limit using ingest_metrics table.
    Called during telemetry ingest — raises 429 if over limit.
    """
    from app.models.models import IngestMetric
    from datetime import timedelta

    quota = _get_quota(db, tenant_id)
    limit = quota.max_telemetry_rate or settings.DEFAULT_TELEMETRY_RATE

    one_min_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
    rate = db.query(func.sum(IngestMetric.key_count)).filter(
        IngestMetric.tenant_id == tenant_id,
        IngestMetric.ts >= one_min_ago,
    ).scalar() or 0

    if rate >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Tenant ingest rate limit exceeded ({int(rate)}/{limit} events/min).",
        )
