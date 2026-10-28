# Load testing — IoT Platform

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
