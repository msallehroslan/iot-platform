#!/usr/bin/env bash
#
# live-telemetry.sh — makes the dashboard look "alive": continuously
# sends small, nudged telemetry updates for every device, so each device
# shows ONLINE and its numeric widgets actually move in real time.
#
# This is DIFFERENT from soak-test.sh (which only reads — GETs widget
# data to check backend health over time) and seed_loadtest_data.py
# (which backfills HISTORY). This script does WRITES: POSTs to the
# device ingest endpoint, exactly like a real device checking in.
#
# How it works: for each device, it reads the device's own current
# latest numeric values (via the JWT-authenticated API), nudges each one
# by a small random amount, and POSTs that as a new ingest using the
# device's own TOKEN (the same auth a real device uses) — so values
# drift realistically around wherever they already are, instead of
# jumping to arbitrary numbers.
#
# USAGE
# -----
#   chmod +x loadtest/live-telemetry.sh
#   nohup ./loadtest/live-telemetry.sh > loadtest/live-telemetry.log 2>&1 &
#   disown
#
# Stop it:
#   pkill -f live-telemetry.sh
#
# Config via env vars:
#   BASE_URL         default http://localhost:8000
#   EMAIL/PASSWORD   demo creds (to list devices + read current values)
#   INTERVAL_S       seconds between ingests per device (default 10)
#   NUDGE_PCT        max %% change per tick, e.g. 0.02 = +/-2%% (default 0.02)
#
# Device tokens are read directly from the DB (docker exec postgres) —
# the ingest endpoint authenticates by token, not JWT, and tokens aren't
# returned by the normal device list API for security.
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
INTERVAL_S="${INTERVAL_S:-10}"
NUDGE_PCT="${NUDGE_PCT:-0.02}"
PG_CONTAINER="${PG_CONTAINER:-postgres}"
PG_URL="${PG_URL:-}"   # optional override, e.g. postgresql://user:pass@localhost:5432/iotdb

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

if [ -z "$PG_URL" ]; then
  # Pull DATABASE_URL straight from the running backend container's env
  # so this script doesn't need the password duplicated here.
  PG_URL=$(docker exec backend printenv DATABASE_URL 2>/dev/null | sed 's#postgres:5432#localhost:5432#')
fi
if [ -z "$PG_URL" ]; then
  log "ERROR could not determine DATABASE_URL — set PG_URL explicitly"
  exit 1
fi

log "live-telemetry starting — BASE_URL=$BASE_URL interval=${INTERVAL_S}s nudge=${NUDGE_PCT}"

TOKEN=""
ROUND=0
DEVICE_IDS=()
DEVICE_NAMES=()
DEVICE_TOKENS=()

refresh_jwt() {
  TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  [ -z "$TOKEN" ] && { log "WARN login failed"; return 1; }
  return 0
}

refresh_devices() {
  DEVICE_IDS=(); DEVICE_NAMES=(); DEVICE_TOKENS=()
  mapfile -t rows < <(docker exec "$PG_CONTAINER" psql "$PG_URL" -t -A -F $'\t' \
    -c "SELECT id, name, token FROM devices;" 2>/dev/null)
  for row in "${rows[@]}"; do
    [ -z "$row" ] && continue
    id="${row%%$'\t'*}"
    rest="${row#*$'\t'}"
    name="${rest%%$'\t'*}"
    tok="${rest#*$'\t'}"
    DEVICE_IDS+=("$id"); DEVICE_NAMES+=("$name"); DEVICE_TOKENS+=("$tok")
  done
  log "devices for live telemetry: ${#DEVICE_IDS[@]} (${DEVICE_NAMES[*]:-none})"
}

nudge_and_send() {
  local device_id="$1" device_name="$2" device_token="$3"
  local latest
  latest=$(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/telemetry/latest/$device_id")
  [ -z "$latest" ] && { log "device=\"$device_name\" no latest telemetry, skipping"; return; }

  local payload
  payload=$(echo "$latest" | python3 -c "
import sys, json, random
rows = json.load(sys.stdin)
nudge_pct = float('$NUDGE_PCT')
out = {}
for r in rows:
    v = r.get('value')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        delta = v * nudge_pct * random.uniform(-1, 1)
        out[r['key']] = round(v + delta, 4)
if not out:
    print('{}')
else:
    print(json.dumps({'values': out}))
")

  if [ "$payload" = "{}" ]; then
    log "device=\"$device_name\" no numeric keys to nudge, skipping"
    return
  fi

  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$BASE_URL/api/v1/telemetry/ingest/$device_token" \
    -H "Content-Type: application/json" \
    -d "$payload")
  n_keys=$(echo "$payload" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['values']))")
  log "device=\"$device_name\" ingest status=$code keys=$n_keys"
}

refresh_jwt
refresh_devices

while true; do
  ROUND=$((ROUND + 1))

  if [ -z "$TOKEN" ] || [ "${#DEVICE_IDS[@]}" -eq 0 ] || [ $((ROUND % 30)) -eq 0 ]; then
    refresh_jwt && refresh_devices
  fi

  if [ -n "$TOKEN" ] && [ "${#DEVICE_IDS[@]}" -gt 0 ]; then
    for idx in "${!DEVICE_IDS[@]}"; do
      nudge_and_send "${DEVICE_IDS[$idx]}" "${DEVICE_NAMES[$idx]}" "${DEVICE_TOKENS[$idx]}"
    done
  else
    log "round=$ROUND skipped — no token/devices yet"
  fi

  sleep "$INTERVAL_S"
done
