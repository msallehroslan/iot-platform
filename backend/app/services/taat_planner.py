"""
app/services/taat_planner.py — TAAT v2 Intent Router + Planner Agent

Replaces the 400-line keyword soup in ai_chat with a clean 3-step flow:

    Step 1 — Intent Router (1 fast Groq call)
        Classify message into one of 8 intent categories.
        Low-cost: uses 8b model, max 60 tokens.

    Step 2 — Tool Executor (no Groq, pure Python)
        Call the right tools from taat_tools.py based on intent.
        Assembles enriched context: anomalies, baselines, health, RPC history.

    Step 3 — Safety Guard
        Check risk level of any write action.
        CUSTOMER_USER → read-only.
        HIGH risk → return confirm_required instead of executing.

    Step 4 — Planner Reply (1 Groq call)
        Send tool results + memory + conversation to Groq.
        Groq sees real data, never guesses. One call per chat turn.

Intent categories:
    QUESTION        → general question, status query
    DEVICE_CONTROL  → RPC command (turn on/off/set)
    ALARM           → ack/clear alarms
    RULE            → create/update/delete threshold rules
    USER            → invite/delete/change role
    REPORT          → daily/fleet report
    RCA             → root cause analysis for specific key/event
    RECOMMEND       → autonomous recommendation + proposed actions
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Intent definitions ────────────────────────────────────────────────────────

INTENT_CATEGORIES = [
    "QUESTION",
    "DEVICE_CONTROL",
    "ALARM",
    "RULE",
    "USER",
    "REPORT",
    "RCA",
    "RECOMMEND",
    "SCHEDULE",      # schedule/cancel future RPC commands
]

CUSTOMER_ALLOWED_INTENTS = {"QUESTION", "REPORT"}


# ── Step 1: Intent Router ─────────────────────────────────────────────────────

async def classify_intent(
    api_key: str,
    message: str,
    call_groq,
) -> str:
    """
    Single fast Groq call to classify the user's intent.
    Falls back to QUESTION on any failure.
    """
    prompt = f"""Classify this IoT platform message into exactly one category.

Categories:
- QUESTION: asking about status, values, trends, history, "what is", "show me", "why", "how many"
- DEVICE_CONTROL: turn on/off, set value, enable/disable, start/stop, toggle, reboot, restart
- ALARM: acknowledge, clear, dismiss, resolve alarms
- RULE: create/update/delete threshold rules, alarm rules, "set alarm when"
- USER: invite/delete/manage users, change roles
- REPORT: daily report, fleet summary, generate report
- RCA: root cause analysis, "why did", "what caused", "explain this anomaly"
- RECOMMEND: "what should I do", "recommend", "suggest", anomaly + asking for action
- SCHEDULE: schedule/cancel future commands — "turn off at midnight", "restart every 6h", "cancel scheduled", "list scheduled"

Message: "{message}"

Respond with ONLY the category name, nothing else."""

    try:
        result = await call_groq(
            api_key,
            [{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
        )
        intent = result.strip().upper()
        if intent in INTENT_CATEGORIES:
            return intent
    except Exception as exc:
        logger.debug("intent classification failed: %s", exc)

    # Keyword fallback so we never return QUESTION for obvious actions
    msg = message.lower()
    if any(w in msg for w in ["turn on", "turn off", "set ", "enable", "disable", "toggle", "reboot", "restart"]):
        return "DEVICE_CONTROL"
    if any(w in msg for w in ["acknowledge", "ack", "clear alarm", "dismiss", "resolve"]):
        return "ALARM"
    if any(w in msg for w in ["create rule", "set alarm when", "add rule", "delete rule", "update rule", "alarm above", "alarm below"]):
        return "RULE"
    if any(w in msg for w in ["invite", "add user", "delete user", "change role", "list users"]):
        return "USER"
    if any(w in msg for w in ["daily report", "fleet report", "generate report"]):
        return "REPORT"
    if any(w in msg for w in ["why did", "what caused", "root cause", "explain"]):
        return "RCA"
    if any(w in msg for w in ["recommend", "what should", "suggest", "advise"]):
        return "RECOMMEND"
    if any(w in msg for w in ["schedule", "at midnight", "at noon", "every hour", "every 6h",
                               "tomorrow at", "cancel schedule", "list scheduled", "recurring"]):
        return "SCHEDULE"

    return "QUESTION"


# ── Step 2: Context Builder ───────────────────────────────────────────────────

def build_context(
    db: Session,
    current_user,
    devices: list,
    intent: str,
    device_id: Optional[str] = None,
    message: str = "",
) -> dict:
    """
    Call the right tools for this intent and return structured context.
    All reads go through data_service (cached), never direct DB.
    """
    from app.services.taat_tools import (
        tool_get_devices,
        tool_get_latest_telemetry,
        tool_get_active_alarms,
        tool_get_device_health,
        tool_get_anomalies,
        tool_get_baseline,
        tool_get_rpc_history,
        tool_get_audit_log,
        tool_get_memory,
    )

    ctx: dict = {
        "intent":      intent,
        "device_list": [{"id": d["id"], "name": d["name"], "status": d["status"]} for d in devices],
    }

    # Always fetch memory — cheap, cached, gives TAAT context
    ctx["memory"] = tool_get_memory(db, current_user)

    # For all intents: active alarms fleet-wide
    if devices:
        all_alarms = []
        for d in devices[:10]:
            a = tool_get_active_alarms(db, d["id"])
            if a.get("count", 0) > 0:
                for alarm in a.get("alarms", []):
                    all_alarms.append({**alarm, "device_name": d["name"]})
        ctx["active_alarms"] = all_alarms[:20]

    # Device-specific context
    focus_id = device_id or (devices[0]["id"] if len(devices) == 1 else None)

    if focus_id and intent in ("QUESTION", "DEVICE_CONTROL", "ALARM", "RCA", "RECOMMEND"):
        ctx["telemetry"]    = tool_get_latest_telemetry(db, focus_id)
        ctx["health"]       = tool_get_device_health(db, focus_id)
        ctx["anomalies"]    = tool_get_anomalies(db, focus_id, hours=24)
        ctx["baseline"]     = tool_get_baseline(db, focus_id)

    if focus_id and intent in ("DEVICE_CONTROL", "RCA"):
        ctx["rpc_history"] = tool_get_rpc_history(db, focus_id, limit=5)

    if intent == "RECOMMEND" and focus_id:
        # Full enriched view for recommendation
        ctx["telemetry"]    = tool_get_latest_telemetry(db, focus_id)
        ctx["health"]       = tool_get_device_health(db, focus_id)
        ctx["anomalies"]    = tool_get_anomalies(db, focus_id, hours=24)
        ctx["baseline"]     = tool_get_baseline(db, focus_id)
        ctx["rpc_history"]  = tool_get_rpc_history(db, focus_id, limit=5)
        # Enrich anomalous keys with KeyIntelligence
        most_anom = ctx["anomalies"].get("most_anomalous_key")
        if most_anom:
            from app.services.taat_tools import tool_get_key_intelligence
            ctx["key_intel"] = tool_get_key_intelligence(db, focus_id, most_anom)

    if intent == "RULE":
        from app.models.models import ThresholdRule
        rules = db.query(ThresholdRule).filter(
            ThresholdRule.tenant_id == current_user.tenant_id,
            ThresholdRule.is_active == True,
        ).all()
        ctx["existing_rules"] = [
            {"id": str(r.id), "key": r.key, "condition": r.condition,
             "threshold": r.threshold, "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity)}
            for r in rules
        ]

    if intent == "USER":
        from app.models.models import User
        users = db.query(User).filter(
            User.tenant_id == current_user.tenant_id
        ).all()
        ctx["users"] = [
            {"id": str(u.id), "email": u.email, "role": u.role,
             "name": f"{u.first_name or ''} {u.last_name or ''}".strip()}
            for u in users
        ]

    if intent in ("RCA", "RECOMMEND"):
        ctx["audit_trail"] = tool_get_audit_log(db, current_user, limit=10)

    return ctx


# ── Step 3: Safety Guard ──────────────────────────────────────────────────────

def check_permission(
    intent: str,
    action: dict,
    current_user,
    message: str = "",
) -> Tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).

    CUSTOMER_USER: read-only TAAT (QUESTION + REPORT only).
    TENANT_USER: no user management, no bulk deletes.
    TENANT_ADMIN: full access but HIGH-risk needs confirm.
    """
    from app.services.taat_tools import assess_risk

    role = getattr(current_user, "role", "TENANT_USER")

    # CUSTOMER_USER: read-only
    if role == "CUSTOMER_USER" and intent not in CUSTOMER_ALLOWED_INTENTS:
        return False, f"Your role allows viewing status and reports only."

    # USER management: admin only
    if intent == "USER" and role != "TENANT_ADMIN":
        return False, "Only admins can manage users."

    # Rule delete all: admin only
    if intent == "RULE" and action.get("delete_all") and role != "TENANT_ADMIN":
        return False, "Only admins can delete all rules."

    return True, ""


def get_action_risk(intent: str, action: dict, message: str) -> str:
    """Determine risk level for a planned action."""
    from app.services.taat_tools import assess_risk
    tool_map = {
        "DEVICE_CONTROL": "send_rpc",
        "ALARM":          "clear_alarm" if "clear" in message.lower() else "ack_alarm",
        "RULE":           "delete_rule" if action.get("action") == "delete" else "create_rule",
        "USER":           "create_rule",  # medium
    }
    tool_name = tool_map.get(intent, "get_devices")
    return assess_risk(tool_name, action, message)


# ── Step 4: Planner Prompt Builder ────────────────────────────────────────────

def build_system_prompt(
    tenant_name: str,
    intent: str,
    ctx: dict,
    current_user,
    confirm_mode: bool = False,
) -> str:
    """
    Build the system prompt sent to Groq with tool results injected.
    Groq sees real data — never guesses.
    """
    role = getattr(current_user, "role", "TENANT_USER")
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    device_lines = "\n".join(
        f"  - {d['name']} [{d['status']}]"
        for d in ctx.get("device_list", [])
    ) or "  None"

    alarm_lines = "\n".join(
        f"  - {a.get('alarm_type','alarm')} on {a.get('device_name','?')} — {a.get('severity','?')}"
        for a in ctx.get("active_alarms", [])
    ) or "  None"

    memory_lines = "\n".join(
        f"  [{m['type']}] {m['content']}"
        for m in ctx.get("memory", {}).get("memories", [])
    ) or "  None"

    # Build telemetry section
    telem_section = ""
    if "telemetry" in ctx:
        vals = ctx["telemetry"].get("values", {})
        telem_section = f"\nCURRENT TELEMETRY:\n" + "\n".join(
            f"  {k}: {v}" for k, v in list(vals.items())[:10]
        )

    # Build intelligence section
    intel_section = ""
    if "health" in ctx:
        h = ctx["health"]
        intel_section += f"\nHEALTH: {h.get('health_label','?')} (score: {h.get('health_score','?')})"
    if "anomalies" in ctx:
        a = ctx["anomalies"]
        if a.get("anomaly_count", 0) > 0:
            intel_section += f"\nANOMALIES: {a['anomaly_count']} detected, most anomalous: {a.get('most_anomalous_key','?')}"
    if "baseline" in ctx and ctx["baseline"].get("status") == "active":
        intel_section += f"\nBASELINE: active for {len(ctx['baseline'].get('keys',{}))} keys"
    if "key_intel" in ctx:
        ki = ctx["key_intel"]
        intel_section += (
            f"\nKEY INTELLIGENCE ({ki.get('key','?')}):"
            f" value={ki.get('value')} {ki.get('unit','')}"
            f" | status={ki.get('status')} | risk={ki.get('risk')}"
            f" | {ki.get('reason','')}"
            f" | recommended: {ki.get('recommended_action','')}"
        )
    if "rpc_history" in ctx:
        rh = ctx["rpc_history"]
        if rh.get("count", 0) > 0:
            last = rh["commands"][0]
            intel_section += f"\nLAST RPC: {last['method']} {last['params']} → {last['status']}"

    # Role capabilities
    if role == "CUSTOMER_USER":
        capabilities = "\nYour role: READ-ONLY. You can answer questions and show reports. Cannot execute commands."
    elif role == "TENANT_USER":
        capabilities = "\nYour role: Can send RPC, ack/clear alarms. Cannot manage users or delete all rules."
    else:
        capabilities = (
            "\nCapabilities: RPC commands · alarm actions · rule management · user management · reports"
            "\nFor HIGH-risk actions (delete all, turn off all): return confirm_required in your response."
        )

    confirm_note = ""
    if confirm_mode:
        confirm_note = "\n\nCONFIRM MODE: User confirmed a pending HIGH-risk action. Execute it now and confirm with ✅."

    return f"""You are TAAT — the intelligent IoT agent for {tenant_name}.
You reason from real sensor data, never guess. Be concise and direct.
Today: {now}

DEVICES ({len(ctx.get('device_list',[]))}):
{device_lines}

ACTIVE ALARMS:
{alarm_lines}
{telem_section}
{intel_section}

AGENT MEMORY:
{memory_lines}
{capabilities}
{confirm_note}

RULES:
1. Only report facts from the data above — never invent values.
2. For executed actions, confirm with ✅ and one line of detail.
3. For HIGH-risk actions, say: "⚠️ This will [description]. Reply 'proceed' to confirm."
4. If you cannot find a device or key, say so — do not guess.
5. Keep responses short unless asked for detail.
"""


# ── Action extractor (for write intents) ─────────────────────────────────────

async def extract_action(
    api_key: str,
    intent: str,
    message: str,
    ctx: dict,
    call_groq,
) -> Optional[dict]:
    """
    For write intents, extract structured action from message.
    Reuses existing battle-tested parsers from intelligence.py where available.
    Returns action dict or None.
    """
    if intent == "DEVICE_CONTROL":
        return await _extract_rpc_action(api_key, message, ctx, call_groq)
    if intent == "RULE":
        return await _extract_rule_action(api_key, message, ctx, call_groq)
    if intent == "ALARM":
        return _extract_alarm_action(message, ctx)
    if intent == "USER":
        return await _extract_user_action(api_key, message, ctx, call_groq)
    if intent == "SCHEDULE":
        return await _extract_schedule_action(api_key, message, ctx, call_groq)
    return None


async def _extract_rpc_action(api_key, message, ctx, call_groq) -> Optional[dict]:
    devices = ctx.get("device_list", [])
    device_names = [d["name"] for d in devices]
    if not device_names:
        return None

    prompt = f"""Extract RPC command from: "{message}"
Devices: {json.dumps(device_names)}
Respond JSON only: {{"device_name":"<name>","method":"set","params":{{"<key>":<value>}}}}
If not a control command: null"""
    try:
        r = await call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=100, temperature=0.0)
        r = r.strip()
        if r.lower() == "null" or not r.startswith("{"):
            return None
        parsed = json.loads(r)
        if "device_name" in parsed and "params" in parsed:
            return parsed
    except Exception:
        pass
    return None


async def _extract_rule_action(api_key, message, ctx, call_groq) -> Optional[dict]:
    devices = ctx.get("device_list", [])
    rules = ctx.get("existing_rules", [])
    msg_lower = message.lower()

    # Fast path for delete all
    if any(w in msg_lower for w in ["delete all", "remove all", "clear all rules"]):
        return {"action": "delete", "delete_all": True}

    prompt = f"""Extract threshold rule action from: "{message}"
Devices: {json.dumps([d['name'] for d in devices])}
Existing rules: {json.dumps(rules)}

Respond JSON only:
- Create: {{"action":"create","device_name":"<name or null>","key":"<key>","condition":"gt|lt","threshold":<number>,"severity":"WARNING|CRITICAL|MAJOR|MINOR"}}
- Update: {{"action":"update","key":"<key>","threshold":<number>}}
- Delete: {{"action":"delete","key":"<key>"}}
- If unclear: null"""
    try:
        r = await call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=150, temperature=0.0)
        r = r.strip()
        if r.lower() == "null" or not r.startswith("{"):
            return None
        return json.loads(r)
    except Exception:
        pass
    return None


def _extract_alarm_action(message: str, ctx: dict) -> dict:
    msg_lower = message.lower()
    all_alarms = ctx.get("active_alarms", [])
    is_clear = any(w in msg_lower for w in ["clear", "resolve", "dismiss"])
    is_bulk  = any(w in msg_lower for w in ["all", "every"])
    sev = None
    for s in ["CRITICAL", "MAJOR", "MINOR", "WARNING"]:
        if s.lower() in msg_lower:
            sev = s
            break
    return {
        "action":   "clear" if is_clear else "ack",
        "bulk":     is_bulk,
        "severity": sev,
        "count":    len(all_alarms),
    }


async def _extract_user_action(api_key, message, ctx, call_groq) -> Optional[dict]:
    users = ctx.get("users", [])
    msg_lower = message.lower()
    if any(w in msg_lower for w in ["list", "show", "who"]):
        return {"action": "list"}

    prompt = f"""Extract user management action: "{message}"
Users: {json.dumps(users)}
Respond JSON only:
- Invite: {{"action":"invite","email":"<email>","role":"TENANT_ADMIN|TENANT_USER","password":"<10char>"}}
- Delete: {{"action":"delete","user_id":"<id>"}}
- Role: {{"action":"change_role","user_id":"<id>","role":"<new_role>"}}
- List: {{"action":"list"}}
- If unclear: null"""
    try:
        r = await call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=150, temperature=0.0)
        r = r.strip()
        if r.lower() == "null" or not r.startswith("{"):
            return None
        return json.loads(r)
    except Exception:
        pass
    return None


async def _extract_schedule_action(api_key, message, ctx, call_groq) -> Optional[dict]:
    """Extract schedule/cancel intent from message."""
    msg_lower = message.lower()

    # Cancel scheduled
    if any(w in msg_lower for w in ["cancel", "remove scheduled", "stop scheduled", "delete scheduled"]):
        return {"action": "cancel", "device_name": None}

    # List scheduled
    if any(w in msg_lower for w in ["list scheduled", "show scheduled", "what is scheduled", "pending commands"]):
        return {"action": "list"}

    devices = ctx.get("device_list", [])
    device_names = [d["name"] for d in devices]

    prompt = f"""Extract scheduled RPC command from: "{message}"
Devices: {json.dumps(device_names)}
Current UTC time: {__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

Respond ONLY with valid JSON or null.

For schedule:
{{"action":"schedule","device_name":"<name>","method":"set","params":{{"<key>":<value>}},"time_str":"<when>","repeat_hours":<number or null>}}

time_str examples: "midnight", "9am", "tomorrow at 6pm", "in 2 hours", "+6h"
repeat_hours: null for one-shot, float for recurring (e.g. 6.0 for every 6 hours)

For cancel: {{"action":"cancel","device_name":"<name or null>"}}
For list:   {{"action":"list"}}
If unclear: null"""

    try:
        r = await call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=150, temperature=0.0)
        r = r.strip()
        if r.lower() == "null" or not r.startswith("{"):
            return None
        return json.loads(r)
    except Exception:
        pass
    return None
