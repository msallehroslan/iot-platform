@tailwind base;
@tailwind components;
@tailwind utilities;

*, *::before, *::after { box-sizing: border-box; }

body {
  margin: 0;
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: #F4F8FF;
  color: #0B1426;
}

::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

/* ── react-grid-layout ─────────────────────────────────────────────────────── */
/* These two stylesheets are required for react-grid-layout to render correctly */

/* Core grid layout styles */
.react-grid-layout {
  position: relative;
  transition: height 200ms ease;
}
.react-grid-item {
  transition: all 200ms ease;
  transition-property: left, top, width, height;
}
.react-grid-item.cssTransforms {
  transition-property: transform, width, height;
}
.react-grid-item.resizing {
  transition: none;
  z-index: 1;
  will-change: width, height;
}
.react-grid-item.react-draggable-dragging {
  transition: none;
  z-index: 3;
  will-change: transform;
}
.react-grid-item.dropping {
  visibility: hidden;
}
.react-grid-item.react-grid-placeholder {
  background: #2F8CFF;
  opacity: 0.12;
  border: 2px dashed #2F8CFF;
  border-radius: 12px;
  transition-duration: 100ms;
  z-index: 2;
  user-select: none;
}
.react-grid-item > .react-resizable-handle {
  position: absolute;
  width: 24px;
  height: 24px;
  bottom: 0;
  right: 0;
  cursor: se-resize;
  z-index: 10;
}
.react-grid-item > .react-resizable-handle::after {
  content: "";
  position: absolute;
  right: 5px;
  bottom: 5px;
  width: 8px;
  height: 8px;
  border-right: 2px solid #94a3b8;
  border-bottom: 2px solid #94a3b8;
  border-radius: 1px;
  transition: border-color 0.15s;
}
.react-grid-item:hover > .react-resizable-handle::after {
  border-color: #2F8CFF;
}
.react-resizable-hide > .react-resizable-handle {
  display: none;
}

/* Drag handle cursor on widget header */
.widget-drag-handle {
  cursor: grab;
}
.widget-drag-handle:active {
  cursor: grabbing;
}
