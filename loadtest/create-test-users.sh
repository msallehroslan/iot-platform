#!/usr/bin/env bash
#
# create-test-users.sh — provisions N test users in the SAME tenant as
# the admin account (so they can see/chat about the same devices),
# for load testing with multiple distinct users instead of hammering
# one shared account (which hits per-user rate limits like TAAT chat's
# 20/hour cap).
#
# USAGE
# -----
#   ADMIN_EMAIL=demo@triaxisai.com ADMIN_PASSWORD=demo1234 \
#   N_USERS=5 ./loadtest/create-test-users.sh
#
# Prints a comma-separated EMAILS= line at the end — pass that straight
# into k6-script.js's taat_chat scenario:
#   k6 run -e EMAILS="$(cat loadtest/test-users.txt)" -e PASSWORD=loadtest123 ...
#
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_EMAIL="${ADMIN_EMAIL:-demo@triaxisai.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-demo1234}"
N_USERS="${N_USERS:-5}"
TEST_PASSWORD="${TEST_PASSWORD:-loadtest123}"
OUT_FILE="${OUT_FILE:-$(dirname "$0")/test-users.txt}"

ADMIN_TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [ -z "$ADMIN_TOKEN" ]; then
  echo "ERROR: admin login failed — check ADMIN_EMAIL/ADMIN_PASSWORD" >&2
  exit 1
fi

EMAILS=()
for i in $(seq 1 "$N_USERS"); do
  email="loadtest$i@test.local"
  resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/auth/users/invite" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$email\",\"password\":\"$TEST_PASSWORD\",\"first_name\":\"Load\",\"last_name\":\"Test$i\",\"role\":\"TENANT_USER\"}")
  code=$(echo "$resp" | tail -1)
  if [ "$code" = "201" ] || [ "$code" = "400" ]; then
    # 400 = "Email already registered" — fine, user from a previous run
    echo "  $email — ok (status $code)"
    EMAILS+=("$email")
  else
    echo "  $email — FAILED (status $code): $(echo "$resp" | head -1)"
  fi
done

if [ "${#EMAILS[@]}" -eq 0 ]; then
  echo "ERROR: no test users created" >&2
  exit 1
fi

joined=$(IFS=,; echo "${EMAILS[*]}")
echo "$joined" > "$OUT_FILE"

echo ""
echo "Created/confirmed ${#EMAILS[@]} test users (password: $TEST_PASSWORD)"
echo "Saved to $OUT_FILE"
echo ""
echo "Use with k6:"
echo "  k6 run -e BASE_URL=$BASE_URL -e EMAILS=\"\$(cat $OUT_FILE)\" -e PASSWORD=$TEST_PASSWORD -e SCENARIO=taat_chat loadtest/k6-script.js"
