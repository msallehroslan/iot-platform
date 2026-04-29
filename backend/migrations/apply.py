"""
migrations/apply.py — Idempotent database migrations.

Run this ONCE against an existing database that was created before these
migrations were added. On a fresh deployment the table and constraint are
created correctly by create_all() at startup, so this script is only needed
if you have an existing Render PostgreSQL database.

Usage:
    python migrations/apply.py

The script is safe to run multiple times — every statement uses IF NOT EXISTS
or checks before altering so it never fails on a database that already has
the changes applied.
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/iotdb"
)

try:
    import psycopg2
except ImportError:
    logger.error("psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


MIGRATIONS = [
    {
        "id":   "001_latest_telemetry_unique_constraint",
        "desc": "Add unique constraint on (device_id, key) to latest_telemetry table",
        "sql":  """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_latest_telemetry_device_key
            ON latest_telemetry (device_id, key);
        """,
    },
    {
        "id":   "002_remove_duplicate_latest_telemetry_rows",
        "desc": "Remove any duplicate (device_id, key) rows that accumulated before the constraint",
        "sql":  """
            DELETE FROM latest_telemetry
            WHERE id NOT IN (
                SELECT DISTINCT ON (device_id, key) id
                FROM latest_telemetry
                ORDER BY device_id, key, ts DESC
            );
        """,
    },
]


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # Create a migrations tracking table if it doesn't exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id          TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ DEFAULT now()
        );
    """)
    conn.commit()

    for m in MIGRATIONS:
        cur.execute("SELECT 1 FROM _migrations WHERE id = %s", (m["id"],))
        if cur.fetchone():
            logger.info("  skip (already applied): %s", m["id"])
            continue

        logger.info("  applying: %s — %s", m["id"], m["desc"])
        try:
            cur.execute(m["sql"])
            cur.execute(
                "INSERT INTO _migrations (id) VALUES (%s)",
                (m["id"],)
            )
            conn.commit()
            logger.info("  ✓ done: %s", m["id"])
        except Exception as exc:
            conn.rollback()
            logger.error("  ✗ failed: %s — %s", m["id"], exc)
            sys.exit(1)

    cur.close()
    conn.close()
    logger.info("All migrations applied.")


if __name__ == "__main__":
    run()


# Migration 003: Assign NULL tenant_id devices to the first tenant
# Devices created before the auth fix have tenant_id = NULL.
# This assigns them to the oldest tenant in the database as a best-effort fix.
# In a real system you'd match them to their creator's tenant.
MIGRATIONS += [
    {
        "id":   "003_assign_null_tenant_devices",
        "desc": "Assign devices with NULL tenant_id to the first available tenant",
        "sql":  """
            UPDATE devices
            SET tenant_id = (SELECT id FROM tenants ORDER BY created_at ASC LIMIT 1)
            WHERE tenant_id IS NULL
              AND (SELECT COUNT(*) FROM tenants) > 0;
        """,
    },
]
