"""
app/services/taat_executor.py — Parallel-aware Tool Executor

Runs a Plan step by step, collecting results into an ExecutionTrace.
Independent read steps (no output_key dependencies) are grouped into
parallel batches and run with asyncio.gather() — cuts RCA response time
by ~5x versus pure sequential execution.

Features:
- Parallel execution for independent read steps
- Sequential execution for steps with output_key dependencies
- Graceful failure — one failed step doesn't abort the plan
- Result accumulation — trace carries full context for the LLM
- Timing — each step is timed for observability
"""
from __future__ import annotations

import asyncio
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
    Run all steps in a plan, parallelising independent reads.

    Steps are grouped into batches:
      - A batch is independent if none of its steps need an output_key
        that hasn't been produced yet.
      - Steps with requires_verification=True (writes) always run solo.
      - All steps in a batch run with asyncio.gather().

    extra_kwargs: additional args injected into every tool call
    (e.g. devices list for create_rule, api_key for LLM tools)
    """
    trace = ExecutionTrace(plan_intent=plan.intent)
    trace.started_at = datetime.now(timezone.utc).isoformat()
    kwargs_base = extra_kwargs or {}
    t_total = time.monotonic()

    batches = _group_into_batches(plan.steps)
    logger.debug(
        "executor.plan trace_id=%s intent=%s steps=%d batches=%d",
        trace.trace_id, plan.intent, len(plan.steps), len(batches),
    )

    for batch in batches:
        if len(batch) == 1:
            # Single step — run directly (avoids gather overhead)
            await _run_step(batch[0], trace, db, current_user, kwargs_base)
        else:
            # Parallel batch — run all steps concurrently
            logger.debug(
                "executor.batch trace_id=%s parallel=%d tools=%s",
                trace.trace_id, len(batch), [s.tool for s in batch],
            )
            await asyncio.gather(*[
                _run_step(step, trace, db, current_user, kwargs_base)
                for step in batch
            ])

    trace.total_ms     = round((time.monotonic() - t_total) * 1000, 1)
    trace.completed_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "executor.plan trace_id=%s intent=%s steps=%d all_success=%s total=%.0fms",
        trace.trace_id, plan.intent, len(plan.steps), trace.all_success, trace.total_ms,
    )
    return trace


async def _run_step(
    step:         Step,
    trace:        ExecutionTrace,
    db:           Session,
    current_user,
    kwargs_base:  dict,
) -> None:
    """Execute a single step and record it on the trace."""
    t0     = time.monotonic()
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
        tool        = step.tool,
        label       = step.label or step.tool,
        success     = result.success,
        data        = result.data,
        error       = result.error,
        duration_ms = round(duration, 1),
    )
    trace.steps.append(step_trace)

    if not result.success:
        trace.all_success = False
        trace.errors.append(f"{step.tool}: {result.error}")
        logger.warning("executor.step failed tool=%s error=%s", step.tool, result.error)

    # Store named output for downstream steps
    if step.output_key and result.data:
        trace.results[step.output_key] = result.data

    logger.debug(
        "executor.step done tool=%s success=%s duration=%.1fms",
        step.tool, result.success, duration,
    )


# ── Batch grouping ────────────────────────────────────────────────────────────

def _group_into_batches(steps: List[Step]) -> List[List[Step]]:
    """
    Group steps into parallel batches.

    Rules:
    - Write steps (requires_verification=True) always run alone.
    - A step that depends on a previous step's output_key runs alone
      after that step completes.
    - All remaining read steps with no inter-dependencies are batched together.

    Example for RCA (6 independent reads):
        Input:  [get_devices, get_telemetry, get_anomalies, get_baseline, get_health, get_alarms, get_memory]
        Output: [[get_devices, get_telemetry, get_anomalies, get_baseline, get_health, get_alarms, get_memory]]
        → all run in parallel, ~5x faster

    Example for DEVICE_CONTROL (read then write):
        Input:  [get_key_intelligence(output_key=pre_state), send_rpc]
        Output: [[get_key_intelligence], [send_rpc]]
        → sequential because send_rpc is a write step
    """
    if not steps:
        return []

    batches: List[List[Step]] = []
    produced_keys: set = set()   # output_keys produced so far
    current_batch: List[Step] = []

    for step in steps:
        needs_key = _needs_prior_output(step, produced_keys)
        is_write  = getattr(step, "requires_verification", False)

        if needs_key or is_write:
            # Flush current batch first
            if current_batch:
                batches.append(current_batch)
                current_batch = []
            # Run this step alone
            batches.append([step])
        else:
            current_batch.append(step)

        if step.output_key:
            produced_keys.add(step.output_key)

    if current_batch:
        batches.append(current_batch)

    return batches


def _needs_prior_output(step: Step, produced_keys: set) -> bool:
    """
    True if any of this step's args reference a key produced by a prior step.
    This catches explicit dependencies like pre_state → send_rpc.
    """
    for v in step.args.values():
        if isinstance(v, str) and v in produced_keys:
            return True
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inject_forward_results(
    merged: dict, results: dict, step: Step
) -> None:
    """
    If a previous step stored device_id under a key, and the current step
    needs device_id but doesn't have it, inject it automatically.
    This allows simple result chaining without explicit wiring.
    """
    if "device_id" not in merged or not merged.get("device_id"):
        for key, data in results.items():
            if isinstance(data, dict) and "device_id" in data:
                merged["device_id"] = data["device_id"]
                break

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
