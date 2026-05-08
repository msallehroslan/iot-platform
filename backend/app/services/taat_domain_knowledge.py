"""
app/services/taat_domain_knowledge.py — Dynamic Industrial Domain Knowledge

Detects device type from telemetry key names and injects the appropriate
diagnostic knowledge into TAAT's system prompt.

Adding a new device type:
  1. Add a detection function: def _is_<type>(keys) -> bool
  2. Add a knowledge function: def _knowledge_<type>() -> str
  3. Register both in DEVICE_PROFILES list at the bottom

This keeps TAAT's diagnostic reasoning grounded in real engineering knowledge
regardless of which device is connected to the platform.
"""

from __future__ import annotations
from typing import Optional


# ── Key normalisation ─────────────────────────────────────────────────────────

def _has_any(keys: set, *patterns: str) -> bool:
    """True if any key contains any of the patterns (case-insensitive)."""
    return any(p in k for k in keys for p in patterns)

def _has_all(keys: set, *patterns: str) -> bool:
    """True if keys contain ALL patterns."""
    return all(any(p in k for k in keys) for p in patterns)


# ── Device type detectors ─────────────────────────────────────────────────────

def _is_pump_motor(keys: set) -> bool:
    """Pump-motor assembly — has both motor and pump velocity measurements."""
    has_motor = _has_any(keys, "motor_de", "motor_nde", "motor_velocity", "motor_speed")
    has_pump  = _has_any(keys, "pump_de", "pump_nde", "pump_velocity", "pump_speed")
    return has_motor and has_pump

def _is_motor_only(keys: set) -> bool:
    """Standalone motor — velocity/speed but no separate pump measurements."""
    has_motor = _has_any(keys, "motor_de", "motor_nde", "motor_velocity", "motor_speed", "motor_rpm")
    has_pump  = _has_any(keys, "pump_de", "pump_nde", "pump_velocity", "pump_speed")
    return has_motor and not has_pump

def _is_compressor(keys: set) -> bool:
    return _has_any(keys, "compressor", "suction_pressure", "discharge_pressure", "compression_ratio")

def _is_conveyor(keys: set) -> bool:
    return _has_any(keys, "belt_speed", "conveyor_speed", "belt_tension", "conveyor_load", "belt_slip")

def _is_hvac(keys: set) -> bool:
    return (_has_any(keys, "supply_temp", "return_temp", "chilled_water", "cooling_load", "hvac")
            or (_has_any(keys, "fan_speed") and _has_any(keys, "temperature", "humidity")))

def _is_power_meter(keys: set) -> bool:
    return _has_any(keys, "voltage", "current", "power_factor", "active_power",
                    "reactive_power", "frequency_hz", "kwh", "amps", "volts")

def _is_flow_sensor(keys: set) -> bool:
    return _has_any(keys, "flow_rate", "flow_velocity", "volumetric_flow",
                    "mass_flow", "liquid_level", "tank_level")

def _is_vibration_sensor(keys: set) -> bool:
    return _has_any(keys, "vibration", "acceleration", "rms_velocity",
                    "displacement", "g_force", "accel_x", "accel_y", "accel_z")

def _is_environmental(keys: set) -> bool:
    """Pure environmental sensor — temp/humidity/pressure but no machinery."""
    machinery = _has_any(keys, "velocity", "speed", "rpm", "motor", "pump",
                         "voltage", "current", "flow", "vibration", "compressor")
    environmental = _has_any(keys, "temperature", "humidity", "pressure", "co2",
                              "lux", "air_quality", "tvoc")
    return environmental and not machinery

def _is_led_relay(keys: set) -> bool:
    """LED/relay control device — digital outputs."""
    return _has_any(keys, "led", "relay", "output", "gpio", "digital_out", "switch")


# ── Domain knowledge blocks ───────────────────────────────────────────────────

def _knowledge_pump_motor() -> str:
    return """ROTATING MACHINERY DOMAIN KNOWLEDGE (Pump-Motor Assembly):
- DE = Drive End (shaft side, connected to coupling). NDE = Non-Drive End (opposite side).
- Motor DE and Pump DE should move TOGETHER when coupling is healthy.
- ASYMMETRIC DE/NDE: Motor-DE drops sharply but Motor-NDE stays stable → drive-end fault (bearing wear, coupling slip, misalignment). Fault is physically at the shaft connection point.
- Motor-DE DROP + Pump-DE RISE → classic coupling slip: motor spinning but not transferring energy to pump shaft. Check coupling condition immediately.
- Motor-DE DROP + Pump-DE DROP together → load reduction or speed change (normal, not a fault).
- Pump-DE and Pump-NDE diverging → impeller imbalance or pump-side bearing issue.
- Temperature rising WITH velocity changes → friction increase, confirms mechanical wear.
- Interpret DE/NDE asymmetry FIRST before any other conclusion."""


def _knowledge_motor_only() -> str:
    return """ROTATING MACHINERY DOMAIN KNOWLEDGE (Standalone Motor):
- DE = Drive End (load-connected end). NDE = Non-Drive End (fan/encoder end).
- DE/NDE asymmetry indicates bearing fault at the asymmetric end — not a general speed change.
- Velocity/RPM declining steadily → possible load increase, bearing wear, or electrical issue.
- Velocity fluctuating (high variance) → mechanical looseness, belt slip, or intermittent electrical fault.
- Temperature rising with velocity changes → winding insulation stress or bearing friction.
- Sudden velocity drop to zero while powered → overcurrent trip or mechanical seizure."""


def _knowledge_compressor() -> str:
    return """COMPRESSOR DOMAIN KNOWLEDGE:
- Suction pressure dropping while discharge pressure holds → refrigerant leak or expansion valve fault.
- Both pressures dropping together → compressor capacity loss (wear or valve fault).
- Compression ratio (discharge/suction) rising → increased load, restriction in discharge line, or condenser fouling.
- Temperature at discharge rising above normal → insufficient cooling, oil loss, or valve leakage.
- High vibration + pressure fluctuation → liquid slugging or mechanical looseness.
- Suction pressure oscillating → hunting expansion valve or refrigerant charge imbalance."""


def _knowledge_conveyor() -> str:
    return """CONVEYOR DOMAIN KNOWLEDGE:
- Belt speed declining under load → motor overload, belt slipping, or drive pulley wear.
- Belt slip (speed lower than drive speed) → belt tension loss, pulley wear, or overloading.
- Belt tension rising → material blockage, seized idler roller, or mis-tracking.
- Speed oscillating → variable load, damaged belt splice, or drive coupling wear.
- Motor current rising with speed declining → mechanical resistance increase (blockage, seized component).
- Temperature rising at drive components → friction from misalignment or lack of lubrication."""


def _knowledge_hvac() -> str:
    return """HVAC DOMAIN KNOWLEDGE:
- Supply temp rising while setpoint unchanged → reduced cooling capacity, refrigerant loss, or dirty coil.
- Supply-Return temp differential (ΔT) shrinking → reduced airflow or coil fouling.
- Fan speed rising with no setpoint change → static pressure increase (dirty filters, damper issue).
- Humidity rising despite dehumidification → coil not reaching dew point (insufficient cooling).
- Supply temp oscillating → hunting controls, short-cycling, or refrigerant charge issue.
- High supply temp + high return temp → complete cooling loss (compressor fault or refrigerant leak)."""


def _knowledge_power_meter() -> str:
    return """POWER MONITORING DOMAIN KNOWLEDGE:
- Power factor below 0.85 → excessive reactive loads (motors, transformers) — consider capacitor correction.
- Voltage dropping under load → undersized supply, high impedance connection, or supply issue.
- Current rising without load increase → insulation degradation, partial fault, or equipment overloading.
- Voltage and current asymmetry across phases → phase imbalance, single-phasing risk.
- Frequency deviation → generator instability or grid disturbance.
- kWh consumption rising with same production → efficiency loss, equipment degradation, or energy waste."""


def _knowledge_flow_sensor() -> str:
    return """FLOW MONITORING DOMAIN KNOWLEDGE:
- Flow rate declining with pump running → pipe blockage, valve partially closed, or pump wear.
- Flow rate zero with pump running → complete blockage, valve closed, or pump cavitation/failure.
- Flow fluctuating → cavitation, air entrainment, or unstable pump operation.
- Tank level rising unexpectedly → inlet flow exceeding outlet, blockage downstream.
- Tank level falling unexpectedly → leak, outlet valve open, or inlet supply failure.
- Flow and pressure both dropping → supply-side issue. Flow dropping with pressure rising → downstream restriction."""


def _knowledge_vibration_sensor() -> str:
    return """VIBRATION MONITORING DOMAIN KNOWLEDGE:
- Overall RMS velocity rising → general mechanical deterioration. >7 mm/s = warning, >11 mm/s = danger.
- High frequency vibration (>1kHz) → bearing defects, gear mesh, or electrical frequencies.
- Low frequency vibration (1-10× RPM) → imbalance (1×), misalignment (2×), looseness (multiple ×).
- X/Y/Z axis asymmetry → directional fault (misalignment, bent shaft, resonance in one plane).
- Sudden spike then return → impact event (loose component, material strike, sudden load change).
- Gradual trend upward → progressive wear. Sudden jump → new fault or component failure event."""


def _knowledge_environmental() -> str:
    return """ENVIRONMENTAL MONITORING DOMAIN KNOWLEDGE:
- Temperature rising beyond setpoint → HVAC failure, heat source added, or insulation loss.
- Humidity rising rapidly → condensation risk, moisture ingress, or HVAC dehumidification loss.
- CO2 rising → ventilation inadequate for occupancy level.
- Temperature and humidity correlated → normal (warmer air holds more moisture). Uncorrelated → external moisture source.
- Pressure dropping → doors/windows opened, ventilation imbalance, or barometric change."""


def _knowledge_led_relay() -> str:
    return """CONTROL DEVICE DOMAIN KNOWLEDGE:
- LED/relay states are binary outputs — ON/OFF or 0/1.
- Unexpected state changes without RPC command → check for spurious triggers in alarm rules or scheduled actions.
- Verify RPC ACK status before assuming command executed — device may have gone offline before handling command.
- Multiple relay state changes in short period → possible control loop oscillation or alarm rule trigger cycling."""


# ── Profile registry ──────────────────────────────────────────────────────────
# Order matters — more specific detectors first

DEVICE_PROFILES = [
    ("pump_motor",    _is_pump_motor,       _knowledge_pump_motor),
    ("motor",         _is_motor_only,       _knowledge_motor_only),
    ("compressor",    _is_compressor,       _knowledge_compressor),
    ("conveyor",      _is_conveyor,         _knowledge_conveyor),
    ("hvac",          _is_hvac,             _knowledge_hvac),
    ("power_meter",   _is_power_meter,      _knowledge_power_meter),
    ("flow_sensor",   _is_flow_sensor,      _knowledge_flow_sensor),
    ("vibration",     _is_vibration_sensor, _knowledge_vibration_sensor),
    ("environmental", _is_environmental,    _knowledge_environmental),
    ("led_relay",     _is_led_relay,        _knowledge_led_relay),
]


# ── Public API ────────────────────────────────────────────────────────────────

def detect_device_type(telemetry_keys: list[str]) -> str:
    """
    Detect device type from telemetry key names.
    Returns profile name or 'generic'.
    """
    keys = {k.lower() for k in telemetry_keys}
    for name, detector, _ in DEVICE_PROFILES:
        if detector(keys):
            return name
    return "generic"


def get_domain_knowledge(telemetry_keys: list[str]) -> str:
    """
    Return domain-specific diagnostic knowledge for injection into TAAT system prompt.
    Detects device type from key names — no configuration required.
    Returns empty string for unknown device types.
    """
    keys = {k.lower() for k in telemetry_keys}
    matched = []
    for name, detector, knowledge_fn in DEVICE_PROFILES:
        if detector(keys):
            matched.append(knowledge_fn())
    return "\n\n".join(matched) if matched else ""
