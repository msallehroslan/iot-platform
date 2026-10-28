/**
 * k6 load test — IoT Platform
 *
 * Covers the 3 areas discussed:
 *   1. dashboard  — many users hitting widget/dashboard reads
 *                   (validates the Redis cache fix actually reduces DB load)
 *   2. ingest     — many devices POSTing telemetry concurrently
 *   3. taat_chat  — concurrent AI chat requests
 *                   (validates the TAAT "parallel executor" fix)
 *
 * USAGE
 * -----
 * Install k6:  https://k6.io/docs/get-started/installation/
 *   macOS:   brew install k6
 *   Linux:   sudo apt install k6   (or see docs for your distro)
 *
 * Run ONE scenario at a time against LOCAL first:
 *   k6 run -e BASE_URL=http://localhost:8000 -e EMAIL=you@example.com \
 *       -e PASSWORD=yourpass -e SCENARIO=dashboard loadtest/k6-script.js
 *
 *   k6 run -e BASE_URL=http://localhost:8000 -e DEVICE_TOKEN=<a real device token> \
 *       -e SCENARIO=ingest loadtest/k6-script.js
 *
 *   k6 run -e BASE_URL=http://localhost:8000 -e EMAIL=... -e PASSWORD=... \
 *       -e SCENARIO=taat_chat loadtest/k6-script.js
 *
 * MULTIPLE USERS instead of one shared account (avoids hitting one
 * user's rate limit, e.g. TAAT chat's 20/hour cap, and is a more
 * realistic "many real users" load). First provision them:
 *   ./loadtest/create-test-users.sh
 * Then pass the comma-separated list it prints via EMAILS instead of
 * EMAIL — each VU picks one deterministically by its VU number:
 *   k6 run -e BASE_URL=http://localhost:8000 \
 *       -e EMAILS="$(cat loadtest/test-users.txt)" -e PASSWORD=loadtest123 \
 *       -e SCENARIO=taat_chat loadtest/k6-script.js
 * (EMAIL still works for a single shared account — EMAILS takes
 * priority when both are set.)
 *
 * Only point BASE_URL at staging/production once you've checked the
 * numbers locally and you're ready to watch /api/v1/system/metrics
 * (DB latency, pool checkout, Redis status) WHILE it runs. Start with
 * VUS=5 there, not the defaults below — this hits a shared DB.
 */
import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const EMAIL = __ENV.EMAIL || "";
const EMAILS = (__ENV.EMAILS || "").split(",").map(e => e.trim()).filter(Boolean);
const PASSWORD = __ENV.PASSWORD || "";
const DEVICE_TOKEN = __ENV.DEVICE_TOKEN || "";
const SCENARIO = __ENV.SCENARIO || "dashboard";

// One user per VU — deterministic so the same VU always re-uses the
// same account's token cache below, round-robining if there are more
// VUs than accounts.
function emailForThisVU() {
  if (EMAILS.length > 0) {
    return EMAILS[(__VU - 1) % EMAILS.length];
  }
  return EMAIL;
}

// Build only the ONE active scenario — k6 requires every key present in
// `scenarios` to have a valid executor, so conditionally-undefined entries
// (the previous approach) make it fail with "doesn't have a specified
// executor type" regardless of which SCENARIO you pick.
const SCENARIO_DEFS = {
  dashboard: {
    executor: "ramping-vus",
    exec: "dashboardScenario",
    startVUs: 0,
    stages: [
      { duration: "30s", target: 20 },   // ramp to 20 concurrent "users"
      { duration: "1m",  target: 20 },   // hold
      { duration: "15s", target: 0 },    // ramp down
    ],
  },
  ingest: {
    executor: "constant-arrival-rate",
    exec: "ingestScenario",
    rate: 50,              // 50 ingest requests/sec
    timeUnit: "1s",
    duration: "1m",
    preAllocatedVUs: 20,
    maxVUs: 100,
  },
  taat_chat: {
    executor: "ramping-vus",
    exec: "taatChatScenario",
    startVUs: 0,
    stages: [
      { duration: "20s", target: 5 },    // chat is expensive — keep this low
      { duration: "40s", target: 5 },
      { duration: "10s", target: 0 },
    ],
  },
};

if (!SCENARIO_DEFS[SCENARIO]) {
  throw new Error(
    `Unknown SCENARIO '${SCENARIO}' — use dashboard, ingest, or taat_chat`,
  );
}

export const options = {
  scenarios: {
    [SCENARIO]: SCENARIO_DEFS[SCENARIO],
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],       // <5% errors
    http_req_duration: ["p(95)<2000"],    // 95% of requests under 2s
  },
};

// ── Shared login (dashboard + taat_chat scenarios) ───────────────────────────

function login() {
  const email = emailForThisVU();
  if (!email || !PASSWORD) {
    throw new Error("Set -e EMAIL=... (or EMAILS=...) -e PASSWORD=... for this scenario");
  }
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );
  check(res, { "login ok": (r) => r.status === 200 });
  const body = res.json();
  return body.access_token;
}

// ── Scenario 1: dashboard / widget reads ─────────────────────────────────────
// Hits EVERY device on the tenant each iteration (not just one) — each
// VU logs in as its own user (see emailForThisVU()/EMAILS above) and
// looks up each device's own real telemetry key (not hardcoded
// "temperature", which only Pump-01 happens to have) the same way
// soak-test.sh does, so this works across different device types.

let _token;
let _devices;  // [{id, key}, ...]

function discoverDevices(token) {
  const devicesRes = http.get(`${BASE_URL}/api/v1/devices/?page=1&page_size=50`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const items = devicesRes.json("items") || [];
  return items.map((d) => {
    const keysRes = http.get(`${BASE_URL}/api/v1/telemetry/keys/${d.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const keys = (keysRes.json("keys") || []);
    return { id: d.id, key: keys[0] || null };
  });
}

export function dashboardScenario() {
  if (!_token) {
    _token = login();
    _devices = discoverDevices(_token);
  }
  if (!_devices || _devices.length === 0) {
    console.warn("No devices found on this tenant — add at least one device first");
    sleep(1);
    return;
  }

  const headers = { Authorization: `Bearer ${_token}` };

  const r1 = http.get(`${BASE_URL}/api/v1/dashboard/stats`, { headers });
  check(r1, { "stats 200": (r) => r.status === 200 });

  for (const device of _devices) {
    if (!device.key) continue;  // no telemetry yet for this device — skip

    const r2 = http.get(
      `${BASE_URL}/api/v1/widgets/data/${device.id}?type=value_card&key=${device.key}`,
      { headers },
    );
    check(r2, { "widget 200": (r) => r.status === 200 });

    const r3 = http.get(
      `${BASE_URL}/api/v1/widgets/data/${device.id}?type=line_chart&key=${device.key}&hours=24`,
      { headers },
    );
    check(r3, { "line_chart 200": (r) => r.status === 200 });
  }

  sleep(1 + Math.random());  // mimic a human refreshing every 1-2s
}

// ── Scenario 2: telemetry ingest ──────────────────────────────────────────────

export function ingestScenario() {
  if (!DEVICE_TOKEN) {
    throw new Error("Set -e DEVICE_TOKEN=... (a real device token) for this scenario");
  }
  const payload = JSON.stringify({
    values: {
      temperature: 20 + Math.random() * 15,
      humidity: 40 + Math.random() * 30,
    },
  });
  const res = http.post(
    `${BASE_URL}/api/v1/telemetry/ingest/${DEVICE_TOKEN}`,
    payload,
    { headers: { "Content-Type": "application/json" } },
  );
  check(res, {
    "ingest 200": (r) => r.status === 200,
    "ingest not rate-limited": (r) => r.status !== 429,
  });
}

// ── Scenario 3: TAAT chat ─────────────────────────────────────────────────────

export function taatChatScenario() {
  if (!_token) {
    _token = login();
  }
  const res = http.post(
    `${BASE_URL}/api/v1/intelligence/chat`,
    JSON.stringify({
      messages: [{ role: "user", content: "What's the status of my devices?" }],
    }),
    {
      headers: {
        Authorization: `Bearer ${_token}`,
        "Content-Type": "application/json",
      },
      timeout: "30s",
    },
  );
  check(res, { "chat 200": (r) => r.status === 200 });
  sleep(2);
}
