#!/usr/bin/env bash
#
# create-rules.sh — create threshold rules (alarms) for:
#   1. GlucIQ-Patient2-Demo / glucose_mmol  > 10   (WARNING — hyperglycemia)
#   2. Pump-01 — one rule PER vibration point, all > 4.5 (CRITICAL — ISO 10816-3):
#        motor_nde_velocity (NDE motor)   motor_de_velocity (DE motor)
#        pump_de_velocity   (DE pump)     pump_nde_velocity (PP pump)
#      Covers all 4 points instead of guessing which one matters — whichever
#      bearing actually goes bad, its own alarm fires with a matching alarm_type.
#
# Run this FIRST to sanity-check the pump vibration key name before
# creating the rule (device key names can vary) — it prints the real
# telemetry keys Pump-01 is currently sending.
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"

TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "ERROR login failed"
  exit 1
fi

get_device_id() {
  curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/devices/?page=1&page_size=50" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for it in data.get('items', []):
    if it['name'] == '$1':
        print(it['id'])
        break
"
}

GLUCIQ_ID=$(get_device_id "GlucIQ-Patient2-Demo")
PUMP_ID=$(get_device_id "Pump-01")

echo "GlucIQ-Patient2-Demo id: ${GLUCIQ_ID:-NOT FOUND}"
echo "Pump-01 id:              ${PUMP_ID:-NOT FOUND}"

if [ -n "$PUMP_ID" ]; then
  echo
  echo "Pump-01 current telemetry keys (confirm vibration key name before creating rule):"
  curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/telemetry/keys/$PUMP_ID" | python3 -m json.tool
fi

create_rule() {
  local device_id="$1" key="$2" condition="$3" threshold="$4" severity="$5" alarm_type="$6"
  echo
  echo "Creating rule: device=$device_id key=$key $condition $threshold severity=$severity"
  curl -sf -X POST "$BASE_URL/api/v1/threshold-rules/" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"device_id\":\"$device_id\",\"key\":\"$key\",\"condition\":\"$condition\",\"threshold\":$threshold,\"severity\":\"$severity\",\"alarm_type\":\"$alarm_type\",\"is_active\":true}" \
    | python3 -m json.tool
}

if [ -n "$GLUCIQ_ID" ]; then
  create_rule "$GLUCIQ_ID" "glucose_mmol" "gt" "10" "WARNING" "high_glucose"
fi

if [ -n "$PUMP_ID" ]; then
  # One rule per vibration point (confirmed key names via the Pump Twin
  # widget's own sensor mapping) — covers all 4 bearings instead of
  # picking just one.
  create_rule "$PUMP_ID" "motor_nde_velocity" "gt" "4.5" "CRITICAL" "high_vibration_nde_motor"
  create_rule "$PUMP_ID" "motor_de_velocity"  "gt" "4.5" "CRITICAL" "high_vibration_de_motor"
  create_rule "$PUMP_ID" "pump_de_velocity"   "gt" "4.5" "CRITICAL" "high_vibration_de_pump"
  create_rule "$PUMP_ID" "pump_nde_velocity"  "gt" "4.5" "CRITICAL" "high_vibration_pp_pump"
fi
