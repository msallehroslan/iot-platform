/**
 * hooks/useDashboardRuntime.js
 *
 * Centralized Dashboard Runtime Store.
 *
 * This is the SINGLE owner of all dashboard data. Widgets are passive renderers.
 * They receive slices from this store and never fetch independently.
 *
 * Architecture:
 *
 *   Dashboard open
 *     → ONE preload request  (telemetry + history + alarms + intelligence)
 *     → hydrate runtime store
 *     → ONE WebSocket per device (coordinator-batched, 250ms flush)
 *     → widgets consume slices via useDashboardSlice()
 *     → WS updates mutate only changed keys → selective rerenders
 *
 * What widgets MUST NOT do:
 *   ✗ telemetryApi.latest()
 *   ✗ telemetryApi.bulkHistory()
 *   ✗ intelligenceApi.unified()
 *   ✗ TelemetrySocket.subscribe()
 *   ✗ widgetApi.* on mount (only on user interaction like window change)
 *
 * What widgets receive:
 *   liveTelem[key]         — latest value for a key
 *   historyData[key]       — array of {ts, value} points
 *   alarms                 — active alarms array
 *   intelligence           — unified intelligence snapshot
 *   deviceLastSeen         — ISO string
 *
 * DB connection budget:
 *   Preload = 1 request → internally uses cached data_service calls
 *   WS = 0 DB (RealtimeCoordinator handles this in-memory)
 *   Total on mount = 1 DB request regardless of widget count
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { TelemetrySocket } from "../services/websocket.js";
import { alarmApi, telemetryApi } from "../services/api.js";

const FLUSH_INTERVAL_MS = 250;
const MAX_HISTORY       = 60;

/**
 * useDashboardRuntime(activeDash, user)
 *
 * Returns the full runtime state for a dashboard.
 * Used ONCE at the UserDashboardPage level — not inside widgets.
 *
 * @param {object|null} activeDash  — dashboard object with .id and .widgets
 * @param {object|null} user        — current user
 * @returns {{
 *   liveTelem:   {[deviceId]: {[key]: value}},
 *   historyData: {[deviceId]: {[key]: [{ts, value}]}},
 *   alarmsData:  {[deviceId]: alarm[]},
 *   intellData:  {[deviceId]: object},   // unified intelligence per device
 *   wsConnected: {[deviceId]: boolean},
 *   preloadDone: boolean,
 *   preloadError: string|null,
 * }}
 */
export function useDashboardRuntime(activeDash, user) {
  const [liveTelem,   setLiveTelem]   = useState({});
  const [historyData, setHistoryData] = useState({});
  const [alarmsData,  setAlarmsData]  = useState({});
  const [intellData,  setIntellData]  = useState({});
  const [wsConnected, setWsConnected] = useState({});
  const [preloadDone, setPreloadDone] = useState(false);
  const [preloadError,setPreloadError]= useState(null);

  // Pending WS buffers — written by WS callbacks, drained by flush timer
  const pendingTelemRef   = useRef({});
  const pendingHistoryRef = useRef({});

  // Track which dashboardId we've preloaded to avoid double-fetch
  const preloadedDashRef = useRef(null);

  // ── Derive unique device IDs from active dashboard widgets ────────────────
  const widgetDeviceIds = activeDash?.widgets
    ? [...new Set(
        activeDash.widgets
          .map(w => w.config?.device_id)
          .filter(Boolean)
      )]
    : [];

  // ── PRELOAD — single request hydrates everything ──────────────────────────
  useEffect(() => {
    if (!activeDash?.id) return;
    if (preloadedDashRef.current === activeDash.id) return; // already loaded

    preloadedDashRef.current = activeDash.id;
    setPreloadDone(false);
    setPreloadError(null);

    const run = async () => {
      const token = localStorage.getItem("access_token") || "";
      let baseUrl = "/api/v1";
      try {
        const { API_BASE } = await import("../services/api.js");
        baseUrl = API_BASE;
      } catch (_) {}

      const preloadUrl = `${baseUrl.replace("/api/v1", "")}/api/v1/user-dashboards/${activeDash.id}/preload`;

      try {
        const r = await fetch(preloadUrl, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) throw new Error(`preload ${r.status}`);
        const data = await r.json();
        const devices = data.devices || {};

        const newTelem   = {};
        const newHistory = {};
        const newAlarms  = {};
        const newIntell  = {};

        Object.entries(devices).forEach(([devId, devData]) => {
          if (devData.telemetry && Object.keys(devData.telemetry).length > 0)
            newTelem[devId] = devData.telemetry;
          if (devData.history && Object.keys(devData.history).length > 0)
            newHistory[devId] = devData.history;
          if (devData.alarms)
            newAlarms[devId] = devData.alarms;
          if (devData.intelligence)
            newIntell[devId] = devData.intelligence;
        });

        setLiveTelem(prev   => ({ ...prev, ...newTelem   }));
        setHistoryData(prev => ({ ...prev, ...newHistory }));
        setAlarmsData(prev  => ({ ...prev, ...newAlarms  }));
        setIntellData(prev  => ({ ...prev, ...newIntell  }));
        setPreloadDone(true);

      } catch (err) {
        setPreloadError(String(err));
        // Fallback: individual REST fetches per device
        await _fallbackFetch(
          widgetDeviceIds,
          setLiveTelem, setHistoryData, setAlarmsData
        );
        setPreloadDone(true);
      }
    };

    run();
  }, [activeDash?.id]);

  // Reset preload tracking when dashboard changes
  useEffect(() => {
    if (!activeDash?.id) return;
    // If dashboard actually changed (not just re-render), reset loaded flag
    // so switching dashboards re-fetches
    return () => {
      if (preloadedDashRef.current === activeDash.id) {
        preloadedDashRef.current = null;
      }
    };
  }, [activeDash?.id]);

  // ── WebSocket subscriptions — ONE per device ──────────────────────────────
  useEffect(() => {
    if (!widgetDeviceIds.length) return;

    // Subscribe to one WS connection per device
    // WS callback writes ONLY to refs — no setState here
    const unsubs = widgetDeviceIds.map(deviceId => {
      return TelemetrySocket.subscribe(deviceId, null, (values, ts) => {
        if (!pendingTelemRef.current[deviceId]) {
          pendingTelemRef.current[deviceId] = {};
        }
        Object.assign(pendingTelemRef.current[deviceId], values);

        if (!pendingHistoryRef.current[deviceId]) {
          pendingHistoryRef.current[deviceId] = {};
        }
        const devHist = pendingHistoryRef.current[deviceId];
        Object.entries(values).forEach(([k, v]) => {
          if (!devHist[k]) devHist[k] = [];
          devHist[k].push({ ts: ts || new Date().toISOString(), value: v });
        });
      });
    });

    // Flush timer: drain WS refs into React state at 250ms cadence
    // ONE setState call covers all devices — single render pass
    const flushTimer = setInterval(() => {
      const pt = pendingTelemRef.current;
      const ph = pendingHistoryRef.current;
      const hasTelem   = Object.keys(pt).length > 0;
      const hasHistory = Object.keys(ph).length > 0;
      if (!hasTelem && !hasHistory) return;

      pendingTelemRef.current   = {};
      pendingHistoryRef.current = {};

      if (hasTelem) {
        setLiveTelem(prev => {
          const next = { ...prev };
          Object.entries(pt).forEach(([devId, vals]) => {
            next[devId] = { ...(prev[devId] || {}), ...vals };
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

    // Connection status polling
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

  // ── Intelligence refresh (light, 60s cadence) ─────────────────────────────
  // Refreshes intelligence snapshots in the background without blocking render
  const intellTimerRef = useRef(null);
  useEffect(() => {
    if (!widgetDeviceIds.length || !preloadDone) return;

    const refresh = async () => {
      const token = localStorage.getItem("access_token") || "";
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
              headers: { Authorization: `Bearer ${token}` },
            });
            if (r.ok) updates[devId] = await r.json();
          } catch (_) {}
        })
      );
      if (Object.keys(updates).length) {
        setIntellData(prev => ({ ...prev, ...updates }));
      }
    };

    // Refresh after 60s, then every 60s
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

/**
 * Fallback when preload endpoint fails.
 * Uses individual REST calls — same as the old behavior.
 * Only fires if preload returns non-200.
 */
async function _fallbackFetch(deviceIds, setLiveTelem, setHistoryData, setAlarmsData) {
  await Promise.allSettled(deviceIds.map(async deviceId => {
    try {
      const rows = await telemetryApi.latest(deviceId);
      if (rows?.length) {
        const values = {};
        rows.forEach(r => { values[r.key] = r.value; });
        setLiveTelem(prev => ({ ...prev, [deviceId]: { ...(prev[deviceId] || {}), ...values } }));
      }
    } catch (_) {}

    try {
      const rows = await alarmApi.list({ device_id: deviceId, limit: 50 });
      setAlarmsData(prev => ({ ...prev, [deviceId]: rows || [] }));
    } catch (_) {}
  }));
}
