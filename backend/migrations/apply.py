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


MIGRATIONS += [
    {
        "id":   "011_add_customer_id_to_users",
        "desc": "Add customer_id to users table for CUSTOMER_USER role scoping",
        "sql":  """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS customer_id UUID REFERENCES customers(id) ON DELETE SET NULL;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "012_create_refresh_tokens_table",
        "desc": "Persist refresh tokens for rotation and revocation support",
        "sql":  """
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token       VARCHAR(512) NOT NULL UNIQUE,
                revoked     BOOLEAN NOT NULL DEFAULT false,
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id
                ON refresh_tokens (user_id);
            CREATE INDEX IF NOT EXISTS ix_refresh_tokens_token
                ON refresh_tokens (token);
        """,
    },
    {
        "id":   "013_create_password_resets_table",
        "desc": "DB-backed password reset tokens (single-use, TTL, survives restart)",
        "sql":  """
            CREATE TABLE IF NOT EXISTS password_resets (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email       VARCHAR(255) NOT NULL,
                token       VARCHAR(512) NOT NULL UNIQUE,
                used        BOOLEAN NOT NULL DEFAULT false,
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_password_resets_email
                ON password_resets (email);
            CREATE INDEX IF NOT EXISTS ix_password_resets_token
                ON password_resets (token);
        """,
    },
    {
        "id":   "014_create_rate_limits_table",
        "desc": "DB-backed rate limiting (replaces in-memory dict, survives restart)",
        "sql":  """
            CREATE TABLE IF NOT EXISTS rate_limits (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                token         VARCHAR(255) NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 1,
                window_start  TIMESTAMPTZ NOT NULL,
                updated_at    TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_rate_limits_token_window
                ON rate_limits (token, window_start);
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "015_partial_unique_index_active_alarms",
        "desc": "Prevent duplicate active alarms at DB level: unique (device_id, alarm_type) when status is ACTIVE",
        "sql":  """
            -- Drop any existing duplicate active alarms before creating the constraint.
            -- Keep the most recent one per (device_id, alarm_type).
            DELETE FROM alarms a
            WHERE a.status IN ('ACTIVE_UNACK', 'ACTIVE_ACK')
              AND a.id NOT IN (
                SELECT DISTINCT ON (device_id, alarm_type) id
                FROM alarms
                WHERE status IN ('ACTIVE_UNACK', 'ACTIVE_ACK')
                ORDER BY device_id, alarm_type, created_at DESC
              );

            -- Partial unique index: only one active alarm per (device_id, alarm_type)
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_alarm_device_type
                ON alarms (device_id, alarm_type)
                WHERE status IN ('ACTIVE_UNACK', 'ACTIVE_ACK');
        """,
    },
]
