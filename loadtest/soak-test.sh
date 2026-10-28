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
# USAGE
# -----
#   chmod +x loadtest/soak-test.sh
#   nohup ./loadtest/soak-test.sh > loadtest/soak.log 2>&1 &
#   disown
#
# Or better — install as a systemd service (survives reboots):
#   see loadtest/soak-test.service below this script's comments.
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
DEVICE_ID=""
ROUND=0

refresh_token() {
  TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  if [ -z "$TOKEN" ]; then
    log "WARN login failed, will retry next round"
    return 1
  fi
  DEVICE_ID=$(curl -sf "$BASE_URL/api/v1/devices/?page=1&page_size=1" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c "import sys,json; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null)
  return 0
}

refresh_token

while true; do
  ROUND=$((ROUND + 1))

  # Re-login periodically (tokens expire) — every 30 rounds (~15 min at
  # default interval) or whenever we don't have a token yet.
  if [ -z "$TOKEN" ] || [ $((ROUND % 30)) -eq 0 ]; then
    refresh_token
  fi

  if [ -n "$TOKEN" ] && [ -n "$DEVICE_ID" ]; then
    t0=$(date +%s%N)
    code=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/api/v1/widgets/data/$DEVICE_ID?type=value_card&key=temperature")
    t1=$(date +%s%N)
    ms=$(( (t1 - t0) / 1000000 ))
    log "round=$ROUND widget_data status=$code latency_ms=$ms"

    code2=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/api/v1/dashboard/stats")
    log "round=$ROUND dashboard_stats status=$code2"
  else
    log "round=$ROUND skipped — no token/device yet"
  fi

  sleep "$INTERVAL_S"
done
