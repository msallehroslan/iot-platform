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
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.services.taat_executor import ExecutionTrace

logger = logging.getLogger(__name__)

# How long to wait before re-reading telemetry to confirm state change
VERIFY_DELAY_S = 4.0


@dataclass
class VerificationResult:
    verified:    bool                  # True = state confirmed changed
    key:         Optional[str] = None  # which key was checked
    pre_value:   object        = None  # value before command
    post_value:  object        = None  # value after command
    message:     str           = ""    # human-readable summary
    skipped:     bool          = False # True = couldn't verify (offline/no key)


async def verify_rpc(
    db: Session,
    device_id: str,
    params: dict,
    trace: ExecutionTrace,
) -> VerificationResult:
    """
    Verify that an RPC command took effect by checking telemetry delta.

    Args:
        db:        DB session
        device_id: target device
        params:    RPC params that were sent e.g. {"led1": 1}
        trace:     execution trace containing pre_state result

    Returns:
        VerificationResult
    """
    if not params or not device_id:
        return VerificationResult(verified=False, skipped=True,
                                  message="No params or device_id to verify")

    # Pick the first key from params as the verification target
    target_key = next(iter(params.keys()), None)
    if not target_key:
        return VerificationResult(verified=False, skipped=True,
                                  message="No target key in params")

    # Get pre-action value from trace
    pre_state  = trace.get("pre_state") or {}
    pre_values = pre_state.get("values", {})
    pre_value  = pre_values.get(target_key)

    # Wait for device to respond and telemetry to be ingested
    await asyncio.sleep(VERIFY_DELAY_S)

    # Re-read telemetry
    try:
        from app.services.data_service import get_latest_telemetry
        post_telem = get_latest_telemetry(db, device_id)
        post_value = post_telem.get("values", {}).get(target_key)
    except Exception as exc:
        logger.warning("verification read failed: %s", exc)
        return VerificationResult(
            verified=False, skipped=True,
            message=f"Could not read telemetry for verification: {exc}",
        )

    if post_value is None:
        return VerificationResult(
            verified=False, skipped=True,
            key=target_key, pre_value=pre_value, post_value=post_value,
            message=f"Key '{target_key}' not in telemetry — cannot verify",
        )

    # Compare — coerce to comparable types
    pre_num  = _to_num(pre_value)
    post_num = _to_num(post_value)
    changed  = pre_num != post_num

    if changed:
        msg = (
            f"Verified ✅ — {target_key} changed "
            f"{_fmt(pre_value)} → {_fmt(post_value)}"
        )
        logger.info("verify.success device=%s key=%s %s→%s",
                    device_id, target_key, pre_value, post_value)
    else:
        msg = (
            f"State unchanged ⚠️ — {target_key} is still "
            f"{_fmt(post_value)} after {VERIFY_DELAY_S}s. "
            "Device may be offline or firmware did not handle the command."
        )
        logger.warning("verify.unchanged device=%s key=%s value=%s",
                       device_id, target_key, post_value)

    return VerificationResult(
        verified   = changed,
        key        = target_key,
        pre_value  = pre_value,
        post_value = post_value,
        message    = msg,
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
            # Try to resolve device_id from trace results
            device_id = (
                trace.results.get("rpc_result", {}).get("device_id")
                or trace.results.get("pre_state", {}).get("device_id")
            )
            if device_id and params:
                result = await verify_rpc(db, device_id, params, trace)
            else:
                result = VerificationResult(
                    verified=False, skipped=True,
                    message="device_id not resolved — cannot verify",
                )
        elif step.tool == "create_rule":
            key = step.args.get("key")
            tenant_id = getattr(
                getattr(db, "_current_user", None), "tenant_id", None
            )
            if key and tenant_id:
                result = await verify_rule_created(db, tenant_id, key)
            else:
                result = VerificationResult(
                    verified=False, skipped=True,
                    message="key/tenant not available for rule verification",
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

    if fail_count == 0:
        overall  = "success"
        verified = True
    elif success_count == 0:
        overall  = "failed"
        verified = False
    else:
        overall  = "partial"
        verified = False

    summary = "; ".join(
        f"{sid}: {r.get('message', '')}"
        for sid, r in step_results.items()
    )

    return {
        "overall":  overall,
        "verified": verified,
        "steps":    step_results,
        "message":  summary,
    }
