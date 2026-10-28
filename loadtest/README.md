# Load testing — IoT Platform

## Current setup (as of this writing)

**Fleet**: 23 devices total —
- `Pump-01`, `GlucIQ-Patient2-Demo`, `Swiftlet-Counter-001` (original demo devices)
- `LoadTest-Sensor-01` .. `LoadTest-Sensor-20` (generic sensors, provisioned via
  `create-extra-devices.sh`) — each reports a random 2-4 key subset from:
  `temperature` (15-45°C), `humidity` (20-90%), `pressure` (80-120kPa),
  `vibration` (0.5-5mm/s), `voltage` (210-240V), `current` (0-20A),
  `rpm` (500-3000), `flow_rate` (10-100L/min), `signal_strength` (0-100%),
  `battery_pct` (0-100%)

**Alarm rules** (Rule Chains page): Pump-01 has 5 CRITICAL vibration rules
(ISO 10816-3, one per bearing point) + GlucIQ has 1 glucose WARNING rule —
all device-scoped. Plus 10 tenant-wide (`device_id=null`) WARNING rules, one
per generic key above, from `create-generic-rules.sh` — these auto-apply to
any device reporting that key, so they cover all 20 LoadTest-Sensor devices
without per-device enumeration.

**Background scripts normally running**:
- `live-telemetry.sh` — nudges every device's current values ±2% every
  ~10s, keeps devices ACTIVE and the dashboard visually live. Safe to leave
  running continuously; low overhead (~342 telemetry rows/min at 23 devices).
- `soak-test.sh` — NOT running by default. Start only when actively load
  testing; it's a genuinely heavy 100-user continuous read load (see
  "Known performance numbers" below) and will make the dashboard feel
  slower for anyone using it live while it runs. Always `pkill -f
  soak-test.sh` when done, don't leave it running unattended.

**100 test user accounts**: `loadtest1@test.local` .. `loadtest100@test.local`,
password `loadtest123` (NOT the demo admin's `demo1234` — using the wrong
password for these accounts across 100 concurrent logins is exactly what
tripped the per-IP login rate limiter during testing; see
`backend/app/routers/auth.py`'s `/login` rate limit, 5 failed attempts per
IP per 15 min, min lesson: always set `PASSWORD=loadtest123` when scripting
logins for these accounts).

**Known performance numbers** (verified, see conversation/commit history for
exact methodology):
- 100 concurrent users + 23 devices, sustained: **0 failed requests**,
  widget API latency avg ~1.35s (min ~175ms, max ~2.7s), DB pool barely
  used (1-2 of 40 connections), DB query latency <1ms.
- Telemetry ingest: ~342 rows/min at normal `live-telemetry.sh` cadence;
  stress-tested (via `ingest-stress-test.sh`) to 1095 calls/min with 0
  failures using `WORKERS_PER_DEVICE=2`.
- Running the 100-user soak test and an ingest stress test at the same
  time visibly slows the dashboard down — that's two heavy synthetic
  loads stacked, not a bug. Don't run both together unless specifically
  testing combined worst-case load.

**Scripts reference** (all in this directory, all idempotent/safe to re-run
unless noted):

| Script | What it does |
|---|---|
| `create-test-users.sh` | Provisions the 100 `loadtest*@test.local` accounts (password `loadtest123`) |
| `create-extra-devices.sh` | Provisions `N` (default 20) generic sensor devices with realistic random telemetry (`N=5 ./create-extra-devices.sh` to add a different count) |
| `fix-sensor-ranges.sh` | Re-ingests realistic values for whatever keys an existing `LoadTest-Sensor-*` device already reports — doesn't add/remove keys, run after `create-extra-devices.sh` if values look stale |
| `create-rules.sh` | Creates Pump-01 vibration (ISO 10816-3, all 4 bearing points) + GlucIQ glucose threshold rules |
| `create-generic-rules.sh` | Creates/refreshes the 10 tenant-wide generic-key threshold rules (deletes old ones with the same alarm_type first, so re-running updates thresholds without duplicating) |
| `live-telemetry.sh` | Continuous ±2% nudge for every device, keeps the dashboard "alive" — see above |
| `soak-test.sh` | 100-user continuous concurrent read load — see above, start/stop deliberately |
| `ingest-stress-test.sh` | Short burst, max-speed ingest test (`DURATION_S`, `WORKERS_PER_DEVICE` env vars) — separate from `live-telemetry.sh`'s steady nudge rate |
| `check-performance.sh` | One-shot snapshot: system/DB/cache metrics, `soak-test.sh` log error/latency summary (overall + since last backend restart), backend error log tail, `docker stats` |
| `metrics-sampler.sh` + `analyze-soak.py` | Long-run (days/weeks) trend sampling via cron — see "Soak / endurance test" section below |
| `k6-script.js` | Short burst load via k6 — see "1. Install k6" section below |

Two kinds of test here, for two different questions:

- **`k6-script.js`** — short burst load (minutes): "does it handle N
  concurrent users right now?" Covers dashboard/widget reads, telemetry
  ingest, and TAAT chat. See the file's own usage comment.
- **`soak-test.sh` + `metrics-sampler.sh` + `analyze-soak.py`** — long
  continuous load (days/weeks): "does it stay healthy over time?" Catches
  slow leaks a 2-minute burst test can't show (e.g. the Redis pubsub
  connection not closed on websocket_manager reconnect, noted in the code
  review — that only shows up as memory climbing over days).

## Soak / endurance test (14-30 days)

1. Start the light continuous background load (survives SSH disconnect
   via `nohup` + `disown`; or set it up as a systemd service for
   reboot-survival — see the comment at the top of `soak-test.sh`):
   ```bash
   chmod +x loadtest/soak-test.sh loadtest/metrics-sampler.sh loadtest/analyze-soak.py
   nohup ./loadtest/soak-test.sh > loadtest/soak.log 2>&1 &
   disown
   ```

2. Schedule the metrics sampler via cron, every 10 minutes:
   ```bash
   crontab -e
   # add:
   */10 * * * * cd /mnt/apps/iot-platform && EMAIL=demo@triaxisai.com PASSWORD=demo1234 ./loadtest/metrics-sampler.sh >> loadtest/metrics-sampler.log 2>&1
   ```

3. Let it run. Check in occasionally with `tail -f loadtest/soak.log` or
   `wc -l loadtest/metrics.jsonl` to confirm it's still collecting.

4. After days/weeks, analyze the trend:
   ```bash
   python3 loadtest/analyze-soak.py
   ```
   A steadily rising `Process memory` or `DB pool checked_out` across
   the whole window (not just noise) is a leak — the thing this test
   exists to catch.

5. Stop it when done:
   ```bash
   pkill -f soak-test.sh
   crontab -e   # remove the metrics-sampler line
   ```

## 1. Install k6

```bash
# Linux (Debian/Ubuntu)
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```

## 2. Run against LOCAL first (cm5)

Start the backend locally, then from another terminal:

```bash
cd /mnt/apps/iot-platform

# Dashboard/widget reads — validates the Redis cache fix
k6 run -e BASE_URL=http://localhost:8000 \
       -e EMAIL=demo@triaxisai.com -e PASSWORD=demo1234 \
       -e SCENARIO=dashboard loadtest/k6-script.js

# Telemetry ingest — needs a real device token (Devices page → a device → copy token)
k6 run -e BASE_URL=http://localhost:8000 \
       -e DEVICE_TOKEN=<paste a device token> \
       -e SCENARIO=ingest loadtest/k6-script.js

# TAAT chat — keep VUs low, this calls an LLM per request
k6 run -e BASE_URL=http://localhost:8000 \
       -e EMAIL=demo@triaxisai.com -e PASSWORD=demo1234 \
       -e SCENARIO=taat_chat loadtest/k6-script.js
```

While each one runs, in a third terminal poll the admin metrics endpoint
to see what's actually happening under load:

```bash
watch -n2 'curl -s -H "Authorization: Bearer <admin access_token>" \
  http://localhost:8000/api/v1/system/metrics | python3 -m json.tool'
```

Things to look at in that output, specifically to confirm the fixes:
- `cache.status` should say `"connected"` and (with the dashboard scenario
  running) Redis hit/miss numbers should move — if you also have access to
  `redis-cli info stats`, `keyspace_hits` should climb. Before the fix this
  never moved no matter the load.
- `database.checked_out` / `database.latency_ms` — should stay flat and low
  during the dashboard scenario once the cache is actually absorbing reads.
  A climbing `checked_out` close to `pool_size` under light load is a sign
  the cache isn't helping.

## 3. Only then, staging/production

- Start with far fewer VUs than the defaults in the script (edit the
  `stages`/`rate` in `k6-script.js`, or override via env where the
  scenario supports it) — e.g. 5 VUs, not 20.
- Run it for a short window (1-2 min) first and watch `/system/metrics`
  live the whole time, the same way as above.
- Never run the `ingest` scenario against a device token for a device
  that's also live in the field — telemetry from the test will land in
  its real history.
- Stop immediately if `http_req_failed` climbs or `database.latency_ms`
  spikes — that's the DB free-tier limit, not a code bug, and more load
  won't tell you anything new once that happens.

## What "good" looks like

- `http_req_duration p(95)` stays under ~2s for dashboard/chat scenarios.
- `http_req_failed` stays under 5% (some 429s from rate limiting during
  the ingest scenario are expected and fine — that's the rate limiter
  doing its job, not a failure).
- Redis `keyspace_hits` actually increases during the dashboard scenario
  (this is the main thing worth checking given the cache fix just landed).
