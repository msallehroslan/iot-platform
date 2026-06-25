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
    "SCHEDULE",
    "REMEMBER",      # save semantic/location/preference memory
    "FLEET",         # compare/rank all devices
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
- REMEMBER: user wants to save a fact — "remember that", "note that", "X is located in", "X is used for", "X controls", "I prefer"
- SCHEDULE: any command with a future time — "schedule", "at midnight", "at 9am", "tomorrow", "every Xh", "in X minutes", "in X hours", "in 2 min", "cancel scheduled". If the word 'schedule' appears OR a future time is mentioned → SCHEDULE, not DEVICE_CONTROL.
- DEVICE_CONTROL: turn on/off RIGHT NOW, set value now, enable/disable now, toggle, reboot immediately
- ALARM: acknowledge, clear, dismiss, resolve alarms
- RULE: create/update/delete threshold rules, alarm rules, "set alarm when"
- USER: invite/delete/manage users, change roles
- REPORT: daily report, fleet summary, generate report
- RCA: root cause analysis, "why did", "what caused", "explain this anomaly"
- RECOMMEND: "what should I do", "recommend", "suggest", anomaly + asking for action
- FLEET: compare all devices, rank by health/alarms, "which device is worst", "show all devices", "fleet overview", "compare pumps"

IMPORTANT: If the message contains a future time ("at midnight", "at 9am", "tomorrow", "every X hours", "in X hours"), classify as SCHEDULE even if it also contains control words like "turn on/off".

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
    # SCHEDULE must be checked FIRST — "turn on at midnight" has both control + time words
    schedule_time_words = ["at midnight", "at noon", "tomorrow at", "at 9", "at 10", "at 11",
                           "at 12", "every hour", "every 6h", "every 2h", "every 12h", "every 24h",
                           "in 1 hour", "in 2 hours", "in 3 hours", "in 6 hours", "in 12 hours",
                           "in 30 min", "in 45 min",
                           "in 1 minute", "in 2 minutes", "in 3 minutes", "in 5 minutes",
                           "in 10 minutes", "in 15 minutes", "in 20 minutes", "in 30 minutes",
                           "in 45 minutes", "in 60 minutes",
                           "in 1 min", "in 2 min", "in 3 min", "in 5 min", "in 10 min", "in 15 min",
                           "cancel schedule", "list scheduled",
                           "schedule", "recurring", "tonight at", "nightly", "daily at"]
    if any(w in msg for w in schedule_time_words):
        return "SCHEDULE"
    if any(w in msg for w in ["turn on", "turn off", "set ", "enable", "disable", "toggle", "reboot", "restart"]):
        return "DEVICE_CONTROL"
    if any(w in msg for w in ["acknowledge", "ack", "clear alarm", "dismiss", "resolve"]):
        return "ALARM"
    if any(w in msg for w in ["create rule", "set alarm when", "add rule", "delete rule", "update rule",
                               "delete all rules", "remove all rules", "clear all rules",
                               "alarm above", "alarm below"]):
        return "RULE"
    if any(w in msg for w in ["invite", "add user", "delete user", "change role", "list users"]):
        return "USER"
    if any(w in msg for w in ["daily report", "fleet report", "generate report"]):
        return "REPORT"
    if any(w in msg for w in ["compare all", "compare device", "compare pump", "rank device",
                               "rank by health", "rank by alarm", "fleet overview",
                               "all devices", "which device is", "worst device", "best device"]):
        return "FLEET"
    if any(w in msg for w in ["why did", "what caused", "root cause", "explain"]):
        return "RCA"
    if any(w in msg for w in ["recommend", "what should", "suggest", "advise"]):
        return "RECOMMEND"
    if any(w in msg for w in ["remember that", "remember:", "note that", "save that",
                               "is located in", "is used for", "is installed at",
                               "controls the", "i prefer", "user prefers"]):
        return "REMEMBER"

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
        "device_list": [
            {
                "id":           d["id"],
                "name":         d["name"],
                "status":       d["status"],
                "last_seen_at": d.get("last_seen_at"),
            }
            for d in devices
        ],
    }

    # ── Each tool call is individually guarded ────────────────────────────────
    # A missing table (migration not run), Redis error, or any transient failure
    # must NEVER crash the chat endpoint. Tools return empty dicts on failure.

    def _safe(fn, *args, fallback=None, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("taat_planner tool error (%s): %s", fn.__name__, exc)
            return fallback if fallback is not None else {}

    # Memory — gracefully handles missing agent_memory table
    ctx["memory"] = _safe(tool_get_memory, db, current_user, fallback={"count": 0, "memories": []})

    # Fleet alarms
    if devices:
        all_alarms = []
        for d in devices[:10]:
            a = _safe(tool_get_active_alarms, db, d["id"], fallback={"count": 0, "alarms": []})
            for alarm in a.get("alarms", []):
                all_alarms.append({**alarm, "device_name": d["name"]})
        ctx["active_alarms"] = all_alarms[:20]

    # Device-specific context
    focus_id = device_id or (devices[0]["id"] if len(devices) == 1 else None)

    if focus_id and intent in ("QUESTION", "DEVICE_CONTROL", "ALARM", "RCA", "RECOMMEND", "SCHEDULE"):
        ctx["telemetry"] = _safe(tool_get_latest_telemetry, db, focus_id)
        ctx["health"]    = _safe(tool_get_device_health,    db, focus_id)
        ctx["anomalies"] = _safe(tool_get_anomalies,        db, focus_id, hours=24)
        ctx["baseline"]  = _safe(tool_get_baseline,         db, focus_id)
        # 48h history for today-vs-yesterday comparisons
        # Pick best key: most anomalous > numeric with non-zero value > first key
        _telem = ctx.get("telemetry", {})
        _telem_vals = (_telem.get("values") or _telem) if isinstance(_telem, dict) else {}
        _most_anom = ctx.get("anomalies", {}).get("most_anomalous_key")
        _all_keys = list(_telem_vals.keys()) if isinstance(_telem_vals, dict) else []
        # Prefer keys with non-zero numeric values
        _nonzero_keys = [
            k for k in _all_keys
            if isinstance(_telem_vals.get(k), (int, float)) and _telem_vals.get(k) != 0
        ]
        _cmp_key = (
            _most_anom if _most_anom and _most_anom in _telem_vals
            else _nonzero_keys[0] if _nonzero_keys
            else _all_keys[0] if _all_keys
            else None
        )
        if _cmp_key:
            from app.services.taat_tools import tool_get_telemetry_history
            # Fetch comparison for top 4 numeric non-zero keys
            _comparison_lines = []
            _checked_keys = [_cmp_key] + [k for k in _nonzero_keys if k != _cmp_key][:3]
            for _ck in _checked_keys:
                _hist = _safe(tool_get_telemetry_history, db, focus_id, _ck, hours=48, resolution="1h")
                if _hist and _hist.get("today_avg") is not None and _hist.get("yesterday_avg") is not None:
                    _comparison_lines.append(
                        f"{_ck}: today={_hist['today_avg']} vs yesterday={_hist['yesterday_avg']} ({_hist.get('comparison','')})"
                    )
            ctx["daily_comparison"] = {
                "key": _cmp_key,
                "comparison": " | ".join(_comparison_lines) if _comparison_lines else "no data",
                "today_avg": None,
                "yesterday_avg": None,
                "today": [],
                "yesterday": [],
                "all_comparisons": _comparison_lines,
            }

    if focus_id and intent in ("DEVICE_CONTROL", "RCA", "SCHEDULE"):
        ctx["rpc_history"] = _safe(tool_get_rpc_history, db, focus_id, limit=5)

    if intent == "RECOMMEND" and focus_id:
        ctx["telemetry"]  = _safe(tool_get_latest_telemetry, db, focus_id)
        ctx["health"]     = _safe(tool_get_device_health,    db, focus_id)
        ctx["anomalies"]  = _safe(tool_get_anomalies,        db, focus_id, hours=24)
        ctx["baseline"]   = _safe(tool_get_baseline,         db, focus_id)
        ctx["rpc_history"] = _safe(tool_get_rpc_history,     db, focus_id, limit=5)
        most_anom = ctx.get("anomalies", {}).get("most_anomalous_key")
        if most_anom:
            from app.services.taat_tools import tool_get_key_intelligence
            ctx["key_intel"] = _safe(tool_get_key_intelligence, db, focus_id, most_anom)

    if intent == "RULE":
        try:
            from app.models.models import ThresholdRule
            rules = db.query(ThresholdRule).filter(
                ThresholdRule.tenant_id == current_user.tenant_id,
                ThresholdRule.is_active == True,
            ).all()
            ctx["existing_rules"] = [
                {"id": str(r.id), "key": r.key, "condition": r.condition,
                 "threshold": r.threshold,
                 "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity)}
                for r in rules
            ]
        except Exception as exc:
            logger.debug("existing_rules fetch failed: %s", exc)
            ctx["existing_rules"] = []

    if intent == "USER":
        try:
            from app.models.models import User
            users = db.query(User).filter(User.tenant_id == current_user.tenant_id).all()
            ctx["users"] = [
                {"id": str(u.id), "email": u.email, "role": u.role,
                 "name": f"{u.first_name or ''} {u.last_name or ''}".strip()}
                for u in users
            ]
        except Exception as exc:
            logger.debug("users fetch failed: %s", exc)
            ctx["users"] = []

    if intent in ("RCA", "RECOMMEND"):
        ctx["audit_trail"] = _safe(tool_get_audit_log, db, current_user, limit=10)

    # ── Slow-loop intelligence snapshot ──────────────────────────────────────
    # SlowLoopEngine runs every 5s and pre-computes degradation velocity,
    # anomaly persistence, failure probability, and ranked recommendations.
    # Reading it here is free (in-memory dict lookup) — no DB, no Redis.
    if focus_id:
        try:
            from app.services.slow_loop_intelligence import slow_loop
            snap = slow_loop.get_snapshot(focus_id)
            if snap:
                ctx["slow_intel"] = snap
        except Exception as exc:
            logger.debug("slow_intel fetch failed: %s", exc)

    # ── Intelligence coordinator snapshot (Redis) ─────────────────────────────
    # IntelligenceCoordinator runs every 30s and writes a richer snapshot to
    # Redis with: status, risk, causal_signals, degradation_rate, confidence.
    # We read it synchronously via redis-py (not aioredis) to avoid async
    # bridging issues inside FastAPI's running event loop.
    #
    # Merge strategy (slow_intel fields win on conflict — they are fresher):
    #   intel_snap  → status, risk, causal_signals, degradation_rate, confidence
    #   slow_intel  → velocity, persistence, failure_probability, RUL, recommendations
    if focus_id:
        try:
            intel_snap = _read_intel_snapshot_sync(focus_id)
            if intel_snap:
                if "slow_intel" in ctx:
                    # slow_intel fields take priority (more frequently updated)
                    ctx["slow_intel"] = {**intel_snap, **ctx["slow_intel"]}
                else:
                    ctx["slow_intel"] = intel_snap
        except Exception as exc:
            logger.debug("intel_coordinator snapshot fetch failed: %s", exc)

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

def _fmt_myt(ts_str: str) -> str:
    """Convert ISO UTC timestamp string to MYT for display."""
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d %H:%M MYT")
    except Exception:
        return ts_str[:16].replace("T", " ")


def _detect_domain(ctx: dict, message: str = "") -> tuple:
    """
    Layer 1 — Detect operational domain from device name, telemetry keys, message.
    Returns (domain_label, expert_role).
    """
    msg = message.lower()
    devices = ctx.get("device_list", [])
    device_names = " ".join(d.get("name", "").lower() for d in devices)
    telem = ctx.get("telemetry", {})
    telem_vals = telem.get("values") or telem if isinstance(telem, dict) else {}
    keys = " ".join(str(k).lower() for k in telem_vals.keys())
    combined = f"{device_names} {keys} {msg}"

    # Pump / motor / rotating equipment
    if any(w in combined for w in ["pump", "motor", "velocity", "vibration", "bearing",
                                    "rpm", "compressor", "fan", "gearbox", "turbine"]):
        return "Rotating Equipment / Industrial Machinery", "Rotating Equipment & Reliability Engineer"

    # Energy / power
    if any(w in combined for w in ["power", "kwh", "voltage", "current", "energy",
                                    "solar", "grid", "inverter", "pv", "watt", "frequency"]):
        return "Energy & Power Systems", "Energy Efficiency & Power Systems Analyst"

    # Data center / IT infrastructure
    if any(w in combined for w in ["server", "cpu", "rack", "data_center", "pdu",
                                    "cooling", "it_load", "ups", "datacenter"]):
        return "Data Center Operations", "Data Center Operations Engineer"

    # Water / wastewater / process
    if any(w in combined for w in ["flow", "pressure", "turbidity", "ph", "chlorine",
                                    "water", "valve", "tank", "dosing", "treatment", "level"]):
        return "Water Treatment & Process", "Water Treatment & Process Operations Specialist"

    # Environment / agriculture
    if any(w in combined for w in ["humidity", "soil", "moisture", "co2", "air_quality",
                                    "weather", "crop", "irrigation", "lux", "rainfall"]):
        return "Environmental / Agricultural Monitoring", "Environmental & Agricultural IoT Analyst"

    # Healthcare / biosensors
    if any(w in combined for w in ["glucose", "heart", "spo2", "blood", "pulse", "ecg",
                                    "patient", "gluciq", "bpm", "hba1c", "insulin",
                                    "temperature" if "patient" in combined else "__skip__"]):
        return "Healthcare & Biosensors", "Healthcare Analytics & Biosensor Intelligence Assistant"

    # Fleet / logistics / GPS tracking
    if any(w in combined for w in ["gps", "latitude", "longitude", "speed", "fleet",
                                    "vehicle", "asset_track", "odometer", "fuel"]):
        return "Fleet & Logistics", "Fleet Operations & Asset Tracking Analyst"

    # Wildlife / acoustic / counting
    if any(w in combined for w in ["swiftlet", "bird", "count", "nest", "acoustic",
                                    "wildlife", "animal", "sound_level"]):
        return "Wildlife & Acoustic Monitoring", "Wildlife Analytics & Acoustic Monitoring Specialist"

    # Cold chain / food safety
    if any(w in combined for w in ["cold_chain", "freezer", "chiller", "food_temp",
                                    "haccp", "refriger"]):
        return "Cold Chain / Food Safety", "Cold Chain & Food Safety Monitoring Specialist"

    # Temperature only (generic)
    if any(w in combined for w in ["temperature", "temp"]):
        return "Thermal Monitoring", "Thermal & Environmental Monitoring Analyst"

    return "Industrial IoT", "Industrial IoT Intelligence Agent"


def _detect_user_intent(intent: str, message: str = "") -> str:
    """
    Layer 2 — Map TAAT intent + message keywords to analytical intent type.
    """
    msg = message.lower()
    if intent == "RCA" or any(w in msg for w in ["why", "cause", "reason", "what happened",
                                                   "fault", "explain this", "what caused"]):
        return "ROOT_CAUSE_ANALYSIS"
    if intent == "RECOMMEND" or any(w in msg for w in ["should i", "what to do", "recommend",
                                                         "suggest", "advise", "what action"]):
        return "RECOMMENDATION"
    if any(w in msg for w in ["report", "summary", "weekly", "monthly", "daily report"]):
        return "REPORTING"
    if any(w in msg for w in ["today vs", "yesterday", "compare", "trend", "over time",
                               "history", "behaving", "last 24", "last week"]):
        return "COMPARISON"
    if intent == "FLEET":
        return "FLEET_OVERVIEW"
    if intent == "QUESTION":
        return "STATUS_CHECK"
    return "GENERAL"



def _get_response_template(domain_label: str, user_intent: str) -> str:
    """
    Layer 3 — Return domain-adaptive response template.
    Each domain gets its own analytical framework.
    Only applied for analytical intents (COMPARISON, RCA, RECOMMENDATION, STATUS_CHECK).
    """
    analytical_intents = {"COMPARISON", "ROOT_CAUSE_ANALYSIS", "RECOMMENDATION", "STATUS_CHECK", "FLEET_OVERVIEW"}
    if user_intent not in analytical_intents:
        return ""  # Simple commands/questions use no template

    domain = domain_label.lower()

    # ── Rotating Equipment / Industrial Machinery ──────────────────────────────
    if any(w in domain for w in ["rotating", "machinery", "pump", "motor"]):
        return """
RESPONSE TEMPLATE FOR ROTATING EQUIPMENT:
1. **Overall Condition** — Is the asset stable, degrading, or showing early warning signs?
2. **Key Changes** — Top 3 most significant vibration, temperature, or load changes with physical meaning.
3. **Most Concerning Signal** — Which parameter is highest risk and what it means mechanically (bearing, alignment, imbalance, cavitation).
4. **Health Interpretation** — Explain health score vs anomaly count. Acknowledge contradictions honestly.
5. **Probable Cause** — Use "may indicate" or "suggests". Possible: bearing wear, misalignment, imbalance, lubrication, hydraulic loading.
6. **Risk Level** — LOW / MEDIUM / HIGH based on health score + anomaly count + trend direction.
7. **Recommended Action** — Specific maintenance or monitoring steps. Include vibration thresholds where relevant."""

    # ── Energy & Power Systems ─────────────────────────────────────────────────
    elif any(w in domain for w in ["energy", "power"]):
        return """
RESPONSE TEMPLATE FOR ENERGY & POWER:
1. **Consumption Overview** — Is energy use increasing, decreasing, or stable vs yesterday/baseline?
2. **Key Changes** — Top 3 changes in power, voltage, current, or frequency with operational context.
3. **Efficiency Signal** — Which parameter suggests efficiency gain or loss?
4. **Load Analysis** — Is the load within normal operating range? Any overload or underload indicators?
5. **Probable Cause** — Use "may indicate". Possible: load change, equipment fault, power quality issue, tariff period shift.
6. **Risk Level** — LOW / MEDIUM / HIGH based on deviation from baseline and active alarms.
7. **Recommended Action** — Load balancing, tariff optimisation, fault investigation, or monitoring steps."""

    # ── Data Center Operations ─────────────────────────────────────────────────
    elif any(w in domain for w in ["data center", "datacenter"]):
        return """
RESPONSE TEMPLATE FOR DATA CENTER OPERATIONS:
1. **Infrastructure Status** — Is the rack/system operating within thermal and power design limits?
2. **Key Changes** — Top 3 changes in power consumption, temperature, cooling performance, or IT load.
3. **Thermal Risk** — Are any temperature readings approaching critical thresholds?
4. **Power & Cooling Efficiency** — Any signs of PUE degradation or cooling inefficiency?
5. **Probable Cause** — Use "may indicate". Possible: hotspot formation, cooling failure, increased workload, airflow obstruction.
6. **Risk Level** — LOW / MEDIUM / HIGH based on temperature margins and power headroom.
7. **Recommended Action** — Cooling adjustment, workload migration, airflow review, or maintenance steps."""

    # ── Water Treatment & Process ──────────────────────────────────────────────
    elif any(w in domain for w in ["water", "process", "treatment"]):
        return """
RESPONSE TEMPLATE FOR WATER TREATMENT & PROCESS:
1. **Process Status** — Is the system operating within design parameters?
2. **Key Changes** — Top 3 changes in flow, pressure, pH, turbidity, or chemical dosing.
3. **Most Critical Parameter** — Which reading is closest to alarm threshold and what it means for process quality.
4. **Process Interpretation** — Explain readings in context of treatment process stage.
5. **Probable Cause** — Use "may indicate". Possible: demand change, equipment wear, chemical variation, blockage.
6. **Risk Level** — LOW / MEDIUM / HIGH based on compliance thresholds and process impact.
7. **Recommended Action** — Process adjustment, chemical dosing, inspection, or regulatory notification if required."""

    # ── Healthcare & Biosensors ────────────────────────────────────────────────
    elif any(w in domain for w in ["healthcare", "biosensor", "health"]):
        return """
RESPONSE TEMPLATE FOR HEALTHCARE & BIOSENSORS:
1. **Patient / Subject Overview** — Is the monitored subject showing stable, improving, or concerning trends?
2. **Key Changes** — Top 3 significant changes in glucose, vitals, or biosensor readings with clinical context.
3. **Most Concerning Reading** — Which value is furthest from normal range and its clinical significance.
4. **Pattern Analysis** — Identify any hypoglycaemia, hyperglycaemia, or abnormal vital sign patterns.
5. **Probable Interpretation** — Use "may suggest". Avoid diagnosis. Possible: dietary change, medication effect, physiological variation.
6. **Risk Level** — LOW / MEDIUM / HIGH based on deviation from clinical reference ranges.
7. **Recommended Action** — Monitoring frequency, clinical review trigger thresholds, or alert escalation."""

    # ── Environmental / Agricultural ──────────────────────────────────────────
    elif any(w in domain for w in ["environmental", "agricultural", "thermal"]):
        return """
RESPONSE TEMPLATE FOR ENVIRONMENTAL MONITORING:
1. **Environment Status** — Are conditions within acceptable ranges for the monitored area or crop?
2. **Key Changes** — Top 3 changes in temperature, humidity, CO2, soil moisture, or air quality.
3. **Most Significant Reading** — Which environmental parameter is most deviated from optimal range.
4. **Condition Interpretation** — What the readings mean for crop health, air quality, or environmental compliance.
5. **Probable Cause** — Use "may indicate". Possible: weather change, equipment drift, seasonal variation, external event.
6. **Risk Level** — LOW / MEDIUM / HIGH based on crop/environment impact thresholds.
7. **Recommended Action** — Irrigation adjustment, ventilation control, equipment calibration, or field inspection."""

    # ── Fleet & Logistics ──────────────────────────────────────────────────────
    elif any(w in domain for w in ["fleet", "logistics"]):
        return """
RESPONSE TEMPLATE FOR FLEET & LOGISTICS:
1. **Asset Status** — Is the vehicle/asset operating normally, idling excessively, or showing anomalies?
2. **Key Changes** — Top 3 changes in utilisation, fuel consumption, speed pattern, or location behaviour.
3. **Most Concerning Signal** — Which metric suggests inefficiency, misuse, or maintenance need.
4. **Utilisation Analysis** — Is the asset being used within optimal operational parameters?
5. **Probable Cause** — Use "may indicate". Possible: route change, driver behaviour, mechanical issue, load variation.
6. **Risk Level** — LOW / MEDIUM / HIGH based on operational and maintenance signals.
7. **Recommended Action** — Route optimisation, driver coaching, maintenance scheduling, or geofence review."""

    # ── Wildlife & Acoustic ────────────────────────────────────────────────────
    elif any(w in domain for w in ["wildlife", "acoustic"]):
        return """
RESPONSE TEMPLATE FOR WILDLIFE & ACOUSTIC MONITORING:
1. **Activity Overview** — Is detected activity increasing, decreasing, or showing unusual patterns?
2. **Key Changes** — Top 3 changes in count, sound level, frequency, or activity timing.
3. **Most Significant Signal** — Which reading deviates most from baseline behaviour patterns.
4. **Behavioural Interpretation** — What the data suggests about wildlife activity or habitat conditions.
5. **Probable Cause** — Use "may suggest". Possible: seasonal behaviour, environmental disturbance, equipment drift.
6. **Confidence Level** — LOW / MEDIUM / HIGH based on data quality and sample size.
7. **Recommended Action** — Monitoring frequency adjustment, field verification, or habitat intervention."""

    # ── Generic fallback ───────────────────────────────────────────────────────
    else:
        return """
RESPONSE TEMPLATE:
1. **Overall Status** — Is the asset/system stable, improving, or degrading?
2. **Key Changes** — Top 3 most significant changes with operational context.
3. **Most Concerning Signal** — Highest-risk reading and its operational significance.
4. **Context Interpretation** — What the data means in the operational context.
5. **Probable Cause** — Use "may indicate". List 2-3 possibilities without false certainty.
6. **Risk Level** — LOW / MEDIUM / HIGH with justification.
7. **Recommended Action** — Specific, actionable next steps."""

def build_system_prompt(
    tenant_name: str,
    intent: str,
    ctx: dict,
    current_user,
    confirm_mode: bool = False,
    message: str = "",
) -> str:
    """
    Build the system prompt sent to the LLM with full intelligence context injected.
    Uses layered domain detection and dynamic expert persona.
    """
    role = getattr(current_user, "role", "TENANT_USER")
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        now = _dt.now(_ZI("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d %H:%M MYT")
    except Exception:
        now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Layer 1, 2 & 3 — detect domain, user intent, and response template
    domain_label, expert_role = _detect_domain(ctx, message)
    user_intent = _detect_user_intent(intent, message)
    response_template = _get_response_template(domain_label, user_intent)

    # ── Device list ────────────────────────────────────────────────────────────
    device_lines = "\n".join(
        (
            f"  - {d['name']} [{d.get('status','?')}]"
            + (f" | {d['label']}" if d.get('label') else "")
            + (f" | lat:{float(d['latitude']):.4f} lng:{float(d['longitude']):.4f}"
               if d.get('latitude') is not None and d.get('longitude') is not None else "")
            + (f" | last seen: {_fmt_myt(d['last_seen_at'])}" if d.get('last_seen_at') else "")
        )
        for d in ctx.get("device_list", [])
    ) or "  None"

    # ── Alarms ─────────────────────────────────────────────────────────────────
    alarm_lines = "\n".join(
        f"  - {a.get('alarm_type','alarm')} on {a.get('device_name','?')} — {a.get('severity','?')}"
        for a in ctx.get("active_alarms", [])
    ) or "  None"

    # ── Memory ─────────────────────────────────────────────────────────────────
    all_memories  = ctx.get("memory", {}).get("memories", [])
    semantic_mem  = [m for m in all_memories if m.get("type") == "semantic"]
    dispatches    = [m for m in all_memories if m.get("type") == "scheduled_dispatch"]
    other_mem     = [m for m in all_memories if m.get("type") not in ("semantic", "scheduled_dispatch")]

    dispatch_section = ""
    if dispatches:
        dispatch_lines   = "\n".join(f"  ⏰ {m['content']}" for m in dispatches[:3])
        dispatch_section = f"\nRECENT SCHEDULED ACTIONS EXECUTED:\n{dispatch_lines}"

    semantic_section = ""
    if semantic_mem:
        sem_lines        = "\n".join(f"  📌 {m['content']}" for m in semantic_mem[:10])
        semantic_section = f"\nINFRASTRUCTURE KNOWLEDGE (persistent):\n{sem_lines}"

    memory_lines = "\n".join(
        f"  [{m['type']}] {m['content']}" for m in other_mem
    ) or "  None"

    # ── Current telemetry ──────────────────────────────────────────────────────
    telem_section = ""
    if "telemetry" in ctx:
        vals = ctx["telemetry"].get("values", {})
        if vals:
            telem_section = "\nCURRENT TELEMETRY:\n" + "\n".join(
                f"  {k}: {v}" for k, v in list(vals.items())[:15]
            )

    # ── Intelligence section — rich structured context ─────────────────────────
    intel_section = ""

    # Health score with full breakdown
    if "health" in ctx:
        h = ctx["health"]
        intel_section += f"\n\nHEALTH SCORE: {h.get('health_score','?')}/100 — {h.get('health_label','?')}"
        comps = h.get("components", {})
        if comps:
            intel_section += (
                f"\n  Components — uptime: {comps.get('uptime','?')} | "
                f"alarm: {comps.get('alarm','?')} | "
                f"stability: {comps.get('stability','?')} | "
                f"freshness: {comps.get('freshness','?')}"
            )
        if h.get("maintenance_due"):
            intel_section += f"\n  ⚠️ MAINTENANCE DUE: {h.get('maintenance_reason','')}"
        if h.get("predicted_failure_hrs"):
            intel_section += f"\n  ⚠️ PREDICTED FAILURE IN: {h['predicted_failure_hrs']} hours"

    # Anomaly summary with recent examples
    if "anomalies" in ctx:
        a     = ctx["anomalies"]
        count = a.get("anomaly_count", 0)
        if count > 0:
            intel_section += f"\n\nANOMALY SUMMARY: {count} anomalies detected (last 24h)"
            intel_section += f"\n  Most anomalous key: {a.get('most_anomalous_key','?')}"
            recent = a.get("recent_anomalies", [])[:3]
            for r in recent:
                intel_section += (
                    f"\n  - {r.get('key','?')}: value={r.get('value','?')} "
                    f"z-score={r.get('z_score','?')} "
                    f"(baseline mean={r.get('mean','?')})"
                )
        else:
            intel_section += "\n\nANOMALY SUMMARY: No anomalies detected in last 24h"

    # Baseline with per-key values
    if "baseline" in ctx:
        bl   = ctx["baseline"]
        bkeys = bl.get("keys", {})
        if bl.get("status") == "active" and bkeys:
            intel_section += f"\n\nBASELINE (normal operating ranges, current hour):"
            for k, v in list(bkeys.items())[:8]:
                if isinstance(v, dict):
                    intel_section += (
                        f"\n  {k}: mean={v.get('mean','?')} "
                        f"stddev={v.get('stddev','?')} "
                        f"range=[{v.get('min','?')} — {v.get('max','?')}]"
                    )
        else:
            intel_section += f"\n\nBASELINE: {bl.get('status','learning')} — building historical norms"

    # Key intelligence
    if "key_intel" in ctx:
        ki = ctx["key_intel"]
        intel_section += (
            f"\n\nKEY INTELLIGENCE ({ki.get('key','?')}):"
            f"\n  value={ki.get('value')} {ki.get('unit','')}"
            f"\n  status={ki.get('status')} | risk={ki.get('risk')}"
            f"\n  reason: {ki.get('reason','')}"
            f"\n  recommended action: {ki.get('recommended_action','')}"
        )

    # Slow loop / intelligence coordinator snapshot
    if "slow_intel" in ctx:
        si = ctx["slow_intel"]
        if si:
            intel_section += f"\n\nINTELLIGENCE SNAPSHOT:"
            if si.get("status"):
                intel_section += f"\n  status: {si['status']}"
            if si.get("risk"):
                intel_section += f" | risk: {si['risk']}"
            if si.get("degradation_rate") is not None:
                intel_section += f"\n  degradation rate: {si['degradation_rate']}"
            if si.get("failure_probability") is not None:
                intel_section += f"\n  failure probability: {si['failure_probability']}"
            if si.get("rul_days") is not None:
                intel_section += f"\n  estimated RUL: {si['rul_days']} days"
            causal = si.get("causal_signals") or si.get("recommendations") or []
            if causal:
                intel_section += f"\n  signals: {', '.join(str(c) for c in causal[:4])}"

    # Last RPC
    if "rpc_history" in ctx:
        rh   = ctx["rpc_history"]
        cmds = rh.get("commands") or rh.get("history") or rh.get("results") or []
        if rh.get("count", 0) > 0 and cmds:
            last = cmds[0]
            intel_section += (
                f"\n\nLAST RPC: {last.get('method','?')} "
                f"{last.get('params',{})} -> {last.get('status','?')}"
            )

    if ctx.get("decision_summary"):
        intel_section += f"\n\nDECISION ENGINE: {ctx['decision_summary']}"

    # Daily comparison — top 4 keys
    if "daily_comparison" in ctx:
        dc      = ctx["daily_comparison"]
        all_cmp = dc.get("all_comparisons") or []
        cmp_str = dc.get("comparison", "")
        if all_cmp:
            intel_section += "\n\nDAILY COMPARISON (today vs yesterday):"
            for _line in all_cmp:
                intel_section += f"\n  {_line}"
        elif cmp_str and cmp_str != "no data":
            intel_section += f"\n\nDAILY COMPARISON: {cmp_str}"

    intel_section += dispatch_section
    intel_section += semantic_section

    # ── Role capabilities ──────────────────────────────────────────────────────
    if role == "CUSTOMER_USER":
        capabilities = "\nYour role: READ-ONLY. You can answer questions and show reports only."
    elif role == "TENANT_USER":
        capabilities = "\nYour role: Can send RPC commands, ack/clear alarms. Cannot manage users or delete all rules."
    else:
        capabilities = (
            "\nCapabilities: RPC commands · alarm actions · rule management · user management · reports"
            "\nFor HIGH-risk actions (delete all, turn off all): ask for confirmation first."
        )

    confirm_note = ""
    if confirm_mode:
        confirm_note = "\n\nCONFIRM MODE: User confirmed a pending HIGH-risk action. Execute it now and confirm with ✅."

    return f"""You are TAAT — the autonomous IoT intelligence agent for {tenant_name}.

DETECTED DOMAIN: {domain_label}
ADOPTED EXPERT ROLE: {expert_role}
USER INTENT: {user_intent}
SESSION TIME: {now}

You are not just a data reporter. You reason like the expert role above.
You interpret sensor data, trends, anomalies, and health signals in the context of the detected domain.
You adapt your language and reasoning to the industry — do not use maintenance terminology for healthcare, or clinical terminology for industrial equipment.

DEVICES ({len(ctx.get('device_list', []))}):
{device_lines}

ACTIVE ALARMS:
{alarm_lines}
{telem_section}
{intel_section}

AGENT MEMORY:
{memory_lines}
{capabilities}
{confirm_note}

RESPONSE RULES:
1. Only report facts from the data above — never invent values or failure probabilities.
2. For executed actions, confirm with ✅ and one line of detail.
3. For HIGH-risk actions, say: "⚠️ This will [description]. Reply 'proceed' to confirm."
4. If you cannot find a device or key, say so clearly — do not guess.
5. For analytical questions (comparison, status, RCA, recommendations) — follow the domain-specific response template below exactly.
   For simple commands or yes/no questions — skip the template and be direct.
{response_template}

6. For simple commands, yes/no questions, or RPC actions — skip the structure, be direct and brief.
7. Do not say "sharp spike" unless trend data explicitly shows SPIKE. Do not invent standards.
8. If anomaly_count is high but health_score appears good, acknowledge the contradiction explicitly.
9. If DAILY COMPARISON is present, include the top changes with physical interpretation.
10. If RECENT SCHEDULED ACTIONS EXECUTED is present, inform the user at the start of your reply.
11. Format scheduled commands as readable text — never raw JSON.
12. If AGENT MEMORY shows a recent outcome for the same action, mention it first.
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

    # Normalise common spacing in key names before passing to Groq
    # e.g. "led 2" → "led2", "relay 1" → "relay1"
    import re as _re
    normalised_msg = _re.sub(r'\b(led|relay|pump|fan|motor|valve|gpio|pin)\s+(\d+)\b',
                             lambda m: m.group(1) + m.group(2), message, flags=_re.IGNORECASE)

    prompt = f"""Extract RPC command from: "{normalised_msg}"
Devices: {json.dumps(device_names)}
IMPORTANT: Key names have NO spaces — "led2" not "led 2", "relay1" not "relay 1".
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

    # Fast path UPDATE — change/update/modify + key + number
    import re as _re
    if any(w in msg_lower for w in ["change", "update", "modify", "change the", "update the"]):
        for rule in rules:
            rkey = rule.get("key", "") if isinstance(rule, dict) else str(rule)
            if rkey.lower() in msg_lower:
                nums = _re.findall(r'[0-9]+\.?[0-9]*', message)
                if nums:
                    dn = next((d["name"] for d in devices if d["name"].lower() in msg_lower), None)
                    return {"action": "update", "key": rkey, "device_name": dn, "threshold": float(nums[0])}

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

# ── Intelligence snapshot sync reader ─────────────────────────────────────────

def _read_intel_snapshot_sync(device_id: str) -> Optional[dict]:
    """
    Read the IntelligenceCoordinator Redis snapshot synchronously.
    Uses redis-py (blocking) so it's safe to call from a sync function
    inside FastAPI's async context without bridging event loops.

    Returns None if Redis is unavailable or no snapshot exists yet.
    """
    try:
        import json
        import os
        import redis

        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return None

        r = redis.from_url(redis_url, decode_responses=True,
                           socket_connect_timeout=1, socket_timeout=1)
        raw = r.get(f"iot:snapshot:{device_id}")
        r.close()
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None
