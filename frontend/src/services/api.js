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
      msg = detail.map(e => typeof e === 'string' ? e : `${e.loc?.slice(-1)[0] || 'field'}: ${e.msg}`).join(' | ');
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
  logout:   (refreshToken)    => apiFetch("/auth/logout",   { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }),
  seedDemo:      ()                    => apiFetch("/auth/seed-demo",      { method: "POST" }),
  resetPassword: (email, new_password) => apiFetch("/auth/reset-password", { method: "POST", body: JSON.stringify({ email, new_password }) }),
};

// ── Devices ───────────────────────────────────────────────────────────────────
export const deviceApi = {
  list:            (params = {}) => apiFetch(`/devices/?${new URLSearchParams(params)}`).then(r => r?.items ?? r),
  listPaged:       (params = {}) => apiFetch(`/devices/?${new URLSearchParams(params)}`),  // returns {total,page,page_size,items}
  get:             id            => apiFetch(`/devices/${id}`),
  create:          body          => apiFetch("/devices/",     { method: "POST",   body: JSON.stringify(body) }),
  update:          (id, body)    => apiFetch(`/devices/${id}`,{ method: "PUT",    body: JSON.stringify(body) }),
  delete:          id            => apiFetch(`/devices/${id}`,{ method: "DELETE" }),
  regenerateToken: id            => apiFetch(`/devices/${id}/token/regenerate`, { method: "POST" }),
};

// ── Telemetry ─────────────────────────────────────────────────────────────────
export const telemetryApi = {
  latest:      deviceId                    => apiFetch(`/telemetry/latest/${deviceId}`),
  history:     (deviceId, key, n)          => apiFetch(`/telemetry/history/${deviceId}?key=${encodeURIComponent(key)}&limit=${n ?? 50}`),
  bulkHistory: (deviceId, keys, limit=50)  => apiFetch(`/telemetry/history/${deviceId}/bulk`, { method: "POST", body: JSON.stringify({ keys, limit }) }),
  keys:        deviceId                    => apiFetch(`/telemetry/keys/${deviceId}`),
  aggregate:   (deviceId, key, window, fn) => apiFetch(`/telemetry/aggregate/${deviceId}?key=${encodeURIComponent(key)}&window=${window}&function=${fn}`),
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

// ── Device Provisioning ───────────────────────────────────────────────────────
export const provisioningApi = {
  // Returns the tenant's provisioning key (JWT required)
  getKey: () => apiFetch("/devices/provisioning-key"),
  // Self-register a device using only the provision key (no JWT)
  // Used by ESP32 / firmware — not called from the web UI
  provision: (body) => apiFetch("/devices/provision", { method: "POST", body: JSON.stringify(body) }),
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

// ── User management (TENANT_ADMIN only) ──────────────────────────────────────
export const userApi = {
  list:       ()             => apiFetch("/auth/users"),
  invite:     body           => apiFetch("/auth/users/invite",      { method: "POST",   body: JSON.stringify(body) }),
  updateRole: (id, body)     => apiFetch(`/auth/users/${id}/role`, { method: "PUT",    body: JSON.stringify(body) }),
  delete:     id             => apiFetch(`/auth/users/${id}`,      { method: "DELETE" }),
};

// ── Customer management ───────────────────────────────────────────────────────
export const customerApi = {
  list:              ()              => apiFetch("/customers/"),
  create:            body            => apiFetch("/customers/",                    { method: "POST",   body: JSON.stringify(body) }),
  delete:            id              => apiFetch(`/customers/${id}`,               { method: "DELETE" }),
  listUsers:         customerId      => apiFetch(`/customers/${customerId}/users`),
  createUser:        (customerId, b) => apiFetch(`/customers/${customerId}/users`, { method: "POST",   body: JSON.stringify(b) }),
};

// ── Threshold Rules ───────────────────────────────────────────────────────────
export const thresholdApi = {
  list:   ()         => apiFetch("/threshold-rules/"),
  create: body       => apiFetch("/threshold-rules/",    { method: "POST",   body: JSON.stringify(body) }),
  update: (id, body) => apiFetch(`/threshold-rules/${id}`, { method: "PUT",  body: JSON.stringify(body) }),
  delete: id         => apiFetch(`/threshold-rules/${id}`, { method: "DELETE" }),
};

// ── RPC ───────────────────────────────────────────────────────────────────────
export const rpcApi = {
  send:    (deviceId, body) => apiFetch(`/rpc/${deviceId}`,  { method: "POST", body: JSON.stringify(body) }),
  history: (deviceId, status) => apiFetch(`/rpc/${deviceId}${status ? `?status=${status}` : ""}`),
};

// ── Widget Templates ──────────────────────────────────────────────────────────
export const widgetTemplateApi = {
  list:   ()         => apiFetch("/widget-templates/"),
  create: body       => apiFetch("/widget-templates/",       { method: "POST",   body: JSON.stringify(body) }),
  get:    id         => apiFetch(`/widget-templates/${id}`),
  delete: id         => apiFetch(`/widget-templates/${id}`,  { method: "DELETE" }),
};

// ── Metrics ───────────────────────────────────────────────────────────────────
export const metricsApi = {
  get: () => apiFetch("/metrics/"),
};

// ── API Keys (TENANT_ADMIN only) ──────────────────────────────────────────────
export const apiKeysApi = {
  list:   ()              => apiFetch("/api-keys/"),
  create: body            => apiFetch("/api-keys/",     { method: "POST",   body: JSON.stringify(body) }),
  revoke: id              => apiFetch(`/api-keys/${id}`, { method: "DELETE" }),
};

// ── System observability (TENANT_ADMIN only) ──────────────────────────────────
export const systemApi = {
  health:  ()                          => apiFetch("/system/health"),
  metrics: ()                          => apiFetch("/system/metrics"),
  audit:   (limit = 50, action = null) => apiFetch(`/system/audit?limit=${limit}${action ? `&action=${encodeURIComponent(action)}` : ""}`),
};

// ── Intelligence API ──────────────────────────────────────────────────────────
export const intelligenceApi = {
  // Phase 6
  trend:   (deviceId, key, minutes=30) => apiFetch(`/intelligence/trend/${deviceId}/${key}?minutes=${minutes}`),
  trends:  (deviceId, minutes=30)      => apiFetch(`/intelligence/trend/${deviceId}?minutes=${minutes}`),
  rca:     (deviceId)                  => apiFetch(`/intelligence/rca/${deviceId}`, { method: "POST" }),
  summary: (deviceId)                  => apiFetch(`/intelligence/summary/${deviceId}`),
  chat:    (messages, deviceId=null, pendingConfirm=null) => apiFetch(`/intelligence/chat`, { method: "POST", body: JSON.stringify({ messages, device_id: deviceId, pending_confirm: pendingConfirm }) }),
  // Phase 7
  anomalies:       (deviceId, hours=24, key=null) => apiFetch(`/intelligence/anomalies/${deviceId}?hours=${hours}${key ? `&key=${encodeURIComponent(key)}` : ""}`),
  baseline:        (deviceId)                     => apiFetch(`/intelligence/baseline/${deviceId}`),
  refreshBaseline: (deviceId)                     => apiFetch(`/intelligence/baseline/${deviceId}/refresh`, { method: "POST" }),
  health:          (deviceId)                     => apiFetch(`/intelligence/health/${deviceId}`),
  fleetHealth:     ()                             => apiFetch(`/intelligence/health`),
  // Phase 8
  alarmAction:     (body)                         => apiFetch(`/intelligence/alarm-action`, { method: "POST", body: JSON.stringify(body) }),
  scheduleRpc:     (body)                         => apiFetch(`/intelligence/schedule-rpc`, { method: "POST", body: JSON.stringify(body) }),
  scheduledRpcs:   (deviceId)                     => apiFetch(`/intelligence/schedule-rpc${deviceId ? `?device_id=${deviceId}` : ""}`),
  cancelSchedule:  (cmdId)                        => apiFetch(`/intelligence/schedule-rpc/${cmdId}`, { method: "DELETE" }),
  explainAlarm:    (alarmId)                      => apiFetch(`/intelligence/alarm-explain/${alarmId}`, { method: "POST" }),
  compareDevice:   (deviceId)                     => apiFetch(`/intelligence/compare/${deviceId}`),
  dailyReport:     ()                             => apiFetch(`/intelligence/report/daily`),
  usage:           ()                             => apiFetch(`/intelligence/usage`),
  // Phase 10 — Unified Intelligence
  unified:         (deviceId)                     => apiFetch(`/intelligence/unified/${deviceId}`),
  unifiedKey:      (deviceId, key)                => apiFetch(`/intelligence/unified/${deviceId}?key=${encodeURIComponent(key)}`),
  unifiedKeys:     (deviceId, keys = [])          => apiFetch(`/intelligence/unified/${deviceId}?keys=${encodeURIComponent(keys.join(','))}`),

  widgetTelemetry: (deviceId, key, opts = {})     => {
    const { hours = 24, limit = 200, resolution = "raw" } = opts;
    return apiFetch(`/intelligence/unified/${deviceId}/telemetry?key=${encodeURIComponent(key)}&hours=${hours}&limit=${limit}&resolution=${resolution}`);
  },
};

// ── Widget Data API (Phase 10 #3) ─────────────────────────────────────────────
// Single entry point for all widget data. Each widget type has its own
// named method AND falls through to the generic dispatch endpoint.
export const widgetApi = {
  // Generic dispatch — routes by widget type
  data: (deviceId, type, params = {}) => {
    const q = new URLSearchParams({ type, ...params });
    return apiFetch(`/widgets/data/${deviceId}?${q}`);
  },

  // Named per-widget methods — cleaner call sites
  gauge:          (deviceId, key)                     => apiFetch(`/widgets/data/${deviceId}/gauge?key=${encodeURIComponent(key)}`),
  valueCard:      (deviceId, key)                     => apiFetch(`/widgets/data/${deviceId}/value_card?key=${encodeURIComponent(key)}`),
  lineChart:      (deviceId, key, opts = {})          => {
    const { hours = 24, limit = 200, resolution = "raw" } = opts;
    return apiFetch(`/widgets/data/${deviceId}/line_chart?key=${encodeURIComponent(key)}&hours=${hours}&limit=${limit}&resolution=${resolution}`);
  },
  barChart:       (deviceId, key, opts = {})          => {
    const { hours = 24, limit = 200, resolution = "raw" } = opts;
    return apiFetch(`/widgets/data/${deviceId}/bar_chart?key=${encodeURIComponent(key)}&hours=${hours}&limit=${limit}&resolution=${resolution}`);
  },
  multiAxisChart: (deviceId, keys = [], opts = {})    => {
    const { hours = 24, limit = 200, resolution = "raw" } = opts;
    return apiFetch(`/widgets/data/${deviceId}/multi_axis_chart?keys=${encodeURIComponent(keys.join(","))}&hours=${hours}&limit=${limit}&resolution=${resolution}`);
  },
  timeseriesTable:(deviceId, key, opts = {})          => {
    const { hours = 1, limit = 50 } = opts;
    return apiFetch(`/widgets/data/${deviceId}/timeseries_table?key=${encodeURIComponent(key)}&hours=${hours}&limit=${limit}`);
  },
  pieChart:       (deviceId, keys = [])               => apiFetch(`/widgets/data/${deviceId}/pie_chart?keys=${encodeURIComponent(keys.join(","))}`),
  statusLight:    (deviceId, key = "")                => apiFetch(`/widgets/data/${deviceId}/status_light?key=${encodeURIComponent(key)}`),
  alarmList:      (deviceId)                          => apiFetch(`/widgets/data/${deviceId}/alarm_list`),
  entityTable:    (deviceId)                          => apiFetch(`/widgets/data/${deviceId}/entity_table`),
  trendIndicator: (deviceId, key, minutes = 30)       => apiFetch(`/widgets/data/${deviceId}/trend_indicator?key=${encodeURIComponent(key)}&minutes=${minutes}`),
  healthScore:    (deviceId)                          => apiFetch(`/widgets/data/${deviceId}/health_score`),
  anomalyScore:   (deviceId, key = "", hours = 24)    => apiFetch(`/widgets/data/${deviceId}/anomaly_score?key=${encodeURIComponent(key)}&hours=${hours}`),
  baseline:       (deviceId, key = "")                => apiFetch(`/widgets/data/${deviceId}/baseline?key=${encodeURIComponent(key)}`),

  taatInsight:    (deviceId, key = "")               => apiFetch(`/widgets/data/${deviceId}/taat_insight?key=${encodeURIComponent(key)}`),
  // Catalogue — returns all supported types and their params
  types:          ()                                  => apiFetch("/widgets/types"),
};
