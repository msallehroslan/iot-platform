"""
app/services/taat_executor.py — Sequential Tool Executor

Runs a Plan step by step, collecting results into an ExecutionTrace.
Results from earlier steps are available to later steps.

Features:
- Sequential execution — each step sees previous results
- Graceful failure — one failed step doesn't abort the plan
- Result accumulation — trace carries full context for the LLM
- Timing — each step is timed for observability
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.taat_agent_planner import Plan, Step
from app.services.taat_tool_registry import ToolResult, call as registry_call

logger = logging.getLogger(__name__)

# ── Read-safe tools — parallelisable with asyncio.gather() ───────────────────
# Write tools (send_rpc, ack_alarm, clear_alarm, create_rule, delete_rule,
# save_memory, schedule_rpc) are intentionally excluded — mutations must be
# sequential to preserve audit trail and avoid race conditions.
READ_TOOLS = frozenset({
    "get_devices",
    "get_latest_telemetry",
    "get_active_alarms",
    "get_device_health",
    "get_anomalies",
    "get_baseline",
    "get_rpc_history",
    "get_audit_log",
    "generate_report",
    "get_memory",
    "get_key_intelligence",
    "get_trends",
    "get_health_summary",
})


# ── Execution trace ───────────────────────────────────────────────────────────

@dataclass
class StepTrace:
    tool:        str
    label:       str
    success:     bool
    data:        Dict[str, Any] = field(default_factory=dict)
    error:       Optional[str]  = None
    duration_ms: float          = 0.0


@dataclass
class ExecutionTrace:
    """
    Full record of a plan execution.
    Conforms to Task 3 spec: trace_id, intent, steps, results, errors,
    started_at, completed_at, duration_ms, verification_summary, decision_summary.
    """
    plan_intent:          str
    trace_id:             str                       = field(default_factory=lambda: str(uuid.uuid4())[:8])
    steps:                List[StepTrace]           = field(default_factory=list)
    results:              Dict[str, Any]            = field(default_factory=dict)
    errors:               List[str]                 = field(default_factory=list)
    all_success:          bool                      = True
    total_ms:             float                     = 0.0
    started_at:           Optional[str]             = None   # ISO UTC
    completed_at:         Optional[str]             = None   # ISO UTC
    verification_summary: Optional[str]             = None   # set by verify_actions()
    decision_summary:     Optional[str]             = None   # set by build_decision()

    def get(self, key: str, default=None):
        return self.results.get(key, default)

    def to_context_dict(self) -> dict:
        """Compact summary for LLM system prompt injection."""
        return {
            "intent":    self.plan_intent,
            "steps_run": len(self.steps),
            "success":   self.all_success,
            **self.results,
        }

    def to_chip_data(self) -> Optional[dict]:
        """Extract the primary action result for frontend chip display."""
        for key in ("rpc_result", "rule_result", "alarm_result"):
            if key in self.results:
                return self.results[key]
        return None


# ── Executor ──────────────────────────────────────────────────────────────────

async def execute(
    plan:         Plan,
    db:           Session,
    current_user,
    extra_kwargs: Dict[str, Any] = None,
) -> ExecutionTrace:
    """
    Run all steps in a plan sequentially.

    extra_kwargs: additional args injected into every tool call
    (e.g. devices list for create_rule, api_key for LLM tools)
    """
    trace = ExecutionTrace(plan_intent=plan.intent)
    trace.started_at = datetime.now(timezone.utc).isoformat()
    kwargs_base = extra_kwargs or {}
    t_total = time.monotonic()

    # ── Partition steps into read batches and write singles ──────────────────
    # Read tools have no side effects and can be parallelised safely.
    # Write tools must remain sequential for audit correctness.
    #
    # Algorithm: scan the step list in order. Accumulate consecutive READ steps
    # into a batch. When a WRITE step or the end is reached, flush the batch
    # via asyncio.gather(), then execute the WRITE step sequentially.
    # This preserves the original step order in the trace.

    step_groups: list = []  # list of (is_parallel: bool, steps: list[Step])
    current_reads: list = []

    for step in plan.steps:
        if step.tool in READ_TOOLS:
            current_reads.append(step)
        else:
            if current_reads:
                step_groups.append((True, current_reads))
                current_reads = []
            step_groups.append((False, [step]))

    if current_reads:
        step_groups.append((True, current_reads))

    for is_parallel, steps in step_groups:
        if is_parallel and len(steps) > 1:
            # ── Parallel READ batch ───────────────────────────────────────────
            logger.debug(
                "executor.parallel trace_id=%s tools=%s",
                trace.trace_id, [s.tool for s in steps],
            )
            t_batch = time.monotonic()

            async def _run_step(step: Step) -> tuple:
                merged = {**kwargs_base, **step.args}
                _inject_forward_results(merged, trace.results, step)
                t0 = time.monotonic()
                try:
                    res = await registry_call(
                        step.tool, db=db, current_user=current_user, **merged
                    )
                except Exception as exc:
                    logger.error("executor.parallel.step failed tool=%s: %s", step.tool, exc)
                    res = ToolResult(tool_name=step.tool, success=False, error=str(exc))
                duration = (time.monotonic() - t0) * 1000
                return step, res, duration

            results_list = await asyncio.gather(*[_run_step(s) for s in steps])

            for step, result, duration in results_list:
                step_trace = StepTrace(
                    tool=step.tool, label=step.label or step.tool,
                    success=result.success, data=result.data,
                    error=result.error, duration_ms=round(duration, 1),
                )
                trace.steps.append(step_trace)
                if not result.success:
                    trace.all_success = False
                    trace.errors.append(f"{step.tool}: {result.error}")
                if step.output_key and result.data:
                    trace.results[step.output_key] = result.data

            logger.debug(
                "executor.parallel done trace_id=%s tools=%d batch_ms=%.1f",
                trace.trace_id, len(steps),
                (time.monotonic() - t_batch) * 1000,
            )

        else:
            # ── Sequential (single READ or any WRITE) ─────────────────────────
            for step in steps:
                t0 = time.monotonic()
                merged = {**kwargs_base, **step.args}
                _inject_forward_results(merged, trace.results, step)
                logger.debug(
                    "executor.step trace_id=%s tool=%s args=%s",
                    trace.trace_id, step.tool, _safe_repr(merged),
                )
                try:
                    result: ToolResult = await registry_call(
                        step.tool, db=db, current_user=current_user, **merged
                    )
                except Exception as exc:
                    logger.error("executor.step failed tool=%s: %s", step.tool, exc)
                    result = ToolResult(tool_name=step.tool, success=False, error=str(exc))

                duration = (time.monotonic() - t0) * 1000
                step_trace = StepTrace(
                    tool=step.tool, label=step.label or step.tool,
                    success=result.success, data=result.data,
                    error=result.error, duration_ms=round(duration, 1),
                )
                trace.steps.append(step_trace)
                if not result.success:
                    trace.all_success = False
                    trace.errors.append(f"{step.tool}: {result.error}")
                    logger.warning("executor.step failed tool=%s error=%s", step.tool, result.error)
                if step.output_key and result.data:
                    trace.results[step.output_key] = result.data
                logger.debug(
                    "executor.step done tool=%s success=%s duration=%.1fms",
                    step.tool, result.success, duration,
                )

    trace.total_ms    = round((time.monotonic() - t_total) * 1000, 1)
    trace.completed_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "executor.plan trace_id=%s intent=%s steps=%d all_success=%s total=%.0fms",
        trace.trace_id, plan.intent, len(plan.steps), trace.all_success, trace.total_ms,
    )
    return trace


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inject_forward_results(
    merged: dict, results: dict, step: Step
) -> None:
    """
    If a previous step stored device_id under a key, and the current step
    needs device_id but doesn't have it, inject it automatically.
    This allows simple result chaining without explicit wiring.
    """
    # If this step needs device_id and it's not already set,
    # try to find it from accumulated results
    if "device_id" not in merged or not merged.get("device_id"):
        for key, data in results.items():
            if isinstance(data, dict) and "device_id" in data:
                merged["device_id"] = data["device_id"]
                break

    # Same for devices list (needed by create_rule)
    if "devices" not in merged:
        for key, data in results.items():
            if isinstance(data, dict) and "devices" in data:
                merged["devices"] = data["devices"]
                break


def _safe_repr(d: dict) -> str:
    """Compact repr that doesn't leak secrets."""
    safe = {}
    for k, v in d.items():
        if k in ("api_key", "token", "password", "secret"):
            safe[k] = "***"
        elif isinstance(v, (str, int, float, bool, type(None))):
            safe[k] = v
        elif isinstance(v, dict):
            safe[k] = f"{{...{len(v)} keys}}"
        elif isinstance(v, list):
            safe[k] = f"[{len(v)} items]"
        else:
            safe[k] = str(type(v).__name__)
    return str(safe)
