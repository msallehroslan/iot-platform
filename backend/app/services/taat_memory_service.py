"""
app/services/taat_memory_service.py — Agent Memory Service

Reads and writes to the agent_memory table.
Called by context builder (read) and after every action (write).

Memory types:
    incident       — "temperature spike on ESP32-001 at 2026-01-15 14:00 UTC"
    device_context — "Pump-01 usually runs at night"
    user_pref      — "User prefers Celsius, daily report at 8 AM"
    device_alias   — "ESP32-e823 controls test LED in lab B"
    outcome        — "RPC set led1=1 on ESP32-001 verified success at 14:05"
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_MEMORIES = 100   # per tenant cap — oldest pruned on write


# ── Read ──────────────────────────────────────────────────────────────────────

def get_memories(
    db: Session,
    tenant_id: UUID,
    user_id: Optional[UUID] = None,
    memory_type: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    """
    Fetch recent memories for a tenant, optionally filtered by type.
    Returns newest first.
    """
    try:
        from app.models.models import AgentMemory
        q = db.query(AgentMemory).filter(AgentMemory.tenant_id == tenant_id)
        if memory_type:
            q = q.filter(AgentMemory.memory_type == memory_type)
        rows = q.order_by(AgentMemory.created_at.desc()).limit(limit).all()
        return [
            {
                "id":          str(r.id),
                "type":        r.memory_type,
                "content":     r.content,
                "user_id":     str(r.user_id) if r.user_id else None,
                "created_at":  r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("get_memories failed (table may not exist yet): %s", exc)
        return []


def get_relevant_memories(
    db: Session,
    tenant_id: UUID,
    device_name: Optional[str] = None,
    key: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Fetch memories relevant to a specific device or key.
    Used by context builder to inject focused memory into the system prompt.
    """
    all_mem = get_memories(db, tenant_id, limit=50)
    if not device_name and not key:
        return all_mem[:limit]

    scored = []
    for m in all_mem:
        content_lower = m["content"].lower()
        score = 0
        if device_name and device_name.lower() in content_lower:
            score += 2
        if key and key.lower() in content_lower:
            score += 1
        if score > 0:
            scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:limit]]


def format_for_prompt(memories: list[dict]) -> str:
    """Format memory list for injection into the system prompt."""
    if not memories:
        return "  None"
    lines = []
    for m in memories:
        ts = m.get("created_at", "")[:16].replace("T", " ") if m.get("created_at") else ""
        lines.append(f"  [{m['type']}] {m['content']}" + (f" ({ts} UTC)" if ts else ""))
    return "\n".join(lines)


# ── Write ─────────────────────────────────────────────────────────────────────

def save_memory(
    db: Session,
    tenant_id: UUID,
    memory_type: str,
    content: str,
    user_id: Optional[UUID] = None,
    commit: bool = True,
) -> bool:
    """
    Write a memory entry. Prunes oldest entries if over MAX_MEMORIES.
    Non-fatal — never raises.
    """
    try:
        from app.models.models import AgentMemory

        # Prune oldest if at cap
        count = db.query(AgentMemory).filter(
            AgentMemory.tenant_id == tenant_id
        ).count()
        if count >= MAX_MEMORIES:
            oldest = (
                db.query(AgentMemory)
                .filter(AgentMemory.tenant_id == tenant_id)
                .order_by(AgentMemory.created_at.asc())
                .limit(max(1, count - MAX_MEMORIES + 1))
                .all()
            )
            for row in oldest:
                db.delete(row)

        mem = AgentMemory(
            tenant_id   = tenant_id,
            user_id     = user_id,
            memory_type = memory_type,
            content     = content[:2000],
        )
        db.add(mem)
        if commit:
            db.commit()
        return True
    except Exception as exc:
        logger.warning("save_memory failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def record_incident(
    db: Session,
    tenant_id: UUID,
    device_name: str,
    description: str,
    user_id: Optional[UUID] = None,
) -> None:
    """Record a notable incident (anomaly, alarm spike, failed action)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = f"{device_name}: {description} at {now}"
    save_memory(db, tenant_id, "incident", content, user_id=user_id)


def record_action_outcome(
    db: Session,
    tenant_id: UUID,
    action_type: str,
    device_name: str,
    params: dict,
    success: bool,
    detail: str = "",
    user_id: Optional[UUID] = None,
) -> None:
    """Record the outcome of an executed action for future TAAT context."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "✅ success" if success else "❌ failed"
    content = (
        f"{action_type} on {device_name} {status}: {params}"
        + (f" — {detail}" if detail else "")
        + f" at {now}"
    )
    save_memory(db, tenant_id, "outcome", content, user_id=user_id)


def record_device_context(
    db: Session,
    tenant_id: UUID,
    device_name: str,
    observation: str,
) -> None:
    """Record a behavioural observation about a device."""
    content = f"{device_name}: {observation}"
    save_memory(db, tenant_id, "device_context", content)


def record_action_outcome(
    db:        "Session",
    tenant_id: "UUID",
    plan:      "Plan",
    decision:  dict,
    user_id:   "Optional[UUID]" = None,
) -> None:
    """
    New signature — takes Plan + decision dict directly.
    Records structured outcome into agent_memory for future context.
    """
    from datetime import datetime, timezone
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status  = "✅ success" if decision.get("verified", True) else "⚠️ unverified"
    risk    = decision.get("risk", "LOW")
    reason  = decision.get("reason", "")
    action  = decision.get("action_taken", "")

    # Build compact content string
    steps_summary = ", ".join(
        s.tool for s in getattr(plan, "steps", [])
    ) or "no steps"

    content = (
        f"[{plan.intent}] {status} risk={risk} "
        f"steps=[{steps_summary}]"
        + (f" action={action}" if action else "")
        + (f" reason={reason}" if reason else "")
        + f" at {now}"
    )[:2000]

    save_memory(db, tenant_id, "outcome", content, user_id=user_id)
