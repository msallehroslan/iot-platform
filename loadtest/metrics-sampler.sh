#!/usr/bin/env bash
#
# metrics-sampler.sh — samples GET /api/v1/system/metrics every run and
# appends one line of JSON (with a timestamp) to loadtest/metrics.jsonl.
#
# Run this on a schedule (cron, every 5-15 min) ALONGSIDE soak-test.sh
# for 14-30 days. At the end, metrics.jsonl has a time series you can
# plot to look for trends — the thing a short burst test can't show:
#   - process.mem_mb climbing steadily over days → a leak (the
#     websocket_manager Redis pubsub leak in the code review is exactly
#     the kind of thing that only shows up this way)
#   - database.checked_out creeping toward pool_size over time → a
#     connection leak somewhere
#   - cache.status flipping to "disabled"/error → Redis dropped and
#     didn't reconnect
#   - database.latency_ms trending up → DB bloat / missing vacuum / disk
#     pressure building on the free-tier instance
#
# USAGE
# -----
#   chmod +x loadtest/metrics-sampler.sh
#   EMAIL=demo@triaxisai.com PASSWORD=demo1234 ./loadtest/metrics-sampler.sh
#
# To run every 10 minutes for weeks via cron (crontab -e):
#   */10 * * * * cd /mnt/apps/iot-platform && EMAIL=demo@triaxisai.com PASSWORD=demo1234 ./loadtest/metrics-sampler.sh >> loadtest/metrics-sampler.log 2>&1
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-demo@triaxisai.com}"
PASSWORD="${PASSWORD:-demo1234}"
OUT_FILE="${OUT_FILE:-$(dirname "$0")/metrics.jsonl}"

TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "{\"ts\":\"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\",\"error\":\"login_failed\"}" >> "$OUT_FILE"
  exit 1
fi

METRICS=$(curl -sf -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/system/metrics")
if [ -z "$METRICS" ]; then
  echo "{\"ts\":\"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\",\"error\":\"metrics_fetch_failed\"}" >> "$OUT_FILE"
  exit 1
fi

echo "$METRICS" | python3 -c "
import sys, json
m = json.load(sys.stdin)
m['_sampled_at'] = '$(date -u '+%Y-%m-%dT%H:%M:%SZ')'
print(json.dumps(m))
" >> "$OUT_FILE"
