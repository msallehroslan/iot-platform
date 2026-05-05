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


MIGRATIONS += [
    {
        "id":   "016_create_rpc_commands_table",
        "desc": "Device RPC command queue for two-way device control",
        "sql":  """
            CREATE TABLE IF NOT EXISTS rpc_commands (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                device_id    UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                method       VARCHAR(255) NOT NULL,
                params       JSONB NOT NULL DEFAULT '{}',
                status       VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                result       JSONB,
                created_by   VARCHAR(255),
                sent_at      TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                created_at   TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_rpc_commands_device_status
                ON rpc_commands (device_id, status);
        """,
    },
    {
        "id":   "017_create_widget_templates_table",
        "desc": "Reusable widget config templates for cross-dashboard reuse",
        "sql":  """
            CREATE TABLE IF NOT EXISTS widget_templates (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                created_by  VARCHAR(255) NOT NULL,
                name        VARCHAR(255) NOT NULL,
                widget_type VARCHAR(50)  NOT NULL,
                config      JSONB NOT NULL DEFAULT '{}',
                is_public   BOOLEAN NOT NULL DEFAULT false,
                created_at  TIMESTAMPTZ DEFAULT now(),
                updated_at  TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_widget_templates_tenant
                ON widget_templates (tenant_id);
        """,
    },
    {
        "id":   "018_create_ingest_metrics_table",
        "desc": "Rolling ingest rate counters for /metrics observability endpoint",
        "sql":  """
            CREATE TABLE IF NOT EXISTS ingest_metrics (
                id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                device_id UUID NOT NULL,
                ts        TIMESTAMPTZ DEFAULT now(),
                key_count INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS ix_ingest_metrics_tenant_ts
                ON ingest_metrics (tenant_id, ts);
        """,
    },
    {
        "id":   "019_telemetry_partitioning_prep",
        "desc": "Prepare telemetry_data for monthly partitioning (TimescaleDB-compatible structure)",
        "sql":  """
            -- Phase 3: Telemetry partitioning preparation.
            --
            -- Full native PostgreSQL partitioning requires recreating the table
            -- which is destructive on live data. Instead we:
            --
            -- 1. Ensure the composite index is optimal for time-range queries
            -- 2. Add a partial index on recent data (last 7 days) — this is what
            --    Postgres query planner uses most for live dashboards
            -- 3. Document the migration path to full partitioning
            --
            -- To upgrade to full partitioning on a maintenance window:
            --   CREATE TABLE telemetry_data_new (LIKE telemetry_data INCLUDING ALL)
            --     PARTITION BY RANGE (ts);
            --   CREATE TABLE telemetry_data_2026_01 PARTITION OF telemetry_data_new
            --     FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
            --   ... create partitions per month ...
            --   INSERT INTO telemetry_data_new SELECT * FROM telemetry_data;
            --   ALTER TABLE telemetry_data RENAME TO telemetry_data_old;
            --   ALTER TABLE telemetry_data_new RENAME TO telemetry_data;
            --
            -- TimescaleDB alternative (zero downtime):
            --   SELECT create_hypertable('telemetry_data', 'ts', migrate_data => true);
            --   This converts the existing table in-place.

            -- Composite index for fast time-range queries per device+key
            -- Note: partial index with NOW() is not allowed in Postgres (non-immutable).
            -- The existing ix_telemetry_data_device_key_ts composite index already covers
            -- this access pattern. We add an ANALYZE to update planner statistics.
            CREATE INDEX IF NOT EXISTS ix_telemetry_device_key_ts
                ON telemetry_data (device_id, key, ts DESC);

            -- Analyse to update planner statistics after index creation
            ANALYZE telemetry_data;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "020_create_tenant_quotas_table",
        "desc": "Per-tenant resource limits (devices, dashboards, ingest rate)",
        "sql":  """
            CREATE TABLE IF NOT EXISTS tenant_quotas (
                id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id          UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
                max_devices        INTEGER,
                max_dashboards     INTEGER,
                max_telemetry_rate INTEGER,
                plan               VARCHAR(50) DEFAULT 'free',
                created_at         TIMESTAMPTZ DEFAULT now(),
                updated_at         TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_tenant_quotas_tenant
                ON tenant_quotas (tenant_id);
        """,
    },
    {
        "id":   "021_create_audit_logs_table",
        "desc": "Immutable audit trail for user actions and system events",
        "sql":  """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   UUID NOT NULL,
                user_id     UUID,
                user_email  VARCHAR(255),
                action      VARCHAR(100) NOT NULL,
                resource    VARCHAR(50),
                resource_id VARCHAR(255),
                detail      JSONB,
                ip_address  VARCHAR(45),
                created_at  TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_audit_logs_tenant_ts
                ON audit_logs (tenant_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_audit_logs_user_action
                ON audit_logs (user_id, action);
        """,
    },
    {
        "id":   "022_create_api_keys_table",
        "desc": "Long-lived API keys for server-to-server authentication",
        "sql":  """
            CREATE TABLE IF NOT EXISTS api_keys (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name         VARCHAR(255) NOT NULL,
                key_hash     VARCHAR(255) NOT NULL UNIQUE,
                key_prefix   VARCHAR(8)   NOT NULL,
                is_active    BOOLEAN NOT NULL DEFAULT true,
                last_used_at TIMESTAMPTZ,
                expires_at   TIMESTAMPTZ,
                created_at   TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_api_keys_tenant
                ON api_keys (tenant_id);
            CREATE INDEX IF NOT EXISTS ix_api_keys_hash
                ON api_keys (key_hash);
        """,
    },
    {
        "id":   "023_purge_ingest_metrics_old_rows",
        "desc": "Clean up ingest_metrics rows older than 24h (they are only needed for 1-min rate window)",
        "sql":  """
            -- Delete rows older than 24 hours. Safe to run on existing data.
            DELETE FROM ingest_metrics WHERE ts < NOW() - INTERVAL '24 hours';

            -- Add TTL-friendly index if not already present
            CREATE INDEX IF NOT EXISTS ix_ingest_metrics_ts
                ON ingest_metrics (ts);
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "024_timescaledb_hypertable",
        "desc": "Convert telemetry_data to TimescaleDB hypertable if extension available; otherwise document staged plan",
        "sql":  """
            -- ================================================================
            -- TELEMETRY STORAGE SCALING — SAFE STAGED APPROACH
            -- ================================================================
            --
            -- WHAT THIS MIGRATION DOES:
            --   1. Attempts TimescaleDB hypertable conversion (Option A)
            --   2. If TimescaleDB is not available → installs a chunk-simulation
            --      index strategy (Option B prep) and documents the manual steps
            --
            -- RENDER POSTGRESQL NOTE:
            --   Render managed Postgres does NOT include TimescaleDB by default.
            --   To use TimescaleDB, create a Render PostgreSQL instance with the
            --   timescaledb extension enabled, or use Timescale Cloud.
            --   This migration will SKIP gracefully if extension is not available.
            --
            -- ================================================================

            DO $$
            DECLARE
                ext_exists BOOLEAN;
                is_hypertable BOOLEAN;
            BEGIN
                -- Check if TimescaleDB extension is available on this server
                SELECT EXISTS (
                    SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'
                ) INTO ext_exists;

                IF ext_exists THEN
                    -- Enable extension (idempotent)
                    EXECUTE 'CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE';

                    -- Check if already a hypertable
                    SELECT EXISTS (
                        SELECT 1 FROM timescaledb_information.hypertables
                        WHERE hypertable_name = 'telemetry_data'
                    ) INTO is_hypertable;

                    IF NOT is_hypertable THEN
                        -- Convert to hypertable with 1-month chunks.
                        -- migrate_data => true: preserves ALL existing rows.
                        -- This operation locks the table briefly but does not drop data.
                        -- Estimated time: ~1min per 10M rows on Render Postgres.
                        PERFORM create_hypertable(
                            'telemetry_data',
                            'ts',
                            chunk_time_interval => INTERVAL '1 month',
                            migrate_data        => true
                        );

                        -- Set compression policy: compress chunks older than 7 days
                        -- Typically achieves 10-20x size reduction on telemetry data.
                        PERFORM add_compression_policy('telemetry_data', INTERVAL '7 days');

                        -- Set retention policy: drop chunks older than retention period
                        -- Replaces the manual purge task in telemetry_service.py
                        PERFORM add_retention_policy('telemetry_data', INTERVAL '90 days');

                        RAISE NOTICE 'TimescaleDB: telemetry_data converted to hypertable with 1-month chunks';
                    ELSE
                        RAISE NOTICE 'TimescaleDB: telemetry_data is already a hypertable, skipping';
                    END IF;

                ELSE
                    -- TimescaleDB not available on this Postgres instance.
                    -- The system continues to work correctly with the existing
                    -- composite index (device_id, key, ts DESC).
                    --
                    -- MANUAL UPGRADE PATHS (run during a maintenance window):
                    --
                    -- OPTION A — Add TimescaleDB:
                    --   1. Provision a new Render Postgres with TimescaleDB enabled
                    --      (or use Timescale Cloud: https://www.timescale.com/cloud)
                    --   2. pg_dump / pg_restore your data
                    --   3. Run this migration again — it will auto-convert
                    --
                    -- OPTION B — Native Postgres monthly partitioning (no extension):
                    --   WARNING: This requires table recreation and is destructive
                    --   without a backup. Steps:
                    --
                    --   STEP 1: Backup
                    --     pg_dump -t telemetry_data $DATABASE_URL > telemetry_backup.sql
                    --
                    --   STEP 2: Create partitioned table
                    --     CREATE TABLE telemetry_data_partitioned (
                    --         LIKE telemetry_data INCLUDING DEFAULTS INCLUDING CONSTRAINTS
                    --     ) PARTITION BY RANGE (ts);
                    --
                    --   STEP 3: Create monthly partitions (repeat per month)
                    --     CREATE TABLE telemetry_data_2026_04
                    --         PARTITION OF telemetry_data_partitioned
                    --         FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
                    --     CREATE TABLE telemetry_data_2026_05
                    --         PARTITION OF telemetry_data_partitioned
                    --         FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
                    --     -- Add a catch-all for future months:
                    --     CREATE TABLE telemetry_data_future
                    --         PARTITION OF telemetry_data_partitioned
                    --         FOR VALUES FROM ('2030-01-01') TO (MAXVALUE);
                    --
                    --   STEP 4: Migrate data (takes time, table is still live)
                    --     INSERT INTO telemetry_data_partitioned SELECT * FROM telemetry_data;
                    --
                    --   STEP 5: Atomic swap (brief lock)
                    --     BEGIN;
                    --       ALTER TABLE telemetry_data RENAME TO telemetry_data_old;
                    --       ALTER TABLE telemetry_data_partitioned RENAME TO telemetry_data;
                    --     COMMIT;
                    --
                    --   STEP 6: Verify and clean up
                    --     SELECT count(*) FROM telemetry_data;    -- should match old count
                    --     DROP TABLE telemetry_data_old;          -- only after verification
                    --
                    --   All existing queries work unchanged after the swap.
                    --   Inserts are auto-routed to the correct partition by ts value.
                    --
                    RAISE NOTICE 'TimescaleDB extension not available. Existing composite index (device_id, key, ts) remains active. See migration comments for upgrade paths.';
                END IF;
            END;
            $$;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "025_add_telemetry_ts_index_and_ingest_cleanup",
        "desc": "Add standalone ts index on telemetry_data for purge queries; add ts index on ingest_metrics",
        "sql":  """
            -- Standalone ts index for purge_old_telemetry() which filters only by ts
            CREATE INDEX IF NOT EXISTS ix_telemetry_data_ts
                ON telemetry_data (ts);

            -- Standalone ts index for ingest_metrics cleanup
            CREATE INDEX IF NOT EXISTS ix_ingest_metrics_ts_only
                ON ingest_metrics (ts);

            -- Autovacuum tuning for high-churn tables
            ALTER TABLE latest_telemetry SET (autovacuum_vacuum_scale_factor = 0.01);
            ALTER TABLE rate_limits      SET (autovacuum_vacuum_scale_factor = 0.01);
            ALTER TABLE devices          SET (autovacuum_vacuum_scale_factor = 0.01);
            ALTER TABLE alarms           SET (autovacuum_vacuum_scale_factor = 0.05);
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "026_deduplicate_user_dashboards",
        "desc": "Remove duplicate Default Dashboards per user, keep the one with widgets or the oldest",
        "sql":  """
            -- Delete duplicate Default Dashboards keeping the one with widgets,
            -- or if all empty, keep the oldest one per user
            DELETE FROM user_dashboards
            WHERE id IN (
                SELECT id FROM (
                    SELECT
                        ud.id,
                        ROW_NUMBER() OVER (
                            PARTITION BY ud.user_id
                            ORDER BY
                                (SELECT COUNT(*) FROM user_widgets uw WHERE uw.dashboard_id = ud.id) DESC,
                                ud.created_at ASC
                        ) AS rn
                    FROM user_dashboards ud
                    WHERE ud.name = 'Default Dashboard'
                ) ranked
                WHERE rn > 1
            );

            -- Set is_default=true on the remaining dashboard for users who have none
            UPDATE user_dashboards ud
            SET is_default = true
            WHERE ud.is_default = false
              AND NOT EXISTS (
                SELECT 1 FROM user_dashboards ud2
                WHERE ud2.user_id = ud.user_id AND ud2.is_default = true
              )
              AND ud.id = (
                SELECT id FROM user_dashboards
                WHERE user_id = ud.user_id
                ORDER BY created_at ASC LIMIT 1
              );
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "027_fix_audit_logs_id_default",
        "desc": "Fix audit_logs id column missing gen_random_uuid() default",
        "sql":  """
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            ALTER TABLE audit_logs ALTER COLUMN id SET DEFAULT gen_random_uuid();
            ALTER TABLE tenant_quotas ALTER COLUMN id SET DEFAULT gen_random_uuid();
            ALTER TABLE api_keys ALTER COLUMN id SET DEFAULT gen_random_uuid();
            ALTER TABLE widget_templates ALTER COLUMN id SET DEFAULT gen_random_uuid();
            ALTER TABLE password_resets ALTER COLUMN id SET DEFAULT gen_random_uuid();
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "028_add_device_location",
        "desc": "Add latitude/longitude columns to devices table for fixed device location",
        "sql":  """
            ALTER TABLE devices ADD COLUMN IF NOT EXISTS latitude  FLOAT;
            ALTER TABLE devices ADD COLUMN IF NOT EXISTS longitude FLOAT;
        """,
    },
]


MIGRATIONS += [
    {
        "id":   "029_threshold_rule_auto_rpc",
        "desc": "Add auto RPC fields to threshold_rules for intelligence actions",
        "sql":  """
            ALTER TABLE threshold_rules ADD COLUMN IF NOT EXISTS auto_rpc_method VARCHAR(100);
            ALTER TABLE threshold_rules ADD COLUMN IF NOT EXISTS auto_rpc_params  JSONB;
            ALTER TABLE threshold_rules ADD COLUMN IF NOT EXISTS auto_rpc_clear   BOOLEAN DEFAULT false;
        """,
    },
]





MIGRATIONS += [
    {
        "id":   "030_anomaly_scores",
        "desc": "Phase 7: anomaly detection scores table",
        "sql":  """
            CREATE TABLE IF NOT EXISTS anomaly_scores (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                key             VARCHAR(255) NOT NULL,
                ts              TIMESTAMPTZ NOT NULL,
                value           FLOAT NOT NULL,
                z_score         FLOAT NOT NULL,
                is_anomaly      BOOLEAN DEFAULT false,
                baseline_mean   FLOAT,
                baseline_stddev FLOAT,
                created_at      TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_anomaly_scores_device_key_ts
                ON anomaly_scores(device_id, key, ts);
        """,
    },
    {
        "id":   "031_device_baselines",
        "desc": "Phase 7: baseline learning table",
        "sql":  """
            CREATE TABLE IF NOT EXISTS device_baselines (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                key             VARCHAR(255) NOT NULL,
                hour_of_day     INTEGER NOT NULL,
                mean            FLOAT NOT NULL,
                stddev          FLOAT NOT NULL DEFAULT 0,
                min_val         FLOAT,
                max_val         FLOAT,
                sample_count    INTEGER DEFAULT 0,
                suggested_upper FLOAT,
                suggested_lower FLOAT,
                updated_at      TIMESTAMPTZ DEFAULT now(),
                CONSTRAINT uq_baseline_device_key_hour UNIQUE (device_id, key, hour_of_day)
            );
            CREATE INDEX IF NOT EXISTS ix_device_baselines_device_key
                ON device_baselines(device_id, key);
        """,
    },
    {
        "id":   "032_device_health_scores",
        "desc": "Phase 7: device health scoring + predictive maintenance table",
        "sql":  """
            CREATE TABLE IF NOT EXISTS device_health_scores (
                id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                device_id             UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                scored_at             TIMESTAMPTZ NOT NULL,
                uptime_score          FLOAT DEFAULT 100,
                alarm_score           FLOAT DEFAULT 100,
                stability_score       FLOAT DEFAULT 100,
                freshness_score       FLOAT DEFAULT 100,
                health_score          FLOAT DEFAULT 100,
                health_label          VARCHAR(20) DEFAULT 'HEALTHY',
                maintenance_due       BOOLEAN DEFAULT false,
                maintenance_reason    TEXT,
                predicted_failure_hrs FLOAT,
                created_at            TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_device_health_device_ts
                ON device_health_scores(device_id, scored_at);
        """,
    },
]

MIGRATIONS += [
    {
        "id":   "033_agent_memory",
        "desc": "TAAT v2: agent memory table for device nicknames, user prefs, incidents",
        "sql":  """
            CREATE TABLE IF NOT EXISTS agent_memory (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
                memory_type  VARCHAR(50) NOT NULL,
                content      TEXT NOT NULL,
                created_at   TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_agent_memory_tenant
                ON agent_memory(tenant_id, memory_type);
        """,
    },
]

MIGRATIONS += [
    {
        "id":   "034_scheduled_rpc",
        "desc": "Scheduled RPC: add SCHEDULED/CANCELLED status values and scheduling columns",
        "sql":  """
            ALTER TABLE rpc_commands
                ADD COLUMN IF NOT EXISTS scheduled_for        TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS repeat_interval_hours FLOAT;

            CREATE INDEX IF NOT EXISTS ix_rpc_commands_scheduled
                ON rpc_commands(scheduled_for)
                WHERE status = 'SCHEDULED';
        """,
    },

    if __name__ == "__main__":
    run()
]
