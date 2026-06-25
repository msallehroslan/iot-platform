"""
app/services/taat_tool_registry.py — Standardised Tool Registry

Every tool follows the same contract:

    Input:  ToolInput  — db, current_user, **kwargs
    Output: ToolResult — { success, data, error, tool_name }

This is the layer the executor calls. It wraps taat_tools.py and
data_service.py with:
    - Uniform error handling
    - Structured output
    - Risk declaration
    - Input validation

Tools exposed:
    READ (7):   get_devices, get_telemetry, get_alarms, get_health,
                get_anomalies, get_baseline, get_key_intelligence
    WRITE (5):  send_rpc, ack_alarm, clear_alarm, create_rule, delete_rule
    MEMORY (2): get_memory, save_memory
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Standard I/O contract ─────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Uniform output for every tool call."""
    tool_name: str
    success:   bool
    data:      Dict[str, Any] = field(default_factory=dict)
    error:     Optional[str]  = None

    def to_dict(self) -> dict:
        return {
            "tool":    self.tool_name,
            "success": self.success,
            "data":    self.data,
            "error":   self.error,
        }

    def __repr__(self):
        return f"ToolResult({self.tool_name}, success={self.success})"


def _result(name: str, data: dict) -> ToolResult:
    return ToolResult(tool_name=name, success=True, data=data)


def _error(name: str, msg: str) -> ToolResult:
    return ToolResult(tool_name=name, success=False, error=msg)


# ── Tool definitions ──────────────────────────────────────────────────────────

async def tool_get_devices(
    db: Session, current_user, **_
) -> ToolResult:
    try:
        from app.services.taat_tools import tool_get_devices as _fn
        return _result("get_devices", _fn(db, current_user))
    except Exception as e:
        return _error("get_devices", str(e))


async def tool_get_telemetry(
    db: Session, current_user,
    device_id: str, **_
) -> ToolResult:
    if not device_id:
        return _error("get_telemetry", "device_id required")
    try:
        from app.services.data_service import get_latest_telemetry
        return _result("get_telemetry", get_latest_telemetry(db, device_id))
    except Exception as e:
        return _error("get_telemetry", str(e))


async def tool_get_alarms(
    db: Session, current_user,
    device_id: str, **_
) -> ToolResult:
    if not device_id:
        return _error("get_alarms", "device_id required")
    try:
        from app.services.data_service import get_active_alarms
        return _result("get_alarms", get_active_alarms(db, device_id))
    except Exception as e:
        return _error("get_alarms", str(e))


async def tool_get_health(
    db: Session, current_user,
    device_id: str, **_
) -> ToolResult:
    if not device_id:
        return _error("get_health", "device_id required")
    try:
        from app.services.data_service import get_health_summary
        return _result("get_health", get_health_summary(db, device_id))
    except Exception as e:
        return _error("get_health", str(e))


async def tool_get_anomalies(
    db: Session, current_user,
    device_id: str, hours: int = 24, **_
) -> ToolResult:
    if not device_id:
        return _error("get_anomalies", "device_id required")
    try:
        from app.services.data_service import get_anomaly_summary
        return _result("get_anomalies", get_anomaly_summary(db, device_id, hours=hours))
    except Exception as e:
        return _error("get_anomalies", str(e))


async def tool_get_baseline(
    db: Session, current_user,
    device_id: str, key: Optional[str] = None, **_
) -> ToolResult:
    if not device_id:
        return _error("get_baseline", "device_id required")
    try:
        from app.services.taat_tools import tool_get_baseline as _fn
        return _result("get_baseline", _fn(db, device_id, key=key))
    except Exception as e:
        return _error("get_baseline", str(e))


async def tool_get_key_intelligence(
    db: Session, current_user,
    device_id: str, key: str, **_
) -> ToolResult:
    if not device_id or not key:
        return _error("get_key_intelligence", "device_id and key required")
    try:
        from app.services.data_service import get_key_intelligence
        return _result("get_key_intelligence", get_key_intelligence(db, device_id, key))
    except Exception as e:
        return _error("get_key_intelligence", str(e))


async def tool_send_rpc(
    db: Session, current_user,
    device_name: str, method: str = "set", params: dict = None, **_
) -> ToolResult:
    if not device_name:
        return _error("send_rpc", "device_name required")
    try:
        from app.services.taat_tools import tool_send_rpc as _fn
        result = await _fn(db, current_user, device_name, method, params or {})
        return ToolResult(
            tool_name="send_rpc",
            success=result.get("success", False),
            data=result,
            error=result.get("reason") if not result.get("success") else None,
        )
    except Exception as e:
        return _error("send_rpc", str(e))


async def tool_ack_alarm(
    db: Session, current_user,
    device_id: Optional[str] = None,
    severity: Optional[str] = None, **_
) -> ToolResult:
    try:
        from app.services.taat_tools import tool_ack_alarm as _fn
        return _result("ack_alarm", _fn(db, current_user, device_id, severity))
    except Exception as e:
        return _error("ack_alarm", str(e))


async def tool_clear_alarm(
    db: Session, current_user,
    device_id: Optional[str] = None,
    severity: Optional[str] = None, **_
) -> ToolResult:
    try:
        from app.services.taat_tools import tool_clear_alarm as _fn
        return _result("clear_alarm", _fn(db, current_user, device_id, severity))
    except Exception as e:
        return _error("clear_alarm", str(e))


async def tool_create_rule(
    db: Session, current_user,
    devices: list,
    key: str, condition: str, threshold: float,
    severity: str = "WARNING",
    device_name: Optional[str] = None,
    alarm_type: Optional[str] = None, **_
) -> ToolResult:
    if not key:
        return _error("create_rule", "key required")
    try:
        from app.services.taat_tools import tool_create_rule as _fn
        return _result("create_rule", _fn(
            db, current_user, devices,
            key=key, condition=condition, threshold=threshold,
            severity=severity, device_name=device_name, alarm_type=alarm_type,
        ))
    except Exception as e:
        return _error("create_rule", str(e))


async def tool_delete_rule(
    db: Session, current_user,
    key: Optional[str] = None,
    delete_all: bool = False, **_
) -> ToolResult:
    try:
        from app.services.taat_tools import tool_delete_rule as _fn
        return _result("delete_rule", _fn(db, current_user, key=key, delete_all=delete_all))
    except Exception as e:
        return _error("delete_rule", str(e))


async def tool_get_memory(
    db: Session, current_user,
    device_name: Optional[str] = None,
    key: Optional[str] = None, **_
) -> ToolResult:
    try:
        from app.services.taat_memory_service import get_relevant_memories
        mems = get_relevant_memories(
            db, current_user.tenant_id,
            device_name=device_name, key=key
        )
        return _result("get_memory", {"count": len(mems), "memories": mems})
    except Exception as e:
        return _error("get_memory", str(e))


async def tool_write_memory(
    db: Session, current_user,
    memory_type: str, content: str, **_
) -> ToolResult:
    try:
        from app.services.taat_memory_service import save_memory
        ok = save_memory(db, current_user.tenant_id, memory_type, content,
                         user_id=current_user.id)
        return ToolResult(tool_name="write_memory", success=ok,
                          data={"memory_type": memory_type})
    except Exception as e:
        return _error("write_memory", str(e))



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


async def tool_get_pump_analysis(
    db: Session, current_user,
    device_id: str, **_
) -> ToolResult:
    """DE/NDE asymmetry detection + pump efficiency estimation."""
    if not device_id:
        return _error("get_pump_analysis", "device_id required")
    try:
        from app.services.taat_tools import tool_get_pump_analysis as _fn
        return _result("get_pump_analysis", _fn(db, device_id))
    except Exception as e:
        return _error("get_pump_analysis", str(e))


# ── Registry map: name → callable ────────────────────────────────────────────

REGISTRY: Dict[str, Callable] = {
    "get_devices":           tool_get_devices,
    "get_telemetry":         tool_get_telemetry,
    "get_alarms":            tool_get_alarms,
    "get_health":            tool_get_health,
    "get_anomalies":         tool_get_anomalies,
    "get_baseline":          tool_get_baseline,
    "get_key_intelligence":  tool_get_key_intelligence,
    "send_rpc":              tool_send_rpc,
    "ack_alarm":             tool_ack_alarm,
    "clear_alarm":           tool_clear_alarm,
    "create_rule":           tool_create_rule,
    "delete_rule":           tool_delete_rule,
    "get_memory":            tool_get_memory,
    "write_memory":          tool_write_memory,
    "get_pump_analysis":     tool_get_pump_analysis,
    "get_telemetry_history": tool_get_telemetry_history,
}


async def call(
    name: str,
    db: Session,
    current_user,
    **kwargs,
) -> ToolResult:
    """
    Call a tool by name. Returns ToolResult(success=False) if tool not found.
    """
    fn = REGISTRY.get(name)
    if not fn:
        return _error(name, f"Unknown tool '{name}'")
    return await fn(db=db, current_user=current_user, **kwargs)
