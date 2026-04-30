/**
 * services/userDashboardService.js
 *
 * Business logic for Phase 2 user-scoped multi-dashboard management.
 * Components import from here, never from userDashboardsHttp directly.
 * All data comes from the backend API — no mock data, no hardcoded state.
 */
import { userDashboardsHttp } from "./api.js";

// ── Dashboard CRUD ────────────────────────────────────────────────────────────

/**
 * List all dashboards for the current user.
 * Returns lightweight list items (no widgets array).
 * Auto-creates a 'Default Dashboard' server-side if the user has none.
 * @returns {Promise<Array<{id, name, is_default, widget_count, created_at}>>}
 */
export async function listUserDashboards() {
  return userDashboardsHttp.list();
}

/**
 * Get the user's default dashboard including all widgets.
 * Called on app load to immediately show the right dashboard.
 * @returns {Promise<Object>} full dashboard with widgets array
 */
export async function getDefaultDashboard() {
  return userDashboardsHttp.getDefault();
}

/**
 * Load a specific dashboard by ID, including all widgets.
 * Called when the user clicks a dashboard in the sidebar.
 * @returns {Promise<Object>} full dashboard with widgets array
 */
export async function getUserDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.get(dashboardId);
}

/**
 * Create a new dashboard.
 * @returns {Promise<Object>} created dashboard (is_default = false, no widgets)
 */
export async function createUserDashboard(name, description = null) {
  if (!name?.trim()) throw new Error("Dashboard name is required");
  return userDashboardsHttp.create({ name: name.trim(), description });
}

/**
 * Rename a dashboard.
 * @returns {Promise<Object>} updated dashboard list item
 */
export async function renameUserDashboard(dashboardId, name) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!name?.trim()) throw new Error("Name cannot be empty");
  return userDashboardsHttp.rename(dashboardId, name.trim());
}

/**
 * Set a dashboard as the user's default.
 * The backend guarantees exactly one default per user.
 * @returns {Promise<Object>} updated dashboard list item
 */
export async function setDefaultDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.setDefault(dashboardId);
}

/**
 * Delete a dashboard and all its widgets.
 * Throws if it's the user's only dashboard.
 */
export async function deleteUserDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.delete(dashboardId);
}

// ── Widget CRUD ───────────────────────────────────────────────────────────────

export async function addUserWidget(dashboardId, widget) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!widget.widget_type) throw new Error("widget_type is required");
  return userDashboardsHttp.addWidget(dashboardId, {
    widget_type: widget.widget_type,
    title:       (widget.title || widget.widget_type).trim(),
    config:      widget.config   || {},
    position:    widget.position || { x: 0, y: 0, w: 2, h: 3 },
  });
}

export async function updateUserWidget(dashboardId, widgetId, updates) {
  if (!dashboardId || !widgetId) throw new Error("dashboardId and widgetId required");
  const body = {};
  if (updates.widget_type !== undefined) body.widget_type = updates.widget_type;
  if (updates.title       !== undefined) body.title       = updates.title;
  if (updates.config      !== undefined) body.config      = updates.config;
  if (updates.position    !== undefined) body.position    = updates.position;
  return userDashboardsHttp.updateWidget(dashboardId, widgetId, body);
}

export async function deleteUserWidget(dashboardId, widgetId) {
  if (!dashboardId || !widgetId) throw new Error("dashboardId and widgetId required");
  return userDashboardsHttp.deleteWidget(dashboardId, widgetId);
}

export async function saveUserLayout(dashboardId, layout) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.saveLayout(dashboardId, layout);
}
