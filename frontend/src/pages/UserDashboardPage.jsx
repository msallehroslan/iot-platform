/**
 * pages/UserDashboardPage.jsx
 *
 * Phase 3 + Critical Fix 1: Per-widget device binding.
 *
 * Each widget now stores config.device_id. The WidgetModal:
 *   1. Fetches devices from GET /api/v1/devices/
 *   2. Shows a device dropdown — user picks a device first
 *   3. Fetches telemetry keys from GET /api/v1/telemetry/keys/{device_id}
 *   4. Saves device_id into widget.config
 *
 * WebSocket subscriptions are opened for EVERY unique device_id across all
 * widgets on the active dashboard. liveTelem and historyData are keyed by
 * device_id so widgets only see their own device's data.
 *
 * Backward compat: widgets with no config.device_id show a warning prompt
 * inside the widget body instead of crashing.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import DashboardSidebar from "../components/sidebar/DashboardSidebar.jsx";
import GridLayout from "../components/dashboard/GridLayout.jsx";
import { WidgetRenderer, WIDGET_REGISTRY } from "../components/widgets/index.jsx";
import {
  listUserDashboards, getDefaultDashboard, getUserDashboard,
  createUserDashboard, renameUserDashboard, setDefaultDashboard,
  deleteUserDashboard, addUserWidget, updateUserWidget,
  deleteUserWidget,
} from "../services/userDashboardService.js";
import {
  persistLayout, applyLayoutToWidgets, getDefaultPositionForType,
} from "../services/widgetService.js";
// DashboardRuntime: centralized owner of all dashboard state
// Widgets are passive — they receive slices, never fetch independently
import { useDashboardRuntime } from "../hooks/useDashboardRuntime.js";
import { deviceApi, telemetryApi } from "../services/api.js";

const ACTIVE_DASH_KEY = "active_user_dashboard_id";
const ACCENT_COLORS   = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16"];

// ── Spinner ───────────────────────────────────────────────────────────────────
function Spinner({ size = 16 }) {
  return (
    <svg style={{ width: size, height: size, animation: "uspin .7s linear infinite", color: "#94a3b8" }} viewBox="0 0 24 24" fill="none">
      <circle style={{ opacity: .25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
      <path style={{ opacity: .75 }} fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/>
    </svg>
  );
}

// ── Add/Edit Widget Modal ─────────────────────────────────────────────────────
// Props:
//   devices    — Array<{id, name, device_type}> from GET /devices/
//   onSave     — called with completed widget data including config.device_id
//   onClose    — closes modal
//   editWidget — existing widget object when editing (null for add)
function WidgetModal({ devices, onSave, onClose, editWidget, user }) {
  const isEdit = !!editWidget;

  // Step 1 = choose widget type, Step 2 = configure
  const [step, setStep] = useState(isEdit ? 2 : 1);
  const [type, setType] = useState(editWidget?.widget_type || "");
  const [title, setTitle] = useState(editWidget?.title || "");

  // ── Device + key state ───────────────────────────────────────────────────
  // selectedDeviceId starts from the saved config when editing
  const [selectedDeviceId, setSelectedDeviceId] = useState(
    editWidget?.config?.device_id || ""
  );
  const [deviceKeys, setDeviceKeys]   = useState([]);  // fetched from backend
  const [keysLoading, setKeysLoading] = useState(false);
  const [keysError, setKeysError]     = useState("");

  // Full config — device_id is merged in on every change
  const [cfg, setCfg] = useState(() => ({
    device_id: editWidget?.config?.device_id || "",
    key:       editWidget?.config?.key  || "",
    keys:      editWidget?.config?.keys || [],
    label: "", unit: "", color: "#3b82f6",
    min: 0, max: 100, decimals: 1,
    threshold_high: "", content: "",
    method: "", param_key: "", input_type: "number",
    lat_key: "lat", lng_key: "lng",
    ...(editWidget?.config || {}),
  }));

  const set = (k, v) => setCfg(c => ({ ...c, [k]: v }));

  // ── Fetch telemetry keys when device selection changes ────────────────────
  useEffect(() => {
    if (!selectedDeviceId) {
      setDeviceKeys([]);
      return;
    }
    setKeysLoading(true);
    setKeysError("");
    telemetryApi.keys(selectedDeviceId)
      .then(res => {
        const ks = res?.keys || [];
        setDeviceKeys(ks);
        setCfg(c => {
          const filteredKeys = (c.keys || []).filter(k => ks.includes(k));
          // For multi-key widgets (bar/pie), auto-select ALL keys when none selected
          // so the Add Widget button is enabled without requiring an extra click
          const isMultiKey = ["bar_chart", "pie_chart"].includes(c.widget_type || type);
          const autoKeys = isMultiKey && filteredKeys.length === 0 ? ks : filteredKeys;
          return {
            ...c,
            device_id: selectedDeviceId,
            key:  ks.includes(c.key) ? c.key : (ks[0] || ""),
            keys: autoKeys,
          };
        });
      })
      .catch(e => {
        setKeysError(`Could not fetch keys: ${e.message}`);
        setDeviceKeys([]);
      })
      .finally(() => setKeysLoading(false));
  }, [selectedDeviceId]);

  // Keep cfg.device_id in sync whenever selectedDeviceId changes
  useEffect(() => {
    set("device_id", selectedDeviceId);
  }, [selectedDeviceId]);

  // ── Widget type flags ─────────────────────────────────────────────────────
  // alarm_list, markdown, entity_table, html_card don't need a single key
  // bar_chart, pie_chart need multiple keys
  // markdown, html_card need freeform content
  const noTelemetry   = ["markdown"].includes(type);
  const needsKey      = ![
    "alarm_list","markdown","entity_table","html_card","pie_chart",
    "rpc_button","rpc_toggle","rpc_input","device_summary","map","multi_axis_chart",
  ].includes(type);
  const isStatusLight = type === "status_light";
  const needsMultiKey  = ["pie_chart","multi_axis_chart"].includes(type);
  const needsContent   = ["markdown","html_card"].includes(type);
  const isRpcButton    = type === "rpc_button";
  const isRpcToggle    = type === "rpc_toggle";
  const isRpcInput     = type === "rpc_input";
  const isMap          = type === "map";
  const needsDevice   = !["markdown"].includes(type);  // everything except pure markdown needs a device

  // ── Validation ────────────────────────────────────────────────────────────
  const canSave = (
    // markdown doesn't need a device
    !needsDevice ||
    // all others must have a device selected
    (selectedDeviceId && (
      // single-key types need a key
      (!needsKey && !needsMultiKey) ||
      (needsKey && cfg.key) ||
      (needsMultiKey && cfg.keys?.length > 0)
    ))
  ) && (
    // RPC widget method validation
    (!isRpcButton || cfg.method?.trim()) &&
    (!isRpcToggle || cfg.key) &&
    (!isRpcInput  || cfg.method?.trim())
  );

  // ── Save handler ──────────────────────────────────────────────────────────
  const handleSave = () => {
    if (!canSave) return;
    const wt = WIDGET_REGISTRY.find(w => w.id === type);
    const autoTitle = `${wt?.label || type}${cfg.key ? ` · ${cfg.key}` : ""}`;
    const position  = isEdit ? editWidget.position : getDefaultPositionForType(type);
    onSave({
      widget_type: type,
      title:       title.trim() || autoTitle,
      config:      { ...cfg, device_id: selectedDeviceId },
      position,
      ...(isEdit ? { id: editWidget.id } : {}),
    });
  };

  const inp = {
    width: "100%", padding: "8px 12px", borderRadius: 8,
    border: "1px solid #e2e8f0", fontSize: 13, color: "#334155",
    background: "#f8fafc", outline: "none",
  };
  const inpDisabled = { ...inp, opacity: .55, cursor: "not-allowed" };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", backdropFilter: "blur(4px)", zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "white", borderRadius: 20, boxShadow: "0 20px 60px rgba(0,0,0,.2)", width: "100%", maxWidth: 520, maxHeight: "90vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 24px", borderBottom: "1px solid #f1f5f9", flexShrink: 0 }}>
          <div>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: "#0f172a", margin: 0 }}>{isEdit ? "Edit Widget" : "Add Widget"}</h3>
            <p style={{ fontSize: 12, color: "#94a3b8", margin: "2px 0 0" }}>{step === 1 ? "Choose type" : "Configure"}</p>
          </div>
          <button onClick={onClose} style={{ border: "none", background: "#f1f5f9", borderRadius: 8, padding: 6, cursor: "pointer", color: "#64748b" }}>
            <svg style={{ width: 16, height: 16 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>

          {/* Step 1 — choose widget type */}
          {step === 1 && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {WIDGET_REGISTRY
                .filter(wt => {
                  if (["rpc_button","rpc_toggle","rpc_input"].includes(wt.id) && user?.role !== "TENANT_ADMIN") return false;
                  if (["multi_axis_chart","bar_chart","timeseries_table","entity_table","pie_chart"].includes(wt.id) && user?.role === "CUSTOMER_USER") return false;
                  return true;
                })
                .map(wt => (
                <button key={wt.id} onClick={() => { setType(wt.id); setStep(2); }}
                  style={{ padding: 16, borderRadius: 12, border: `2px solid ${type === wt.id ? "#3b82f6" : "#e2e8f0"}`, background: type === wt.id ? "#eff6ff" : "white", cursor: "pointer", textAlign: "left" }}>
                  <svg style={{ width: 20, height: 20, color: "#3b82f6", marginBottom: 8, display: "block" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={wt.icon}/></svg>
                  <p style={{ fontSize: 13, fontWeight: 600, color: "#1e293b", margin: "0 0 2px" }}>{wt.label}</p>
                  <p style={{ fontSize: 10, color: "#94a3b8", margin: 0 }}>{wt.desc}</p>
                </button>
              ))}
            </div>
          )}

          {/* Step 2 — configure */}
          {step === 2 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

              {/* Back button (add mode only) */}
              {!isEdit && (
                <button onClick={() => setStep(1)} style={{ border: "none", background: "none", cursor: "pointer", color: "#3b82f6", fontSize: 12, display: "flex", alignItems: "center", gap: 4, padding: 0, width: "fit-content" }}>
                  <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>Back
                </button>
              )}

              {/* Widget type badge */}
              <div style={{ padding: "8px 12px", background: "#eff6ff", borderRadius: 8, display: "flex", alignItems: "center", gap: 8 }}>
                <svg style={{ width: 14, height: 14, color: "#3b82f6" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={WIDGET_REGISTRY.find(w=>w.id===type)?.icon||""}/></svg>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#1d4ed8" }}>{WIDGET_REGISTRY.find(w=>w.id===type)?.label}</span>
              </div>

              {/* ── Device selector ─────────────────────────────────────── */}
              {needsDevice && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                    Device <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  {devices.length === 0 ? (
                    <div style={{ ...inp, display: "flex", alignItems: "center", gap: 8, color: "#94a3b8" }}>
                      <Spinner size={14} />
                      <span>Loading devices…</span>
                    </div>
                  ) : (
                    <select
                      style={{ ...inp, cursor: "pointer" }}
                      value={selectedDeviceId}
                      onChange={e => setSelectedDeviceId(e.target.value)}
                    >
                      <option value="">— Select a device —</option>
                      {devices.map(d => (
                        <option key={d.id} value={d.id}>
                          {d.name}{d.label ? ` (${d.label})` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                  {!selectedDeviceId && (
                    <p style={{ fontSize: 11, color: "#f59e0b", margin: "5px 0 0" }}>
                      Select a device to load its telemetry keys.
                    </p>
                  )}
                </div>
              )}

              {/* ── Telemetry key (single) ──────────────────────────────── */}
              {needsKey && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                    Telemetry Key <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  {keysLoading ? (
                    <div style={{ ...inp, display: "flex", alignItems: "center", gap: 8, color: "#94a3b8" }}>
                      <Spinner size={14} /><span>Loading keys…</span>
                    </div>
                  ) : !selectedDeviceId ? (
                    <select style={inpDisabled} disabled>
                      <option>Select a device first</option>
                    </select>
                  ) : keysError ? (
                    <div style={{ padding: "8px 12px", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, fontSize: 12, color: "#dc2626" }}>
                      {keysError}
                    </div>
                  ) : deviceKeys.length === 0 ? (
                    <div style={{ padding: "8px 12px", background: "#fefce8", border: "1px solid #fde68a", borderRadius: 8, fontSize: 12, color: "#92400e" }}>
                      No telemetry keys found for this device yet. Send data first, then re-open this modal.
                    </div>
                  ) : (
                    <select style={{ ...inp, cursor: "pointer" }} value={cfg.key} onChange={e => set("key", e.target.value)}>
                      {deviceKeys.map(k => <option key={k}>{k}</option>)}
                    </select>
                  )}
                </div>
              )}

              {/* ── Telemetry keys (multi) ──────────────────────────────── */}
              {needsMultiKey && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                    Keys to display <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  {!selectedDeviceId ? (
                    <p style={{ fontSize: 12, color: "#94a3b8" }}>Select a device first.</p>
                  ) : keysLoading ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}><Spinner size={14} /><span style={{ fontSize: 12, color: "#94a3b8" }}>Loading…</span></div>
                  ) : deviceKeys.length === 0 ? (
                    <p style={{ fontSize: 12, color: "#f59e0b" }}>No keys yet — send telemetry to this device first.</p>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {deviceKeys.map(k => {
                        const on = (cfg.keys || []).includes(k);
                        return (
                          <label key={k} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 12px", borderRadius: 20, border: `1px solid ${on ? "#3b82f6" : "#e2e8f0"}`, background: on ? "#eff6ff" : "white", cursor: "pointer", fontSize: 12, color: on ? "#1d4ed8" : "#64748b" }}>
                            <input type="checkbox" checked={on}
                              onChange={e => set("keys", e.target.checked ? [...(cfg.keys||[]),k] : (cfg.keys||[]).filter(x=>x!==k))}
                              style={{ display: "none" }}
                            />{k}
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* ── entity_table: show all keys, no selection needed ─────── */}
              {type === "entity_table" && selectedDeviceId && (
                <div style={{ padding: "8px 12px", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, fontSize: 12, color: "#065f46" }}>
                  Will display all live telemetry keys from this device.
                </div>
              )}

              {/* ── alarm_list: device bound, no key ─────────────────────── */}
              {type === "alarm_list" && selectedDeviceId && (
                <div style={{ padding: "8px 12px", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, fontSize: 12, color: "#065f46" }}>
                  Will show active alarms for this device.
                </div>
              )}

              {/* Title */}
              <div>
                <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Title</label>
                <input style={inp} value={title} onChange={e => setTitle(e.target.value)}
                  placeholder={`${WIDGET_REGISTRY.find(w=>w.id===type)?.label || type}${cfg.key ? ` · ${cfg.key}` : ""}`}
                />
              </div>

              {/* Content (markdown / html) */}
              {needsContent && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                    {type === "html_card" ? "HTML Template (use ${key} for values)" : "Markdown Text"}
                  </label>
                  <textarea style={{ ...inp, resize: "none", fontFamily: "monospace", fontSize: 12 }} rows={5}
                    value={cfg.content || ""} onChange={e => set("content", e.target.value)}
                    placeholder={type === "html_card" ? "<h2>Temp: ${temperature}°C</h2>" : "**Status:** Online\n`key`: value"}
                  />
                </div>
              )}

              {isRpcInput && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                      Command Method * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(e.g. setValue, setSetpoint)</span>
                    </label>
                    <input style={inp} value={cfg.method || ""} onChange={e => set("method", e.target.value)}
                      placeholder="e.g. setValue" />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                        Param Name * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(key in JSON)</span>
                      </label>
                      <input style={inp} value={cfg.param_key || "value"} onChange={e => set("param_key", e.target.value)}
                        placeholder="e.g. value, setpoint, level" />
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Input Type</label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.input_type || "number"} onChange={e => set("input_type", e.target.value)}>
                        <option value="number">Number</option>
                        <option value="text">Text</option>
                      </select>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                        Current value key <span style={{ color: "#94a3b8", fontWeight: 400 }}>(optional)</span>
                      </label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.key || ""} onChange={e => set("key", e.target.value)}>
                        <option value="">— None —</option>
                        {deviceKeys.map(k => <option key={k}>{k}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Unit</label>
                      <input style={inp} value={cfg.unit || ""} onChange={e => set("unit", e.target.value)} placeholder="e.g. °C, %, RPM" />
                    </div>
                  </div>
                  <div style={{ padding: "10px 12px", background: "#f8fafc", borderRadius: 8, fontSize: 11, color: "#64748b", lineHeight: 1.7 }}>
                    <strong>How it works:</strong><br/>
                    Device receives: <span style={{ fontFamily: "monospace" }}>{"{method: \"" + (cfg.method||"setValue") + "\", params: {\"" + (cfg.param_key||"value") + "\": &lt;input&gt;}}"}</span>
                  </div>
                </div>
              )}
              {isRpcButton && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                      Command Method * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(sent to device on click)</span>
                    </label>
                    <input style={inp} value={cfg.method || ""} onChange={e => set("method", e.target.value)}
                      placeholder="e.g. turnOn, reboot, setValue" />
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Button Label</label>
                    <input style={inp} value={cfg.label || ""} onChange={e => set("label", e.target.value)}
                      placeholder="e.g. Turn On, Restart" />
                  </div>
                </div>
              )}

              {isRpcToggle && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                        Monitor Key * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(reads ON/OFF)</span>
                      </label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.key || ""}
                        onChange={e => { set("key", e.target.value); if (!cfg.param_key) set("param_key", e.target.value); }}>
                        <option value="">— Select key —</option>
                        {deviceKeys.map(k => <option key={k}>{k}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                        Control Key * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(sets via RPC)</span>
                      </label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.param_key || cfg.key || ""}
                        onChange={e => set("param_key", e.target.value)}>
                        <option value="">— Same as monitor —</option>
                        {deviceKeys.map(k => <option key={k}>{k}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Label</label>
                    <input style={inp} value={cfg.label || ""} onChange={e => set("label", e.target.value)}
                      placeholder="e.g. Pump, Relay 1, Fan, LED 2" />
                  </div>
                  <div style={{ padding: "10px 12px", background: "#f0fdf4", borderRadius: 8, fontSize: 11, color: "#166534", lineHeight: 1.7 }}>
                    <strong>Sends:</strong> <span style={{ fontFamily: "monospace" }}>{`{"method":"set","params":{"${cfg.param_key||cfg.key||"key"}":true/false}}`}</span><br/>
                    <span style={{ color: "#64748b" }}>Works for any actuator — LED, relay, motor, pump, fan.</span>
                  </div>
                </div>
              )}

              {isStatusLight && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                      Telemetry Key <span style={{ color: "#94a3b8", fontWeight: 400 }}>(optional — leave blank for device online/offline)</span>
                    </label>
                    <select style={{ ...inp, cursor: "pointer" }} value={cfg.key || ""} onChange={e => set("key", e.target.value)}>
                      <option value="">— Device online/offline —</option>
                      {deviceKeys.map(k => <option key={k}>{k}</option>)}
                    </select>
                  </div>
                  <div style={{ padding: "10px 12px", background: "#f8fafc", borderRadius: 8, fontSize: 11, color: "#64748b", lineHeight: 1.7 }}>
                    {cfg.key
                      ? <span>Shows <strong style={{color:"#10b981"}}>ON</strong> / <strong style={{color:"#94a3b8"}}>OFF</strong> based on <code style={{fontFamily:"monospace"}}>{cfg.key}</code> value (1/true = ON)</span>
                      : <span>Shows <strong style={{color:"#10b981"}}>ONLINE</strong> / <strong style={{color:"#94a3b8"}}>OFFLINE</strong> based on last device heartbeat</span>
                    }
                  </div>
                </div>
              )}
              {isMap && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Latitude key *</label>
                    <select style={{ ...inp, cursor: "pointer" }} value={cfg.lat_key || "lat"} onChange={e => set("lat_key", e.target.value)}>
                      <option value="lat">lat</option>
                      {deviceKeys.filter(k=>k!=="lat").map(k=><option key={k}>{k}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Longitude key *</label>
                    <select style={{ ...inp, cursor: "pointer" }} value={cfg.lng_key || "lng"} onChange={e => set("lng_key", e.target.value)}>
                      <option value="lng">lng</option>
                      {deviceKeys.filter(k=>k!=="lng").map(k=><option key={k}>{k}</option>)}
                    </select>
                  </div>
                </div>
              )}

              {/* Unit + decimals */}
              {["value_card","gauge","timeseries_table"].includes(type) && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Unit</label>
                    <input style={inp} value={cfg.unit || ""} onChange={e => set("unit", e.target.value)} placeholder="°C"/>
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Decimals</label>
                    <input type="number" style={inp} value={cfg.decimals ?? 1} min={0} max={4} onChange={e => set("decimals", parseInt(e.target.value))}/>
                  </div>
                </div>
              )}

              {/* Min / max (gauge) */}
              {type === "gauge" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Min</label>
                    <input type="number" style={inp} value={cfg.min ?? 0} onChange={e => set("min", parseFloat(e.target.value))}/>
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Max</label>
                    <input type="number" style={inp} value={cfg.max ?? 100} onChange={e => set("max", parseFloat(e.target.value))}/>
                  </div>
                </div>
              )}

              {/* Alert threshold (value_card) */}
              {type === "value_card" && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Alert threshold (optional)</label>
                  <input type="number" style={inp} value={cfg.threshold_high || ""}
                    placeholder="e.g. 80 → turns red above this"
                    onChange={e => set("threshold_high", e.target.value ? parseFloat(e.target.value) : "")}
                  />
                </div>
              )}

              {/* Accent colour */}
              {!needsContent && (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Accent colour</label>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {ACCENT_COLORS.map(c => (
                      <button key={c} onClick={() => set("color", c)}
                        style={{ width: 28, height: 28, borderRadius: "50%", background: c, border: cfg.color === c ? "3px solid #1e293b" : "2px solid white", boxShadow: "0 1px 3px rgba(0,0,0,.2)", outline: cfg.color === c ? `2px solid ${c}` : "none", outlineOffset: 2, cursor: "pointer" }}
                      />
                    ))}
                  </div>
                </div>
              )}

            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "16px 24px", borderTop: "1px solid #f1f5f9", display: "flex", gap: 10, flexShrink: 0 }}>
          {step === 1 ? (
            <button onClick={onClose} style={{ flex: 1, padding: "10px", borderRadius: 10, border: "1px solid #e2e8f0", background: "transparent", cursor: "pointer", fontSize: 14, color: "#64748b" }}>Cancel</button>
          ) : (
            <>
              <button
                onClick={handleSave}
                disabled={!canSave}
                title={!canSave ? "Select a device and telemetry key first" : ""}
                style={{ flex: 1, padding: "10px", borderRadius: 10, border: "none", background: canSave ? "#3b82f6" : "#cbd5e1", color: "white", cursor: canSave ? "pointer" : "not-allowed", fontSize: 14, fontWeight: 600, transition: "background .15s" }}
              >
                {isEdit ? "Save Changes" : "Add Widget"}
              </button>
              <button onClick={onClose} style={{ padding: "10px 16px", borderRadius: 10, border: "1px solid #e2e8f0", background: "transparent", cursor: "pointer", fontSize: 14, color: "#64748b" }}>Cancel</button>
            </>
          )}
        </div>

      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function UserDashboardPage({ onToast, user }) {

  // ── Dashboard navigation state ────────────────────────────────────────────
  const [dashboardList, setDashboardList] = useState([]);
  const [activeDash,    setActiveDash]    = useState(null);
  const [devices,       setDevices]       = useState([]);

  // ── DashboardRuntime: single source of truth for all widget data ──────────
  // Owns: preload, WS subscriptions, telemetry, history, alarms, intelligence
  // Widgets receive slices — never fetch independently
  const {
    liveTelem,
    historyData,
    alarmsData,
    intellData,
    wsConnected: wsConnectedMap,
    preloadDone,
  } = useDashboardRuntime(activeDash, user);

  // Derive single wsConnected bool for header indicator
  const wsConnected = Object.values(wsConnectedMap).some(Boolean);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [loadingList,  setLoadingList]  = useState(true);
  const [loadingDash,  setLoadingDash]  = useState(false);
  const [listError,    setListError]    = useState("");
  const [dashError,    setDashError]    = useState("");
  const isAdmin = user?.role === "TENANT_ADMIN";
  const [editMode,     setEditMode]     = useState(false);
  const [showModal,    setShowModal]    = useState(false);
  const [editingWidget,setEditingWidget]= useState(null);
  const [saving,       setSaving]       = useState(false);
  const [layoutSaving, setLayoutSaving] = useState(false);

  // ── Fetch device list once on mount ───────────────────────────────────────
  useEffect(() => {
    deviceApi.list({ limit: 200 })
      .then(list => setDevices(list || []))
      .catch(() => {}); // non-fatal — modal shows empty dropdown with message
  }, []);

  // ── Load dashboard list + restore last selection ──────────────────────────
  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      // Remove duplicate dashboards silently on every mount
      try { await userDashboardsHttp.deduplicate(); } catch (_) {}
      setLoadingList(true); setListError("");
      try {
        const [list, dash] = await Promise.all([
          listUserDashboards(),
          (async () => {
            const savedId = localStorage.getItem(ACTIVE_DASH_KEY);
            if (savedId) {
              try { return await getUserDashboard(savedId); }
              catch (_) { localStorage.removeItem(ACTIVE_DASH_KEY); }
            }
            return getDefaultDashboard();
          })(),
        ]);
        if (cancelled) return;
        setDashboardList(list);
        setActiveDash(dash);
        localStorage.setItem(ACTIVE_DASH_KEY, dash.id);
      } catch (e) {
        if (!cancelled) setListError(e.message);
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    };
    init();
    return () => { cancelled = true; };
  }, []);

  // ── Load a specific dashboard ─────────────────────────────────────────────
  const loadDashboard = useCallback(async (id) => {
    setLoadingDash(true); setDashError("");
    try {
      const d = await getUserDashboard(id);
      setActiveDash(d);
      localStorage.setItem(ACTIVE_DASH_KEY, id);
    } catch (e) {
      setDashError(e.message);
    } finally {
      setLoadingDash(false);
    }
  }, []);

  // ── Sidebar actions ───────────────────────────────────────────────────────
  const handleCreate = async (name) => {
    setSaving(true); setListError("");
    try {
      const d = await createUserDashboard(name);
      setDashboardList(prev => [...prev, { ...d, widget_count: 0 }]);
      setActiveDash(d);
      localStorage.setItem(ACTIVE_DASH_KEY, d.id);
      if (onToast) onToast("Dashboard created");
    } catch (e) {
      setListError(e.message);
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  const handleRename = async (id, name) => {
    setSaving(true);
    try {
      const updated = await renameUserDashboard(id, name);
      setDashboardList(prev => prev.map(d => d.id === id ? { ...d, name: updated.name } : d));
      if (activeDash?.id === id) setActiveDash(a => ({ ...a, name: updated.name }));
      if (onToast) onToast("Renamed");
    } catch (e) {
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  const handleSetDefault = async (id) => {
    setSaving(true);
    try {
      await setDefaultDashboard(id);
      setDashboardList(prev => prev.map(d => ({ ...d, is_default: d.id === id })));
      if (onToast) onToast("Set as default");
    } catch (e) {
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  const handleDelete = async (id) => {
    setSaving(true);
    try {
      await deleteUserDashboard(id);
      const remaining = dashboardList.filter(d => d.id !== id);
      setDashboardList(remaining);
      if (activeDash?.id === id) {
        if (remaining.length > 0) await loadDashboard(remaining[0].id);
        else setActiveDash(null);
      }
      if (onToast) onToast("Dashboard deleted");
    } catch (e) {
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  // ── Widget CRUD ───────────────────────────────────────────────────────────
  const handleSaveWidget = async (data) => {
    setSaving(true); setDashError("");
    try {
      if (data.id) {
        await updateUserWidget(activeDash.id, data.id, {
          widget_type: data.widget_type, title: data.title,
          config: data.config, position: data.position,
        });
      } else {
        await addUserWidget(activeDash.id, data);
      }
      await loadDashboard(activeDash.id);
      setDashboardList(prev => prev.map(d =>
        d.id === activeDash.id
          ? { ...d, widget_count: (activeDash?.widgets?.length ?? 0) + (data.id ? 0 : 1) }
          : d
      ));
      setShowModal(false); setEditingWidget(null);
      if (onToast) onToast(data.id ? "Widget updated" : "Widget added");
    } catch (e) {
      setDashError(e.message);
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  const handleRemoveWidget = async (widgetId) => {
    setSaving(true);
    try {
      await deleteUserWidget(activeDash.id, widgetId);
      await loadDashboard(activeDash.id);
      setDashboardList(prev => prev.map(d =>
        d.id === activeDash.id ? { ...d, widget_count: Math.max(0, (d.widget_count ?? 1) - 1) } : d
      ));
      if (onToast) onToast("Widget removed");
    } catch (e) {
      if (onToast) onToast(e.message, "error");
    } finally { setSaving(false); }
  };

  // ── Layout persistence ────────────────────────────────────────────────────
  // Stable renderWidget — only re-creates when liveTelem/historyData actually change
  // Without this, GridLayout calls renderWidget for ALL widgets on every 250ms flush
  const renderWidget = useCallback((widget) => (
    <WidgetRenderer
      widget={widget}
      liveTelem={liveTelem[widget.config?.device_id] || {}}
      historyData={historyData[widget.config?.device_id] || {}}
      alarms={alarmsData[widget.config?.device_id] || []}
      intelligence={intellData[widget.config?.device_id] || null}
      missingDevice={!widget.config?.device_id}
      deviceId={widget.config?.device_id || null}
      userRole={user?.role}
    />
  ), [liveTelem, historyData, alarmsData, intellData, user?.role]);

  const handleLayoutChange = useCallback(async (newRglLayout) => {
    if (!activeDash?.id || !newRglLayout?.length) return;
    setActiveDash(prev => {
      if (!prev) return prev;
      return { ...prev, widgets: applyLayoutToWidgets(prev.widgets, newRglLayout) };
    });
    setLayoutSaving(true);
    try {
      await persistLayout(activeDash.id, newRglLayout);
    } catch (e) {
      if (onToast) onToast(`Layout save failed: ${e.message}`, "error");
    } finally {
      setLayoutSaving(false);
    }
  }, [activeDash?.id, onToast]);

  // ── WebSocket + telemetry state is now owned by useDashboardRuntime ────────
  // liveTelem, historyData, alarmsData, intellData, wsConnected
  // are all provided by the runtime above. No subscriptions here.

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
      <style>{`
        @keyframes uspin { from { transform: rotate(0) } to { transform: rotate(360deg) } }
        .dash-row:hover .row-menu-btn { opacity: 1 !important; }
      `}</style>

      <DashboardSidebar
        dashboards={dashboardList}
        activeDashboardId={activeDash?.id}
        onSelect={loadDashboard}
        onCreate={handleCreate}
        onRename={handleRename}
        onSetDefault={handleSetDefault}
        onDelete={handleDelete}
        loading={loadingList}
        error={listError}
      />

      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ flex: 1, padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Toolbar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: "#0f172a", margin: 0 }}>
                {activeDash?.name || "Dashboard"}
                {activeDash?.is_default && (
                  <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 700, background: "#fef3c7", color: "#92400e", border: "1px solid #fde68a", borderRadius: 4, padding: "2px 6px", verticalAlign: "middle" }}>DEFAULT</span>
                )}
              </h2>
              <p style={{ fontSize: 12, color: "#94a3b8", margin: "3px 0 0" }}>
                {activeDash?.widgets?.length ?? 0} widget{activeDash?.widgets?.length !== 1 ? "s" : ""}
                {editMode && <span style={{ marginLeft: 8, color: "#3b82f6" }}>· Drag to reposition · Resize from corner</span>}
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 8, background: wsConnected ? "#f0fdf4" : "#f8fafc" }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: wsConnected ? "#10b981" : "#94a3b8" }} />
                <span style={{ fontSize: 11, fontWeight: 500, color: wsConnected ? "#065f46" : "#64748b" }}>
                  {wsConnected ? "Live" : "Static"}
                </span>
              </div>
              <button onClick={() => setEditMode(e => !e)}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: editMode ? "2px solid #3b82f6" : "1px solid #e2e8f0", background: editMode ? "#eff6ff" : "white", cursor: "pointer", fontSize: 13, fontWeight: 500, color: editMode ? "#1d4ed8" : "#475569" }}>
                <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                {editMode ? "Done Editing" : "Edit Layout"}
              </button>
              {editMode && (
                <button onClick={() => { setEditingWidget(null); setShowModal(true); }}
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: "none", background: "#3b82f6", color: "white", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
                  <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                  Add Widget
                </button>
              )}
            </div>
          </div>

          {/* Error banner */}
          {dashError && (
            <div style={{ padding: "10px 16px", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 10, fontSize: 13, color: "#dc2626", flexShrink: 0 }}>
              {dashError}
              <button onClick={() => setDashError("")} style={{ marginLeft: 12, fontSize: 12, color: "#dc2626", background: "none", border: "none", cursor: "pointer", textDecoration: "underline" }}>dismiss</button>
            </div>
          )}

          {/* Edit mode banner */}
          {editMode && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 10, flexShrink: 0 }}>
              <svg style={{ width: 16, height: 16, color: "#3b82f6", flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
              <p style={{ fontSize: 13, color: "#1d4ed8", margin: 0 }}>
                <strong>Drag widgets to reposition.</strong> Drag the corner to resize.
                Layout saves to the database automatically when you release.
              </p>
            </div>
          )}

          {/* Widget grid */}
          {loadingDash ? (
            <div style={{ display: "flex", justifyContent: "center", padding: 64 }}><Spinner size={28} /></div>
          ) : !activeDash ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", flex: 1, gap: 12 }}>
              <p style={{ fontSize: 14, color: "#64748b", margin: 0 }}>No dashboard selected.</p>
            </div>
          ) : activeDash.widgets.length === 0 && !editMode ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", flex: 1, gap: 14, padding: 60 }}>
              <div style={{ width: 56, height: 56, borderRadius: 16, background: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <svg style={{ width: 24, height: 24, color: "#cbd5e1" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
              </div>
              <p style={{ fontSize: 14, fontWeight: 600, color: "#475569", margin: 0 }}>No widgets on this dashboard</p>
              <button onClick={() => { setEditMode(true); setEditingWidget(null); setShowModal(true); }}
                style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 18px", borderRadius: 10, border: "none", background: "#3b82f6", color: "white", fontSize: 14, fontWeight: 500, cursor: "pointer" }}>
                <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                Add First Widget
              </button>
            </div>
          ) : (
            <GridLayout
              widgets={activeDash.widgets}
              editMode={editMode}
              onLayoutChange={handleLayoutChange}
              onEditWidget={widget => { setEditingWidget(widget); setShowModal(true); }}
              onRemoveWidget={handleRemoveWidget}
              onAddWidget={() => { setEditingWidget(null); setShowModal(true); }}
              saving={layoutSaving}
              renderWidget={renderWidget}
            />
          )}
        </div>
      </div>

      {/* Widget modal */}
      {showModal && (
        <WidgetModal
          devices={devices}
          onSave={handleSaveWidget}
          onClose={() => { setShowModal(false); setEditingWidget(null); }}
          editWidget={editingWidget}
          user={user}
        />
      )}
    </div>
  );
}
