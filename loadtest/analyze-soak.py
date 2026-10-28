#!/usr/bin/env python3
"""
analyze-soak.py — summarize loadtest/metrics.jsonl produced by
metrics-sampler.sh over a 14-30 day soak test.

Usage: python3 loadtest/analyze-soak.py [path/to/metrics.jsonl]

Prints first/last/min/max/trend for the fields worth watching over a
long run: process memory, DB latency, DB pool checkout, cache status.
A steadily rising mem_mb or checked_out across the whole window is the
signature of a leak that a short burst test can't surface.
"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "loadtest/metrics.jsonl"

rows = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

rows = [r for r in rows if "error" not in r]
if not rows:
    print("No valid samples found.")
    sys.exit(1)

print(f"{len(rows)} samples, {rows[0]['_sampled_at']} → {rows[-1]['_sampled_at']}\n")


def series(path_keys):
    out = []
    for r in rows:
        v = r
        for k in path_keys:
            v = v.get(k) if isinstance(v, dict) else None
            if v is None:
                break
        if isinstance(v, (int, float)):
            out.append(v)
    return out


def report(name, path_keys, unit=""):
    s = series(path_keys)
    if not s:
        print(f"{name}: no data")
        return
    first, last = s[0], s[-1]
    delta = last - first
    pct = (delta / first * 100) if first else 0
    flag = "  <-- rising, check for a leak" if pct > 20 and delta > 0 else ""
    print(f"{name}: first={first}{unit} last={last}{unit} min={min(s)}{unit} max={max(s)}{unit} "
          f"delta={delta:+.1f}{unit} ({pct:+.1f}%){flag}")


report("Process memory",   ["process", "mem_mb"], " MB")
report("DB latency",       ["database", "latency_ms"], " ms")
report("DB pool checked_out", ["database", "checked_out"])
report("WS clients",       ["websocket", "total_clients"])

cache_statuses = {r.get("cache", {}).get("status") for r in rows if isinstance(r.get("cache"), dict)}
print(f"\nCache statuses seen: {cache_statuses}")
redis_statuses = {r.get("redis") for r in rows}
print(f"Redis statuses seen: {redis_statuses}")

errors = sum(1 for r in rows if "error" in r)
print(f"\nFailed samples (login/fetch errors): {errors}")
