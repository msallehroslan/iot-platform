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


MIGRATIONS += [
    {
        "id":   "004_add_provisioning_key_to_tenants",
        "desc": "Add provisioning_key column to tenants table for device self-registration",
        "sql":  """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS provisioning_key VARCHAR(64) UNIQUE;
        """,
    },
    {
        "id":   "005_backfill_provisioning_keys",
        "desc": "Generate provisioning keys for existing tenants that don't have one",
        "sql":  """
            UPDATE tenants
            SET provisioning_key = REPLACE(gen_random_uuid()::text, '-', '')
            WHERE provisioning_key IS NULL;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "006_create_telemetry_keys_table",
        "desc": "Create telemetry_keys metadata table",
        "sql":  """
            CREATE TABLE IF NOT EXISTS telemetry_keys (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                device_id   UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                key         VARCHAR(255) NOT NULL,
                label       VARCHAR(255),
                unit        VARCHAR(50),
                data_type   VARCHAR(20) NOT NULL DEFAULT 'number',
                created_at  TIMESTAMPTZ DEFAULT now(),
                updated_at  TIMESTAMPTZ,
                CONSTRAINT uq_telemetry_keys_device_key UNIQUE (device_id, key)
            );
            CREATE INDEX IF NOT EXISTS ix_telemetry_keys_device_id
                ON telemetry_keys (device_id);
        """,
    },
    {
        "id":   "007_backfill_telemetry_keys_from_latest",
        "desc": "Populate telemetry_keys from existing latest_telemetry rows",
        "sql":  """
            INSERT INTO telemetry_keys (id, device_id, key, data_type)
            SELECT
                gen_random_uuid(),
                device_id,
                key,
                CASE
                    WHEN value_num  IS NOT NULL THEN 'number'
                    WHEN value_bool IS NOT NULL THEN 'boolean'
                    ELSE 'string'
                END
            FROM latest_telemetry
            ON CONFLICT (device_id, key) DO NOTHING;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "008_add_last_seen_at_to_devices",
        "desc": "Add last_seen_at column to devices table",
        "sql":  """
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
        """,
    },
    {
        "id":   "009_create_threshold_rules_table",
        "desc": "Create threshold_rules table for DB-backed alarm rules",
        "sql":  """
            CREATE TABLE IF NOT EXISTS threshold_rules (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                device_id   UUID REFERENCES devices(id) ON DELETE CASCADE,
                key         VARCHAR(255) NOT NULL,
                condition   VARCHAR(10)  NOT NULL DEFAULT 'gt',
                threshold   FLOAT        NOT NULL,
                severity    VARCHAR(20)  NOT NULL DEFAULT 'WARNING',
                alarm_type  VARCHAR(255) NOT NULL,
                is_active   BOOLEAN      NOT NULL DEFAULT true,
                created_at  TIMESTAMPTZ DEFAULT now(),
                updated_at  TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_threshold_rules_tenant_device
                ON threshold_rules (tenant_id, device_id);
        """,
    },
    {
        "id":   "010_composite_index_telemetry_data",
        "desc": "Add composite index on telemetry_data (device_id, key, ts) for fast aggregates",
        "sql":  """
            CREATE INDEX IF NOT EXISTS ix_telemetry_device_key_ts
                ON telemetry_data (device_id, key, ts DESC);
        """,
    },
]
