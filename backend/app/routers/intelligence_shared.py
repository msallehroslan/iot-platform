"""
app/routers/intelligence_shared.py
Shared imports, helpers, constants and _call_groq for all intelligence sub-routers.
All sub-routers do: from app.routers.intelligence_shared import *
"""
from __future__ import annotations

import os
import re as _re
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth_deps import get_current_user, require_admin
from app.models.models import Device, Alarm, TelemetryData, ThresholdRule
from app.services.trend_service import get_device_key_trend, get_all_key_trends
from app.services.data_service import (
    get_latest_telemetry as ds_get_latest,
    get_aggregated_telemetry as ds_get_aggregated,
    get_active_alarms as ds_get_alarms,
    get_baseline_now as ds_get_baseline,
    get_anomaly_summary as ds_get_anomaly,
    get_health_summary as ds_get_health,
    get_unified_intelligence,
    get_key_intelligence,
)

logger = logging.getLogger(__name__)

# ── Ollama / LLM configuration ────────────────────────────────────────────────
# Single source of truth — all sub-routers import from here.

OLLAMA_URL   = "http://192.168.1.22:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"

# Legacy name constants — kept so any code that references them doesn't break.
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "qwen3:8b")
GROQ_MODEL_DEEP = os.getenv("GROQ_MODEL_DEEP", "qwen3:8b")

# Rate limiter constants
GROQ_CHAT_LIMIT    = int(os.getenv("GROQ_CHAT_LIMIT",    "20"))
GROQ_CHAT_WINDOW_H = int(os.getenv("GROQ_CHAT_WINDOW_H", "1"))

# Accounts excluded from rate limiting
GROQ_RATE_LIMIT_EXCLUDED: set[str] = {
    "msallehroslan@gmail.com",
}


async def _call_groq(
    api_key: str,
    messages: list,
    max_tokens: int = 4096,
    temperature: float = 0.4,
    model: str = None,
) -> str:
    """
    Universal LLM call — routes to Ollama Qwen3:8b.
    Named _call_groq for backward compatibility with all call sites.
    api_key is accepted but ignored — Ollama does not require one.
    """
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append("[SYSTEM]\n" + content)
        elif role == "assistant":
            parts.append("[ASSISTANT]\n" + content)
        else:
            parts.append("[USER]\n" + content)
    prompt = "\n\n".join(parts)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model":       OLLAMA_MODEL,
                "prompt":      prompt,
                "stream":      False,
                "temperature": temperature,
                "options":     {"num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        # Strip Qwen3 <think>...</think> reasoning blocks
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        return text


# ── Device access helpers ─────────────────────────────────────────────────────

def _assert_device(device_id: UUID, current_user, db: Session) -> Device:
    """Fetch device and verify tenant ownership. Raises 404 if not found."""
    q = db.query(Device).filter(
        Device.id        == device_id,
        Device.tenant_id == current_user.tenant_id,
    )
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    device = q.first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _scoped_devices(current_user, db: Session):
    """Return device query scoped to the current user's access level."""
    q = db.query(Device).filter(Device.tenant_id == current_user.tenant_id)
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        q = q.filter(Device.customer_id == current_user.customer_id)
    return q
