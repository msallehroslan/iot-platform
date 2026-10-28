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

RESTART_ISO=""
if [ -z "$TOKEN" ]; then
  echo "WARN login failed — check EMAIL/PASSWORD/BASE_URL"
else
  METRICS=$(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/system/metrics")
  if [ -n "$METRICS" ]; then
    echo "$METRICS" | python3 -m json.tool
    # Backend container start time, used below to split "overall" vs
    # "since this backend process started" latency — a deploy/restart
    # mid-test otherwise silently blends pre-fix and post-fix numbers
    # into one misleading average.
    RESTART_ISO=$(echo "$METRICS" | python3 -c "
import sys, json, datetime
m = json.load(sys.stdin)
up = m.get('uptime_seconds')
if up is not None:
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=up)
    print(t.strftime('%Y-%m-%dT%H:%M:%SZ'))
" 2>/dev/null)
    [ -n "$RESTART_ISO" ] && echo "(backend process started around $RESTART_ISO)"
  else
    echo "WARN could not fetch /system/metrics"
  fi
fi

hr "2. soak-test.sh log — recent non-200 / failed responses"
if [ -f "$SOAK_LOG" ]; then
  n_bad=$(grep -vc "status=200" "$SOAK_LOG" 2>/dev/null || echo 0)
  n_000=$(grep -c "status=000" "$SOAK_LOG" 2>/dev/null || echo 0)
  echo "non-200 lines: $n_bad (of which status=000 — connection refused/dropped, e.g. during a backend restart: $n_000)"
  echo "showing last 20:"
  grep -v "status=200" "$SOAK_LOG" 2>/dev/null | tail -20
else
  echo "no $SOAK_LOG found — soak-test.sh not running or logging elsewhere"
fi

hr "3. soak-test.sh log — widget_data latency (ms)"
if [ -f "$SOAK_LOG" ]; then
  echo "overall (whole log, may blend pre/post any deploy mid-test):"
  grep -o 'latency_ms=[0-9]*' "$SOAK_LOG" 2>/dev/null | cut -d= -f2 | \
    awk '{sum+=$1; n++; if(n==1||$1<min)min=$1; if($1>max)max=$1}
         END{if(n>0) printf "n=%d avg=%.0fms min=%dms max=%dms\n", n, sum/n, min, max;
             else print "no latency samples found"}'

  if [ -n "$RESTART_ISO" ]; then
    echo "since backend restart ($RESTART_ISO onward):"
    awk -F'[][]' -v cut="$RESTART_ISO" '$2 >= cut' "$SOAK_LOG" 2>/dev/null | \
      grep -o 'latency_ms=[0-9]*' | cut -d= -f2 | \
      awk '{sum+=$1; n++; if(n==1||$1<min)min=$1; if($1>max)max=$1}
           END{if(n>0) printf "n=%d avg=%.0fms min=%dms max=%dms\n", n, sum/n, min, max;
               else print "no samples since restart yet"}'
  fi
else
  echo "no $SOAK_LOG found"
fi

hr "4. Backend log — errors/timeouts/slow requests (last 5 min)"
BACKEND_LOGS=$(docker compose logs backend --since 5m 2>&1)
if [ -z "$BACKEND_LOGS" ] || echo "$BACKEND_LOGS" | grep -qi "unknown command\|no configuration file\|error response from daemon"; then
  BACKEND_LOGS=$(docker logs backend --since 5m 2>&1)
fi
MATCHES=$(echo "$BACKEND_LOGS" | grep -iE "error|timeout|queuepool|slow_request" | tail -30)
if [ -n "$MATCHES" ]; then
  echo "$MATCHES"
elif [ -n "$BACKEND_LOGS" ]; then
  echo "(no matching error/timeout/slow_request lines in the last 5 min)"
else
  echo "WARN could not read backend container logs"
fi

hr "5. Docker container resource usage"
docker stats --no-stream 2>/dev/null || echo "WARN could not read docker stats"

echo
