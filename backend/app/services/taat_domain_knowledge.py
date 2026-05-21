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

def _is_gluciq(keys: set) -> bool:
    """GlucIQ blood glucose monitoring system."""
    return _has_any(keys, "glucose_mmol", "glucose_prediction",
                    "carbs_iob", "insulin_iob", "rmse_roll", "ceg_ab_roll")

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

def _knowledge_gluciq() -> str:
    return """GLUCIQ BLOOD GLUCOSE MONITORING DOMAIN KNOWLEDGE (T1DM Patient):

CLINICAL THRESHOLDS (Malaysian standard, mmol/L):
- Hypoglycemia:   glucose < 4.0 mmol/L → DANGEROUS — patient needs sugar immediately
- Target range:   4.0 – 10.0 mmol/L   → NORMAL — no action needed
- Hyperglycemia:  glucose > 10.0 mmol/L → needs insulin correction

TELEMETRY KEYS EXPLAINED:
- glucose_mmol:             current CGM reading in mmol/L (updates every second)
- glucose_prediction_mmol:  GlucIQ model prediction 30 minutes ahead
- risk_status:              HYPOGLYCEMIA_RISK / TARGET_RANGE / HYPERGLYCEMIA_RISK
- current_risk:             live risk based on current glucose (updates every second)
- velocity:                 glucose rate of change (mmol/L per 5 min)
                            > +0.5 = rising fast (post-meal)
                            < -0.5 = dropping fast (insulin action or exercise)
- carbs_iob:                carbohydrates on board in grams (exponential decay of recent meals)
- insulin_iob:              insulin on board in units (exponential decay of recent doses)
- carbs_input:              carbs entered this interval (grams)
- insulin_input:            insulin dosed this interval (units)
- online_updates:           number of self-learning gradient updates
                            higher = model more personalised to this patient

ACCURACY METRICS:
- rmse_roll:    rolling RMSE in mmol/L — lower is better, target < 1.5 mmol/L
- mae_roll:     rolling MAE in mmol/L — lower is better
- mard_roll:    mean absolute relative difference in % — target < 20%
- ceg_ab_roll:  Clarke Error Grid Zone A+B percentage
                > 95% = clinically safe predictions
                < 80% = dangerous prediction errors

SELF-LEARNING SYSTEM:
- GlucIQ uses online gradient descent to personalise predictions for each patient
- Only the personalisation gate layers update (LSTM/GRU backbone stays frozen)
- Model accuracy improves as online_updates increases over days
- Patient profile (mean glucose, TIR, hypo rate etc.) auto-updates every 24 hours

PATIENT CONTEXT (Patient 2 — GlucIQ-Patient2-Demo):
- Simulated Malaysian T1DM patient in reactive physiological mode
- Randomised daily schedule: breakfast, lunch, dinner + random snacks/corrections
- Sick days (~14% of days): reduced insulin sensitivity → higher glucose
- Exercise days (~20% of days): increased insulin sensitivity → lower glucose
- Baseline target glucose: 6.5 mmol/L

CLINICAL INTERPRETATION GUIDE:
- If glucose_prediction_mmol < 4.0 AND current glucose is dropping (negative velocity)
  → warn about impending hypoglycemia, recommend carbohydrate intake
- If glucose_prediction_mmol > 10.0 AND carbs_iob is high
  → expected post-meal rise, monitor and consider correction bolus
- If rmse_roll > 2.0 → prediction accuracy is poor, treat predictions with caution
- If ceg_ab_roll < 85% → significant prediction errors, check model status
- If online_updates = 0 → model just started, predictions are generic (less accurate)
- If online_updates > 100 → model well-personalised to this patient"""


def _knowledge_pump_motor() -> str:
    return """ROTATING MACHINERY DOMAIN KNOWLEDGE (Pump-Motor Assembly):

── DE/NDE ASYMMETRY (interpret this FIRST) ───────────────────────────────────
- DE = Drive End (shaft side, connected to coupling). NDE = Non-Drive End (opposite side).
- Motor DE and Pump DE should move TOGETHER when coupling is healthy.
- Motor-DE drops sharply, Motor-NDE stable → drive-end fault (bearing wear, coupling slip, misalignment). Fault is at the shaft connection point.
- Motor-DE drops + Pump-DE RISES → classic coupling slip: motor spinning but not transferring torque to pump shaft. Inspect coupling immediately.
- Motor-DE drops + Pump-DE drops together → load reduction or speed change — normal, not a fault.
- Pump-DE and Pump-NDE diverging → impeller imbalance or pump-side bearing fault.
- Temperature rising WITH velocity changes → friction increase, confirms mechanical wear.

── BEARING FAULT STAGES & SEVERITY (ISO 10816-3) ────────────────────────────
Stage 1 — Early (incipient): High-frequency ultrasonic noise only. No change in velocity RMS.
  → Action: increase monitoring frequency, no immediate intervention needed.
Stage 2 — Developing: High-frequency harmonics appear in spectrum. Slight RMS increase.
  Velocity RMS: 1.8–2.8 mm/s → Zone A (new machine acceptable). Monitor trend closely.
Stage 3 — Advanced: Sub-harmonics and sidebands visible. Audible change in noise.
  Velocity RMS: 2.8–4.5 mm/s → Zone B (alert threshold). Schedule maintenance.
Stage 4 — Critical/Failure imminent: Broadband noise floor rising. Temperature increase.
  Velocity RMS: > 4.5 mm/s → Zone C/D (danger — take out of service immediately).
- ISO 10816-7 governs rotodynamic pumps specifically (shaft height, rigid vs flexible mount).
- Set WARNING alarm at Zone B/C boundary (4.5 mm/s RMS). Set DANGER/TRIP at Zone C/D (7.1 mm/s RMS).
- Change-based alarm: alert if velocity increases >25% above rolling baseline — catches faults earlier than absolute thresholds alone.
- DE bearing faults: typically present as high-frequency sidebands around shaft frequency.
- NDE bearing faults: often appear at 2× shaft frequency with modulation.

── CAVITATION DETECTION & CAUSES ────────────────────────────────────────────
Cavitation = vapour bubbles forming in low-pressure suction zone, then collapsing violently at impeller.
Telemetry signatures:
- Flow rate drops while pump is running and speed is unchanged → suction starvation.
- Vibration increases — especially axial direction — with broadband high-frequency noise floor rising.
- Vane pass frequency (VPF = RPM/60 × number of impeller vanes) amplitude spikes.
- Motor current rises as pump works harder against reduced hydraulic output.
- Suction pressure (if instrumented) drops below NPSH required.
Causes to diagnose:
- Suction head too low (tank level drop, suction valve partially closed).
- Fluid temperature too high → vapour pressure rises → NPSH available decreases.
- Flow rate too high (operating far right of pump curve, beyond BEP).
- Suction pipe too long, small bore, or has too many fittings — increases suction losses.
- Air entrainment in suction line.
Progressive damage: pitting and erosion of impeller vanes, eventual impeller destruction.
Cavitation sounds like gravel rattling or clicking inside the pump casing.

── SEAL FAILURE PATTERNS ────────────────────────────────────────────────────
Mechanical seal is most common cause of pump downtime. Failure is usually gradual.
Early warning telemetry signs:
- Temperature rising near seal area (seal chamber or bearing frame temp sensor) by >5°C above baseline.
- Vibration amplitude increasing — mechanical seals are damaged by shaft vibration and misalignment.
- Flow rate declining with speed unchanged → possible internal bypass through seal area.
Failure progression:
1. Thin lubrication film breaks down → face friction increases → temperature rises.
2. Elastomer degradation (O-rings) → intermittent leakage begins.
3. Face grooves and wear → steady drip becomes continuous leak.
4. If dry-running occurs → seal faces fail within seconds.
Root causes to check:
- High vibration / shaft misalignment → uneven face wear (fix alignment before replacing seal).
- Cavitation → impeller erosion debris contaminates seal faces.
- Temperature excursion beyond elastomer limits → O-ring hardening and cracking.
- Insufficient flush flow → solids accumulate on seal faces.
Note: If seal temperature rises AND vibration rises together → likely misalignment-driven failure.
      If seal temperature rises with stable vibration → likely flush/cooling issue or dry running.

── IMPELLER WEAR & HYDRAULIC FAULTS ─────────────────────────────────────────
- Impeller wear (erosion, corrosion, cavitation pitting) → head and flow rate both decline at same speed.
- Wear ring clearance increase → internal recirculation increases → efficiency drops, flow drops.
- Vane pass frequency (VPF = shaft RPM/60 × vane count) amplitude rising → impeller imbalance or damage.
- Impeller imbalance → 1× RPM vibration elevated — symmetric across DE and NDE.
- Broken/chipped vane → 1× RPM plus sub-harmonics. Vibration asymmetric.
- Off-BEP operation (below 70% or above 120% of design flow):
    Low flow: internal recirculation, suction vortices, subsynchronous vibration.
    High flow: increased NPSH required, cavitation risk, bearing overload.
- BEP (Best Efficiency Point): the flow rate at which hydraulic efficiency is maximum.
  Operating >±20% from BEP significantly reduces bearing life and increases vibration.
- Impeller trimming (diameter reduction) lowers H-Q curve and BEP flow proportionally.

── LUBRICATION & MAINTENANCE INTERVALS ──────────────────────────────────────
Grease-lubricated bearings (most common for pump motors up to 300kW):
- Relubrication interval: typically 2,000–4,000 hours at rated speed and temperature.
- Halve interval if: ambient >40°C, speed >3,000 RPM, wet/dirty environment.
- Over-greasing is a leading cause of bearing failure — excess grease churns, overheats, loses viscosity.
- Under-greasing: metal-to-metal contact, early spalling, high temperature.
Oil-lubricated bearings (larger pumps, sleeve bearings):
- Oil change: every 2,000 hours or annually (whichever first).
- Check oil colour — dark/milky = water ingress or oxidation. Metallic particles = bearing wear.
Telemetry-based maintenance triggers:
- Bearing temperature rising >10°C above established baseline → relubricate or inspect.
- Bearing temperature sustained >80°C (grease) or >70°C (oil) → ALERT — inspect immediately.
- Bearing temperature >100°C → CRITICAL — shut down, risk of bearing seizure.
- Vibration trending upward over 30+ days → schedule bearing inspection.
Mechanical seal service life: typically 12–24 months in clean service. Halve in abrasive/chemical service.

── VIBRATION FREQUENCY SIGNATURES ───────────────────────────────────────────
Key frequencies to correlate with measured vibration spectrum:
- 1× shaft RPM      → imbalance, bent shaft, misalignment (1× dominant)
- 2× shaft RPM      → angular misalignment, looseness, resonance
- 0.5× shaft RPM    → oil whirl (sleeve bearings), internal recirculation at low flow
- VPF = RPM/60 × N → vane pass frequency (N = number of impeller vanes, typically 5–7)
                      elevated VPF → impeller damage, cavitation, off-BEP operation
- BPFI, BPFO        → bearing defect frequencies (inner race, outer race) — need bearing geometry
- High frequency broadband noise floor rising → cavitation, bearing spalling, loose components
- Subsynchronous (<1× RPM) → fluid instability, rotating stall at low flow
- Motor electrical frequency (50 Hz or 60 Hz) harmonics → rotor bar issues, supply imbalance
Interpretation rule: always compare spectrum to baseline. Absolute amplitude alone is misleading —
a 3 mm/s machine that increased from 1 mm/s is more concerning than a 4 mm/s stable machine.

── ELECTRICAL MOTOR FAULTS (CURRENT & WINDING) ──────────────────────────────
Motor Current Signature Analysis (MCSA) — detectable from current sensor on motor supply:
- Current rising with speed unchanged → bearing friction increasing, mechanical load increasing, or winding fault.
- Current fluctuating rhythmically → load oscillation, coupling fault, or broken rotor bar (sidebands at ±slip frequency around supply frequency).
- Phase current imbalance >2% → phase supply issue, winding imbalance, or partial short.
- Current rising + temperature rising + velocity stable → winding insulation degradation (IR test recommended).
- Current drops suddenly while speed maintained → unloading event (valve opened, pipe burst).
Winding temperature limits (standard IEC):
- Class B insulation: max 130°C (alarm >110°C, trip >125°C)
- Class F insulation: max 155°C (alarm >130°C, trip >150°C)
- Class H insulation: max 180°C (alarm >155°C, trip >175°C)
Temperature rise rules:
- Winding temp = ambient + temperature rise rating. At 40°C ambient, Class F motor alarms at 130°C.
- Every 10°C above rated temperature halves insulation life (Arrhenius rule of thumb).
Rotor bar faults: appear as sidebands at (f_supply ± 2 × slip × f_supply) in current spectrum.

── PUMP CURVES & EFFICIENCY LOSS ────────────────────────────────────────────
H-Q curve (Head vs Flow): head decreases as flow increases. Steeper curve = more stable operation.
Efficiency curve peaks at BEP — target operation within 70–110% of BEP flow.
Detecting efficiency loss from telemetry:
- Power consumption (kW) rising with same flow and head → efficiency degrading (impeller wear, wear ring wear).
- Flow rate dropping with head and speed unchanged → internal recirculation, wear ring clearance increased.
- Head dropping with flow and speed unchanged → impeller erosion, gas ingestion, or recirculation.
- Pump curve shift (head drops across all flows at same speed) → generalised impeller wear.
- NPSH margin shrinking (suction pressure falling toward vapour pressure) → cavitation risk imminent.
Efficiency loss cost: a 5% efficiency loss on a 75kW pump running 8,000 hr/year costs ~£1,500–£2,000/year in extra energy.
Affinity laws (speed change effects):
- Flow ∝ speed. Head ∝ speed². Power ∝ speed³.
- Halving pump speed → power drops to 12.5% of original. VFD control is highly effective for variable-demand systems.
Worn wear rings: most common cause of gradual performance loss without obvious vibration change.
Check if head and flow are declining together at constant speed — strong indicator of wear ring clearance growth."""


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
# GlucIQ is first so it always matches before generic detectors

DEVICE_PROFILES = [
    ("gluciq",        _is_gluciq,           _knowledge_gluciq),
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
