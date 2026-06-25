import { userDashboardsHttp } from "./api.js";

export async function listUserDashboards() { return userDashboardsHttp.list(); }
export async function getDefaultDashboard() { return userDashboardsHttp.getDefault(); }
export async function getUserDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.get(dashboardId);
}
export async function createUserDashboard(name, description = null) {
  if (!name?.trim()) throw new Error("Dashboard name is required");
  return userDashboardsHttp.create({ name: name.trim(), description });
}
export async function renameUserDashboard(dashboardId, name) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!name?.trim()) throw new Error("Name cannot be empty");
  return userDashboardsHttp.rename(dashboardId, name.trim());
}
export async function setDefaultDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.setDefault(dashboardId);
}
export async function deleteUserDashboard(dashboardId) {
  if (!dashboardId) throw new Error("dashboardId is required");
  return userDashboardsHttp.delete(dashboardId);
}
export async function addUserWidget(dashboardId, widget) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!widget.widget_type) throw new Error("widget_type is required");
  return userDashboardsHttp.addWidget(dashboardId, {
    widget_type: widget.widget_type,
    title: (widget.title || widget.widget_type).trim(),
    config: widget.config || {},
    position: widget.position || { x: 0, y: 0, w: 2, h: 3 },
  });
}
export async function updateUserWidget(dashboardId, widgetId, updates) {
  if (!dashboardId || !widgetId) throw new Error("dashboardId and widgetId required");
  const body = {};
  if (updates.widget_type !== undefined) body.widget_type = updates.widget_type;
  if (updates.title !== undefined) body.title = updates.title;
  if (updates.config !== undefined) body.config = updates.config;
  if (updates.position !== undefined) body.position = updates.position;
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
