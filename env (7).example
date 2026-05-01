/**
 * dashboardService.js — business logic for dashboards + widgets.
 * Components call these functions, never dashboardsHttp directly.
 * All data comes from the backend. No mock data. No local state.
 */
import { dashboardsHttp, telemetryApi, alarmApi } from "./api.js";

// ── Dashboard CRUD ────────────────────────────────────────────────────────────

/**
 * Fetch all dashboards for a device.
 * @returns {Promise<Array>} list of dashboard objects (no widgets array)
 */
export async function listDashboards(deviceId) {
  if (!deviceId) throw new Error("deviceId is required");
  return dashboardsHttp.list(deviceId);
}

/**
 * Create a new dashboard for a device.
 * @returns {Promise<Object>} created dashboard with empty widgets array
 */
export async function createDashboard(deviceId, name, options = {}) {
  if (!deviceId) throw new Error("deviceId is required");
  if (!name?.trim()) throw new Error("Dashboard name is required");
  return dashboardsHttp.create({
    device_id: deviceId,
    name:        name.trim(),
    description: options.description || null,
    is_default:  options.isDefault   || false,
  });
}

/**
 * Load a single dashboard including all its widgets.
 * @returns {Promise<Object>} dashboard with widgets array
 */
export async function getDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return dashboardsHttp.get(dashboardId);
}

/**
 * Rename or update a dashboard.
 */
export async function updateDashboard(dashboardId, updates) {
  if (!dashboardId) throw new Error("dashboardId is required");
  const body = {};
  if (updates.name        !== undefined) body.name        = updates.name;
  if (updates.description !== undefined) body.description = updates.description;
  if (updates.isDefault   !== undefined) body.is_default  = updates.isDefault;
  return dashboardsHttp.update(dashboardId, body);
}

/**
 * Delete a dashboard and all its widgets.
 */
export async function deleteDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return dashboardsHttp.delete(dashboardId);
}

// ── Widget CRUD ───────────────────────────────────────────────────────────────

/**
 * Add a widget to a dashboard.
 * @param {string} dashboardId
 * @param {Object} widget - { widget_type, title, config, position }
 * @returns {Promise<Object>} created widget
 */
export async function addWidget(dashboardId, widget) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!widget.widget_type) throw new Error("widget_type is required");
  return dashboardsHttp.addWidget(dashboardId, {
    widget_type: widget.widget_type,
    title:       (widget.title || widget.widget_type).trim(),
    config:      widget.config   || {},
    position:    widget.position || { x: 0, y: 0, w: 2, h: 3 },
  });
}

/**
 * Update widget config, title, type, or position.
 */
export async function updateWidget(dashboardId, widgetId, updates) {
  if (!dashboardId || !widgetId) throw new Error("dashboardId and widgetId required");
  const body = {};
  if (updates.widget_type !== undefined) body.widget_type = updates.widget_type;
  if (updates.title       !== undefined) body.title       = updates.title;
  if (updates.config      !== undefined) body.config      = updates.config;
  if (updates.position    !== undefined) body.position    = updates.position;
  return dashboardsHttp.updateWidget(dashboardId, widgetId, body);
}

/**
 * Remove a widget.
 */
export async function deleteWidget(dashboardId, widgetId) {
  if (!dashboardId || !widgetId) throw new Error("dashboardId and widgetId required");
  return dashboardsHttp.deleteWidget(dashboardId, widgetId);
}

/**
 * Bulk-save widget grid positions after drag-and-drop.
 * @param {Array<{id,x,y,w,h}>} layout
 */
export async function saveLayout(dashboardId, layout) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return dashboardsHttp.saveLayout(dashboardId, layout);
}

// ── Telemetry helpers (used by widgets) ───────────────────────────────────────

/**
 * Get latest telemetry values for a device as a flat key→value map.
 * @returns {Promise<Object>} e.g. { temperature: 72.4, humidity: 65 }
 */
export async function getLatestTelemetry(deviceId) {
  if (!deviceId) return {};
  const rows = await telemetryApi.latest(deviceId);
  const map = {};
  (rows || []).forEach(r => { map[r.key] = r.value; });
  return map;
}

/**
 * Get telemetry history for a single key.
 * @returns {Promise<Array<{ts, value}>>}
 */
export async function getTelemetryHistory(deviceId, key, limit = 50) {
  if (!deviceId || !key) return [];
  return telemetryApi.history(deviceId, key, limit);
}

/**
 * Get all telemetry keys for a device.
 * @returns {Promise<string[]>}
 */
export async function getTelemetryKeys(deviceId) {
  if (!deviceId) return [];
  const res = await telemetryApi.keys(deviceId);
  return res?.keys || [];
}

/**
 * Get active alarms for a device.
 */
export async function getDeviceAlarms(deviceId) {
  if (!deviceId) return [];
  return alarmApi.list({ device_id: deviceId, limit: 20 });
}
