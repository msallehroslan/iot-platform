"""
app/routers/intelligence_rca.py — Trend detection, RCA, AI summary.
Split from intelligence.py (Task 5 hardening).
"""
from app.routers.intelligence_shared import *

router_rca = APIRouter(prefix="/intelligence", tags=["Intelligence"])


def _rule_based_analysis(context: dict) -> str:
    """Simple rule-based analysis when LLM is not available."""
    lines = []
    alarms = context.get("alarms_last_24h", [])
    trends = context.get("current_trends", {})

    active = [a for a in alarms if "ACTIVE" in a.get("status", "")]
    if active:
        lines.append(f"**1. Health Status** — WARNING: {len(active)} active alarm(s)")
    else:
        lines.append("**1. Health Status** — HEALTHY: No active alarms")

    lines.append("\n**2. Root Cause Analysis**")
    if alarms:
        for a in alarms[:3]:
            details = a.get("details", {})
            lines.append(f"- {a['type']}: {details.get('message', 'threshold breached')}")
    else:
        lines.append("- No alarms in last 24 hours")

    lines.append("\n**3. Trend Insights**")
    for key, t in trends.items():
        trend = t.get("trend", "UNKNOWN")
        change = t.get("change_pct", 0)
        lines.append(f"- {key}: {trend} ({change:+.1f}% over window)")

    lines.append("\n**4. Risk Assessment**")
    rising_critical = [k for k, v in trends.items()
                      if v.get("trend") == "RISING" and abs(v.get("change_pct", 0)) > 20]
    if rising_critical:
        lines.append(f"- {', '.join(rising_critical)} rising rapidly — monitor closely")
    else:
        lines.append("- No immediate risk detected from current trends")

    lines.append("\n**5. Recommended Actions**")
    if active:
        lines.append("1. Acknowledge and investigate active alarms")
    if rising_critical:
        lines.append(f"2. Check {rising_critical[0]} source — rapid increase detected")
    lines.append("3. Monitor device telemetry for continued anomalies")

    return "\n".join(lines)


# ── Trend Detection ───────────────────────────────────────────────────────────

@router_rca.get("/trend/{device_id}/{key}")
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


@router_rca.get("/trend/{device_id}")
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

@router_rca.post("/rca/{device_id}")
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

    # ── Call Ollama via _call_groq ────────────────────────────────────────────
    try:
        analysis = await _call_groq(
            "ollama", [{"role": "user", "content": prompt}],
            max_tokens=4096, temperature=0.3,
        )
        return {
            "device_id":    str(device_id),
            "device_name":  device.name,
            "analysis":     analysis,
            "context":      context,
            "engine":       "ollama/qwen3:8b",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("rca.llm_failed device=%s error=%s", device_id, exc)
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

@router_rca.get("/summary/{device_id}")
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
            Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),  # works with string enum
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
