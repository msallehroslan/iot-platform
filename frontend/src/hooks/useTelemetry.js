/**
 * hooks/useTelemetry.js
 *
 * React hook that provides real-time telemetry for a single device.
 *
 * Responsibilities:
 *   - Subscribes to TelemetrySocket for the given deviceId
 *   - Maintains `liveValues`  — flat { key: latestValue } map
 *   - Maintains `history`     — { key: [{ts, value}, ...] } (last 50 per key)
 *   - Exposes `connected` + `usingFallback` status flags for the UI indicator
 *   - Cleans up the subscription on unmount
 *
 * Performance:
 *   - Uses useRef for history to avoid re-creating the object on every update
 *   - liveValues is spread-updated with only changed keys — React bails out
 *     of re-rendering child components that don't use the changed key
 *   - Widgets receive only the slice of state they subscribe to
 *
 * Usage:
 *   function MyWidget({ deviceId, telemetryKey }) {
 *     const { liveValues, history, connected } = useTelemetry(deviceId, [telemetryKey]);
 *     const value   = liveValues[telemetryKey];
 *     const history = history[telemetryKey] || [];
 *   }
 *
 *   // No key filter — receive everything:
 *   const { liveValues } = useTelemetry(deviceId, null);
 */

import { useState, useEffect, useRef } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { telemetryApi } from "../services/api.js";

const MAX_HISTORY = 50;

/**
 * @param {string|null}   deviceId  - device UUID; pass null to disable
 * @param {string[]|null} keys      - keys to subscribe to (null = all)
 */
export function useTelemetry(deviceId, keys = null) {
  const [liveValues, setLiveValues] = useState({});
  const [connected,  setConnected]  = useState(false);
  const [fallback,   setFallback]   = useState(false);

  // History is a mutable ref so appending doesn't trigger a render.
  // Components that need history should access historyRef.current directly,
  // or we expose a stable historyData state updated on each WS message.
  const [historyData, setHistoryData] = useState({});  // { key: [{ts, value}] }
  const historyRef = useRef({});

  // ── Initial REST fetch to populate state before first WS message ─────────
  useEffect(() => {
    if (!deviceId) return;

    // Load initial latest values
    telemetryApi.latest(deviceId).then(rows => {
      if (!rows?.length) return;
      const values = {};
      rows.forEach(r => { values[r.key] = r.value; });
      setLiveValues(values);
    }).catch(() => {});

    // Load initial history for subscribed keys
    const keysToLoad = keys || [];
    if (!keysToLoad.length) {
      // No specific keys: fetch key list first, then histories
      telemetryApi.keys(deviceId)
        .then(res => (res?.keys || []))
        .then(allKeys => {
          allKeys.forEach(k => {
            telemetryApi.history(deviceId, k, MAX_HISTORY).then(pts => {
              if (!pts?.length) return;
              historyRef.current = { ...historyRef.current, [k]: pts };
              setHistoryData(h => ({ ...h, [k]: pts }));
            }).catch(() => {});
          });
        })
        .catch(() => {});
    } else {
      keysToLoad.forEach(k => {
        telemetryApi.history(deviceId, k, MAX_HISTORY).then(pts => {
          if (!pts?.length) return;
          historyRef.current = { ...historyRef.current, [k]: pts };
          setHistoryData(h => ({ ...h, [k]: pts }));
        }).catch(() => {});
      });
    }
  }, [deviceId]);   // only on deviceId change, not key changes

  // ── WebSocket subscription ────────────────────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    const unsub = TelemetrySocket.subscribe(deviceId, keys, (values, ts) => {
      // 1. Update live values (only changed keys — React will shallow-compare)
      setLiveValues(prev => ({ ...prev, ...values }));

      // 2. Append to history for each received key
      const updatedHistory = { ...historyRef.current };
      let changed = false;
      Object.entries(values).forEach(([k, v]) => {
        const arr = [...(updatedHistory[k] || [])];
        arr.push({ ts, value: v });
        if (arr.length > MAX_HISTORY) arr.shift();
        updatedHistory[k] = arr;
        changed = true;
      });
      if (changed) {
        historyRef.current = updatedHistory;
        setHistoryData(updatedHistory);
      }

      // 3. Update connection status
      const status = TelemetrySocket.getStatus(deviceId);
      setConnected(status.connected);
      setFallback(status.useFallback);
    });

    // Poll connection status every 2s for the indicator (WS doesn't fire
    // events for "still connected" state — only open/close)
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
    liveValues,    // { key: latestValue }
    historyData,   // { key: [{ts, value}] }
    connected,     // true if WebSocket is open
    usingFallback: fallback,  // true if falling back to REST polling
  };
}

/**
 * Lightweight hook for the Overview / TelCard components.
 * Subscribes to a single device, no key filtering needed.
 * Replaces the refreshKey-based polling pattern.
 *
 * @param {string} deviceId
 * @returns {{ values: Object, ts: string|null, connected: boolean }}
 */
export function useDeviceTelemetry(deviceId) {
  const [values,    setValues]    = useState({});
  const [ts,        setTs]        = useState(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!deviceId) return;

    // Seed with REST on mount
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
