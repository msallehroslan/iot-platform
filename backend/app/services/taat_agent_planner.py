"""
app/services/taat_agent_planner.py — Multi-Step Planner

Converts a classified intent + context into an ordered list of
tool steps for the executor to run.

A Plan is a list of Steps. Each Step names a tool and its args.
The executor runs them in order, passing results forward.

Principles:
- Plans are deterministic for common intents (no Groq needed)
- Groq is called only for complex RCA / RECOMMEND where reasoning matters
- Every plan is minimal — only the steps actually needed
- Plans declare their risk level so the safety guard can intercept

Supported plan types:
    QUESTION       → read-only steps (telemetry, health, alarms)
    DEVICE_CONTROL → get_key_intelligence → send_rpc → verify
    SCHEDULE       → (handled by scheduled_rpc_service, no plan needed)
    ALARM          → get_alarms → ack/clear_alarm
    RULE           → get existing rules → create/delete_rule
    RCA            → telemetry + anomalies + baseline + key_intel + memory
    RECOMMEND      → all intel layers → propose actions
    USER           → (direct execution, no plan needed)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Plan data structures ──────────────────────────────────────────────────────

@dataclass
class Step:
    """A single tool call in a plan."""
    tool:                   str                        # tool name from registry
    args:                   Dict[str, Any]             = field(default_factory=dict)
    label:                  str                        = ""
    output_key:             Optional[str]              = None
    requires_verification:  bool                       = False  # True → verify_actions() checks telemetry delta


@dataclass
class Plan:
    """Ordered sequence of tool calls for a single user request."""
    intent:     str
    steps:      List[Step]             = field(default_factory=list)
    risk:       str                    = "LOW"     # LOW | MEDIUM | HIGH
    summary:    str                    = ""        # for logging / chip display


# ── Planner ───────────────────────────────────────────────────────────────────

def make_plan(
    intent:    str,
    ctx:       dict,
    action:    Optional[dict],
    message:   str = "",
    device_id: Optional[str] = None,
) -> Plan:
    """
    Build an execution plan from intent + context.

    ctx keys available:
        device_list, active_alarms, telemetry, health, anomalies,
        baseline, rpc_history, existing_rules, users, key_intel, memory

    action: extracted action dict from taat_planner.extract_action()
    """
    # Pick focus device
    focus_id = device_id
    if not focus_id and len(ctx.get("device_list", [])) == 1:
        focus_id = ctx["device_list"][0]["id"]

    fn = _PLAN_BUILDERS.get(intent, _plan_question)
    plan = fn(ctx, action, message, focus_id)
    logger.info("plan.built intent=%s steps=%d risk=%s", intent, len(plan.steps), plan.risk)
    return plan


# ── Plan builders ─────────────────────────────────────────────────────────────

def _plan_question(ctx, action, message, device_id) -> Plan:
    """Read-only: fetch what's needed to answer the question."""
    steps = []
    msg = message.lower()

    # Always include device list
    steps.append(Step("get_devices", {}, "Fetch device list"))

    if device_id:
        steps.append(Step("get_telemetry",  {"device_id": device_id}, "Fetch latest telemetry",  "telemetry"))
        steps.append(Step("get_alarms",     {"device_id": device_id}, "Fetch active alarms",     "alarms"))

        if any(w in msg for w in ["health", "score", "status"]):
            steps.append(Step("get_health",   {"device_id": device_id}, "Fetch health score", "health"))

        if any(w in msg for w in ["anomaly", "anomalies", "unusual", "strange"]):
            steps.append(Step("get_anomalies", {"device_id": device_id}, "Fetch anomalies", "anomalies"))

        if any(w in msg for w in ["baseline", "normal", "typical", "range"]):
            steps.append(Step("get_baseline",  {"device_id": device_id}, "Fetch baseline", "baseline"))

        # If asking about a specific key, enrich it
        key = _find_key_in_message(message, ctx)
        if key:
            steps.append(Step(
                "get_key_intelligence",
                {"device_id": device_id, "key": key},
                f"Enrich {key}",
                "key_intel",
            ))

    return Plan(intent="QUESTION", steps=steps, risk="LOW",
                summary="Fetch device status and telemetry")


def _plan_device_control(ctx, action, message, device_id) -> Plan:
    """
    get_key_intelligence (verify current state) → send_rpc → [verify]
    """
    if not action or not action.get("device_name"):
        return _plan_question(ctx, action, message, device_id)

    device_name = action["device_name"]
    method      = action.get("method", "set")
    params      = action.get("params", {})
    key         = next(iter(params.keys()), "") if params else ""

    steps = []

    # Step 1: Read current state before acting
    if device_id and key:
        steps.append(Step(
            "get_key_intelligence",
            {"device_id": device_id, "key": key},
            f"Read current {key} state",
            "pre_state",
        ))

    # Step 2: Send the command
    steps.append(Step(
        tool                  = "send_rpc",
        args                  = {"device_name": device_name, "method": method, "params": params},
        label                 = f"Send {method} {params} to {device_name}",
        output_key            = "rpc_result",
        requires_verification = True,   # verify telemetry delta after RPC
    ))

    return Plan(
        intent="DEVICE_CONTROL",
        steps=steps,
        risk="MEDIUM",
        summary=f"Control {device_name}: {params}",
    )


def _plan_alarm(ctx, action, message, device_id) -> Plan:
    """get_alarms → ack or clear"""
    if not action:
        # Just listing alarms
        steps = [Step("get_alarms", {"device_id": device_id or ""}, "Fetch active alarms", "alarms")]
        return Plan(intent="ALARM", steps=steps, risk="LOW", summary="List active alarms")

    op       = action.get("action", "ack")
    severity = action.get("severity")
    tool     = "clear_alarm" if op == "clear" else "ack_alarm"

    steps = [
        Step("get_alarms", {"device_id": device_id}, "Fetch current alarms before action", "alarms"),
        Step(tool, {"device_id": device_id, "severity": severity}, f"{op} alarms", "alarm_result"),
    ]
    return Plan(intent="ALARM", steps=steps, risk="MEDIUM",
                summary=f"{op} alarms{' — ' + severity if severity else ''}")


def _plan_rule(ctx, action, message, device_id) -> Plan:
    """
    For create: just create_rule.
    For delete all: HIGH risk.
    For delete/update single: MEDIUM.
    """
    if not action:
        return Plan(intent="RULE", steps=[], risk="LOW", summary="List rules")

    op = action.get("action", "create")

    if op == "delete" and action.get("delete_all"):
        steps = [
            Step("delete_rule", {"delete_all": True}, "Delete all rules", "rule_result")
        ]
        return Plan(intent="RULE", steps=steps, risk="HIGH",
                    summary="Delete ALL threshold rules")

    if op == "delete":
        steps = [
            Step("delete_rule", {"key": action.get("key")}, f"Delete rule: {action.get('key')}", "rule_result")
        ]
        return Plan(intent="RULE", steps=steps, risk="MEDIUM",
                    summary=f"Delete rule for key '{action.get('key')}'")

    if op in ("create", "update"):
        devices = ctx.get("device_list", [])
        steps = [
            Step("create_rule", {
                "devices":     devices,
                "key":         action.get("key", "value"),
                "condition":   action.get("condition", "gt"),
                "threshold":   float(action.get("threshold", 0)),
                "severity":    action.get("severity", "WARNING"),
                "device_name": action.get("device_name"),
            }, f"Create rule: {action.get('key')} {action.get('condition')} {action.get('threshold')}", "rule_result"),
        ]
        return Plan(intent="RULE", steps=steps, risk="MEDIUM",
                    summary=f"Create alarm rule for '{action.get('key')}'")

    return Plan(intent="RULE", steps=[], risk="LOW", summary="Rule action")


def _plan_rca(ctx, action, message, device_id) -> Plan:
    """
    Full intelligence read: telemetry + anomalies + baseline + key_intel + memory.
    All reads — no writes.
    """
    steps = [
        Step("get_devices", {}, "Fetch device list"),
    ]
    if device_id:
        steps += [
            Step("get_telemetry",  {"device_id": device_id}, "Fetch latest telemetry",  "telemetry"),
            Step("get_anomalies",  {"device_id": device_id, "hours": 48}, "Fetch anomalies (48h)", "anomalies"),
            Step("get_baseline",   {"device_id": device_id}, "Fetch baseline norms",    "baseline"),
            Step("get_health",     {"device_id": device_id}, "Fetch health score",      "health"),
            Step("get_alarms",     {"device_id": device_id}, "Fetch active alarms",     "alarms"),
            Step("get_memory",     {"device_id": device_id}, "Fetch device memory",     "memory"),
        ]
        # Try to enrich the most anomalous key
        most_anom = ctx.get("anomalies", {}).get("most_anomalous_key")
        if most_anom:
            steps.append(Step(
                "get_key_intelligence",
                {"device_id": device_id, "key": most_anom},
                f"Enrich anomalous key: {most_anom}",
                "key_intel",
            ))

    return Plan(intent="RCA", steps=steps, risk="LOW",
                summary="Root cause analysis — full intelligence read")


def _plan_recommend(ctx, action, message, device_id) -> Plan:
    """Same as RCA but framed for recommendation output."""
    plan = _plan_rca(ctx, action, message, device_id)
    plan.intent  = "RECOMMEND"
    plan.summary = "Autonomous recommendation — full intelligence read"
    return plan


# ── Helper ────────────────────────────────────────────────────────────────────

def _find_key_in_message(message: str, ctx: dict) -> Optional[str]:
    """Find a telemetry key name mentioned in the message."""
    msg = message.lower()
    telemetry = ctx.get("telemetry", {}).get("values", {})
    for k in telemetry:
        if k.lower() in msg:
            return k
    return None


# ── Dispatch table ────────────────────────────────────────────────────────────

_PLAN_BUILDERS = {
    "QUESTION":       _plan_question,
    "DEVICE_CONTROL": _plan_device_control,
    "ALARM":          _plan_alarm,
    "RULE":           _plan_rule,
    "RCA":            _plan_rca,
    "RECOMMEND":      _plan_recommend,
}
