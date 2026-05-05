"""
db_audit.py — Database compliance audit.
Run: python scripts/db_audit.py
"""
import os, sys, psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/iotdb")
conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor(cursor_factory=RealDictCursor)

results = {"pass": 0, "fail": 0, "warn": 0}

def ok(msg):   print(f"  ✓ {msg}"); results["pass"] += 1
def fail(msg): print(f"  ✗ {msg}"); results["fail"] += 1
def warn(msg): print(f"  ⚠ {msg}"); results["warn"] += 1
def section(t): print(f"\n{'='*55}\n  {t}\n{'='*55}")

def check_table(t):
    cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s)",(t,))
    return cur.fetchone()["exists"]

def check_column(t, c):
    cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s)",(t,c))
    return cur.fetchone()["exists"]

def check_index(i):
    cur.execute("SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname=%s)",(i,))
    return cur.fetchone()["exists"]

def get_count(t, w=""):
    try:
        cur.execute(f"SELECT COUNT(*) as n FROM {t} {w}")
        return cur.fetchone()["n"]
    except: return -1

section("1. REQUIRED TABLES")
for t in ["tenants","users","customers","devices","telemetry_data","latest_telemetry",
          "telemetry_keys","alarms","threshold_rules","dashboards","widgets",
          "user_dashboards","user_widgets","refresh_tokens","password_resets",
          "rate_limits","rpc_commands","widget_templates","ingest_metrics",
          "tenant_quotas","audit_logs","api_keys","_migrations"]:
    (ok if check_table(t) else fail)(t)

section("2. CRITICAL COLUMNS")
COLS = {
    "tenants":          ["id","name","provisioning_key"],
    "users":            ["id","tenant_id","email","hashed_password","role","is_active","customer_id"],
    "devices":          ["id","tenant_id","customer_id","name","token","status","last_seen_at"],
    "telemetry_data":   ["id","device_id","key","value_num","value_str","value_bool","value_json","ts"],
    "latest_telemetry": ["id","device_id","key","value_num","value_str","value_bool","value_json","ts"],
    "alarms":           ["id","device_id","alarm_type","severity","status","details","ack_ts","clear_ts","cleared_by"],
    "threshold_rules":  ["id","tenant_id","device_id","key","condition","threshold","severity","alarm_type","is_active"],
    "dashboards":       ["id","device_id","name","is_default"],
    "widgets":          ["id","dashboard_id","widget_type","title","config","position"],
    "user_dashboards":  ["id","user_id","name","is_default"],
    "user_widgets":     ["id","dashboard_id","widget_type","title","config","position"],
    "refresh_tokens":   ["id","user_id","token","revoked","expires_at"],
    "password_resets":  ["id","email","token","used","expires_at"],
    "rate_limits":      ["token","request_count","window_start"],
    "rpc_commands":     ["id","device_id","method","params","status","sent_at","completed_at"],
    "widget_templates": ["id","tenant_id","name","widget_type","config"],
    "ingest_metrics":   ["id","tenant_id","device_id","key_count","ts"],
    "tenant_quotas":    ["id","tenant_id","max_devices","max_dashboards","max_telemetry_rate","plan"],
    "audit_logs":       ["id","tenant_id","user_id","action","resource","resource_id","detail","created_at"],
    "api_keys":         ["id","tenant_id","user_id","name","key_hash","key_prefix","is_active","expires_at"],
}
for table, cols in COLS.items():
    if not check_table(table): fail(f"{table}: table missing"); continue
    missing = [c for c in cols if not check_column(table,c)]
    (fail if missing else ok)(f"{table}: " + (f"MISSING {missing}" if missing else f"all {len(cols)} columns OK"))

section("3. CRITICAL INDEXES")
for idx, desc in {
    "uq_latest_telemetry_device_key": "latest_telemetry uniqueness",
    "ix_telemetry_device_key_ts":     "telemetry hot query path",
    "uniq_active_alarm_device_type":  "duplicate alarm prevention",
}.items():
    (ok if check_index(idx) else fail)(f"{idx} — {desc}")

section("4. MIGRATIONS APPLIED")
EXPECTED = [f"{str(i).zfill(3)}" for i in range(1,25)]
if check_table("_migrations"):
    cur.execute("SELECT id FROM _migrations")
    applied = {r["id"] for r in cur.fetchall()}
    unapplied = [m for m in applied if True]  # just count applied
    ok(f"{len(applied)} migrations applied")
    # Check for any expected prefix not applied
    for prefix in EXPECTED:
        matches = [a for a in applied if a.startswith(prefix+"_")]
        if not matches:
            fail(f"Migration {prefix}_* not found in _migrations")
else:
    fail("_migrations table missing — run python migrations/apply.py")

section("5. DATA SANITY")
if check_table("users") and check_table("tenants"):
    n = get_count("users u","LEFT JOIN tenants t ON u.tenant_id=t.id WHERE t.id IS NULL")
    (ok if n==0 else fail)(f"Orphaned users: {n}")

if check_table("devices"):
    n = get_count("devices d","LEFT JOIN tenants t ON d.tenant_id=t.id WHERE t.id IS NULL")
    (ok if n==0 else fail)(f"Orphaned devices: {n}")

if check_table("users"):
    n = get_count("users","WHERE role='CUSTOMER_USER' AND customer_id IS NULL")
    (ok if n==0 else warn)(f"CUSTOMER_USER with no customer_id: {n}" + (" — they see no devices" if n>0 else ""))

if check_table("alarms"):
    cur.execute("SELECT COUNT(*) as n FROM (SELECT device_id,alarm_type,COUNT(*) FROM alarms WHERE status IN('ACTIVE_UNACK','ACTIVE_ACK') GROUP BY device_id,alarm_type HAVING COUNT(*)>1) d")
    n = cur.fetchone()["n"]
    (ok if n==0 else fail)(f"Duplicate active alarms: {n}")

if check_table("widgets"):
    VALID = ('value_card','line_chart','gauge','status_light','bar_chart','alarm_list',
             'timeseries_table','pie_chart','markdown','entity_table','html_card',
             'multi_axis_chart','map','device_summary','rpc_button','rpc_toggle','rpc_input')
    ph = ','.join(['%s']*len(VALID))
    cur.execute(f"SELECT COUNT(*) as n FROM widgets WHERE widget_type NOT IN ({ph})",VALID)
    n = cur.fetchone()["n"]
    (ok if n==0 else warn)(f"Widgets with unknown type: {n}")

if check_table("password_resets"):
    n = get_count("password_resets","WHERE used=false AND expires_at<NOW()")
    (ok if n==0 else warn)(f"Expired unused password resets: {n} (harmless)")

if check_table("refresh_tokens"):
    n = get_count("refresh_tokens","WHERE revoked=true AND expires_at<NOW()-INTERVAL'30 days'")
    (ok if n==0 else warn)(f"Old revoked refresh tokens: {n} (safe to purge)")

section("6. ROW COUNTS")
for t in ["tenants","users","devices","telemetry_data","latest_telemetry","alarms",
          "threshold_rules","dashboards","widgets","user_dashboards","user_widgets",
          "rpc_commands","audit_logs","ingest_metrics"]:
    if check_table(t): print(f"  {t:30s} {get_count(t):>8} rows")

section("SUMMARY")
total = sum(results.values())
print(f"\n  Checks: {total}  ✓ {results['pass']}  ⚠ {results['warn']}  ✗ {results['fail']}")
if results["fail"]==0 and results["warn"]==0:
    print("  ✅ Database fully compliant with frontend.")
elif results["fail"]==0:
    print("  ✅ No critical issues. Review warnings.")
else:
    print("  ❌ Critical issues found. Run: python migrations/apply.py")
    sys.exit(1)

cur.close(); conn.close()
