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
    "temp_outlet":        (42.0, 6.0, 2.0, 24),
    "pressure_in":        (280.0, 20.0, 8.0, 24),  # suction kPa — NORMAL >=250
    "pressure_out":       (450.0, 20.0, 8.0, 24),  # discharge kPa — NORMAL >=400
    "shaft_rpm":          (1450.0, 15.0, 5.0, 24), # RPM
}


def generate_points(key: str, start: datetime, end: datetime, interval_min: int):
    base, amp, noise, period_hours = KEY_PROFILES.get(key, (50.0, 10.0, 2.0, 24))
    t = start
    period = timedelta(hours=period_hours)
    while t <= end:
        hours_elapsed = (t - start).total_seconds() / 3600
        phase = 2 * math.pi * (hours_elapsed / period_hours)
        value = base + amp * math.sin(phase) + random.uniform(-noise, noise)
        # Occasional anomaly spike (1% of points) — exercises anomaly_service too
        if random.random() < 0.01:
            value += random.choice([-1, 1]) * amp * 4
        yield t, round(value, 3)
        t += timedelta(minutes=interval_min)


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
    latest_values = {}

    for key in keys:
        points = list(generate_points(key, start, end, interval_min))
        rows = [(str(uuid.uuid4()), device_id, key, v, ts) for ts, v in points]

        execute_values(
            cur,
            """
            INSERT INTO telemetry_data (id, device_id, key, value_num, ts)
            VALUES %s
            """,
            rows,
            page_size=2000,
        )
        total_inserted += len(rows)
        latest_values[key] = points[-1][1] if points else None
        print(f"  {key}: {len(rows)} points over {days}d @ {interval_min}min")

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

    print(f"\nDone — inserted {total_inserted} telemetry_data rows across {len(keys)} keys.")
    print("Tip: run baseline/health scoring next so widgets that depend on them have data:")
    print("  docker exec -it backend python -c \"")
    print("from app.core.database import SessionLocal; from app.services import baseline_service, health_service")
    print("db = SessionLocal()")
    print(f"baseline_service.update_baselines_for_device(db, '{device_id}')")
    print("from app.models.models import Device")
    print(f"health_service.score_device(db, db.query(Device).get('{device_id}')); db.commit()")
    print("\"")


if __name__ == "__main__":
    main()
