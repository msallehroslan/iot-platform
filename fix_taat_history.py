import re

# ── 1. Add tool_get_telemetry_history to taat_tools.py ───────────────────────
with open("/mnt/apps/iot-platform/backend/app/services/taat_tools.py", "r") as f:
    content = f.read()

new_tool = '''
def tool_get_telemetry_history(
    db: Session,
    device_id: str,
    key: str,
    hours: float = 48,
    resolution: str = "1h",
) -> dict:
    """
    Fetch aggregated telemetry history for a key over a time window.
    Used for today-vs-yesterday comparisons and trend analysis.
    Default: 48h at 1h resolution — covers today + yesterday.
    """
    from app.services.data_service import get_aggregated_telemetry
    from datetime import datetime, timezone, timedelta

    data = get_aggregated_telemetry(db, device_id, key, hours=hours, limit=500, resolution=resolution)
    points = data.get("points", [])

    if not points:
        return {"device_id": device_id, "key": key, "today": [], "yesterday": [], "comparison": "no data"}

    # Split into today and yesterday buckets
    now   = datetime.now(timezone.utc)
    today_start     = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    today_pts     = []
    yesterday_pts = []

    for p in points:
        try:
            ts = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
            if ts >= today_start:
                today_pts.append(p)
            elif ts >= yesterday_start:
                yesterday_pts.append(p)
        except Exception:
            continue

    # Compute daily averages for comparison
    def _avg(pts):
        vals = [p["value"] for p in pts if p.get("value") is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    today_avg     = _avg(today_pts)
    yesterday_avg = _avg(yesterday_pts)

    if today_avg is not None and yesterday_avg is not None:
        delta     = round(today_avg - yesterday_avg, 3)
        delta_pct = round((delta / yesterday_avg) * 100, 1) if yesterday_avg != 0 else None
        comparison = f"today avg {today_avg} vs yesterday avg {yesterday_avg} (delta: {delta:+.3f}, {delta_pct:+.1f}%)" if delta_pct is not None else f"today avg {today_avg} vs yesterday avg {yesterday_avg}"
    elif today_avg is not None:
        comparison = f"today avg {today_avg} — no yesterday data yet"
    elif yesterday_avg is not None:
        comparison = f"yesterday avg {yesterday_avg} — no data yet today"
    else:
        comparison = "insufficient data for comparison"

    return {
        "device_id":     device_id,
        "key":           key,
        "hours":         hours,
        "resolution":    resolution,
        "today":         today_pts,
        "yesterday":     yesterday_pts,
        "today_avg":     today_avg,
        "yesterday_avg": yesterday_avg,
        "comparison":    comparison,
        "all_points":    points,
    }

'''

# Insert before tool_get_rpc_history
target = "def tool_get_rpc_history"
assert target in content, "Could not find insertion point in taat_tools.py"
content = content.replace(target, new_tool + target, 1)

with open("/mnt/apps/iot-platform/backend/app/services/taat_tools.py", "w") as f:
    f.write(content)
print("✅ taat_tools.py updated")


# ── 2. Register in taat_tool_registry.py ─────────────────────────────────────
with open("/mnt/apps/iot-platform/backend/app/services/taat_tool_registry.py", "r") as f:
    content = f.read()

# Add the async wrapper
new_registry_tool = '''
async def tool_get_telemetry_history(
    db: Session, current_user,
    device_id: str, key: str,
    hours: float = 48, resolution: str = "1h", **_
) -> ToolResult:
    if not device_id or not key:
        return _error("get_telemetry_history", "device_id and key required")
    try:
        from app.services.taat_tools import tool_get_telemetry_history as _fn
        return _result("get_telemetry_history", _fn(db, device_id, key, hours=hours, resolution=resolution))
    except Exception as e:
        return _error("get_telemetry_history", str(e))

'''

target = 'async def tool_get_pump_analysis'
assert target in content, "Could not find insertion point in taat_tool_registry.py"
content = content.replace(target, new_registry_tool + target, 1)

# Add to REGISTRY dict
content = content.replace(
    '"get_pump_analysis":     tool_get_pump_analysis,',
    '"get_pump_analysis":     tool_get_pump_analysis,\n    "get_telemetry_history": tool_get_telemetry_history,',
    1
)

with open("/mnt/apps/iot-platform/backend/app/services/taat_tool_registry.py", "w") as f:
    f.write(content)
print("✅ taat_tool_registry.py updated")


# ── 3. Wire into build_context in taat_planner.py ────────────────────────────
with open("/mnt/apps/iot-platform/backend/app/services/taat_planner.py", "r") as f:
    content = f.read()

old = '''        ctx["telemetry"] = _safe(tool_get_latest_telemetry, db, focus_id)
        ctx["health"]    = _safe(tool_get_device_health,    db, focus_id)
        ctx["anomalies"] = _safe(tool_get_anomalies,        db, focus_id, hours=24)
        ctx["baseline"]  = _safe(tool_get_baseline,         db, focus_id)'''

new = '''        ctx["telemetry"] = _safe(tool_get_latest_telemetry, db, focus_id)
        ctx["health"]    = _safe(tool_get_device_health,    db, focus_id)
        ctx["anomalies"] = _safe(tool_get_anomalies,        db, focus_id, hours=24)
        ctx["baseline"]  = _safe(tool_get_baseline,         db, focus_id)
        # 48h history for today-vs-yesterday comparisons
        # Use the most recent telemetry key as the primary comparison key
        _telem = ctx.get("telemetry", {})
        _keys  = list((_telem.get("values") or _telem).keys()) if isinstance(_telem, dict) else []
        if _keys:
            from app.services.taat_tools import tool_get_telemetry_history
            ctx["daily_comparison"] = _safe(
                tool_get_telemetry_history, db, focus_id,
                _keys[0], hours=48, resolution="1h"
            )'''

assert old in content, "Could not find build_context block in taat_planner.py"
content = content.replace(old, new, 1)

with open("/mnt/apps/iot-platform/backend/app/services/taat_planner.py", "w") as f:
    f.write(content)
print("✅ taat_planner.py updated")

print("\nAll done. Rebuild backend to apply.")
