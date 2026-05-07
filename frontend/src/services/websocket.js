/**
 * services/websocket.js
 *
 * Central WebSocket service for real-time telemetry.
 *
 * Architecture:
 *   - ONE WebSocket connection per device_id (not per widget)
 *   - Multiple subscribers can attach to the same connection
 *   - Each subscriber filters the messages it cares about by key
 *   - On disconnect: exponential-backoff reconnect, then REST fallback
 *   - On reconnect: re-runs REST fetch so state is always consistent
 *
 * Message format from backend:
 *   {
 *     "type":      "telemetry",
 *     "device_id": "uuid",
 *     "values":    { "temperature": 28.5, "humidity": 70 },
 *     "ts":        "2025-04-29T10:00:00.123Z"
 *   }
 *
 * Usage:
 *   import { TelemetrySocket } from "./websocket.js";
 *
 *   // Subscribe to all keys for a device
 *   const unsub = TelemetrySocket.subscribe(deviceId, null, (values, ts) => {
 *     console.log(values); // { temperature: 28.5 }
 *   });
 *   unsub(); // cleanup
 *
 *   // Subscribe to specific keys only (widget-level filtering)
 *   const unsub = TelemetrySocket.subscribe(deviceId, ["temperature"], (values, ts) => {
 *     // values will only contain keys that match the filter
 *   });
 */

import { WS_BASE, telemetryApi } from "./api.js";

// ── Constants ─────────────────────────────────────────────────────────────────
const PING_INTERVAL_MS    = 25_000;   // send "ping" every 25s to keep connection alive
const RECONNECT_BASE_MS   = 1_000;   // first reconnect attempt after 1s
const RECONNECT_MAX_MS    = 30_000;  // cap at 30s
const RECONNECT_JITTER_MS = 500;     // ±500ms jitter to avoid thundering herd
const FALLBACK_POLL_MS    = 10_000;   // REST polling interval when WS is unavailable

// ── Per-device connection state ───────────────────────────────────────────────

/**
 * @typedef {Object} Subscriber
 * @property {string}        id        - unique subscriber id
 * @property {string[]|null} keys      - telemetry keys to listen to (null = all)
 * @property {Function}      callback  - (values: Object, ts: string) => void
 */

/**
 * @typedef {Object} DeviceConnection
 * @property {WebSocket|null}  ws
 * @property {Subscriber[]}    subscribers
 * @property {number}          pingTimer
 * @property {number}          reconnectTimer
 * @property {number}          reconnectDelay
 * @property {number}          fallbackTimer
 * @property {boolean}         connected
 * @property {boolean}         useFallback
 * @property {string}          deviceId
 */

/** @type {Map<string, DeviceConnection>} */
const _connections = new Map();

let _subIdCounter = 0;
const _nextSubId = () => `sub_${++_subIdCounter}`;

// ── Internal helpers ──────────────────────────────────────────────────────────

function _getState(deviceId) {
  if (!_connections.has(deviceId)) {
    _connections.set(deviceId, {
      ws:             null,
      subscribers:    [],
      pingTimer:      0,
      reconnectTimer: 0,
      reconnectDelay: RECONNECT_BASE_MS,
      fallbackTimer:  0,
      connected:      false,
      useFallback:    false,
      deviceId,

      lastDispatch: 0,
    });
  }
  return _connections.get(deviceId);
}

function _dispatchToSubscribers(state, values, ts) {
  for (const sub of state.subscribers) {
    // Filter: if subscriber has a key list, only pass matching keys
    if (sub.keys && sub.keys.length > 0) {
      const filtered = {};
      let hasMatch = false;
      for (const k of sub.keys) {
        if (k in values) { filtered[k] = values[k]; hasMatch = true; }
      }
      if (hasMatch) sub.callback(filtered, ts);
    } else {
      // No filter — send everything
      sub.callback(values, ts);
    }
  }
}

function _clearTimers(state) {
  if (state.pingTimer)      clearInterval(state.pingTimer);
  if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
  if (state.fallbackTimer)  clearInterval(state.fallbackTimer);
  state.pingTimer      = 0;
  state.reconnectTimer = 0;
  state.fallbackTimer  = 0;
}

// ── REST fallback polling ─────────────────────────────────────────────────────

function _startFallback(state) {
  if (state.fallbackTimer) return;   // already polling

  const poll = async () => {
    if (!state.subscribers.length) return;
    try {
      const rows = await telemetryApi.latest(state.deviceId);
      if (!rows?.length) return;
      const values = {};
      const ts = rows.reduce((latest, r) => {
        values[r.key] = r.value;
        return r.ts > latest ? r.ts : latest;
      }, "");
      _dispatchToSubscribers(state, values, ts);
    } catch (_) {
      // Silently ignore — network may be down
    }
  };

  poll();  // immediate first run
  state.fallbackTimer = setInterval(poll, FALLBACK_POLL_MS);
  state.useFallback = true;
}

function _stopFallback(state) {
  if (state.fallbackTimer) {
    clearInterval(state.fallbackTimer);
    state.fallbackTimer = 0;
  }
  state.useFallback = false;
}

// ── WebSocket lifecycle ───────────────────────────────────────────────────────

function _connect(deviceId) {
  const state = _getState(deviceId);
  if (!state.subscribers.length) return;   // nobody is listening, skip
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;  // already open/connecting

  // Include JWT token in WS URL query param — WS connections can't use headers
  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('access_token') : '';
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
  const url = `${WS_BASE}/api/v1/ws/telemetry/${deviceId}${tokenParam}`;

  let ws;
  try {
    ws = new WebSocket(url);
  } catch (_) {
    // WebSocket constructor can throw in some environments (e.g. bad URL)
    _startFallback(state);
    return;
  }

  state.ws = ws;

  ws.onopen = () => {
    state.connected    = true;
    state.reconnectDelay = RECONNECT_BASE_MS;  // reset backoff

    // Stop fallback polling now that WS is live
    _stopFallback(state);

    // Keepalive ping
    state.pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send("ping");
      }
    }, PING_INTERVAL_MS);

    // Immediately fetch latest values so widgets aren't stale on reconnect
    telemetryApi.latest(deviceId).then(rows => {
      if (!rows?.length) return;
      const values = {};
      const ts = rows.reduce((latest, r) => {
        values[r.key] = r.value;
        return r.ts > latest ? r.ts : latest;
      }, "");
      _dispatchToSubscribers(state, values, ts);
    }).catch(() => {});
  };
  
   
    const DISPATCH_INTERVAL_MS = 1000;
  ws.onmessage = (event) => {

  const now = Date.now();

  if (now - state.lastDispatch < DISPATCH_INTERVAL_MS){
    return;
  }
  state.lastDispatch = now;
  

  try {

    const msg = JSON.parse(event.data);

    if (msg.type === "telemetry" && msg.values) {

      _dispatchToSubscribers(
        state,
        msg.values,
        msg.ts
      );
    }

  } catch (_) {}
};

  ws.onclose = () => {
    state.connected = false;
    clearInterval(state.pingTimer);
    state.pingTimer = 0;

    if (!state.subscribers.length) return;  // nobody left, don't reconnect

    // Start fallback polling immediately while we try to reconnect
    _startFallback(state);

    // Exponential backoff with jitter
    const jitter = Math.random() * RECONNECT_JITTER_MS - RECONNECT_JITTER_MS / 2;
    const delay  = Math.min(state.reconnectDelay + jitter, RECONNECT_MAX_MS);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, RECONNECT_MAX_MS);

    state.reconnectTimer = setTimeout(() => {
      state.reconnectTimer = 0;
      _connect(deviceId);
    }, delay);
  };

  ws.onerror = () => {
    // onerror is always followed by onclose — let onclose handle reconnect
    ws.close();
  };
}

function _disconnect(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return;

  _clearTimers(state);

  if (state.ws) {
    state.ws.onclose = null;   // prevent reconnect loop
    state.ws.onerror = null;
    state.ws.close();
    state.ws = null;
  }

  state.connected  = false;
  state.useFallback = false;
  _connections.delete(deviceId);
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Subscribe to real-time telemetry for a device.
 *
 * @param {string}        deviceId  - device UUID
 * @param {string[]|null} keys      - telemetry keys to watch (null = all keys)
 * @param {Function}      callback  - called with (values: Object, ts: string)
 * @returns {Function}              - call to unsubscribe
 */
function subscribe(deviceId, keys, callback) {
  if (!deviceId || !callback) throw new Error("deviceId and callback are required");

  const state = _getState(deviceId);
  const sub   = { id: _nextSubId(), keys: keys || null, callback };

  state.subscribers.push(sub);

  // Open connection if not already open
  _connect(deviceId);

  // Return unsubscribe function
  return () => {
    state.subscribers = state.subscribers.filter(s => s.id !== sub.id);

    // If no more subscribers, tear down the connection
    if (!state.subscribers.length) {
      _disconnect(deviceId);
    }
  };
}

/**
 * Get the current connection status for a device.
 * @param {string} deviceId
 * @returns {{ connected: boolean, useFallback: boolean }}
 */
function getStatus(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return { connected: false, useFallback: false };
  return { connected: state.connected, useFallback: state.useFallback };
}

/**
 * Force-close and reconnect a device's WebSocket.
 * Useful when the device token changes.
 */
function reconnect(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return;

  const subs = [...state.subscribers];
  _disconnect(deviceId);

  // Re-register same subscribers on a fresh connection
  if (subs.length) {
    const newState = _getState(deviceId);
    newState.subscribers = subs;
    _connect(deviceId);
  }
}

export const TelemetrySocket = { subscribe, getStatus, reconnect };
