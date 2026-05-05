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
    """Helper: call Groq and return text reply. Defaults to FAST model."""
    use_model = model or GROQ_MODEL_FAST
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": use_model, "max_tokens": max_tokens,
                  "messages": messages, "temperature": temperature},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Groq model strategy ──────────────────────────────────────────────────────
# 8b: high quota (14,400/day) — chat, RPC parsing, summaries, comparisons
# 70b: low quota (1,000/day)  — RCA, alarm explanation, daily report
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
GROQ_MODEL_DEEP = os.getenv("GROQ_MODEL_DEEP", "llama-3.1-8b-instant")

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
        "threshold", "alarm rule", "rule chain", "set alarm",
        "create alarm", "delete rule", "remove rule", "delete all",
        "remove all", "standard deviation", "baseline",
        "i've set", "i have set", "was set", "report", "analysis",
        "acknowledge", "clear alarm",
    ]

    msg_lower = user_message.lower()

    # Bail if exclusion keyword found
    if any(ex in msg_lower for ex in exclusion_keywords):
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
    Find the device by name and queue an RPC command.
    Returns result dict or None on failure.
    """
    from app.models.models import RpcCommand, RpcCommandStatus
    from app.core.websocket_manager import manager as ws_manager

    # Find matching device (case-insensitive)
    matched = next(
        (d for d in devices if d.name.lower() == device_name.lower()), None
    )
    if not matched:
        # Try partial match
        matched = next(
            (d for d in devices if device_name.lower() in d.name.lower()), None
        )
    if not matched:
        return None

    try:
        cmd = RpcCommand(
            device_id  = matched.id,
            method     = "set",
            params     = params,
            status     = RpcCommandStatus.PENDING,
            created_by = str(current_user.id),
        )
        db.add(cmd)
        db.commit()
        db.refresh(cmd)

        # Push via WebSocket immediately
        try:
            await ws_manager.broadcast_json(str(matched.id), {
                "type":   "rpc",
                "cmd_id": str(cmd.id),
                "method": "set",
                "params": params,
            })
        except Exception:
            pass  # WS failure doesn't block — device will poll

        logger.info("chat.rpc sent device=%s params=%s by user=%s", matched.name, params, current_user.id)
        return {
            "device_id":   str(matched.id),
            "device_name": matched.name,
            "cmd_id":      str(cmd.id),
            "params":      params,
        }
    except Exception as exc:
        logger.error("chat.rpc failed: %s", exc)
        db.rollback()
        return None


# ── Rule chain intent detection + execution ───────────────────────────────────

RULE_KEYWORDS = [
    "set alarm", "create alarm", "add alarm", "create rule", "add rule",
    "set rule", "update rule", "change rule", "delete rule", "remove rule",
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

Valid conditions: gt (greater than), lt (less than), gte (>=), lte (<=), eq (equals)
Valid severities: CRITICAL, MAJOR, MINOR, WARNING, INDETERMINATE

Respond ONLY with valid JSON or null. No explanation, no markdown.

For CREATE:
{{"action": "create", "device_name": "<name or null for tenant-wide>", "key": "<telemetry key>", "condition": "<gt|lt|gte|lte|eq>", "threshold": <number>, "severity": "<severity>", "alarm_type": "<descriptive name>"}}

For UPDATE (when user says "change", "update", "modify" an existing rule):
{{"action": "update", "rule_id": "<id from existing rules>", "key": "<key>", "condition": "<condition>", "threshold": <number>, "severity": "<severity>", "alarm_type": "<alarm_type>"}}

For DELETE ONE (when user says "delete", "remove" a specific rule):
{{"action": "delete", "rule_id": "<id from existing rules>"}}

For DELETE ALL (when user says "delete all rules", "remove all rules", "clear all rules", "delete all rules chain"):
{{"action": "delete", "delete_all": true}}

Examples:
- "set distance alarm on Temperature above 410 warning" → {{"action":"create","device_name":"Temperature","key":"distance","condition":"gt","threshold":410,"severity":"WARNING","alarm_type":"High Distance"}}
- "create critical alarm when temperature exceeds 80" → {{"action":"create","device_name":null,"key":"temperature","condition":"gt","threshold":80,"severity":"CRITICAL","alarm_type":"High Temperature"}}
- "change the humidity rule to 75" → {{"action":"update","rule_id":"<matching id>","threshold":75,...}}
- "delete the distance rule" → {{"action":"delete","rule_id":"<matching id>"}}
- "delete all rules chain" → {{"action":"delete","delete_all":true}}
- "remove all rules" → {{"action":"delete","delete_all":true}}
- If unclear or just a question → null"""

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
            rule_id = intent.get("rule_id")
            rule = db.query(ThresholdRule).filter(
                ThresholdRule.id == rule_id,
                ThresholdRule.tenant_id == current_user.tenant_id,
            ).first()
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

            elif rule_id:
                rule = db.query(ThresholdRule).filter(
                    ThresholdRule.id == rule_id,
                    ThresholdRule.tenant_id == current_user.tenant_id,
                ).first()
                if not rule:
                    return None
                key = rule.key
                audit(db, tenant_id=current_user.tenant_id, user=current_user,
                      action="rule.delete", resource="threshold_rule", resource_id=str(rule.id),
                      detail={"source": "chat"})
                db.delete(rule)
                db.commit()
                return {"action": "deleted", "rule_id": str(rule_id), "key": key}

            else:
                return None

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
    AI Chatbot — platform-aware assistant that can:
    - Answer questions about devices, alarms, trends
    - Execute RPC commands directly ("turn on led1 on ESP32-001")
    - Powered by Groq Llama 3.3 70B
    """
    api_key = os.getenv("GROQ_API_KEY")

    # ── Gather platform context ───────────────────────────────────────────────
    from app.models.models import Tenant, LatestTelemetry
    tenant  = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    devices = _scoped_devices(current_user, db).limit(20).all()

    active_alarms = db.query(Alarm).filter(
        Alarm.device_id.in_([d.id for d in devices]),
        Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),
    ).limit(10).all() if devices else []

    # Latest telemetry per device (first 5)
    telemetry_ctx = {}
    for device in devices[:5]:
        rows = db.query(LatestTelemetry).filter(LatestTelemetry.device_id == device.id).limit(10).all()
        if rows:
            telemetry_ctx[device.name] = {
                r.key: float(r.value_num) if r.value_num is not None else r.value_str
                for r in rows
            }

    device_list = [{"name": d.name, "id": str(d.id), "type": d.device_type, "status": d.status.value} for d in devices]
    alarm_list  = [{"type": a.alarm_type, "severity": a.severity.value,
                    "device": next((d.name for d in devices if d.id == a.device_id), "unknown")}
                   for a in active_alarms]

    # Device-specific trend context
    device_context = ""
    if body.device_id:
        try:
            trends = get_all_key_trends(db, str(body.device_id), minutes=30)
            device_context = f"\nFocused device trends: {json.dumps({k: v['trend'] for k, v in trends.items()})}"
        except Exception:
            pass

    # ── Rule-based fallback (no API key) ─────────────────────────────────────
    if not api_key:
        last_msg = body.messages[-1].content.lower() if body.messages else ""
        if any(w in last_msg for w in ["alarm", "alert"]):
            reply = f"There are currently **{len(active_alarms)} active alarm(s)**. Add GROQ_API_KEY to enable full AI chat."
        elif any(w in last_msg for w in ["device", "sensor"]):
            reply = f"You have **{len(devices)} device(s)**. Add GROQ_API_KEY to enable full AI chat."
        else:
            reply = "Add **GROQ_API_KEY** to your Render environment variables (free at console.groq.com) to enable AI chat."
        return {"reply": reply, "engine": "rule-based"}

    # ── Groq rate limit check ─────────────────────────────────────────────────
    rate_info = _check_groq_rate_limit(db, str(current_user.id), getattr(current_user, "email", ""))

    # ── RPC Intent Detection ─────────────────────────────────────────────────
    # Check if user wants to control a device before generating a chat reply
    last_user_msg = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), ""
    )

    rpc_executed    = None
    alarm_actioned  = None
    rule_actioned   = None
    pending_confirm = body.pending_confirm if hasattr(body, "pending_confirm") else None

    if current_user.role != "CUSTOMER_USER" and last_user_msg and api_key:
        msg_lower = last_user_msg.lower()

        # ── Rule chain intent detection ────────────────────────────────────
        try:
            existing_rules = db.query(Alarm.__class__).filter(False).all()  # placeholder
            from app.models.models import ThresholdRule as TR
            existing_rules = db.query(TR).filter(
                TR.tenant_id == current_user.tenant_id,
                TR.is_active == True,
            ).all()
            rule_intent = await _try_parse_rule_intent(api_key, last_user_msg, device_list, existing_rules)
            if rule_intent:
                rule_actioned = await _execute_rule_from_chat(db, current_user, device_list, rule_intent)
        except Exception as exc:
            logger.debug("rule intent check failed (non-fatal): %s", exc)

        # ── Alarm action detection ─────────────────────────────────────────
        alarm_keywords = ["acknowledge", "ack", "clear", "dismiss", "resolve"]
        if any(kw in msg_lower for kw in alarm_keywords):
            try:
                action = "ack_all" if any(w in msg_lower for w in ["all", "every"]) else None
                if "clear" in msg_lower or "resolve" in msg_lower or "dismiss" in msg_lower:
                    action = "clear_all" if any(w in msg_lower for w in ["all", "every"]) else "clear_all"
                elif "ack" in msg_lower or "acknowledge" in msg_lower:
                    action = "ack_all"

                if action:
                    sev = None
                    for s in ["CRITICAL", "MAJOR", "MINOR", "WARNING"]:
                        if s.lower() in msg_lower:
                            sev = s
                            break
                    from app.models.models import AlarmStatus
                    base_q = db.query(Alarm).filter(
                        Alarm.device_id.in_([d.id for d in devices]),
                        Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]),
                    )
                    if sev:
                        base_q = base_q.filter(Alarm.severity == sev)
                    target_alarms = base_q.all()
                    now = datetime.now(timezone.utc)
                    count = 0
                    for a in target_alarms:
                        if "clear" in action:
                            a.status = AlarmStatus.CLEARED_ACK if a.ack_ts else AlarmStatus.CLEARED_UNACK
                            a.clear_ts = now
                            a.cleared_by = str(current_user.id)
                        else:
                            a.status = AlarmStatus.ACTIVE_ACK
                            a.ack_ts = now
                            a.ack_by = str(current_user.id)
                        count += 1
                    if count:
                        db.commit()
                        alarm_actioned = {"action": action, "count": count, "severity": sev}
            except Exception as exc:
                logger.debug("alarm action failed (non-fatal): %s", exc)

        # ── RPC Intent Detection ───────────────────────────────────────────
        try:
            device_keys = _get_device_keys(db, device_list)
            intent = await _try_parse_rpc_intent(api_key, last_user_msg, device_list, device_keys)
            if intent:
                rpc_executed = await _execute_rpc_from_chat(
                    db, current_user, devices,
                    intent["device_name"], intent["params"],
                )
        except Exception as exc:
            logger.debug("rpc intent check failed (non-fatal): %s", exc)

    # ── Build chat system prompt ──────────────────────────────────────────────
    rpc_capability = (
        "\n\nYou CAN directly execute RPC commands on devices. "
        "When you do, confirm it with: \'✅ Done — sent [params] to [device]\'. "
        "The command has already been queued — do not say you cannot execute commands."
        if current_user.role != "CUSTOMER_USER"
        else "\n\nYou cannot execute RPC commands for this role."
    )

    system_prompt = f"""You are an intelligent IoT platform assistant for {tenant.name if tenant else "TriAxis Nexus"}.
You help operators monitor devices, understand alarms, analyse trends, and directly control devices.

DEVICES ({len(devices)} total):
{chr(10).join(f"- {d['name']} (ID: {d['id']}) [{d['type']}] — {d['status']}" for d in device_list)}

ACTIVE ALARMS ({len(active_alarms)}):
{chr(10).join(f"- {a['type']} on {a['device']} — {a['severity']}" for a in alarm_list) or "None"}

LATEST TELEMETRY:
{chr(10).join(f"- {dev}: {vals}" for dev, vals in telemetry_ctx.items()) or "No data"}
{device_context}
{rpc_capability}

Be concise and technical. Use bullet points for lists. Today is {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}.
"""

    chat_messages = [{"role": "system", "content": system_prompt}]

    # Inject action confirmations so LLM responds naturally
    if rpc_executed:
        chat_messages.append({
            "role": "system",
            "content": (
                f"[SYSTEM: RPC command already executed — "
                f"sent {json.dumps(rpc_executed['params'])} to {rpc_executed['device_name']} "
                f"(cmd_id: {rpc_executed['cmd_id']}). "
                f"Confirm this to the user naturally with ✅.]"
            )
        })
    if alarm_actioned:
        chat_messages.append({
            "role": "system",
            "content": (
                f"[SYSTEM: Alarm action already executed — "
                f"{alarm_actioned['action']} on {alarm_actioned['count']} alarm(s)"
                f"{' ('+alarm_actioned['severity']+' only)' if alarm_actioned.get('severity') else ''}. "
                f"Confirm this to the user naturally with ✅.]"
            )
        })

    for msg in body.messages:
        chat_messages.append({"role": msg.role, "content": msg.content})

    # ── Call Groq ─────────────────────────────────────────────────────────────
    try:
        reply = await _call_groq(api_key, chat_messages, max_tokens=512, temperature=0.4)
        return {
            "reply":          reply,
            "engine":         f"groq/{GROQ_MODEL_FAST}",
            "rpc_executed":   rpc_executed,
            "alarm_actioned": alarm_actioned,
            "rate":           rate_info,       # frontend can show usage counter
        }
    except Exception as exc:
        logger.error("chat.failed error=%s", exc)
        return {
            "reply":  f"Sorry, I'm having trouble connecting right now. Please try again. ({str(exc)[:60]})",
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
You CAN execute RPC commands directly — use the exact key names listed above for each device.
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


# ── 2. Scheduled RPC ──────────────────────────────────────────────────────────

class ScheduledRpcRequest(BaseModel):
    device_id: UUID
    params: dict
    run_at: datetime          # UTC datetime to execute
    repeat_hours: Optional[float] = None   # e.g. 6.0 = every 6 hours


@router.post("/schedule-rpc")
def schedule_rpc(
    body: ScheduledRpcRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Schedule an RPC command to run at a future time.
    Stored in rpc_commands with a future created_at — picked up by the scheduler.
    """
    _assert_device(body.device_id, current_user, db)
    from app.models.models import RpcCommand, RpcCommandStatus

    cmd = RpcCommand(
        device_id  = body.device_id,
        method     = "set",
        params     = body.params,
        status     = "SCHEDULED",
        created_by = str(current_user.id),
        # Store scheduled time in result field as metadata
        result     = {
            "scheduled_for": body.run_at.isoformat(),
            "repeat_hours":  body.repeat_hours,
        },
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    return {
        "cmd_id":       str(cmd.id),
        "device_id":    str(body.device_id),
        "params":       body.params,
        "scheduled_for": body.run_at.isoformat(),
        "repeat_hours":  body.repeat_hours,
    }


@router.get("/schedule-rpc")
def list_scheduled_rpc(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all pending scheduled RPC commands for the tenant."""
    from app.models.models import RpcCommand
    devices = _scoped_devices(current_user, db).all()
    device_ids = [d.id for d in devices]
    cmds = (
        db.query(RpcCommand)
        .filter(
            RpcCommand.device_id.in_(device_ids),
            RpcCommand.status == "SCHEDULED",
        )
        .order_by(RpcCommand.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "cmd_id":        str(c.id),
            "device_id":     str(c.device_id),
            "params":        c.params,
            "scheduled_for": c.result.get("scheduled_for") if c.result else None,
            "repeat_hours":  c.result.get("repeat_hours") if c.result else None,
        }
        for c in cmds
    ]


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

    summary = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "period":            "Last 24 hours",
        "total_devices":     len(devices),
        "active_alarms":     total_active_alarms,
        "maintenance_needed": maintenance_needed,
        "critical_devices":  len(critical_devices),
        "devices":           report_data,
    }

    if not api_key:
        return {"report": summary, "narrative": "Add GROQ_API_KEY for AI narrative."}

    prompt = f"""Generate a concise daily IoT fleet health report.

Data:
{json.dumps(summary, indent=2)}

Write a professional 3-paragraph executive summary covering:
1. Overall fleet health status
2. Devices needing immediate attention
3. Recommended actions for today

Be direct and actionable. Use exact device names and numbers."""

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
