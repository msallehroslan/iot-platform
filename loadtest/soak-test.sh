#!/usr/bin/env bash
#
# soak-test.sh — lightweight CONTINUOUS background load for endurance
# testing (days/weeks), as opposed to k6-script.js which is a short
# burst (minutes) load test.
#
# Why not just run k6 for 30 days straight: k6 is built for short/medium
# runs, its summary reporting isn't designed for week-long sessions, and
# on a Pi-class machine (cm5) a sustained 20-VU k6 process is more load
# than a soak test needs — a soak test's point is DURATION, not peak
# throughput. This script instead does a light, steady trickle of real
# requests (a handful per minute) that's gentle enough to run for weeks
# without burning disk/CPU, while still exercising the WebSocket
# connection lifecycle, cache reads, and DB pool over a long period —
# exactly what would surface slow leaks (e.g. the websocket_manager
# Redis pubsub leak noted in the code review) that a 2-minute burst
# test can't catch.
#
# Rotates through EVERY device on the tenant (not just one) — one
# device per round, cycling back to the start. Each device's own
# telemetry keys are looked up (not hardcoded), so this works generically
# across different device types (pump, counter, patient monitor, ...).
#
# USAGE
# -----
#   chmod +x loadtest/soak-test.sh
#   nohup ./loadtest/soak-test.sh > loadtest/soak.log 2>&1 &
#   disown
#
# Stop it:
#   pkill -f soak-test.sh
#
# Config via env vars:
#   BASE_URL   default http://localhost:8000
#   EMAIL/PASSWORD  demo creds
#   INTERVAL_S      seconds between each round of requests (default 30)
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
INTERVAL_S="${INTERVAL_S:-30}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

log "soak-test starting — BASE_URL=$BASE_URL interval=${INTERVAL_S}s"

TOKEN=""
ROUND=0

# Parallel bash arrays: DEVICE_IDS[i] / DEVICE_NAMES[i] / DEVICE_KEYS[i]
DEVICE_IDS=()
DEVICE_NAMES=()
DEVICE_KEYS=()   # first known telemetry key for that device, or "" if none yet

refresh_token() {
  TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  if [ -z "$TOKEN" ]; then
    log "WARN login failed, will retry next round"
    return 1
  fi
  return 0
}

refresh_devices() {
  # Pull ALL devices (paginate in case there are more than page_size),
  # then look up one real telemetry key per device so the widget_data
  # call below works for any device type, not just ones with "temperature".
  local page=1 page_size=50
  DEVICE_IDS=(); DEVICE_NAMES=(); DEVICE_KEYS=()

  while true; do
    page_json=$(curl -sf -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/api/v1/devices/?page=$page&page_size=$page_size")
    [ -z "$page_json" ] && break

    mapfile -t ids_names < <(echo "$page_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
items = data.get('items', [])
for it in items:
    print(f\"{it['id']}\t{it['name']}\")
" 2>/dev/null)

    [ "${#ids_names[@]}" -eq 0 ] && break

    for row in "${ids_names[@]}"; do
      id="${row%%$'\t'*}"
      name="${row#*$'\t'}"
      DEVICE_IDS+=("$id")
      DEVICE_NAMES+=("$name")
    done

    total=$(echo "$page_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null)
    if [ -n "$total" ] && [ "$((page * page_size))" -ge "$total" ]; then
      break
    fi
    page=$((page + 1))
  done

  for id in "${DEVICE_IDS[@]}"; do
    key=$(curl -sf -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/api/v1/telemetry/keys/$id" \
      | python3 -c "import sys,json; ks=json.load(sys.stdin).get('keys',[]); print(ks[0] if ks else '')" 2>/dev/null)
    DEVICE_KEYS+=("$key")
  done

  log "devices discovered: ${#DEVICE_IDS[@]} (${DEVICE_NAMES[*]:-none})"
}

refresh_token
refresh_devices

while true; do
  ROUND=$((ROUND + 1))

  # Re-login + re-discover devices periodically — every 30 rounds
  # (~15 min at default interval), or whenever we have nothing yet.
  if [ -z "$TOKEN" ] || [ "${#DEVICE_IDS[@]}" -eq 0 ] || [ $((ROUND % 30)) -eq 0 ]; then
    refresh_token && refresh_devices
  fi

  n="${#DEVICE_IDS[@]}"
  if [ -n "$TOKEN" ] && [ "$n" -gt 0 ]; then
    # Hit every device once per round (not round-robin) — with only a
    # handful of devices this is still a light request rate overall,
    # and it exercises all of them continuously rather than each one
    # only once every N rounds.
    for idx in "${!DEVICE_IDS[@]}"; do
      device_id="${DEVICE_IDS[$idx]}"
      device_name="${DEVICE_NAMES[$idx]}"
      key="${DEVICE_KEYS[$idx]}"

      if [ -n "$key" ]; then
        t0=$(date +%s%N)
        code=$(curl -s -o /dev/null -w "%{http_code}" \
          -H "Authorization: Bearer $TOKEN" \
          "$BASE_URL/api/v1/widgets/data/$device_id?type=value_card&key=$key")
        t1=$(date +%s%N)
        ms=$(( (t1 - t0) / 1000000 ))
        log "round=$ROUND device=\"$device_name\" key=$key widget_data status=$code latency_ms=$ms"
      else
        log "round=$ROUND device=\"$device_name\" skipped — no telemetry key known yet"
      fi
    done

    code2=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/api/v1/dashboard/stats")
    log "round=$ROUND dashboard_stats status=$code2"
  else
    log "round=$ROUND skipped — no token/devices yet"
  fi

  sleep "$INTERVAL_S"
done
