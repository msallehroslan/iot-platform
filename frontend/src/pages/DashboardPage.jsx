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
import { dashboardsHttp, telemetryApi, deviceApi, intelligenceApi } from "../services/api.js";       // injected into persistLayout
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
function WidgetModal({ availableKeys, onSave, onClose, editWidget, user }) {
  const isEdit = !!editWidget;
  const [step,  setStep]  = useState(isEdit ? 2 : 1);
  const [type,  setType]  = useState(editWidget?.widget_type || "");
  const [title, setTitle] = useState(editWidget?.title || "");
  const [cfg,   setCfg]   = useState(() => {
    const base = {
      key: availableKeys[0] || "",
      label: "", unit: "", color: "#3b82f6",
      min: 0, max: 100, decimals: 1,
      threshold_high: "", keys: [], content: "",
      method: "", param_key: "value", input_type: "number",
      lat_key: "lat", lng_key: "lng",
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

  // When real keys finish loading and replace the fallback list, sync cfg.key
  // so the select doesn't show blank (race: modal opened before keys API returned)
  useEffect(() => {
    if (!availableKeys.length) return;
    setCfg(c => ({
      ...c,
      key:  c.key && availableKeys.includes(c.key) ? c.key : availableKeys[0],
      keys: (c.keys || []).length ? c.keys.filter(k => availableKeys.includes(k)) : c.keys,
    }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availableKeys]);

  const needsKey      = ![
    "alarm_list","markdown","entity_table","html_card","pie_chart",
    "rpc_button","rpc_toggle","rpc_input","device_summary","map","multi_axis_chart",
    "pump_twin",
  ].includes(type);
  const isPumpTwin    = type === "pump_twin";
  const isStatusLight = type === "status_light";
  const needsMultiKey  = ["pie_chart","multi_axis_chart"].includes(type);
  const needsContent   = ["markdown","html_card"].includes(type);
  const isRpcButton    = type === "rpc_button";
  const isRpcToggle    = type === "rpc_toggle";
  const isRpcInput     = type === "rpc_input";
  const isMap          = type === "map";
  const isMultiAxisChart = type === "multi_axis_chart";

  const handleSave = () => {
    // Frontend validation for RPC widgets
    if (isRpcButton && !cfg.method?.trim()) {
      alert("RPC Button requires a Method name (e.g. turnOn, reboot)"); return;
    }
    if (isRpcToggle && !cfg.key) {
      alert("RPC Toggle requires a Telemetry Key to read current state"); return;
    }
    if (isRpcInput && !cfg.method?.trim()) {
      alert("RPC Input requires a Method name"); return;
    }
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
              {WIDGET_REGISTRY
                .filter(wt => {
                  // Hide RPC widgets for non-admins
                  if (["rpc_button","rpc_toggle","rpc_input"].includes(wt.id) && user?.role !== "TENANT_ADMIN") return false;
                  // Hide technical widgets for CUSTOMER_USER
                  if (["multi_axis_chart","bar_chart","timeseries_table","entity_table","pie_chart"].includes(wt.id) && user?.role === "CUSTOMER_USER") return false;
                  return true;
                })
                .map(wt => (
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
                  <select style={{ ...inp, cursor: "pointer" }} value={cfg.key || ""} onChange={e => set("key", e.target.value)}>
                    <option value="">— Select key —</option>
                    {availableKeys.map(k => <option key={k}>{k}</option>)}
                  </select>
                </div>
              )}
              {isPumpTwin && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 2 }}>Sensor key mapping</label>
                  {[
                    { label: "NDE motor vibration",   key: "key_vib_nde"      },
                    { label: "DE motor vibration",    key: "key_vib_de"       },
                    { label: "DE pump vibration",     key: "key_vib_de_pump"  },
                    { label: "PP pump vibration",     key: "key_vib_pp"       },
                    { label: "NDE motor temperature", key: "key_temp_nde"     },
                    { label: "DE motor temperature",  key: "key_temp_de"      },
                    { label: "DE pump temperature",   key: "key_temp_de_pump" },
                    { label: "Fluid inlet temp",      key: "key_temp_inlet"   },
                    { label: "Fluid outlet temp",     key: "key_temp_outlet"  },
                    { label: "Suction pressure",      key: "key_pressure_in"  },
                    { label: "Discharge pressure",    key: "key_pressure_out" },
                    { label: "Shaft speed (RPM)",     key: "key_speed"        },
                  ].map(({ label, key }) => (
                    <div key={key} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, alignItems: "center" }}>
                      <label style={{ fontSize: 11, color: "#64748b", fontWeight: 500 }}>{label}</label>
                      <select style={{ ...inp, cursor: "pointer", fontSize: 12 }}
                        value={cfg[key] || ""}
                        onChange={e => set(key, e.target.value)}>
                        <option value="">— not mapped —</option>
                        {availableKeys.map(k => <option key={k} value={k}>{k}</option>)}
                      </select>
                    </div>
                  ))}
                  <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginTop: 4 }}>Efficiency (optional)</label>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    <div>
                      <label style={{ fontSize: 11, color: "#94a3b8" }}>Fallback head (m)</label>
                      <input type="number" style={{ ...inp, fontSize: 12 }} placeholder="e.g. 30"
                        value={cfg.head_m || ""}
                        onChange={e => set("head_m", e.target.value ? parseFloat(e.target.value) : "")} />
                    </div>
                    <div>
                      <label style={{ fontSize: 11, color: "#94a3b8" }}>Fluid Cp (J/kg·K)</label>
                      <input type="number" style={{ ...inp, fontSize: 12 }} placeholder="4186 (water)"
                        value={cfg.fluid_cp || ""}
                        onChange={e => set("fluid_cp", e.target.value ? parseFloat(e.target.value) : "")} />
                    </div>
                  </div>
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
                        {availableKeys.map(k => <option key={k}>{k}</option>)}
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
              {/* ── RPC Button config ─────────────────────────────────── */}
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
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                      Button Label
                    </label>
                    <input style={inp} value={cfg.label || ""} onChange={e => set("label", e.target.value)}
                      placeholder="e.g. Turn On, Restart Device" />
                  </div>
                  <div style={{ padding: "10px 12px", background: "#f8fafc", borderRadius: 8, fontSize: 11, color: "#64748b", lineHeight: 1.6 }}>
                    💡 The device must handle this method via MQTT or HTTP polling.<br/>
                    See <strong>ESP32 RPC Guide</strong> in the docs.
                  </div>
                </div>
              )}

              {/* ── RPC Toggle config ─────────────────────────────────── */}
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
                        {availableKeys.map(k => <option key={k}>{k}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                        Control Key * <span style={{ color: "#94a3b8", fontWeight: 400 }}>(sets via RPC)</span>
                      </label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.param_key || cfg.key || ""}
                        onChange={e => set("param_key", e.target.value)}>
                        <option value="">— Same as monitor —</option>
                        {availableKeys.map(k => <option key={k}>{k}</option>)}
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
                    <span style={{ color: "#64748b" }}>Reads state from <code style={{fontFamily:"monospace"}}>{cfg.key||"key"}</code> telemetry. Works for any actuator — LED, relay, motor, pump, fan.</span>
                  </div>
                </div>
              )}

              {/* ── Map config ────────────────────────────────────────── */}
              {isMap && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Latitude key *</label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.lat_key || "lat"} onChange={e => set("lat_key", e.target.value)}>
                        <option value="lat">lat</option>
                        {availableKeys.filter(k=>k!="lat").map(k=><option key={k}>{k}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>Longitude key *</label>
                      <select style={{ ...inp, cursor: "pointer" }} value={cfg.lng_key || "lng"} onChange={e => set("lng_key", e.target.value)}>
                        <option value="lng">lng</option>
                        {availableKeys.filter(k=>k!="lng").map(k=><option key={k}>{k}</option>)}
                      </select>
                    </div>
                  </div>
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
              {isStatusLight && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "#64748b", marginBottom: 6 }}>
                      Telemetry Key <span style={{ color: "#94a3b8", fontWeight: 400 }}>(optional — leave blank for device online/offline)</span>
                    </label>
                    <select style={{ ...inp, cursor: "pointer" }} value={cfg.key || ""} onChange={e => set("key", e.target.value)}>
                      <option value="">— Device online/offline —</option>
                      {availableKeys.map(k => <option key={k}>{k}</option>)}
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
export default function DashboardPage({ device, onBack, user }) {

  // ── Remote state (from API — no mock data) ───────────────────────────────
  const [dashboards,    setDashboards]    = useState([]);
  const [activeDash,    setActiveDash]    = useState(null);
  const [liveTelem,     setLiveTelem]     = useState({});
  const [historyData,   setHistoryData]   = useState({});
  const [intelligence,  setIntelligence]  = useState(null);
  const [availableKeys, setAvailableKeys] = useState([]);
  const [alarms,        setAlarms]        = useState([]);

  // ── UI state ─────────────────────────────────────────────────────────────
  const [loadingList,   setLoadingList]   = useState(true);
  const [loadingDash,   setLoadingDash]   = useState(false);
  const isAdmin = user?.role === "TENANT_ADMIN";
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
  const [rcaResult,     setRcaResult]     = useState(null);
  const [rcaLoading,    setRcaLoading]    = useState(false);
  const [allDevices,    setAllDevices]    = useState([]);
  // Fetch all devices for Fleet Map
  useEffect(() => { deviceApi.list({ limit:200 }).then(d=>setAllDevices(d?.items||d||[])).catch(()=>{}); }, []);

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

  // ── 3. Telemetry keys + bulk history + intelligence ──────────────────────
  // Same pattern as UserDashboardPage / useDashboardRuntime:
  //   - Parallel fetch: keys + latest + alarms + intelligence
  //   - Intelligence refresh every 60s, always update (no stale comparison)
  //   - No fast-refresh timer needed — initial load covers enriched_keys
  useEffect(() => {
    if (!device?.id) return;

    async function loadAll() {
      try {
        const [ks, latest, deviceAlarms, intel] = await Promise.all([
          getTelemetryKeys(device.id),
          getLatestTelemetry(device.id),
          getDeviceAlarms(device.id),
          intelligenceApi.unified(device.id).catch(() => null),
        ]);
        setAvailableKeys(ks);
        setLiveTelem(latest);
        setAlarms(deviceAlarms);
        if (intel) setIntelligence(intel);

        if (!ks.length) return;

        const bulk = await telemetryApi.bulkHistory(device.id, ks, 50);
        if (bulk?.data) {
          const cleanBulk = {};
          Object.entries(bulk.data).forEach(([k, pts]) => {
            if (!Array.isArray(pts)) return;
            const clean = pts.filter(p => p && p.ts && Number.isFinite(
              typeof p.value === "number" ? p.value : parseFloat(p.value)
            )).map(p => ({
              ts: p.ts,
              value: typeof p.value === "number" ? p.value : parseFloat(p.value),
            }));
            if (clean.length) cleanBulk[k] = clean;
          });
          setHistoryData(cleanBulk);
        }
      } catch (e) {
        // non-fatal
      }
    }

    loadAll();

    // Refresh intelligence every 60s — same cadence as useDashboardRuntime
    // Always update state (no stale comparison) so enriched_keys stay fresh
    const intelTimer = setInterval(async () => {
      try {
        const intel = await intelligenceApi.unified(device.id);
        if (intel) setIntelligence(intel);
      } catch (_) {}
    }, 60_000);

    return () => clearInterval(intelTimer);
  }, [device?.id]);

  // ── 4. WebSocket ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!device?.id) return;
    let telemetryBuffer = {};
    let historyBuffer = {};
    let updateTimer = null;

    const unsub = TelemetrySocket.subscribe(
      device.id,
      null,
      (values, ts) => {

        // wsConnected is polled by statusTimer below — not set per-message
        // ================= BUFFER TELEMETRY =================

        Object.assign(
          telemetryBuffer,
          values
        );

        // ================= BUFFER HISTORY =================

        Object.entries(values).forEach(([k, v]) => {
          // Guard: only push finite numeric values into history
          const _n = typeof v === "number" ? v : parseFloat(v);
          if (!Number.isFinite(_n)) return;

          if (!historyBuffer[k]) {
            historyBuffer[k] = [];
          }

          historyBuffer[k].push({
            ts: ts || new Date().toISOString(),
            value: _n,
          });

          // Prevent oversized temporary buffers
          if (historyBuffer[k].length > 10) {
            historyBuffer[k].shift();
          }
        });

        // ================= BATCH UPDATE =================

        if (!updateTimer) {
  
          updateTimer = setTimeout(() => {

            // ---------- LIVE TELEMETRY ----------

            setLiveTelem(prev => ({
              ...prev,
              ...telemetryBuffer,
            }));

            // ---------- HISTORY ----------

            setHistoryData(prev => {

              const next = { ...prev };

              Object.entries(historyBuffer)
                .forEach(([k, arr]) => {

                  next[k] = [
                    ...(next[k] || []),
                    ...arr,
                  ].slice(-50);

                });

              return next;
            });

            // ---------- RESET ----------

            telemetryBuffer = {};
            historyBuffer = {};
            updateTimer = null;

          }, 500); // batch every 500ms
        }
      }
    );

    // Poll wsConnected every 2s — avoids per-message setState overhead
    const statusTimer = setInterval(() => {
      const connected = TelemetrySocket.getStatus(device.id).connected;
      setWsConnected(prev => prev === connected ? prev : connected);
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
  // Stable renderWidget — only changes when actual data changes
  // Prevents GridLayout from re-calling renderWidget for ALL widgets on every flush
  const renderWidget = useCallback((widget) => (
    <WidgetRenderer
      widget={widget}
      liveTelem={liveTelem}
      historyData={historyData}
      alarms={alarms}
      intelligence={intelligence}
      deviceLastSeen={device?.last_seen_at}
      userRole={user?.role}
      deviceId={device?.id}
      allDevices={allDevices||[]}
      currentDevice={device}
    />
  ), [liveTelem, historyData, alarms, intelligence, device, user?.role, allDevices]);

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

  const handleRCA = async () => {
    if (!device?.id || rcaLoading) return;
    setRcaLoading(true);
    try {
      const result = await intelligenceApi.rca(device.id);
      setRcaResult(result);
    } catch(e) {}
    finally { setRcaLoading(false); }
  };

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
          {isAdmin && (
            <button onClick={handleRCA} disabled={rcaLoading}
              style={{ display:"flex", alignItems:"center", gap:6, padding:"7px 14px", borderRadius:8, border:"1px solid #D8E3F3", background: rcaLoading ? "#EAF2FF" : "#0B1426", color:"white", cursor: rcaLoading ? "wait" : "pointer", fontSize:13, fontWeight:500 }}>
              {rcaLoading
                ? <><div style={{width:12,height:12,border:"2px solid white",borderTopColor:"transparent",borderRadius:"50%",animation:"spin 1s linear infinite"}}/> Analysing…</>
                : <><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg> AI Analysis</>
              }
            </button>
          )}
          {isAdmin && <button onClick={() => setEditMode(e => !e)}
            style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: editMode ? "2px solid #3b82f6" : "1px solid #e2e8f0", background: editMode ? "#eff6ff" : "white", cursor: "pointer", fontSize: 13, fontWeight: 500, color: editMode ? "#1d4ed8" : "#475569" }}>
            <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            {editMode ? "Done Editing" : "Edit Layout"}
          </button>}
          {editMode && activeDash && (
            <button onClick={() => { setEditingWidget(null); setShowModal(true); }}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 8, border: "none", background: "#3b82f6", color: "white", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
              <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              Add Widget
            </button>
          )}
        </div>
      </div>

      {/* ── RCA Result Panel ── */}
      {rcaResult && (() => {
        // Parse the 5 sections from the analysis text
        const text = rcaResult.analysis || "";
        const sections = [
          { key:"1", icon:"🏥", label:"Health Status",       color:"#3b82f6", bg:"#eff6ff" },
          { key:"2", icon:"🔍", label:"Root Cause Analysis", color:"#ef4444", bg:"#fef2f2" },
          { key:"3", icon:"📈", label:"Trend Insights",      color:"#8b5cf6", bg:"#f5f3ff" },
          { key:"4", icon:"⚠️", label:"Risk Assessment",     color:"#f59e0b", bg:"#fffbeb" },
          { key:"5", icon:"✅", label:"Recommended Actions", color:"#10b981", bg:"#f0fdf4" },
        ];

        // Split text by section headers
        const parsedSections = sections.map((s, i) => {
          const next = sections[i + 1];
          const startRe = new RegExp(`\\*?\\*?${s.key}[.:].*?\\*?\\*?`, "i");
          const endRe   = next ? new RegExp(`\\*?\\*?${next.key}[.:]`, "i") : null;
          const startIdx = text.search(startRe);
          if (startIdx === -1) return { ...s, content: "" };
          const afterStart = text.slice(startIdx).replace(startRe, "").trim();
          const endIdx = endRe ? afterStart.search(endRe) : -1;
          const content = (endIdx === -1 ? afterStart : afterStart.slice(0, endIdx))
            .replace(/\*\*/g, "").trim();
          return { ...s, content };
        }).filter(s => s.content);

        const healthColor = text.toLowerCase().includes("critical") ? "#ef4444"
          : text.toLowerCase().includes("warning") ? "#f59e0b" : "#10b981";
        const healthLabel = text.toLowerCase().includes("critical") ? "CRITICAL"
          : text.toLowerCase().includes("warning") ? "WARNING" : "HEALTHY";

        return (
          <div style={{background:"linear-gradient(135deg,#0B1426,#1a2e5a)",borderRadius:16,overflow:"hidden",boxShadow:"0 8px 32px rgba(11,20,38,0.3)"}}>
            {/* Header */}
            <div style={{padding:"16px 20px",display:"flex",alignItems:"center",justifyContent:"space-between",borderBottom:"1px solid rgba(255,255,255,0.08)"}}>
              <div style={{display:"flex",alignItems:"center",gap:10}}>
                <div style={{width:36,height:36,borderRadius:"50%",background:"rgba(47,140,255,0.2)",border:"1px solid rgba(47,140,255,0.4)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:16}}>🧠</div>
                <div>
                  <p style={{margin:0,fontSize:13,fontWeight:700,color:"white"}}>AI Root Cause Analysis</p>
                  <p style={{margin:0,fontSize:10,color:"rgba(255,255,255,0.5)"}}>{device.name} · {rcaResult.generated_at ? new Date(rcaResult.generated_at).toLocaleString() : new Date().toLocaleString()}</p>
                </div>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:8}}>
                <div style={{padding:"3px 10px",borderRadius:20,background:`${healthColor}22`,border:`1px solid ${healthColor}44`,fontSize:10,fontWeight:700,color:healthColor}}>{healthLabel}</div>
                <span style={{fontSize:9,color:"rgba(255,255,255,0.3)",background:"rgba(255,255,255,0.05)",padding:"2px 8px",borderRadius:4}}>{rcaResult.engine}</span>
                <button onClick={()=>setRcaResult(null)} style={{background:"none",border:"none",cursor:"pointer",color:"rgba(255,255,255,0.4)",fontSize:18,lineHeight:1}}>×</button>
              </div>
            </div>

            {/* Sections */}
            <div style={{padding:"16px 20px",display:"flex",flexDirection:"column",gap:12}}>
              {parsedSections.length > 0 ? parsedSections.map(s => (
                <div key={s.key} style={{background:"rgba(255,255,255,0.04)",borderRadius:10,padding:"12px 14px",border:`1px solid rgba(255,255,255,0.06)`,borderLeft:`3px solid ${s.color}`}}>
                  <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:6}}>
                    <span style={{fontSize:14}}>{s.icon}</span>
                    <span style={{fontSize:11,fontWeight:700,color:s.color,textTransform:"uppercase",letterSpacing:"0.5px"}}>{s.label}</span>
                  </div>
                  <p style={{margin:0,fontSize:12,color:"rgba(255,255,255,0.8)",lineHeight:1.7,whiteSpace:"pre-wrap"}}>{s.content}</p>
                </div>
              )) : (
                // Fallback if sections can't be parsed
                <div style={{background:"rgba(255,255,255,0.04)",borderRadius:10,padding:"12px 14px",border:"1px solid rgba(255,255,255,0.06)"}}>
                  <p style={{margin:0,fontSize:12,color:"rgba(255,255,255,0.8)",lineHeight:1.7,whiteSpace:"pre-wrap"}}>{text.replace(/\*\*/g,"")}</p>
                </div>
              )}
            </div>
          </div>
        );
      })()}

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
          renderWidget={renderWidget}
        />
      )}

      {/* ── Widget modal ── */}
      {showModal && (
        <WidgetModal
          availableKeys={availableKeys.length ? availableKeys : ["temperature","humidity","voltage","pressure"]}
          onSave={handleSaveWidget}
          onClose={() => { setShowModal(false); setEditingWidget(null); }}
          editWidget={editingWidget}
          user={user}
        />
      )}
    </div>
  );
}
