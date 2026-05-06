"""
app/services/taat_decision_engine.py — Decision Engine

Merges execution results + verification results into a single
structured decision that the LLM uses to generate its reply.

The LLM never decides status/risk — this function does it
deterministically from real data. The LLM only narrates the decision.

Output contract:
{
    "status":       "NORMAL" | "WARNING" | "CRITICAL" | "UNKNOWN",
    "risk":         "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
    "reason":       str,
    "action_taken": str | None,
    "verified":     bool,
    "confidence":   float   # 0.0 – 1.0
}
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_decision(
    execution_results: dict,
    verification_results: Optional[dict] = None,
    intent: str = "QUESTION",
) -> dict:
    """
    Merge execution + verification into a structured decision.

    execution_results:   trace.results dict from executor
    verification_results: VerificationResult.__dict__ or None
    intent:              classified intent string
    """
    status     = "UNKNOWN"
    risk       = "LOW"
    reason     = ""
    action_taken = None
    verified   = False
    confidence = 0.5

    # ── 1. Read key intelligence if available ─────────────────────────────────
    ki = execution_results.get("key_intel") or {}
    if ki:
        status     = _map_status(ki.get("status", "UNKNOWN"))
        risk       = _map_risk(ki.get("risk", "LOW"))
        reason     = ki.get("reason", "")
        confidence = _confidence_from_ki(ki)

    # ── 2. Read health if no key intel ────────────────────────────────────────
    elif "health" in execution_results:
        h = execution_results["health"]
        score = h.get("health_score") or h.get("score")
        label = h.get("health_label", "HEALTHY")
        status, risk = _health_to_status_risk(score, label)
        reason = f"Health score: {score:.0f} ({label})" if score is not None else label
        confidence = 0.7

    # ── 3. Read alarms if available ───────────────────────────────────────────
    alarms = execution_results.get("alarms", {})
    alarm_count = alarms.get("count", 0)
    if alarm_count > 0 and status in ("UNKNOWN", "NORMAL"):
        highest = alarms.get("highest_severity", "WARNING")
        status  = "CRITICAL" if highest in ("CRITICAL", "MAJOR") else "WARNING"
        risk    = "HIGH"     if highest in ("CRITICAL", "MAJOR") else "MEDIUM"
        reason  = f"{alarm_count} active alarm(s), highest: {highest}"
        confidence = 0.85

    # ── 4. RPC action result ──────────────────────────────────────────────────
    rpc = execution_results.get("rpc_result") or {}
    if rpc:
        if rpc.get("success"):
            dev  = rpc.get("device_name", "device")
            prms = rpc.get("params", {})
            action_taken = f"Sent command to {dev}: {prms}"
        else:
            action_taken = f"Command failed: {rpc.get('reason', 'unknown error')}"

    # ── 5. Rule action result ──────────────────────────────────────────────────
    rule = execution_results.get("rule_result") or {}
    if rule and not action_taken:
        if rule.get("success") or rule.get("rule_id") or rule.get("deleted"):
            op   = "Created" if rule.get("rule_id") else "Deleted"
            key  = rule.get("key", "")
            thr  = rule.get("threshold", "")
            action_taken = f"{op} rule: {key} {thr}".strip()

    # ── 6. Alarm action result ─────────────────────────────────────────────────
    alarm_action = execution_results.get("alarm_result") or {}
    if alarm_action and not action_taken:
        count  = alarm_action.get("count", 0)
        op     = alarm_action.get("action", "actioned")
        action_taken = f"{op.capitalize()} {count} alarm(s)"

    # ── 7. Verification overlay ────────────────────────────────────────────────
    if verification_results:
        verified   = verification_results.get("verified", False)
        ver_msg    = verification_results.get("message", "")
        skipped    = verification_results.get("skipped", False)

        if not skipped:
            if verified:
                confidence = min(confidence + 0.15, 1.0)
                if action_taken:
                    action_taken = f"{action_taken} — confirmed ✅"
            else:
                confidence = max(confidence - 0.1, 0.1)
                risk = _escalate_risk(risk)
                if action_taken and ver_msg:
                    action_taken = f"{action_taken} — {ver_msg}"
        else:
            verified = True   # skipped = can't verify = assume ok

    # ── 8. Fallback defaults ──────────────────────────────────────────────────
    if not reason:
        telem = execution_results.get("telemetry", {})
        val_count = len(telem.get("values", {}))
        if val_count:
            reason = f"Device reporting {val_count} telemetry key(s)"
            status = "NORMAL"
            risk   = "LOW"
            confidence = 0.6
        else:
            reason = "No data available"
            status = "UNKNOWN"
            confidence = 0.3

    return {
        "status":       status,
        "risk":         risk,
        "reason":       reason,
        "action_taken": action_taken,
        "verified":     verified,
        "confidence":   round(confidence, 2),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _map_status(s: str) -> str:
    return s if s in ("NORMAL", "WARNING", "CRITICAL", "UNKNOWN") else "UNKNOWN"


def _map_risk(r: str) -> str:
    return r if r in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else "LOW"


def _escalate_risk(r: str) -> str:
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    idx   = order.index(r) if r in order else 0
    return order[min(idx + 1, len(order) - 1)]


def _confidence_from_ki(ki: dict) -> float:
    """Higher confidence when we have baseline + anomaly data."""
    base = 0.5
    if ki.get("baseline_status") == "active":
        base += 0.2
    if ki.get("anomaly_z_score") is not None:
        base += 0.1
    if ki.get("trend") not in (None, "UNKNOWN"):
        base += 0.1
    return min(base, 1.0)


def _health_to_status_risk(score, label: str):
    if score is None:
        return "UNKNOWN", "LOW"
    if score >= 80 or label == "HEALTHY":
        return "NORMAL", "LOW"
    if score >= 50 or label == "WARNING":
        return "WARNING", "MEDIUM"
    return "CRITICAL", "HIGH"


def build_decision(
    intent:       str,
    plan:         "Plan",
    trace:        "ExecutionTrace",
    verification: dict,
) -> dict:
    """
    New signature — takes full context objects.
    Wraps the original build_decision logic, adding plan + trace awareness.
    """
    # Pull structured data from trace
    results = trace.results if hasattr(trace, "results") else {}
    ver     = verification  or {}

    base = _build_decision_from_results(intent, results, ver)

    # Overlay plan metadata
    base["plan_intent"] = intent
    base["steps_run"]   = len(trace.steps) if hasattr(trace, "steps") else 0
    base["all_success"] = trace.all_success if hasattr(trace, "all_success") else True
    base["plan_risk"]   = plan.risk if hasattr(plan, "risk") else base.get("risk", "LOW")

    # Risk escalation: if plan declared HIGH, honour it
    if base["plan_risk"] == "HIGH":
        base["risk"] = "HIGH"

    # Write decision_summary back onto trace for observability (Task 2)
    if hasattr(trace, "decision_summary"):
        trace.decision_summary = summarize_decision(base)

    return base


def _build_decision_from_results(intent: str, results: dict, verification: dict) -> dict:
    """Core logic — same as original build_decision but extracted for reuse."""
    status     = "UNKNOWN"
    risk       = "LOW"
    reason     = ""
    action_taken = None
    verified   = verification.get("verified", False)
    confidence = 0.5

    ki = results.get("key_intel") or {}
    if ki:
        status     = _map_status(ki.get("status", "UNKNOWN"))
        risk       = _map_risk(ki.get("risk", "LOW"))
        reason     = ki.get("reason", "")
        confidence = _confidence_from_ki(ki)
    elif "health" in results:
        h = results["health"]
        score = h.get("health_score") or h.get("score")
        label = h.get("health_label", "HEALTHY")
        status, risk = _health_to_status_risk(score, label)
        reason = f"Health score: {score:.0f} ({label})" if score is not None else label
        confidence = 0.7

    alarms = results.get("alarms", {})
    if alarms.get("count", 0) > 0 and status in ("UNKNOWN", "NORMAL"):
        highest = alarms.get("highest_severity", "WARNING")
        status  = "CRITICAL" if highest in ("CRITICAL", "MAJOR") else "WARNING"
        risk    = "HIGH"     if highest in ("CRITICAL", "MAJOR") else "MEDIUM"
        reason  = f"{alarms['count']} active alarm(s), highest: {highest}"
        confidence = 0.85

    rpc = results.get("rpc_result") or {}
    if rpc:
        dev  = rpc.get("device_name", "device")
        prms = rpc.get("params", {})
        action_taken = f"Sent command to {dev}: {prms}" if rpc.get("success") else                        f"Command failed: {rpc.get('reason', 'unknown')}"

    rule = results.get("rule_result") or {}
    if rule and not action_taken:
        op = "Created" if rule.get("rule_id") else "Deleted"
        action_taken = f"{op} rule: {rule.get('key', '')} {rule.get('threshold', '')}".strip()

    alarm_action = results.get("alarm_result") or {}
    if alarm_action and not action_taken:
        action_taken = f"{alarm_action.get('action','actioned').capitalize()} {alarm_action.get('count',0)} alarm(s)"

    ver_msg = verification.get("message", "")
    if verified:
        confidence = min(confidence + 0.15, 1.0)
        if action_taken:
            action_taken = f"{action_taken} — confirmed ✅"
    elif not verification.get("overall") in (None, "skipped"):
        confidence = max(confidence - 0.1, 0.1)
        risk = _escalate_risk(risk)
        if action_taken and ver_msg:
            action_taken = f"{action_taken} — {ver_msg}"

    if not reason:
        val_count = len(results.get("telemetry", {}).get("values", {}))
        if val_count:
            reason = f"Device reporting {val_count} telemetry key(s)"
            status, risk = "NORMAL", "LOW"
            confidence   = 0.6
        else:
            reason     = "No data available"
            status     = "UNKNOWN"
            confidence = 0.3

    return {
        "status":       status,
        "risk":         risk,
        "reason":       reason,
        "action_taken": action_taken,
        "verified":     verified,
        "confidence":   round(confidence, 2),
    }


def summarize_decision(decision: dict) -> str:
    """
    One-line summary for injection into the Groq system prompt.
    The LLM narrates this — it never determines status itself.
    """
    parts = [
        f"STATUS: {decision.get('status', 'UNKNOWN')}",
        f"RISK: {decision.get('risk', 'LOW')}",
        f"CONFIDENCE: {decision.get('confidence', 0.5)}",
        f"REASON: {decision.get('reason', '')}",
    ]
    if decision.get("action_taken"):
        parts.append(f"ACTION: {decision['action_taken']}")
    if "verified" in decision:
        parts.append(f"VERIFIED: {decision['verified']}")
    return " | ".join(parts)


def build_failure_decision(trace: "ExecutionTrace") -> dict:
    """
    Called when trace.errors is non-empty.
    Returns a structured failure decision without hallucinating success.
    """
    errors     = getattr(trace, "errors", [])
    steps_run  = len(getattr(trace, "steps", []))
    failed     = [s for s in getattr(trace, "steps", []) if not s.success]
    failed_tools = [s.tool for s in failed]

    reason = f"{len(errors)} step(s) failed: {'; '.join(errors[:3])}"
    if len(errors) > 3:
        reason += f" (+{len(errors)-3} more)"

    return {
        "status":       "UNKNOWN",
        "risk":         "MEDIUM",
        "reason":       reason,
        "action_taken": f"Execution failed on: {', '.join(failed_tools)}" if failed_tools else "Execution failed",
        "verified":     False,
        "confidence":   0.1,
        "failure":      True,
        "errors":       errors,
        "steps_run":    steps_run,
    }
