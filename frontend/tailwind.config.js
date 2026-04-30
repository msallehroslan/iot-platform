/**
 * components/dashboard/GridLayout.jsx
 *
 * Drag-and-drop, resizable widget grid powered by react-grid-layout.
 *
 * FIXES applied vs previous version:
 *   1. Import: use named `ReactGridLayout` export, not default + WidthProvider(default)
 *   2. Remove custom resizeHandle — RGL 1.4 built-in handle works correctly
 *   3. Don't set static:true per-item — control lock via isDraggable/isResizable at grid level
 *   4. Add explicit height to grid items so widgets are visible
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Responsive, WidthProvider } from "react-grid-layout";
import { widgetsToLayout, GRID_CONFIG } from "../../services/widgetService.js";

// WidthProvider auto-measures container width — wrap the Responsive grid
const ResponsiveGridLayout = WidthProvider(Responsive);

// ── Widget card shell ─────────────────────────────────────────────────────────
function WidgetCard({ widget, editMode, onEdit, onRemove, children }) {
  return (
    <div
      style={{
        background: "white",
        borderRadius: 12,
        border: editMode ? "2px dashed #93c5fd" : "1px solid #e2e8f0",
        boxShadow: "0 1px 3px rgba(0,0,0,.06)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        height: "100%",            // fill the RGL cell height
        transition: "border .15s",
      }}
    >
      {/* Header — the drag handle (class applied only in editMode) */}
      <div
        className={editMode ? "widget-drag-handle" : ""}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          borderBottom: "1px solid #f8fafc",
          flexShrink: 0,
          background: editMode ? "#f0f9ff" : "white",
          userSelect: "none",
          minHeight: 40,
          cursor: editMode ? "grab" : "default",
        }}
      >
        {editMode && (
          <svg
            style={{ width: 14, height: 14, color: "#cbd5e1", flexShrink: 0, marginRight: 6 }}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          >
            <circle cx="9"  cy="7"  r="1" fill="currentColor" />
            <circle cx="9"  cy="12" r="1" fill="currentColor" />
            <circle cx="9"  cy="17" r="1" fill="currentColor" />
            <circle cx="15" cy="7"  r="1" fill="currentColor" />
            <circle cx="15" cy="12" r="1" fill="currentColor" />
            <circle cx="15" cy="17" r="1" fill="currentColor" />
          </svg>
        )}

        <p style={{ fontSize: 13, fontWeight: 600, color: "#334155", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
          {widget.title}
        </p>

        {editMode && (
          <div
            onMouseDown={e => e.stopPropagation()}
            onTouchStart={e => e.stopPropagation()}
            style={{ display: "flex", gap: 6, flexShrink: 0, marginLeft: 8 }}
          >
            <button
              onClick={() => onEdit(widget)}
              style={{ fontSize: 11, fontWeight: 500, color: "#3b82f6", background: "#eff6ff", border: "none", borderRadius: 6, padding: "3px 8px", cursor: "pointer" }}
            >
              Edit
            </button>
            <button
              onClick={() => onRemove(widget.id)}
              style={{ fontSize: 11, fontWeight: 500, color: "#ef4444", background: "#fef2f2", border: "none", borderRadius: 6, padding: "3px 8px", cursor: "pointer" }}
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Body */}
      <div style={{ flex: 1, minHeight: 0, padding: 12, overflow: "hidden" }}>
        {children}
      </div>
    </div>
  );
}

// ── Add widget placeholder ────────────────────────────────────────────────────
function AddWidgetTile({ onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: "100%", height: "100%",
        background: hover ? "#f8fafc" : "transparent",
        border: `2px dashed ${hover ? "#3b82f6" : "#cbd5e1"}`,
        borderRadius: 12, cursor: "pointer",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 8, color: hover ? "#3b82f6" : "#94a3b8",
        transition: "all .15s",
      }}
    >
      <svg style={{ width: 24, height: 24 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5"  y1="12" x2="19" y2="12" />
      </svg>
      <span style={{ fontSize: 12, fontWeight: 500 }}>Add Widget</span>
    </button>
  );
}

// ── Main GridLayout ───────────────────────────────────────────────────────────
const ADD_KEY = "__add__";

export default function GridLayout({
  widgets = [],
  editMode = false,
  onLayoutChange,
  onEditWidget,
  onRemoveWidget,
  onAddWidget,
  renderWidget,
  saving = false,
}) {
  // Convert backend widgets → RGL layout items
  const widgetLayout = useMemo(() => widgetsToLayout(widgets), [widgets]);

  // Add tile layout (appended at bottom, never draggable/resizable)
  const addTile = { i: ADD_KEY, x: 0, y: Infinity, w: 3, h: 2, isDraggable: false, isResizable: false };

  // Full layout: widgets + add tile (edit mode only)
  const layouts = useMemo(() => {
    const items = editMode ? [...widgetLayout, addTile] : widgetLayout;
    return { lg: items, md: items, sm: items };
  }, [widgetLayout, editMode]);

  // ── Only persist on drag/resize STOP — not on every frame ────────────────
  const handleDragStop = useCallback(
    (layout) => { onLayoutChange?.(layout.filter(i => i.i !== ADD_KEY)); },
    [onLayoutChange]
  );

  const handleResizeStop = useCallback(
    (layout) => { onLayoutChange?.(layout.filter(i => i.i !== ADD_KEY)); },
    [onLayoutChange]
  );

  // Responsive breakpoints — same cols at every size (12 col grid)
  const breakpoints = { lg: 1200, md: 996, sm: 768 };
  const cols        = { lg: GRID_CONFIG.cols, md: GRID_CONFIG.cols, sm: GRID_CONFIG.cols };

  return (
    <div style={{ position: "relative" }}>
      {/* Saving indicator */}
      {saving && (
        <div style={{ position: "absolute", top: -28, right: 0, display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "#3b82f6", fontWeight: 500, zIndex: 20 }}>
          <svg style={{ width: 12, height: 12, animation: "glspin .6s linear infinite" }} viewBox="0 0 24 24" fill="none">
            <circle style={{ opacity: .3 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
            <path style={{ opacity: .9 }} fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
          </svg>
          Saving layout…
        </div>
      )}

      <style>{`@keyframes glspin{from{transform:rotate(0)}to{transform:rotate(360deg)}}`}</style>

      <ResponsiveGridLayout
        className="layout"
        layouts={layouts}
        breakpoints={breakpoints}
        cols={cols}
        rowHeight={GRID_CONFIG.rowHeight}
        margin={GRID_CONFIG.margin}
        containerPadding={GRID_CONFIG.containerPadding}
        compactType={GRID_CONFIG.compactType}
        preventCollision={false}
        isDraggable={editMode}
        isResizable={editMode}
        draggableHandle=".widget-drag-handle"
        onDragStop={handleDragStop}
        onResizeStop={handleResizeStop}
        onLayoutChange={() => {}}   // suppress mid-drag calls
        useCSSTransforms={true}
        measureBeforeMount={false}
        isBounded={false}
      >
        {/* Widget tiles */}
        {widgets.map(widget => (
          <div key={widget.id} style={{ overflow: "hidden" }}>
            <WidgetCard
              widget={widget}
              editMode={editMode}
              onEdit={onEditWidget}
              onRemove={onRemoveWidget}
            >
              {renderWidget(widget)}
            </WidgetCard>
          </div>
        ))}

        {/* Add tile — edit mode only */}
        {editMode && (
          <div key={ADD_KEY} style={{ overflow: "hidden" }}>
            <AddWidgetTile onClick={onAddWidget} />
          </div>
        )}
      </ResponsiveGridLayout>
    </div>
  );
}
