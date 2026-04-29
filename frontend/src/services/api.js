/**
 * api.js — HTTP transport layer.
 * One function per backend endpoint. No business logic here.
 * All callers import from dashboardService.js, not directly from here.
 */

const BASE_URL =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL) ||
  "http://localhost:8000";

export const API_BASE = `${BASE_URL}/api/v1`;
export const WS_BASE  = BASE_URL.replace(/^http/, "ws");

function getToken() {
  return localStorage.getItem("access_token");
}

export async function apiFetch(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });

  if (res.status === 401) {
    localStorage.clear();
    window.location.reload();
    return;
  }
  if (res.status === 204) return null;

  const data = await res.json();
  if (!res.ok) {
    // FastAPI validation errors return detail as an array of objects.
    // Extract a readable string instead of letting it become [object Object].
    const detail = data?.detail;
    let msg;
    if (Array.isArray(detail)) {
      // [{loc:[...], msg:'...', type:'...'}, ...]
      msg = detail.map(e => `${e.loc?.slice(-1)[0] || 'field'}: ${e.msg}`).join(' | ');
    } else {
      msg = typeof detail === 'string' ? detail : `HTTP ${res.status}`;
    }
    throw new Error(msg);
  }
  return data;
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login:    (email, password) => apiFetch("/auth/login",    { method: "POST", body: JSON.stringify({ email, password }) }),
  register: (body)            => apiFetch("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  seedDemo:      ()                    => apiFetch("/auth/seed-demo",      { method: "POST" }),
  resetPassword: (email, new_password) => apiFetch("/auth/reset-password", { method: "POST", body: JSON.stringify({ email, new_password }) }),
};

// ── Devices ───────────────────────────────────────────────────────────────────
export const deviceApi = {
  list:            (params = {}) => apiFetch(`/devices/?${new URLSearchParams(params)}`),
  get:             id            => apiFetch(`/devices/${id}`),
  create:          body          => apiFetch("/devices/",     { method: "POST",   body: JSON.stringify(body) }),
  update:          (id, body)    => apiFetch(`/devices/${id}`,{ method: "PUT",    body: JSON.stringify(body) }),
  delete:          id            => apiFetch(`/devices/${id}`,{ method: "DELETE" }),
  regenerateToken: id            => apiFetch(`/devices/${id}/token/regenerate`, { method: "POST" }),
};

// ── Telemetry ─────────────────────────────────────────────────────────────────
export const telemetryApi = {
  latest:  deviceId          => apiFetch(`/telemetry/latest/${deviceId}`),
  history: (deviceId, key, n) => apiFetch(`/telemetry/history/${deviceId}?key=${encodeURIComponent(key)}&limit=${n ?? 50}`),
  keys:    deviceId          => apiFetch(`/telemetry/keys/${deviceId}`),
};

// ── Alarms ────────────────────────────────────────────────────────────────────
export const alarmApi = {
  list:   (params = {}) => apiFetch(`/alarms/?${new URLSearchParams(params)}`),
  create: body          => apiFetch("/alarms/",           { method: "POST", body: JSON.stringify(body) }),
  ack:    id            => apiFetch(`/alarms/${id}/ack`,  { method: "POST" }),
  clear:  id            => apiFetch(`/alarms/${id}/clear`,{ method: "POST" }),
  delete: id            => apiFetch(`/alarms/${id}`,      { method: "DELETE" }),
};

// ── Overview stats ────────────────────────────────────────────────────────────
export const statsApi = {
  get: () => apiFetch("/dashboard/stats"),
};

// ── Dashboards (raw HTTP — use dashboardService.js for business logic) ────────
export const dashboardsHttp = {
  list:         deviceId           => apiFetch(`/dashboards/?device_id=${deviceId}`),
  create:       body               => apiFetch("/dashboards/",           { method: "POST",   body: JSON.stringify(body) }),
  get:          id                 => apiFetch(`/dashboards/${id}`),
  update:       (id, body)         => apiFetch(`/dashboards/${id}`,       { method: "PUT",    body: JSON.stringify(body) }),
  delete:       id                 => apiFetch(`/dashboards/${id}`,       { method: "DELETE" }),
  listWidgets:  dashId             => apiFetch(`/dashboards/${dashId}/widgets/`),
  addWidget:    (dashId, body)     => apiFetch(`/dashboards/${dashId}/widgets/`,         { method: "POST",   body: JSON.stringify(body) }),
  updateWidget: (dashId, wId, body)=> apiFetch(`/dashboards/${dashId}/widgets/${wId}`,  { method: "PUT",    body: JSON.stringify(body) }),
  deleteWidget: (dashId, wId)      => apiFetch(`/dashboards/${dashId}/widgets/${wId}`,  { method: "DELETE" }),
  saveLayout:   (dashId, layout)   => apiFetch(`/dashboards/${dashId}/layout`,           { method: "PUT",    body: JSON.stringify({ layout }) }),
};

// ── User Dashboards (Phase 2 — multi-dashboard sidebar) ───────────────────────
export const userDashboardsHttp = {
  // Dashboard CRUD
  list:       ()           => apiFetch("/user-dashboards/"),
  getDefault: ()           => apiFetch("/user-dashboards/default"),
  get:        id           => apiFetch(`/user-dashboards/${id}`),
  create:     body         => apiFetch("/user-dashboards/",                   { method: "POST",   body: JSON.stringify(body) }),
  rename:     (id, name)   => apiFetch(`/user-dashboards/${id}/rename`,       { method: "PUT",    body: JSON.stringify({ name }) }),
  setDefault: id           => apiFetch(`/user-dashboards/${id}/set-default`,  { method: "POST" }),
  delete:     id           => apiFetch(`/user-dashboards/${id}`,              { method: "DELETE" }),
  // Widget CRUD
  addWidget:    (dashId, body)      => apiFetch(`/user-dashboards/${dashId}/widgets/`,        { method: "POST",   body: JSON.stringify(body) }),
  updateWidget: (dashId, wId, body) => apiFetch(`/user-dashboards/${dashId}/widgets/${wId}`, { method: "PUT",    body: JSON.stringify(body) }),
  deleteWidget: (dashId, wId)       => apiFetch(`/user-dashboards/${dashId}/widgets/${wId}`, { method: "DELETE" }),
  saveLayout:    (dashId, layout)    => apiFetch(`/user-dashboards/${dashId}/layout`,          { method: "PUT",    body: JSON.stringify({ layout }) }),
  deduplicate:   ()                  => apiFetch(`/user-dashboards/deduplicate`,               { method: "POST" }),
};
