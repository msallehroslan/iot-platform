"""
app/services/taat_policy.py — Auto-Execute Policy Gate

Controls whether TAAT can auto-execute a HIGH-risk action without
requiring explicit user confirmation.

Policy lookup order:
    1. Check agent_memory for an explicit policy entry
       (memory_type="auto_execute_policy", stored by an admin)
    2. Default: DENY — never auto-execute without policy approval

Policy entry format (stored in agent_memory.content as JSON):
    {
        "device_id": "<uuid or '*' for all>",
        "key":       "<key name or '*' for all>",
        "action":    "<allow|deny>",
        "reason":    "Admin approved 2026-01-15"
    }

To approve auto-execute for a device/key via TAAT:
    "allow auto execute for led1 on ESP32-001"
    → TAAT stores: {"device_id": "<id>", "key": "led1", "action": "allow"}

To revoke:
    "revoke auto execute for led1 on ESP32-001"
    → TAAT stores: {"device_id": "<id>", "key": "led1", "action": "deny"}
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def allows_auto_execute(
    db:        Session,
    tenant_id: UUID,
    device_id: Optional[str],
    key:       Optional[str],
    params:    Optional[dict] = None,
) -> bool:
    """
    Returns True only if an explicit policy entry approves auto-execution
    for this device/key combination.

    Default: False — recommend only, never auto-execute without approval.
    This is intentional: safety over convenience.
    """
    try:
        from app.models.models import AgentMemory

        policies = (
            db.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id    == tenant_id,
                AgentMemory.memory_type  == "auto_execute_policy",
            )
            .order_by(AgentMemory.created_at.desc())
            .limit(50)
            .all()
        )

        for p in policies:
            try:
                entry = json.loads(p.content)
            except (json.JSONDecodeError, TypeError):
                continue

            policy_device = entry.get("device_id", "*")
            policy_key    = entry.get("key",       "*")
            policy_action = entry.get("action",    "deny")

            device_match = policy_device == "*" or policy_device == device_id
            key_match    = policy_key    == "*" or policy_key    == key

            if device_match and key_match:
                allowed = policy_action == "allow"
                logger.info(
                    "policy.check device=%s key=%s → %s (policy_id=%s)",
                    device_id, key, "allow" if allowed else "deny", p.id,
                )
                return allowed

    except Exception as exc:
        logger.debug("policy.check failed: %s — defaulting to deny", exc)

    # No matching policy found → deny (safe default)
    logger.debug("policy.check no entry found device=%s key=%s → deny", device_id, key)
    return False


def set_policy(
    db:        Session,
    tenant_id: UUID,
    device_id: Optional[str],
    key:       Optional[str],
    allow:     bool,
    reason:    str = "",
    user_id:   Optional[UUID] = None,
) -> bool:
    """
    Store an auto-execute policy entry.
    Called by TAAT when admin says "allow auto execute for X".
    """
    try:
        from app.models.models import AgentMemory
        from datetime import datetime, timezone

        content = json.dumps({
            "device_id": device_id or "*",
            "key":       key       or "*",
            "action":    "allow" if allow else "deny",
            "reason":    reason or f"Set at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        })

        entry = AgentMemory(
            tenant_id   = tenant_id,
            user_id     = user_id,
            memory_type = "auto_execute_policy",
            content     = content,
        )
        db.add(entry)
        db.commit()

        logger.info(
            "policy.set device=%s key=%s action=%s",
            device_id, key, "allow" if allow else "deny",
        )
        return True

    except Exception as exc:
        logger.error("policy.set failed: %s", exc)
        db.rollback()
        return False
