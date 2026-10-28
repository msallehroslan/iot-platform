#!/usr/bin/env bash
#
# check-performance.sh — one-shot snapshot of dashboard/backend health,
# meant to be run a few minutes into (or right after) a load test
# (soak-test.sh / k6-script.js / live-telemetry.sh) to see how the
# system is holding up: DB pool, memory, cache, request errors,
# latency, and container resource usage.
#
# USAGE
# -----
#   ./loadtest/check-performance.sh
#
# Config via env vars:
#   BASE_URL   default http://localhost:8000
#   EMAIL      default demo@triaxisai.com
#   PASSWORD   default demo1234
#   SOAK_LOG   default loadtest/soak.log
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
SOAK_LOG="${SOAK_LOG:-$(dirname "$0")/soak.log}"

hr() { printf '\n=== %s ===\n' "$1"; }

hr "1. System metrics (DB pool / memory / cache)"
TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "WARN login failed — check EMAIL/PASSWORD/BASE_URL"
else
  curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/system/metrics" | python3 -m json.tool 2>/dev/null \
    || echo "WARN could not fetch /system/metrics"
fi

hr "2. soak-test.sh log — recent non-200 responses"
if [ -f "$SOAK_LOG" ]; then
  n_bad=$(grep -vc "status=200" "$SOAK_LOG" 2>/dev/null || echo 0)
  echo "non-200 lines: $n_bad (showing last 20)"
  grep -v "status=200" "$SOAK_LOG" 2>/dev/null | tail -20
else
  echo "no $SOAK_LOG found — soak-test.sh not running or logging elsewhere"
fi

hr "3. soak-test.sh log — widget_data latency (ms)"
if [ -f "$SOAK_LOG" ]; then
  grep -o 'latency_ms=[0-9]*' "$SOAK_LOG" 2>/dev/null | cut -d= -f2 | \
    awk '{sum+=$1; n++; if(n==1||$1<min)min=$1; if($1>max)max=$1}
         END{if(n>0) printf "n=%d avg=%.0fms min=%dms max=%dms\n", n, sum/n, min, max;
             else print "no latency samples found"}'
else
  echo "no $SOAK_LOG found"
fi

hr "4. Backend log — errors/timeouts/slow requests (last 5 min)"
docker compose logs backend --since 5m 2>/dev/null | grep -iE "error|timeout|queuepool|slow_request" | tail -30 \
  || echo "WARN could not read backend container logs (run from the compose project dir?)"

hr "5. Docker container resource usage"
docker stats --no-stream 2>/dev/null || echo "WARN could not read docker stats"

echo
