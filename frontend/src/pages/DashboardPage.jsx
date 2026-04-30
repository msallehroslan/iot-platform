/**
 * pages/DashboardPage.jsx  —  Device-scoped dashboard
 *
 * Layout:  device header  +  dashboard tabs  +  GridLayout (drag + resize)
 *
 * Data flow (strict — NO mock data):
 *   1. Mount → GET /dashboards/?device_id=  (list tabs)
 *   2. Select tab → GET /dashboards/{id}    (full dashboard with widgets)
 *   3. Widgets rendered via GridLayout; positions come from widget.position JSON
 *   4. DragStop / ResizeStop → persistLayout() → PUT /dashboards/{id}/layout
 *   5. Reload page → positions restored exactly from Postgres
 *
 * Pattern mirrors UserDashboardPage.jsx exactly:
 *   - Same GridLayout component
 *   - Same persistLayout() call (injecting dashboardsHttp.saveLayout)
 *   - Same optimistic-update pattern before the API responds
 *   - Same layoutSaving indicator
 */
import { useState, useEffect, useCallback } from "react";
import {
  listDashboards, createDashboard, getDashboard, updateDashboard,
  deleteDashboard, addWidget, updateWidget, deleteWidget,
  getLatestTelemetry, getTelemetryHistory, getTelemetryKeys, getDeviceAlarms,
} from "../services/dashboardService.js";
import {
  persistLayout, applyLayoutToWidgets, getDefaultPositionForType,
} from "../services/widgetService.js";
import { dashboardsHttp } from "../services/api.js";       // injected into persistLayout
import { TelemetrySocket } from "../services/websocket.js";
import GridLayout from "../components/dashboard/GridLayout.jsx";
import { WidgetRenderer, WIDGET_REGISTRY } from "../components/widgets/index.jsx";

const ACCENT_COLORS = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16"];

// ── Spinner ───────────────────────────────────────────────────────────────────
function Spinner() {
  return (
    <svg style={{ width: 16, height: 16, animation: "spin .7s linear infinite", color: "#94a3b8" }} viewBox="0 0 24 24" fill="none">
      <circle style={{ opacity: .25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
      <path  style={{ opacity: .75 }} fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/>
    </svg>
  );
}

// ── Add / Edit Widget Modal ───────────────────────────────────────────────────
// The old "Size" picker is removed — initial size now comes from
// getDefaultPositionForType(), matching UserDashboardPage behaviour.
// The user drags to resize after adding, same as the user dashboard.
function WidgetModal({ availableKeys, onSave, onClose, editWidget }) {
  const isEdit = !!editWidget;
  const [step,  setStep]  = useState(isEdit ? 2 : 1);
  const [type,  setType]  = useState(editWidget?.widget_type || "");
  const [title, setTitle] = useState(editWidget?.title || "");
  const [cfg,   setCfg]   = useState(() => {
    const base = {
      key: availableKeys[0] || "temperature",
      label: "", unit: "", color: "#3b82f6",
      min: 0, max: 100, decimals: 1,
      threshold_high: "", keys: [], content: "",
      ...(editWidget?.config || {}),
    };
    // Auto-select all available keys for bar/pie chart when creating a new widget
    const isMultiKey = ["bar_chart", "pie_chart"].includes(editWidget?.widget_type || "");
    if (!editWidget && isMultiKey && availableKeys.length > 0) {
      base.keys = [...availableKeys];
    }
    return base;
  });
  const set = (k, v) => setCfg(c => ({ ...c, [k]: v }));

  const needsKey      = !["alarm_list","markdown","entity_table","html_card","pie_chart"].includes(type);  // bar_chart now needs a key
  const needsMultiKey = ["pie_chart"].includes(type);  // bar_chart now uses single key (time-series)
  const needsContent  = ["markdown","html_card"].includes(type);

  const handleSave = () => {
    const wt   = WIDGET_REGISTRY.find(w => w.id === type);
    const auto = `${wt?.label || type}${cfg.key ? ` · ${cfg.key}` : ""}`;
    // New widgets: sensible default size from widgetService (same as user dashboard)
    // Edited widgets: keep existing position — drag/resize changes it, not this modal
    const position = isEdit ? editWidget.position : getDefaultPositionForType(type);
    onSave({
      widget_type: type,
      title:       title.trim() || auto,
      config:      cfg,
      position,
      ...(isEdit ? { id: editWidget.id } : {}),
    });
  };

  const inp = {
    width: "100%", padding: "8px 12px", borderRadius: 8,
    border: "1px solid #e2e8f0", fontSize: 13, color: "#334155",
    background: "#f8fafc", outline: "none",
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", backdropFilter: "blur(4px)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "white", borderRadius: 20, boxShadow: "0 20px 60px rgba(0,0,0,.2)", width: "100%", maxWidth: 520, maxHeight: "90vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 24px", borderBottom: "1px solid #f1f5f9", flexShrink: 0 }}>
          <div>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: "#0f172a", margin: 0 }}>{isEdit ? "Edit Widget" : "Add Widget"}</h3>
            <p style={{ fontSize: 12, color: "#94a3b8", margin: "2px 0 0" }}>{step === 1 ? "Choose type" : "Configure"}</p>
          </div>
          <button onClick={onClose} style={{ border: "none", background: "#f1f5f9", borderRadius: 8, padding: 6, cursor: "pointer", color: "#64748b" }}>
            <svg style={{ width: 16, height: 16 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
          {step === 1 && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {WIDGET_REGISTRY.map(wt => (
                <button key={wt.id} onClick={() => {
            setType(wt.id);
            // Auto-select all keys for multi-key widgets
            if (["bar_chart", "pie_chart"].includes(wt.id) && availableKeys.length > 0) {
              setCfg(c => ({ ...c, keys: [...availableKeys] }));
            }
            setStep(2);
          }}
                  style={{ padding: 16, borderRadius: 12, border: `2px solid ${type === wt.id ? "#3b82f6" : "#e2e8f0"}`, background: type === wt.id ? "#eff6ff" : "white", cursor: "pointer", textAlign: "left" }}>
                  <svg style={{ width: 20, height: 20, color: "#3b82f6", marginBottom: 8, display: "block" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={wt.icon}/></svg>
                  <p style={{ fontSize: 13, fontWeight: 600, color: "#1e293b", margin: "0 0 2px" }}>{wt.label}</p>
                  <p style={{ fontSize: 10, color: "#94a3b8", margin: 0 }}>{wt.desc}</p>
                </button>
              ))}
            </div>
          )}

          {step === 2 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {!isEdit && (
                <button onClick={() => setStep(1)} style={{ border: "none", background: "none", cursor: "pointer", color: "#3b82f6", fontSize: 12, display: "flex", alignItems: "center", gap: 4, padding: 0, width: "fit-content" }}>
                  <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
                  Back
                </button>
              )}
              <div style={{ padding: "8px 12px", background: "#eff6ff", borderRadius: 8, display: "flex", alignItems: "center", gap: 8 }}>
                <svg style={{ width: 14, height: 14, color: "#3b82f6" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={WIDGET_REGISTRY.find(w => w.id === type)?.icon || ""}/></svg>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#1d4ed8" }}>{WIDGET_REGISTRY.find(w => w.id === type)?.label}</span>
              </div>
              <div>
                <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Widget Title</label>
                <input style={inp} value={title} onChange={e => setTitle(e.target.value)}
                  placeholder={`${WIDGET_REGISTRY.find(w=>w.id===type)?.label || type}${cfg.key ? ` · ${cfg.key}` : ""}`} />
              </div>
              {needsKey && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Telemetry Key</label>
                  <select style={{ ...inp, cursor: "pointer" }} value={cfg.key} onChange={e => set("key", e.target.value)}>
                    {availableKeys.map(k => <option key={k}>{k}</option>)}
                  </select>
                </div>
              )}
              {needsMultiKey && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Keys to display</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {availableKeys.map(k => {
                      const on = (cfg.keys || []).includes(k);
                      return (
                        <label key={k} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 12px", borderRadius: 20, border: `1px solid ${on ? "#3b82f6" : "#e2e8f0"}`, background: on ? "#eff6ff" : "white", cursor: "pointer", fontSize: 12, color: on ? "#1d4ed8" : "#64748b" }}>
                          <input type="checkbox" checked={on}
                            onChange={e => set("keys", e.target.checked ? [...(cfg.keys||[]),k] : (cfg.keys||[]).filter(x=>x!==k))}
                            style={{ display: "none" }} />{k}
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              {needsContent && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                    {type === "html_card" ? "HTML Template (use ${key} for values)" : "Markdown Text"}
                  </label>
                  <textarea style={{ ...inp, resize: "none", fontFamily: "monospace", fontSize: 12 }} rows={5}
                    value={cfg.content || ""} onChange={e => set("content", e.target.value)}
                    placeholder={type === "html_card" ? "<h2>Temp: ${temperature}°C</h2>" : "**Status:** Online\n`key`: value"} />
                </div>
              )}
              {["value_card","gauge","timeseries_table"].includes(type) && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Unit</label>
                    <input style={inp} value={cfg.unit || ""} onChange={e => set("unit", e.target.value)} placeholder="°C" />
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Decimals</label>
                    <input type="number" style={inp} value={cfg.decimals ?? 1} min={0} max={4} onChange={e => set("decimals", parseInt(e.target.value))} />
                  </div>
                </div>
              )}
              {type === "gauge" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Min</label>
                    <input type="number" style={inp} value={cfg.min ?? 0} onChange={e => set("min", parseFloat(e.target.value))} />
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Max</label>
                    <input type="number" style={inp} value={cfg.max ?? 100} onChange={e => set("max", parseFloat(e.target.value))} />
                  </div>
                </div>
              )}
              {type === "value_card" && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Alert threshold (optional)</label>
                  <input type="number" style={inp} value={cfg.threshold_high || ""}
                    placeholder="e.g. 80 → turns red above this value"
                    onChange={e => set("threshold_high", e.target.value ? parseFloat(e.target.value) : "")} />
                </div>
              )}
              {!needsContent && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Accent colour</label>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {ACCENT_COLORS.map(c => (
                      <button key={c} onClick={() => set("color", c)}
                        style={{ width: 28, height: 28, borderRadius: "50%", background: c, border: cfg.color === c ? "3px solid #1e293b" : "2px solid white", boxShadow: "0 1px 3px rgba(0,0,0,.2)", outline: cfg.color === c ? `2px solid ${c}` : "none", outlineOffset: 2, cursor: "pointer" }} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{ padding: "16px 24px", borderTop: "1px solid #f1f5f9", display: "flex", gap: 10, flexShrink: 0 }}>
          {step === 1
            ? <button onClick={onClose} style={{ flex: 1, padding: "10px", borderRadius: 10, border: "1px solid #e2e8f0", background: "transparent", cursor: "pointer", fontSize: 14, color: "#64748b" }}>Cancel</button>
            : <>
                <button onClick={handleSave} style={{ flex: 1, padding: "10px", borderRadius: 10, border: "none", background: "#3b82f6", color: "white", cursor: "pointer", fontSize: 14, fontWeight: 600 }}>
                  {isEdit ? "Save Changes" : "Add Widget"}
                </button>
                <button onClick={onClose} style={{ padding: "10px 16px", borderRadius: 10, border: "1px solid #e2e8f0", background: "transparent", cursor: "pointer", fontSize: 14, color: "#64748b" }}>Cancel</button>
              </>
          }
        </div>
      </div>
    </div>
  );
}

// ── Main Dashboard Page ───────────────────────────────────────────────────────
export default function DashboardPage({ device, onBack }) {

  // ── Remote state (from API — no mock data) ───────────────────────────────
  const [dashboards,    setDashboards]    = useState([]);
  const [activeDash,    setActiveDash]    = useState(null);
  const [liveTelem,     setLiveTelem]     = useState({});
  const [historyData,   setHistoryData]   = useState({});
  const [availableKeys, setAvailableKeys] = useState([]);
  const [alarms,        setAlarms]        = useState([]);

  // ── UI state ─────────────────────────────────────────────────────────────
  const [loadingList,   setLoadingList]   = useState(true);
  const [loadingDash,   setLoadingDash]   = useState(false);
  const [editMode,      setEditMode]      = useState(false);
  const [showModal,     setShowModal]     = useState(false);
  const [editingWidget, setEditingWidget] = useState(null);
  const [newDashName,   setNewDashName]   = useState("");
  const [showNewDash,   setShowNewDash]   = useState(false);
  const [renamingId,    setRenamingId]    = useState(null);
  const [renameVal,     setRenameVal]     = useState("");
  const [saving,        setSaving]        = useState(false);
  const [layoutSaving,  setLayoutSaving]  = useState(false);
  const [error,         setError]         = useState("");
  const [wsConnected,   setWsConnected]   = useState(false);

  // ── 1. Load dashboard list ────────────────────────────────────────────────
  useEffect(() => {
    if (!device?.id) return;
    setLoadingList(true);
    listDashboards(device.id)
      .then(async list => {
        if (list.length === 0) {
          // Auto-create a default dashboard so the user can add widgets immediately
          try {
            const d = await createDashboard(device.id, "Default Dashboard");
            setDashboards([d]);
            loadDashboard(d.id);
          } catch (e) {
            setError(e.message);
            setLoadingList(false);
          }
          return;
        }
        setDashboards(list);
        const def = list.find(d => d.is_default) || list[0];
        if (def) loadDashboard(def.id);
        else     setLoadingList(false);
      })
      .catch(e => { setError(e.message); setLoadingList(false); });
  }, [device?.id]);

  // ── 2. Load full dashboard (widgets + positions) ─────────────────────────
  const loadDashboard = useCallback(async (id) => {
    setLoadingDash(true); setError("");
    try {
      const d = await getDashboard(id);
      setActiveDash(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingDash(false);
      setLoadingList(false);
    }
  }, []);

  // ── 3. Telemetry keys + history ───────────────────────────────────────────
  useEffect(() => {
    if (!device?.id) return;
    getTelemetryKeys(device.id).then(ks => {
      setAvailableKeys(ks);
      ks.forEach(k => {
        getTelemetryHistory(device.id, k, 50)
          .then(pts => setHistoryData(h => ({ ...h, [k]: pts })))
          .catch(() => {});
      });
    }).catch(() => {});
    getLatestTelemetry(device.id).then(setLiveTelem).catch(() => {});
    getDeviceAlarms(device.id).then(setAlarms).catch(() => {});
  }, [device?.id]);

  // ── 4. WebSocket ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!device?.id) return;
    const unsub = TelemetrySocket.subscribe(device.id, null, (values, ts) => {
      setWsConnected(TelemetrySocket.getStatus(device.id).connected);
      setLiveTelem(prev => ({ ...prev, ...values }));
      setHistoryData(prev => {
        const next = { ...prev };
        Object.entries(values).forEach(([k, v]) => {
          const arr = [...(next[k] || [])];
          arr.push({ ts: ts || new Date().toISOString(), value: v });
          if (arr.length > 50) arr.shift();
          next[k] = arr;
        });
        return next;
      });
    });
    const statusTimer = setInterval(() => {
      setWsConnected(TelemetrySocket.getStatus(device.id).connected);
    }, 2000);
    return () => { unsub(); clearInterval(statusTimer); };
  }, [device?.id]);

  // ── 5. Layout persistence ─────────────────────────────────────────────────
  //
  // Called by GridLayout ONLY on dragStop / resizeStop — never mid-drag.
  //
  // Flow:
  //   1. Optimistic update — apply new positions to local state immediately
  //      so the grid doesn't jump back while the API round-trips.
  //   2. persistLayout(dashboardId, rglLayout, dashboardsHttp.saveLayout)
  //        → layoutToPositions: strips __add__ tile, clamps negatives
  //        → PUT /api/v1/dashboards/{id}/layout  { layout: [{id,x,y,w,h},...] }
  //   3. Backend writes widget.position JSON for each widget_id in the payload.
  //   4. On reload → getDashboard() returns updated positions → grid restores.
  //
  // dashboardsHttp.saveLayout is injected so widgetService stays transport-agnostic.
  const handleLayoutChange = useCallback(async (newRglLayout) => {
    if (!activeDash?.id || !newRglLayout?.length) return;

    // Optimistic — instant visual update, no API wait
    setActiveDash(prev => {
      if (!prev) return prev;
      return { ...prev, widgets: applyLayoutToWidgets(prev.widgets, newRglLayout) };
    });

    setLayoutSaving(true);
    try {
      await persistLayout(
        activeDash.id,
        newRglLayout,
        dashboardsHttp.saveLayout,  // ← device-scoped: PUT /dashboards/{id}/layout
      );
    } catch (e) {
      setError(`Layout save failed: ${e.message}`);
    } finally {
      setLayoutSaving(false);
    }
  }, [activeDash?.id]);

  // ── Widget CRUD ───────────────────────────────────────────────────────────
  const handleSaveWidget = async (data) => {
    if (!activeDash?.id) { setError("No dashboard selected. Create a dashboard first."); return; }
    setSaving(true); setError("");
    try {
      if (data.id) {
        await updateWidget(activeDash.id, data.id, {
          widget_type: data.widget_type, title: data.title,
          config:      data.config,      position: data.position,
        });
      } else {
        await addWidget(activeDash.id, data);
      }
      await loadDashboard(activeDash.id);
      setShowModal(false); setEditingWidget(null);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const handleRemoveWidget = async (widgetId) => {
    if (!activeDash?.id) return;
    setSaving(true); setError("");
    try {
      await deleteWidget(activeDash.id, widgetId);
      await loadDashboard(activeDash.id);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  // ── Dashboard CRUD ────────────────────────────────────────────────────────
  const handleCreateDashboard = async () => {
    if (!newDashName.trim()) return;
    setSaving(true); setError("");
    try {
      const d = await createDashboard(device.id, newDashName.trim());
      setDashboards(ds => [...ds, d]);
      setNewDashName(""); setShowNewDash(false);
      loadDashboard(d.id);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const handleDeleteDashboard = async (id) => {
    if (!window.confirm("Delete this dashboard and all its widgets?")) return;
    setSaving(true); setError("");
    try {
      await deleteDashboard(id);
      const remaining = dashboards.filter(d => d.id !== id);
      setDashboards(remaining);
      if (activeDash?.id === id) {
        setActiveDash(null);
        if (remaining[0]) loadDashboard(remaining[0].id);
      }
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const handleRename = async (id) => {
    if (!renameVal.trim()) { setRenamingId(null); return; }
    setSaving(true);
    try {
      const updated = await updateDashboard(id, { name: renameVal.trim() });
      setDashboards(ds => ds.map(d => d.id === id ? { ...d, name: updated.name } : d));
      if (activeDash?.id === id) setActiveDash(a => ({ ...a, name: updated.name }));
    } catch (e) { setError(e.message); }
    finally { setSaving(false); setRenamingId(null); }
  };

  // ── Render ────────────────────────────────────────────────────────────────
  if (loadingList) return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 240 }}>
      <Spinner />
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <style>{`@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}`}</style>

      {/* ── Back + device header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <button onClick={onBack}
            style={{ display: "flex", alignItems: "center", gap: 6, border: "none", background: "none", cursor: "pointer", color: "#94a3b8", fontSize: 13, fontWeight: 500 }}>
            <svg style={{ width: 16, height: 16 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
            All Devices
          </button>
          <div style={{ width: 1, height: 20, background: "#e2e8f0" }} />
          <div>
            <p style={{ fontSize: 15, fontWeight: 600, color: "#0f172a", margin: 0 }}>{device.name}</p>
            <p style={{ fontSize: 11, color: "#94a3b8", margin: 0 }}>{device.device_type} · {device.label || "No label"}</p>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 8, background: wsConnected ? "#f0fdf4" : "#f8fafc" }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: wsConnected ? "#10b981" : "#94a3b8", flexShrink: 0 }} />
            <span style={{ fontSize: 11, fontWeight: 500, color: wsConnected ? "#065f46" : "#64748b" }}>
              {wsConnected ? "Live (WebSocket)" : "Polling"}
            </span>
          </div>
          <button onClick={() => setEditMode(e => !e)}
            style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: editMode ? "2px solid #3b82f6" : "1px solid #e2e8f0", background: editMode ? "#eff6ff" : "white", cursor: "pointer", fontSize: 13, fontWeight: 500, color: editMode ? "#1d4ed8" : "#475569" }}>
            <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            {editMode ? "Done Editing" : "Edit Layout"}
          </button>
          {editMode && activeDash && (
            <button onClick={() => { setEditingWidget(null); setShowModal(true); }}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: "none", background: "#3b82f6", color: "white", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
              <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              Add Widget
            </button>
          )}
        </div>
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div style={{ padding: "10px 16px", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 10, fontSize: 13, color: "#dc2626" }}>
          {error}
          <button onClick={() => setError("")} style={{ marginLeft: 12, fontSize: 12, color: "#dc2626", background: "none", border: "none", cursor: "pointer", textDecoration: "underline" }}>dismiss</button>
        </div>
      )}

      {/* ── Dashboard tabs ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {dashboards.map(d => (
          <div key={d.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {renamingId === d.id
              ? <input autoFocus value={renameVal} onChange={e => setRenameVal(e.target.value)}
                  onBlur={() => handleRename(d.id)}
                  onKeyDown={e => { if (e.key === "Enter") handleRename(d.id); if (e.key === "Escape") setRenamingId(null); }}
                  style={{ padding: "6px 12px", borderRadius: 8, border: "2px solid #3b82f6", fontSize: 13, fontWeight: 500, outline: "none", width: 160 }} />
              : <button onClick={() => loadDashboard(d.id)}
                  style={{ padding: "6px 14px", borderRadius: 8, border: "none", background: activeDash?.id === d.id ? "#3b82f6" : "white", color: activeDash?.id === d.id ? "white" : "#64748b", cursor: "pointer", fontSize: 13, fontWeight: 500, boxShadow: activeDash?.id === d.id ? "0 1px 4px rgba(59,130,246,.3)" : "0 0 0 1px #e2e8f0" }}>
                  {d.name}
                  {d.widget_count > 0 && <span style={{ marginLeft: 6, fontSize: 10, opacity: .7 }}>{d.widget_count}</span>}
                </button>
            }
            {editMode && activeDash?.id === d.id && !renamingId && (
              <div style={{ display: "flex", gap: 2 }}>
                <button onClick={() => { setRenamingId(d.id); setRenameVal(d.name); }}
                  style={{ padding: 4, borderRadius: 6, border: "none", background: "transparent", cursor: "pointer", color: "#94a3b8" }}>
                  <svg style={{ width: 12, height: 12 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                {dashboards.length > 1 && (
                  <button onClick={() => handleDeleteDashboard(d.id)}
                    style={{ padding: 4, borderRadius: 6, border: "none", background: "transparent", cursor: "pointer", color: "#94a3b8" }}>
                    <svg style={{ width: 12, height: 12 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        {showNewDash
          ? <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input autoFocus value={newDashName} onChange={e => setNewDashName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") handleCreateDashboard(); if (e.key === "Escape") setShowNewDash(false); }}
                placeholder="Dashboard name…"
                style={{ padding: "6px 12px", fontSize: 13, border: "1px solid #93c5fd", borderRadius: 8, outline: "none", width: 180 }} />
              <button onClick={handleCreateDashboard} disabled={saving}
                style={{ padding: "6px 12px", borderRadius: 8, border: "none", background: "#3b82f6", color: "white", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                {saving ? "…" : "Create"}
              </button>
              <button onClick={() => setShowNewDash(false)}
                style={{ border: "none", background: "none", cursor: "pointer", color: "#94a3b8", padding: 4 }}>
                <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
          : <button onClick={() => setShowNewDash(true)}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", borderRadius: 8, border: "1px dashed #cbd5e1", background: "transparent", cursor: "pointer", fontSize: 13, color: "#94a3b8" }}>
              <svg style={{ width: 12, height: 12 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              New Dashboard
            </button>
        }
      </div>

      {/* ── Edit mode banner ── */}
      {editMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 10 }}>
          <svg style={{ width: 16, height: 16, color: "#3b82f6", flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <p style={{ fontSize: 13, color: "#1d4ed8", margin: 0 }}>
            <strong>Drag widgets to reposition.</strong> Drag the corner to resize.
            Layout saves to the database automatically when you release.
          </p>
        </div>
      )}

      {/* ── Widget grid ── */}
      {loadingDash ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 48 }}><Spinner /></div>
      ) : !activeDash ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 80, gap: 12 }}>
          <p style={{ fontSize: 14, color: "#64748b", margin: 0 }}>No dashboard yet. Create one above.</p>
        </div>
      ) : activeDash.widgets.length === 0 ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 80, gap: 12 }}>
          <div style={{ width: 56, height: 56, borderRadius: 16, background: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg style={{ width: 24, height: 24, color: "#cbd5e1" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          </div>
          <p style={{ fontSize: 14, fontWeight: 600, color: "#475569", margin: 0 }}>No widgets yet</p>
          <button onClick={() => { setEditMode(true); setEditingWidget(null); setShowModal(true); }}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 18px", borderRadius: 10, border: "none", background: "#3b82f6", color: "white", fontSize: 14, fontWeight: 500, cursor: "pointer" }}>
            <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add First Widget
          </button>
        </div>
      ) : (
        // ── GridLayout replaces the old `display: grid` ──────────────────────
        // widget.position  {x,y,w,h} → widgetsToLayout() → RGL layout items
        // dragStop / resizeStop → handleLayoutChange → persistLayout
        //   → PUT /api/v1/dashboards/{id}/layout  (device-scoped endpoint)
        // Identical integration to UserDashboardPage, different HTTP endpoint.
        <GridLayout
          widgets={activeDash.widgets}
          editMode={editMode}
          onLayoutChange={handleLayoutChange}
          onEditWidget={widget => { setEditingWidget(widget); setShowModal(true); }}
          onRemoveWidget={handleRemoveWidget}
          onAddWidget={() => { setEditingWidget(null); setShowModal(true); }}
          saving={layoutSaving}
          renderWidget={widget => (
            <WidgetRenderer
              widget={widget}
              liveTelem={liveTelem}
              historyData={historyData}
              alarms={alarms}
            />
          )}
        />
      )}

      {/* ── Widget modal ── */}
      {showModal && (
        <WidgetModal
          availableKeys={availableKeys.length ? availableKeys : ["temperature","humidity","voltage","pressure"]}
          onSave={handleSaveWidget}
          onClose={() => { setShowModal(false); setEditingWidget(null); }}
          editWidget={editingWidget}
        />
      )}
    </div>
  );
}
