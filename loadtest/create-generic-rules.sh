#!/usr/bin/env bash
#
# create-generic-rules.sh — one threshold rule PER generic telemetry key
# (temperature, humidity, pressure, vibration, voltage, current, rpm,
# flow_rate, signal_strength, battery_pct), each tenant-wide
# (device_id=null) so it auto-applies to ANY device that reports that
# key — including the 20 LoadTest-Sensor devices, each of which only
# has a random 2-4 key subset. One rule per key covers all of them
# instead of enumerating device+key combinations.
#
# USAGE
# -----
#   ./loadtest/create-generic-rules.sh
#
# Config via env vars:
#   BASE_URL   default http://localhost:8000
#   EMAIL      default demo@triaxisai.com (must be TENANT_ADMIN)
#   PASSWORD   default demo1234
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  log "ERROR login failed — check EMAIL/PASSWORD/BASE_URL"
  exit 1
fi

create_rule() {
  local key="$1" condition="$2" threshold="$3" alarm_type="$4"
  log "Creating rule: key=$key $condition $threshold -> $alarm_type (tenant-wide, WARNING)"
  curl -s -X POST "$BASE_URL/api/v1/threshold-rules/" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"key\":\"$key\",\"condition\":\"$condition\",\"threshold\":$threshold,\"severity\":\"WARNING\",\"alarm_type\":\"$alarm_type\",\"is_active\":true}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('  ->', d.get('id', d))" 2>/dev/null
}

# Values are random uniform(10, 500) for these generic sensors — thresholds
# picked near the top/bottom of that range so rules fire occasionally
# under load testing, not on every single reading.
create_rule "temperature"      "gt" "400" "high_temperature"
create_rule "humidity"         "gt" "450" "high_humidity"
create_rule "pressure"         "lt" "50"  "low_pressure"
create_rule "vibration"        "gt" "400" "high_vibration"
create_rule "voltage"          "lt" "50"  "low_voltage"
create_rule "current"          "gt" "400" "overcurrent"
create_rule "rpm"              "gt" "450" "overspeed"
create_rule "flow_rate"        "lt" "50"  "low_flow"
create_rule "signal_strength"  "lt" "50"  "weak_signal"
create_rule "battery_pct"      "lt" "50"  "low_battery"

log "done — 10 tenant-wide generic rules created."
