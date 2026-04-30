/**
 * hooks/useTelemetry.js
 *
 * PHASE 2 FIX: Replace N+1 serial history fetches with single bulk request.
 *
 * Before: 10 keys → GET /keys + 10× GET /history = 11 requests
 * After:  10 keys → GET /keys + POST /history/bulk = 2 requests
 *
 * Responsibilities:
 *   - Seed liveValues from GET /latest (1 request)
 *   - Seed historyData from POST /history/bulk (1 request for all keys)
 *   - Subscribe to WebSocket for live updates
 *   - Append incoming WS values to history ring buffer (MAX_HISTORY points)
 *   - Expose connected + usingFallback status
 */

import { useState, useEffect, useRef } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { telemetryApi } from "../services/api.js";

const MAX_HISTORY = 50;

/**
 * @param {string|null}   deviceId - device UUID; pass null to disable
 * @param {string[]|null} keys     - keys to watch (null = all keys for this device)
 */
export function useTelemetry(deviceId, keys = null) {
  const [liveValues,  setLiveValues]  = useState({});
  const [historyData, setHistoryData] = useState({});
  const [connected,   setConnected]   = useState(false);
  const [fallback,    setFallback]    = useState(false);

  const historyRef = useRef({});

  // ── Initial REST seed ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    let cancelled = false;

    async function seed() {
      try {
        // 1 request: latest values for all keys
        const latestRows = await telemetryApi.latest(deviceId);
        if (cancelled) return;
        if (latestRows?.length) {
          const values = {};
          latestRows.forEach(r => { values[r.key] = r.value; });
          setLiveValues(values);
        }

        // Determine which keys to fetch history for
        let keysToLoad = keys && keys.length > 0 ? keys : null;
        if (!keysToLoad) {
          // No specific keys requested — fetch all known keys for this device
          const res = await telemetryApi.keys(deviceId);
          if (cancelled) return;
          keysToLoad = res?.keys || [];
        }

        if (!keysToLoad.length) return;

        // 1 request: bulk history for all keys (replaces N serial requests)
        const bulkResult = await telemetryApi.bulkHistory(deviceId, keysToLoad, MAX_HISTORY);
        if (cancelled) return;

        // bulkResult.data = { key: [{ts, value}, ...], ... }
        const histMap = bulkResult?.data || {};
        historyRef.current = histMap;
        setHistoryData(histMap);

      } catch (err) {
        // Non-fatal — WS will populate live data anyway
      }
    }

    seed();

    return () => { cancelled = true; };
  }, [deviceId]); // re-seed only when deviceId changes


  // ── WebSocket subscription ─────────────────────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    const unsub = TelemetrySocket.subscribe(deviceId, keys, (values, ts) => {
      // Update live values
      setLiveValues(prev => ({ ...prev, ...values }));

      // Append to history ring buffer for each received key
      const updated = { ...historyRef.current };
      let changed = false;
      Object.entries(values).forEach(([k, v]) => {
        const arr = [...(updated[k] || [])];
        arr.push({ ts, value: v });
        if (arr.length > MAX_HISTORY) arr.shift();
        updated[k] = arr;
        changed = true;
      });
      if (changed) {
        historyRef.current = updated;
        setHistoryData(updated);
      }

      // Update connection status
      const status = TelemetrySocket.getStatus(deviceId);
      setConnected(status.connected);
      setFallback(status.useFallback);
    });

    const statusInterval = setInterval(() => {
      const s = TelemetrySocket.getStatus(deviceId);
      setConnected(s.connected);
      setFallback(s.useFallback);
    }, 2000);

    return () => {
      unsub();
      clearInterval(statusInterval);
    };
  }, [deviceId, JSON.stringify(keys)]);

  return {
    liveValues,              // { key: latestValue }
    historyData,             // { key: [{ts, value}, ...] }
    connected,               // true if WebSocket is open
    usingFallback: fallback, // true if falling back to REST polling
  };
}


/**
 * Lightweight hook for Overview / device cards.
 * Single device, no history needed.
 */
export function useDeviceTelemetry(deviceId) {
  const [values,    setValues]    = useState({});
  const [ts,        setTs]        = useState(null);
  const [connected, setConnected] = useState(false);

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
      setValues(prev => ({ ...prev, ...newValues }));
      setTs(newTs);
      setConnected(TelemetrySocket.getStatus(deviceId).connected);
    });

    const statusInterval = setInterval(() => {
      setConnected(TelemetrySocket.getStatus(deviceId).connected);
    }, 2000);

    return () => { unsub(); clearInterval(statusInterval); };
  }, [deviceId]);

  return { values, ts, connected };
}
