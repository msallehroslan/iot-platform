"""
app/routers/intelligence_data.py — Data intelligence endpoints.
Anomaly, baseline, health, alarm-action, schedule-rpc, reports, unified.
Split from intelligence.py (Task 5 hardening).
"""
from app.routers.intelligence_shared import *

router_data = APIRouter(prefix="/intelligence", tags=["Intelligence"])

# ── Phase 7: Anomaly Detection ────────────────────────────────────────────────

@router_data.get("/anomalies/{device_id}")
def get_anomalies(
    device_id: UUID,
    key: Optional[str] = None,
    hours: int = 24,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get anomaly scores for a device.
    Returns anomalies only by default; pass only_anomalies=false for all scores.
    """
    _assert_device(device_id, current_user, db)
    from app.services.anomaly_service import get_anomalies, get_anomaly_summary
    return {
        "device_id": str(device_id),
        "summary":   get_anomaly_summary(db, str(device_id), hours=hours),
        "anomalies": get_anomalies(db, str(device_id), key=key, hours=hours),
    }


# ── Phase 7: Baseline Learning ────────────────────────────────────────────────

@router_data.get("/baseline/{device_id}")
def get_baseline(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get learned baselines for a device.
    Returns per-key, per-hour-of-day statistics.
    Status = 'learning' until enough data (30 days), then 'active'.
    """
    _assert_device(device_id, current_user, db)
    from app.services.baseline_service import get_baseline_for_device, get_threshold_suggestions
    return {
        "device_id":   str(device_id),
        "baseline":    get_baseline_for_device(db, str(device_id)),
        "suggestions": get_threshold_suggestions(db, str(device_id)),
    }


@router_data.post("/baseline/{device_id}/refresh")
def refresh_baseline(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Manually trigger baseline recalculation for a device (admin only)."""
    _assert_device(device_id, current_user, db)
    from app.services.baseline_service import update_baselines_for_device
    rows = update_baselines_for_device(db, str(device_id))
    return {"device_id": str(device_id), "baseline_rows_updated": rows}


# ── Phase 7: Health Scoring + Predictive Maintenance ─────────────────────────

@router_data.get("/health/{device_id}")
def get_device_health(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get current health score and maintenance prediction for a device.
    Includes component breakdown: uptime, alarm, stability, freshness.
    """
    device = _assert_device(device_id, current_user, db)
    from app.services.health_service import get_latest_health, score_device
    health = get_latest_health(db, str(device_id))
    if not health:
        # Score on-demand if no cached score exists
        try:
            s = score_device(db, device)
            db.commit()
            health = get_latest_health(db, str(device_id))
        except Exception as exc:
            logger.error("on-demand health score failed: %s", exc)
    return {
        "device_id":   str(device_id),
        "device_name": device.name,
        "health":      health,
    }


@router_data.get("/health")
def get_fleet_health(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get health scores for all devices in the tenant.
    Sorted worst-first. Includes maintenance alerts.
    CUSTOMER_USER sees only their customer's devices.
    """
    from app.services.health_service import get_fleet_health as _fleet_health
    all_health = _fleet_health(db, str(current_user.tenant_id))

    # Apply CUSTOMER_USER scoping
    if current_user.role == "CUSTOMER_USER" and current_user.customer_id:
        scoped_ids = {
            str(d.id) for d in
            _scoped_devices(current_user, db).all()
        }
        all_health = [h for h in all_health if h["device_id"] in scoped_ids]

    maintenance_count = sum(1 for h in all_health if h["health"].get("maintenance_due"))
    return {
        "devices":           all_health,
        "total":             len(all_health),
        "maintenance_alerts": maintenance_count,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8 — Intelligence Enhancements
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Alarm control via chat ─────────────────────────────────────────────────

class AlarmActionRequest(BaseModel):
    action: str        # "ack" | "clear" | "ack_all" | "clear_all"
    alarm_id: Optional[UUID] = None
    severity_filter: Optional[str] = None   # for bulk ops


@router_data.post("/alarm-action")
def alarm_action(
    body: AlarmActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Perform alarm actions via chat or direct API.
    Supports single and bulk ack/clear scoped to the current user's tenant.
    """
    from app.models.models import AlarmStatus
    now = datetime.now(timezone.utc)

    # Scope to tenant + customer if needed
    base_q = db.query(Alarm).filter(
        Alarm.device_id.in_(
            [d.id for d in _scoped_devices(current_user, db).all()]
        )
    )

    if body.action == "ack" and body.alarm_id:
        alarm = base_q.filter(Alarm.id == body.alarm_id).first()
        if not alarm:
            raise HTTPException(404, "Alarm not found")
        alarm.status = AlarmStatus.ACTIVE_ACK
        alarm.ack_ts = now
        alarm.ack_by = str(current_user.id)
        db.commit()
        return {"actioned": 1, "action": "ack", "alarm_id": str(body.alarm_id)}

    elif body.action == "clear" and body.alarm_id:
        alarm = base_q.filter(Alarm.id == body.alarm_id).first()
        if not alarm:
            raise HTTPException(404, "Alarm not found")
        alarm.status = AlarmStatus.CLEARED_ACK if alarm.ack_ts else AlarmStatus.CLEARED_UNACK
        alarm.clear_ts = now
        alarm.cleared_by = str(current_user.id)
        db.commit()
        return {"actioned": 1, "action": "clear", "alarm_id": str(body.alarm_id)}

    elif body.action == "ack_all":
        q = base_q.filter(Alarm.status == AlarmStatus.ACTIVE_UNACK)
        if body.severity_filter:
            q = q.filter(Alarm.severity == body.severity_filter)
        alarms = q.all()
        for a in alarms:
            a.status = AlarmStatus.ACTIVE_ACK
            a.ack_ts = now
            a.ack_by = str(current_user.id)
        db.commit()
        return {"actioned": len(alarms), "action": "ack_all"}

    elif body.action == "clear_all":
        q = base_q.filter(Alarm.status.in_([AlarmStatus.ACTIVE_UNACK, AlarmStatus.ACTIVE_ACK]))
        if body.severity_filter:
            q = q.filter(Alarm.severity == body.severity_filter)
        alarms = q.all()
        for a in alarms:
            a.status = AlarmStatus.CLEARED_ACK if a.ack_ts else AlarmStatus.CLEARED_UNACK
            a.clear_ts = now
            a.cleared_by = str(current_user.id)
        db.commit()
        return {"actioned": len(alarms), "action": "clear_all"}

    raise HTTPException(400, "Invalid action")


# ── 2. Scheduled RPC (Phase 11 clean rewrite) ────────────────────────────────

class ScheduledRpcRequest(BaseModel):
    device_id:             UUID
    method:                str = "set"
    params:                dict = {}
    scheduled_for:         datetime
    repeat_interval_hours: Optional[float] = None


@router_data.post("/schedule-rpc")
def schedule_rpc(
    body: ScheduledRpcRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Schedule an RPC command to run at a specific UTC time.
    Optional repeat_interval_hours creates a recurring command.
    Background dispatcher in main.py fires it every 30s check.
    """
    from app.services.scheduled_rpc_service import schedule_command
    cmd = schedule_command(
        db,
        device_id             = body.device_id,
        method                = body.method,
        params                = body.params,
        scheduled_for         = body.scheduled_for,
        repeat_interval_hours = body.repeat_interval_hours,
        current_user          = current_user,
        source                = "dashboard",
    )
    from app.services.scheduled_rpc_service import _humanise_schedule
    return {
        "cmd_id":                str(cmd.id),
        "device_id":             str(cmd.device_id),
        "method":                cmd.method,
        "params":                cmd.params,
        "scheduled_for":         cmd.scheduled_for.isoformat(),
        "repeat_interval_hours": cmd.repeat_interval_hours,
        "human_label":           _humanise_schedule(cmd.scheduled_for, cmd.repeat_interval_hours),
        "is_scheduled":          True,
    }


@router_data.get("/schedule-rpc")
def list_scheduled_rpc(
    device_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all pending SCHEDULED commands for the tenant."""
    from app.services.scheduled_rpc_service import list_scheduled
    return list_scheduled(db, current_user, device_id=device_id)


@router_data.delete("/schedule-rpc/{cmd_id}")
def cancel_scheduled_rpc(
    cmd_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancel a specific scheduled command."""
    from app.services.scheduled_rpc_service import cancel_scheduled
    return cancel_scheduled(db, cmd_id=cmd_id, current_user=current_user)


# ── 3. "Why did this alarm fire?" deep RCA ────────────────────────────────────

@router_data.post("/alarm-explain/{alarm_id}")
async def explain_alarm(
    alarm_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Deep explanation of why a specific alarm fired.
    Correlates alarm time with telemetry, trend, baseline deviation, and anomaly scores.
    """
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if not alarm:
        raise HTTPException(404, "Alarm not found")

    device = _assert_device(alarm.device_id, current_user, db)
    api_key = os.getenv("GROQ_API_KEY")

    # Telemetry around alarm time (±10 min)
    t0 = alarm.start_ts or alarm.created_at
    before = t0 - timedelta(minutes=10)
    after  = t0 + timedelta(minutes=10)

    rows = (
        db.query(TelemetryData)
        .filter(
            TelemetryData.device_id == alarm.device_id,
            TelemetryData.ts.between(before, after),
            TelemetryData.value_num.isnot(None),
        )
        .order_by(TelemetryData.ts)
        .limit(100)
        .all()
    )
    telemetry_window = {}
    for r in rows:
        telemetry_window.setdefault(r.key, []).append({
            "ts": r.ts.isoformat(), "value": float(r.value_num)
        })

    # Anomaly scores around alarm time
    from app.services.anomaly_service import get_anomalies
    anomalies = get_anomalies(db, str(alarm.device_id), hours=2, only_anomalies=True)

    # Baseline for the alarm key at that hour
    from app.services.baseline_service import get_baseline_for_device
    baseline = get_baseline_for_device(db, str(alarm.device_id), current_hour=t0.hour)

    context = {
        "alarm": {
            "type":     alarm.alarm_type,
            "severity": alarm.severity.value if hasattr(alarm.severity, "value") else str(alarm.severity),
            "fired_at": t0.isoformat(),
            "details":  alarm.details,
        },
        "device":          {"name": device.name, "type": device.device_type},
        "telemetry_window": telemetry_window,
        "anomalies_nearby": anomalies[:5],
        "baseline":         baseline,
    }

    if not api_key:
        return {"alarm_id": str(alarm_id), "explanation": "Add GROQ_API_KEY for AI explanation.", "context": context}

    prompt = f"""Explain why this IoT alarm fired based on the data below.

{json.dumps(context, indent=2)}

Provide:
1. **Root Cause** — exactly what value/condition triggered it
2. **Lead-up** — what was happening in the 10 minutes before
3. **Anomaly Context** — was this a statistical outlier vs normal baseline?
4. **Likely Cause** — hardware, environment, or configuration issue?
5. **Fix** — one specific action to resolve or prevent recurrence

Be concise and technical. Base everything on the actual data."""

    try:
        explanation = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=600, temperature=0.2, model=GROQ_MODEL_DEEP)
        return {"alarm_id": str(alarm_id), "explanation": explanation, "context": context, "engine": f"groq/{GROQ_MODEL_DEEP}"}
    except Exception as exc:
        return {"alarm_id": str(alarm_id), "explanation": f"AI unavailable: {exc}", "context": context}


# ── 4. Comparative intelligence (this week vs last week) ─────────────────────

@router_data.get("/compare/{device_id}")
async def compare_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Compare device behaviour this week vs last week.
    Uses telemetry aggregates and alarm counts.
    """
    device = _assert_device(device_id, current_user, db)
    api_key = os.getenv("GROQ_API_KEY")

    now  = datetime.now(timezone.utc)
    w0_start = now - timedelta(days=7)
    w1_start = now - timedelta(days=14)

    def _period_stats(start, end):
        rows = (
            db.query(TelemetryData.key, TelemetryData.value_num)
            .filter(
                TelemetryData.device_id == device_id,
                TelemetryData.value_num.isnot(None),
                TelemetryData.ts.between(start, end),
            )
            .all()
        )
        from collections import defaultdict
        import math
        buckets = defaultdict(list)
        for key, val in rows:
            buckets[key].append(val)

        stats = {}
        for key, vals in buckets.items():
            n = len(vals)
            mean = sum(vals) / n if n else 0
            stats[key] = {
                "mean":    round(mean, 3),
                "min":     round(min(vals), 3),
                "max":     round(max(vals), 3),
                "samples": n,
            }
        alarm_count = db.query(Alarm).filter(
            Alarm.device_id == device_id,
            Alarm.created_at.between(start, end),
        ).count()
        return {"telemetry": stats, "alarm_count": alarm_count}

    this_week = _period_stats(w0_start, now)
    last_week = _period_stats(w1_start, w0_start)

    context = {
        "device":    {"name": device.name, "type": device.device_type},
        "this_week": this_week,
        "last_week": last_week,
    }

    if not api_key:
        return {"device_id": str(device_id), "comparison": context, "insight": "Add GROQ_API_KEY for AI insight."}

    prompt = f"""Compare this IoT device's behaviour this week vs last week.

{json.dumps(context, indent=2)}

Provide:
1. **Key Changes** — which metrics changed most significantly (% change)
2. **Alarm Trend** — better or worse than last week?
3. **Anomalies** — anything unusual in this week's data?
4. **Verdict** — is the device improving, degrading, or stable?

Be concise and data-driven."""

    try:
        insight = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=500, temperature=0.2, model=GROQ_MODEL_FAST)
        return {"device_id": str(device_id), "comparison": context, "insight": insight, "engine": f"groq/{GROQ_MODEL_FAST}"}
    except Exception as exc:
        return {"device_id": str(device_id), "comparison": context, "insight": f"AI unavailable: {exc}"}


# ── 5. Daily health report ────────────────────────────────────────────────────

@router_data.get("/report/daily")
async def daily_report(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Generate a daily health report for all devices in the tenant.
    Summarises alarms, trends, health scores, and top issues.
    """
    api_key = os.getenv("GROQ_API_KEY")
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    devices = _scoped_devices(current_user, db).all()
    from app.services.health_service import get_latest_health

    report_data = []
    for device in devices:
        alarms_24h = db.query(Alarm).filter(
            Alarm.device_id == device.id,
            Alarm.created_at >= since_24h,
        ).count()

        active_alarms = db.query(Alarm).filter(
            Alarm.device_id == device.id,
            Alarm.status.in_(["ACTIVE_UNACK", "ACTIVE_ACK"]),
        ).count()

        health = get_latest_health(db, str(device.id))
        trends = get_all_key_trends(db, str(device.id), minutes=60)

        report_data.append({
            "device":        device.name,
            "status":        device.status.value,
            "alarms_24h":    alarms_24h,
            "active_alarms": active_alarms,
            "health_score":  health["health_score"] if health else None,
            "health_label":  health["health_label"] if health else "UNKNOWN",
            "maintenance":   health["maintenance_due"] if health else False,
            "trends":        {k: v["trend"] for k, v in trends.items()},
        })

    # Sort: maintenance first, then by health score
    report_data.sort(key=lambda x: (not x["maintenance"], x["health_score"] or 100))

    total_active_alarms = sum(d["active_alarms"] for d in report_data)
    maintenance_needed  = [d["device"] for d in report_data if d["maintenance"]]
    critical_devices    = [d for d in report_data if d["health_label"] == "CRITICAL"]

    summary = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "period":            "Last 24 hours",
        "total_devices":     len(devices),
        "active_alarms":     total_active_alarms,
        "maintenance_needed": maintenance_needed,
        "critical_devices":  len(critical_devices),
        "devices":           report_data,
    }

    if not api_key:
        return {"report": summary, "narrative": "Add GROQ_API_KEY for AI narrative."}

    prompt = f"""Generate a concise daily IoT fleet health report.

Data:
{json.dumps(summary, indent=2)}

Write a professional 3-paragraph executive summary covering:
1. Overall fleet health status
2. Devices needing immediate attention
3. Recommended actions for today

Be direct and actionable. Use exact device names and numbers."""

    try:
        narrative = await _call_groq(api_key, [{"role": "user", "content": prompt}], max_tokens=600, temperature=0.3, model=GROQ_MODEL_DEEP)
        return {"report": summary, "narrative": narrative, "engine": f"groq/{GROQ_MODEL_DEEP}"}
    except Exception as exc:
        return {"report": summary, "narrative": f"AI unavailable: {exc}"}


# ── Groq usage endpoint ───────────────────────────────────────────────────────

@router_data.get("/usage")
def get_groq_usage(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get current Groq API usage for the authenticated user.
    Returns requests used, limit, and reset time.
    """
    # Excluded accounts have unlimited access
    if getattr(current_user, "email", "").lower() in GROQ_RATE_LIMIT_EXCLUDED:
        return {
            "used": 0, "limit": 999999, "remaining": 999999,
            "resets_in_mins": 0, "window_hours": GROQ_CHAT_WINDOW_H,
            "pct_used": 0.0, "excluded": True,
        }

    from app.models.models import RateLimit
    from datetime import timedelta

    window_duration = timedelta(hours=GROQ_CHAT_WINDOW_H)
    now             = datetime.now(timezone.utc)
    window_start    = now - window_duration
    token           = f"groq:{current_user.id}"

    row = (
        db.query(RateLimit)
        .filter(
            RateLimit.token == token,
            RateLimit.window_start >= window_start,
        )
        .order_by(RateLimit.window_start.desc())
        .first()
    )

    used = row.request_count if row else 0
    resets_in_mins = GROQ_CHAT_WINDOW_H * 60
    if row:
        resets_at      = row.window_start + window_duration
        resets_in_mins = max(0, int((resets_at - now).total_seconds() / 60))

    return {
        "used":           used,
        "limit":          GROQ_CHAT_LIMIT,
        "remaining":      max(0, GROQ_CHAT_LIMIT - used),
        "resets_in_mins": resets_in_mins,
        "window_hours":   GROQ_CHAT_WINDOW_H,
        "pct_used":       round((used / GROQ_CHAT_LIMIT) * 100, 1),
    }


# ── Unified Intelligence API (Phase 10) ───────────────────────────────────────

@router_data.get("/unified/{device_id}")
def get_unified_device_intelligence(
    device_id: UUID,
    key:  Optional[str] = None,
    keys: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Unified Intelligence — two modes in one endpoint (Gap 2 / TAAT v2).

    MODE 1 — Device summary (no key params):
        GET /intelligence/unified/{device_id}
        Returns device-level: status, risk, reason, recommendation + all sub-layers.
        Existing callers unchanged — backward compatible.

    MODE 2 — Per-key enrichment (?key= or ?keys=):
        GET /intelligence/unified/{device_id}?key=temperature
        Returns KeyIntelligence for one key:
        { key, value, unit, baseline_min, baseline_max, trend, anomaly,
          risk, status, reason, recommended_action, ts }

        GET /intelligence/unified/{device_id}?keys=temperature,humidity
        Returns enriched_keys: [KeyIntelligence, ...] alongside device summary.

    Both TAAT tools and widgets use this same contract.
    """
    device = _assert_device(device_id, current_user, db)
    device_id_str = str(device_id)

    # ── Mode 2a: single key enrichment ───────────────────────────────────────
    if key:
        return get_key_intelligence(db, device_id_str, key, device=device)

    # ── Base: device-level summary ────────────────────────────────────────────
    result = get_unified_intelligence(db, device_id_str, device=device)

    # ── Mode 2b: add enriched_keys array if ?keys= requested ─────────────────
    if keys:
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        enriched = []
        for k in key_list:
            try:
                enriched.append(get_key_intelligence(db, device_id_str, k, device=device))
            except Exception:
                pass
        result["enriched_keys"] = enriched

    return result


@router_data.get("/unified/{device_id}/telemetry")
def get_widget_telemetry(
    device_id: UUID,
    key: str,
    hours: int = 24,
    limit: int = 200,
    resolution: str = "raw",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Phase 10 — Widget Telemetry Data (downsampling-ready).

        resolution=raw   → raw rows up to limit
        resolution=5min  → 5-minute AVG buckets
        resolution=1h    → hourly AVG buckets
        resolution=1d    → daily AVG buckets

    Returns min/max alongside avg for bucketed resolutions.
    """
    _assert_device(device_id, current_user, db)
    from app.services.data_service import get_aggregated_telemetry
    return get_aggregated_telemetry(
        db, str(device_id), key,
        hours=hours, limit=limit, resolution=resolution,
    )
