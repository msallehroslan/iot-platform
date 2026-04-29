/**
 * services/widgetService.js
 *
 * Layout conversion + persistence between the backend position format
 * and react-grid-layout's format.
 *
 * Backend:  { x, y, w, h }        (integers, stored in position JSON column)
 * RGL:      { i, x, y, w, h }     (i = widget id string)
 *
 * Both dashboard types (device-scoped and user-scoped) share ALL helpers here.
 * persistLayout() accepts a saveLayoutFn so callers inject the correct HTTP
 * client without duplicating any conversion logic:
 *
 *   // Device dashboard  → PUT /dashboards/{id}/layout
 *   persistLayout(dashboardId, rglLayout, dashboardsHttp.saveLayout)
 *
 *   // User dashboard    → PUT /user-dashboards/{id}/layout
 *   persistLayout(dashboardId, rglLayout, userDashboardsHttp.saveLayout)
 */
import { userDashboardsHttp } from "./api.js";

// ── Format converters ─────────────────────────────────────────────────────────

/**
 * Convert backend widget array → RGL layout array.
 * Guards against null/undefined positions with safe defaults.
 */
export function widgetsToLayout(widgets) {
  return widgets.map((w, idx) => ({
    i: w.id,
    x: Number.isFinite(w.position?.x) ? w.position.x : (idx % 4) * 3,
    y: Number.isFinite(w.position?.y) ? w.position.y : Math.floor(idx / 4) * 3,
    w: Number.isFinite(w.position?.w) && w.position.w > 0 ? w.position.w : 3,
    h: Number.isFinite(w.position?.h) && w.position.h > 0 ? w.position.h : 3,
  }));
}

/**
 * Convert RGL layout → backend bulk-save payload.
 * Strips the __add__ tile and clamps negatives.
 */
export function layoutToPositions(layout) {
  return layout
    .filter(item => item.i !== "__add__")
    .map(item => ({
      id: item.i,
      x:  Math.max(0, item.x),
      y:  Math.max(0, item.y),
      w:  Math.max(1, item.w),
      h:  Math.max(1, item.h),
    }));
}

/**
 * Apply a new RGL layout back onto local widget state (optimistic update).
 * Called immediately after dragStop/resizeStop before the API responds.
 */
export function applyLayoutToWidgets(widgets, rglLayout) {
  const posMap = {};
  rglLayout.forEach(item => {
    posMap[item.i] = { x: item.x, y: item.y, w: item.w, h: item.h };
  });
  return widgets.map(w => ({
    ...w,
    position: posMap[w.id] ? { ...posMap[w.id] } : w.position,
  }));
}

// ── Layout persistence ────────────────────────────────────────────────────────

/**
 * Persist layout to the backend.
 * Called ONLY on dragStop / resizeStop — never during drag.
 *
 * @param {string}   dashboardId
 * @param {Array<{i,x,y,w,h}>} rglLayout  — from RGL stop event
 * @param {Function} [saveLayoutFn]        — optional override for which HTTP
 *   endpoint to call. Defaults to userDashboardsHttp.saveLayout so existing
 *   UserDashboardPage callers need no change.
 *
 *   Device dashboard passes:  dashboardsHttp.saveLayout
 *   User  dashboard passes:   nothing (default used)
 */
export async function persistLayout(dashboardId, rglLayout, saveLayoutFn) {
  if (!dashboardId) throw new Error("dashboardId is required");
  if (!rglLayout?.length) return { updated: [], count: 0 };
  const positions = layoutToPositions(rglLayout);
  const fn = saveLayoutFn ?? userDashboardsHttp.saveLayout;
  return fn(dashboardId, positions);
}

// ── Grid configuration ────────────────────────────────────────────────────────

export const GRID_CONFIG = {
  cols:             12,
  rowHeight:        80,      // px — each h unit = 80px + margin
  margin:           [12, 12],
  containerPadding: [0, 0],
  compactType:      "vertical",
};

/**
 * Sensible default size for each widget type when first added.
 * Uses y: Infinity so RGL appends at the first free row.
 */
export function getDefaultPositionForType(widgetType) {
  const sizes = {
    value_card:       { w: 3, h: 2 },
    line_chart:       { w: 6, h: 4 },
    gauge:            { w: 3, h: 3 },
    status_light:     { w: 2, h: 3 },
    bar_chart:        { w: 5, h: 3 },
    alarm_list:       { w: 4, h: 4 },
    timeseries_table: { w: 5, h: 4 },
    pie_chart:        { w: 4, h: 3 },
    markdown:         { w: 3, h: 2 },
    entity_table:     { w: 6, h: 4 },
    html_card:        { w: 4, h: 3 },
  };
  // y: 9999 tells RGL to place at the bottom row (Infinity is invalid JSON → null → 422).
  return { x: 0, y: 9999, ...(sizes[widgetType] || { w: 4, h: 3 }) };
}
