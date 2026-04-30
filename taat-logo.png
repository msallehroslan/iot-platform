/**
 * components/sidebar/DashboardSidebar.jsx
 *
 * Left-panel sidebar for ThingsBoard-style multi-dashboard management.
 *
 * Features:
 *  - Lists all user dashboards from GET /user-dashboards/
 *  - Highlights the active dashboard
 *  - Click → loads that dashboard (calls onSelect)
 *  - ➕ New Dashboard button with inline name input
 *  - Per-dashboard ⋮ menu: Rename / Set as Default / Delete
 *  - Default badge on the current default
 *  - Loading / error states
 *  - Keyboard: Enter to confirm inline edits, Escape to cancel
 *
 * Props:
 *  - dashboards: Array      — list from listUserDashboards()
 *  - activeDashboardId: str — currently loaded dashboard id
 *  - onSelect(id): fn       — called when user clicks a dashboard
 *  - onCreate(name): fn     — called with new dashboard name
 *  - onRename(id, name): fn
 *  - onSetDefault(id): fn
 *  - onDelete(id): fn
 *  - loading: bool          — show skeleton while fetching
 *  - error: string          — error message to display
 */
import { useState, useRef, useEffect } from "react";

// ── Icon helpers ──────────────────────────────────────────────────────────────
const Icon = ({ path, size = 14, className = "" }) => (
  <svg
    style={{ width: size, height: size, flexShrink: 0 }}
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d={path} />
  </svg>
);

const ICONS = {
  dashboard:   "M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z",
  plus:        "M12 5v14M5 12h14",
  dots:        "M12 5h.01M12 12h.01M12 19h.01",
  rename:      "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z",
  star:        "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z",
  trash:       "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  check:       "M20 6 9 17l-5-5",
  x:           "M18 6 6 18M6 6l12 12",
  chevronDown: "M6 9l6 6 6-6",
  warning:     "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01",
};

// ── Dashboard menu ────────────────────────────────────────────────────────────
function DashboardMenu({ dashboard, onRename, onSetDefault, onDelete, onClose }) {
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const items = [
    {
      label: "Rename",
      icon: ICONS.rename,
      action: () => { onRename(); onClose(); },
    },
    ...(!dashboard.is_default ? [{
      label: "Set as Default",
      icon: ICONS.star,
      action: () => { onSetDefault(); onClose(); },
    }] : []),
    {
      label: "Delete",
      icon: ICONS.trash,
      action: () => { onDelete(); onClose(); },
      danger: true,
    },
  ];

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        right: 0,
        top: "calc(100% + 4px)",
        background: "white",
        border: "1px solid #e2e8f0",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(0,0,0,.12)",
        zIndex: 200,
        minWidth: 160,
        overflow: "hidden",
        padding: "4px 0",
      }}
    >
      {items.map((item) => (
        <button
          key={item.label}
          onClick={item.action}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 14px",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 500,
            color: item.danger ? "#ef4444" : "#374151",
            textAlign: "left",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = item.danger ? "#fef2f2" : "#f8fafc"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}
        >
          <Icon path={item.icon} size={13} />
          {item.label}
        </button>
      ))}
    </div>
  );
}

// ── Single dashboard row ──────────────────────────────────────────────────────
function DashboardRow({
  dashboard, isActive, onSelect, onRename, onSetDefault, onDelete,
  renaming, onRenameSubmit, onRenameCancel,
}) {
  const [menuOpen,   setMenuOpen]   = useState(false);
  const [renameVal,  setRenameVal]  = useState(dashboard.name);
  const inputRef = useRef(null);

  useEffect(() => {
    if (renaming && inputRef.current) {
      setRenameVal(dashboard.name);
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [renaming]);

  const submitRename = () => {
    if (renameVal.trim() && renameVal.trim() !== dashboard.name) {
      onRenameSubmit(renameVal.trim());
    } else {
      onRenameCancel();
    }
  };

  return (
    <div
      style={{
        position: "relative",
        borderRadius: 8,
        background: isActive ? "rgba(59,130,246,.12)" : "transparent",
        transition: "background .15s",
      }}
    >
      {renaming ? (
        // ── Inline rename input ──
        <div style={{ padding: "4px 6px" }}>
          <input
            ref={inputRef}
            value={renameVal}
            onChange={e => setRenameVal(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter")  submitRename();
              if (e.key === "Escape") onRenameCancel();
            }}
            onBlur={submitRename}
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: 13,
              border: "1.5px solid #3b82f6",
              borderRadius: 6,
              outline: "none",
              background: "white",
              color: "#1e293b",
              boxSizing: "border-box",
            }}
          />
        </div>
      ) : (
        // ── Normal row ──
        <div
          style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", cursor: "pointer" }}
          onClick={() => onSelect(dashboard.id)}
        >
          {/* Icon */}
          <div style={{
            width: 28, height: 28, borderRadius: 6, flexShrink: 0,
            background: isActive ? "rgba(59,130,246,.18)" : "#f1f5f9",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Icon path={ICONS.dashboard} size={13} style={{ color: isActive ? "#3b82f6" : "#64748b" }} />
          </div>

          {/* Name + meta */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{
                fontSize: 13, fontWeight: isActive ? 600 : 400,
                color: isActive ? "#1d4ed8" : "#374151",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                maxWidth: 110,
              }}>
                {dashboard.name}
              </span>
              {dashboard.is_default && (
                <span style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: ".04em",
                  background: "#fef3c7", color: "#92400e",
                  border: "1px solid #fde68a", borderRadius: 4,
                  padding: "0 4px", flexShrink: 0,
                }}>
                  DEFAULT
                </span>
              )}
            </div>
            <span style={{ fontSize: 10, color: "#94a3b8" }}>
              {dashboard.widget_count ?? 0} widget{dashboard.widget_count !== 1 ? "s" : ""}
            </span>
          </div>

          {/* ⋮ menu button */}
          <div style={{ position: "relative", flexShrink: 0 }}>
            <button
              onClick={e => { e.stopPropagation(); setMenuOpen(o => !o); }}
              style={{
                width: 24, height: 24, border: "none", background: "transparent",
                borderRadius: 4, cursor: "pointer", display: "flex",
                alignItems: "center", justifyContent: "center",
                color: "#94a3b8",
                opacity: isActive ? 1 : 0,
              }}
              className="row-menu-btn"
            >
              <Icon path={ICONS.dots} size={13} />
            </button>

            {menuOpen && (
              <DashboardMenu
                dashboard={dashboard}
                onRename={onRename}
                onSetDefault={onSetDefault}
                onDelete={onDelete}
                onClose={() => setMenuOpen(false)}
              />
            )}
          </div>
        </div>
      )}

      {/* Show menu button on hover via CSS */}
      <style>{`
        .dash-row:hover .row-menu-btn { opacity: 1 !important; }
      `}</style>
    </div>
  );
}

// ── Skeleton loader ───────────────────────────────────────────────────────────
function Skeleton() {
  return (
    <div style={{ padding: "4px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
      {[80, 65, 90, 55].map(w => (
        <div key={w} style={{
          height: 36, borderRadius: 8, background: "#f1f5f9",
          animation: "shimmer 1.4s ease-in-out infinite",
          width: `${w}%`,
        }} />
      ))}
      <style>{`
        @keyframes shimmer {
          0%,100% { opacity: 1 }
          50%      { opacity: .4 }
        }
      `}</style>
    </div>
  );
}

// ── Main DashboardSidebar ─────────────────────────────────────────────────────
export default function DashboardSidebar({
  dashboards = [],
  activeDashboardId,
  onSelect,
  onCreate,
  onRename,
  onSetDefault,
  onDelete,
  loading = false,
  error = "",
}) {
  const [creating,    setCreating]    = useState(false);
  const [newName,     setNewName]     = useState("");
  const [renamingId,  setRenamingId]  = useState(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState(null);
  const [collapsed,   setCollapsed]   = useState(false);
  const createInputRef = useRef(null);

  useEffect(() => {
    if (creating && createInputRef.current) {
      createInputRef.current.focus();
    }
  }, [creating]);

  const handleCreate = () => {
    if (!newName.trim()) { setCreating(false); return; }
    onCreate(newName.trim());
    setNewName("");
    setCreating(false);
  };

  const handleDeleteClick = (id) => {
    if (deleteConfirmId === id) {
      onDelete(id);
      setDeleteConfirmId(null);
    } else {
      setDeleteConfirmId(id);
      // Auto-cancel after 3 s
      setTimeout(() => setDeleteConfirmId(c => c === id ? null : c), 3000);
    }
  };

  return (
    <aside style={{
      width: collapsed ? 44 : 220,
      flexShrink: 0,
      background: "#0f172a",
      borderRight: "1px solid #1e293b",
      display: "flex",
      flexDirection: "column",
      height: "100%",
      transition: "width .2s ease",
      overflow: "hidden",
    }}>
      {/* ── Header ── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: collapsed ? "14px 10px" : "14px 12px",
        borderBottom: "1px solid #1e293b",
        flexShrink: 0,
      }}>
        {!collapsed && (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 26, height: 26, borderRadius: 6,
              background: "rgba(59,130,246,.2)",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <Icon path={ICONS.dashboard} size={13} style={{ color: "#93c5fd" }} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#e2e8f0", letterSpacing: ".02em" }}>
              Dashboards
            </span>
          </div>
        )}

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(c => !c)}
          style={{
            border: "none", background: "transparent", cursor: "pointer",
            color: "#475569", padding: 4, borderRadius: 4,
            display: "flex", alignItems: "center",
          }}
          title={collapsed ? "Expand" : "Collapse"}
        >
          <svg style={{ width: 14, height: 14, transform: collapsed ? "rotate(180deg)" : "none", transition: "transform .2s" }}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      </div>

      {/* ── Dashboard list ── */}
      {!collapsed && (
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 6px" }}>
          {/* Error */}
          {error && (
            <div style={{
              margin: "4px 2px 8px", padding: "8px 10px",
              background: "#450a0a", border: "1px solid #7f1d1d",
              borderRadius: 8, display: "flex", alignItems: "center", gap: 6,
            }}>
              <Icon path={ICONS.warning} size={12} style={{ color: "#fca5a5", flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: "#fca5a5", lineHeight: 1.4 }}>{error}</span>
            </div>
          )}

          {/* Loading skeleton */}
          {loading && !dashboards.length ? (
            <Skeleton />
          ) : (
            dashboards.map(d => (
              <div key={d.id} className="dash-row">
                <DashboardRow
                  dashboard={d}
                  isActive={d.id === activeDashboardId}
                  onSelect={onSelect}
                  onRename={() => setRenamingId(d.id)}
                  onSetDefault={() => onSetDefault(d.id)}
                  onDelete={() => handleDeleteClick(d.id)}
                  renaming={renamingId === d.id}
                  onRenameSubmit={(name) => { onRename(d.id, name); setRenamingId(null); }}
                  onRenameCancel={() => setRenamingId(null)}
                />

                {/* Delete confirmation */}
                {deleteConfirmId === d.id && (
                  <div style={{
                    margin: "2px 8px 6px",
                    padding: "8px 10px",
                    background: "#450a0a", border: "1px solid #7f1d1d",
                    borderRadius: 8, fontSize: 11, color: "#fca5a5",
                  }}>
                    <p style={{ margin: "0 0 6px", fontWeight: 500 }}>
                      Delete "{d.name}" and all its widgets?
                    </p>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        onClick={() => { onDelete(d.id); setDeleteConfirmId(null); }}
                        style={{ flex: 1, padding: "4px", borderRadius: 6, border: "none", background: "#ef4444", color: "white", fontSize: 11, fontWeight: 600, cursor: "pointer" }}
                      >
                        Delete
                      </button>
                      <button
                        onClick={() => setDeleteConfirmId(null)}
                        style={{ flex: 1, padding: "4px", borderRadius: 6, border: "1px solid #7f1d1d", background: "transparent", color: "#fca5a5", fontSize: 11, cursor: "pointer" }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}

          {/* Empty state */}
          {!loading && dashboards.length === 0 && !error && (
            <div style={{ padding: "24px 12px", textAlign: "center" }}>
              <p style={{ fontSize: 12, color: "#475569", margin: 0 }}>No dashboards yet</p>
            </div>
          )}
        </div>
      )}

      {/* ── Create new dashboard ── */}
      {!collapsed && (
        <div style={{
          padding: "10px 8px", borderTop: "1px solid #1e293b", flexShrink: 0,
        }}>
          {creating ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <input
                ref={createInputRef}
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter")  handleCreate();
                  if (e.key === "Escape") { setCreating(false); setNewName(""); }
                }}
                placeholder="Dashboard name…"
                style={{
                  width: "100%", padding: "7px 10px", fontSize: 12,
                  border: "1.5px solid #3b82f6", borderRadius: 8, outline: "none",
                  background: "#1e293b", color: "#e2e8f0",
                  boxSizing: "border-box",
                }}
              />
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={handleCreate}
                  style={{
                    flex: 1, padding: "6px", borderRadius: 6, border: "none",
                    background: "#3b82f6", color: "white", fontSize: 12,
                    fontWeight: 600, cursor: "pointer",
                  }}
                >
                  Create
                </button>
                <button
                  onClick={() => { setCreating(false); setNewName(""); }}
                  style={{
                    padding: "6px 10px", borderRadius: 6,
                    border: "1px solid #334155", background: "transparent",
                    color: "#94a3b8", fontSize: 12, cursor: "pointer",
                  }}
                >
                  ✕
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setCreating(true)}
              style={{
                width: "100%", display: "flex", alignItems: "center", justifyContent: "center",
                gap: 6, padding: "8px", border: "1px dashed #334155", borderRadius: 8,
                background: "transparent", cursor: "pointer", color: "#64748b",
                fontSize: 12, fontWeight: 500, transition: "all .15s",
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = "#3b82f6"; e.currentTarget.style.color = "#3b82f6"; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = "#334155"; e.currentTarget.style.color = "#64748b"; }}
            >
              <Icon path={ICONS.plus} size={12} />
              New Dashboard
            </button>
          )}
        </div>
      )}

      {/* Collapsed add button */}
      {collapsed && (
        <div style={{ padding: "8px 6px", flexShrink: 0 }}>
          <button
            onClick={() => { setCollapsed(false); setCreating(true); }}
            title="New Dashboard"
            style={{
              width: "100%", padding: "8px", border: "1px dashed #334155",
              borderRadius: 8, background: "transparent", cursor: "pointer",
              color: "#64748b", display: "flex", justifyContent: "center",
            }}
          >
            <Icon path={ICONS.plus} size={14} />
          </button>
        </div>
      )}
    </aside>
  );
}
