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
 * OPT 1 — Accumulate-and-flush (replaces drop-gate throttle):
 *   Previous: DISPATCH_INTERVAL_MS = 1000 + return-early DROPPED messages.
 *   New: every WS message merges into pendingValues (last-write-wins per key).
 *   A setInterval flushes subscribers every FLUSH_INTERVAL_MS (250ms).
 *   No messages are ever dropped. React renders batched by flush cadence.
 *
 * Message format from backend (coordinator batch):
 *   { "type": "telemetry", "device_id": "uuid", "values": {...},
 *     "ts": "...", "batched": true }
 */

import { WS_BASE, telemetryApi } from "./api.js";

// ── Constants ─────────────────────────────────────────────────────────────────
const PING_INTERVAL_MS    = 25_000;
const RECONNECT_BASE_MS   = 1_000;
const RECONNECT_MAX_MS    = 30_000;
const RECONNECT_JITTER_MS = 500;
const FALLBACK_POLL_MS    = 10_000;
const FLUSH_INTERVAL_MS   = 250;   // matches RealtimeCoordinator flush cadence

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
      // Priority 2: key-indexed subscriber map for O(1) dispatch per key
      // keyIndex: Map<key_string, Set<subscriber_id>>
      // "null" key means "subscribe to all keys"
      keyIndex:       new Map(),
      pingTimer:      0,
      reconnectTimer: 0,
      reconnectDelay: RECONNECT_BASE_MS,
      fallbackTimer:  0,
      flushTimer:     0,
      pendingValues:  {},
      pendingTs:      "",
      connected:      false,
      useFallback:    false,
      deviceId,
    });
  }
  return _connections.get(deviceId);
}

function _dispatchToSubscribers(state, values, ts) {
  // Priority 2: key-indexed dispatch — only notify subscribers whose keys changed.
  // Build a per-subscriber filtered payload in one pass over changed keys.
  // Cost: O(changed_keys × avg_subscribers_per_key) instead of O(all_subscribers × all_keys).
  const changedKeys = Object.keys(values);
  if (changedKeys.length === 0) return;

  // Accumulate per-subscriber filtered values
  const subPayloads = new Map(); // sub_id → {values: {}, sub}

  for (const k of changedKeys) {
    // Notify key-specific subscribers
    const keySubs = state.keyIndex.get(k);
    if (keySubs) {
      for (const subId of keySubs) {
        const sub = state._subById?.get(subId);
        if (!sub) continue;
        let payload = subPayloads.get(subId);
        if (!payload) { payload = { values: {}, sub }; subPayloads.set(subId, payload); }
        payload.values[k] = values[k];
      }
    }
    // Notify wildcard subscribers (keys=null — subscribe to everything)
    const wildcardSubs = state.keyIndex.get(null);
    if (wildcardSubs) {
      for (const subId of wildcardSubs) {
        const sub = state._subById?.get(subId);
        if (!sub) continue;
        let payload = subPayloads.get(subId);
        if (!payload) { payload = { values: {}, sub }; subPayloads.set(subId, payload); }
        payload.values[k] = values[k];
      }
    }
  }

  for (const { values: filtered, sub } of subPayloads.values()) {
    sub.callback(filtered, ts);
  }
}

// Build or rebuild keyIndex from current subscribers array
function _rebuildKeyIndex(state) {
  const index = new Map();
  const byId  = new Map();
  for (const sub of state.subscribers) {
    byId.set(sub.id, sub);
    if (!sub.keys || sub.keys.length === 0) {
      // Wildcard — interested in all keys
      if (!index.has(null)) index.set(null, new Set());
      index.get(null).add(sub.id);
    } else {
      for (const k of sub.keys) {
        if (!index.has(k)) index.set(k, new Set());
        index.get(k).add(sub.id);
      }
    }
  }
  state.keyIndex  = index;
  state._subById  = byId;
}

function _clearTimers(state) {
  if (state.pingTimer)      clearInterval(state.pingTimer);
  if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
  if (state.fallbackTimer)  clearInterval(state.fallbackTimer);
  if (state.flushTimer)     clearInterval(state.flushTimer);
  state.pingTimer      = 0;
  state.reconnectTimer = 0;
  state.fallbackTimer  = 0;
  state.flushTimer     = 0;
}

// ── Accumulate-and-flush ──────────────────────────────────────────────────────

function _startFlushTimer(state) {
  if (state.flushTimer) return;
  state.flushTimer = setInterval(() => {
    if (Object.keys(state.pendingValues).length === 0) return;
    const values = state.pendingValues;
    const ts     = state.pendingTs;
    state.pendingValues = {};
    state.pendingTs     = "";
    _dispatchToSubscribers(state, values, ts);
  }, FLUSH_INTERVAL_MS);
}

function _stopFlushTimer(state) {
  if (state.flushTimer) {
    clearInterval(state.flushTimer);
    state.flushTimer = 0;
  }
}

// ── REST fallback polling ─────────────────────────────────────────────────────

function _startFallback(state) {
  if (state.fallbackTimer) return;

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
      Object.assign(state.pendingValues, values);
      if (!state.pendingTs || ts > state.pendingTs) state.pendingTs = ts;
    } catch (_) {}
  };

  poll();
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
  if (!state.subscribers.length) return;
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  const token = typeof localStorage !== "undefined" ? localStorage.getItem("access_token") : "";
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
  const url = `${WS_BASE}/api/v1/ws/telemetry/${deviceId}${tokenParam}`;

  let ws;
  try {
    ws = new WebSocket(url);
  } catch (_) {
    _startFallback(state);
    return;
  }

  state.ws = ws;

  ws.onopen = () => {
    state.connected      = true;
    state.reconnectDelay = RECONNECT_BASE_MS;

    _stopFallback(state);
    _startFlushTimer(state);

    state.pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, PING_INTERVAL_MS);

    // Seed latest values on connect — route through buffer
    telemetryApi.latest(deviceId).then(rows => {
      if (!rows?.length) return;
      const values = {};
      const ts = rows.reduce((latest, r) => {
        values[r.key] = r.value;
        return r.ts > latest ? r.ts : latest;
      }, "");
      Object.assign(state.pendingValues, values);
      if (!state.pendingTs || ts > state.pendingTs) state.pendingTs = ts;
    }).catch(() => {});
  };

  // Accumulate only — no dispatch. Flush timer handles dispatch.
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "telemetry" && (msg.values || msg.delta)) {
        Object.assign(state.pendingValues, msg.values || msg.delta);
        if (!state.pendingTs || (msg.ts && msg.ts > state.pendingTs)) {
          state.pendingTs = msg.ts || state.pendingTs;
        }
      }
    } catch (_) {}
  };

  ws.onclose = () => {
    state.connected = false;
    clearInterval(state.pingTimer);
    state.pingTimer = 0;
    _stopFlushTimer(state);

    if (!state.subscribers.length) return;

    _startFallback(state);

    const jitter = Math.random() * RECONNECT_JITTER_MS - RECONNECT_JITTER_MS / 2;
    const delay  = Math.min(state.reconnectDelay + jitter, RECONNECT_MAX_MS);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, RECONNECT_MAX_MS);

    state.reconnectTimer = setTimeout(() => {
      state.reconnectTimer = 0;
      _connect(deviceId);
    }, delay);
  };

  ws.onerror = () => { ws.close(); };
}

function _disconnect(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return;

  _clearTimers(state);

  if (state.ws) {
    state.ws.onclose = null;
    state.ws.onerror = null;
    state.ws.close();
    state.ws = null;
  }

  state.connected     = false;
  state.useFallback   = false;
  state.pendingValues = {};
  state.pendingTs     = "";
  _connections.delete(deviceId);
}

// ── Public API ────────────────────────────────────────────────────────────────

function subscribe(deviceId, keys, callback) {
  if (!deviceId || !callback) throw new Error("deviceId and callback are required");

  const state = _getState(deviceId);
  const sub   = { id: _nextSubId(), keys: keys || null, callback };

  state.subscribers.push(sub);
  _rebuildKeyIndex(state);  // Priority 2: update key index
  _connect(deviceId);

  return () => {
    state.subscribers = state.subscribers.filter(s => s.id !== sub.id);
    _rebuildKeyIndex(state);  // rebuild after removal
    if (!state.subscribers.length) _disconnect(deviceId);
  };
}

function getStatus(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return { connected: false, useFallback: false };
  return { connected: state.connected, useFallback: state.useFallback };
}

function reconnect(deviceId) {
  const state = _connections.get(deviceId);
  if (!state) return;
  const subs = [...state.subscribers];
  _disconnect(deviceId);
  if (subs.length) {
    const newState = _getState(deviceId);
    newState.subscribers = subs;
    _connect(deviceId);
  }
}

export const TelemetrySocket = { subscribe, getStatus, reconnect };
