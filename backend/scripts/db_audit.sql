-- =============================================================================
-- TriAxis IoT Platform — Full Database Audit Script
-- Run in Render Shell:  psql $DATABASE_URL -f db_audit.sql
-- Or paste into Render's PSQL console section by section.
-- =============================================================================

\set ON_ERROR_STOP off
\pset format aligned
\pset border 2

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — MIGRATION STATUS
-- All 24 migrations must be present. Missing = tables/indexes not applied.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 1 — MIGRATION STATUS'
\echo '══════════════════════════════════════════════════════════'

SELECT
    id                                          AS migration,
    TO_CHAR(applied_at, 'YYYY-MM-DD HH24:MI')   AS applied_at
FROM _migrations
ORDER BY applied_at;

-- Expected count: 24
SELECT
    COUNT(*)                                    AS migrations_applied,
    CASE WHEN COUNT(*) = 24 THEN '✓ OK' ELSE '✗ MISSING — run: python migrations/apply.py' END AS status
FROM _migrations;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — TABLE EXISTENCE CHECK
-- Every ORM model must have a real table.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 2 — TABLE EXISTENCE'
\echo '══════════════════════════════════════════════════════════'

WITH expected(tbl) AS (
    VALUES
        ('tenants'),('users'),('customers'),('devices'),
        ('telemetry_data'),('latest_telemetry'),('telemetry_keys'),
        ('alarms'),('threshold_rules'),
        ('dashboards'),('widgets'),
        ('user_dashboards'),('user_widgets'),
        ('refresh_tokens'),('password_resets'),('rate_limits'),
        ('rpc_commands'),('widget_templates'),('ingest_metrics'),
        ('tenant_quotas'),('audit_logs'),('api_keys'),
        ('_migrations')
)
SELECT
    e.tbl                                               AS table_name,
    CASE WHEN t.tablename IS NOT NULL THEN '✓ exists' ELSE '✗ MISSING' END AS status,
    COALESCE(s.n_live_tup::text, '—')                  AS live_rows
FROM expected e
LEFT JOIN pg_tables t
    ON t.tablename = e.tbl AND t.schemaname = 'public'
LEFT JOIN pg_stat_user_tables s
    ON s.relname = e.tbl
ORDER BY e.tbl;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — CRITICAL INDEX CHECK
-- Missing indexes = slow queries under load.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 3 — CRITICAL INDEXES'
\echo '══════════════════════════════════════════════════════════'

WITH expected_indexes(idx) AS (
    VALUES
        ('uq_latest_telemetry_device_key'),
        ('uq_telemetry_keys_device_key'),
        ('ix_telemetry_device_key_ts'),
        ('ix_threshold_rules_tenant_device'),
        ('ix_rpc_commands_device_status'),
        ('ix_ingest_metrics_tenant_ts'),
        ('ix_audit_logs_tenant_ts'),
        ('ix_audit_logs_user_action'),
        ('ix_rate_limits_token_window')
)
SELECT
    e.idx                                                           AS index_name,
    CASE WHEN i.indexname IS NOT NULL THEN '✓ exists' ELSE '✗ MISSING' END AS status,
    i.indexdef
FROM expected_indexes e
LEFT JOIN pg_indexes i
    ON i.indexname = e.idx AND i.schemaname = 'public'
ORDER BY e.idx;

-- Partial unique index for active alarms (migration 015)
SELECT
    indexname,
    indexdef,
    CASE WHEN indexname IS NOT NULL THEN '✓ exists' ELSE '✗ MISSING' END AS status
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'alarms'
  AND indexdef ILIKE '%where%'
ORDER BY indexname;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — TENANT HEALTH
-- Orphan check + provisioning key integrity.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 4 — TENANT HEALTH'
\echo '══════════════════════════════════════════════════════════'

-- Tenant summary
SELECT
    t.id,
    t.name                                          AS tenant,
    COUNT(DISTINCT u.id)                            AS users,
    COUNT(DISTINCT d.id)                            AS devices,
    COUNT(DISTINCT c.id)                            AS customers,
    COALESCE(q.plan, 'free')                        AS plan,
    CASE WHEN t.provisioning_key IS NOT NULL
         THEN '✓' ELSE '✗ NULL' END                AS has_prov_key,
    TO_CHAR(t.created_at, 'YYYY-MM-DD')             AS created
FROM tenants t
LEFT JOIN users       u ON u.tenant_id = t.id
LEFT JOIN devices     d ON d.tenant_id = t.id
LEFT JOIN customers   c ON c.tenant_id = t.id
LEFT JOIN tenant_quotas q ON q.tenant_id = t.id
GROUP BY t.id, t.name, t.provisioning_key, q.plan, t.created_at
ORDER BY t.created_at DESC;

-- Tenants missing quota row (will use global defaults — not a blocker but worth knowing)
SELECT
    t.name AS tenant,
    '⚠ no quota row — using global defaults' AS note
FROM tenants t
LEFT JOIN tenant_quotas q ON q.tenant_id = t.id
WHERE q.id IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 5 — USER INTEGRITY
-- Role validity, CUSTOMER_USER scoping, orphaned users.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 5 — USER INTEGRITY'
\echo '══════════════════════════════════════════════════════════'

-- Role distribution
SELECT
    role,
    COUNT(*)        AS count,
    COUNT(*) FILTER (WHERE is_active) AS active,
    COUNT(*) FILTER (WHERE NOT is_active) AS inactive
FROM users
GROUP BY role
ORDER BY role;

-- Invalid roles (anything outside the 3 valid values)
SELECT id, email, role, '✗ INVALID ROLE' AS issue
FROM users
WHERE role NOT IN ('TENANT_ADMIN','TENANT_USER','CUSTOMER_USER');

-- CUSTOMER_USER with no customer_id (broken scoping — they'll see nothing)
SELECT id, email, role, '✗ CUSTOMER_USER missing customer_id' AS issue
FROM users
WHERE role = 'CUSTOMER_USER' AND customer_id IS NULL;

-- CUSTOMER_USER whose customer_id points to a different tenant
SELECT u.id, u.email, u.tenant_id AS user_tenant, c.tenant_id AS cust_tenant,
       '✗ customer_id cross-tenant mismatch' AS issue
FROM users u
JOIN customers c ON c.id = u.customer_id
WHERE u.role = 'CUSTOMER_USER'
  AND u.tenant_id != c.tenant_id;

-- Users with no tenant (created before tenant assignment — migration 003 should have fixed)
SELECT id, email, role, '✗ NULL tenant_id' AS issue
FROM users
WHERE tenant_id IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 6 — DEVICE INTEGRITY
-- Orphans, missing tokens, cross-tenant contamination.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 6 — DEVICE INTEGRITY'
\echo '══════════════════════════════════════════════════════════'

-- Device status summary per tenant
SELECT
    t.name          AS tenant,
    COUNT(*)        AS total_devices,
    COUNT(*) FILTER (WHERE d.status = 'ACTIVE')   AS active,
    COUNT(*) FILTER (WHERE d.status = 'INACTIVE') AS inactive,
    COUNT(*) FILTER (WHERE d.status = 'DISABLED') AS disabled,
    COUNT(*) FILTER (WHERE d.last_seen_at > NOW() - INTERVAL '5 minutes') AS online_now,
    COUNT(*) FILTER (WHERE d.last_seen_at IS NULL) AS never_seen
FROM devices d
JOIN tenants t ON t.id = d.tenant_id
GROUP BY t.id, t.name
ORDER BY t.name;

-- Devices with NULL tenant (orphans)
SELECT id, name, token, '✗ NULL tenant_id' AS issue
FROM devices
WHERE tenant_id IS NULL;

-- Devices whose customer_id belongs to a different tenant
SELECT d.id, d.name, d.tenant_id AS dev_tenant, c.tenant_id AS cust_tenant,
       '✗ customer_id cross-tenant' AS issue
FROM devices d
JOIN customers c ON c.id = d.customer_id
WHERE d.tenant_id != c.tenant_id;

-- Duplicate device tokens (should be impossible with unique constraint, but check)
SELECT token, COUNT(*) AS cnt, '✗ DUPLICATE TOKEN' AS issue
FROM devices
GROUP BY token
HAVING COUNT(*) > 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 7 — TELEMETRY HEALTH
-- latest_telemetry sync, key registry, stale data.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 7 — TELEMETRY HEALTH'
\echo '══════════════════════════════════════════════════════════'

-- Row counts
SELECT
    (SELECT COUNT(*) FROM telemetry_data)       AS raw_rows,
    (SELECT COUNT(*) FROM latest_telemetry)     AS latest_rows,
    (SELECT COUNT(*) FROM telemetry_keys)       AS key_registry_rows,
    (SELECT COUNT(DISTINCT device_id) FROM telemetry_data) AS devices_with_data;

-- Devices that have latest_telemetry but NO matching telemetry_data
-- (should never happen — indicates a data integrity issue)
SELECT lt.device_id, lt.key, '✗ latest but no raw history' AS issue
FROM latest_telemetry lt
LEFT JOIN telemetry_data td
    ON td.device_id = lt.device_id AND td.key = lt.key
WHERE td.id IS NULL
LIMIT 20;

-- latest_telemetry keys not registered in telemetry_keys
-- (backfill migration 007 should have fixed; new keys added post-migration must auto-register)
SELECT lt.device_id, lt.key, '⚠ not in telemetry_keys registry' AS issue
FROM latest_telemetry lt
LEFT JOIN telemetry_keys tk
    ON tk.device_id = lt.device_id AND tk.key = lt.key
WHERE tk.id IS NULL
LIMIT 20;

-- Oldest and newest telemetry per tenant (data retention window check)
SELECT
    t.name      AS tenant,
    MIN(td.ts)  AS oldest_record,
    MAX(td.ts)  AS newest_record,
    NOW() - MAX(td.ts) AS since_last_ingest,
    COUNT(*)    AS total_rows
FROM telemetry_data td
JOIN devices d ON d.id = td.device_id
JOIN tenants t ON t.id = d.tenant_id
GROUP BY t.id, t.name
ORDER BY t.name;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 8 — ALARM ENGINE HEALTH
-- Active alarm count, orphan alarms, lifecycle violations.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 8 — ALARM ENGINE HEALTH'
\echo '══════════════════════════════════════════════════════════'

-- Alarm summary by tenant + status
SELECT
    t.name          AS tenant,
    a.status,
    a.severity,
    COUNT(*)        AS count
FROM alarms a
JOIN devices d ON d.id = a.device_id
JOIN tenants t ON t.id = d.tenant_id
GROUP BY t.name, a.status, a.severity
ORDER BY t.name, a.status, a.severity;

-- Alarms with invalid status values
SELECT id, status, '✗ INVALID STATUS' AS issue
FROM alarms
WHERE status NOT IN ('ACTIVE_UNACK','ACTIVE_ACK','CLEARED_UNACK','CLEARED_ACK');

-- Cleared alarms missing end_ts (lifecycle bug)
SELECT id, status, clear_ts, '✗ cleared but end_ts is NULL' AS issue
FROM alarms
WHERE status IN ('CLEARED_UNACK','CLEARED_ACK')
  AND end_ts IS NULL
LIMIT 20;

-- ACK'd alarms missing ack_ts
SELECT id, status, ack_ts, '✗ acked but ack_ts is NULL' AS issue
FROM alarms
WHERE status IN ('ACTIVE_ACK','CLEARED_ACK')
  AND ack_ts IS NULL
LIMIT 20;

-- Alarms whose device no longer exists (orphans — FK cascade should prevent this)
SELECT a.id, a.device_id, '✗ orphan — device deleted' AS issue
FROM alarms a
LEFT JOIN devices d ON d.id = a.device_id
WHERE d.id IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 9 — WIDGET INTEGRITY
-- Valid widget types, non-null configs, orphan widgets.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 9 — WIDGET INTEGRITY'
\echo '══════════════════════════════════════════════════════════'

-- Widget type distribution (device dashboards)
SELECT
    widget_type,
    COUNT(*)        AS count,
    COUNT(*) FILTER (WHERE config::text = '{}' OR config IS NULL) AS empty_config
FROM widgets
GROUP BY widget_type
ORDER BY count DESC;

-- Unknown widget types (not in the 18 valid types)
SELECT id, widget_type, dashboard_id, '✗ UNKNOWN WIDGET TYPE' AS issue
FROM widgets
WHERE widget_type NOT IN (
    'value_card','line_chart','gauge','status_light',
    'bar_chart','alarm_list','timeseries_table','pie_chart',
    'markdown','entity_table','html_card',
    'multi_axis_chart','map','device_summary',
    'rpc_button','rpc_toggle','rpc_input'
);

-- Same check for user_widgets
SELECT id, widget_type, dashboard_id, '✗ UNKNOWN WIDGET TYPE (user_widgets)' AS issue
FROM user_widgets
WHERE widget_type NOT IN (
    'value_card','line_chart','gauge','status_light',
    'bar_chart','alarm_list','timeseries_table','pie_chart',
    'markdown','entity_table','html_card',
    'multi_axis_chart','map','device_summary',
    'rpc_button','rpc_toggle','rpc_input'
);

-- Widgets with NULL or malformed position JSON
SELECT id, widget_type, position, '✗ missing position fields' AS issue
FROM widgets
WHERE (position->>'x') IS NULL
   OR (position->>'y') IS NULL
   OR (position->>'w') IS NULL
   OR (position->>'h') IS NULL;

SELECT id, widget_type, position, '✗ missing position fields (user_widgets)' AS issue
FROM user_widgets
WHERE (position->>'x') IS NULL
   OR (position->>'y') IS NULL
   OR (position->>'w') IS NULL
   OR (position->>'h') IS NULL;

-- Dashboards with no widgets (empty dashboards — not a bug, just informational)
SELECT
    d.id, d.name, d.device_id,
    '⚠ dashboard has no widgets' AS note
FROM dashboards d
LEFT JOIN widgets w ON w.dashboard_id = d.id
WHERE w.id IS NULL;

SELECT
    ud.id, ud.name, ud.user_id,
    '⚠ user_dashboard has no widgets' AS note
FROM user_dashboards ud
LEFT JOIN user_widgets uw ON uw.dashboard_id = ud.id
WHERE uw.id IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 10 — SECURITY TABLE HEALTH
-- Refresh tokens, rate limits, API keys, password resets.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 10 — SECURITY HEALTH'
\echo '══════════════════════════════════════════════════════════'

-- Refresh token stats
SELECT
    COUNT(*)                                                    AS total_tokens,
    COUNT(*) FILTER (WHERE revoked = false AND expires_at > NOW()) AS active_valid,
    COUNT(*) FILTER (WHERE revoked = true)                      AS revoked,
    COUNT(*) FILTER (WHERE expires_at < NOW() AND revoked = false) AS expired_not_revoked,
    MIN(created_at)                                             AS oldest_token
FROM refresh_tokens;

-- Expired but not revoked tokens (should be cleaned up)
SELECT COUNT(*) AS stale_tokens,
    CASE WHEN COUNT(*) > 0 THEN '⚠ run token cleanup' ELSE '✓ OK' END AS status
FROM refresh_tokens
WHERE expires_at < NOW() AND revoked = false;

-- Rate limit buckets (stale windows > 2 minutes old)
SELECT
    COUNT(*)    AS total_buckets,
    COUNT(*) FILTER (WHERE window_start < NOW() - INTERVAL '2 minutes') AS stale_buckets,
    CASE WHEN COUNT(*) FILTER (WHERE window_start < NOW() - INTERVAL '2 minutes') > 1000
         THEN '⚠ needs cleanup' ELSE '✓ OK' END AS status
FROM rate_limits;

-- API key stats
SELECT
    COUNT(*)                                                    AS total_keys,
    COUNT(*) FILTER (WHERE is_active = true)                    AS active,
    COUNT(*) FILTER (WHERE is_active = false)                   AS revoked,
    COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at < NOW() AND is_active = true)
                                                                AS expired_still_active,
    MAX(last_used_at)                                           AS last_used
FROM api_keys;

-- API keys that are expired but still marked active (should auto-revoke on use)
SELECT id, name, key_prefix, expires_at, '⚠ expired but is_active=true' AS issue
FROM api_keys
WHERE is_active = true
  AND expires_at IS NOT NULL
  AND expires_at < NOW();

-- Password reset tokens still marked unused but expired
SELECT COUNT(*) AS stale_resets,
    CASE WHEN COUNT(*) > 0 THEN '⚠ stale reset tokens' ELSE '✓ OK' END AS status
FROM password_resets
WHERE used = false AND expires_at < NOW();

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 11 — RPC COMMAND HEALTH
-- Stuck commands, timeout candidates.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 11 — RPC COMMAND HEALTH'
\echo '══════════════════════════════════════════════════════════'

-- RPC summary by status
SELECT
    status,
    COUNT(*)                AS count,
    MIN(created_at)         AS oldest,
    MAX(created_at)         AS newest
FROM rpc_commands
GROUP BY status
ORDER BY status;

-- Stuck PENDING commands older than 10 minutes (device likely offline)
SELECT
    rc.id, d.name AS device, rc.method, rc.created_at,
    NOW() - rc.created_at AS age,
    '⚠ PENDING > 10 min — device may be offline' AS note
FROM rpc_commands rc
JOIN devices d ON d.id = rc.device_id
WHERE rc.status = 'PENDING'
  AND rc.created_at < NOW() - INTERVAL '10 minutes'
ORDER BY rc.created_at;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 12 — AUDIT LOG COVERAGE
-- Verify all key action types are present, no giant gaps.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 12 — AUDIT LOG COVERAGE'
\echo '══════════════════════════════════════════════════════════'

-- Action type distribution
SELECT
    action,
    COUNT(*)                AS total,
    MAX(created_at)         AS last_seen,
    COUNT(DISTINCT tenant_id) AS tenants_using
FROM audit_logs
GROUP BY action
ORDER BY total DESC;

-- Expected action types — flag any that have NEVER appeared
WITH expected_actions(action) AS (
    VALUES
        ('user.register'),('user.invite'),('user.role_change'),('user.delete'),
        ('device.create'),('device.update'),('device.delete'),('device.token_regenerate'),
        ('alarm.ack'),('alarm.clear'),('alarm.delete'),
        ('customer.create'),('customer.delete'),('customer_user.create'),
        ('rule.create'),('rule.update'),('rule.delete'),
        ('rpc.send'),
        ('api_key.create'),('api_key.revoke'),
        ('dashboard.create'),('dashboard.delete'),
        ('widget.add'),('widget.delete'),
        ('widget_template.create'),('widget_template.delete'),
        ('user_dashboard.create'),('user_dashboard.delete')
)
SELECT
    e.action,
    COALESCE(a.cnt::text, '—')  AS times_logged,
    CASE WHEN a.cnt IS NULL THEN '⚠ never logged (may be unused feature)'
         ELSE '✓' END           AS status
FROM expected_actions e
LEFT JOIN (
    SELECT action, COUNT(*) AS cnt FROM audit_logs GROUP BY action
) a ON a.action = e.action
ORDER BY e.action;

-- Recent audit activity (last 50 entries)
SELECT
    TO_CHAR(created_at, 'MM-DD HH24:MI:SS')    AS time,
    action,
    resource,
    LEFT(resource_id, 8)                        AS res_id,
    user_email
FROM audit_logs
ORDER BY created_at DESC
LIMIT 50;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 13 — INGEST METRICS & QUOTA HEALTH
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 13 — INGEST METRICS & QUOTAS'
\echo '══════════════════════════════════════════════════════════'

-- Ingest rate last 5 minutes per tenant
SELECT
    t.name          AS tenant,
    COUNT(im.id)    AS buckets,
    SUM(im.key_count) AS total_events,
    ROUND(SUM(im.key_count) / GREATEST(EXTRACT(EPOCH FROM (NOW() - MIN(im.ts))) / 60, 1), 1) AS avg_events_per_min
FROM ingest_metrics im
JOIN tenants t ON t.id = im.tenant_id
WHERE im.ts > NOW() - INTERVAL '5 minutes'
GROUP BY t.id, t.name
ORDER BY avg_events_per_min DESC;

-- Stale ingest_metrics rows (older than 10 minutes — cleanup task should purge these)
SELECT
    COUNT(*)    AS stale_rows,
    MIN(ts)     AS oldest,
    CASE WHEN COUNT(*) > 10000 THEN '⚠ cleanup task may not be running'
         ELSE '✓ OK' END AS status
FROM ingest_metrics
WHERE ts < NOW() - INTERVAL '10 minutes';

-- Tenant quota vs actual usage
SELECT
    t.name                                              AS tenant,
    COUNT(DISTINCT d.id)                                AS devices_used,
    COALESCE(q.max_devices, 100)                        AS device_limit,
    ROUND(COUNT(DISTINCT d.id)::numeric / NULLIF(COALESCE(q.max_devices, 100), 0) * 100, 1) AS device_pct,
    COALESCE(q.max_telemetry_rate, 1000)                AS rate_limit,
    COALESCE(q.plan, 'free')                            AS plan
FROM tenants t
LEFT JOIN devices d ON d.tenant_id = t.id
LEFT JOIN tenant_quotas q ON q.tenant_id = t.id
GROUP BY t.id, t.name, q.max_devices, q.max_telemetry_rate, q.plan
ORDER BY device_pct DESC NULLS LAST;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 14 — THRESHOLD RULES SANITY
-- Rules pointing to deleted devices, duplicate conflicts.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 14 — THRESHOLD RULES'
\echo '══════════════════════════════════════════════════════════'

-- Rule summary
SELECT
    t.name          AS tenant,
    COUNT(*)        AS total_rules,
    COUNT(*) FILTER (WHERE tr.is_active)        AS active,
    COUNT(*) FILTER (WHERE tr.device_id IS NULL) AS tenant_wide,
    COUNT(*) FILTER (WHERE tr.device_id IS NOT NULL) AS device_specific
FROM threshold_rules tr
JOIN tenants t ON t.id = tr.tenant_id
GROUP BY t.id, t.name
ORDER BY t.name;

-- Rules with invalid condition operators
SELECT id, key, condition, threshold, '✗ INVALID CONDITION' AS issue
FROM threshold_rules
WHERE condition NOT IN ('gt','lt','gte','lte','eq');

-- Device-specific rules whose device was deleted (FK cascade should prevent, but verify)
SELECT tr.id, tr.key, tr.device_id, '✗ orphan — device not found' AS issue
FROM threshold_rules tr
LEFT JOIN devices d ON d.id = tr.device_id
WHERE tr.device_id IS NOT NULL AND d.id IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 15 — DATABASE SIZE & PERFORMANCE
-- Table sizes, index hit rates, bloat indicators.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 15 — DATABASE SIZE & PERFORMANCE'
\echo '══════════════════════════════════════════════════════════'

-- Table sizes (largest first)
SELECT
    relname                         AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_size_pretty(pg_relation_size(relid))        AS table_size,
    pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
    n_live_tup                      AS live_rows,
    n_dead_tup                      AS dead_rows,
    CASE WHEN n_live_tup > 0
         THEN ROUND(n_dead_tup::numeric / n_live_tup * 100, 1)
         ELSE 0 END                 AS dead_pct
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- Index hit rate (should be >99% for healthy cache usage)
SELECT
    schemaname,
    relname                         AS table_name,
    indexrelname                    AS index_name,
    idx_scan                        AS times_used,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND schemaname = 'public'
ORDER BY relname;
-- ^ These are indexes that have never been used — candidates for removal

-- Sequential scans on large tables (indicates missing index)
SELECT
    relname                         AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    CASE WHEN seq_scan > 0 AND n_live_tup > 1000
         THEN '⚠ high seq_scan on large table'
         ELSE '✓ OK' END AS status
FROM pg_stat_user_tables
WHERE n_live_tup > 1000
ORDER BY seq_scan DESC
LIMIT 15;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 16 — FINAL SUMMARY SCORECARD
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '══════════════════════════════════════════════════════════'
\echo ' SECTION 16 — SUMMARY SCORECARD'
\echo '══════════════════════════════════════════════════════════'

SELECT * FROM (
    SELECT 1 AS ord, 'Migrations applied'        AS check,
        CASE WHEN (SELECT COUNT(*) FROM _migrations) = 24
             THEN '✓ 24/24' ELSE '✗ ' || (SELECT COUNT(*) FROM _migrations)::text || '/24' END AS result
    UNION ALL
    SELECT 2, 'Orphan devices (no tenant)',
        CASE WHEN (SELECT COUNT(*) FROM devices WHERE tenant_id IS NULL) = 0
             THEN '✓ 0' ELSE '✗ ' || (SELECT COUNT(*) FROM devices WHERE tenant_id IS NULL)::text END
    UNION ALL
    SELECT 3, 'Invalid user roles',
        CASE WHEN (SELECT COUNT(*) FROM users WHERE role NOT IN ('TENANT_ADMIN','TENANT_USER','CUSTOMER_USER')) = 0
             THEN '✓ 0' ELSE '✗ ' || (SELECT COUNT(*) FROM users WHERE role NOT IN ('TENANT_ADMIN','TENANT_USER','CUSTOMER_USER'))::text END
    UNION ALL
    SELECT 4, 'CUSTOMER_USER missing customer_id',
        CASE WHEN (SELECT COUNT(*) FROM users WHERE role='CUSTOMER_USER' AND customer_id IS NULL) = 0
             THEN '✓ 0' ELSE '✗ ' || (SELECT COUNT(*) FROM users WHERE role='CUSTOMER_USER' AND customer_id IS NULL)::text END
    UNION ALL
    SELECT 5, 'Duplicate device tokens',
        CASE WHEN (SELECT COUNT(*) FROM (SELECT token FROM devices GROUP BY token HAVING COUNT(*) > 1) x) = 0
             THEN '✓ 0' ELSE '✗ FOUND' END
    UNION ALL
    SELECT 6, 'Alarm lifecycle violations (clear/no end_ts)',
        CASE WHEN (SELECT COUNT(*) FROM alarms WHERE status IN ('CLEARED_UNACK','CLEARED_ACK') AND end_ts IS NULL) = 0
             THEN '✓ 0' ELSE '⚠ ' || (SELECT COUNT(*) FROM alarms WHERE status IN ('CLEARED_UNACK','CLEARED_ACK') AND end_ts IS NULL)::text END
    UNION ALL
    SELECT 7, 'Unknown widget types',
        CASE WHEN (SELECT COUNT(*) FROM widgets WHERE widget_type NOT IN (
            'value_card','line_chart','gauge','status_light','bar_chart','alarm_list',
            'timeseries_table','pie_chart','markdown','entity_table','html_card',
            'multi_axis_chart','map','device_summary','rpc_button','rpc_toggle','rpc_input')) = 0
             THEN '✓ 0' ELSE '✗ FOUND' END
    UNION ALL
    SELECT 8, 'Expired API keys still active',
        CASE WHEN (SELECT COUNT(*) FROM api_keys WHERE is_active=true AND expires_at < NOW()) = 0
             THEN '✓ 0' ELSE '⚠ ' || (SELECT COUNT(*) FROM api_keys WHERE is_active=true AND expires_at < NOW())::text END
    UNION ALL
    SELECT 9, 'Stuck RPC commands (>10 min)',
        CASE WHEN (SELECT COUNT(*) FROM rpc_commands WHERE status='PENDING' AND created_at < NOW() - INTERVAL '10 min') = 0
             THEN '✓ 0' ELSE '⚠ ' || (SELECT COUNT(*) FROM rpc_commands WHERE status='PENDING' AND created_at < NOW() - INTERVAL '10 min')::text END
    UNION ALL
    SELECT 10, 'Stale rate_limit rows (>2 min)',
        CASE WHEN (SELECT COUNT(*) FROM rate_limits WHERE window_start < NOW() - INTERVAL '2 min') < 1000
             THEN '✓ OK' ELSE '⚠ ' || (SELECT COUNT(*) FROM rate_limits WHERE window_start < NOW() - INTERVAL '2 min')::text || ' stale rows' END
    UNION ALL
    SELECT 11, 'Audit log — total entries',
        (SELECT COUNT(*)::text FROM audit_logs)
    UNION ALL
    SELECT 12, 'Total tenants / devices / users',
        (SELECT COUNT(*)::text FROM tenants) || ' tenants / ' ||
        (SELECT COUNT(*)::text FROM devices) || ' devices / ' ||
        (SELECT COUNT(*)::text FROM users)   || ' users'
) scorecard
ORDER BY ord;

\echo ''
\echo 'Audit complete.'
