#!/usr/bin/env bash
#
# fix-sensor-ranges.sh — re-ingests realistic values for the KEYS a
# LoadTest-Sensor-* device ALREADY reports (doesn't add/remove keys),
# fixing the "temperature 423.72" problem from the original
# create-extra-devices.sh run (blanket 10-500 range for every key).
#
# live-telemetry.sh nudges by a small % of the CURRENT value each
# round, so once the current value is realistic, future nudges stay
# in a realistic range too — no need to touch live-telemetry.sh.
#
# USAGE
# -----
#   ./loadtest/fix-sensor-ranges.sh
#
# Config via env vars:
#   BASE_URL   default http://localhost:8000
#   EMAIL      default demo@triaxisai.com
#   PASSWORD   default demo1234
#   PREFIX     device name prefix to match (default "LoadTest-Sensor")
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
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

# device_id<TAB>name<TAB>token for every device matching PREFIX
mapfile -t rows < <(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/devices/?limit=100" \
  | python3 -c "
import sys, json
items = json.load(sys.stdin).get('items', [])
for d in items:
    if d['name'].startswith('$PREFIX'):
        print(f\"{d['id']}\t{d['name']}\")
")

log "found ${#rows[@]} devices matching prefix '$PREFIX'"

for row in "${rows[@]}"; do
  [ -z "$row" ] && continue
  device_id="${row%%$'\t'*}"
  name="${row#*$'\t'}"

  # Current key->value, so we know which keys THIS device has (don't
  # add/remove keys, just replace out-of-range values).
  latest=$(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/telemetry/latest/$device_id")

  token=$(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/devices/$device_id" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
  if [ -z "$token" ]; then
    log "device=\"$name\" WARN no token from GET /devices/{id} (token not in response) — trying devices list instead"
    continue
  fi

  payload=$(echo "$latest" | python3 -c "
import sys, json, random
rows = json.load(sys.stdin)
KEY_RANGES = {
    'temperature':      (15, 45),
    'humidity':         (20, 90),
    'pressure':         (80, 120),
    'vibration':        (0.5, 5),
    'voltage':          (210, 240),
    'current':          (0, 20),
    'rpm':              (500, 3000),
    'flow_rate':        (10, 100),
    'signal_strength':  (0, 100),
    'battery_pct':      (0, 100),
}
values = {}
for r in rows:
    k = r['key']
    if k in KEY_RANGES:
        values[k] = round(random.uniform(*KEY_RANGES[k]), 2)
print(json.dumps({'values': values}) if values else '{}')
")

  if [ "$payload" = "{}" ]; then
    log "device=\"$name\" no known generic keys to fix, skipping"
    continue
  fi

  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$BASE_URL/api/v1/telemetry/ingest/$token" \
    -H "Content-Type: application/json" \
    -d "$payload")
  log "device=\"$name\" fixed_status=$code payload=$payload"
done

log "done."
