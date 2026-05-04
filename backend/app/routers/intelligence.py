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
                    "model": "llama-3.3-70b-versatile",
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
            "engine":       "groq/llama-3.3-70b",
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
    device_id: Optional[UUID] = None   # optional device context


async def _call_groq(api_key: str, messages: list, max_tokens: int = 512, temperature: float = 0.4) -> str:
    """Helper: call Groq and return text reply."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": max_tokens,
                  "messages": messages, "temperature": temperature},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


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
    control_keywords = [
        "turn on", "turn off", "switch on", "switch off",
        "set ", "enable", "disable", "activate", "deactivate",
        "toggle", "open", "close", "start", "stop", "run", "pause",
    ]
    msg_lower = user_message.lower()
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
- "turn on motor1" on a device with key "motor1" → {{"params": {{"motor1": true}}}}
- "turn off pump_main" → {{"params": {{"pump_main": false}}}}
- "start the fan" on device with key "fan" → {{"params": {{"fan": true}}}}
- "set motor_speed to 75" → {{"params": {{"motor_speed": 75}}}}
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

    # ── RPC Intent Detection ─────────────────────────────────────────────────
    # Check if user wants to control a device before generating a chat reply
    last_user_msg = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), ""
    )

    rpc_executed = None
    # Only TENANT_ADMIN and TENANT_USER can execute RPC via chat
    if current_user.role != "CUSTOMER_USER" and last_user_msg:
        try:
            # Fetch REAL keys from DB for every device — no hardcoding
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

    # If RPC was executed, inject a system note so LLM confirms it naturally
    if rpc_executed:
        chat_messages.append({
            "role": "system",
            "content": (
                f"[SYSTEM: RPC command already executed — "
                f"sent {json.dumps(rpc_executed['params'])} to {rpc_executed['device_name']} "
                f"(cmd_id: {rpc_executed['cmd_id']}). "
                f"Confirm this to the user naturally.]"
            )
        })

    for msg in body.messages:
        chat_messages.append({"role": msg.role, "content": msg.content})

    # ── Call Groq ─────────────────────────────────────────────────────────────
    try:
        reply = await _call_groq(api_key, chat_messages, max_tokens=512, temperature=0.4)
        return {
            "reply":        reply,
            "engine":       "groq-llama-3.3-70b",
            "rpc_executed": rpc_executed,   # frontend can show a special RPC badge
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
                    "model": "llama-3.3-70b-versatile",
                    "messages": groq_messages,
                    "max_tokens": 512,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]

        return {"reply": reply, "engine": "groq/llama-3.3-70b"}

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
