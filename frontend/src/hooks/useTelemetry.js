/**
 * hooks/useTelemetry.js
 *
 * OPT 2 — Buffered frontend telemetry flushing.
 *
 * Previous: every WS callback fired setLiveValues() + setHistoryData()
 * directly, causing a React render storm: N keys × M widgets per message.
 *
 * New: WS callback writes ONLY to refs (pendingValuesRef, pendingHistoryRef,
 * pendingTsRef). A setInterval flushes to React state every FLUSH_INTERVAL_MS.
 * This decouples message arrival rate from React render rate entirely.
 *
 * Render budget:
 *   - 1Hz ESP32 telemetry × 3 devices = 3 msgs/s → 3 renders/s (old)
 *   - After fix: 4 renders/s regardless of message rate (FLUSH_INTERVAL_MS=250)
 *   - Dashboard with 8 widgets: 8× fewer renders per second
 *
 * Preserved:
 *   - realtime behavior (250ms max lag — imperceptible)
 *   - connection indicators (polled separately)
 *   - fallback polling (still uses REST)
 *   - reconnect logic (unchanged in websocket.js)
 *   - MAX_HISTORY ring buffer (unchanged)
 *   - bulk history seeding on mount (unchanged)
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { telemetryApi } from "../services/api.js";

const MAX_HISTORY      = 50;
const FLUSH_INTERVAL_MS = 250;   // matches RealtimeCoordinator + websocket.js

/**
 * @param {string|null}   deviceId - device UUID; pass null to disable
 * @param {string[]|null} keys     - keys to watch (null = all keys for this device)
 */
export function useTelemetry(deviceId, keys = null) {
  const [liveValues,  setLiveValues]  = useState({});
  const [historyData, setHistoryData] = useState({});
  const [connected,   setConnected]   = useState(false);
  const [fallback,    setFallback]    = useState(false);

  // Stable refs — written by WS callback, read by flush interval
  const historyRef       = useRef({});
  const pendingValuesRef = useRef(null);   // null = no pending update
  const pendingHistoryRef = useRef(null);  // null = no pending update
  const pendingTsRef     = useRef("");

  // ── Initial REST seed ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    let cancelled = false;

    async function seed() {
      try {
        const latestRows = await telemetryApi.latest(deviceId);
        if (cancelled) return;
        if (latestRows?.length) {
          const values = {};
          latestRows.forEach(r => { values[r.key] = r.value; });
          setLiveValues(values);
        }

        let keysToLoad = keys && keys.length > 0 ? keys : null;
        if (!keysToLoad) {
          const res = await telemetryApi.keys(deviceId);
          if (cancelled) return;
          keysToLoad = res?.keys || [];
        }

        if (!keysToLoad.length) return;

        const bulkResult = await telemetryApi.bulkHistory(deviceId, keysToLoad, MAX_HISTORY);
        if (cancelled) return;

        const histMap = bulkResult?.data || {};
        historyRef.current = histMap;
        setHistoryData(histMap);
      } catch (_) {}
    }

    seed();
    return () => { cancelled = true; };
  }, [deviceId]);

  // ── WebSocket subscription + buffered flush ────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    // WS callback: accumulate into refs only — zero React state writes here
    const unsub = TelemetrySocket.subscribe(deviceId, keys, (values, ts) => {
      // Merge incoming values into pending buffer (last-write-wins per key)
      if (pendingValuesRef.current === null) {
        pendingValuesRef.current = { ...values };
      } else {
        Object.assign(pendingValuesRef.current, values);
      }

      // Build pending history updates — append to ring buffer per key
      if (pendingHistoryRef.current === null) {
        pendingHistoryRef.current = {};
      }
      const pending = pendingHistoryRef.current;
      Object.entries(values).forEach(([k, v]) => {
        if (!pending[k]) pending[k] = [];
        pending[k].push({ ts, value: v });
      });

      if (!pendingTsRef.current || ts > pendingTsRef.current) {
        pendingTsRef.current = ts;
      }
    });

    // Flush timer: drain pending refs into React state every FLUSH_INTERVAL_MS
    // This is the ONLY place setLiveValues / setHistoryData are called from the
    // WS path — batching all accumulated updates into a single React render.
    const flushTimer = setInterval(() => {
      const pendingValues  = pendingValuesRef.current;
      const pendingHistory = pendingHistoryRef.current;

      if (!pendingValues && !pendingHistory) return;

      // Swap refs atomically before doing any React work
      pendingValuesRef.current  = null;
      pendingHistoryRef.current = null;

      // Flush live values
      if (pendingValues) {
        setLiveValues(prev => ({ ...prev, ...pendingValues }));
      }

      // Flush history — apply ring buffer logic
      if (pendingHistory) {
        const current = historyRef.current;
        const updated = { ...current };
        let changed = false;

        Object.entries(pendingHistory).forEach(([k, newPoints]) => {
          const arr = [...(updated[k] || [])];
          for (const pt of newPoints) {
            arr.push(pt);
            if (arr.length > MAX_HISTORY) arr.shift();
          }
          updated[k] = arr;
          changed = true;
        });

        if (changed) {
          historyRef.current = updated;
          setHistoryData(updated);
        }
      }
    }, FLUSH_INTERVAL_MS);

    // Connection status polling — independent of data flush
    const statusInterval = setInterval(() => {
      const s = TelemetrySocket.getStatus(deviceId);
      setConnected(s.connected);
      setFallback(s.useFallback);
    }, 2000);

    return () => {
      unsub();
      clearInterval(flushTimer);
      clearInterval(statusInterval);
      // Clear pending buffers on cleanup to prevent stale data on remount
      pendingValuesRef.current  = null;
      pendingHistoryRef.current = null;
      pendingTsRef.current      = "";
    };
  }, [deviceId, JSON.stringify(keys)]);

  return {
    liveValues,
    historyData,
    connected,
    usingFallback: fallback,
  };
}


/**
 * Lightweight hook for Overview / device cards.
 * Single device, no history needed.
 * Also uses buffered flush to avoid per-message renders.
 */
export function useDeviceTelemetry(deviceId) {
  const [values,    setValues]    = useState({});
  const [ts,        setTs]        = useState(null);
  const [connected, setConnected] = useState(false);

  const pendingValuesRef = useRef(null);
  const pendingTsRef     = useRef(null);

  useEffect(() => {
    if (!deviceId) return;

    telemetryApi.latest(deviceId).then(rows => {
      if (!rows?.length) return;
      const v = {};
      let latestTs = null;
      rows.forEach(r => {
        v[r.key] = r.value;
        if (!latestTs || r.ts > latestTs) latestTs = r.ts;
      });
      setValues(v);
      setTs(latestTs);
    }).catch(() => {});

    const unsub = TelemetrySocket.subscribe(deviceId, null, (newValues, newTs) => {
      if (pendingValuesRef.current === null) {
        pendingValuesRef.current = { ...newValues };
      } else {
        Object.assign(pendingValuesRef.current, newValues);
      }
      if (!pendingTsRef.current || (newTs && newTs > pendingTsRef.current)) {
        pendingTsRef.current = newTs;
      }
    });

    const flushTimer = setInterval(() => {
      const pv = pendingValuesRef.current;
      const pt = pendingTsRef.current;
      if (!pv) return;
      pendingValuesRef.current = null;
      pendingTsRef.current     = null;
      setValues(prev => ({ ...prev, ...pv }));
      if (pt) setTs(pt);
    }, FLUSH_INTERVAL_MS);

    const statusInterval = setInterval(() => {
      setConnected(TelemetrySocket.getStatus(deviceId).connected);
    }, 2000);

    return () => {
      unsub();
      clearInterval(flushTimer);
      clearInterval(statusInterval);
      pendingValuesRef.current = null;
      pendingTsRef.current     = null;
    };
  }, [deviceId]);

  return { values, ts, connected };
}


// ── Priority 3: Per-widget telemetry slice hook ───────────────────────────────
// Subscribes to ONLY the keys a widget needs.
// Prevents global liveTelem reference changes from triggering unrelated renders.
//
// Usage in a widget:
//   const { value, history } = useTelemSlice(deviceId, "temperature");
//
// The widget receives a stable primitive (value) not a whole object slice.
// React.memo comparators can then do simple === checks.

export function useTelemSlice(deviceId, key) {
  const [value,   setValue]   = useState(null);
  const [history, setHistory] = useState([]);
  const [ts,      setTs]      = useState(null);

  const pendingRef    = useRef(null);
  const historyRef    = useRef([]);

  useEffect(() => {
    if (!deviceId || !key) return;

    // Seed from REST
    import("../services/api.js").then(({ telemetryApi }) => {
      telemetryApi.latest(deviceId).then(rows => {
        const row = rows?.find(r => r.key === key);
        if (row) { setValue(row.value); setTs(row.ts); }
      }).catch(() => {});

      telemetryApi.bulkHistory(deviceId, [key], MAX_HISTORY).then(res => {
        const pts = res?.data?.[key] || [];
        historyRef.current = pts;
        setHistory(pts);
      }).catch(() => {});
    });

    const unsub = TelemetrySocket.subscribe(deviceId, [key], (values, newTs) => {
      if (key in values) {
        pendingRef.current = { value: values[key], ts: newTs };
      }
    });

    const flushTimer = setInterval(() => {
      const p = pendingRef.current;
      if (!p) return;
      pendingRef.current = null;
      setValue(p.value);
      setTs(p.ts);
      const arr = [...historyRef.current, { ts: p.ts, value: p.value }];
      if (arr.length > MAX_HISTORY) arr.shift();
      historyRef.current = arr;
      setHistory(arr);
    }, FLUSH_INTERVAL_MS);

    return () => {
      unsub();
      clearInterval(flushTimer);
      pendingRef.current = null;
    };
  }, [deviceId, key]);

  return { value, history, ts };
}


// ── Priority 4: Dashboard preload hook ───────────────────────────────────────
// Fetches all dashboard data in a single preload request.
// Returns preloaded slices that widgets can consume without individual fetches.
//
// Usage in UserDashboardPage:
//   const { preload, loading } = useDashboardPreload(dashboardId);
//   // preload.devices[deviceId].telemetry
//   // preload.devices[deviceId].intelligence
//   // preload.devices[deviceId].alarms
//   // preload.devices[deviceId].history[key]

export function useDashboardPreload(dashboardId) {
  const [preload, setPreload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    if (!dashboardId) return;
    let cancelled = false;

    setLoading(true);
    setError(null);

    import("../services/api.js").then(({ API_BASE }) => {
      const token = localStorage.getItem("access_token") || "";
      fetch(`${API_BASE}/user-dashboards/${dashboardId}/preload`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(data => {
          if (!cancelled) {
            setPreload(data);
            setLoading(false);
          }
        })
        .catch(err => {
          if (!cancelled) {
            setError(err);
            setLoading(false);
          }
        });
    });

    return () => { cancelled = true; };
  }, [dashboardId]);

  return { preload, loading, error };
}
