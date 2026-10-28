#!/usr/bin/env bash
#
# create-extra-devices.sh — provisions N generic sensor devices with a
# small set of random numeric telemetry keys, for a bigger/more varied
# load test fleet. Existing scripts pick these up automatically:
#   - live-telemetry.sh discovers devices via `SELECT ... FROM devices`
#   - soak-test.sh discovers devices via GET /devices/ (paginated)
# No changes needed to either script.
#
# USAGE
# -----
#   N=20 ./loadtest/create-extra-devices.sh
#
# Config via env vars:
#   BASE_URL   default http://localhost:8000
#   EMAIL      default demo@triaxisai.com (must be TENANT_ADMIN)
#   PASSWORD   default demo1234
#   N          number of devices to create (default 20)
#   PREFIX     device name prefix (default "LoadTest-Sensor")
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
N="${N:-20}"
PREFIX="${PREFIX:-LoadTest-Sensor}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  log "ERROR login failed — check EMAIL/PASSWORD/BASE_URL"
  exit 1
fi

# Each device gets a DIFFERENT subset of 10 candidate keys (2-4 picked
# at random, see payload generation below) so the fleet isn't uniform —
# more realistic for load testing dashboards/widgets that handle
# varying key sets per device.

for i in $(seq 1 "$N"); do
  name="${PREFIX}-$(printf '%02d' "$i")"

  create_resp=$(curl -sf -X POST "$BASE_URL/api/v1/devices/" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"device_type\":\"sensor\",\"label\":\"Load Test Generic Sensor\"}")

  device_id=$(echo "$create_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  device_token=$(echo "$create_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

  if [ -z "$device_id" ] || [ -z "$device_token" ]; then
    log "device=\"$name\" FAILED to create: $create_resp"
    continue
  fi

  # Pick 2-4 random keys and an initial random value for each, so the
  # device shows up with real data immediately (not just a bare shell).
  payload=$(python3 -c "
import random, json
all_keys = ['temperature','humidity','pressure','vibration','voltage','current','rpm','flow_rate','signal_strength','battery_pct']
picked = random.sample(all_keys, random.randint(2, 4))
values = {k: round(random.uniform(10, 500), 2) for k in picked}
print(json.dumps({'values': values}))
")

  ingest_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$BASE_URL/api/v1/telemetry/ingest/$device_token" \
    -H "Content-Type: application/json" \
    -d "$payload")

  log "device=\"$name\" id=$device_id ingest_status=$ingest_code payload=$payload"
done

log "done — created up to $N devices (prefix=$PREFIX). live-telemetry.sh and soak-test.sh will pick them up on their next device-discovery cycle."
