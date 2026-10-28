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

# Our known tenant-wide generic alarm_types — used to find and remove
# any earlier version of these rules before recreating, so re-running
# this script (e.g. after fixing thresholds) doesn't pile up duplicates.
KNOWN_TYPES="high_temperature high_humidity low_pressure high_vibration low_voltage overcurrent overspeed low_flow weak_signal low_battery"

log "removing any existing tenant-wide rules with a known generic alarm_type..."
curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/threshold-rules/" \
  | python3 -c "
import sys, json
known = set('$KNOWN_TYPES'.split())
rules = json.load(sys.stdin)
for r in rules:
    if r.get('device_id') is None and r.get('alarm_type') in known:
        print(r['id'])
" | while read -r rule_id; do
    [ -z "$rule_id" ] && continue
    curl -s -o /dev/null -X DELETE "$BASE_URL/api/v1/threshold-rules/$rule_id" \
      -H "Authorization: Bearer $TOKEN"
    log "  deleted old rule id=$rule_id"
  done

create_rule() {
  local key="$1" condition="$2" threshold="$3" alarm_type="$4"
  log "Creating rule: key=$key $condition $threshold -> $alarm_type (tenant-wide, WARNING)"
  curl -s -X POST "$BASE_URL/api/v1/threshold-rules/" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"key\":\"$key\",\"condition\":\"$condition\",\"threshold\":$threshold,\"severity\":\"WARNING\",\"alarm_type\":\"$alarm_type\",\"is_active\":true}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('  ->', d.get('id', d))" 2>/dev/null
}

# Thresholds matched to each key's realistic range (see
# create-extra-devices.sh / fix-sensor-ranges.sh KEY_RANGES), not the
# old blanket 10-500 range.
create_rule "temperature"      "gt" "40"   "high_temperature"  # range 15-45 C
create_rule "humidity"         "gt" "85"   "high_humidity"     # range 20-90 %
create_rule "pressure"         "lt" "85"   "low_pressure"      # range 80-120 kPa
create_rule "vibration"        "gt" "4.5"  "high_vibration"    # range 0.5-5 mm/s, ISO 10816-3
create_rule "voltage"          "lt" "215"  "low_voltage"       # range 210-240 V
create_rule "current"          "gt" "18"   "overcurrent"       # range 0-20 A
create_rule "rpm"              "gt" "2800" "overspeed"         # range 500-3000 RPM
create_rule "flow_rate"        "lt" "15"   "low_flow"          # range 10-100 L/min
create_rule "signal_strength"  "lt" "20"   "weak_signal"       # range 0-100 %
create_rule "battery_pct"      "lt" "20"   "low_battery"       # range 0-100 %

log "done — 10 tenant-wide generic rules created/refreshed."
