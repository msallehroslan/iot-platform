"""
app/routers/intelligence.py — Intelligence Layer API

Endpoints:
  GET  /intelligence/trend/{device_id}/{key}     — trend for one key
  GET  /intelligence/trend/{device_id}           — trends for all keys
  POST /intelligence/rca/{device_id}             — LLM root cause analysis
  GET  /intelligence/summary/{device_id}         — AI health summary
"""

from __future__ import annotations

import os
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
# TAAT modules imported inside ai_chat to avoid circular import at startup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["Intelligence"])


def _assert_device(device_id: UUID, current_user, db: Session) -> Device:
    q = db.query(Device).filter(
        Device.id == device_id,
        Device.tenant_id == current_user.tenant_id,
    )
    # CUSTOMER_USER can only access their customer's devices
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


# ── Production safety helpers ─────────────────────────────────────────────────

_OFFLINE_THRESHOLD_MINS = 5

def _is_device_offline(device: Device) -> bool:
    """
    Task 7: stale / offline detection.
    Returns True if device hasn't reported in OFFLINE_THRESHOLD_MINS.
    """
    if not device.last_seen_at:
        return True
    from datetime import datetime, timezone
    age = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds() / 60
    return age > _OFFLINE_THRESHOLD_MINS


def _safe_chat_response(error: str, engine: str = "error") -> dict:
    """
    Task 7: safe fallback response — never crashes, never silent.
    """
    logger.error("taat.safe_fallback: %s", error)
    return {
        "reply":  f"I encountered an issue: {error[:120]}. Please try again.",
        "engine": engine,
    }


# ── Trend Detection ───────────────────────────────────────────────────────────

@router.get("/trend/{device_id}/{key}")
def get_trend(
    device_id: UUID,
    key: str,
    minutes: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get trend analysis for a specific telemetry key."""
    _assert_device(device_id, current_user, db)
    return get_device_key_trend(db, str(device_id), key, minutes)


@router.get("/trend/{device_id}")
def get_all_trends(
    device_id: UUID,
    minutes: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get trend analysis for all keys on a device."""
    _assert_device(device_id, current_user, db)
    return get_all_key_trends(db, str(device_id), minutes)


# ── LLM Root Cause Analysis ───────────────────────────────────────────────────

@router.post("/rca/{device_id}")
async def root_cause_analysis(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Intelligence Layer: LLM-powered root cause analysis.
    Gathers device context, recent alarms, telemetry trends,
    and threshold rules, then asks Claude to explain what's happening.
    """
    device = _assert_device(device_id, current_user, db)

    # ── Gather context ────────────────────────────────────────────────────────
    # 1. Recent alarms (last 24h)
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    alarms = (
        db.query(Alarm)
        .filter(
            Alarm.device_id == device_id,
            Alarm.created_at >= since_24h,
        )
        .order_by(Alarm.created_at.desc())
        .limit(20)
        .all()
    )

    # 2. Current trends for all keys
    trends = get_all_key_trends(db, str(device_id), minutes=60)

    # 3. Active threshold rules
    rules = (
        db.query(ThresholdRule)
        .filter(
            ThresholdRule.tenant_id == current_user.tenant_id,
            ThresholdRule.is_active == True,
        )
        .filter(
            (ThresholdRule.device_id == device_id) |
            (ThresholdRule.device_id == None)
        )
        .all()
    )

    # 4. Recent telemetry snapshot (last 10 values per key)
    telemetry_snapshot = {}
    since_1h = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_rows = (
        db.query(TelemetryData)
        .filter(
            TelemetryData.device_id == device_id,
            TelemetryData.ts >= since_1h,
            TelemetryData.value_num.isnot(None),
        )
        .order_by(TelemetryData.ts.desc())
        .limit(100)
        .all()
    )
    for row in recent_rows:
        if row.key not in telemetry_snapshot:
            telemetry_snapshot[row.key] = []
        if len(telemetry_snapshot[row.key]) < 10:
            telemetry_snapshot[row.key].append({
                "ts": row.ts.isoformat(),
                "value": float(row.value_num),
            })

    # ── Build prompt ──────────────────────────────────────────────────────────
    alarm_summary = []
    for a in alarms:
        alarm_summary.append({
            "type": a.alarm_type,
            "severity": a.severity.value if hasattr(a.severity, 'value') else str(a.severity),
            "status": a.status.value if hasattr(a.status, 'value') else str(a.status),
            "created": a.created_at.isoformat(),
            "details": a.details,
        })

    trend_summary = {
        k: {
            "trend": v["trend"],
            "change_pct": v.get("change_pct", 0),
            "latest": v.get("latest_value"),
            "confidence": v.get("confidence", 0),
        }
        for k, v in trends.items()
    }

    rules_summary = [
        {
            "key": r.key,
            "condition": r.condition,
            "threshold": r.threshold,
            "severity": r.severity.value if hasattr(r.severity, 'value') else str(r.severity),
            "alarm_type": r.alarm_type,
        }
        for r in rules
    ]

    context = {
        "device": {
            "name": device.name,
            "type": device.device_type,
            "status": device.status.value if hasattr(device.status, 'value') else str(device.status),
            "last_seen": device.last_seen_at.isoformat() if device.last_seen_at else None,
        },
        "alarms_last_24h": alarm_summary,
        "current_trends": trend_summary,
        "active_rules": rules_summary,
        "recent_telemetry": telemetry_snapshot,
    }

    prompt = f"""You are an IoT platform intelligence engine analyzing a device's health.

Device context:
{json.dumps(context, indent=2)}

Analyze this data and provide:

1. **Health Status** — Overall device health: HEALTHY / WARNING / CRITICAL with one sentence explanation.

2. **Root Cause Analysis** — What is causing any alarms or anomalies? Be specific about which telemetry keys are involved and why.

3. **Trend Insights** — What patterns do you see in the data? Any correlations between keys?

4. **Risk Assessment** — What could happen in the next 1-4 hours if current trends continue?

5. **Recommended Actions** — Specific actionable steps ranked by priority (max 3).

Be concise, technical, and actionable. Avoid generic advice. Base everything on the actual data provided.
Format your response with clear sections using the numbered headers above."""

    # ── Call Claude API ───────────────────────────────────────────────────────
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        # Return structured analysis without LLM if no key configured
        return {
            "device_id": str(device_id),
            "device_name": device.name,
            "analysis": _rule_based_analysis(context),
            "context": context,
            "engine": "rule-based (set GROQ_API_KEY for AI)",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL_DEEP,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            analysis = data["choices"][0]["message"]["content"]

        return {
            "device_id":    str(device_id),
            "device_name":  device.name,
            "analysis":     analysis,
            "context":      context,
            "engine":       f"groq/{GROQ_MODEL_DEEP}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        logger.error("rca.llm_failed device=%s error=%s", device_id, exc)
        # Fallback to rule-based analysis
        return {
            "device_id":    str(device_id),
            "device_name":  device.name,
            "analysis":     _rule_based_analysis(context),
            "context":      context,
            "engine":       "rule-based-fallback",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error":        str(exc),
        }


# ── AI Health Summary ─────────────────────────────────────────────────────────

@router.get("/summary/{device_id}")
async def device_summary(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Quick AI health summary for a device.
    Lighter than full RCA — returns one-line status + key insights.
    """
    device = _assert_device(device_id, current_user, db)

    # Active alarms
    active_alarms = (
        db.query(Alarm)
        .filter(
            Alarm.device_id == device_id,
            Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),
        )
        .all()
    )

    # Trends
    trends = get_all_key_trends(db, str(device_id), minutes=30)

    # Build quick summary
    rising  = [k for k, v in trends.items() if v["trend"] == "RISING"]
    falling = [k for k, v in trends.items() if v["trend"] == "FALLING"]
    spikes  = [k for k, v in trends.items() if v["trend"] in ("SPIKE", "DROP")]

    health = "HEALTHY"
    insights = []

    if active_alarms:
        critical = [a for a in active_alarms if str(a.severity).upper() in ("CRITICAL", "MAJOR")]
        health = "CRITICAL" if critical else "WARNING"
        insights.append(f"{len(active_alarms)} active alarm(s): {', '.join(set(a.alarm_type for a in active_alarms[:3]))}")

    if spikes:
        health = max(health, "WARNING", key=lambda x: ["HEALTHY","WARNING","CRITICAL"].index(x))
        insights.append(f"Anomaly detected in: {', '.join(spikes)}")

    if rising:
        insights.append(f"Rising trend: {', '.join(rising)}")
    if falling:
        insights.append(f"Falling trend: {', '.join(falling)}")

    if not insights:
        insights.append("All parameters within normal range")

    # Time since last seen
    if device.last_seen_at:
        age = (datetime.now(timezone.utc) - device.last_seen_at).total_seconds()
        if age > 300:
            health = "WARNING"
            insights.insert(0, f"Device offline for {int(age/60)} minutes")

    return {
        "device_id":    str(device_id),
        "device_name":  device.name,
        "health":       health,
        "insights":     insights,
        "active_alarms": len(active_alarms),
        "trends":       {k: v["trend"] for k, v in trends.items()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Rule-based fallback (no LLM) ─────────────────────────────────────────────

def _rule_based_analysis(context: dict) -> str:
    """Simple rule-based analysis when LLM is not available."""
    lines = []
    alarms = context.get("alarms_last_24h", [])
    trends = context.get("current_trends", {})
    device = context.get("device", {})

    # Health status
    active = [a for a in alarms if "ACTIVE" in a.get("status", "")]
    if active:
        lines.append(f"**1. Health Status** — WARNING: {len(active)} active alarm(s)")
    else:
        lines.append("**1. Health Status** — HEALTHY: No active alarms")

    # Root cause
    lines.append("\n**2. Root Cause Analysis**")
    if alarms:
        for a in alarms[:3]:
            details = a.get("details", {})
            lines.append(f"- {a['type']}: {details.get('message', 'threshold breached')}")
    else:
        lines.append("- No alarms in last 24 hours")

    # Trends
    lines.append("\n**3. Trend Insights**")
    for key, t in trends.items():
        trend = t.get("trend", "UNKNOWN")
        change = t.get("change_pct", 0)
        lines.append(f"- {key}: {trend} ({change:+.1f}% over window)")

    # Risk
    lines.append("\n**4. Risk Assessment**")
    rising_critical = [k for k, v in trends.items()
                      if v.get("trend") == "RISING" and abs(v.get("change_pct", 0)) > 20]
    if rising_critical:
        lines.append(f"- {', '.join(rising_critical)} rising rapidly — monitor closely")
    else:
        lines.append("- No immediate risk detected from current trends")

    # Actions
    lines.append("\n**5. Recommended Actions**")
    if active:
        lines.append("1. Acknowledge and investigate active alarms")
    if rising_critical:
        lines.append(f"2. Check {rising_critical[0]} source — rapid increase detected")
    lines.append("- Add ANTHROPIC_API_KEY env var for AI-powered analysis")

    return "\n".join(lines)


# ── AI Chatbot ────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str     # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    device_id: Optional[UUID] = None        # optional device context
    pending_confirm: Optional[dict] = None  # pending RPC awaiting confirmation


async def _call_groq(api_key: str, messages: list, max_tokens: int = 512, temperature: float = 0.4, model: str = None) -> str:
    """Helper: call Groq and return text reply. Retries on 429/5xx with exponential backoff."""
    import asyncio as _asyncio
    use_model = model or GROQ_MODEL_FAST
    delays = [1.0, 3.0]  # wait 1s then 3s before final attempt
    last_exc = None
    for attempt, delay in enumerate([0.0] + delays):
        if delay:
            await _asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": use_model, "max_tokens": max_tokens,
                          "messages": messages, "temperature": temperature},
                )
                if resp.status_code in (429, 500, 502, 503) and attempt < len(delays):
                    logger.warning("_call_groq attempt %d status=%d — retrying", attempt + 1, resp.status_code)
                    last_exc = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in (429, 500, 502, 503) or attempt >= len(delays):
                raise
            logger.warning("_call_groq attempt %d status=%d — retrying", attempt + 1, exc.response.status_code)
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt >= len(delays):
                raise
            logger.warning("_call_groq attempt %d timeout — retrying", attempt + 1)
    raise last_exc


# ── Groq model strategy ──────────────────────────────────────────────────────
# 8b: high quota (14,400/day) — chat, RPC parsing, summaries, comparisons
# 70b: low quota (1,000/day)  — RCA, alarm explanation, daily report
# ── Model selection ───────────────────────────────────────────────────────────
# Override via Render env vars to switch models without redeploying.
#
# Groq-hosted options (all free tier, just set env var):
#   Llama:  llama-3.1-8b-instant   llama-3.3-70b-versatile
#   Gemma:  gemma2-9b-it            gemma-7b-it
#   Mixtral: mixtral-8x7b-32768
#
# FAST = used for every chat turn (intent + reply)
# DEEP = used for RCA, alarm explanation, daily report
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
GROQ_MODEL_DEEP = os.getenv("GROQ_MODEL_DEEP", "llama-3.3-70b-versatile")

# ── Groq rate limiter ─────────────────────────────────────────────────────────
# 20 chat requests per user per hour — prevents one user burning the shared quota.
# Uses the existing rate_limits table (token = "groq:<user_id>", window = 1 hour).

GROQ_CHAT_LIMIT    = int(os.getenv("GROQ_CHAT_LIMIT", "20"))     # requests per window
GROQ_CHAT_WINDOW_H = int(os.getenv("GROQ_CHAT_WINDOW_H", "1"))   # window size in hours


# Users excluded from Groq rate limiting (superadmins / platform owners)
GROQ_RATE_LIMIT_EXCLUDED = {
    "msallehroslan@gmail.com",
}


def _check_groq_rate_limit(db: Session, user_id: str, user_email: str = "") -> dict:
    """
    Check and increment the Groq chat rate limit for a user.
    Returns {"allowed": bool, "used": int, "limit": int, "resets_in_mins": int}
    Raises HTTP 429 if over limit.
    Excluded emails bypass the limit entirely.
    """
    # Bypass for excluded accounts
    if user_email.lower() in GROQ_RATE_LIMIT_EXCLUDED:
        return {"allowed": True, "used": 0, "limit": 999999, "resets_in_mins": 0, "excluded": True}

    from app.models.models import RateLimit
    from datetime import timedelta

    window_duration = timedelta(hours=GROQ_CHAT_WINDOW_H)
    now             = datetime.now(timezone.utc)
    window_start    = now - window_duration
    token           = f"groq:{user_id}"

    # Find existing window row
    row = (
        db.query(RateLimit)
        .filter(
            RateLimit.token == token,
            RateLimit.window_start >= window_start,
        )
        .order_by(RateLimit.window_start.desc())
        .first()
    )

    if row is None:
        # First request in this window — create row
        row = RateLimit(
            token         = token,
            request_count = 1,
            window_start  = now,
        )
        db.add(row)
        db.commit()
        return {"allowed": True, "used": 1, "limit": GROQ_CHAT_LIMIT, "resets_in_mins": GROQ_CHAT_WINDOW_H * 60}

    if row.request_count >= GROQ_CHAT_LIMIT:
        resets_at   = row.window_start + window_duration
        resets_mins = max(1, int((resets_at - now).total_seconds() / 60))
        raise HTTPException(
            status_code=429,
            detail={
                "error":          "rate_limit_exceeded",
                "message":        f"You've used {row.request_count}/{GROQ_CHAT_LIMIT} AI requests this hour. Resets in {resets_mins} minute(s).",
                "used":           row.request_count,
                "limit":          GROQ_CHAT_LIMIT,
                "resets_in_mins": resets_mins,
            }
        )

    row.request_count += 1
    db.commit()
    return {"allowed": True, "used": row.request_count, "limit": GROQ_CHAT_LIMIT, "resets_in_mins": GROQ_CHAT_WINDOW_H * 60}


def _get_device_keys(db: Session, devices: list) -> dict:
    """
    Fetch actual telemetry keys for each device from the DB.
    Returns {device_name: [key1, key2, ...]} using real data.
    """
    from app.models.models import LatestTelemetry
    device_keys = {}
    for d in devices:
        rows = (
            db.query(LatestTelemetry.key)
            .filter(LatestTelemetry.device_id == d["id"])
            .all()
        )
        if rows:
            device_keys[d["name"]] = [r.key for r in rows]
    return device_keys


async def _try_parse_rpc_intent(
    api_key: str,
    user_message: str,
    devices: list,
    device_keys: dict,
) -> Optional[dict]:
    """
    Use LLM to detect if the user wants to send an RPC command.
    Returns {"device_name": str, "params": dict} or None.

    Uses REAL telemetry keys from the DB — no hardcoding.
    Only triggers for clear control intent keywords.
    """
    # Control keywords — simple and direct
    control_keywords = [
        "turn on", "turn off", "switch on", "switch off",
        "set ", "enable", "disable", "activate", "deactivate",
        "toggle", "open", "close", "start", "stop", "run", "pause",
    ]

    # Hard exclusions — never trigger RPC for these
    exclusion_keywords = [
        "threshold", "alarm rule", "rule chain",
        "set alarm", "set distance alarm", "set temperature alarm",
        "set humidity alarm", "set pressure alarm",
        "create alarm", "create rule", "add alarm", "add rule",
        "delete rule", "remove rule", "delete all", "remove all",
        "standard deviation", "baseline",
        "i've set", "i have set", "was set",
        "report", "analysis", "acknowledge", "clear alarm",
        " alarm ", " alarm on ", "alarm above", "alarm below",
        "warning alarm", "critical alarm", "major alarm",
    ]

    msg_lower = user_message.lower()

    # Bail if exclusion keyword found
    if any(ex in msg_lower for ex in exclusion_keywords):
        return None

    # Also bail if message contains alarm/rule creation patterns
    alarm_creation_words = ["above", "below", "greater than", "less than", "exceeds", "drops below"]
    alarm_subject_words = ["alarm", "rule", "warning", "critical", "threshold"]
    has_alarm_subject = any(w in msg_lower for w in alarm_subject_words)
    has_comparison = any(w in msg_lower for w in alarm_creation_words)
    if has_alarm_subject and has_comparison:
        return None

    if not any(kw in msg_lower for kw in control_keywords):
        return None

    device_names = [d["name"] for d in devices]
    if not device_names:
        return None

    # Build device→keys context for the LLM
    keys_context = ""
    if device_keys:
        keys_context = "\nActual controllable keys per device (from live telemetry):\n"
        for dname, keys in device_keys.items():
            keys_context += f"  {dname}: {json.dumps(keys)}\n"
    else:
        keys_context = "\nNo telemetry keys known yet — infer from user message."

    parse_prompt = f"""You are an IoT RPC command parser. Extract the device control intent from the user message.

Available devices: {json.dumps(device_names)}
{keys_context}
User message: "{user_message}"

Respond ONLY with valid JSON in this exact format (no explanation, no markdown):
{{"device_name": "<exact device name from list>", "params": {{"<key>": <true/false or number>}}}}

Rules:
- Use the EXACT key names from the device's actual key list above.
- "turn on led1" → {{"params": {{"led1": true}}}}
- "turn off led2" → {{"params": {{"led2": false}}}}
- "set relay1 to 1" → {{"params": {{"relay1": true}}}}
- For boolean keys: on/start/enable/open/activate = true, off/stop/disable/close/deactivate = false
- If no device is mentioned and only one device exists, use that device.
- If the key name is ambiguous, pick the closest match from the device's actual key list.
- If intent is unclear or not a control command, respond with: null"""

    try:
        result = await _call_groq(api_key, [{"role": "user", "content": parse_prompt}], max_tokens=150, temperature=0.1)
        result = result.strip()
        if result.lower() == "null" or not result.startswith("{"):
            return None
        parsed = json.loads(result)
        if "device_name" in parsed and "params" in parsed:
            return parsed
    except Exception as exc:
        logger.debug("rpc intent parse failed: %s", exc)
    return None


async def _execute_rpc_from_chat(
    db: Session,
    current_user,
    devices: list,
    device_name: str,
    params: dict,
) -> Optional[dict]:
    """
    Find the device by name and queue an RPC command via rpc_service.
    Returns result dict or None on failure.

    Phase 11: now delegates entirely to rpc_service.send_command_by_device_name()
    — validation, audit logging, rate limiting and WS dispatch all happen there.
    """
    from app.services.rpc_service import send_command_by_device_name
    return await send_command_by_device_name(
        db,
        devices     = devices,
        device_name = device_name,
        method      = "set",
        params      = params,
        current_user= current_user,
        source      = "chat",
    )


# ── Rule chain intent detection + execution ───────────────────────────────────

RULE_KEYWORDS = [
    "set alarm", "create alarm", "add alarm", "create rule", "add rule",
    "set rule", "update rule", "change rule", "change the", "update the", "modify rule", "delete rule", "remove rule",
    "delete all rules", "remove all rules", "clear all rules",
    "delete rules chain", "clear rules chain", "remove rules chain",
    "set threshold", "change threshold", "update threshold",
    "when temperature", "when humidity", "when distance", "when pressure",
    "alert me", "notify me", "alarm when", "trigger when",
    "above ", "below ", "greater than", "less than", "exceeds", "drops below",
]

RULE_EXCLUSION = [
    "what is", "what are", "show me", "list", "which rules", "current rules",
    "existing rules", "how many rules", "do i have",
]


# ── User management intent ────────────────────────────────────────────────────

USER_KEYWORDS = [
    "invite user", "add user", "create user", "new user",
    "delete user", "remove user", "deactivate user",
    "change role", "update role", "make admin", "make tenant user",
    "list users", "list all users", "show users", "show all users",
    "who has access", "all users", "view users", "get users",
]


async def _try_parse_user_intent(
    api_key: str,
    user_message: str,
    existing_users: list,
) -> Optional[dict]:
    """
    Detect user management intent.
    Returns action dict or None.
    """
    msg_lower = user_message.lower()
    if not any(kw in msg_lower for kw in USER_KEYWORDS):
        return None
    # Fast path for list — no Groq needed
    if any(kw in msg_lower for kw in ["list", "show", "view", "all users", "who has"]):
        return {"action": "list"}

    users_summary = [
        {"email": u.email, "id": str(u.id), "role": u.role,
         "name": f"{u.first_name or ''} {u.last_name or ''}".strip()}
        for u in existing_users
    ]

    parse_prompt = f"""You are a user management parser for an IoT platform.

Existing users: {json.dumps(users_summary)}
User message: "{user_message}"

Valid roles: TENANT_ADMIN, TENANT_USER

Respond ONLY with valid JSON or null:

For INVITE: {{"action":"invite","email":"<email>","role":"<role>","first_name":"<name or null>","password":"<random 10 char if not specified>"}}
For DELETE: {{"action":"delete","user_id":"<id from existing users>"}}
For ROLE CHANGE: {{"action":"change_role","user_id":"<id>","role":"<new role>"}}
For LIST: {{"action":"list"}}

Examples:
- "invite john@example.com as admin" → {{"action":"invite","email":"john@example.com","role":"TENANT_ADMIN","first_name":null,"password":"Rand0mPass1"}}
- "delete john@example.com" → {{"action":"delete","user_id":"<matching id>"}}
- "make john admin" → {{"action":"change_role","user_id":"<matching id>","role":"TENANT_ADMIN"}}
- "list users" → {{"action":"list"}}
- If unclear → null"""

    try:
        result = await _call_groq(api_key, [{"role": "user", "content": parse_prompt}], max_tokens=200, temperature=0.1)
        result = result.strip()
        if result.lower() == "null" or not result.startswith("{"):
            return None
        parsed = json.loads(result)
        if "action" in parsed:
            return parsed
    except Exception as exc:
        logger.debug("user intent parse failed: %s", exc)
    return None


async def _execute_user_from_chat(
    db: Session,
    current_user,
    intent: dict,
    existing_users: list,
) -> Optional[dict]:
    """Execute user management action."""
    from app.models.models import User
    from app.core.security import get_password_hash
    from app.services.audit import audit

    action = intent.get("action")

    try:
        if action == "list":
            return {
                "action": "list",
                "users": [
                    {"email": u.email, "role": u.role,
                     "name": f"{u.first_name or ''} {u.last_name or ''}".strip(),
                     "active": u.is_active}
                    for u in existing_users
                ]
            }

        elif action == "invite":
            email = intent.get("email", "").strip().lower()
            if not email or "@" not in email:
                return None
            existing = db.query(User).filter(User.email == email).first()
            if existing:
                return {"action": "invite", "error": f"{email} already exists"}

            role = intent.get("role", "TENANT_USER")
            if role not in ("TENANT_ADMIN", "TENANT_USER"):
                role = "TENANT_USER"

            import secrets as _secrets
            password = intent.get("password") or _secrets.token_urlsafe(10)

            user = User(
                email           = email,
                hashed_password = get_password_hash(password),
                first_name      = intent.get("first_name"),
                role            = role,
                tenant_id       = current_user.tenant_id,
                is_active       = True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="user.invite", resource="user", resource_id=str(user.id),
                  detail={"email": email, "role": role, "source": "chat"}, commit=True)
            return {
                "action":   "invited",
                "email":    email,
                "role":     role,
                "password": password,  # shown once so admin can share
            }

        elif action == "delete":
            user_id = intent.get("user_id")
            user = db.query(User).filter(
                User.id == user_id,
                User.tenant_id == current_user.tenant_id,
            ).first()
            if not user:
                return None
            if str(user.id) == str(current_user.id):
                return {"action": "delete", "error": "Cannot delete yourself"}
            email = user.email
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="user.delete", resource="user", resource_id=str(user_id),
                  detail={"email": email, "source": "chat"})
            db.delete(user)
            db.commit()
            return {"action": "deleted", "email": email}

        elif action == "change_role":
            user_id = intent.get("user_id")
            new_role = intent.get("role", "TENANT_USER")
            if new_role not in ("TENANT_ADMIN", "TENANT_USER"):
                return None
            user = db.query(User).filter(
                User.id == user_id,
                User.tenant_id == current_user.tenant_id,
            ).first()
            if not user:
                return None
            old_role = user.role
            user.role = new_role
            db.commit()
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="user.update", resource="user", resource_id=str(user_id),
                  detail={"email": user.email, "old_role": old_role, "new_role": new_role, "source": "chat"}, commit=True)
            return {"action": "role_changed", "email": user.email, "old_role": old_role, "new_role": new_role}

    except Exception as exc:
        logger.error("user management execution failed: %s", exc)
        db.rollback()

    return None


async def _try_parse_rule_intent(
    api_key: str,
    user_message: str,
    devices: list,
    existing_rules: list,
) -> Optional[dict]:
    """
    Use LLM to detect if user wants to create/update/delete a threshold rule.
    Returns action dict or None.
    """
    msg_lower = user_message.lower()

    # Must have at least one rule keyword
    if not any(kw in msg_lower for kw in RULE_KEYWORDS):
        return None

    # Skip if it's just a question about rules
    if any(ex in msg_lower for ex in RULE_EXCLUSION):
        return None

    # Smart fast path for DELETE/UPDATE — no Groq, match against actual existing rules
    import re as _re
    if any(w in msg_lower for w in ["delete", "remove", "clear"]):
        if any(w in msg_lower for w in ["all rules", "rules chain", "rule chain", "all rule"]):
            return {"action": "delete", "delete_all": True}
        # Match against actual rule keys that exist in DB
        for rule in existing_rules:
            if rule.key.lower() in msg_lower:
                dn = next((d["name"] for d in devices if d["name"].lower() in msg_lower), None)
                return {"action": "delete", "key": rule.key, "device_name": dn}
        # Fallback: extract word before "rule" or "alarm"
        m = _re.search(r'(\w+)\s+(?:rule|alarm)', msg_lower)
        if m and m.group(1) not in ["the","a","an","this","that","all","any"]:
            return {"action": "delete", "key": m.group(1), "device_name": None}

    if any(w in msg_lower for w in ["change", "update", "modify", "change the", "update the", "set"]):
        for rule in existing_rules:
            if rule.key.lower() in msg_lower:
                nums = _re.findall(r'\d+\.?\d*', user_message)
                threshold = float(nums[0]) if nums else None
                dn = next((d["name"] for d in devices if d["name"].lower() in msg_lower), None)
                if threshold is not None:
                    return {"action": "update", "key": rule.key, "device_name": dn,
                            "threshold": threshold, "condition": rule.condition,
                            "severity": rule.severity.value if hasattr(rule.severity,"value") else str(rule.severity)}

    # Fast path for CREATE — extract device, key, threshold, severity directly
    # Pattern: "set <key> alarm on <device> above/below <number> <severity>"
    # or: "create <severity> alarm when <key> exceeds <number>"
    import re as _re2
    create_words = ["set", "create", "add", "alert me", "notify me", "alarm when", "when "]
    condition_words = {"above": "gt", "exceeds": "gt", "greater than": "gt", "more than": "gt",
                       "below": "lt", "drops below": "lt", "less than": "lt", "under": "lt"}
    severity_words = {"critical": "CRITICAL", "major": "MAJOR", "minor": "MINOR",
                      "warning": "WARNING", "warn": "WARNING", "indeterminate": "INDETERMINATE"}

    if any(w in msg_lower for w in create_words) and not any(w in msg_lower for w in ["delete","remove","change","update","modify"]):
        # Extract threshold number
        nums = _re2.findall(r'\d+\.?\d*', user_message)
        threshold = float(nums[0]) if nums else None

        # Extract severity
        severity = "WARNING"
        for sw, sv in severity_words.items():
            if sw in msg_lower:
                severity = sv
                break

        # Extract condition
        condition = "gt"
        for cw, cv in condition_words.items():
            if cw in msg_lower:
                condition = cv
                break

        # Extract device name from message
        device_name = None
        for d in devices:
            if d["name"].lower() in msg_lower:
                device_name = d["name"]
                break

        # Extract key — look for telemetry keys in message
        # Get all actual keys from existing rules + devices
        all_known_keys = list(set(r.key for r in existing_rules))
        # Also try to find key from message words (word before "alarm" or "rule")
        key_match = _re2.search(r'([a-z_]+)\s+(?:alarm|rule|threshold|sensor)', msg_lower)
        extracted_key = None
        if key_match and key_match.group(1) not in ["a","an","the","this","that","set","create","add","new","high","low","critical","warning","major","minor"]:
            extracted_key = key_match.group(1)
        # Check if extracted key matches a known rule key
        if not extracted_key:
            for k in all_known_keys:
                if k in msg_lower:
                    extracted_key = k
                    break

        if extracted_key and threshold is not None:
            # Make sure we don't use device name as key
            if device_name and extracted_key.lower() == device_name.lower():
                extracted_key = None  # ambiguous, let Groq handle
            else:
                alarm_type = f"High {extracted_key.replace('_',' ').title()}" if condition == "gt" else f"Low {extracted_key.replace('_',' ').title()}"
                return {
                    "action": "create",
                    "device_name": device_name,
                    "key": extracted_key,
                    "condition": condition,
                    "threshold": threshold,
                    "severity": severity,
                    "alarm_type": alarm_type,
                }

    # Fast path for UPDATE — match key from existing rules, extract number
    if any(w in msg_lower for w in ["change", "update", "modify", "change the", "update the"]):
        import re as _re3
        for rule in existing_rules:
            if rule.key.lower() in msg_lower:
                nums = _re3.findall(r'[0-9]+\.?[0-9]*', user_message)
                if nums:
                    dn = next((d["name"] for d in devices if d["name"].lower() in msg_lower), None)
                    return {
                        "action": "update",
                        "key": rule.key,
                        "device_name": dn,
                        "threshold": float(nums[0]),
                        "condition": rule.condition,
                        "severity": rule.severity.value if hasattr(rule.severity, "value") else str(rule.severity),
                    }

    # Fast path for DELETE — no Groq, parse key from message directly
    if any(w in msg_lower for w in ["delete", "remove", "clear"]):
        if any(w in msg_lower for w in ["all rules", "rules chain", "rule chain"]):
            return {"action": "delete", "delete_all": True}
        for k in ["temperature","humidity","distance","pressure","glucose","motion"]:
            if k in msg_lower:
                dn = next((d["name"] for d in devices if d["name"].lower() in msg_lower), None)
                return {"action": "delete", "key": k, "device_name": dn}

    device_names = [{"name": d["name"], "id": d["id"]} for d in devices]
    rules_summary = [
        {
            "id": str(r.id),
            "device": next((d["name"] for d in devices if str(d["id"]) == str(r.device_id)), "tenant-wide"),
            "key": r.key,
            "condition": r.condition,
            "threshold": r.threshold,
            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
            "alarm_type": r.alarm_type,
        }
        for r in existing_rules
    ]

    parse_prompt = f"""You are an IoT alarm rule parser. Extract the rule action from the user message.

Available devices: {json.dumps(device_names)}
Existing rules: {json.dumps(rules_summary)}

User message: "{user_message}"

CRITICAL RULES:
1. The "key" field must be the EXACT telemetry key name mentioned by the user
   - "distance alarm" → key = "distance"
   - "temperature alarm" → key = "temperature"
   - "humidity alarm" → key = "humidity"
   - NEVER substitute one key for another
2. NEVER include "rule_id" in your response — the system will find the rule by key name
3. For device_name — use EXACT name from the available devices list, or null for all devices

Valid conditions: gt (greater than), lt (less than), gte (>=), lte (<=), eq (equals)
Valid severities: CRITICAL, MAJOR, MINOR, WARNING, INDETERMINATE

Respond ONLY with valid JSON or null. No explanation, no markdown.

For CREATE:
{{"action": "create", "device_name": "<exact name or null>", "key": "<telemetry key>", "condition": "<gt|lt|gte|lte|eq>", "threshold": <number>, "severity": "<severity>", "alarm_type": "<descriptive name>"}}

For UPDATE:
{{"action": "update", "key": "<telemetry key>", "device_name": "<exact name or null>", "threshold": <number>, "condition": "<condition>", "severity": "<severity>"}}

For DELETE ONE:
{{"action": "delete", "key": "<telemetry key>", "device_name": "<exact name or null>"}}

For DELETE ALL:
{{"action": "delete", "delete_all": true}}

Examples:
- "set distance alarm on Temperature above 410 warning" → {{"action":"create","device_name":"Temperature","key":"distance","condition":"gt","threshold":410,"severity":"WARNING","alarm_type":"High Distance"}}
- "create critical alarm when temperature exceeds 80" → {{"action":"create","device_name":null,"key":"temperature","condition":"gt","threshold":80,"severity":"CRITICAL","alarm_type":"High Temperature"}}
- "change the humidity rule to 75" → {{"action":"update","key":"humidity","device_name":null,"threshold":75,"condition":"gt","severity":"WARNING"}}
- "update temperature alarm on Temperature device to 85 critical" → {{"action":"update","key":"temperature","device_name":"Temperature","threshold":85,"condition":"gt","severity":"CRITICAL"}}
- "delete the distance rule on Temperature" → {{"action":"delete","key":"distance","device_name":"Temperature"}}
- "delete the humidity rule" → {{"action":"delete","key":"humidity","device_name":null}}
- "delete all rules chain" → {{"action":"delete","delete_all":true}}
- If unclear → null"""

    try:
        result = await _call_groq(api_key, [{"role": "user", "content": parse_prompt}], max_tokens=200, temperature=0.1)
        result = result.strip()
        if result.lower() == "null" or not result.startswith("{"):
            return None
        parsed = json.loads(result)
        if "action" in parsed:
            return parsed
    except Exception as exc:
        logger.debug("rule intent parse failed: %s", exc)
    return None


async def _execute_rule_from_chat(
    db: Session,
    current_user,
    devices: list,
    intent: dict,
) -> Optional[dict]:
    """
    Execute a rule create/update/delete based on parsed intent.
    Returns result dict or None on failure.
    """
    from app.models.models import ThresholdRule, AlarmSeverity as AS
    from app.services.audit import audit

    action = intent.get("action")

    try:
        if action == "create":
            # Find device_id from name
            device_id = None
            if intent.get("device_name"):
                matched = next(
                    (d for d in devices if d["name"].lower() == intent["device_name"].lower()),
                    None
                )
                if not matched:
                    matched = next(
                        (d for d in devices if intent["device_name"].lower() in d["name"].lower()),
                        None
                    )
                if matched:
                    device_id = matched["id"]

            severity_str = intent.get("severity", "WARNING").upper()
            try:
                severity = AS[severity_str]
            except KeyError:
                severity = AS.WARNING

            rule = ThresholdRule(
                tenant_id  = current_user.tenant_id,
                device_id  = device_id,
                key        = intent.get("key", "value"),
                condition  = intent.get("condition", "gt"),
                threshold  = float(intent.get("threshold", 0)),
                severity   = severity,
                alarm_type = intent.get("alarm_type", f"{intent.get('key','value')} alarm"),
                is_active  = True,
            )
            db.add(rule)
            db.commit()
            db.refresh(rule)
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="rule.create", resource="threshold_rule", resource_id=str(rule.id),
                  detail={"key": rule.key, "threshold": rule.threshold, "source": "chat"}, commit=True)

            device_name = intent.get("device_name") or "all devices"
            return {
                "action":      "created",
                "rule_id":     str(rule.id),
                "device":      device_name,
                "key":         rule.key,
                "condition":   rule.condition,
                "threshold":   rule.threshold,
                "severity":    severity_str,
                "alarm_type":  rule.alarm_type,
            }

        elif action == "update":
            # Always find by key + device name — never trust Groq's rule_id
            key = intent.get("key")
            device_name = intent.get("device_name")
            rule = None
            if key:
                q = db.query(ThresholdRule).filter(
                    ThresholdRule.tenant_id == current_user.tenant_id,
                    ThresholdRule.key == key,
                    ThresholdRule.is_active == True,
                )
                if device_name:
                    matched_dev = next((d for d in devices if d["name"].lower() == device_name.lower()), None)
                    if matched_dev:
                        q = q.filter(ThresholdRule.device_id == matched_dev["id"])
                rule = q.first()
            if not rule:
                return None

            if "threshold" in intent:  rule.threshold  = float(intent["threshold"])
            if "condition" in intent:  rule.condition  = intent["condition"]
            if "severity"  in intent:
                try:    rule.severity = AS[intent["severity"].upper()]
                except: pass
            if "alarm_type" in intent: rule.alarm_type = intent["alarm_type"]
            if "key" in intent:        rule.key        = intent["key"]

            db.commit()
            audit(db, tenant_id=current_user.tenant_id, user=current_user,
                  action="rule.update", resource="threshold_rule", resource_id=str(rule.id),
                  detail={"source": "chat"}, commit=True)

            return {
                "action":    "updated",
                "rule_id":   str(rule.id),
                "key":       rule.key,
                "threshold": rule.threshold,
                "severity":  rule.severity.value if hasattr(rule.severity, "value") else str(rule.severity),
            }

        elif action == "delete":
            rule_id = intent.get("rule_id")
            delete_all = intent.get("delete_all", False)

            if delete_all:
                # Bulk delete all rules for this tenant
                rules = db.query(ThresholdRule).filter(
                    ThresholdRule.tenant_id == current_user.tenant_id,
                ).all()
                count = len(rules)
                for r in rules:
                    audit(db, tenant_id=current_user.tenant_id, user=current_user,
                          action="rule.delete", resource="threshold_rule", resource_id=str(r.id),
                          detail={"source": "chat_bulk"})
                    db.delete(r)
                db.commit()
                return {"action": "deleted_all", "count": count}

            else:
                key = intent.get("key")
                device_name = intent.get("device_name")
                rule = None
                if key:
                    q = db.query(ThresholdRule).filter(
                        ThresholdRule.tenant_id == current_user.tenant_id,
                        ThresholdRule.key == key,
                        ThresholdRule.is_active == True,
                    )
                    if device_name:
                        matched_dev = next((d for d in devices if d["name"].lower() == device_name.lower()), None)
                        if matched_dev:
                            q = q.filter(ThresholdRule.device_id == matched_dev["id"])
                    rule = q.first()
                if not rule:
                    return None
                rule_key = rule.key
                audit(db, tenant_id=current_user.tenant_id, user=current_user,
                      action="rule.delete", resource="threshold_rule", resource_id=str(rule.id),
                      detail={"key": rule_key, "source": "chat"})
                db.delete(rule)
                db.commit()
                return {"action": "deleted", "key": rule_key}

    except Exception as exc:
        logger.error("rule execution failed: %s", exc)
        db.rollback()

    return None


@router.post("/chat")
async def ai_chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    TAAT v2 — Intent Router → Tool Executor → Safety Guard → Groq Reply

    Flow:
        1. Classify intent (1 fast Groq call, 8b model)
        2. Build context via tool calls (data_service, fully cached)
        3. For write intents: extract action + check safety guard
        4. Execute if allowed and not HIGH-risk (or confirmed)
        5. Build system prompt with real tool results
        6. One Groq call for the final reply

    All existing actions (RPC, alarms, rules, users) are preserved.
    CUSTOMER_USER now gets read-only TAAT (was fully blocked before).
    """
    # ── TAAT imports (inline to avoid circular import at startup) ───────────
    from app.services.taat_planner import (
        classify_intent, build_context, build_system_prompt,
        check_permission, get_action_risk, extract_action,
        CUSTOMER_ALLOWED_INTENTS,
    )
    from app.services.taat_agent_planner import make_plan
    from app.services.taat_executor     import execute as run_plan
    from app.services.taat_verification import verify_rpc, verify_rule_created, verify_actions
    from app.services.taat_decision_engine import build_decision, summarize_decision, build_failure_decision
    from app.services.taat_memory_service import (
        record_action_outcome, record_incident, get_relevant_memories, format_for_prompt
    )

    api_key = os.getenv("GROQ_API_KEY")

    # ── Gather tenant + devices ───────────────────────────────────────────────
    from app.models.models import Tenant as _TenantModel
    tenant      = db.query(_TenantModel).filter(_TenantModel.id == current_user.tenant_id).first()
    tenant_name = tenant.name if tenant else "TriAxis Nexus"

    raw_devices = _scoped_devices(current_user, db).limit(50).all()
    devices = [
        {
            "id":           str(d.id),
            "name":         d.name,
            "type":         d.device_type,
            "status":       d.status.value if hasattr(d.status, "value") else str(d.status),
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "label":        getattr(d, "label", None),        # e.g. "Building A"
            "latitude":     getattr(d, "latitude", None),
            "longitude":    getattr(d, "longitude", None),
            "description":  getattr(d, "description", None),
        }
        for d in raw_devices
    ]

    last_user_msg = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), ""
    )

    # ── Rule-based fallback (no API key) ─────────────────────────────────────
    if not api_key:
        from app.services.taat_tools import tool_get_active_alarms
        alarm_count = sum(
            tool_get_active_alarms(db, d["id"]).get("count", 0)
            for d in devices[:5]
        )
        return {
            "reply": (
                f"**{len(devices)} device(s)** connected. "
                f"**{alarm_count} active alarm(s)**. "
                "Add GROQ_API_KEY to Render to enable full AI capabilities."
            ),
            "engine": "rule-based",
        }

    # ── Rate limit ────────────────────────────────────────────────────────────
    rate_info = _check_groq_rate_limit(db, str(current_user.id), getattr(current_user, "email", ""))

    # ── Confirm mode ──────────────────────────────────────────────────────────
    confirm_mode   = False
    pending_action = body.pending_confirm if hasattr(body, "pending_confirm") else None
    if pending_action and last_user_msg.lower().strip() in ("proceed", "confirm", "yes", "ok", "do it", "proceed."):
        confirm_mode = True

    # ── Step 1: Classify intent ───────────────────────────────────────────────
    if confirm_mode and pending_action:
        intent = pending_action.get("intent", "QUESTION")
        logger.info("taat_agent confirm_mode intent=%s", intent)
    else:
        try:
            intent = await classify_intent(api_key, last_user_msg, _call_groq)
        except Exception as exc:
            logger.warning("intent classification failed: %s", exc)
            intent = "QUESTION"

    # ── Step 2: Build context ─────────────────────────────────────────────────
    device_id_str = str(body.device_id) if body.device_id else None
    try:
        ctx = build_context(db, current_user, devices, intent, device_id_str, last_user_msg)
        try:
            from app.services.taat_context_compressor import compress_context
            ctx = compress_context(ctx, intent=intent)
        except Exception as _ce:
            logger.warning("compress_context failed (using raw ctx): %s", _ce)
    except Exception as exc:
        logger.error("build_context failed: %s", exc)
        ctx = {"intent": intent, "device_list": devices,
               "active_alarms": [], "memory": {"count": 0, "memories": []}}

    # ── Step 3: Permission check ──────────────────────────────────────────────
    action           = None
    trace            = None
    confirm_required = None
    risk_level       = "LOW"
    action_result    = None
    verification     = None

    write_intents = {"DEVICE_CONTROL", "ALARM", "RULE", "USER", "SCHEDULE", "REMEMBER"}

    if intent in write_intents:
        allowed, deny_reason = check_permission(intent, {}, current_user, last_user_msg)
        if not allowed:
            return {
                "reply":   f"⛔ {deny_reason}",
                "engine":  f"groq/{GROQ_MODEL_FAST}",
                "intent":  intent,
                "blocked": True,
            }

        # ── Step 4: Extract action ────────────────────────────────────────────
        if confirm_mode and pending_action:
            action = pending_action.get("action")
        else:
            try:
                action = await extract_action(api_key, intent, last_user_msg, ctx, _call_groq)
            except Exception as exc:
                logger.debug("action extraction failed: %s", exc)

        if action:
            # ── Step 4b: Inject memory before planning ────────────────────────
            focus_dev_pre = action.get("device_name")
            focus_key_pre = next(iter(action.get("params", {}).keys()), None) if action.get("params") else None
            ctx["memory"] = {
                "count": 0, "memories":
                get_relevant_memories(
                    db, current_user.tenant_id,
                    device_name=focus_dev_pre,
                    key=focus_key_pre,
                )
            }

            # ── Step 5: Build plan ────────────────────────────────────────────
            plan = make_plan(
                intent    = intent,
                ctx       = ctx,
                action    = action,
                message   = last_user_msg,
                device_id = device_id_str,
            )
            risk_level = plan.risk

            # HIGH risk → confirm card (unless already confirmed)
            if risk_level == "HIGH" and not confirm_mode:
                confirm_required = {"intent": intent, "action": action, "risk": "HIGH"}
                ctx["pending_confirmation"] = confirm_required

            else:
                # ── Step 6: Execute plan ──────────────────────────────────────
                try:
                    trace = await run_plan(
                        plan         = plan,
                        db           = db,
                        current_user = current_user,
                        extra_kwargs = {"devices": devices, "api_key": api_key},
                    )
                    action_result = trace.to_chip_data()

                    # ── Step 7: Plan-aware verification (background) ──────
                    # Run verification in background — don't block the HTTP response.
                    # Verification result is pushed via WebSocket when complete.
                    needs_verify = any(getattr(s, "requires_verification", False) for s in plan.steps)
                    if needs_verify:
                        import asyncio as _asyncio
                        from app.core.websocket_manager import manager as _ws_manager

                        async def _bg_verify(plan=plan, trace=trace, device_id=device_id_str):
                            try:
                                ver = await verify_actions(plan, trace, db)
                                if device_id:
                                    await _ws_manager.broadcast_json(str(device_id), {
                                        "type":         "verification",
                                        "trace_id":     trace.trace_id,
                                        "verification": ver,
                                    })
                                logger.info("bg_verify done trace_id=%s overall=%s",
                                            trace.trace_id, ver.get("overall"))
                            except Exception as _ve:
                                logger.warning("bg_verify failed trace_id=%s: %s", trace.trace_id, _ve)

                        _asyncio.ensure_future(_bg_verify())
                        verification = {"overall": "pending", "verified": False,
                                        "steps": {}, "message": "Verification running in background"}
                        ctx["verification"] = verification
                    else:
                        try:
                            verification = await verify_actions(plan, trace, db)
                            ctx["verification"] = verification
                        except Exception as exc:
                            logger.debug("verify_actions skipped: %s", exc)
                            verification = {"overall": "skipped", "verified": True,
                                            "steps": {}, "message": ""}

                    # ── Step 7b: Decision engine ─────────────────────────────
                    if trace.errors:
                        decision = build_failure_decision(trace)
                    else:
                        decision = build_decision(
                            intent       = intent,
                            plan         = plan,
                            trace        = trace,
                            verification = verification or {},
                        )
                    ctx["decision"] = decision

                    # ── Step 8: Memory ────────────────────────────────────────
                    if action_result:
                        success = action_result.get("success", trace.all_success)
                        dev_name = action.get("device_name", "device")
                        ver_msg  = verification.get("message", "") if isinstance(verification, dict) else ""
                        record_action_outcome(
                            db        = db,
                            tenant_id = current_user.tenant_id,
                            plan      = plan,
                            decision  = decision,
                            user_id   = current_user.id,
                        )

                    # ── Successful action pattern learning ───────────────
                    # Store ONLY when all conditions met — exact spec:
                    #   risk=HIGH, verified=True, action_taken set,
                    #   confidence>=0.85, not a failure
                    if (
                        decision.get("risk") == "HIGH"
                        and decision.get("verified") is True
                        and decision.get("action_taken")
                        and decision.get("confidence", 0) >= 0.85
                        and not decision.get("failure")
                    ):
                        try:
                            import json as _json
                            from app.services.taat_memory_service import save_memory
                            from app.services.taat_policy import allows_auto_execute

                            dev_name = action.get("device_name", "")
                            prms     = action.get("params", {})
                            key_name = next(iter(prms.keys()), "") if prms else ""

                            pattern_content = _json.dumps({
                                "plan_intent":  plan.intent,
                                "plan_steps":   [s.tool for s in plan.steps],
                                "action_taken": decision["action_taken"],
                                "confidence":   decision["confidence"],
                                "note": (
                                    "Successful high-risk remediation pattern. "
                                    "Suggest next time, but do not auto-execute "
                                    "without policy approval."
                                ),
                            }, default=str)

                            save_memory(
                                db,
                                tenant_id   = current_user.tenant_id,
                                memory_type = "successful_action_pattern",
                                content     = pattern_content,
                                user_id     = current_user.id,
                            )
                            logger.info(
                                "action_pattern stored device=%s key=%s confidence=%.2f",
                                dev_name, key_name, decision["confidence"],
                            )

                            # Policy gate — recommend or approve auto-execute
                            if allows_auto_execute(
                                db        = db,
                                tenant_id = current_user.tenant_id,
                                device_id = device_id_str,
                                key       = key_name,
                                params    = prms,
                            ):
                                ctx["policy_auto_execute"] = True
                                logger.info("policy: auto_execute approved %s %s", dev_name, prms)
                            else:
                                ctx["policy_auto_execute"] = False
                                logger.info("policy: recommend-only %s %s", dev_name, prms)

                        except Exception as exc:
                            logger.debug("action_pattern save skipped: %s", exc)

                except Exception as exc:
                    logger.error("plan execution failed: %s", exc)
                    db.rollback()

        # ── SCHEDULE intent — direct execution (bypass planner) ───────────────
        elif intent == "SCHEDULE":
            try:
                action = await extract_action(api_key, intent, last_user_msg, ctx, _call_groq) if not action else action
                if action:
                    from app.services.scheduled_rpc_service import (
                        schedule_by_device_name, cancel_scheduled, list_scheduled,
                        parse_schedule_time,
                    )
                    sched_action = action.get("action", "schedule")
                    if sched_action == "list":
                        action_result = list_scheduled(db, current_user)
                    elif sched_action == "cancel":
                        action_result = cancel_scheduled(db, device_id=None, current_user=current_user)
                    elif sched_action == "schedule" and action.get("device_name"):
                        from zoneinfo import ZoneInfo
                        MYT = ZoneInfo("Asia/Kuala_Lumpur")
                        scheduled_for = parse_schedule_time(
                            action.get("time_str", "in 1 hour"),
                            action.get("repeat_hours"),
                        )
                        # Ensure timezone-aware UTC datetime
                        if scheduled_for.tzinfo is None:
                            scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
                        # Convert ONLY for human display
                        scheduled_for_local = scheduled_for.astimezone(MYT)
                        display_time = scheduled_for_local.strftime("%Y-%m-%d %H:%M MYT")
                        action_result = await schedule_by_device_name(
                            db,
                            devices               = raw_devices,
                            device_name           = action["device_name"],
                            method                = action.get("method", "set"),
                            params                = action.get("params", {}),
                            scheduled_for         = scheduled_for,
                            repeat_interval_hours = action.get("repeat_hours"),
                            current_user          = current_user,
                            source                = "taat_agent",
                        )
                        # Human-readable schedule time
                        if isinstance(action_result, dict):
                            action_result["scheduled_display"] = display_time
            except Exception as exc:
                logger.error("schedule execution failed: %s", exc)
                db.rollback()

        # ── USER intent — direct execution ────────────────────────────────────
        elif intent == "USER" and current_user.role == "TENANT_ADMIN":
            if action:
                try:
                    from app.models.models import User as UserModel
                    existing_users = db.query(UserModel).filter(
                        UserModel.tenant_id == current_user.tenant_id
                    ).all()
                    action_result = await _execute_user_from_chat(
                        db, current_user, action, existing_users
                    )
                except Exception as exc:
                    logger.error("user action failed: %s", exc)

        # ── REMEMBER intent — save semantic/location/preference memory ──────
        elif intent == "REMEMBER":
            try:
                from app.services.taat_memory_service import save_semantic_memory
                # Extract the fact to remember — strip common prefixes
                import re as _re
                fact = last_user_msg
                for prefix in ["remember that", "remember:", "note that", "save that", "please remember"]:
                    fact = _re.sub(rf"^{prefix}\s*", "", fact, flags=_re.IGNORECASE).strip()
                ok = save_semantic_memory(db, current_user.tenant_id, fact, user_id=current_user.id)
                action_result = {
                    "success":  ok,
                    "remembered": fact,
                    "type":     "semantic",
                }
                logger.info("semantic.saved tenant=%s content=%s", current_user.tenant_id, fact[:80])
            except Exception as exc:
                logger.error("remember failed: %s", exc)

    # ── READ intents: run through planner for rich parallel data ─────────────
    # QUESTION, RCA, RECOMMEND, FLEET all need the planner + executor to
    # fetch per-device data before the system prompt is built.
    elif intent in ("QUESTION", "RCA", "RECOMMEND", "FLEET"):
        try:
            plan = make_plan(
                intent    = intent,
                ctx       = ctx,
                action    = None,
                message   = last_user_msg,
                device_id = device_id_str,
            )
            trace = await run_plan(
                plan         = plan,
                db           = db,
                current_user = current_user,
                extra_kwargs = {"devices": devices, "api_key": api_key},
            )
            # Merge trace results into ctx so build_system_prompt sees them
            for key, val in trace.results.items():
                if key not in ctx:
                    ctx[key] = val
            logger.info(
                "read_plan trace_id=%s intent=%s steps=%d",
                trace.trace_id, intent, len(trace.steps),
            )
        except Exception as exc:
            logger.error("read plan execution failed intent=%s: %s", intent, exc)

    # ── Step 9: Build system prompt ───────────────────────────────────────────
    # Inject relevant memory for current device/key
    focus_device_name = action.get("device_name") if action else None
    focus_key         = next(iter((action.get("params") or {}).keys()), None) if action else None
    relevant_memories = get_relevant_memories(
        db, current_user.tenant_id,
        device_name = focus_device_name,
        key         = focus_key,
    )
    ctx["memory"] = {"count": len(relevant_memories), "memories": relevant_memories}

    # Inject trace results + decision into context
    if trace:
        for key, val in trace.results.items():
            if key not in ctx:
                ctx[key] = val
    # Build decision even for read-only flows (QUESTION, RCA, RECOMMEND)
    if trace and not ctx.get("decision"):
        ctx["decision"] = build_decision(
            intent       = intent,
            plan         = plan if "plan" in dir() else type("P", (), {"steps":[], "risk":"LOW", "intent":intent})(),
            trace        = trace,
            verification = verification if isinstance(verification, dict) else {},
        )

    # Inject verification result (verification is always a dict from verify_actions)
    if verification and isinstance(verification, dict) and verification.get("message"):
        ctx.setdefault("verification", {})["message"] = verification["message"]

    # Decision engine summary — LLM narrates this, never determines it
    if ctx.get("decision"):
        ctx["decision_summary"] = summarize_decision(ctx["decision"])
    system_prompt = build_system_prompt(
        tenant_name  = tenant_name,
        intent       = intent,
        ctx          = ctx,
        current_user = current_user,
        confirm_mode = confirm_mode,
    )

    # ── Step 10: Build Groq messages ──────────────────────────────────────────
    chat_messages = [{"role": "system", "content": system_prompt}]

    if action_result:
        outcome   = "✅ SUCCESS" if action_result.get("success", True) else "⚠️ FAILED"
        ver_result = ctx.get("verification", {})
        ver_msg   = ver_result.get("message", "") if isinstance(ver_result, dict) else ""
        ver_overall = ver_result.get("overall", "skipped") if isinstance(ver_result, dict) else "skipped"

        # Build a structured update the LLM MUST include in its reply
        dev_name = action_result.get("device_name", "device")
        params   = action_result.get("params", {})

        # Scheduled action response override
        if (
            intent == "REMEMBER"
            and isinstance(action_result, dict)
            and action_result.get("success")
        ):
            fact = action_result.get("remembered", "")
            update_instruction = (
                f"Tell the user EXACTLY: ✅ Saved permanently: \"{fact}\". "
                f"I will use this in all future conversations."
            )
        elif (
            intent == "SCHEDULE"
            and isinstance(action_result, dict)
            and action_result.get("is_scheduled")
        ):
            # Use scheduled_display (MYT) if available, else human_label, else raw scheduled_for
            disp = (
                action_result.get("scheduled_display")
                or action_result.get("human_label")
                or action_result.get("scheduled_for", "")
            )
            dev  = action_result.get("device_name", "device")
            prms = action_result.get("params", {})
            update_instruction = (
                f"Output EXACTLY this line, no changes: "
                f"⏰ Scheduled → {dev}: {prms} at {disp}"
            )
        elif ver_overall == "success" and ver_msg:
            update_instruction = (
                f"Tell the user: Command executed on {dev_name}: {params}. {ver_msg}. "
                f"Device confirmed the change. Be brief and direct."
            )
        elif ver_overall == "failed" and ver_msg:
            update_instruction = (
                f"Tell the user: Command was sent to {dev_name}: {params}, but {ver_msg}. "
                f"The device may be offline or firmware did not respond. Be direct."
            )
        else:
            update_instruction = (
                f"Tell the user: Command sent to {dev_name}: {params}. "
                f"Waiting for device to respond (verification pending or skipped). Be brief."
            )

        chat_messages.append({
            "role":    "system",
            "content": f"[ACTION {outcome}] {update_instruction}",
        })
    if confirm_required:
        chat_messages.append({
            "role":    "system",
            "content": (
                f"[HIGH RISK ACTION PENDING] Intent: {intent}. Action: {json.dumps(action)}. "
                "Explain clearly what will happen and ask the user to reply 'proceed' to confirm."
            ),
        })

    for msg in body.messages:
        chat_messages.append({"role": msg.role, "content": msg.content})

    # ── Step 11: Groq reply ───────────────────────────────────────────────────
    try:
        reply = await _call_groq(api_key, chat_messages, max_tokens=700, temperature=0.4)

        return {
            "reply":            reply,
            "engine":           f"groq/{GROQ_MODEL_FAST}",
            "intent":           intent,
            "risk":             risk_level,
            "action_result":    action_result,
            "confirm_required": confirm_required,
            "verification":     ctx.get("verification"),
            "decision":         ctx.get("decision"),
            # Legacy chip fields
            "rpc_executed":     action_result if intent in ("DEVICE_CONTROL", "SCHEDULE") and action_result else None,
            "alarm_actioned":   action_result if intent == "ALARM"  and action_result else None,
            "rule_actioned":    action_result if intent == "RULE"   and action_result else None,
            "user_actioned":    action_result if intent == "USER"   and action_result else None,
            "rate":             rate_info,
            "trace_id":         trace.trace_id if trace else None,
        }

    except Exception as exc:
        import traceback
        logger.error("taat_agent chat.failed: %s\n%s", exc, traceback.format_exc())
        return {
            "reply":  f"Sorry, I'm having trouble connecting right now. Error: {str(exc)[:120]}",
            "engine": "error",
        }



# ── (duplicate /chat removed — ai_chat above is the canonical endpoint) ───────

async def _chat_unused(
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    AI Chatbot endpoint.
    Accepts: {messages: [{role, content}], context: {devices, alarms}}
    Returns: {reply: str, engine: str}
    """
    messages  = body.get("messages", [])
    user_msg  = messages[-1]["content"] if messages else ""

    # ── Gather platform context ───────────────────────────────────────────────
    from app.models.models import LatestTelemetry
    devices = db.query(Device).filter(
        Device.tenant_id == current_user.tenant_id
    ).limit(20).all()

    active_alarms = db.query(Alarm).filter(
        Alarm.device_id.in_([d.id for d in devices]),
        Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),
    ).limit(20).all()

    # Latest telemetry per device
    telemetry_ctx = {}
    for device in devices[:5]:
        rows = db.query(LatestTelemetry).filter(
            LatestTelemetry.device_id == device.id
        ).limit(10).all()
        if rows:
            telemetry_ctx[device.name] = {
                r.key: float(r.value_num) if r.value_num is not None else r.value_str
                for r in rows
            }

    system_prompt = f"""You are an IoT platform intelligence assistant for TriAxis Nexus.
You have access to the following real-time platform data:

DEVICES ({len(devices)} total):
{chr(10).join(f"- {d.name} ({d.device_type}) — {d.status.value if hasattr(d.status,'value') else d.status}" for d in devices)}

ACTIVE ALARMS ({len(active_alarms)} total):
{chr(10).join(f"- {a.alarm_type} on device {a.device_id} — {a.severity.value if hasattr(a.severity,'value') else a.severity}" for a in active_alarms[:10]) or "None"}

LATEST TELEMETRY:
{chr(10).join(f"- {dev}: {vals}" for dev, vals in telemetry_ctx.items()) or "No data"}

DEVICE KEYS (actual controllable/readable keys per device):
{chr(10).join(f"- {name}: {json.dumps(keys)}" for name, keys in (_get_device_keys(db, device_list) if devices else {}).items()) or "No keys known yet — send telemetry first"}

Answer questions about devices, alarms, telemetry, and platform health.
You can execute RPC commands, acknowledge alarms, and manage rules — but ONLY confirm actions that appear in a [SYSTEM: already executed] message. Never fabricate or assume an action was taken. If no action was executed, say so honestly.
Be concise, technical, and helpful. If asked about something not in your context, say so clearly.
Format responses clearly — use bullet points for lists, be direct."""

    # Build message history for Groq
    groq_messages = [{"role": "system", "content": system_prompt}]
    for m in messages[-10:]:  # last 10 messages for context
        groq_messages.append({"role": m["role"], "content": m["content"]})

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "reply": "AI chatbot requires GROQ_API_KEY. Add it to your Render environment variables (free at console.groq.com).",
            "engine": "none",
        }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL_FAST,
                    "messages": groq_messages,
                    "max_tokens": 512,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]

        return {"reply": reply, "engine": f"groq/{GROQ_MODEL_FAST}"}

    except Exception as exc:
        logger.error("chat.failed error=%s", exc)
        return {
            "reply": f"Sorry, I encountered an error: {str(exc)}",
            "engine": "error",
        }


# ── Phase 7: Anomaly Detection ────────────────────────────────────────────────

@router.get("/anomalies/{device_id}")
def get_anomalies(
    device_id: UUID,
    key: Optional[str] = None,
    hours: int = 24,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get anomaly scores for a device.
    Returns anomalies only by default; pass only_anomalies=false for all scores.
    """
    _assert_device(device_id, current_user, db)
    from app.services.anomaly_service import get_anomalies, get_anomaly_summary
    return {
        "device_id": str(device_id),
        "summary":   get_anomaly_summary(db, str(device_id), hours=hours),
        "anomalies": get_anomalies(db, str(device_id), key=key, hours=hours),
    }


# ── Phase 7: Baseline Learning ────────────────────────────────────────────────

@router.get("/baseline/{device_id}")
def get_baseline(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get learned baselines for a device.
    Returns per-key, per-hour-of-day statistics.
    Status = 'learning' until enough data (30 days), then 'active'.
    """
    _assert_device(device_id, current_user, db)
    from app.services.baseline_service import get_baseline_for_device, get_threshold_suggestions
    return {
        "device_id":   str(device_id),
        "baseline":    get_baseline_for_device(db, str(device_id)),
        "suggestions": get_threshold_suggestions(db, str(device_id)),
    }


@router.post("/baseline/{device_id}/refresh")
def refresh_baseline(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Manually trigger baseline recalculation for a device (admin only)."""
    _assert_device(device_id, current_user, db)
    from app.services.baseline_service import update_baselines_for_device
    rows = update_baselines_for_device(db, str(device_id))
    return {"device_id": str(device_id), "baseline_rows_updated": rows}


# ── Phase 7: Health Scoring + Predictive Maintenance ─────────────────────────

@router.get("/health/{device_id}")
def get_device_health(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get current health score and maintenance prediction for a device.
    Includes component breakdown: uptime, alarm, stability, freshness.
    """
    device = _assert_device(device_id, current_user, db)
    from app.services.health_service import get_latest_health, score_device
    health = get_latest_health(db, str(device_id))
    if not health:
        # Score on-demand if no cached score exists
        try:
            s = score_device(db, device)
            db.commit()
            health = get_latest_health(db, str(device_id))
        except Exception as exc:
            logger.error("on-demand health score failed: %s", exc)
    return {
        "device_id":   str(device_id),
        "device_name": device.name,
        "health":      health,
    }


@router.get("/health")
def get_fleet_health(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get health scores for all devices in the tenant.
    Sorted worst-first. Includes maintenance alerts.
    CUSTOMER_USER sees only their customer's devices.
    """
    from app.services.health_service import get_fleet_health as _fleet_health
    all_health = _fleet_health(db, str(current_user.tenant_id))

    # Apply CUSTOMER_USER scoping
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        scoped_ids = {
            str(d.id) for d in
            _scoped_devices(current_user, db).all()
        }
        all_health = [h for h in all_health if h["device_id"] in scoped_ids]

    maintenance_count = sum(1 for h in all_health if h["health"].get("maintenance_due"))
    return {
        "devices":           all_health,
        "total":             len(all_health),
        "maintenance_alerts": maintenance_count,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8 — Intelligence Enhancements
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Alarm control via chat ─────────────────────────────────────────────────

class AlarmActionRequest(BaseModel):
    action: str        # "ack" | "clear" | "ack_all" | "clear_all"
    alarm_id: Optional[UUID] = None
    severity_filter: Optional[str] = None   # for bulk ops


@router.post("/alarm-action")
def alarm_action(
    body: AlarmActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Perform alarm actions via chat or direct API.
    Supports single and bulk ack/clear scoped to the current user's tenant.
    """
    from app.models.models import AlarmStatus
    now = datetime.now(timezone.utc)

    # Scope to tenant + customer if needed
    base_q = db.query(Alarm).filter(
        Alarm.device_id.in_(
            [d.id for d in _scoped_devices(current_user, db).all()]
        )
    )

    if body.action == "ack" and body.alarm_id:
        alarm = base_q.filter(Alarm.id == body.alarm_id).first()
        if not alarm:
            raise HTTPException(404, "Alarm not found")
        alarm.status = AlarmStatus.ACTIVE_ACK
        alarm.ack_ts = now
        alarm.ack_by = str(current_user.id)
        db.commit()
        return {"actioned": 1, "action": "ack", "alarm_id": str(body.alarm_id)}

    elif body.action == "clear" and body.alarm_id:
        alarm = base_q.filter(Alarm.id == body.alarm_id).first()
        if not alarm:
            raise HTTPException(404, "Alarm not found")
        alarm.status = AlarmStatus.CLEARED_ACK if alarm.ack_ts else AlarmStatus.CLEARED_UNACK
        alarm.clear_ts = now
        alarm.cleared_by = str(current_user.id)
        db.commit()
        return {"actioned": 1, "action": "clear", "alarm_id": str(body.alarm_id)}

    elif body.action == "ack_all":
        q = base_q.filter(Alarm.status == AlarmStatus.ACTIVE_UNACK)
        if body.severity_filter:
            q = q.filter(Alarm.severity == body.severity_filter)
        alarms = q.all()
        for a in alarms:
            a.status = AlarmStatus.ACTIVE_ACK
            a.ack_ts = now
            a.ack_by = str(current_user.id)
        db.commit()
        return {"actioned": len(alarms), "action": "ack_all"}

    elif body.action == "clear_all":
        q = base_q.filter(Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]))
        if body.severity_filter:
            q = q.filter(Alarm.severity == body.severity_filter)
        alarms = q.all()
        for a in alarms:
            a.status = AlarmStatus.CLEARED_ACK if a.ack_ts else AlarmStatus.CLEARED_UNACK
            a.clear_ts = now
            a.cleared_by = str(current_user.id)
        db.commit()
        return {"actioned": len(alarms), "action": "clear_all"}

    raise HTTPException(400, "Invalid action")


# ── 2. Scheduled RPC (Phase 11 clean rewrite) ────────────────────────────────

class ScheduledRpcRequest(BaseModel):
    device_id:             UUID
    method:                str = "set"
    params:                dict = {}
    scheduled_for:         datetime
    repeat_interval_hours: Optional[float] = None


@router.post("/schedule-rpc")
def schedule_rpc(
    body: ScheduledRpcRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Schedule an RPC command to run at a specific UTC time.
    Optional repeat_interval_hours creates a recurring command.
    Background dispatcher in main.py fires it every 30s check.
    """
    from app.services.scheduled_rpc_service import schedule_command
    cmd = schedule_command(
        db,
        device_id             = body.device_id,
        method                = body.method,
        params                = body.params,
        scheduled_for         = body.scheduled_for,
        repeat_interval_hours = body.repeat_interval_hours,
        current_user          = current_user,
        source                = "dashboard",
    )
    from app.services.scheduled_rpc_service import _humanise_schedule
    return {
        "cmd_id":                str(cmd.id),
        "device_id":             str(cmd.device_id),
        "method":                cmd.method,
        "params":                cmd.params,
        "scheduled_for":         cmd.scheduled_for.isoformat(),
        "repeat_interval_hours": cmd.repeat_interval_hours,
        "human_label":           _humanise_schedule(cmd.scheduled_for, cmd.repeat_interval_hours),
        "is_scheduled":          True,
    }


@router.get("/schedule-rpc")
def list_scheduled_rpc(
    device_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all pending SCHEDULED commands for the tenant."""
    from app.services.scheduled_rpc_service import list_scheduled
    return list_scheduled(db, current_user, device_id=device_id)


@router.delete("/schedule-rpc/{cmd_id}")
def cancel_scheduled_rpc(
    cmd_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancel a specific scheduled command."""
    from app.services.scheduled_rpc_service import cancel_scheduled
    return cancel_scheduled(db, cmd_id=cmd_id, current_user=current_user)


# ── 3. "Why did this alarm fire?" deep RCA ────────────────────────────────────

@router.post("/alarm-explain/{alarm_id}")
async def explain_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Deep explanation of why a specific alarm fired.
    Correlates alarm time with telemetry, trend, baseline deviation, and anomaly scores.
    """
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if not alarm:
        raise HTTPException(404, "Alarm not found")

    device = _assert_device(alarm.device_id, current_user, db)
    api_key = os.getenv("GROQ_API_KEY")

    # Telemetry around alarm time (±10 min)
    t0 = alarm.start_ts or alarm.created_at
    before = t0 - timedelta(minutes=10)
    after  = t0 + timedelta(minutes=10)

    rows = (
        db.query(TelemetryData)
        .filter(
            TelemetryData.device_id == alarm.device_id,
            TelemetryData.ts.between(before, after),
            TelemetryData.value_num.isnot(None),
        )
        .order_by(TelemetryData.ts)
        .limit(100)
        .all()
    )
    telemetry_window = {}
    for r in rows:
        telemetry_window.setdefault(r.key, []).append({
            "ts": r.ts.isoformat(), "value": float(r.value_num)
        })

    # Anomaly scores around alarm time
    from app.services.anomaly_service import get_anomalies
    anomalies = get_anomalies(db, str(alarm.device_id), hours=2, only_anomalies=True)

    # Baseline for the alarm key at that hour
    from app.services.baseline_service import get_baseline_for_device
    baseline = get_baseline_for_device(db, str(alarm.device_id), current_hour=t0.hour)

    context = {
        "alarm": {
            "type":     alarm.alarm_type,
            "severity": alarm.severity.value if hasattr(alarm.severity, "value") else str(alarm.severity),
            "fired_at": t0.isoformat(),
            "details":  alarm.details,
        },
        "device":          {"name": device.name, "type": device.device_type},
        "telemetry_window": telemetry_window,
        "anomalies_nearby": anomalies[:5],
        "baseline":         baseline,
    }

    if not api_key:
        return {"alarm_id": str(alarm_id), "explanation": "Add GROQ_API_KEY for AI explanation.", "context": context}

    prompt = f"""Explain why this IoT alarm fired based on the data below.

{json.dumps(context, indent=2)}

Provide:
1. **Root Cause** — exactly what value/condition triggered it
2. **Lead-up** — what was happening in the 10 minutes before
3. **Anomaly Context** — was this a statistical outlier vs normal baseline?
4. **Likely Cause** — hardware, environment, or configuration issue?
5. **Fix** — one specific action to resolve or prevent recurrence

Be concise and technical. Base everything on the actual data."""

    try:
        explanation = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=600, temperature=0.2, model=GROQ_MODEL_DEEP)
        return {"alarm_id": str(alarm_id), "explanation": explanation, "context": context, "engine": f"groq/{GROQ_MODEL_DEEP}"}
    except Exception as exc:
        return {"alarm_id": str(alarm_id), "explanation": f"AI unavailable: {exc}", "context": context}


# ── 4. Comparative intelligence (this week vs last week) ─────────────────────

@router.get("/compare/{device_id}")
async def compare_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Compare device behaviour this week vs last week.
    Uses telemetry aggregates and alarm counts.
    """
    device = _assert_device(device_id, current_user, db)
    api_key = os.getenv("GROQ_API_KEY")

    now  = datetime.now(timezone.utc)
    w0_start = now - timedelta(days=7)
    w1_start = now - timedelta(days=14)

    def _period_stats(start, end):
        rows = (
            db.query(TelemetryData.key, TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts.between(start, end),
            )
            .all()
        )
        from collections import defaultdict
        import math
        buckets = defaultdict(list)
        for key, val in rows:
            buckets[key].append(val)

        stats = {}
        for key, vals in buckets.items():
            n = len(vals)
            mean = sum(vals) / n if n else 0
            stats[key] = {
                "mean":    round(mean, 3),
                "min":     round(min(vals), 3),
                "max":     round(max(vals), 3),
                "samples": n,
            }
        alarm_count = db.query(Alarm).filter(
            Alarm.device_id == device_id,
            Alarm.created_at.between(start, end),
        ).count()
        return {"telemetry": stats, "alarm_count": alarm_count}

    this_week = _period_stats(w0_start, now)
    last_week = _period_stats(w1_start, w0_start)

    context = {
        "device":    {"name": device.name, "type": device.device_type},
        "this_week": this_week,
        "last_week": last_week,
    }

    if not api_key:
        return {"device_id": str(device_id), "comparison": context, "insight": "Add GROQ_API_KEY for AI insight."}

    prompt = f"""Compare this IoT device's behaviour this week vs last week.

{json.dumps(context, indent=2)}

Provide:
1. **Key Changes** — which metrics changed most significantly (% change)
2. **Alarm Trend** — better or worse than last week?
3. **Anomalies** — anything unusual in this week's data?
4. **Verdict** — is the device improving, degrading, or stable?

Be concise and data-driven."""

    try:
        insight = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=500, temperature=0.2, model=GROQ_MODEL_FAST)
        return {"device_id": str(device_id), "comparison": context, "insight": insight, "engine": f"groq/{GROQ_MODEL_FAST}"}
    except Exception as exc:
        return {"device_id": str(device_id), "comparison": context, "insight": f"AI unavailable: {exc}"}


# ── 5. Daily health report ────────────────────────────────────────────────────

@router.get("/report/daily")
async def daily_report(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Generate a daily health report for all devices in the tenant.
    Summarises alarms, trends, health scores, and top issues.
    """
    api_key = os.getenv("GROQ_API_KEY")
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    devices = _scoped_devices(current_user, db).all()
    from app.services.health_service import get_latest_health

    report_data = []
    for device in devices:
        alarms_24h = db.query(Alarm).filter(
            Alarm.device_id == device.id,
            Alarm.created_at >= since_24h,
        ).count()

        active_alarms = db.query(Alarm).filter(
            Alarm.device_id == device.id,
            Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),
        ).count()

        health = get_latest_health(db, str(device.id))
        trends = get_all_key_trends(db, str(device.id), minutes=60)

        report_data.append({
            "device":        device.name,
            "status":        device.status.value,
            "alarms_24h":    alarms_24h,
            "active_alarms": active_alarms,
            "health_score":  health["health_score"] if health else None,
            "health_label":  health["health_label"] if health else "UNKNOWN",
            "maintenance":   health["maintenance_due"] if health else False,
            "trends":        {k: v["trend"] for k, v in trends.items()},
        })

    # Sort: maintenance first, then by health score
    report_data.sort(key=lambda x: (not x["maintenance"], x["health_score"] or 100))

    total_active_alarms = sum(d["active_alarms"] for d in report_data)
    maintenance_needed  = [d["device"] for d in report_data if d["maintenance"]]
    critical_devices    = [d for d in report_data if d["health_label"] == "CRITICAL"]

    from zoneinfo import ZoneInfo
    MYT = ZoneInfo("Asia/Kuala_Lumpur")
    now_myt = datetime.now(MYT)

    # Add last_seen in MYT to each device
    for i, device in enumerate(devices):
        ls = device.last_seen_at
        if ls:
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            report_data[i]["last_seen"] = ls.astimezone(MYT).strftime("%Y-%m-%d %H:%M MYT")
        else:
            report_data[i]["last_seen"] = "Never"

    summary = {
        "generated_at":       now_myt.strftime("%Y-%m-%d %H:%M MYT"),
        "period":             "Last 24 hours",
        "total_devices":      len(devices),
        "active_alarms":      total_active_alarms,
        "maintenance_needed": maintenance_needed,
        "critical_devices":   len(critical_devices),
        "devices":            report_data,
    }

    if not api_key:
        return {"report": summary, "narrative": "Add GROQ_API_KEY for AI narrative."}

    # Inject recent agent memory into report
    memory_context = ""
    try:
        from app.services.taat_memory_service import get_relevant_memories
        memories = get_relevant_memories(db, current_user.tenant_id, limit=10)
        if memories:
            memory_lines = "\n".join(
                f"  - [{m['type']}] {m['content'][:120]}"
                for m in memories
            )
            memory_context = f"\n\nRECENT AGENT MEMORY (actual actions taken):\n{memory_lines}"
    except Exception:
        pass

    # Build explicit device list for prompt — Groq must use these exact values
    device_lines = "\n".join(
        f"  - {d['device']}: {d['status']}, last seen: {d['last_seen']}, health: {d.get('health_label','UNKNOWN')}"
        for d in report_data
    )

    memory_lines_direct = ""
    try:
        from app.services.taat_memory_service import get_relevant_memories
        mems = get_relevant_memories(db, current_user.tenant_id, limit=10)
        if mems:
            memory_lines_direct = "\n".join(
                f"  - [{m['type']}] {m['content'][:150]}"
                for m in mems
            )
        else:
            memory_lines_direct = "  None"
    except Exception:
        memory_lines_direct = "  None"

    prompt = f"""Write a daily IoT fleet health report. Use ONLY the data provided below — do not invent or rephrase timestamps.

Generated: {now_myt.strftime("%Y-%m-%d %H:%M MYT")}
Period: Last 24 hours

DEVICES:
{device_lines}

ACTIVE ALARMS: {total_active_alarms if total_active_alarms else "None"}

AGENT MEMORY (copy these exactly, do not summarise):
{memory_lines_direct}

Output format — use exactly these section headers:
**DEVICE STATUS**
[list each device with name, status, last seen — use the exact MYT times above]

**ACTIVE ALARMS**
[list or "None"]

**RECENT SCHEDULED ACTIONS EXECUTED**
[from agent memory where type=scheduled_dispatch, or "None"]

**AGENT MEMORY**
[list every memory entry above — do NOT write "available upon request"]

**RECOMMENDATIONS**
[2-3 actionable items based on the data]

RULES:
- Copy timestamps exactly as given — never convert or reformat them
- Show ALL memory entries — never hide them
- Be concise and direct"""

    try:
        narrative = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=600, temperature=0.3, model=GROQ_MODEL_DEEP)
        return {"report": summary, "narrative": narrative, "engine": f"groq/{GROQ_MODEL_DEEP}"}
    except Exception as exc:
        return {"report": summary, "narrative": f"AI unavailable: {exc}"}


# ── Groq usage endpoint ───────────────────────────────────────────────────────

@router.get("/usage")
def get_groq_usage(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get current Groq API usage for the authenticated user.
    Returns requests used, limit, and reset time.
    """
    # Excluded accounts have unlimited access
    if getattr(current_user, "email", "").lower() in GROQ_RATE_LIMIT_EXCLUDED:
        return {
            "used": 0, "limit": 999999, "remaining": 999999,
            "resets_in_mins": 0, "window_hours": GROQ_CHAT_WINDOW_H,
            "pct_used": 0.0, "excluded": True,
        }

    from app.models.models import RateLimit
    from datetime import timedelta

    window_duration = timedelta(hours=GROQ_CHAT_WINDOW_H)
    now             = datetime.now(timezone.utc)
    window_start    = now - window_duration
    token           = f"groq:{current_user.id}"

    row = (
        db.query(RateLimit)
        .filter(
            RateLimit.token == token,
            RateLimit.window_start >= window_start,
        )
        .order_by(RateLimit.window_start.desc())
        .first()
    )

    used = row.request_count if row else 0
    resets_in_mins = GROQ_CHAT_WINDOW_H * 60
    if row:
        resets_at      = row.window_start + window_duration
        resets_in_mins = max(0, int((resets_at - now).total_seconds() / 60))

    return {
        "used":           used,
        "limit":          GROQ_CHAT_LIMIT,
        "remaining":      max(0, GROQ_CHAT_LIMIT - used),
        "resets_in_mins": resets_in_mins,
        "window_hours":   GROQ_CHAT_WINDOW_H,
        "pct_used":       round((used / GROQ_CHAT_LIMIT) * 100, 1),
    }


# ── Unified Intelligence API (Phase 10) ───────────────────────────────────────

@router.get("/unified/{device_id}")
def get_unified_device_intelligence(
    device_id: UUID,
    key:  Optional[str] = None,
    keys: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Unified Intelligence — two modes in one endpoint (Gap 2 / TAAT v2).

    MODE 1 — Device summary (no key params):
        GET /intelligence/unified/{device_id}
        Returns device-level: status, risk, reason, recommendation + all sub-layers.
        Existing callers unchanged — backward compatible.

    MODE 2 — Per-key enrichment (?key= or ?keys=):
        GET /intelligence/unified/{device_id}?key=temperature
        Returns KeyIntelligence for one key:
        { key, value, unit, baseline_min, baseline_max, trend, anomaly,
          risk, status, reason, recommended_action, ts }

        GET /intelligence/unified/{device_id}?keys=temperature,humidity
        Returns enriched_keys: [KeyIntelligence, ...] alongside device summary.

    Both TAAT tools and widgets use this same contract.
    """
    device = _assert_device(device_id, current_user, db)
    device_id_str = str(device_id)

    # ── Mode 2a: single key enrichment ───────────────────────────────────────
    if key:
        return get_key_intelligence(db, device_id_str, key, device=device)

    # ── Base: device-level summary ────────────────────────────────────────────
    result = get_unified_intelligence(db, device_id_str, device=device)

    # ── Mode 2b: add enriched_keys array if ?keys= requested ─────────────────
    if keys:
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        enriched = []
        for k in key_list:
            try:
                enriched.append(get_key_intelligence(db, device_id_str, k, device=device))
            except Exception:
                pass
        result["enriched_keys"] = enriched

    return result


@router.get("/unified/{device_id}/telemetry")
def get_widget_telemetry(
    device_id: UUID,
    key: str,
    hours: int = 24,
    limit: int = 200,
    resolution: str = "raw",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Phase 10 — Widget Telemetry Data (downsampling-ready).

        resolution=raw   → raw rows up to limit
        resolution=5min  → 5-minute AVG buckets
        resolution=1h    → hourly AVG buckets
        resolution=1d    → daily AVG buckets

    Returns min/max alongside avg for bucketed resolutions.
    """
    _assert_device(device_id, current_user, db)
    from app.services.data_service import get_aggregated_telemetry
    return get_aggregated_telemetry(
        db, str(device_id), key,
        hours=hours, limit=limit, resolution=resolution,
    )
