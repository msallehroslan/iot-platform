/**
 * hooks/useTelemetry.js — NaN-SAFE PATCHED VERSION
 *
 * Changes from original:
 *   1. sanitizeTelem()   — strips non-finite values at WS callback entry
 *   2. sanitizePoint()   — drops invalid points before ring buffer push
 *   3. sanitizeHistMap() — sanitizes bulkHistory REST response before setState
 *   4. useTelemSlice     — sanitizes seed and WS points
 *
 * All other behavior (flush cadence, reconnect, fallback polling, MAX_HISTORY) is UNCHANGED.
 */

import { useState, useEffect, useRef } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { telemetryApi, getApiToken } from "../services/api.js";

const MAX_HISTORY       = 50;
const FLUSH_INTERVAL_MS = 250;

// ── Sanitization helpers ──────────────────────────────────────────────────────

function sanitizeTelem(values) {
  if (!values || typeof values !== "object") return {};
  const out = {};
  for (const [k, v] of Object.entries(values)) {
    if (v === null || v === undefined) continue;
    if (typeof v === "number") {
      if (Number.isFinite(v)) out[k] = v;
      // else drop: NaN, Infinity
    } else if (typeof v === "string" && v !== "") {
      const n = parseFloat(v);
      // If it's a numeric string, coerce to number; otherwise keep as string
      if (Number.isFinite(n)) out[k] = n;
      else out[k] = v;
    } else if (typeof v === "boolean") {
      out[k] = v;
    }
  }
  return out;
}

/**
 * Validate a single history point.
 * Returns the point if valid, null if invalid.
 */
function sanitizePoint(ts, value) {
  if (!ts) return null;
  if (typeof value === "number" && !Number.isFinite(value)) return null;
  if (value === null || value === undefined) return null;
  return { ts, value };
}

/**
 * Sanitize a bulkHistory response { key: [{ts, value}] }.
 */
function sanitizeHistMap(histMap) {
  if (!histMap || typeof histMap !== "object") return {};
  const out = {};
  for (const [k, pts] of Object.entries(histMap)) {
    if (!Array.isArray(pts)) continue;
    const clean = pts
      .filter(p => p && p.ts)
      .map(p => sanitizePoint(p.ts, typeof p.value === "number" ? p.value : parseFloat(p.value)))
      .filter(Boolean);
    if (clean.length > 0) out[k] = clean;
  }
  return out;
}

// ── useTelemetry ──────────────────────────────────────────────────────────────

export function useTelemetry(deviceId, keys = null) {
  const [liveValues,  setLiveValues]  = useState({});
  const [historyData, setHistoryData] = useState({});
  const [connected,   setConnected]   = useState(false);
  const [fallback,    setFallback]    = useState(false);

  const historyRef        = useRef({});
  const pendingValuesRef  = useRef(null);
  const pendingHistoryRef = useRef(null);
  const pendingTsRef      = useRef("");

  // ── Initial REST seed ────────────────────────────────────────────────────
  useEffect(() => {
    if (!deviceId) return;
    let cancelled = false;

    async function seed() {
      try {
        const latestRows = await telemetryApi.latest(deviceId);
        if (cancelled) return;
        if (latestRows?.length) {
          const values = {};
          latestRows.forEach(r => {
            const n = typeof r.value === "number" ? r.value : parseFloat(r.value);
            // Accept finite numbers + non-numeric strings (status fields)
            if (Number.isFinite(n)) values[r.key] = n;
            else if (typeof r.value === "string" && r.value !== "") values[r.key] = r.value;
            else if (typeof r.value === "boolean") values[r.key] = r.value;
          });
          if (Object.keys(values).length) setLiveValues(values);
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

        // SANITIZE bulk history before storing
        const histMap = sanitizeHistMap(bulkResult?.data || {});
        historyRef.current = histMap;
        setHistoryData(histMap);

      } catch (_) {}
    }

    seed();
    return () => { cancelled = true; };
  }, [deviceId]);

  // ── WebSocket subscription + buffered flush ──────────────────────────────
  useEffect(() => {
    if (!deviceId) return;

    const unsub = TelemetrySocket.subscribe(deviceId, keys, (values, ts) => {
      // SANITIZE at WS ingress
      const cleanValues = sanitizeTelem(values);
      if (Object.keys(cleanValues).length === 0) return;

      if (pendingValuesRef.current === null) {
        pendingValuesRef.current = { ...cleanValues };
      } else {
        Object.assign(pendingValuesRef.current, cleanValues);
      }

      if (pendingHistoryRef.current === null) {
        pendingHistoryRef.current = {};
      }
      const pending = pendingHistoryRef.current;
      Object.entries(cleanValues).forEach(([k, v]) => {
        // Only numeric values go into history
        if (typeof v === "number" && Number.isFinite(v)) {
          if (!pending[k]) pending[k] = [];
          pending[k].push({ ts, value: v });
        }
      });

      if (!pendingTsRef.current || (ts && ts > pendingTsRef.current)) {
        pendingTsRef.current = ts;
      }
    });

    const flushTimer = setInterval(() => {
      const pendingValues  = pendingValuesRef.current;
      const pendingHistory = pendingHistoryRef.current;

      if (!pendingValues && !pendingHistory) return;

      pendingValuesRef.current  = null;
      pendingHistoryRef.current = null;

      if (pendingValues) {
        setLiveValues(prev => ({ ...prev, ...pendingValues }));
      }

      if (pendingHistory) {
        const current = historyRef.current;
        const updated = { ...current };
        let changed = false;

        Object.entries(pendingHistory).forEach(([k, newPoints]) => {
          const arr = [...(updated[k] || [])];
          for (const pt of newPoints) {
            // Validate each point before push (belt-and-suspenders)
            const clean = sanitizePoint(pt.ts, pt.value);
            if (!clean) continue;
            arr.push(clean);
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

    const statusInterval = setInterval(() => {
      const s = TelemetrySocket.getStatus(deviceId);
      setConnected(s.connected);
      setFallback(s.useFallback);
    }, 2000);

    return () => {
      unsub();
      clearInterval(flushTimer);
      clearInterval(statusInterval);
      pendingValuesRef.current  = null;
      pendingHistoryRef.current = null;
      pendingTsRef.current      = "";
    };
  }, [deviceId, JSON.stringify(keys)]);

  return { liveValues, historyData, connected, usingFallback: fallback };
}

// ── useDeviceTelemetry (lightweight, no history) ──────────────────────────────

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
        const n = typeof r.value === "number" ? r.value : parseFloat(r.value);
        if (Number.isFinite(n)) v[r.key] = n;
        else if (typeof r.value === "string" && r.value !== "") v[r.key] = r.value;
        if (!latestTs || r.ts > latestTs) latestTs = r.ts;
      });
      setValues(v);
      setTs(latestTs);
    }).catch(() => {});

    const unsub = TelemetrySocket.subscribe(deviceId, null, (newValues, newTs) => {
      const clean = sanitizeTelem(newValues);
      if (Object.keys(clean).length === 0) return;

      if (pendingValuesRef.current === null) {
        pendingValuesRef.current = { ...clean };
      } else {
        Object.assign(pendingValuesRef.current, clean);
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

// ── useTelemSlice (per-widget, single key) ────────────────────────────────────

export function useTelemSlice(deviceId, key) {
  const [value,   setValue]   = useState(null);
  const [history, setHistory] = useState([]);
  const [ts,      setTs]      = useState(null);

  const pendingRef  = useRef(null);
  const historyRef  = useRef([]);

  useEffect(() => {
    if (!deviceId || !key) return;

    import("../services/api.js").then(({ telemetryApi }) => {
      telemetryApi.latest(deviceId).then(rows => {
        const row = rows?.find(r => r.key === key);
        if (row) {
          const n = typeof row.value === "number" ? row.value : parseFloat(row.value);
          if (Number.isFinite(n)) { setValue(n); setTs(row.ts); }
        }
      }).catch(() => {});

      telemetryApi.bulkHistory(deviceId, [key], MAX_HISTORY).then(res => {
        const raw = res?.data?.[key] || [];
        const pts = raw
          .filter(p => p && p.ts)
          .map(p => {
            const n = typeof p.value === "number" ? p.value : parseFloat(p.value);
            return Number.isFinite(n) ? { ts: p.ts, value: n } : null;
          })
          .filter(Boolean);
        historyRef.current = pts;
        setHistory(pts);
      }).catch(() => {});
    });

    const unsub = TelemetrySocket.subscribe(deviceId, [key], (values, newTs) => {
      if (key in values) {
        const raw = values[key];
        const n   = typeof raw === "number" ? raw : parseFloat(raw);
        if (Number.isFinite(n)) {
          pendingRef.current = { value: n, ts: newTs };
        }
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

// ── useDashboardPreload ───────────────────────────────────────────────────────
// Unchanged — preload data is sanitized in useDashboardRuntime before use.

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
      const token = getApiToken() || "";
      fetch(`${API_BASE}/user-dashboards/${dashboardId}/preload`, {
        credentials: "include",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(data => {
          if (!cancelled) { setPreload(data); setLoading(false); }
        })
        .catch(err => {
          if (!cancelled) { setError(err); setLoading(false); }
        });
    });

    return () => { cancelled = true; };
  }, [dashboardId]);

  return { preload, loading, error };
}
