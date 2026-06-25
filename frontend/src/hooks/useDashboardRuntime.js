/**
 * hooks/useDashboardRuntime.js  — NaN-SAFE PATCHED VERSION
 *
 * Changes from original:
 *   1. sanitizeTelem()   — strips non-finite values from all incoming telemetry
 *   2. sanitizeHistory() — drops invalid points from history arrays before insertion
 *   3. Ring buffer capped at MAX_HISTORY (unchanged = 60, but now enforced in preload too)
 *   4. Preload history points sanitized before setState
 *   5. WS pending buffer sanitized before flush
 *
 * All other behavior (flush cadence, WS subscription, intelligence refresh) is UNCHANGED.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { alarmApi, telemetryApi, getApiToken } from "../services/api.js";

const FLUSH_INTERVAL_MS = 250;
const MAX_HISTORY       = 60;

// ── Sanitization primitives ───────────────────────────────────────────────────

/**
 * Sanitize a flat telemetry values object.
 * Drops keys whose values are non-finite (NaN, Infinity, null, undefined, "").
 * Returns a new object — original is never mutated.
 */
function sanitizeTelem(values) {
  if (!values || typeof values !== "object") return {};
  const out = {};
  for (const [k, v] of Object.entries(values)) {
    if (v === null || v === undefined) continue;
    if (typeof v === "number") {
      if (Number.isFinite(v)) out[k] = v;
    } else if (typeof v === "string" && v !== "") {
      const n = parseFloat(v);
      if (Number.isFinite(n)) out[k] = n; // coerce string numbers
      else out[k] = v;                     // keep non-numeric strings (e.g. status)
    } else if (typeof v === "boolean") {
      out[k] = v;
    } else if (typeof v === "number" && !Number.isFinite(v)) {
      // Skip NaN/Infinity
    } else {
      out[k] = v;
    }
  }
  return out;
}

/**
 * Sanitize a history array [{ts, value}].
 * Drops points with non-finite numeric values or missing ts.
 * Preserves non-numeric string values (e.g. status fields).
 * Caps to MAX_HISTORY (most recent).
 */
function sanitizeHistory(pts) {
  if (!pts || !Array.isArray(pts)) return [];
  const out = [];
  for (const p of pts) {
    if (!p || !p.ts) continue;
    const v = p.value;
    if (typeof v === "number" && !Number.isFinite(v)) continue; // drop NaN/Infinity
    if (v === null || v === undefined) continue;
    out.push({ ts: p.ts, value: v });
  }
  // Return most recent MAX_HISTORY points
  return out.length > MAX_HISTORY ? out.slice(out.length - MAX_HISTORY) : out;
}

/**
 * Sanitize a full device history map { key: [{ts, value}] }.
 */
function sanitizeDeviceHistory(histMap) {
  if (!histMap || typeof histMap !== "object") return {};
  const out = {};
  for (const [k, pts] of Object.entries(histMap)) {
    const clean = sanitizeHistory(pts);
    if (clean.length > 0) out[k] = clean;
  }
  return out;
}

// ── Main hook ─────────────────────────────────────────────────────────────────

export function useDashboardRuntime(activeDash, user) {
  const [liveTelem,   setLiveTelem]   = useState({});
  const [historyData, setHistoryData] = useState({});
  const [alarmsData,  setAlarmsData]  = useState({});
  const [intellData,  setIntellData]  = useState({});
  const [wsConnected, setWsConnected] = useState({});
  const [preloadDone, setPreloadDone] = useState(false);
  const [preloadError,setPreloadError]= useState(null);

  const pendingTelemRef   = useRef({});
  const pendingHistoryRef = useRef({});
  const preloadedDashRef  = useRef(null);
  // Remember last known non-empty telem per device
  // Prevents value resetting to 0 on WS reconnect or re-render
  const lastTelemRef = useRef({});

  const widgetDeviceIds = activeDash?.widgets
    ? [...new Set(
        activeDash.widgets
          .map(w => w.config?.device_id)
          .filter(Boolean)
      )]
    : [];

  // ── PRELOAD ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!activeDash?.id) return;
    if (preloadedDashRef.current === activeDash.id) return;

    preloadedDashRef.current = activeDash.id;
    setPreloadDone(false);
    setPreloadError(null);

    const run = async () => {
      const token = getApiToken() || "";
      let baseUrl = "/api/v1";
      try {
        const { API_BASE } = await import("../services/api.js");
        baseUrl = API_BASE;
      } catch (_) {}

      const preloadUrl = `${baseUrl.replace("/api/v1", "")}/api/v1/user-dashboards/${activeDash.id}/preload`;

      try {
        const r = await fetch(preloadUrl, {
          credentials: "include",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!r.ok) throw new Error(`preload ${r.status}`);
        const data = await r.json();
        const devices = data.devices || {};

        const newTelem   = {};
        const newHistory = {};
        const newAlarms  = {};
        const newIntell  = {};

        Object.entries(devices).forEach(([devId, devData]) => {
          // SANITIZE telemetry before storing
          if (devData.telemetry && Object.keys(devData.telemetry).length > 0) {
            const cleaned = sanitizeTelem(devData.telemetry);
            if (Object.keys(cleaned).length > 0) newTelem[devId] = cleaned;
          }
          // SANITIZE history before storing
          if (devData.history && Object.keys(devData.history).length > 0) {
            const cleaned = sanitizeDeviceHistory(devData.history);
            if (Object.keys(cleaned).length > 0) newHistory[devId] = cleaned;
          }
          if (devData.alarms)
            newAlarms[devId] = devData.alarms;
          if (devData.intelligence)
            newIntell[devId] = devData.intelligence;
        });

        // Update lastTelemRef before setting state
        Object.entries(newTelem).forEach(([devId, vals]) => {
          lastTelemRef.current[devId] = {
            ...(lastTelemRef.current[devId] || {}),
            ...vals,
          };
        });
        setLiveTelem(prev   => ({ ...prev, ...newTelem   }));
        setHistoryData(prev => ({ ...prev, ...newHistory }));
        setAlarmsData(prev  => ({ ...prev, ...newAlarms  }));
        setIntellData(prev  => ({ ...prev, ...newIntell  }));
        setPreloadDone(true);

      } catch (err) {
        setPreloadError(String(err));
        await _fallbackFetch(
          widgetDeviceIds,
          setLiveTelem, setHistoryData, setAlarmsData
        );
        setPreloadDone(true);
      }
    };

    run();
  }, [activeDash?.id]);

  // Reset preload tracking on dashboard change
  useEffect(() => {
    if (!activeDash?.id) return;
    return () => {
      if (preloadedDashRef.current === activeDash.id) {
        preloadedDashRef.current = null;
      }
    };
  }, [activeDash?.id]);

  // ── WebSocket subscriptions ────────────────────────────────────────────────
  useEffect(() => {
    if (!widgetDeviceIds.length) return;

    const unsubs = widgetDeviceIds.map(deviceId => {
      return TelemetrySocket.subscribe(deviceId, null, (values, ts) => {
        // SANITIZE at WS ingress — before writing to ref buffers
        const cleanValues = sanitizeTelem(values);
        if (Object.keys(cleanValues).length === 0) return; // nothing valid

        if (!pendingTelemRef.current[deviceId]) {
          pendingTelemRef.current[deviceId] = {};
        }
        Object.assign(pendingTelemRef.current[deviceId], cleanValues);

        if (!pendingHistoryRef.current[deviceId]) {
          pendingHistoryRef.current[deviceId] = {};
        }
        const devHist = pendingHistoryRef.current[deviceId];
        const safeTsStr = ts || new Date().toISOString();

        Object.entries(cleanValues).forEach(([k, v]) => {
          // Only push numeric telemetry into history
          if (typeof v === "number" && Number.isFinite(v)) {
            if (!devHist[k]) devHist[k] = [];
            devHist[k].push({ ts: safeTsStr, value: v });
          }
        });
      });
    });

    const flushTimer = setInterval(() => {
      const pt = pendingTelemRef.current;
      const ph = pendingHistoryRef.current;
      const hasTelem   = Object.keys(pt).length > 0;
      const hasHistory = Object.keys(ph).length > 0;
      if (!hasTelem && !hasHistory) return;

      pendingTelemRef.current   = {};
      pendingHistoryRef.current = {};

      if (hasTelem) {
        // Update lastTelemRef with latest non-zero values
        Object.entries(pt).forEach(([devId, vals]) => {
          const filtered = {};
          Object.entries(vals).forEach(([k, v]) => {
            // Only remember non-zero/non-null values
            if (v !== null && v !== undefined && v !== 0) {
              filtered[k] = v;
            } else if (v === 0 && lastTelemRef.current[devId]?.[k] === undefined) {
              // Accept 0 only if we've never seen this key before
              filtered[k] = v;
            }
          });
          if (Object.keys(filtered).length) {
            lastTelemRef.current[devId] = {
              ...(lastTelemRef.current[devId] || {}),
              ...filtered,
            };
          }
        });
        setLiveTelem(prev => {
          const next = { ...prev };
          Object.entries(pt).forEach(([devId, vals]) => {
            // Merge with last known values — never lose a value on reconnect
            next[devId] = {
              ...(lastTelemRef.current[devId] || {}),
              ...(prev[devId] || {}),
              ...vals,
            };
          });
          return next;
        });
      }

      if (hasHistory) {
        setHistoryData(prev => {
          const next = { ...prev };
          Object.entries(ph).forEach(([devId, keyMap]) => {
            const devHist = { ...(prev[devId] || {}) };
            Object.entries(keyMap).forEach(([k, newPts]) => {
              const arr = [...(devHist[k] || [])];
              for (const pt of newPts) {
                // Double-check sanitization before appending to ring buffer
                if (!pt.ts || typeof pt.value !== "number" || !Number.isFinite(pt.value)) continue;
                arr.push(pt);
                if (arr.length > MAX_HISTORY) arr.shift();
              }
              devHist[k] = arr;
            });
            next[devId] = devHist;
          });
          return next;
        });
      }
    }, FLUSH_INTERVAL_MS);

    const statusTimer = setInterval(() => {
      setWsConnected(prev => {
        const next = { ...prev };
        let changed = false;
        widgetDeviceIds.forEach(devId => {
          const c = TelemetrySocket.getStatus(devId).connected;
          if (next[devId] !== c) { next[devId] = c; changed = true; }
        });
        return changed ? next : prev;
      });
    }, 2000);

    return () => {
      unsubs.forEach(fn => fn());
      clearInterval(flushTimer);
      clearInterval(statusTimer);
      pendingTelemRef.current   = {};
      pendingHistoryRef.current = {};
    };
  }, [widgetDeviceIds.join(",")]);

  // ── Intelligence refresh (immediate on load + 60s cadence) ─────────────────
  // BUG-13 fix: Without this, health_score / anomaly_score / taat_insight widgets
  // show blank for up to 60s on first load if preload returned no intelligence.
  // Fix: fire refresh() immediately when preloadDone becomes true, then repeat every 60s.
  const intellTimerRef = useRef(null);
  useEffect(() => {
    if (!widgetDeviceIds.length || !preloadDone) return;

    const refresh = async () => {
      const token = getApiToken() || "";
      let base = "/api/v1";
      try {
        const { API_BASE } = await import("../services/api.js");
        base = API_BASE;
      } catch (_) {}

      const updates = {};
      await Promise.allSettled(
        widgetDeviceIds.map(async devId => {
          try {
            const r = await fetch(`${base}/intelligence/unified/${devId}`, {
              credentials: "include",
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            if (r.ok) updates[devId] = await r.json();
          } catch (_) {}
        })
      );
      if (Object.keys(updates).length) {
        setIntellData(prev => ({ ...prev, ...updates }));
      }
    };

    // BUG-13: Fire immediately on load so intelligence widgets don't wait 60s.
    // Check if any device is missing intelligence data before fetching.
    const missingIntel = widgetDeviceIds.some(id => !intellData[id]);
    if (missingIntel) {
      refresh();
    }

    // Then refresh every 60s to keep intelligence current
    intellTimerRef.current = setInterval(refresh, 60_000);
    return () => clearInterval(intellTimerRef.current);
  }, [widgetDeviceIds.join(","), preloadDone]);

  return {
    liveTelem,
    historyData,
    alarmsData,
    intellData,
    wsConnected,
    preloadDone,
    preloadError,
  };
}

// ── Fallback REST fetch (unchanged) ──────────────────────────────────────────
async function _fallbackFetch(deviceIds, setLiveTelem, setHistoryData, setAlarmsData) {
  await Promise.allSettled(deviceIds.map(async deviceId => {
    try {
      const rows = await telemetryApi.latest(deviceId);
      if (rows?.length) {
        const values = {};
        rows.forEach(r => {
          // Sanitize here too
          const n = typeof r.value === "number" ? r.value : parseFloat(r.value);
          if (Number.isFinite(n)) values[r.key] = n;
          else if (typeof r.value === "string" && r.value !== "") values[r.key] = r.value;
        });
        if (Object.keys(values).length) {
          setLiveTelem(prev => ({
            ...prev,
            [deviceId]: {
              ...(prev[deviceId] || {}),
              ...values,
            }
          }));
        }
      }
    } catch (_) {}

    try {
      const rows = await alarmApi.list({ device_id: deviceId, limit: 50 });
      setAlarmsData(prev => ({ ...prev, [deviceId]: rows || [] }));
    } catch (_) {}
  }));
}