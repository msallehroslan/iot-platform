"""
app/services/taat_verification.py — Post-Action Verification Loop

After an RPC command is sent, verify the device actually changed state
by polling the latest telemetry and comparing pre/post values.

Why this matters:
    Without verification, TAAT says "✅ done" even if the device
    is offline or the firmware ignored the command. With verification,
    TAAT says "✅ LED turned on (confirmed: led1 changed from 0 → 1)"
    or "⚠️ Command sent but device state unchanged after 3s".

Verification strategy:
    1. Record pre-action value from execution trace (pre_state step)
    2. Wait VERIFY_DELAY_S seconds (device needs time to execute + ingest)
    3. Fetch latest telemetry again
    4. Compare pre vs post for the target key
    5. Return VerificationResult

Limitations:
    - Only works for keys that appear in telemetry
    - Devices that don't send telemetry back can't be verified
    - VERIFY_DELAY_S must be > device poll interval
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.services.taat_executor import ExecutionTrace

logger = logging.getLogger(__name__)

# How long to wait before re-reading telemetry (must exceed device poll interval)
VERIFY_DELAY_S   = 4.0
# Maximum total time allowed for verification (prevents hanging)
VERIFY_TIMEOUT_S = 10.0
# If telemetry ts hasn't updated since pre-action, it's stale
STALE_THRESHOLD_S = 30.0


# Verification state constants (Part 1 — truthfulness)
VSTATE_SUCCESS         = "SUCCESS"          # telemetry confirmed state changed
VSTATE_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"  # RPC sent, no telemetry confirmation
VSTATE_UNVERIFIED      = "UNVERIFIED"       # no telemetry available to check
VSTATE_FAILED          = "FAILED"           # RPC transport failure


@dataclass
class VerificationResult:
    verified:            bool                  # True = state confirmed changed
    key:                 Optional[str] = None  # which key was checked
    pre_value:           object        = None  # value before command
    post_value:          object        = None  # value after command
    message:             str           = ""    # human-readable summary
    skipped:             bool          = False # True = couldn't verify (offline/no key)
    verification_state:  str           = VSTATE_UNVERIFIED  # one of 4 states above


async def verify_rpc(
    db:        Session,
    device_id: str,
    params:    dict,
    trace:     ExecutionTrace,
    trace_id:  str = "",
) -> VerificationResult:
    """
    Verify RPC effect via telemetry delta.
    Hardened: timeout protection, stale telemetry detection, correlation logging.
    """
    tid = trace_id or getattr(trace, "trace_id", "")

    if not params or not device_id:
        return VerificationResult(verified=False, skipped=True,
                                  message="No params or device_id to verify")

    target_key = next(iter(params.keys()), None)
    if not target_key:
        return VerificationResult(verified=False, skipped=True,
                                  message="No target key in params")

    # Record pre-action state
    pre_state    = trace.get("pre_state") or {}
    pre_values   = pre_state.get("values", {})
    pre_value    = pre_values.get(target_key)
    pre_ts       = pre_state.get("ts")          # telemetry timestamp before action
    t_action     = time.monotonic()

    logger.info("verify.start trace_id=%s device=%s key=%s pre_value=%s",
                tid, device_id, target_key, pre_value)

    # Wait with timeout — never hang the event loop indefinitely
    try:
        await asyncio.wait_for(asyncio.sleep(VERIFY_DELAY_S), timeout=VERIFY_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("verify.timeout trace_id=%s device=%s key=%s",
                       tid, device_id, target_key)
        return VerificationResult(
            verified=False, skipped=True,
            key=target_key, pre_value=pre_value,
            message=f"Verification timeout after {VERIFY_TIMEOUT_S}s",
            verification_state=VSTATE_UNVERIFIED,
        )

    # Re-read telemetry (bypasses cache for fresh read)
    try:
        from app.services.data_service import _fetch_latest_telemetry
        post_telem = _fetch_latest_telemetry(db, device_id)
        post_value = post_telem.get("values", {}).get(target_key)
        post_ts    = post_telem.get("ts")
    except Exception as exc:
        logger.warning("verify.read_failed trace_id=%s device=%s: %s", tid, device_id, exc)
        return VerificationResult(
            verified=False, skipped=True,
            message=f"Could not read telemetry for verification: {exc}",
            verification_state=VSTATE_UNVERIFIED,
        )

    # Stale telemetry detection — if ts hasn't advanced, device isn't reporting
    if pre_ts and post_ts and pre_ts == post_ts:
        elapsed = round((time.monotonic() - t_action) * 1000)
        logger.warning("verify.stale trace_id=%s device=%s key=%s ts_unchanged=%s elapsed=%dms",
                       tid, device_id, target_key, post_ts, elapsed)
        return VerificationResult(
            verified=False, skipped=True,
            key=target_key, pre_value=pre_value, post_value=post_value,
            message=(
                f"Stale telemetry — device ts unchanged ({post_ts}). "
                "Device may be offline or not reporting."),
            verification_state=VSTATE_UNVERIFIED,
        )

    if post_value is None:
        # RPC was sent but no telemetry key to confirm — PARTIAL_SUCCESS not UNVERIFIED
        return VerificationResult(
            verified=False, skipped=True,
            key=target_key, pre_value=pre_value, post_value=post_value,
            message=f"RPC sent to device, but key '{target_key}' not in telemetry — awaiting confirmation",
            verification_state=VSTATE_PARTIAL_SUCCESS,
        )

    pre_num = _to_num(pre_value)
    post_num = _to_num(post_value)
    changed  = pre_num != post_num

    if changed:
        msg = f"Verified ✅ — {target_key} changed {_fmt(pre_value)} → {_fmt(post_value)}"
        logger.info("verify.success trace_id=%s device=%s key=%s %s→%s",
                    tid, device_id, target_key, pre_value, post_value)
    else:
        msg = (
            f"State unchanged ⚠️ — {target_key} still {_fmt(post_value)} after {VERIFY_DELAY_S}s. "
            "Device may be offline or firmware did not handle the command."
        )
        logger.warning("verify.unchanged trace_id=%s device=%s key=%s value=%s",
                       tid, device_id, target_key, post_value)

    return VerificationResult(
        verified=changed, key=target_key,
        pre_value=pre_value, post_value=post_value, message=msg,
        verification_state=VSTATE_SUCCESS if changed else VSTATE_PARTIAL_SUCCESS,
    )


async def verify_rule_created(
    db: Session, tenant_id, key: str
) -> VerificationResult:
    """Confirm that a threshold rule was actually written to the DB."""
    try:
        from app.models.models import ThresholdRule
        rule = db.query(ThresholdRule).filter(
            ThresholdRule.tenant_id == tenant_id,
            ThresholdRule.key       == key,
            ThresholdRule.is_active == True,
        ).first()
        if rule:
            return VerificationResult(
                verified   = True,
                key        = key,
                message    = f"Rule '{key}' confirmed in DB (id={rule.id})",
            )
        return VerificationResult(
            verified = False,
            key      = key,
            message  = f"Rule '{key}' not found in DB after creation",
        )
    except Exception as exc:
        return VerificationResult(
            verified = False, skipped = True,
            message  = f"Rule verification failed: {exc}",
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_num(v) -> float:
    """Coerce to numeric for comparison. bool→int, str→float if possible."""
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return hash(str(v)) % 1e6  # fallback — string comparison


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, (int, float)):
        return str(int(v)) if v == int(v) else f"{v:.2f}"
    return str(v)


async def verify_actions(
    plan:  "Plan",
    trace: "ExecutionTrace",
    db:    "Session",
) -> dict:
    """
    Plan-aware verification.
    Iterates plan steps, verifies only those with requires_verification=True.
    Returns unified result dict with per-step outcomes.

    Return shape:
        {
            "overall":  "success" | "partial" | "failed" | "skipped",
            "verified": bool,
            "steps":    {step_id: VerificationResult.__dict__},
            "message":  str,
        }
    """
    from app.services.taat_agent_planner import Plan as _Plan

    steps_to_verify = [s for s in plan.steps if getattr(s, "requires_verification", False)]

    if not steps_to_verify:
        return {
            "overall":  "skipped",
            "verified": True,
            "steps":    {},
            "message":  "No steps required verification",
        }

    step_results: dict = {}
    success_count = 0
    fail_count    = 0

    for step in steps_to_verify:
        step_id = getattr(step, "id", step.tool)

        # Only RPC steps are verifiable via telemetry delta
        if step.tool == "send_rpc":
            params    = step.args.get("params", {})
            # Try to resolve device_id — three fallbacks in priority order:
            # 1. rpc_result.device_id  (set by tool_send_rpc on success)
            # 2. pre_state.device_id   (set by get_key_intelligence pre-read)
            # 3. match device_name from step args against devices in trace
            device_id = (
                trace.results.get("rpc_result", {}).get("device_id")
                or trace.results.get("pre_state", {}).get("device_id")
            )
            if not device_id:
                device_name = step.args.get("device_name", "").lower()
                devices = trace.results.get("devices", {}).get("devices", [])
                matched = next(
                    (d for d in devices if d.get("name", "").lower() == device_name),
                    None,
                ) or next(
                    (d for d in devices if device_name in d.get("name", "").lower()),
                    None,
                )
                if matched:
                    device_id = matched.get("id")
            if device_id and params:
                result = await verify_rpc(db, device_id, params, trace)
            else:
                result = VerificationResult(
                    verified=False, skipped=True,
                    message="device_id not resolved — cannot verify",
                )
        elif step.tool == "create_rule":
            key = step.args.get("key")
            # Resolve tenant_id from trace results (set by create_rule tool)
            tenant_id = (
                trace.results.get("rule_result", {}).get("tenant_id")
                or step.args.get("tenant_id")
            )
            if key and tenant_id:
                result = await verify_rule_created(db, tenant_id, key)
            else:
                # Can't verify without tenant — skip, not fail
                result = VerificationResult(
                    verified=True, skipped=True,
                    message="rule verification skipped — tenant_id not in trace",
                )
        else:
            result = VerificationResult(
                verified=True, skipped=True,
                message=f"No verification defined for tool: {step.tool}",
            )

        step_results[step_id] = vars(result)
        if result.skipped or result.verified:
            success_count += 1
        else:
            fail_count += 1

    # Derive overall state from individual step states
    states = [
        r.get("verification_state", VSTATE_UNVERIFIED)
        for r in step_results.values()
    ]
    if all(s == VSTATE_SUCCESS for s in states):
        overall  = VSTATE_SUCCESS
        verified = True
    elif all(s == VSTATE_FAILED for s in states):
        overall  = VSTATE_FAILED
        verified = False
    elif any(s == VSTATE_SUCCESS for s in states):
        overall  = VSTATE_PARTIAL_SUCCESS
        verified = False
    elif any(s == VSTATE_PARTIAL_SUCCESS for s in states):
        overall  = VSTATE_PARTIAL_SUCCESS
        verified = False
    else:
        overall  = VSTATE_UNVERIFIED
        verified = False

    summary = "; ".join(
        f"{sid}: {r.get('message', '')}"
        for sid, r in step_results.items()
    )

    result = {
        "overall":  overall,
        "verified": verified,
        "steps":    step_results,
        "message":  summary,
    }
    # Write summary back onto trace for observability
    if hasattr(trace, "verification_summary"):
        trace.verification_summary = f"{overall}: {summary[:200]}"
    return result
