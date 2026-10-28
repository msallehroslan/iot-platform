"""
seed_loadtest_data.py — Seed a device with realistic historical telemetry
so dashboard/widget load tests hit real data volume (line charts, 7d
aggregates, baselines, trend, health score) instead of an empty history.

Run from inside the backend container (reuses its DATABASE_URL):
    docker exec -it backend python scripts/seed_loadtest_data.py <device_id>

Options (env vars):
    SEED_DAYS            default 14   — how many days of history to generate
    SEED_INTERVAL_MIN    default 5    — minutes between points
    SEED_KEYS            default "temperature,humidity,pressure,vibration"

This inserts directly via psycopg2 (bulk execute_values) — much faster
than POSTing thousands of requests through the ingest API, and lets you
control the time range (ingest always stamps "now").

Re-seeding behaviour (v2)
--------------------------
Before generating, each key's baseline is anchored to whatever REAL data
the device already has *older than* the seed window (if any) — not a
hardcoded constant — so synthetic points land in the same range as the
device's actual history instead of reading as a sudden "sensor drift"
against it. Any previously-seeded points inside the seed window are
deleted first, so re-running this script doesn't pile up old bad data
that keeps skewing the baseline.

temp_outlet is generated from temp_inlet + a small realistic rise
(not independently) — generating both as unrelated series can produce
an unrealistic ΔT that breaks the thermodynamic efficiency formula in
PumpTwinWidget.jsx (η needs ΔT to be small and physically plausible).
"""
import os
import sys
import math
import random
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/iotdb")

# Per-key generator: (base, amplitude, noise, period_hours)
# Value = base + amplitude*sin(2*pi*t/period) + uniform noise
# `base` is only the FALLBACK used when the device has no real history for
# that key older than the seed window — see _anchor_base() below.
KEY_PROFILES = {
    "temperature": (28.0, 4.0, 0.6, 24),   # daily cycle, °C
    "humidity":    (65.0, 15.0, 2.0, 24),  # daily cycle, %
    "pressure":    (1013.0, 3.0, 0.5, 12), # slow drift, hPa
    "vibration":   (0.3, 0.15, 0.05, 6),   # mechanical noise, g

    # ── Pump Twin widget — matches PumpTwinWidget.jsx's ISO 10816-3 /
    # pressure thresholds (see device.additional_info key_* mapping).
    # Baselines sit comfortably in the NORMAL band; the 1%-chance spike
    # in generate_points() occasionally pushes a point into WARNING/
    # CRITICAL so the fault-diagnosis UI has something to show.
    "motor_nde_velocity": (1.2, 0.5, 0.2, 24),   # vib mm/s — NORMAL <2.3, CRITICAL >=4.5
    "motor_de_velocity":  (1.3, 0.5, 0.2, 24),
    "pump_de_velocity":   (1.5, 0.6, 0.2, 24),
    "pump_nde_velocity":  (1.4, 0.5, 0.2, 24),
    "temp_nde":           (55.0, 8.0, 3.0, 24),  # bearing °C — NORMAL <65, CRITICAL >=80
    "temp_de":            (58.0, 8.0, 3.0, 24),
    "temp_de_pump":       (56.0, 8.0, 3.0, 24),
    "temp_inlet":         (35.0, 6.0, 2.0, 24),  # fluid °C — NORMAL <50
    # temp_outlet has no independent profile — see _generate_temp_outlet()
    "pressure_in":        (280.0, 20.0, 8.0, 24),  # suction kPa — NORMAL >=250
    "pressure_out":       (450.0, 20.0, 8.0, 24),  # discharge kPa — NORMAL >=400
    "shaft_rpm":          (1450.0, 15.0, 5.0, 24), # RPM
}

# Realistic pump thermal rise across the pump (outlet is always slightly
# warmer than inlet — a few tenths to ~1.5°C under normal operation).
OUTLET_RISE_MIN, OUTLET_RISE_MAX = 0.2, 1.2


def _anchor_base(cur, device_id: str, key: str, seed_start: datetime, fallback: float) -> float:
    """
    Use the device's own real history (older than the seed window) as the
    generator's baseline, if any exists — so synthetic points read as a
    continuation of the device's actual pattern instead of a sudden drift.
    Falls back to KEY_PROFILES' hardcoded base when there's no prior data.
    """
    cur.execute(
        "SELECT AVG(value_num) FROM telemetry_data WHERE device_id = %s AND key = %s AND ts < %s",
        (device_id, key, seed_start),
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return fallback


def _clear_seed_window(cur, device_id: str, key: str, start: datetime, end: datetime) -> int:
    """Delete any previously-seeded points for this key in [start, end] so
    re-running this script doesn't accumulate old, differently-anchored
    synthetic data that keeps skewing the baseline."""
    cur.execute(
        "DELETE FROM telemetry_data WHERE device_id = %s AND key = %s AND ts >= %s AND ts <= %s",
        (device_id, key, start, end),
    )
    return cur.rowcount


def generate_points(key: str, base: float, start: datetime, end: datetime, interval_min: int):
    _, amp, noise, period_hours = KEY_PROFILES.get(key, (None, 10.0, 2.0, 24))
    t = start
    while t <= end:
        hours_elapsed = (t - start).total_seconds() / 3600
        phase = 2 * math.pi * (hours_elapsed / period_hours)
        value = base + amp * math.sin(phase) + random.uniform(-noise, noise)
        # Occasional anomaly spike (1% of points) — exercises anomaly_service too
        if random.random() < 0.01:
            value += random.choice([-1, 1]) * amp * 4
        yield t, round(value, 3)
        t += timedelta(minutes=interval_min)


def generate_temp_outlet(inlet_points):
    """Derive outlet temp from inlet + a small realistic rise, point for
    point, instead of generating it as an unrelated series (which can
    produce an unrealistic/negative ΔT that breaks PumpTwinWidget's
    thermodynamic efficiency formula)."""
    for ts, t_in in inlet_points:
        rise = random.uniform(OUTLET_RISE_MIN, OUTLET_RISE_MAX)
        yield ts, round(t_in + rise, 3)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/seed_loadtest_data.py <device_id>")
        sys.exit(1)

    device_id = sys.argv[1]
    try:
        uuid.UUID(device_id)
    except ValueError:
        print(f"'{device_id}' is not a valid UUID")
        sys.exit(1)

    days = int(os.getenv("SEED_DAYS", "14"))
    interval_min = int(os.getenv("SEED_INTERVAL_MIN", "5"))
    keys = [k.strip() for k in os.getenv("SEED_KEYS", "temperature,humidity,pressure,vibration").split(",")]

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("SELECT id, tenant_id, name FROM devices WHERE id = %s", (device_id,))
    row = cur.fetchone()
    if not row:
        print(f"Device {device_id} not found")
        sys.exit(1)
    print(f"Seeding device: {row[2]} ({device_id})")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    total_inserted = 0
    total_cleared = 0
    latest_values = {}
    inlet_points_by_run = None  # cache temp_inlet points for temp_outlet derivation

    for key in keys:
        cleared = _clear_seed_window(cur, device_id, key, start, end)
        total_cleared += cleared

        if key == "temp_outlet" and "temp_inlet" in keys:
            # Will be generated right after temp_inlet below using the SAME
            # timestamps — skip here, handled in the temp_inlet branch.
            continue

        fallback_base = KEY_PROFILES.get(key, (50.0,))[0]
        base = _anchor_base(cur, device_id, key, start, fallback_base)
        anchored = base != fallback_base
        points = list(generate_points(key, base, start, end, interval_min))
        rows = [(str(uuid.uuid4()), device_id, key, v, ts) for ts, v in points]

        execute_values(
            cur,
            "INSERT INTO telemetry_data (id, device_id, key, value_num, ts) VALUES %s",
            rows,
            page_size=2000,
        )
        total_inserted += len(rows)
        latest_values[key] = points[-1][1] if points else None
        anchor_note = f"anchored to existing mean={base:.1f}" if anchored else f"no prior data, using default base={base:.1f}"
        print(f"  {key}: {len(rows)} points over {days}d @ {interval_min}min ({anchor_note}, cleared {cleared} old)")

        if key == "temp_inlet":
            inlet_points_by_run = points

    if "temp_outlet" in keys:
        cleared = total_cleared  # already cleared above in the loop
        if inlet_points_by_run is None:
            # temp_inlet wasn't in SEED_KEYS — generate it standalone just
            # to derive outlet from, but don't persist it separately.
            fallback_base = KEY_PROFILES.get("temp_inlet", (35.0,))[0]
            base = _anchor_base(cur, device_id, "temp_inlet", start, fallback_base)
            inlet_points_by_run = list(generate_points("temp_inlet", base, start, end, interval_min))

        outlet_points = list(generate_temp_outlet(inlet_points_by_run))
        rows = [(str(uuid.uuid4()), device_id, "temp_outlet", v, ts) for ts, v in outlet_points]
        execute_values(
            cur,
            "INSERT INTO telemetry_data (id, device_id, key, value_num, ts) VALUES %s",
            rows,
            page_size=2000,
        )
        total_inserted += len(rows)
        latest_values["temp_outlet"] = outlet_points[-1][1] if outlet_points else None
        print(f"  temp_outlet: {len(rows)} points derived from temp_inlet + {OUTLET_RISE_MIN}-{OUTLET_RISE_MAX}°C rise")

    # Upsert latest_telemetry so widgets/value_card show current values immediately
    now = datetime.now(timezone.utc)
    for key, value in latest_values.items():
        cur.execute(
            """
            INSERT INTO latest_telemetry (id, device_id, key, value_num, ts)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (device_id, key)
            DO UPDATE SET value_num = EXCLUDED.value_num, ts = EXCLUDED.ts
            """,
            (str(uuid.uuid4()), device_id, key, value, now),
        )

    cur.execute(
        "UPDATE devices SET last_seen_at = %s, status = 'ACTIVE' WHERE id = %s",
        (now, device_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone — inserted {total_inserted} telemetry_data rows across {len(latest_values)} keys (cleared {total_cleared} previously-seeded rows first).")
    print("IMPORTANT: re-run baseline/health scoring so the new, anchored data replaces the old skewed baseline:")
    print("  docker exec -it backend python -c \"")
    print("from app.core.database import SessionLocal; from app.services import baseline_service, health_service")
    print("db = SessionLocal()")
    print(f"baseline_service.update_baselines_for_device(db, '{device_id}')")
    print("from app.models.models import Device")
    print(f"health_service.score_device(db, db.query(Device).get('{device_id}')); db.commit()")
    print("\"")
    print("\nNote: last_seen_at is set to NOW by this script, but the backend's")
    print("periodic offline-check will mark the device INACTIVE again ~5 minutes")
    print("after you stop sending new data — that's expected for a seeded demo")
    print("device, not a bug (see widget's 'Device is offline' banner).")


if __name__ == "__main__":
    main()
