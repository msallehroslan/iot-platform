#!/usr/bin/env bash
#
# ingest-stress-test.sh — hammers the telemetry ingest endpoint for all
# devices as FAST as possible (no interval delay, unlike
# live-telemetry.sh's steady 10s nudge) for a short burst, to find
# actual ingest throughput capacity rather than current configured rate.
#
# USAGE
# -----
#   DURATION_S=20 ./loadtest/ingest-stress-test.sh
#
# Config via env vars:
#   BASE_URL           default http://localhost:8000
#   PG_CONTAINER       default postgres
#   DURATION_S         how long to hammer, in seconds (default 20)
#   WORKERS_PER_DEVICE parallel hammer loops per device token (default 1) —
#                       raise this to push throughput higher without
#                       creating more device records; each worker hammers
#                       the SAME device's token independently.
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
PG_CONTAINER="${PG_CONTAINER:-postgres}"
DURATION_S="${DURATION_S:-20}"
WORKERS_PER_DEVICE="${WORKERS_PER_DEVICE:-1}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

PG_URL=$(docker exec backend printenv DATABASE_URL 2>/dev/null | sed 's#postgres:5432#localhost:5432#')
if [ -z "$PG_URL" ]; then
  log "ERROR could not determine DATABASE_URL"
  exit 1
fi

mapfile -t rows < <(docker exec "$PG_CONTAINER" psql "$PG_URL" -t -A -F $'\t' \
  -c "SELECT id, name, token FROM devices;" 2>/dev/null)

DEVICE_NAMES=(); DEVICE_TOKENS=()
for row in "${rows[@]}"; do
  [ -z "$row" ] && continue
  id="${row%%$'\t'*}"; rest="${row#*$'\t'}"
  name="${rest%%$'\t'*}"; tok="${rest#*$'\t'}"
  DEVICE_NAMES+=("$name"); DEVICE_TOKENS+=("$tok")
done

n_devices="${#DEVICE_TOKENS[@]}"
log "stress-testing ingest for $n_devices devices x $WORKERS_PER_DEVICE worker(s) each, duration=${DURATION_S}s, no delay between ingests"

COUNT_FILE=$(mktemp)
echo 0 > "$COUNT_FILE"
FAIL_FILE=$(mktemp)
echo 0 > "$FAIL_FILE"

hammer_device() {
  local token="$1" local_count=0 local_fail=0
  local end_ts=$(( $(date +%s) + DURATION_S ))
  while [ "$(date +%s)" -lt "$end_ts" ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
      "$BASE_URL/api/v1/telemetry/ingest/$token" \
      -H "Content-Type: application/json" \
      -d "{\"values\":{\"stress_test\":$RANDOM}}")
    if [ "$code" = "200" ]; then
      local_count=$((local_count + 1))
    else
      local_fail=$((local_fail + 1))
    fi
  done
  # Append this worker's totals; summed after all workers finish.
  echo "$local_count" >> "$COUNT_FILE.parts"
  echo "$local_fail"  >> "$FAIL_FILE.parts"
}

touch "$COUNT_FILE.parts" "$FAIL_FILE.parts"

pids=()
for token in "${DEVICE_TOKENS[@]}"; do
  for w in $(seq 1 "$WORKERS_PER_DEVICE"); do
    hammer_device "$token" &
    pids+=($!)
  done
done

log "hammering... (waiting ${DURATION_S}s + wrap-up)"
for pid in "${pids[@]}"; do wait "$pid"; done

total_ok=$(awk '{s+=$1} END{print s+0}' "$COUNT_FILE.parts")
total_fail=$(awk '{s+=$1} END{print s+0}' "$FAIL_FILE.parts")
rate_per_min=$(( total_ok * 60 / DURATION_S ))

log "done. successful_ingests=$total_ok failed=$total_fail duration=${DURATION_S}s"
log "=> approx max ingest-call throughput: $rate_per_min calls/min (single key per call in this test)"

rm -f "$COUNT_FILE" "$FAIL_FILE" "$COUNT_FILE.parts" "$FAIL_FILE.parts"
