"""
app/services/taat_context_compressor.py

Context Compression Layer for TAAT.

PROBLEM:
  Raw TAAT context grows unbounded — 27 verbose alarms, full Welford stats,
  raw telemetry dictionaries, unprocessed memory blobs. Sending all of this
  to the LLM wastes tokens and degrades reasoning quality.

SOLUTION:
  compress_context(ctx) transforms the raw context dict produced by
  build_context() into a compact, operator-focused summary before
  build_system_prompt() sees it.

ARCHITECTURE POSITION:
  intelligence.py → build_context() → compress_context() → build_system_prompt()

COMPRESSION RULES:
  - Alarms: keep top N by severity, summarize the rest as a count
  - Telemetry: round to 2dp, drop keys not relevant to current intent
  - Health: single label + score, drop raw breakdown
  - Anomalies: top 3 anomalous keys + severity, drop raw stats
  - Memory: top 5 most relevant entries, drop duplicates
  - RPC history: last command only + its outcome
  - Baseline: active/inactive + count of keys, not raw stats
  - Key intelligence: single compact line per key

OUTPUT CONTRACT:
  Returns a dict with the same top-level keys as ctx but with compressed values.
  build_system_prompt() does NOT need to change — it reads the same keys.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Severity ordering ─────────────────────────────────────────────────────────

_SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

def _sev_key(alarm: dict) -> int:
    return _SEV_RANK.get(str(alarm.get("severity", "INFO")).upper(), 99)


# ── Public interface ──────────────────────────────────────────────────────────

def compress_context(ctx: dict, intent: str = "") -> dict:
    """
    Transform a raw TAAT context dict into a compact operator-focused summary.

    This is a pure function — it does not modify ctx in place.
    All compression is lossless at the operator-decision level:
    information needed for safe reasoning is preserved.

    Args:
        ctx:    Raw context dict from build_context()
        intent: Current intent string (used to focus compression)

    Returns:
        Compressed context dict safe to pass to build_system_prompt()
    """
    out: dict = {"intent": ctx.get("intent", intent)}

    # Device list — keep as-is (compact by design)
    out["device_list"] = ctx.get("device_list", [])

    # Alarms — severity-ranked, top 5 verbose + count summary
    out["active_alarms"] = _compress_alarms(ctx.get("active_alarms", []))

    # Telemetry — round values, keep all keys (they're already a flat dict)
    if "telemetry" in ctx:
        out["telemetry"] = _compress_telemetry(ctx["telemetry"])

    # Health — single summary line
    if "health" in ctx:
        out["health"] = _compress_health(ctx["health"])

    # Anomalies — top 3 anomalous keys only
    if "anomalies" in ctx:
        out["anomalies"] = _compress_anomalies(ctx["anomalies"])

    # Baseline — active flag + key count only
    if "baseline" in ctx:
        out["baseline"] = _compress_baseline(ctx["baseline"])

    # Key intelligence — compact single-line dict
    if "key_intel" in ctx:
        out["key_intel"] = _compress_key_intel(ctx["key_intel"])

    # RPC history — last command only
    if "rpc_history" in ctx:
        out["rpc_history"] = _compress_rpc_history(ctx["rpc_history"])

    # Memory — top 5 by relevance score (already scored by taat_memory_service)
    if "memory" in ctx:
        out["memory"] = _compress_memory(ctx["memory"])

    # Audit trail — last 3 entries only
    if "audit_trail" in ctx:
        out["audit_trail"] = _compress_audit(ctx["audit_trail"])

    # Pass through unchanged
    for k in ("existing_rules", "users", "decision_summary", "baseline_deviation", "daily_comparison", "slow_intel"):
        if k in ctx:
            out[k] = ctx[k]

    return out


# ── Compressor sub-functions ──────────────────────────────────────────────────

def _compress_alarms(alarms: List[dict]) -> List[dict]:
    """
    Keep top 5 by severity. Append a synthetic summary entry if more exist.

    BAD:  27 alarm dicts with raw timestamps, UUIDs, and internal state
    GOOD: 3 CRITICAL alarms + "and 24 more (LOW/INFO)"
    """
    if not alarms:
        return []

    sorted_alarms = sorted(alarms, key=_sev_key)
    top            = sorted_alarms[:5]
    rest           = sorted_alarms[5:]

    compressed = []
    for a in top:
        compressed.append({
            "alarm_type":  a.get("alarm_type", "alarm"),
            "severity":    a.get("severity", "?"),
            "device_name": a.get("device_name", "?"),
            "message":     (a.get("message") or "")[:80],  # truncate long messages
        })

    if rest:
        # Group remaining by severity
        by_sev: Dict[str, int] = {}
        for a in rest:
            s = str(a.get("severity", "INFO")).upper()
            by_sev[s] = by_sev.get(s, 0) + 1
        summary_parts = [f"{v} {k}" for k, v in sorted(by_sev.items(), key=lambda x: _SEV_RANK.get(x[0], 99))]
        compressed.append({
            "alarm_type":  "_summary",
            "severity":    "INFO",
            "device_name": "fleet",
            "message":     f"...and {len(rest)} more: {', '.join(summary_parts)}",
        })

    return compressed


def _compress_telemetry(telem: dict) -> dict:
    """
    Round float values to 2dp. Keep all keys — telemetry dicts are already small.
    """
    if not telem:
        return telem
    vals = telem.get("values", telem)
    if not isinstance(vals, dict):
        return telem

    rounded = {}
    for k, v in vals.items():
        if isinstance(v, float):
            rounded[k] = round(v, 2)
        else:
            rounded[k] = v

    # Preserve the wrapper if it existed
    if "values" in telem:
        return {**telem, "values": rounded}
    return rounded


def _compress_health(health: dict) -> dict:
    """
    Keep: health_score, health_label, rul_estimate.
    Drop: raw sub-scores, breakdown arrays.

    BAD:  {health_score: 72, health_label: "FAIR", motor_score: 68, bearing_score: 75, ...12 more keys}
    GOOD: {health_score: 72, health_label: "FAIR", rul_estimate: "~18 days"}
    """
    if not health:
        return health
    return {
        "health_score":  health.get("health_score"),
        "health_label":  health.get("health_label", "UNKNOWN"),
        "rul_estimate":  health.get("rul_estimate") or health.get("rul_days"),
        "degrading":     health.get("degrading", False),
    }


def _compress_anomalies(anomalies: dict) -> dict:
    """
    Keep: count, top 3 anomalous keys with z-scores.
    Drop: raw statistical arrays, full key lists.

    BAD:  {anomaly_count: 8, all_keys: [...20 items...], stats: {mean: ..., std: ...}}
    GOOD: {anomaly_count: 8, top_keys: ["motor_de_velocity(z=4.2)", "temperature(z=3.1)"]}
    """
    if not anomalies:
        return anomalies

    count = anomalies.get("anomaly_count", 0)
    if count == 0:
        return {"anomaly_count": 0}

    # Extract key+z-score pairs from various response shapes
    top_keys: List[str] = []
    most_anom = anomalies.get("most_anomalous_key")
    if most_anom:
        z = anomalies.get("max_z_score") or anomalies.get("z_score")
        if z is not None:
            top_keys.append(f"{most_anom}(z={z:.1f})")
        else:
            top_keys.append(most_anom)

    # If there are per-key details available
    key_scores = anomalies.get("key_scores") or anomalies.get("anomalous_keys") or []
    if isinstance(key_scores, list):
        for ks in key_scores[:3]:
            if isinstance(ks, dict):
                kname = ks.get("key") or ks.get("name", "?")
                kz    = ks.get("z_score") or ks.get("score")
                label = f"{kname}(z={kz:.1f})" if kz is not None else kname
                if label not in top_keys:
                    top_keys.append(label)

    return {
        "anomaly_count":     count,
        "most_anomalous_key": most_anom,
        "top_anomalous":     top_keys[:3],
    }


def _compress_baseline(baseline: dict) -> dict:
    """
    Keep: status, key count.
    Drop: per-key mean/std arrays.
    """
    if not baseline:
        return baseline
    keys = baseline.get("keys", {})
    return {
        "status":    baseline.get("status", "unknown"),
        "key_count": len(keys) if isinstance(keys, dict) else 0,
    }


def _compress_key_intel(ki: dict) -> dict:
    """
    Compact single-line key intelligence.
    Already small — just drop verbose fields.
    """
    if not ki:
        return ki
    return {
        "key":                ki.get("key"),
        "value":              ki.get("value"),
        "unit":               ki.get("unit"),
        "status":             ki.get("status"),
        "risk":               ki.get("risk"),
        "reason":             (ki.get("reason") or "")[:120],
        "recommended_action": ki.get("recommended_action"),
    }


def _compress_rpc_history(rpc: dict) -> dict:
    """
    Keep only the most recent command + its outcome.
    """
    if not rpc:
        return rpc
    commands = rpc.get("commands", [])
    if not commands:
        return {"count": 0}
    last = commands[0]
    return {
        "count":        rpc.get("count", len(commands)),
        "last_command": {
            "method": last.get("method"),
            "params": last.get("params"),
            "status": last.get("status"),
            "ts":     last.get("executed_at") or last.get("ts"),
        },
    }


def _compress_memory(memory: dict) -> dict:
    """
    Keep top 5 memory entries. Truncate long content strings.
    """
    if not memory:
        return memory
    all_mem = memory.get("memories", [])
    top     = all_mem[:10]  # taat_memory_service already scores/orders these
    return {
        "count":    memory.get("count", len(all_mem)),
        "memories": [
            {
                "type":    m.get("type"),
                "content": (m.get("content") or "")[:160],  # truncate verbose entries
            }
            for m in top
        ],
    }


def _compress_audit(audit: Any) -> Any:
    """
    Keep last 3 audit entries. Truncate descriptions.
    """
    if not audit:
        return audit
    if isinstance(audit, dict):
        entries = audit.get("entries", audit.get("events", []))
    elif isinstance(audit, list):
        entries = audit
    else:
        return audit

    top = entries[:3]
    return [
        {
            "action":      e.get("action") or e.get("event_type", "?"),
            "description": (e.get("description") or e.get("message") or "")[:100],
            "ts":          e.get("created_at") or e.get("ts"),
        }
        for e in top
        if isinstance(e, dict)
    ]
