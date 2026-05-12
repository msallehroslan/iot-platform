/**
 * App.jsx — root shell + all non-dashboard pages
 * Dashboard logic lives in pages/DashboardPage.jsx
 * API calls use services/api.js + services/dashboardService.js
 */
import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import DashboardPage from "./pages/DashboardPage.jsx";
import UserDashboardPage from "./pages/UserDashboardPage.jsx";
import { authApi, deviceApi, telemetryApi, alarmApi, statsApi, provisioningApi, userApi, customerApi, thresholdApi, rpcApi, widgetTemplateApi, metricsApi, apiKeysApi, systemApi, intelligenceApi, API_BASE } from "./services/api.js";
import { useDeviceTelemetry } from "./hooks/useTelemetry.js";
import { TelemetrySocket } from "./services/websocket.js";

// ── Shared chart: Sparkline ──────────────────────────────────────────────────
function Sparkline({ data = [], color = "#3b82f6", height = 44 }) {
  if (data.length < 2) return <div style={{ height }} className="flex items-end"><div className="w-full h-0.5 bg-slate-100 rounded" /></div>;
  const W = 300, H = height;
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1;
  const px = i => (i / (data.length - 1)) * W;
  const py = v => H - 2 - ((v - mn) / rng) * (H - 6);
  const d  = data.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `${d} L${px(data.length - 1)},${H} L0,${H} Z`;
  const gid = `sg${color.replace(/[^a-z0-9]/gi, "")}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.18" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(data.length - 1)} cy={py(data[data.length - 1])} r="3" fill={color} />
    </svg>
  );
}

function LineChart({ data = [], color = "#3b82f6" }) {
  // NaN-safe: drop non-finite values before any coordinate math
  const pts = (data || []).filter(p => {
    if (!p || !p.ts) return false;
    const n = typeof p.value === "number" ? p.value : parseFloat(p.value);
    return Number.isFinite(n);
  }).map(p => ({ ts: p.ts, value: typeof p.value === "number" ? p.value : parseFloat(p.value) }));

  if (pts.length < 2) return (
    <div className="flex flex-col items-center justify-center h-36 gap-2">
      <svg className="w-8 h-8 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      <p className="text-xs text-slate-400">No telemetry data yet</p>
    </div>
  );
  const W = 500, H = 150, pad = { t: 10, r: 10, b: 22, l: 32 };
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const vals = pts.map(p => p.value);
  // safeRange: guaranteed finite mn/mx/rng — no divide-by-zero
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const rng = (mx - mn) || 1;
  // px: safe denominator — max(1, n-1) prevents divide-by-zero on single point
  const px = i => pad.l + (i / Math.max(1, pts.length - 1)) * w;
  const py = v => { const y = pad.t + h - ((v - mn) / rng) * h; return Number.isFinite(y) ? y : pad.t; };
  // Build path skipping any non-finite coords (belt-and-suspenders)
  let pathStr = "", first = true;
  pts.forEach((_, i) => {
    const x = px(i), y = py(pts[i].value);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    pathStr += `${first ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)} `;
    first = false;
  });
  if (!pathStr) return <div className="h-36 bg-slate-50 rounded-xl"/>;
  const lastX = px(pts.length - 1), lastY = py(pts[pts.length - 1].value);
  const area = `${pathStr} L${lastX.toFixed(1)},${(pad.t+h).toFixed(1)} L${pad.l.toFixed(1)},${(pad.t+h).toFixed(1)} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      <defs><linearGradient id="lcA" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.12"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>
      {[0,.25,.5,.75,1].map(t => { const y=pad.t+h*t,val=(mx-rng*t).toFixed(1); return <g key={t}><line x1={pad.l} y1={y} x2={pad.l+w} y2={y} stroke="#f1f5f9" strokeWidth="1"/><text x={pad.l-4} y={y+3} fontSize="8" fill="#94a3b8" textAnchor="end" fontFamily="monospace">{val}</text></g>; })}
      {pts.filter((_,i)=>i%Math.max(1,Math.floor(pts.length/5))===0||i===pts.length-1).map((p,i,arr)=>{
        const origIdx = pts.indexOf(p);
        return <text key={origIdx} x={px(origIdx)} y={pad.t+h+15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">{new Date(p.ts).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</text>;
      })}
      <path d={area} fill="url(#lcA)"/>
      <path d={pathStr.trim()} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
      {Number.isFinite(lastX) && Number.isFinite(lastY) && (
        <circle cx={lastX.toFixed(1)} cy={lastY.toFixed(1)} r="4" fill={color} stroke="white" strokeWidth="2"/>
      )}
    </svg>
  );
}

// ── Badges ───────────────────────────────────────────────────────────────────
const SB = { ACTIVE:"inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200", INACTIVE:"inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-500 ring-1 ring-slate-200", DISABLED:"inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-50 text-red-600 ring-1 ring-red-200" };
const SD = { ACTIVE:"w-1.5 h-1.5 rounded-full bg-emerald-500", INACTIVE:"w-1.5 h-1.5 rounded-full bg-slate-400", DISABLED:"w-1.5 h-1.5 rounded-full bg-red-500" };
const SEVB = { CRITICAL:"inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-700", MAJOR:"inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-orange-100 text-orange-700", MINOR:"inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-yellow-100 text-yellow-700", WARNING:"inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-700", INDETERMINATE:"inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-slate-100 text-slate-600" };
const AST = { ACTIVE_UNACK:{label:"Active",cls:"inline-flex px-2 py-0.5 rounded text-xs font-medium bg-amber-50 text-amber-700 ring-1 ring-amber-200"}, ACTIVE_ACK:{label:"Acknowledged",cls:"inline-flex px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700 ring-1 ring-blue-200"}, CLEARED_UNACK:{label:"Cleared",cls:"inline-flex px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500 ring-1 ring-slate-200"}, CLEARED_ACK:{label:"Cleared",cls:"inline-flex px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"} };

// ── Atoms ────────────────────────────────────────────────────────────────────
function Toast({ msg, type="success", onDone }) {
  useEffect(() => { const t = setTimeout(onDone, 3000); return () => clearTimeout(t); }, []);
  return <div className={`fixed bottom-5 right-5 z-[200] ${type==="error"?"bg-red-500":"bg-emerald-500"} text-white text-sm font-medium px-4 py-2.5 rounded-xl shadow-lg flex items-center gap-2`}>{type==="error"?<svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>:<svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>}{msg}</div>;
}
function Spinner() { return <svg className="w-4 h-4 animate-spin text-slate-400" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/></svg>; }
function Empty({ icon, title, sub }) {
  return <div className="flex flex-col items-center justify-center py-16 gap-3"><div className="w-14 h-14 rounded-2xl bg-slate-100 flex items-center justify-center"><svg className="w-6 h-6 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={icon}/></svg></div><p className="text-sm font-semibold text-slate-600">{title}</p>{sub&&<p className="text-xs text-slate-400 text-center max-w-xs">{sub}</p>}</div>;
}
// ── NAV + SIDEBAR ────────────────────────────────────────────────────────────
const NAV = [
  { id:"overview",          label:"Overview",          icon:"M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z" },
  { id:"user-dashboards",   label:"My Dashboards",     icon:"M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z" },
  { id:"device-dashboards", label:"Device Dashboards", icon:"M4 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5zM14 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V5zM4 15a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4zM14 15a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-4z" },
  { id:"devices",           label:"Devices",           icon:"M2 3a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3zM8 21h8M12 17v4" },
  { id:"alarms",            label:"Alarms",            icon:"M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0" },
  { id:"rule-chains",       label:"Rule Chains",       icon:"M6 3v12m12-9a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6m12-9a9 9 0 0 1-9 9" },
  { id:"customers",         label:"Customers",         icon:"M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm14 2v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" },
  { id:"users",             label:"Users & Roles",     icon:"M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" },
  { id:"settings",          label:"Settings",          icon:"M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm6.93-3h1.07a2 2 0 0 1 0 4h-1.07A7 7 0 0 1 17 18.93V20a2 2 0 0 1-4 0v-1.07A7 7 0 0 1 11.07 16H10a2 2 0 0 1 0-4h1.07A7 7 0 0 1 13 4.07V3a2 2 0 0 1 4 0v1.07A7 7 0 0 1 18.93 6H20a2 2 0 0 1 0 4h-1.07" },
  { id:"api-keys",          label:"API Keys",           icon:"M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" },
  { id:"system-metrics",    label:"System Metrics",    icon:"M22 12h-4l-3 9L9 3l-3 9H2" },
  { id:"audit-log",         label:"Audit Log",          icon:"M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z" },
];

const Sidebar = React.memo(function Sidebar({ page, setPage, user, onLogout, alarmCount }) {
  const [col, setCol] = React.useState(false);
  const ini  = user ? (user.first_name?.[0] || user.email?.[0] || "U").toUpperCase() : "U";
  const name = user ? (user.first_name ? `${user.first_name} ${user.last_name||""}`.trim() : user.email) : "User";

  return (
    <aside style={{
      width: col ? 56 : 224, flexShrink: 0, display: "flex", flexDirection: "column",
      height: "100vh", background: "#EAF2FF", transition: "width 200ms", position: "relative",
    }}>
      {/* Logo */}
      <div style={{ padding: col ? "18px 14px" : "20px 16px 16px", borderBottom: "1px solid #D8E3F3", overflow: "hidden", minHeight: 60, display: "flex", alignItems: "center" }}>
        {!col
          ? <span style={{ fontWeight: 700, fontSize: 13, color: "#0B1426", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>TriAxis Nexus</span>
          : <div style={{ width: 28, height: 28, borderRadius: 8, background: "#2F8CFF", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2"><path d="M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z"/></svg>
            </div>
        }
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, overflowY: "auto", padding: col ? "12px 6px 0" : "12px 8px 0" }}>
        {!col && <p style={{ padding: "8px 12px 4px", fontSize: 10, fontWeight: 600, color: "#6B7F9F", textTransform: "uppercase", letterSpacing: "0.18em", margin: 0 }}>Menu</p>}
        {NAV.filter(({ id }) => {
          const adminOnly = ["customers", "users", "api-keys", "system-metrics", "audit-log"];
          if (adminOnly.includes(id) && user?.role !== "TENANT_ADMIN") return false;
          return true;
        }).map(({ id, label, icon }) => {
          const active = page === id;
          return (
            <button key={id} title={col ? label : undefined} onClick={() => setPage(id)}
              style={{
                width: "100%", display: "flex", alignItems: "center",
                gap: col ? 0 : 12, justifyContent: col ? "center" : "flex-start",
                padding: col ? "10px 0" : "10px 12px",
                borderRadius: 8, fontSize: 13,
                fontWeight: active ? 600 : 500,
                color: active ? "#0B4BB3" : "#334866",
                background: active ? "#D7E8FF" : "transparent",
                border: "none", cursor: "pointer", textAlign: "left", marginBottom: 2,
                transition: "background 150ms",
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = "rgba(215,232,255,0.6)"; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}>
              <svg style={{ width: 17, height: 17, flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d={icon}/>
              </svg>
              {!col && <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>}
              {!col && id === "alarms" && alarmCount > 0 && (
                <span style={{ background: "#EF4444", color: "#fff", fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 9999, minWidth: 18, textAlign: "center" }}>{alarmCount}</span>
              )}
              {!col && id === "device-dashboards" && (
                <span style={{ background: "#2F8CFF", color: "#fff", fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 9999 }}>NEW</span>
              )}
              {!col && active && !["alarms", "device-dashboards"].includes(id) && (
                <span style={{ width: 6, height: 6, borderRadius: 9999, background: "#2F8CFF", flexShrink: 0 }}/>
              )}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div style={{ borderTop: "1px solid #D8E3F3", padding: col ? "10px 6px" : 12, display: "flex", flexDirection: "column", gap: col ? 6 : 10 }}>
        {/* User */}
        <div onClick={onLogout}
          style={{ display: "flex", alignItems: "center", gap: col ? 0 : 10, justifyContent: col ? "center" : "flex-start", padding: "8px 10px", borderRadius: 8, cursor: "pointer", transition: "background 150ms" }}
          onMouseEnter={e => e.currentTarget.style.background = "#D7E8FF"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <div style={{ width: 28, height: 28, borderRadius: 9999, background: "#2F8CFF", display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 700, fontSize: 12, flexShrink: 0 }}>{ini}</div>
          {!col && <div style={{ overflow: "hidden", flex: 1 }}>
            <p style={{ fontSize: 12, fontWeight: 500, color: "#0B1426", margin: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{name}</p>
            <p style={{ fontSize: 10, color: "#6B7F9F", margin: 0 }}>{user?.role || "TENANT_ADMIN"} · Sign out</p>
          </div>}
        </div>

        {/* Branding */}
        {!col && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: 4 }}>
            <span style={{ fontSize: 9, color: "#6B7F9F" }}>In collaboration with</span>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, borderRight: "1px solid #C5D5E8", paddingRight: 10 }}>
                <img src="/taat-logo-2.png" alt="TAAT" style={{ height: 22 }}/>
                <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
                  <span style={{ fontSize: 9, fontWeight: 700, color: "#07142F" }}>TriAxis AI</span>
                  <span style={{ fontSize: 7, color: "#6B7F9F" }}>Technologies</span>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <img src="/greenson-logo.jpg" alt="Greenson" style={{ height: 16 }}/>
                <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
                  <span style={{ fontSize: 9, fontWeight: 700, color: "#0B1426" }}>Greenson</span>
                  <span style={{ fontSize: 7, color: "#6B7F9F" }}>Technology</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Collapse toggle */}
        <button onClick={() => setCol(c => !c)} style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "center",
          padding: "6px 0", borderRadius: 8, border: "none", background: "transparent",
          color: "#6B7F9F", cursor: "pointer", transition: "background 150ms",
        }}
          onMouseEnter={e => e.currentTarget.style.background = "#D7E8FF"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <svg style={{ width: 16, height: 16, transform: col ? "rotate(180deg)" : "none", transition: "transform 200ms" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </button>
      </div>
    </aside>
  );
}); // end Sidebar memo

const Header = React.memo(function Header({ title, onRefresh, refreshing }) {
  const [time, setTime] = React.useState(new Date());
  React.useEffect(() => { const t = setInterval(() => setTime(new Date()), 1000); return () => clearInterval(t); }, []);
  return (
    <header style={{
      height: 64, flexShrink: 0, display: "flex", alignItems: "center",
      justifyContent: "space-between", padding: "0 28px",
      background: "#F4F8FF", borderBottom: "1px solid #D8E3F3",
      boxShadow: "0 1px 2px rgba(59,130,246,0.04), 0 1px 3px rgba(15,23,42,0.04)",
    }}>
      <div>
        <h1 style={{ fontSize: 16, fontWeight: 700, color: "#0B1426", margin: 0 }}>{title}</h1>
        <p style={{ fontSize: 11, color: "#6B7F9F", marginTop: 2, margin: 0 }}>
          {time.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
        </p>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button onClick={onRefresh}
          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 500, color: "#334866", padding: "6px 12px", borderRadius: 8, background: "transparent", border: "none", cursor: "pointer", transition: "background 150ms" }}
          onMouseEnter={e => e.currentTarget.style.background = "#D7E8FF"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <svg style={{ width: 14, height: 14 }} className={refreshing ? "animate-spin" : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="23 4 23 10 17 10"/>
            <polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
          Refresh
        </button>
        <div style={{ width: 1, height: 16, background: "#D8E3F3" }}/>
        <span style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: "#6B7F9F" }}>
          {time.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}); // end Header memo

function OverviewPage({ refreshKey, onToast }) {
  const [stats,setStats]=useState(null); const [devices,setDevices]=useState([]); const [alarms,setAlarms]=useState([]); const [loading,setLoading]=useState(true);
  const [chartDev,setChartDev]=useState(null); const [chartKey,setChartKey]=useState("temperature"); const [chartData,setChartData]=useState([]); const [chartKeys,setChartKeys]=useState([]);
  const [summaries,setSummaries]=useState({}); const [summaryLoading,setSummaryLoading]=useState(false);

  const fetchAll=useCallback(async()=>{
    try{
      const[s,d,a]=await Promise.all([statsApi.get(),deviceApi.list({limit:20}),alarmApi.list({limit:5})]);
      setStats(s);setDevices(d);setAlarms(a);
      // Use functional update to avoid stale chartDev closure
      setChartDev(prev=>prev||(d.length>0?d[0]:null));
    }catch(e){onToast(e.message,"error");}
    finally{setLoading(false);}
  }, [onToast]);

  useEffect(()=>{fetchAll();},[refreshKey]);

  // Fetch AI summaries — two separate effects:
  // 1. Fires immediately when devices first arrive (no throttle on first load)
  // 2. Refreshes every 60s via refreshKey, but skips if fetched recently
  const lastSummaryRef = useRef(0);
  const hasFetchedRef  = useRef(false);

  const fetchSummaries = (activeDevs) => {
    if (!activeDevs.length) return;
    lastSummaryRef.current = Date.now();
    Promise.allSettled(activeDevs.map(d=>intelligenceApi.summary(d.id).then(r=>({id:d.id,data:r}))))
      .then(results=>{
        const updates={};
        results.forEach(r=>{ if(r.status==="fulfilled" && r.value?.data) updates[r.value.id]=r.value.data; });
        if(Object.keys(updates).length>0){
          setSummaries(prev=>({...prev,...updates}));
        }
      });
  };

  // Effect 1: run immediately when devices first load (hasFetched guard prevents re-runs)
  useEffect(()=>{
    const activeDevs = devices.filter(d=>d.status==="ACTIVE").slice(0,6);
    if(!activeDevs.length) return;
    if(hasFetchedRef.current) return; // already fetched once this session
    hasFetchedRef.current = true;
    fetchSummaries(activeDevs);
  },[devices.length]);

  // Effect 2: periodic refresh via refreshKey — throttled to 60s minimum gap
  useEffect(()=>{
    if(!hasFetchedRef.current) return; // wait for initial fetch first
    const activeDevs = devices.filter(d=>d.status==="ACTIVE").slice(0,6);
    if(!activeDevs.length) return;
    const now = Date.now();
    if(now - lastSummaryRef.current < 60_000) return; // throttle
    fetchSummaries(activeDevs);
  },[refreshKey]);

  useEffect(()=>{if(!chartDev)return;telemetryApi.keys(chartDev.id).then(r=>{
    const NON_SENSOR = new Set(["latitude","longitude","lat","lng","lon","gps_lat","gps_lng","altitude","rssi","snr","battery","signal"]);
    const ks=(r?.keys||[]).filter(k=>!NON_SENSOR.has(k.toLowerCase()));
    setChartKeys(ks);
    if(ks.length>0&&!ks.includes(chartKey))setChartKey(ks[0]);
  }).catch(()=>{});},[chartDev?.id]);
  useEffect(()=>{
    if(!chartDev||!chartKey)return;
    telemetryApi.history(chartDev.id,chartKey,50).then(raw=>{
      // Sanitize: drop non-finite values that cause cliff artifacts
      const clean=(raw||[]).filter(p=>{
        const n=typeof p.value==="number"?p.value:parseFloat(p.value);
        return Number.isFinite(n);
      }).map(p=>({ts:p.ts,value:typeof p.value==="number"?p.value:parseFloat(p.value)}));
      setChartData(clean);
    }).catch(()=>setChartData([]));
  },[chartDev?.id,chartKey]);
  useEffect(()=>{
    if(!chartDev?.id||!chartKey)return;
    // Buffer WS points — flush to state every 500ms to avoid per-message renders
    const pendingPts = [];
    const unsub = TelemetrySocket.subscribe(chartDev.id,[chartKey],(vals,ts)=>{
      if(!(chartKey in vals))return;
      const n = typeof vals[chartKey]==="number" ? vals[chartKey] : parseFloat(vals[chartKey]);
      if(!Number.isFinite(n)) return;
      pendingPts.push({ts, value:n});
    });
    const flush = setInterval(()=>{
      if(!pendingPts.length) return;
      const toAdd = pendingPts.splice(0);
      setChartData(prev=>{
        const a=[...prev,...toAdd];
        return a.length>50 ? a.slice(-50) : a;
      });
    }, 500);
    return ()=>{ unsub(); clearInterval(flush); };
  },[chartDev?.id,chartKey]);

  const active=devices.filter(d=>d.status==="ACTIVE");
  const HEALTH_COLOR = { HEALTHY:"#10b981", WARNING:"#f59e0b", CRITICAL:"#ef4444" };
  const HEALTH_BG    = { HEALTHY:"#f0fdf4", WARNING:"#fffbeb", CRITICAL:"#fef2f2" };
  const TREND_ICON   = { RISING:"↑", FALLING:"↓", STABLE:"→", SPIKE:"⚡", DROP:"⬇", VOLATILE:"〜", UNKNOWN:"?" };

  return (
    <div className="space-y-6">
      {/* ── Stat cards ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {[{label:"Total Devices",value:stats?.total_devices,color:"#3b82f6",bg:"bg-[#EAF2FF]",ic:"text-[#2F8CFF]",path:"M2 3a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3zM8 21h8M12 17v4"},{label:"Active Nodes",value:stats?.active_devices,color:"#10b981",bg:"bg-emerald-50",ic:"text-emerald-500",path:"M1.42 9a16 16 0 0 1 21.16 0M5 12.55a11 11 0 0 1 14.08 0M10.83 15.76a6.06 6.06 0 0 1 2.34 0M12 20h.01"},{label:"Active Alarms",value:stats?.active_alarms,color:"#f59e0b",bg:"bg-amber-50",ic:"text-amber-500",path:"M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0"},{label:"Events Today",value:stats?.telemetry_today?.toLocaleString(),color:"#8b5cf6",bg:"bg-violet-50",ic:"text-violet-500",path:"M4 7c0-1.1.9-2 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7zm0 5h16"}].map(({label,value,color,bg,ic,path})=>(
          <div key={label} style={{background:"#FFFFFF",border:"1px solid #D8E3F3",borderRadius:16,boxShadow:"0 1px 2px rgba(59,130,246,0.04), 0 1px 3px rgba(15,23,42,0.04)",padding:20,display:"flex",flexDirection:"column",gap:12,transition:"box-shadow 200ms",cursor:"default"}} onMouseEnter={e=>e.currentTarget.style.boxShadow="0 4px 6px -1px rgba(59,130,246,0.08), 0 2px 4px -2px rgba(15,23,42,0.06)"} onMouseLeave={e=>e.currentTarget.style.boxShadow="0 1px 2px rgba(59,130,246,0.04), 0 1px 3px rgba(15,23,42,0.04)"}>
            <div className="flex items-start justify-between"><div><p className="text-[11px] font-semibold uppercase tracking-widest text-[#6B7F9F] mb-1">{label}</p><p className="text-3xl font-bold text-[#0B1426] leading-none">{loading?"—":(value??0)}</p></div><div className={`w-11 h-11 rounded-xl ${bg} flex items-center justify-center flex-shrink-0`}><svg className={`w-5 h-5 ${ic}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={path}/></svg></div></div>
            {/* Trend line removed — was fake sine wave unrelated to real data */}
            <div style={{height:36,borderRadius:8,background:`${color}08`,display:"flex",alignItems:"center",justifyContent:"center"}}>
              <div style={{width:"85%",height:2,borderRadius:2,background:`${color}20`}}/>
            </div>
          </div>
        ))}
      </div>

      {/* ── Intelligence Panel ── */}
      {active.length>0&&(
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-[#0B1426]">Fleet Intelligence</h2>
              <span className="text-[10px] font-medium bg-[#EAF2FF] text-[#2F8CFF] px-2 py-0.5 rounded-full">AI</span>
            </div>
            {summaryLoading&&<div className="flex items-center gap-1.5 text-[11px] text-[#6B7F9F]"><div style={{width:10,height:10,border:"1.5px solid #6B7F9F",borderTopColor:"transparent",borderRadius:"50%",animation:"spin 1s linear infinite"}}/> Analysing…</div>}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {active.slice(0,6).map(d=>{
              const s=summaries[d.id];
              const health=s?.health||"UNKNOWN";
              const hc=HEALTH_COLOR[health]||"#94a3b8";
              const hbg=HEALTH_BG[health]||"#f8fafc";
              const trends=s?.trends||{};
              const insights=s?.insights||[];
              return (
                <div key={d.id} style={{background:"#FFFFFF",border:"1px solid #D8E3F3",borderRadius:16,boxShadow:"0 1px 2px rgba(59,130,246,0.04)",padding:16,transition:"box-shadow 200ms"}} onMouseEnter={e=>e.currentTarget.style.boxShadow="0 4px 6px -1px rgba(59,130,246,0.08), 0 2px 4px -2px rgba(15,23,42,0.06)"} onMouseLeave={e=>e.currentTarget.style.boxShadow="0 1px 2px rgba(59,130,246,0.04)"}>
                  {/* Header */}
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <div style={{width:8,height:8,borderRadius:"50%",background:hc,flexShrink:0}}/>
                      <p className="text-xs font-semibold text-[#0B1426] truncate">{d.name}</p>
                    </div>
                    <span className="text-[9px] font-bold px-2 py-0.5 rounded-full flex-shrink-0" style={{background:hbg,color:hc}}>{health}</span>
                  </div>
                  {/* Trend pills */}
                  {Object.keys(trends).length>0&&(
                    <div className="flex flex-wrap gap-1 mb-3">
                      {Object.entries(trends).filter(([key])=>!["latitude","longitude","lat","lng","lon","gps_lat","gps_lng","altitude","rssi","snr","battery","signal"].includes(key.toLowerCase())).slice(0,4).map(([key,trend])=>(
                        <span key={key} className="text-[9px] px-1.5 py-0.5 rounded-md font-medium" style={{background:"#F4F8FF",color:"#334866"}}>
                          {TREND_ICON[trend]||"?"} {key}
                        </span>
                      ))}
                    </div>
                  )}
                  {/* Insights */}
                  {summaryLoading&&!s?(
                    <div className="space-y-1.5">{[1,2].map(i=><div key={i} className="h-3 bg-slate-100 rounded animate-pulse" style={{width:i===1?"80%":"60%"}}/>)}</div>
                  ):(
                    <div className="space-y-1">
                      {insights.slice(0,2).map((ins,i)=>(
                        <p key={i} className="text-[10px] text-[#6B7F9F] leading-relaxed">{ins}</p>
                      ))}
                      {!s&&<p className="text-[10px] text-[#94a3b8]">Waiting for analysis…</p>}
                    </div>
                  )}
                  {/* Alarms badge */}
                  {s?.active_alarms>0&&(
                    <div className="mt-3 flex items-center gap-1.5 text-[10px] text-red-600 bg-red-50 px-2 py-1 rounded-lg">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:10,height:10}}><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0"/></svg>
                      {s.active_alarms} active alarm{s.active_alarms>1?"s":""}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Telemetry + Alarms ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        <div className="col-span-2 rounded-2xl border shadow-sm shadow-blue-100/40 p-5" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
          <div className="flex items-start justify-between mb-5"><div><h2 className="text-sm font-semibold text-[#0B1426]">Telemetry History</h2><p className="text-xs text-[#6B7F9F] mt-0.5">Last 50 points</p></div>
            <div className="flex items-center gap-2">
              {devices.length>0&&<select value={chartDev?.id||""} onChange={e=>setChartDev(devices.find(d=>d.id===e.target.value))} className="text-xs border rounded-lg px-3 py-1.5 bg-white outline-none cursor-pointer max-w-[140px]" style={{borderColor:"#D8E3F3",color:"#334866"}}>{devices.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select>}
              {chartKeys.length>0?<select value={chartKey} onChange={e=>setChartKey(e.target.value)} className="text-xs border rounded-lg px-3 py-1.5 bg-white outline-none cursor-pointer" style={{borderColor:"#D8E3F3",color:"#334866"}}>{chartKeys.map(k=><option key={k}>{k}</option>)}</select>:<input value={chartKey} onChange={e=>setChartKey(e.target.value)} className="text-xs border border-slate-200 rounded-lg px-3 py-1.5 w-28 bg-white text-slate-600 outline-none" placeholder="key…"/>}
            </div>
          </div>
          <LineChart data={chartData} color="#3b82f6"/>
          <div className="flex items-center justify-end gap-2 mt-3"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"/><span className="text-[10px] text-slate-400 font-medium">{chartData.length} pts · LIVE</span></div>
        </div>
        <div className="rounded-2xl border shadow-sm shadow-blue-100/40 p-5" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
          <div className="flex items-center justify-between mb-4"><h2 className="text-sm font-semibold text-[#0B1426]">Recent Alarms</h2>{alarms.length>0&&<span className="text-[10px] font-semibold bg-red-50 text-red-600 px-2 py-0.5 rounded-full">{alarms.length}</span>}</div>
          {alarms.length===0?<Empty icon="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0" title="No alarms" sub="System healthy"/>:<div className="space-y-2">{alarms.map(a=><div key={a.id} className="flex items-start gap-3 p-3 rounded-xl border bg-[#F4F8FF]" style={{borderColor:"#D8E3F3"}}><span className={SEVB[a.severity]||SEVB.INDETERMINATE}>{a.severity}</span><div className="min-w-0"><p className="text-xs font-medium text-slate-700 truncate">{a.alarm_type}</p><p className="text-[10px] text-slate-400 mt-0.5 truncate">{a.device_name||"—"} · {new Date(a.start_ts).toLocaleTimeString()}</p></div></div>)}</div>}
        </div>
      </div>
      {active.length>0&&<div><h2 className="text-sm font-semibold text-[#0B1426] mb-3">Latest Telemetry</h2><div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">{active.slice(0,8).map(d=><TelCard key={d.id} device={d}/>)}</div></div>}
      {/* Fleet Intelligence Panel */}
    </div>
  );
}

function TelCard({ device }) {
  // Real-time via WebSocket; falls back to REST polling if WS unavailable
  const { values, ts, connected } = useDeviceTelemetry(device.id);
  const rows = Object.entries(values).map(([key, value]) => ({ key, value }));
  return (
    <div className="rounded-2xl border p-4 shadow-sm shadow-blue-100/40 hover:shadow-md hover:shadow-blue-100/70 transition-shadow" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
      <div className="flex items-start justify-between mb-3">
        <div className="min-w-0"><p className="text-xs font-semibold text-slate-700 truncate">{device.name}</p><p className="text-[10px] text-slate-400 mt-0.5">{device.device_type}</p></div>
        <div className="flex items-center gap-2">
          <span style={{width:6,height:6,borderRadius:"50%",background:connected?"#10b981":"#94a3b8",flexShrink:0,display:"inline-block"}} title={connected?"WebSocket live":"Polling"} />
          <span className={SB[device.status]||SB.INACTIVE}><span className={SD[device.status]||SD.INACTIVE}/>{device.status==="ACTIVE"?"Live":device.status.charAt(0)+device.status.slice(1).toLowerCase()}</span>
        </div>
      </div>
      {(() => {
        const NON_SENSOR = new Set(["latitude","longitude","lat","lng","lon","gps_lat","gps_lng","altitude","rssi","snr","battery","signal"]);
        const sensorRows = rows.filter(r => !NON_SENSOR.has(r.key.toLowerCase()));
        return sensorRows.length===0
          ? <p className="text-[11px] text-slate-400 py-2">No telemetry</p>
          : <div className="divide-y divide-slate-50">{sensorRows.slice(0,5).map(r=><div key={r.key} className="flex items-center justify-between py-1.5"><span className="text-[11px] text-slate-500">{r.key}</span><span className="text-[11px] font-semibold font-mono text-slate-800">{typeof r.value==="number"?r.value.toFixed(2):String(r.value??"—")}</span></div>)}</div>;
      })()}
      {ts&&<p className="text-[10px] text-slate-400 mt-3">{new Date(ts).toLocaleTimeString()}</p>}
    </div>
  );
}
// ── Device List for dashboards ───────────────────────────────────────────────
function DeviceListForDashboards({ onOpen }) {
  const [devices,setDevices]=useState([]); const [loading,setLoading]=useState(true); const [search,setSearch]=useState("");
  useEffect(()=>{
    deviceApi.list({limit:50})
      .then(r=>{ const arr=Array.isArray(r)?r:(r?.items||[]); setDevices(arr); })
      .catch(()=>{})
      .finally(()=>setLoading(false));
  },[]);
  const filtered=devices.filter(d=>d?.name?.toLowerCase().includes(search.toLowerCase()));
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div><h2 className="text-sm font-semibold text-[#0B1426]">Device Dashboards</h2><p className="text-xs text-[#6B7F9F] mt-0.5">Select a device to build its custom widget dashboard</p></div>
        <div className="relative"><svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg><input className="pl-9 pr-4 py-2 text-sm border rounded-lg bg-white text-[#334866] outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 w-56" style={{borderColor:"#D8E3F3"}} placeholder="Search…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
      </div>
      {loading?<div className="flex justify-center py-12"><Spinner/></div>:
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(d=>(
            <div key={d.id} onClick={()=>onOpen(d)} className="bg-white rounded-2xl border p-5 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all shadow-sm shadow-blue-100/40 relative overflow-hidden" style={{borderColor:"#D8E3F3"}}>
              <div className="absolute top-0 inset-x-0 h-0.5" style={{background:d.status==="ACTIVE"?"linear-gradient(to right,#10b981,#3b82f6)":"#e2e8f0",borderRadius:"12px 12px 0 0"}}/>
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3"><div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center"><svg className="w-5 h-5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div><div><p className="text-sm font-semibold text-[#0B1426]">{d.name}</p><p className="text-[10px] text-slate-400 mt-0.5">{d.device_type}{d.label?` · ${d.label}`:""}</p></div></div>
                <span className={`${SB[d.status]||SB.INACTIVE} text-[10px]`}><span className={SD[d.status]||SD.INACTIVE}/>{d.status}</span>
              </div>
              <div className="flex items-center justify-between"><p className="text-[10px] text-slate-400">Open dashboard →</p><div className="flex items-center gap-1 text-xs font-medium text-blue-500">Open<svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 18 15 12 9 6"/></svg></div></div>
            </div>
          ))}
        </div>}
    </div>
  );
}

// ── Devices page ─────────────────────────────────────────────────────────────
const INP = "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-700 bg-slate-50 outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 focus:bg-white";

function DevicesPage({ onOpenDrawer, onToast, user }) {
  const isAdmin = user?.role === "TENANT_ADMIN";
  const PAGE_SIZE = 20;

  const [devices,  setDevices]  = useState([]);
  const [total,    setTotal]    = useState(0);
  const [page,     setPage]     = useState(1);
  const [search,   setSearch]   = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [loading,  setLoading]  = useState(true);
  const [showM,    setShowM]    = useState(false);
  const [editDev,  setEditDev]  = useState(null);
  const [delId,    setDelId]    = useState(null);

  // Debounce search — 300ms after last keystroke, reset to page 1
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchPage = useCallback(async (pg, q) => {
    setLoading(true);
    try {
      const params = { page: pg, page_size: PAGE_SIZE };
      if (q) params.search = q;
      const res = await deviceApi.listPaged(params);
      setDevices(res?.items || []);
      setTotal(res?.total  || 0);
    } catch (e) { onToast(e.message, "error"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPage(page, debouncedSearch); }, [page, debouncedSearch]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleDel = async id => {
    if (!window.confirm("Delete this device? This will remove all its telemetry, alarms, rules and history. This cannot be undone.")) return;
    try { await deviceApi.delete(id); onToast("Device deleted"); fetchPage(page, debouncedSearch); }
    catch (e) { onToast(e.message, "error"); }
  };

  const handleSaved = () => {
    setShowM(false); setEditDev(null);
    onToast(editDev ? "Device updated" : "Device created");
    fetchPage(page, debouncedSearch);
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4">
        <div className="relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          <input className="pl-9 pr-4 py-2 text-sm border rounded-lg bg-white text-[#334866] outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 w-64" style={{borderColor:"#D8E3F3"}}
            placeholder="Search by name or type…" value={search} onChange={e => setSearch(e.target.value)}/>
        </div>
        {isAdmin && (
          <button onClick={() => { setEditDev(null); setShowM(true); }}
            className="flex items-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors shadow-sm shadow-blue-500/25">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add Device
          </button>
        )}
      </div>

      {/* Table */}
      <div className="rounded-2xl border shadow-sm shadow-blue-100/40 overflow-hidden" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
        {loading ? (
          <div className="flex justify-center py-12"><Spinner/></div>
        ) : devices.length === 0 ? (
          <Empty icon="M2 3a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3zM8 21h8M12 17v4"
            title={debouncedSearch ? "No match" : "No devices"} sub="Add your first device"/>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-100 bg-slate-50">
                {["Device","Type","Status","Token","Created",""].map(h => (
                  <th key={h} className="text-left px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-400">{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {devices.map(d => (
                  <tr key={d.id} onClick={() => onOpenDrawer(d)}
                    className="border-b border-slate-50 last:border-0 hover:bg-slate-50 cursor-pointer transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center flex-shrink-0">
                          <svg className="w-4 h-4 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                        </div>
                        <div><p className="font-medium text-slate-700">{d.name}</p>{d.label && <p className="text-[11px] text-slate-400">{d.label}</p>}</div>
                      </div>
                    </td>
                    <td className="px-5 py-3.5"><span className="text-[11px] font-mono bg-slate-100 text-slate-600 px-2 py-0.5 rounded">{d.device_type}</span></td>
                    <td className="px-5 py-3.5"><span className={SB[d.status]||SB.INACTIVE}><span className={SD[d.status]||SD.INACTIVE}/>{d.status}</span></td>
                    <td className="px-5 py-3.5 font-mono text-[11px] text-slate-400">{d.token.slice(0,8)}…</td>
                    <td className="px-5 py-3.5 text-[12px] text-slate-400">{new Date(d.created_at).toLocaleDateString()}</td>
                    <td className="px-5 py-3.5" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center gap-1 justify-end">
                        {isAdmin && <button onClick={() => { setEditDev(d); setShowM(true); }}
                          className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition-colors">
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>}
                        {isAdmin && <button onClick={() => handleDel(d.id)}
                          className="p-1.5 rounded-lg transition-colors hover:bg-red-50 text-slate-400 hover:text-red-500">
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                        </button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Footer: count + pagination controls */}
            <div className="px-5 py-3 bg-slate-50 border-t border-slate-100 flex items-center justify-between">
              <span className="text-[11px] text-slate-400">
                {total === 0 ? "No devices" : `Showing ${(page-1)*PAGE_SIZE+1}–${Math.min(page*PAGE_SIZE,total)} of ${total} device${total!==1?"s":""}`}
              </span>
              {totalPages > 1 && (
                <div className="flex items-center gap-1">
                  <button onClick={() => setPage(p => Math.max(1, p-1))} disabled={page===1}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
                    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
                  </button>
                  {Array.from({length:totalPages},(_,i)=>i+1)
                    .filter(p => p===1 || p===totalPages || Math.abs(p-page)<=1)
                    .reduce((acc,p,i,arr) => { if(i>0 && p-arr[i-1]>1) acc.push("…"); acc.push(p); return acc; }, [])
                    .map((p,i) => p==="…"
                      ? <span key={`e${i}`} className="px-1 text-[11px] text-slate-400">…</span>
                      : <button key={p} onClick={() => setPage(p)}
                          className={`min-w-[28px] h-7 rounded-lg text-[11px] font-semibold transition-colors ${page===p ? "bg-[#2F8CFF] text-white" : "text-slate-500 hover:bg-slate-200"}`}>
                          {p}
                        </button>
                    )
                  }
                  <button onClick={() => setPage(p => Math.min(totalPages, p+1))} disabled={page===totalPages}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
                    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 18 15 12 9 6"/></svg>
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {showM && <DeviceModal device={editDev} onSaved={handleSaved} onClose={() => { setShowM(false); setEditDev(null); }} onToast={onToast}/>}
    </div>
  );
}


function DeviceModal({ device, onSaved, onClose, onToast }) {
  const isEdit=!!device;
  const [form,setForm]=useState({name:device?.name||"",device_type:device?.device_type||"DEFAULT",label:device?.label||"",description:device?.description||"",status:device?.status||"INACTIVE",customer_id:device?.customer_id||"",latitude:device?.latitude||"",longitude:device?.longitude||""});
  const [saving,setSaving]=useState(false); const [err,setErr]=useState("");
  const [customers,setCustomers]=useState([]);
  useEffect(()=>{ customerApi.list().then(setCustomers).catch(()=>{}); },[]);
  const set=(k,v)=>setForm(f=>({...f,[k]:v}));
  const submit=async()=>{
    if(!form.name.trim()){setErr("Name required");return;}
    setSaving(true);setErr("");
    try{
      const lat=form.latitude&&form.latitude!==""?parseFloat(form.latitude):null;const lng=form.longitude&&form.longitude!==""?parseFloat(form.longitude):null;const payload={name:form.name,device_type:form.device_type,label:form.label,description:form.description,customer_id:form.customer_id||null,latitude:lat,longitude:lng};
      const s=isEdit?await deviceApi.update(device.id,{...payload,status:form.status}):await deviceApi.create(payload);
      onSaved(s);
    }catch(e){setErr(e.message);}finally{setSaving(false);}
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md border border-slate-100">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100"><h3 className="font-semibold text-slate-800">{isEdit?"Edit Device":"Add Device"}</h3><button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400"><svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>
        <div className="p-6 space-y-4">
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Name *</label><input className={INP} placeholder="My Sensor" value={form.name} onChange={e=>set("name",e.target.value)}/></div>
          <div className="grid grid-cols-2 gap-3"><div><label className="block text-xs font-medium text-slate-500 mb-1.5">Type</label><select className={INP+" cursor-pointer"} value={form.device_type} onChange={e=>set("device_type",e.target.value)}>{["DEFAULT","GATEWAY","SENSOR","ACTUATOR","METER","CAMERA"].map(t=><option key={t}>{t}</option>)}</select></div>{isEdit&&<div><label className="block text-xs font-medium text-slate-500 mb-1.5">Status</label><select className={INP+" cursor-pointer"} value={form.status} onChange={e=>set("status",e.target.value)}>{["ACTIVE","INACTIVE","DISABLED"].map(s=><option key={s}>{s}</option>)}</select></div>}</div>
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Assign to Customer <span className="text-slate-300 font-normal">(optional)</span></label><select className={INP+" cursor-pointer"} value={form.customer_id||""} onChange={e=>set("customer_id",e.target.value)}><option value="">— No customer (tenant-wide) —</option>{customers.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Label</label><input className={INP} placeholder="Building A" value={form.label} onChange={e=>set("label",e.target.value)}/></div>
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Description</label><textarea className={INP+" resize-none"} rows={2} value={form.description} onChange={e=>set("description",e.target.value)}/></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="block text-xs font-medium text-slate-500 mb-1.5">📍 Latitude <span className="text-slate-300 font-normal">(fixed location)</span></label><input className={INP} placeholder="e.g. 1.4927" type="number" step="any" value={form.latitude} onChange={e=>set("latitude",e.target.value)}/></div>
            <div><label className="block text-xs font-medium text-slate-500 mb-1.5">📍 Longitude</label><input className={INP} placeholder="e.g. 103.7414" type="number" step="any" value={form.longitude} onChange={e=>set("longitude",e.target.value)}/></div>
          </div>
          {err&&<p className="text-xs text-red-500 bg-red-50 px-3 py-2 rounded-lg">{err}</p>}
          <div className="flex gap-2 pt-1"><button onClick={submit} disabled={saving} className="flex-1 flex items-center justify-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] disabled:opacity-60 text-white font-medium text-sm py-2.5 rounded-lg">{saving&&<Spinner/>}{isEdit?"Update":"Create"}</button><button onClick={onClose} className="px-4 border border-slate-200 text-sm text-slate-500 rounded-lg hover:bg-slate-50">Cancel</button></div>
        </div>
      </div>
    </div>
  );
}
// ── Alarms page ───────────────────────────────────────────────────────────────
function AlarmsPage({ onToast, user }) {
  const canAck = user?.role === "TENANT_ADMIN" || user?.role === "TENANT_USER";
  const [alarms,setAlarms]=useState([]); const [loading,setLoading]=useState(true); const [filter,setFilter]=useState("ACTIVE");
  const fetchAlarms=useCallback(async()=>{try{const p={};if(filter==="ACTIVE")p.status="ACTIVE_UNACK";else if(filter==="ACK")p.status="ACTIVE_ACK";else if(filter==="CLEARED")p.status="CLEARED_ACK";const data=await alarmApi.list(p);const ord={CRITICAL:0,MAJOR:1,MINOR:2,WARNING:3,INDETERMINATE:4};data.sort((a,b)=>(ord[a.severity]??5)-(ord[b.severity]??5));setAlarms(data);}catch(e){onToast(e.message,"error");}finally{setLoading(false);}}, [filter]);
  useEffect(()=>{setLoading(true);fetchAlarms();},[filter]);
  const handleAck=async id=>{try{const u=await alarmApi.ack(id);setAlarms(as=>as.map(a=>a.id===id?{...a,...u}:a));onToast("Acknowledged");}catch(e){onToast(e.message,"error");}};
  const handleClear=async id=>{try{const u=await alarmApi.clear(id);setAlarms(as=>filter!=="ALL"?as.filter(a=>a.id!==id):as.map(a=>a.id===id?{...a,...u}:a));onToast("Cleared");}catch(e){onToast(e.message,"error");}};
  const handleDel=async id=>{try{await alarmApi.delete(id);setAlarms(as=>as.filter(a=>a.id!==id));onToast("Deleted");}catch(e){onToast(e.message,"error");}};
  const unack=alarms.filter(a=>a.status==="ACTIVE_UNACK").length, crit=alarms.filter(a=>a.severity==="CRITICAL").length;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">{[{l:"Unacknowledged",v:unack,c:"text-amber-500",b:"bg-amber-50 border-amber-100"},{l:"Critical",v:crit,c:"text-red-500",b:"bg-red-50 border-red-100"},{l:"Total",v:alarms.length,c:"text-[#2F8CFF]",b:"bg-blue-50 border-blue-100"}].map(x=><div key={x.l} className={`rounded-xl border p-4 flex items-center gap-4 shadow-sm ${x.b}`}><span className={`text-3xl font-bold ${x.c}`}>{x.v}</span><span className="text-xs text-slate-500 font-medium">{x.l}</span></div>)}</div>
      <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-lg w-fit">{["ACTIVE","ACK","CLEARED","ALL"].map(f=><button key={f} onClick={()=>setFilter(f)} className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-all ${filter===f?"bg-white text-slate-800 shadow-sm":"text-slate-500 hover:text-slate-700"}`}>{f==="ACK"?"Acknowledged":f==="ACTIVE"?"Active":f.charAt(0)+f.slice(1).toLowerCase()}</button>)}</div>
      <div className="rounded-2xl border shadow-sm shadow-blue-100/40 overflow-hidden" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
        {loading?<div className="flex justify-center py-12"><Spinner/></div>:alarms.length===0?<Empty icon="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0" title="No alarms" sub="Nothing for this filter"/>:
          <table className="w-full text-sm"><thead><tr className="border-b border-slate-100 bg-slate-50">{["Severity","Alarm Type","Device","Status","Triggered","Actions"].map(h=><th key={h} className="text-left px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-400">{h}</th>)}</tr></thead>
            <tbody>{alarms.map(a=>{const si=AST[a.status]||AST.ACTIVE_UNACK;return(
              <tr key={a.id} className="border-b border-slate-50 last:border-0 hover:bg-slate-50 transition-colors">
                <td className="px-5 py-3.5"><span className={SEVB[a.severity]||SEVB.INDETERMINATE}>{a.severity}</span></td>
                <td className="px-5 py-3.5"><p className="font-medium text-slate-700 text-[13px]">{a.alarm_type}</p>{a.details?.message&&<p className="text-[11px] text-[#6B7F9F] mt-0.5">{a.details.message}</p>}</td>
                <td className="px-5 py-3.5 text-[13px] text-slate-600">{a.device_name||"—"}</td>
                <td className="px-5 py-3.5"><span className={si.cls}>{si.label}</span></td>
                <td className="px-5 py-3.5 text-[12px] text-slate-400">{new Date(a.start_ts).toLocaleString()}</td>
                <td className="px-5 py-3.5"><div className="flex items-center gap-1">
                  {canAck&&(a.status==="ACTIVE_UNACK"||a.status==="CLEARED_UNACK")&&<button onClick={()=>handleAck(a.id)} className="p-1.5 rounded-lg hover:bg-emerald-50 text-slate-400 hover:text-emerald-600"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg></button>}
                  {canAck&&!a.status.startsWith("CLEARED")&&<button onClick={()=>handleClear(a.id)} className="p-1.5 rounded-lg hover:bg-blue-50 text-slate-400 hover:text-blue-500"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></button>}
                  <button onClick={()=>handleDel(a.id)} className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button>
                </div></td>
              </tr>);})}</tbody>
          </table>}
      </div>
    </div>
  );
}

// ── Device Drawer ─────────────────────────────────────────────────────────────
const DrawerMemo = React.memo(function DeviceDrawer({ device: initDev, onClose, refreshKey, onToast, user }) {
  const isAdmin = user?.role === "TENANT_ADMIN";
  const [device,setDevice]=useState(initDev); const [chartData,setChartData]=useState([]); const [selKey,setSelKey]=useState(""); const [copied,setCopied]=useState(false); const [regen,setRegen]=useState(false);
  const BASE_URL=(typeof import.meta!=="undefined"&&import.meta.env?.VITE_API_URL)||"http://localhost:8000";
  // Real-time via WebSocket; keys come from useTelemetry
  const { values: liveMap, connected: wsLive } = useDeviceTelemetry(device.id);
  const rows = Object.entries(liveMap).map(([key, value]) => ({ key, value }));
  const keys = Object.keys(liveMap);
  useEffect(()=>{if(keys.length>0&&!selKey)setSelKey(keys[0]);},[keys.join(",")]);
  useEffect(()=>{
    if(selKey){telemetryApi.history(device.id,selKey,50).then(setChartData).catch(()=>setChartData([]));}
  },[selKey, device.id]);
  const handleRegen=async()=>{if(!window.confirm("Regenerate token?"))return;setRegen(true);try{const u=await deviceApi.regenerateToken(device.id);setDevice(u);onToast("Token regenerated");}catch(e){onToast(e.message,"error");}finally{setRegen(false);}};
  const copy=t=>{navigator.clipboard.writeText(t).catch(()=>{});setCopied(true);setTimeout(()=>setCopied(false),1800);};
  const curl=`curl -X POST \\\n  ${BASE_URL}/api/v1/telemetry/ingest/${device.token} \\\n  -H "Content-Type: application/json" \\\n  -d '{"values": {"temperature": 25.4}}'`;
  return (
    <div className="fixed inset-0 z-50 flex justify-end"><div className="absolute inset-0 bg-black/30 backdrop-blur-[2px]" onClick={onClose}/>
      <div className="relative w-[440px] h-full bg-white border-l border-slate-200 flex flex-col shadow-2xl overflow-y-auto" style={{animation:"slideIn .2s ease"}}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 sticky top-0 bg-white z-10">
          <div className="flex items-center gap-3"><div className="w-9 h-9 rounded-xl bg-blue-50 flex items-center justify-center"><svg className="w-4 h-4 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div><div><p className="text-sm font-semibold text-[#0B1426]">{device.name}</p><span className={`${SB[device.status]||SB.INACTIVE} text-[10px] mt-0.5`}><span className={SD[device.status]||SD.INACTIVE}/>{device.status}</span></div></div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400"><svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div className="p-5 border-b border-slate-50">
          <div className="grid grid-cols-2 gap-3 mb-4">{[["Type",device.device_type],["Created",new Date(device.created_at).toLocaleDateString()],["Label",device.label||"—"],["ID",device.id.slice(0,8)+"…"]].map(([k,v])=><div key={k}><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-0.5">{k}</p><p className="text-sm text-slate-700 font-medium">{v}</p></div>)}</div>
          <div><div className="flex items-center justify-between mb-1.5"><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Token</p>{isAdmin && <button onClick={handleRegen} disabled={regen} className="flex items-center gap-1 text-[10px] font-medium text-slate-400 hover:text-slate-600 px-2 py-1 rounded hover:bg-slate-100"><svg className={`w-3 h-3 ${regen?"animate-spin":""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/></svg>Regenerate</button>}</div>
          <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2"><code className="text-[11px] text-slate-600 font-mono flex-1 truncate">{device.token}</code><button onClick={()=>copy(device.token)} className="flex-shrink-0 flex items-center gap-1 text-[10px] font-medium">{copied?<span className="text-emerald-500 flex items-center gap-1"><svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>Copied!</span>:<span className="text-blue-500 flex items-center gap-1"><svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy</span>}</button></div></div>
          <div className="mt-4"><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-1.5">Ingest Example</p><pre className="bg-slate-800 text-slate-300 text-[10px] rounded-lg p-3 overflow-x-auto leading-relaxed font-mono whitespace-pre">{curl}</pre></div>
        </div>
        <div className="p-5 border-b border-slate-50"><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-3">Latest Values</p>
          {rows.length===0?<p className="text-xs text-slate-400">No telemetry</p>:<div className="grid grid-cols-2 gap-2.5">{rows.map(r=>{const nv=typeof r.value==="number"?r.value:parseFloat(r.value);const isN=!isNaN(nv);const cm={temperature:["bg-orange-50","text-orange-600"],humidity:["bg-[#EAF2FF]","text-blue-600"],voltage:["bg-violet-50","text-violet-600"],pressure:["bg-emerald-50","text-emerald-600"]};const[bg,clr]=cm[r.key]||["bg-slate-50","text-slate-600"];return<div key={r.key} className={`rounded-xl p-3 ${bg}`}><p className={`text-[10px] font-medium opacity-70 mb-1 ${clr}`}>{r.key}</p><p className={`text-2xl font-bold font-mono ${clr}`}>{isN?nv.toFixed(2):String(r.value??"—")}</p></div>;})}</div>}
        </div>
        <div className="p-5"><div className="flex items-center justify-between mb-3"><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">History</p>{keys.length>0&&<select value={selKey} onChange={e=>setSelKey(e.target.value)} className="text-xs border border-slate-200 rounded-lg px-2.5 py-1 bg-white text-slate-600 outline-none cursor-pointer">{keys.map(k=><option key={k}>{k}</option>)}</select>}</div>
          <LineChart data={chartData} color="#3b82f6"/>{chartData.length>0&&<div className="flex items-center justify-end gap-2 mt-2"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"/><span className="text-[10px] text-slate-400">{chartData.length} samples · {selKey}</span></div>}
        </div>
      </div>
      <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
    </div>
  );
}, (prev, next) => prev.device?.id === next.device?.id && prev.user?.id === next.user?.id);

// ── Settings + misc pages ─────────────────────────────────────────────────────

function SettingsPage({ user, onLogout }) {
  const BASE_URL=(typeof import.meta!=="undefined"&&import.meta.env?.VITE_API_URL)||"http://localhost:8000";
  const WS_BASE=BASE_URL.replace(/^http/,"ws");
  const [provKey, setProvKey] = useState("");
  const [provLoading, setProvLoading] = useState(true);
  const [provCopied, setProvCopied] = useState(false);
  const [provEndpoint, setProvEndpoint] = useState("");

  useEffect(() => {
    provisioningApi.getKey()
      .then(d => { setProvKey(d.provisioning_key || ""); setProvEndpoint(d.provision_endpoint || ""); })
      .catch(() => {})
      .finally(() => setProvLoading(false));
  }, []);

  const copyKey = () => {
    navigator.clipboard.writeText(provKey).catch(() => {});
    setProvCopied(true);
    setTimeout(() => setProvCopied(false), 2000);
  };

  const esp32Code = `// ── Device Provisioning (auto-register on first boot) ──────
#define PROVISION_KEY  "${provKey}"
#define PROVISION_URL  "${BASE_URL}/api/v1/devices/provision"
#define DEVICE_NAME    "ESP32-GluciQ-001"  // unique name per device

String deviceToken = "";  // filled after provisioning

bool provision() {
  HTTPClient http;
  http.begin(PROVISION_URL);
  http.addHeader("Content-Type", "application/json");
  String body = "{\"provision_key\":\"" + String(PROVISION_KEY) + "\","
                "\"device_name\":\"" + String(DEVICE_NAME) + "\","
                "\"device_type\":\"SENSOR\"}";
  int code = http.POST(body);
  if (code == 200 || code == 201) {
    String resp = http.getString();
    // Parse token from: {"device_id":"...","token":"...","status":"..."}
    int t1 = resp.indexOf("\"token\":\"") + 9;
    int t2 = resp.indexOf("\"", t1);
    deviceToken = resp.substring(t1, t2);
    Serial.println("Provisioned! Token: " + deviceToken);
    http.end(); return true;
  }
  http.end(); return false;
}`;

  return (
    <div className="max-w-2xl space-y-4">
      {[{t:"Profile",f:[["Email",user?.email||"—"],["Role",user?.role||"TENANT_ADMIN"],["Name",user?.first_name?`${user.first_name} ${user.last_name||""}`.trim():"—"]]},{t:"API Configuration",f:[["Backend URL",BASE_URL],["Telemetry Ingest",`${BASE_URL}/api/v1/telemetry/ingest/{token}`],["WebSocket",`${WS_BASE}/api/v1/ws/telemetry/{device_id}`]]}].map(s=>(
        <div key={s.t} className="rounded-2xl border shadow-sm shadow-blue-100/40 overflow-hidden" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}><div className="px-5 py-3.5 border-b border-slate-50"><h3 className="text-sm font-semibold text-slate-700">{s.t}</h3></div><div className="p-5 grid grid-cols-2 gap-4">{s.f.map(([k,v])=><div key={k}><label className="block text-xs font-medium text-slate-400 mb-1.5">{k}</label><input readOnly value={v} className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 outline-none font-mono"/></div>)}</div></div>
      ))}

      {/* ── Device Provisioning Key ── */}
      <div className="rounded-2xl border shadow-sm shadow-blue-100/40 overflow-hidden" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
        <div className="px-5 py-3.5 border-b border-slate-50 flex items-center gap-2">
          <svg className="w-4 h-4 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
          <h3 className="text-sm font-semibold text-slate-700">Device Provisioning</h3>
        </div>
        <div className="p-5 space-y-4">
          <p className="text-xs text-slate-500 leading-relaxed">
            Use this key in your ESP32 / firmware so devices can <strong>self-register</strong> the first time
            they boot — without needing a user account or JWT token. The device receives a unique token
            it can use for all future telemetry ingestion.
          </p>

          {/* Provisioning key display */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Your Provisioning Key</label>
            <div className="flex gap-2">
              <input
                readOnly
                value={provLoading ? "Loading…" : provKey}
                className="flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-700 outline-none font-mono"
              />
              <button
                onClick={copyKey}
                disabled={provLoading || !provKey}
                className="flex items-center gap-1.5 px-4 py-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] disabled:opacity-50 text-white text-xs font-semibold rounded-lg"
              >
                {provCopied
                  ? <><svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>Copied!</>
                  : <><svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy</>
                }
              </button>
            </div>
          </div>

          {/* Endpoint */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Provision Endpoint</label>
            <input readOnly value={`${BASE_URL}/api/v1/devices/provision`}
              className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600 outline-none font-mono"/>
          </div>

          {/* How it works */}
          <div className="bg-blue-50 rounded-lg p-3 text-xs text-blue-700 space-y-1">
            <p className="font-semibold">How it works</p>
            <p>1. Device sends <code className="bg-blue-100 px-1 rounded">POST /api/v1/devices/provision</code> with your key and a unique device name</p>
            <p>2. Platform creates the device under your tenant and returns a <strong>device token</strong></p>
            <p>3. Device saves the token and uses it for all future telemetry: <code className="bg-blue-100 px-1 rounded">/api/v1/telemetry/ingest/&#123;token&#125;</code></p>
            <p>4. If the device name already exists, the same token is returned — safe to call on every boot</p>
          </div>

          {/* ESP32 code snippet */}
          {provKey && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">ESP32 Arduino Code Snippet</label>
              <pre className="bg-slate-800 text-slate-300 text-[10px] rounded-lg p-3 overflow-x-auto leading-relaxed font-mono whitespace-pre">{esp32Code}</pre>
            </div>
          )}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-red-100 shadow-sm overflow-hidden"><div className="px-5 py-3.5 border-b border-red-50"><h3 className="text-sm font-semibold text-red-600">Danger Zone</h3></div><div className="p-5 flex items-center justify-between"><div><p className="text-sm font-medium text-slate-700">Sign out</p><p className="text-xs text-[#6B7F9F] mt-0.5">Clears your session</p></div><button onClick={onLogout} className="flex items-center gap-2 bg-red-500 hover:bg-red-600 text-white text-sm font-medium px-4 py-2 rounded-lg"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>Sign Out</button></div></div>
    </div>
  );
}
function ComingSoon({ label, desc, icon }) { return <div className="flex flex-col items-center justify-center py-20 gap-3"><div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mb-1"><svg className="w-7 h-7 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={icon}/></svg></div><h2 className="text-base font-semibold text-slate-700">{label}</h2><p className="text-sm text-slate-400 text-center max-w-xs">{desc}</p><span className="text-xs font-medium text-slate-400 bg-slate-100 px-3 py-1 rounded-full mt-1">Coming Soon</span></div>; }

// ── RBAC: Users & Roles Page ──────────────────────────────────────────────────
function UsersPage({ onToast, user: currentUser }) {
  const isAdmin = currentUser?.role === "TENANT_ADMIN";
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showEdit, setShowEdit] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [editUser, setEditUser] = useState(null);
  const [saving, setSaving] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: "", password: "", first_name: "", last_name: "", role: "TENANT_USER" });
  const INP = "w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-400";

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try { setUsers(await userApi.list()); }
    catch (e) { onToast(e.message, "error"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchUsers(); }, []);

  const handleInvite = async () => {
    if (!inviteForm.email || !inviteForm.password) return;
    setSaving(true);
    try {
      await userApi.invite(inviteForm);
      await fetchUsers();
      setShowInvite(false);
      setInviteForm({ email: "", password: "", first_name: "", last_name: "", role: "TENANT_USER" });
      onToast("User invited successfully");
    } catch (e) { onToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  const handleSaveRole = async (data) => {
    setSaving(true);
    try {
      await userApi.updateRole(data.id, { role: data.role, is_active: data.is_active });
      await fetchUsers();
      setShowEdit(false); setEditUser(null);
      onToast("Role updated");
    } catch (e) { onToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  const handleDelete = async (u) => {
    if (!window.confirm(`Remove ${u.email} from this tenant?`)) return;
    try { await userApi.delete(u.id); setUsers(us => us.filter(x => x.id !== u.id)); onToast("User removed"); }
    catch (e) { onToast(e.message, "error"); }
  };

  const ROLE_BADGE = {
    TENANT_ADMIN:  "bg-purple-100 text-purple-700",
    TENANT_USER:   "bg-blue-100 text-blue-700",
    CUSTOMER_USER: "bg-amber-100 text-amber-700",
  };

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Users & Roles</h2>
          <p className="text-xs text-slate-400 mt-0.5">Invite staff and manage their access level</p>
        </div>
        {isAdmin && (
          <button onClick={() => setShowInvite(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#2F8CFF] hover:bg-blue-600 text-white text-sm font-semibold rounded-xl">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Invite User
          </button>
        )}
      </div>

      {/* Role cards */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { role: "TENANT_ADMIN", color: "purple", desc: "Full access — create devices, manage users, configure rules" },
          { role: "TENANT_USER",  color: "blue",   desc: "Read-only — view devices and telemetry, cannot create or delete" },
          { role: "CUSTOMER_USER",color: "amber",  desc: "Scoped to one customer — only sees their assigned devices" },
        ].map(({ role, color, desc }) => (
          <div key={role} className={`rounded-xl border p-3.5 bg-${color}-50 border-${color}-200`}>
            <span className={`inline-flex px-2 py-0.5 rounded text-xs font-semibold bg-${color}-100 text-${color}-700`}>{role}</span>
            <p className={`text-xs text-${color}-700 mt-2 leading-relaxed`}>{desc}</p>
          </div>
        ))}
      </div>

      {/* Info box — no self registration */}
      <div className="flex items-start gap-2.5 bg-blue-50 border border-blue-100 rounded-xl p-3.5">
        <svg className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <p className="text-xs text-blue-700">Staff users must be <strong>invited by an admin</strong> — they cannot self-register. Use <strong>Invite User</strong> above to add TENANT_ADMIN or TENANT_USER accounts. Customer-scoped users are created from the <strong>Customers</strong> page.</p>
      </div>

      {/* User table */}
      <div className="bg-white rounded-2xl border shadow-sm overflow-hidden" style={{borderColor:"#D8E3F3"}}>
        <div className="px-5 py-3.5 border-b border-slate-50 flex items-center justify-between">
          <p className="text-sm font-semibold text-slate-700">Tenant Users</p>
          <span className="text-xs text-slate-400">{users.length} user{users.length !== 1 ? "s" : ""}</span>
        </div>
        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b border-slate-100 bg-slate-50">
              {["User", "Role", "Status", "Customer Scope", ""].map(h => (
                <th key={h} className="text-left px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-400">{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className="border-b border-slate-50 hover:bg-slate-50/50">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2.5">
                      <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-xs font-semibold text-blue-600 flex-shrink-0">
                        {(u.first_name?.[0] || u.email?.[0] || "U").toUpperCase()}
                      </div>
                      <div>
                        <p className="font-medium text-slate-700 text-xs">{u.first_name ? `${u.first_name} ${u.last_name || ""}`.trim() : "—"}</p>
                        <p className="text-[11px] text-slate-400">{u.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-semibold ${ROLE_BADGE[u.role] || "bg-slate-100 text-slate-600"}`}>
                      {u.role}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${u.is_active ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${u.is_active ? "bg-emerald-500" : "bg-slate-400"}`}/>
                      {u.is_active ? "Active" : "Disabled"}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-[11px] text-slate-400 font-mono">
                    {u.customer_id ? u.customer_id.slice(0, 8) + "…" : "—"}
                  </td>
                  <td className="px-5 py-3.5">
                    {isAdmin && String(u.id) !== String(currentUser?.id) && (
                      <div className="flex items-center gap-1 justify-end">
                        <button onClick={() => { setEditUser({...u}); setShowEdit(true); }}
                          className="p-1.5 rounded-lg hover:bg-blue-50 text-slate-400 hover:text-blue-500">
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>
                        <button onClick={() => handleDelete(u)}
                          className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500">
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!users.length && (
                <tr><td colSpan={5} className="px-5 py-10 text-center text-sm text-slate-400">No users yet — invite your first team member above</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Invite User Modal */}
      {showInvite && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <div>
                <h3 className="text-sm font-semibold text-slate-800">Invite Team Member</h3>
                <p className="text-xs text-slate-400 mt-0.5">They'll join your tenant with the selected role</p>
              </div>
              <button onClick={() => setShowInvite(false)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="p-5 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">First Name</label>
                  <input className={INP} placeholder="Ali" value={inviteForm.first_name}
                    onChange={e => setInviteForm(f => ({ ...f, first_name: e.target.value }))} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">Last Name</label>
                  <input className={INP} placeholder="Hassan" value={inviteForm.last_name}
                    onChange={e => setInviteForm(f => ({ ...f, last_name: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Email *</label>
                <input type="email" className={INP} placeholder="ali@company.com" value={inviteForm.email}
                  onChange={e => setInviteForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Password *</label>
                <input type="password" className={INP} placeholder="Min 8 characters" value={inviteForm.password}
                  onChange={e => setInviteForm(f => ({ ...f, password: e.target.value }))} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Role</label>
                <select className={INP + " cursor-pointer"} value={inviteForm.role}
                  onChange={e => setInviteForm(f => ({ ...f, role: e.target.value }))}>
                  <option value="TENANT_USER">TENANT_USER — Read only (recommended)</option>
                  <option value="TENANT_ADMIN">TENANT_ADMIN — Full access</option>
                </select>
              </div>
              <div className="bg-amber-50 rounded-lg p-2.5 text-xs text-amber-700">
                Note: CUSTOMER_USER accounts are created from the <strong>Customers</strong> page, not here.
              </div>
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button onClick={handleInvite} disabled={saving || !inviteForm.email || !inviteForm.password}
                className="flex-1 py-2 bg-[#2F8CFF] hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl">
                {saving ? "Creating…" : "Create User"}
              </button>
              <button onClick={() => setShowInvite(false)}
                className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-xl hover:bg-slate-50">Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Role Modal */}
      {showEdit && editUser && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <h3 className="text-sm font-semibold text-slate-800">Edit Role</h3>
              <button onClick={() => { setShowEdit(false); setEditUser(null); }} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <p className="text-xs font-medium text-slate-500 mb-1">User</p>
                <p className="text-sm font-semibold text-slate-800">{editUser.email}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Role</label>
                <select className={INP} value={editUser.role}
                  onChange={e => setEditUser(u => ({ ...u, role: e.target.value }))}>
                  <option value="TENANT_ADMIN">TENANT_ADMIN — Full access</option>
                  <option value="TENANT_USER">TENANT_USER — Read only</option>
                  <option value="CUSTOMER_USER">CUSTOMER_USER — Customer scoped</option>
                </select>
              </div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-slate-500">Account Active</label>
                <button onClick={() => setEditUser(u => ({ ...u, is_active: !u.is_active }))}
                  className={`w-10 h-5 rounded-full transition-colors ${editUser.is_active ? "bg-emerald-500" : "bg-slate-300"} relative`}>
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all ${editUser.is_active ? "left-5" : "left-0.5"}`}/>
                </button>
              </div>
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button onClick={() => handleSaveRole(editUser)} disabled={saving}
                className="flex-1 py-2 bg-[#2F8CFF] hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl">
                {saving ? "Saving…" : "Save Changes"}
              </button>
              <button onClick={() => { setShowEdit(false); setEditUser(null); }}
                className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-xl hover:bg-slate-50">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ── RBAC: Customers Page ──────────────────────────────────────────────────────
function CustomersPage({ onToast, user: currentUser }) {
  const isAdmin = currentUser?.role === "TENANT_ADMIN";
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [cusUsers, setCusUsers] = useState([]);
  const [cusUsersLoading, setCusUsersLoading] = useState(false);
  const [showNewCus, setShowNewCus] = useState(false);
  const [showNewUser, setShowNewUser] = useState(false);
  const [saving, setSaving] = useState(false);
  const [cusForm, setCusForm] = useState({ name: "", email: "", city: "", country: "" });
  const [userForm, setUserForm] = useState({ email: "", password: "", first_name: "", last_name: "" });
  const INP = "w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-400";

  const fetchCustomers = useCallback(async () => {
    setLoading(true);
    try { setCustomers(await customerApi.list()); }
    catch (e) { onToast(e.message, "error"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchCustomers(); }, []);

  const loadCusUsers = async (cus) => {
    setSelected(cus); setCusUsersLoading(true);
    try { setCusUsers(await customerApi.listUsers(cus.id)); }
    catch { setCusUsers([]); }
    finally { setCusUsersLoading(false); }
  };

  const handleCreateCustomer = async () => {
    if (!cusForm.name.trim()) return;
    setSaving(true);
    try {
      await customerApi.create({ ...cusForm, tenant_id: "00000000-0000-0000-0000-000000000000" });
      await fetchCustomers();
      setShowNewCus(false); setCusForm({ name: "", email: "", city: "", country: "" });
      onToast("Customer created");
    } catch (e) { onToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  const handleDeleteCustomer = async (id) => {
    if (!window.confirm("Delete this customer and all their users?")) return;
    try {
      await customerApi.delete(id);
      setCustomers(cs => cs.filter(c => c.id !== id));
      if (selected?.id === id) setSelected(null);
      onToast("Customer deleted");
    } catch (e) { onToast(e.message, "error"); }
  };

  const handleCreateUser = async () => {
    if (!userForm.email || !userForm.password) return;
    setSaving(true);
    try {
      await customerApi.createUser(selected.id, userForm);
      setCusUsers(await customerApi.listUsers(selected.id));
      setShowNewUser(false); setUserForm({ email: "", password: "", first_name: "", last_name: "" });
      onToast("Customer user created");
    } catch (e) { onToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Customers</h2>
          <p className="text-xs text-slate-400 mt-0.5">Manage customer accounts and their scoped users</p>
        </div>
        {isAdmin && (
          <button onClick={() => setShowNewCus(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#2F8CFF] hover:bg-blue-600 text-white text-sm font-semibold rounded-xl">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New Customer
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Customer list */}
        <div className="bg-white rounded-2xl border shadow-sm overflow-hidden" style={{borderColor:"#D8E3F3"}}>
          <div className="px-5 py-3.5 border-b border-slate-50">
            <p className="text-sm font-semibold text-slate-700">All Customers</p>
          </div>
          {loading ? (
            <div className="flex justify-center py-10"><Spinner /></div>
          ) : customers.length === 0 ? (
            <Empty icon="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8" title="No customers yet" sub="Create your first customer to get started" />
          ) : (
            <div className="divide-y divide-slate-50">
              {customers.map(c => (
                <div key={c.id} onClick={() => loadCusUsers(c)}
                  className={`flex items-center justify-between px-5 py-3.5 cursor-pointer hover:bg-slate-50 transition-colors ${selected?.id === c.id ? "bg-blue-50" : ""}`}>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-600">
                      {c.name[0].toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-700">{c.name}</p>
                      <p className="text-[11px] text-slate-400">{c.email || c.city || "No details"}</p>
                    </div>
                  </div>
                  {isAdmin && (
                    <button onClick={e => { e.stopPropagation(); handleDeleteCustomer(c.id); }}
                      className="p-1.5 rounded-lg hover:bg-red-50 text-slate-300 hover:text-red-500">
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Selected customer users */}
        <div className="bg-white rounded-2xl border shadow-sm overflow-hidden" style={{borderColor:"#D8E3F3"}}>
          <div className="px-5 py-3.5 border-b border-slate-50 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-700">
              {selected ? `${selected.name} — Users` : "Select a customer"}
            </p>
            {selected && isAdmin && (
              <button onClick={() => setShowNewUser(true)}
                className="flex items-center gap-1 px-3 py-1.5 bg-[#2F8CFF] hover:bg-blue-600 text-white text-xs font-semibold rounded-lg">
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                Add User
              </button>
            )}
          </div>
          {!selected ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400 gap-2">
              <svg className="w-8 h-8 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
              <p className="text-xs">Click a customer to see their users</p>
            </div>
          ) : cusUsersLoading ? (
            <div className="flex justify-center py-10"><Spinner /></div>
          ) : cusUsers.length === 0 ? (
            <Empty icon="M16 11c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 3-1.34 3-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3" title="No users" sub="Add a CUSTOMER_USER to give scoped access" />
          ) : (
            <div className="divide-y divide-slate-50">
              {cusUsers.map(u => (
                <div key={u.id} className="flex items-center justify-between px-5 py-3">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center text-xs font-bold text-amber-600">
                      {(u.first_name?.[0] || u.email[0]).toUpperCase()}
                    </div>
                    <div>
                      <p className="text-xs font-medium text-slate-700">{u.first_name ? `${u.first_name} ${u.last_name || ""}`.trim() : u.email}</p>
                      <p className="text-[11px] text-slate-400">{u.email}</p>
                    </div>
                  </div>
                  <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-amber-100 text-amber-700">CUSTOMER_USER</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* New Customer Modal */}
      {showNewCus && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <h3 className="text-sm font-semibold">New Customer</h3>
              <button onClick={() => setShowNewCus(false)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="p-5 space-y-3">
              {[["Name *", "name", "Acme Corp"], ["Email", "email", "contact@acme.com"], ["City", "city", "Kuala Lumpur"], ["Country", "country", "Malaysia"]].map(([label, key, ph]) => (
                <div key={key}>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">{label}</label>
                  <input className={INP} placeholder={ph} value={cusForm[key]}
                    onChange={e => setCusForm(f => ({ ...f, [key]: e.target.value }))} />
                </div>
              ))}
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button onClick={handleCreateCustomer} disabled={saving || !cusForm.name.trim()}
                className="flex-1 py-2 bg-[#2F8CFF] hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl">
                {saving ? "Creating…" : "Create Customer"}
              </button>
              <button onClick={() => setShowNewCus(false)} className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-xl">Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* New Customer User Modal */}
      {showNewUser && selected && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <div>
                <h3 className="text-sm font-semibold">Add User to {selected.name}</h3>
                <p className="text-xs text-slate-400 mt-0.5">Creates a CUSTOMER_USER scoped to this customer</p>
              </div>
              <button onClick={() => setShowNewUser(false)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="p-5 space-y-3">
              {[["Email *", "email", "user@example.com", "email"], ["Password *", "password", "Min 8 characters", "password"],
                ["First Name", "first_name", "Optional", "text"], ["Last Name", "last_name", "Optional", "text"]].map(([label, key, ph, type]) => (
                <div key={key}>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">{label}</label>
                  <input type={type} className={INP} placeholder={ph} value={userForm[key]}
                    onChange={e => setUserForm(f => ({ ...f, [key]: e.target.value }))} />
                </div>
              ))}
              <div className="bg-amber-50 rounded-lg p-2.5 text-xs text-amber-700">
                This user will only see devices assigned to <strong>{selected.name}</strong>
              </div>
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button onClick={handleCreateUser} disabled={saving || !userForm.email || !userForm.password}
                className="flex-1 py-2 bg-[#2F8CFF] hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl">
                {saving ? "Creating…" : "Create User"}
              </button>
              <button onClick={() => setShowNewUser(false)} className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-xl">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Rule Chains Page (Threshold Rules) ───────────────────────────────────────
function RuleChainsPage({ onToast, user }) {
  const isAdmin = user?.role === "TENANT_ADMIN";
  const [rules,    setRules]    = useState([]);
  const [devices,  setDevices]  = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving,   setSaving]   = useState(false);
  const [devKeys,  setDevKeys]  = useState([]);

  const EMPTY_FORM = {
    device_id: "", key: "", condition: "gt",
    threshold: "", severity: "WARNING", alarm_type: "", is_active: true,
  };
  const [form, setForm] = useState(EMPTY_FORM);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const INP = "w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-400";

  const SEV_COLORS = {
    CRITICAL: "bg-red-100 text-red-700",
    MAJOR:    "bg-orange-100 text-orange-700",
    MINOR:    "bg-yellow-100 text-yellow-700",
    WARNING:  "bg-amber-100 text-amber-700",
    INDETERMINATE: "bg-slate-100 text-slate-600",
  };

  const COND_LABELS = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=" };

  useEffect(() => {
    Promise.all([thresholdApi.list(), deviceApi.list()])
      .then(([r, d]) => { setRules(r || []); setDevices(d || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Dynamically load telemetry keys when device selection changes
  useEffect(() => {
    if (!form.device_id) { setDevKeys([]); return; }
    telemetryApi.keys(form.device_id)
      .then(res => setDevKeys(res?.keys || []))
      .catch(() => setDevKeys([]));
  }, [form.device_id]);

  const handleCreate = async () => {
    if (!form.key || !form.threshold || !form.alarm_type) {
      onToast("Key, threshold, and alarm type are required", "error"); return;
    }
    setSaving(true);
    try {
      const body = {
        device_id:  form.device_id || null,
        key:        form.key,
        condition:  form.condition,
        threshold:  parseFloat(form.threshold),
        severity:   form.severity,
        alarm_type: form.alarm_type,
        is_active:  form.is_active,
      };
      const created = await thresholdApi.create(body);
      setRules(r => [...r, created]);
      setShowForm(false); setForm(EMPTY_FORM);
      onToast("Rule created");
    } catch (e) { onToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  const handleToggle = async (rule) => {
    try {
      const updated = await thresholdApi.update(rule.id, { ...rule, is_active: !rule.is_active });
      setRules(rs => rs.map(r => r.id === rule.id ? updated : r));
      onToast(updated.is_active ? "Rule enabled" : "Rule disabled");
    } catch (e) { onToast(e.message, "error"); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this rule?")) return;
    try {
      await thresholdApi.delete(id);
      setRules(rs => rs.filter(r => r.id !== id));
      onToast("Rule deleted");
    } catch (e) { onToast(e.message, "error"); }
  };

  const deviceName = (id) => devices.find(d => d.id === id)?.name || "All devices";

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Threshold Rules</h2>
          <p className="text-xs text-slate-400 mt-0.5">Auto-trigger alarms for any telemetry key on any device</p>
        </div>
        {isAdmin && (
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#2F8CFF] hover:bg-blue-600 text-white text-sm font-semibold rounded-xl">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New Rule
          </button>
        )}
      </div>

      {/* How it works */}
      <div className="flex items-start gap-2.5 bg-blue-50 border border-blue-100 rounded-xl p-3.5">
        <svg className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <p className="text-xs text-blue-700 leading-relaxed">
          Rules are evaluated on every telemetry ingest for <strong>any key</strong> — temperature, glucose, voltage, vibration, or any custom key.
          When a condition is met, an alarm is raised. When the value recovers, the alarm is <strong>auto-cleared</strong>.
        </p>
      </div>

      {/* Rules table */}
      <div className="bg-white rounded-2xl border shadow-sm overflow-hidden" style={{borderColor:"#D8E3F3"}}>
        <div className="px-5 py-3.5 border-b border-slate-50 flex items-center justify-between">
          <p className="text-sm font-semibold text-slate-700">Active Rules</p>
          <span className="text-xs text-slate-400">{rules.length} rule{rules.length !== 1 ? "s" : ""}</span>
        </div>
        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : rules.length === 0 ? (
          <Empty icon="M6 3v12m12-9a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6m12-9a9 9 0 0 1-9 9" title="No rules yet" sub="Create your first threshold rule above" />
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b border-slate-100 bg-slate-50">
              {["Device", "Key", "Condition", "Alarm Type", "Severity", "Status", ""].map(h => (
                <th key={h} className="text-left px-4 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-400">{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rules.map(r => (
                <tr key={r.id} className={`border-b border-slate-50 hover:bg-slate-50/50 ${!r.is_active ? "opacity-50" : ""}`}>
                  <td className="px-4 py-3 text-xs text-slate-600">{deviceName(r.device_id)}</td>
                  <td className="px-4 py-3"><span className="font-mono text-[11px] bg-slate-100 text-slate-700 px-2 py-0.5 rounded">{r.key}</span></td>
                  <td className="px-4 py-3 text-xs font-medium text-slate-700">
                    <span className="font-mono">{COND_LABELS[r.condition] || r.condition}</span>
                    <span className="ml-1.5 font-semibold">{r.threshold}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-600">{r.alarm_type}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-semibold ${SEV_COLORS[r.severity] || "bg-slate-100 text-slate-600"}`}>
                      {r.severity}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {isAdmin ? (
                      <button onClick={() => handleToggle(r)}
                        className={`w-9 h-5 rounded-full transition-colors ${r.is_active ? "bg-emerald-500" : "bg-slate-300"} relative`}>
                        <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all ${r.is_active ? "left-4" : "left-0.5"}`}/>
                      </button>
                    ) : (
                      <span className={`text-[11px] font-medium ${r.is_active ? "text-emerald-600" : "text-slate-400"}`}>
                        {r.is_active ? "Active" : "Disabled"}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {isAdmin && (
                      <button onClick={() => handleDelete(r.id)}
                        className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500">
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* New Rule Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <div>
                <h3 className="text-sm font-semibold text-slate-800">New Threshold Rule</h3>
                <p className="text-xs text-slate-400 mt-0.5">Works for any telemetry key</p>
              </div>
              <button onClick={() => { setShowForm(false); setForm(EMPTY_FORM); }}
                className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div className="p-5 space-y-3 max-h-[70vh] overflow-y-auto">

              {/* Device selector */}
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Device <span className="text-slate-300">(leave blank for all devices)</span></label>
                <select className={INP + " cursor-pointer"} value={form.device_id}
                  onChange={e => { set("device_id", e.target.value); set("key", ""); }}>
                  <option value="">— All devices in tenant —</option>
                  {devices.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>

              {/* Key selector — dynamic from backend */}
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">
                  Telemetry Key *
                  {form.device_id && devKeys.length === 0 && (
                    <span className="text-slate-400 font-normal ml-1">(no keys yet — type manually)</span>
                  )}
                </label>
                {form.device_id && devKeys.length > 0 ? (
                  <select className={INP + " cursor-pointer"} value={form.key}
                    onChange={e => set("key", e.target.value)}>
                    <option value="">— Select key —</option>
                    {devKeys.map(k => <option key={k} value={k}>{k}</option>)}
                  </select>
                ) : (
                  <input className={INP} placeholder="e.g. temperature, glucose, voltage" value={form.key}
                    onChange={e => set("key", e.target.value)} />
                )}
              </div>

              {/* Condition + threshold */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">Condition *</label>
                  <select className={INP + " cursor-pointer"} value={form.condition}
                    onChange={e => set("condition", e.target.value)}>
                    <option value="gt">&gt; greater than</option>
                    <option value="gte">≥ greater or equal</option>
                    <option value="lt">&lt; less than</option>
                    <option value="lte">≤ less or equal</option>
                    <option value="eq">= equal to</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">Threshold *</label>
                  <input type="number" className={INP} placeholder="e.g. 80" value={form.threshold}
                    onChange={e => set("threshold", e.target.value)} />
                </div>
              </div>

              {/* Severity */}
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Severity</label>
                <select className={INP + " cursor-pointer"} value={form.severity}
                  onChange={e => set("severity", e.target.value)}>
                  {["CRITICAL","MAJOR","MINOR","WARNING","INDETERMINATE"].map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>

              {/* Alarm type */}
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Alarm Type * <span className="text-slate-400 font-normal">(descriptive name)</span></label>
                <input className={INP} placeholder="e.g. High Temperature, Low Battery"
                  value={form.alarm_type} onChange={e => set("alarm_type", e.target.value)} />
              </div>

              {/* Preview */}
              {form.key && form.threshold && (
                <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-600 border border-slate-200">
                  <span className="font-medium">Preview: </span>
                  If <span className="font-mono bg-white px-1 rounded border">{form.key}</span>
                  {" "}{COND_LABELS[form.condition] || form.condition}{" "}
                  <span className="font-semibold">{form.threshold}</span>
                  {" → trigger "}<span className="font-medium text-amber-700">{form.alarm_type || "alarm"}</span>
                  {" ("}{form.severity}{")"}
                  {". Auto-clears when condition is no longer met."}
                </div>
              )}
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button onClick={handleCreate} disabled={saving || !form.key || !form.threshold || !form.alarm_type}
                className="flex-1 py-2 bg-[#2F8CFF] hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl">
                {saving ? "Creating…" : "Create Rule"}
              </button>
              <button onClick={() => { setShowForm(false); setForm(EMPTY_FORM); }}
                className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-xl hover:bg-slate-50">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Login page ────────────────────────────────────────────────────────────────
// ── Reset Password page ───────────────────────────────────────────────────────
// Option 1 — simple direct reset: enter email + new password, no email link needed.
function ResetPasswordPage({ onBack }) {
  const [email,   setEmail]   = useState("");
  const [pw,      setPw]      = useState("");
  const [pw2,     setPw2]     = useState("");
  const [loading, setLoading] = useState(false);
  const [done,    setDone]    = useState(false);
  const [error,   setError]   = useState("");

  const submit = async () => {
    if (!email.trim())         { setError("Please enter your email address"); return; }
    if (!pw || pw.length < 8)  { setError("Password must be at least 8 characters"); return; }
    if (pw !== pw2)             { setError("Passwords do not match"); return; }
    setLoading(true); setError("");
    try {
      await authApi.resetPassword(email.trim(), pw);
      setDone(true);
    } catch(e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-8">
      <div className="w-full max-w-md rounded-2xl border bg-white/70 p-7 shadow-sm" style={{borderColor:"#D8E3F3"}}>
        <button onClick={onBack} className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-600 mb-8">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
          Back to login
        </button>
        {done ? (
          <div className="text-center">
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
            </div>
            <h2 className="text-xl font-bold text-slate-800 mb-2">Password updated!</h2>
            <p className="text-sm text-slate-400 mb-6">Your password has been reset successfully.</p>
            <button onClick={onBack} className="w-full flex items-center justify-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] text-white font-semibold text-sm py-2.5 rounded-lg">
              Back to Login
            </button>
          </div>
        ) : (
          <>
            <h1 className="text-2xl font-bold text-slate-800 mb-1">Reset password</h1>
            <p className="text-sm text-slate-400 mb-6">Enter your registered email and choose a new password.</p>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Email address</label>
                <input type="email" value={email} onChange={e=>setEmail(e.target.value)} className={INP} placeholder="you@example.com"/>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">New Password</label>
                <input type="password" value={pw} onChange={e=>setPw(e.target.value)} className={INP} placeholder="Min 8 characters"/>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Confirm Password</label>
                <input type="password" value={pw2} onChange={e=>setPw2(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&submit()} className={INP} placeholder="Repeat password"/>
              </div>
            </div>
            {error && <p className="mt-3 text-xs text-red-500 bg-red-50 px-3 py-2 rounded-lg">{error}</p>}
            <button onClick={submit} disabled={loading}
              className="w-full mt-5 flex items-center justify-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] disabled:opacity-60 text-white font-semibold text-sm py-2.5 rounded-lg">
              {loading && <Spinner/>} Reset Password
            </button>
          </>
        )}
      </div>
    </div>
  );
}


// ── RBAC: Users & Roles Page ──────────────────────────────────────────────────

// ── Rule Chains Page (Threshold Rules) ───────────────────────────────────────
// ── Login page ────────────────────────────────────────────────────────────────

// ── API Keys Page ─────────────────────────────────────────────────────────────
function ApiKeysPage({ onToast }) {
  const [keys, setKeys]       = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating]     = useState(false);
  const [newKey, setNewKey]         = useState(null); // raw key shown once
  const [form, setForm]             = useState({ name: "", expires_days: "" });
  const [revoking, setRevoking]     = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setKeys(await apiKeysApi.list()); }
    catch(e) { onToast({ msg: e.message, type: "error" }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!form.name.trim()) return onToast({ msg: "Name is required", type: "error" });
    setCreating(true);
    try {
      const body = { name: form.name.trim(), ...(form.expires_days ? { expires_days: parseInt(form.expires_days) } : {}) };
      const res = await apiKeysApi.create(body);
      setNewKey(res);
      setForm({ name: "", expires_days: "" });
      setShowCreate(false);
      load();
    } catch(e) { onToast({ msg: e.message, type: "error" }); }
    finally { setCreating(false); }
  };

  const handleRevoke = async (id) => {
    setRevoking(id);
    try {
      await apiKeysApi.revoke(id);
      onToast({ msg: "API key revoked", type: "success" });
      load();
    } catch(e) { onToast({ msg: e.message, type: "error" }); }
    finally { setRevoking(null); }
  };

  const copyKey = (key) => {
    navigator.clipboard.writeText(key).catch(() => {});
    onToast({ msg: "Copied to clipboard", type: "success" });
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-[#0B1426]">API Keys</h2>
          <p className="text-xs text-[#6B7F9F] mt-0.5">Long-lived keys for server-to-server integrations. Keys are shown once on creation.</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors shadow-sm shadow-blue-500/25">
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Create Key
        </button>
      </div>

      {/* One-time key reveal banner */}
      {newKey && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <svg className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-amber-800">Save this key — it won&apos;t be shown again</p>
              <p className="text-xs text-amber-700 mt-0.5 mb-2">This is the only time the raw key will be displayed.</p>
              <div className="flex items-center gap-2 bg-white border border-amber-200 rounded-lg px-3 py-2">
                <code className="flex-1 text-xs font-mono text-slate-700 break-all">{newKey.raw_key}</code>
                <button onClick={() => copyKey(newKey.raw_key)} className="flex-shrink-0 text-xs font-medium text-amber-700 hover:text-amber-900 px-2 py-1 rounded hover:bg-amber-50">Copy</button>
              </div>
            </div>
            <button onClick={() => setNewKey(null)} className="text-amber-400 hover:text-amber-600 flex-shrink-0">
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <h3 className="text-sm font-bold text-[#0B1426]">Create API Key</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm(f=>({...f,name:e.target.value}))} placeholder="e.g. CI/CD pipeline" className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Expires in days <span className="text-slate-400">(leave empty = never)</span></label>
                <input type="number" value={form.expires_days} onChange={e => setForm(f=>({...f,expires_days:e.target.value}))} placeholder="e.g. 90" min="1" className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300" />
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button onClick={() => setShowCreate(false)} className="flex-1 py-2 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50">Cancel</button>
              <button onClick={handleCreate} disabled={creating} className="flex-1 py-2 rounded-lg bg-[#2F8CFF] text-white text-sm font-medium hover:bg-[#0B4BB3] disabled:opacity-50">{creating ? "Creating…" : "Create"}</button>
            </div>
          </div>
        </div>
      )}

      {/* Keys table */}
      <div className="bg-white rounded-2xl border border-[#D8E3F3] overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-32 text-sm text-slate-400">Loading…</div>
        ) : keys.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-slate-400">
            <svg className="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
            <p className="text-sm">No API keys yet</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#D8E3F3] bg-[#F4F8FF]">
                <th className="text-left px-5 py-3 text-xs font-semibold text-[#6B7F9F]">Name</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-[#6B7F9F]">Prefix</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-[#6B7F9F]">Created</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-[#6B7F9F]">Expires</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-[#6B7F9F]">Last Used</th>
                <th className="px-5 py-3"/>
              </tr>
            </thead>
            <tbody>
              {keys.map((k, i) => (
                <tr key={k.id} className={`border-b border-[#D8E3F3] last:border-0 ${i%2===1?"bg-[#F8FAFF]":""}`}>
                  <td className="px-5 py-3 font-medium text-[#0B1426]">{k.name}</td>
                  <td className="px-5 py-3"><code className="font-mono text-xs bg-slate-100 px-2 py-0.5 rounded">{k.key_prefix}…</code></td>
                  <td className="px-5 py-3 text-[#6B7F9F] text-xs">{new Date(k.created_at).toLocaleDateString()}</td>
                  <td className="px-5 py-3 text-xs">
                    {k.expires_at ? (
                      <span className={new Date(k.expires_at) < new Date() ? "text-red-500 font-medium" : "text-[#6B7F9F]"}>
                        {new Date(k.expires_at).toLocaleDateString()}
                      </span>
                    ) : <span className="text-emerald-600 font-medium">Never</span>}
                  </td>
                  <td className="px-5 py-3 text-xs text-[#6B7F9F]">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—"}</td>
                  <td className="px-5 py-3 text-right">
                    <button onClick={() => handleRevoke(k.id)} disabled={revoking === k.id} className="text-xs font-medium text-red-500 hover:text-red-700 hover:bg-red-50 px-2.5 py-1 rounded-lg disabled:opacity-40 transition-colors">
                      {revoking === k.id ? "Revoking…" : "Revoke"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
        <p className="text-xs text-blue-700"><strong>Usage:</strong> Send API keys in the <code className="font-mono bg-blue-100 px-1 rounded">Authorization: ApiKey &lt;key&gt;</code> header for server-to-server requests. Keys bypass JWT expiry but are rate-limited the same way.</p>
      </div>
    </div>
  );
}

// ── System Metrics Page ───────────────────────────────────────────────────────
function SystemMetricsPage({ onToast }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastFetched, setLastFetched] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sys, tenant] = await Promise.all([systemApi.metrics(), metricsApi.get()]);
      setData({ sys, tenant });
      setLastFetched(new Date());
    } catch(e) { onToast({ msg: e.message, type: "error" }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const Stat = ({ label, value, unit = "", color = "text-[#0B1426]", sub }) => (
    <div className="bg-white rounded-xl border border-[#D8E3F3] px-5 py-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-[#6B7F9F] mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value ?? "—"}<span className="text-sm font-normal text-[#6B7F9F] ml-1">{unit}</span></p>
      {sub && <p className="text-[10px] text-[#6B7F9F] mt-0.5">{sub}</p>}
    </div>
  );

  const Bar = ({ label, pct, color = "#2F8CFF" }) => (
    <div>
      <div className="flex justify-between text-xs mb-1"><span className="text-[#334866] font-medium">{label}</span><span className="text-[#6B7F9F]">{pct?.toFixed(1) ?? "—"}%</span></div>
      <div className="h-2 bg-slate-100 rounded-full overflow-hidden"><div style={{ width: `${Math.min(pct||0,100)}%`, background: color }} className="h-full rounded-full transition-all duration-500" /></div>
    </div>
  );

  const uptime = data?.sys?.uptime_seconds;
  const uptimeStr = uptime != null ? `${Math.floor(uptime/3600)}h ${Math.floor((uptime%3600)/60)}m` : "—";
  const s = data?.sys; const t = data?.tenant;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-[#0B1426]">System Metrics</h2>
          <p className="text-xs text-[#6B7F9F] mt-0.5">Live infrastructure health. Auto-refreshes every 30 seconds.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastFetched && <span className="text-[10px] text-[#6B7F9F]">Updated {lastFetched.toLocaleTimeString()}</span>}
          <button onClick={load} disabled={loading} className="flex items-center gap-1.5 text-xs font-medium text-[#334866] hover:text-[#0B1426] px-3 py-1.5 rounded-lg hover:bg-[#D7E8FF] transition-colors border border-[#D8E3F3]">
            <svg className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            Refresh
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center justify-center h-48 text-sm text-slate-400">Loading metrics…</div>
      ) : (
        <>
          {/* Tenant stats */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F] mb-3">Tenant Activity</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Total Devices"    value={t?.total_devices}       />
              <Stat label="Active (5 min)"   value={t?.active_devices}      color={t?.active_devices > 0 ? "text-emerald-600" : "text-[#0B1426]"} />
              <Stat label="Active Alarms"    value={t?.total_alarms_active} color={t?.total_alarms_active > 0 ? "text-red-500" : "text-[#0B1426]"} />
              <Stat label="Ingest Rate"      value={t?.ingest_rate_per_min} unit="evt/min" />
            </div>
          </div>

          {/* Infrastructure */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Process */}
            <div className="bg-white rounded-2xl border border-[#D8E3F3] p-5 space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F]">Process</p>
                <span className="text-[10px] text-[#6B7F9F]">Uptime: {uptimeStr}</span>
              </div>
              <Bar label="Process CPU" pct={s?.process?.cpu_pct} color={s?.process?.cpu_pct > 80 ? "#ef4444" : "#2F8CFF"} />
              <div className="flex justify-between text-xs">
                <span className="text-[#334866] font-medium">Process Memory</span>
                <span className="text-[#6B7F9F]">{s?.process?.mem_mb?.toFixed(0) ?? "—"} MB</span>
              </div>
            </div>

            {/* System */}
            <div className="bg-white rounded-2xl border border-[#D8E3F3] p-5 space-y-4">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F]">Host System</p>
              <Bar label="System CPU" pct={s?.system?.cpu_pct} color={s?.system?.cpu_pct > 80 ? "#ef4444" : "#10b981"} />
              <Bar label="System Memory" pct={s?.system?.mem_pct} color={s?.system?.mem_pct > 85 ? "#ef4444" : "#10b981"} />
              {s?.system?.mem_used_gb != null && <p className="text-[10px] text-[#6B7F9F]">{s.system.mem_used_gb} GB used</p>}
            </div>

            {/* Database */}
            <div className="bg-white rounded-2xl border border-[#D8E3F3] p-5 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F]">Database</p>
              <div className="grid grid-cols-2 gap-3">
                {[
                  ["Pool Size",    s?.database?.pool_size],
                  ["Checked Out",  s?.database?.checked_out],
                  ["Overflow",     s?.database?.overflow],
                  ["Latency",      s?.database?.latency_ms != null ? `${s.database.latency_ms} ms` : "—"],
                ].map(([label, val]) => (
                  <div key={label} className="bg-[#F4F8FF] rounded-lg px-3 py-2">
                    <p className="text-[10px] text-[#6B7F9F]">{label}</p>
                    <p className="text-sm font-bold text-[#0B1426] mt-0.5">{val ?? "—"}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* WebSocket + Redis */}
            <div className="bg-white rounded-2xl border border-[#D8E3F3] p-5 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F]">WebSocket &amp; Redis</p>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[#334866]">Connected Clients</span>
                  <span className="font-bold text-[#0B1426]">{s?.websocket?.total_clients ?? "—"}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#334866]">Active Devices</span>
                  <span className="font-bold text-[#0B1426]">{s?.websocket?.active_devices ?? "—"}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#334866]">WS Backend</span>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${s?.websocket?.backend === "redis" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>{s?.websocket?.backend ?? "—"}</span>
                </div>
                <div className="flex justify-between text-sm items-center">
                  <span className="text-[#334866]">Redis</span>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${s?.redis === "ok" ? "bg-emerald-100 text-emerald-700" : s?.redis === "not_configured" ? "bg-slate-100 text-slate-600" : "bg-red-100 text-red-600"}`}>
                    {s?.redis === "ok" ? "Connected" : s?.redis === "not_configured" ? "Not configured" : "Error"}
                  </span>
                </div>
              </div>
            </div>

            {/* Cache stats (Phase 11) */}
            <div className="bg-white rounded-2xl border border-[#D8E3F3] p-5 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6B7F9F]">Data Cache</p>
              {!s?.cache?.enabled ? (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="w-2 h-2 rounded-full bg-slate-300 flex-shrink-0"/>
                  Disabled — set REDIS_URL to enable
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex justify-between text-sm items-center">
                    <span className="text-[#334866]">Status</span>
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${s.cache.status === "ok" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-600"}`}>
                      {s.cache.status === "ok" ? "Active" : s.cache.status}
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-[#334866]">Keys cached</span>
                    <span className="font-bold text-[#0B1426]">{s.cache.key_count ?? "—"}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-[#334866]">Hit rate</span>
                    <span className={`font-bold ${(s.cache.hit_rate_pct ?? 0) > 60 ? "text-emerald-600" : "text-amber-500"}`}>
                      {s.cache.hit_rate_pct ?? "—"}%
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-[#334866]">Memory</span>
                    <span className="font-mono text-xs text-[#334866]">{s.cache.used_memory_human ?? "—"}</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Tenant WS clients */}
          <div className="bg-[#EAF2FF] rounded-xl px-4 py-3 text-xs text-[#334866]">
            <strong>Active WebSocket clients:</strong> {s?.websocket?.total_clients ?? "—"} &nbsp;·&nbsp; <strong>WS backend:</strong> {s?.websocket?.backend ?? "—"} &nbsp;·&nbsp; <strong>Tenant ingest:</strong> {t?.active_ws_clients ?? "—"} clients
          </div>
        </>
      )}
    </div>
  );
}

// ── Audit Log Page ────────────────────────────────────────────────────────────
const AUDIT_ACTION_COLORS = {
  "device.create":    "bg-emerald-100 text-emerald-700",
  "device.delete":    "bg-red-100 text-red-600",
  "device.update":    "bg-blue-100 text-blue-700",
  "alarm.ack":        "bg-amber-100 text-amber-700",
  "alarm.clear":      "bg-slate-100 text-slate-600",
  "api_key.create":   "bg-purple-100 text-purple-700",
  "api_key.revoke":   "bg-red-100 text-red-600",
  "user.invite":      "bg-blue-100 text-blue-700",
  "user.delete":      "bg-red-100 text-red-600",
};
const auditBadge = (action) => AUDIT_ACTION_COLORS[action] || "bg-slate-100 text-slate-600";

function AuditLogPage({ onToast }) {
  const [rows, setRows]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [limit, setLimit]       = useState(50);
  const [actionFilter, setActionFilter] = useState("");
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await systemApi.audit(limit, actionFilter || null)); }
    catch(e) { onToast({ msg: e.message, type: "error" }); }
    finally { setLoading(false); }
  }, [limit, actionFilter]);

  useEffect(() => { load(); }, [load]);

  // Distinct actions from current rows for filter dropdown
  const actions = Array.from(new Set(rows.map(r => r.action))).sort();

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-base font-bold text-[#0B1426]">Audit Log</h2>
          <p className="text-xs text-[#6B7F9F] mt-0.5">All admin actions for your tenant, newest first.</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={actionFilter} onChange={e => setActionFilter(e.target.value)} className="text-xs border border-[#D8E3F3] rounded-lg px-3 py-2 bg-white text-[#334866] focus:outline-none focus:ring-2 focus:ring-blue-300">
            <option value="">All actions</option>
            {actions.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
          <select value={limit} onChange={e => setLimit(Number(e.target.value))} className="text-xs border border-[#D8E3F3] rounded-lg px-3 py-2 bg-white text-[#334866] focus:outline-none focus:ring-2 focus:ring-blue-300">
            {[25,50,100,200].map(n => <option key={n} value={n}>{n} rows</option>)}
          </select>
          <button onClick={load} disabled={loading} className="flex items-center gap-1.5 text-xs font-medium text-[#334866] hover:text-[#0B1426] px-3 py-2 rounded-lg hover:bg-[#D7E8FF] transition-colors border border-[#D8E3F3]">
            <svg className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-[#D8E3F3] overflow-hidden">
        {loading && rows.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-sm text-slate-400">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-slate-400">
            <svg className="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/></svg>
            <p className="text-sm">No audit entries</p>
          </div>
        ) : (
          <div className="divide-y divide-[#D8E3F3]">
            {rows.map(r => (
              <div key={r.id}>
                <button onClick={() => setExpanded(expanded === r.id ? null : r.id)} className="w-full flex items-center gap-3 px-5 py-3 hover:bg-[#F4F8FF] transition-colors text-left">
                  <div className="flex-1 flex items-center gap-3 min-w-0">
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 ${auditBadge(r.action)}`}>{r.action}</span>
                    <span className="text-sm text-[#0B1426] font-medium truncate">{r.resource}{r.resource_id ? ` · ${r.resource_id.slice(0,8)}…` : ""}</span>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <span className="text-xs text-[#6B7F9F] hidden md:block">{r.user_email || "system"}</span>
                    <span className="text-xs text-[#6B7F9F]">{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</span>
                    <svg className={`w-3.5 h-3.5 text-[#6B7F9F] flex-shrink-0 transition-transform ${expanded === r.id ? "rotate-180" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9"/></svg>
                  </div>
                </button>
                {expanded === r.id && (
                  <div className="px-5 pb-3 bg-[#F8FAFF] border-t border-[#D8E3F3]">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-3 text-xs">
                      <div><p className="text-[#6B7F9F] font-medium">Action</p><p className="text-[#0B1426] mt-0.5">{r.action}</p></div>
                      <div><p className="text-[#6B7F9F] font-medium">Resource</p><p className="text-[#0B1426] mt-0.5">{r.resource}</p></div>
                      <div><p className="text-[#6B7F9F] font-medium">Resource ID</p><p className="text-[#0B1426] mt-0.5 font-mono">{r.resource_id || "—"}</p></div>
                      <div><p className="text-[#6B7F9F] font-medium">User</p><p className="text-[#0B1426] mt-0.5">{r.user_email || "system"}</p></div>
                    </div>
                    {r.detail && Object.keys(r.detail).length > 0 && (
                      <div className="mt-3">
                        <p className="text-[#6B7F9F] text-xs font-medium mb-1">Detail</p>
                        <pre className="text-[11px] bg-white border border-[#D8E3F3] rounded-lg px-3 py-2 overflow-x-auto text-[#334866]">{JSON.stringify(r.detail, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LoginPage({ onLogin }) {
  const [tab,      setTab]      = useState("signin");
  const [email,    setEmail]    = useState("demo@triaxisai.com");
  const [pw,       setPw]       = useState("demo1234");
  const [fname,    setFname]    = useState("");
  const [lname,    setLname]    = useState("");
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState("");

  const submit = async () => {
    setLoading(true); setError("");
    try {
      let d;
      if (tab === "signin") {
        d = await authApi.login(email, pw);
      } else {
        await authApi.register({ email, password: pw, first_name: fname, last_name: lname });
        d = await authApi.login(email, pw);
      }
      localStorage.setItem("access_token", d.access_token);
      if (d.refresh_token) localStorage.setItem("refresh_token", d.refresh_token);
      localStorage.setItem("user", JSON.stringify(d.user));
      onLogin(d.user);
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const features = [
    { t: "Live Devices",        s: "Fleet monitoring",   icon: (<><circle cx="12" cy="12" r="3"/><path d="M5 12a7 7 0 0 1 14 0"/><path d="M2 12a10 10 0 0 1 20 0"/></>) },
    { t: "Smart Alerts",        s: "Faster response",    icon: (<><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></>) },
    { t: "Real-time Telemetry", s: "Live data stream",   icon: (<><path d="M3 12h3l3-8 4 16 3-8h5"/></>) },
    { t: "RPC Control",         s: "Device commands",    icon: (<><path d="M12 2v4"/><path d="M12 18v4"/><path d="M4.93 4.93l2.83 2.83"/><path d="M16.24 16.24l2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><circle cx="12" cy="12" r="3"/></>) },
  ];
  const stats = [
    { v: "128",    l: "Devices Online", dot: "#10B981" },
    { v: "3",      l: "Active Alerts",  dot: "#F59E0B" },
    { v: "42",     l: "RPC Executed",   dot: "#2F8CFF" },
    { v: "99.98%", l: "Uptime",         dot: "#10B981" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "linear-gradient(180deg, #F7FAFF 0%, #EEF3FB 100%)" }}>
      <style>{`
        @keyframes tn-pulse { 0%,100% { opacity:0.6; transform:scale(1); } 50% { opacity:1; transform:scale(1.4); } }
        @keyframes tn-orbit-1 { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
        @keyframes tn-orbit-2 { from { transform:rotate(360deg); } to { transform:rotate(0deg); } }
        @keyframes tn-wave { 0% { stroke-dashoffset:0; } 100% { stroke-dashoffset:-120; } }
        @keyframes tn-float { 0%,100% { transform:translateY(0); } 50% { transform:translateY(-6px); } }
        .tn-feat:hover { transform:translateY(-2px); box-shadow:0 12px 30px -10px rgba(15,42,82,0.18); border-color:rgba(47,140,255,0.35)!important; }
        .tn-feat { transition:transform .25s ease, box-shadow .25s ease, border-color .25s ease; }
        .tn-btn-primary { transition:transform .15s ease, box-shadow .15s ease, background .15s ease; }
        .tn-btn-primary:hover { background:#1F7AEC!important; box-shadow:0 10px 24px -6px rgba(47,140,255,0.55)!important; transform:translateY(-1px); }
        .tn-input { transition:border-color .15s ease, box-shadow .15s ease; }
        .tn-input:focus { border-color:#2F8CFF!important; box-shadow:0 0 0 4px rgba(47,140,255,0.12)!important; outline:none; }
        .tn-tab { transition:background .2s ease, color .2s ease, box-shadow .2s ease; }
        .tn-demo { transition:border-color .2s ease, background .2s ease; }
        .tn-demo:hover { border-color:#2F8CFF!important; background:#F4F8FF!important; }
      `}</style>

      {/* Top header */}
      <header style={{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"16px 40px", background:"rgba(255,255,255,0.85)", backdropFilter:"blur(10px)", borderBottom:"1px solid #EAF0F8", position:"relative", zIndex:10 }}>
        <div style={{ display:"flex", alignItems:"center", gap:14 }}>
          <img src="/taat-logo-2.png" alt="TAAT" style={{ height:30 }}/>
          <div style={{ width:1, height:22, background:"#E2E8F0" }}/>
          <div style={{ display:"flex", flexDirection:"column", lineHeight:1.15 }}>
            <span style={{ fontSize:14, fontWeight:700, color:"#0B1426", letterSpacing:"-0.005em" }}>TriAxis AI Technologies</span>
            <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:10, fontWeight:500, color:"#6B7F9F", letterSpacing:"0.18em", textTransform:"uppercase" }}>Industrial AI · IoT</span>
          </div>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:14 }}>
          <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:10, fontWeight:500, color:"#94A3B8", letterSpacing:"0.18em", textTransform:"uppercase" }}>In collaboration with</span>
          <div style={{ display:"flex", alignItems:"center", gap:10, padding:"6px 12px", background:"#FFFFFF", border:"1px solid #EAF0F8", borderRadius:8 }}>
            <img src="/greenson-logo.jpg" alt="Greenson" style={{ height:22, borderRadius:3 }}/>
            <span style={{ fontSize:13, fontWeight:700, color:"#0B1426" }}>Greenson Technology</span>
            <span style={{ width:6, height:6, borderRadius:9999, background:"#10B981", boxShadow:"0 0 0 3px rgba(16,185,129,0.18)" }}/>
          </div>
        </div>
      </header>

      {/* Main body */}
      <div style={{ flex:1, display:"grid", gridTemplateColumns:"1.15fr 1fr", gap:0, alignItems:"stretch", position:"relative", overflow:"hidden" }}>
        {/* Background */}
        <div aria-hidden="true" style={{ position:"absolute", inset:0, pointerEvents:"none", background:"radial-gradient(900px 600px at 10% 10%, rgba(47,140,255,0.12), transparent 60%), radial-gradient(700px 500px at 95% 90%, rgba(47,140,255,0.08), transparent 60%)" }}/>
        <svg aria-hidden="true" width="100%" height="100%" style={{ position:"absolute", inset:0, opacity:0.5, pointerEvents:"none" }}>
          <defs>
            <pattern id="tn-grid" x="0" y="0" width="56" height="56" patternUnits="userSpaceOnUse"><path d="M56 0H0V56" fill="none" stroke="#D8E3F3" strokeWidth="0.6"/></pattern>
            <pattern id="tn-dots" x="0" y="0" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.8" fill="#C7D5E8"/></pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#tn-grid)"/>
          <rect width="100%" height="100%" fill="url(#tn-dots)" opacity="0.7"/>
        </svg>
        <svg aria-hidden="true" viewBox="0 0 1200 80" preserveAspectRatio="none" style={{ position:"absolute", bottom:0, left:0, width:"100%", height:80, opacity:0.35, pointerEvents:"none" }}>
          <path d="M0 40 Q150 10 300 40 T600 40 T900 40 T1200 40" fill="none" stroke="#2F8CFF" strokeWidth="1.2" strokeDasharray="4 8" style={{ animation:"tn-wave 6s linear infinite" }}/>
          <path d="M0 50 Q150 70 300 50 T600 50 T900 50 T1200 50" fill="none" stroke="#2F8CFF" strokeWidth="0.8" strokeDasharray="2 10" opacity="0.6" style={{ animation:"tn-wave 9s linear infinite" }}/>
        </svg>

        {/* LEFT: brand + features */}
        <div style={{ padding:"64px 64px 64px 72px", display:"flex", flexDirection:"column", justifyContent:"center", position:"relative", zIndex:2 }}>
          <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:28 }}>
            <span style={{ width:32, height:1.5, background:"linear-gradient(90deg, #2F8CFF, transparent)" }}/>
            <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:10, fontWeight:600, color:"#2F8CFF", letterSpacing:"0.32em", textTransform:"uppercase" }}>Industrial AI · IoT Platform</span>
          </div>

          <div style={{ display:"flex", alignItems:"center", gap:28, marginBottom:28 }}>
            <div style={{ position:"relative", width:132, height:132, flexShrink:0, animation:"tn-float 6s ease-in-out infinite" }}>
              <svg viewBox="0 0 132 132" style={{ position:"absolute", inset:0, animation:"tn-orbit-1 22s linear infinite" }}>
                <circle cx="66" cy="66" r="62" fill="none" stroke="#CFE0FB" strokeWidth="0.8" strokeDasharray="2 6"/>
                <circle cx="66" cy="4" r="2.5" fill="#2F8CFF"/>
              </svg>
              <svg viewBox="0 0 132 132" style={{ position:"absolute", inset:0, animation:"tn-orbit-2 14s linear infinite" }}>
                <circle cx="66" cy="66" r="50" fill="none" stroke="#A7C5F4" strokeWidth="0.6" strokeDasharray="1 4"/>
                <circle cx="116" cy="66" r="2" fill="#10B981"/>
              </svg>
              <div style={{ position:"absolute", inset:14, borderRadius:"50%", background:"radial-gradient(circle at 30% 25%, #FFFFFF 0%, #E8F1FF 55%, #D2E3FB 100%)", boxShadow:"inset 0 2px 8px rgba(255,255,255,0.9), 0 16px 36px -10px rgba(47,140,255,0.45), 0 0 0 1px rgba(47,140,255,0.12)", display:"flex", alignItems:"center", justifyContent:"center", overflow:"hidden" }}>
                <img src="/taat-robot.png" alt="TAAT" style={{ width:"82%", height:"82%", objectFit:"contain" }}/>
              </div>
              <div style={{ position:"absolute", bottom:-6, left:"50%", transform:"translateX(-50%)", padding:"3px 10px", background:"#0B1426", color:"#FFFFFF", fontFamily:"'JetBrains Mono', monospace", fontSize:9, fontWeight:600, letterSpacing:"0.22em", borderRadius:9999, boxShadow:"0 6px 14px -4px rgba(11,20,38,0.5)" }}>TAAT</div>
            </div>
            <div>
              <h1 style={{ fontSize:52, fontWeight:700, color:"#0B1426", letterSpacing:"-0.03em", lineHeight:1.02, margin:0 }}>
                <span style={{ color:"#2F8CFF" }}>TriAxis</span> Nexus<br/>Platform
              </h1>
              <p style={{ fontSize:14, fontWeight:500, color:"#475569", margin:"10px 0 0", letterSpacing:"0.01em" }}>
                AI-powered industrial IoT intelligence — predictive, autonomous, enterprise-grade.
              </p>
            </div>
          </div>

          {/* Live stats strip */}
          <div style={{ display:"grid", gridTemplateColumns:"repeat(4, 1fr)", gap:0, maxWidth:580, marginBottom:28, background:"rgba(255,255,255,0.75)", backdropFilter:"blur(8px)", border:"1px solid #EAF0F8", borderRadius:12, padding:"14px 4px", boxShadow:"0 1px 2px rgba(15,42,82,0.04)" }}>
            {stats.map((s, i) => (
              <div key={s.l} style={{ padding:"0 16px", borderRight: i < stats.length-1 ? "1px solid #EAF0F8" : "none", display:"flex", flexDirection:"column", gap:4 }}>
                <div style={{ display:"flex", alignItems:"center", gap:6 }}>
                  <span style={{ width:6, height:6, borderRadius:9999, background:s.dot, animation:"tn-pulse 2s ease-in-out infinite" }}/>
                  <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:9, fontWeight:600, color:"#94A3B8", letterSpacing:"0.18em", textTransform:"uppercase" }}>{s.l}</span>
                </div>
                <span style={{ fontSize:22, fontWeight:700, color:"#0B1426", letterSpacing:"-0.02em" }}>{s.v}</span>
              </div>
            ))}
          </div>

          {/* Feature cards */}
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:12, maxWidth:580 }}>
            {features.map((f) => (
              <div key={f.t} className="tn-feat" style={{ background:"rgba(255,255,255,0.9)", backdropFilter:"blur(8px)", border:"1px solid #EAF0F8", borderRadius:12, padding:"16px 18px", boxShadow:"0 1px 2px rgba(15,42,82,0.04)", display:"flex", alignItems:"center", gap:14 }}>
                <div style={{ width:38, height:38, borderRadius:10, background:"linear-gradient(135deg, #EAF2FF, #D8E7FC)", border:"1px solid #D2E1F8", display:"flex", alignItems:"center", justifyContent:"center", flexShrink:0 }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2F8CFF" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{f.icon}</svg>
                </div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:2 }}>
                    <span style={{ fontSize:13.5, fontWeight:700, color:"#0B1426" }}>{f.t}</span>
                    <span style={{ width:5, height:5, borderRadius:9999, background:"#10B981", animation:"tn-pulse 2s ease-in-out infinite" }}/>
                  </div>
                  <div style={{ fontSize:11.5, color:"#6B7F9F" }}>{f.s}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT: sign in card */}
        <div style={{ display:"flex", alignItems:"center", justifyContent:"center", padding:"48px 72px 48px 24px", position:"relative", zIndex:2 }}>
          <div aria-hidden="true" style={{ position:"absolute", width:380, height:380, borderRadius:"50%", background:"radial-gradient(circle, rgba(47,140,255,0.18), transparent 65%)", filter:"blur(10px)" }}/>
          <div style={{ position:"relative", width:"100%", maxWidth:420, background:"rgba(255,255,255,0.92)", backdropFilter:"blur(14px)", borderRadius:18, padding:36, boxShadow:"0 40px 80px -20px rgba(15,42,82,0.22), 0 12px 24px -12px rgba(15,42,82,0.10), inset 0 1px 0 rgba(255,255,255,0.9)", border:"1px solid rgba(208,222,244,0.9)" }}>
            <div aria-hidden="true" style={{ position:"absolute", top:0, left:24, right:24, height:2, background:"linear-gradient(90deg, transparent, #2F8CFF, transparent)", borderRadius:2 }}/>

            <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:22 }}>
              <div>
                <h2 style={{ fontSize:24, fontWeight:700, color:"#0B1426", margin:0, marginBottom:4, letterSpacing:"-0.018em" }}>Welcome back</h2>
                <p style={{ fontSize:12.5, color:"#6B7F9F", margin:0 }}>Sign in to continue to TriAxis Nexus</p>
              </div>
              <div style={{ display:"flex", alignItems:"center", gap:6, padding:"5px 10px", background:"rgba(16,185,129,0.08)", border:"1px solid rgba(16,185,129,0.25)", borderRadius:9999 }}>
                <span style={{ width:6, height:6, borderRadius:9999, background:"#10B981", animation:"tn-pulse 2s ease-in-out infinite" }}/>
                <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:9, fontWeight:600, color:"#0B8459", letterSpacing:"0.18em", textTransform:"uppercase" }}>Secure</span>
              </div>
            </div>

            {/* Tabs */}
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", background:"#F4F8FF", padding:4, borderRadius:10, marginBottom:22, gap:4, border:"1px solid #E2EBF8" }}>
              <button onClick={() => setTab("signin")} className="tn-tab" style={{ padding:"9px 0", fontSize:13, fontWeight:600, border:"none", borderRadius:8, cursor:"pointer", background: tab==="signin" ? "#FFFFFF" : "transparent", color: tab==="signin" ? "#0B1426" : "#6B7F9F", boxShadow: tab==="signin" ? "0 1px 3px rgba(15,42,82,0.10)" : "none" }}>Sign In</button>
              <button onClick={() => setTab("neworg")} className="tn-tab" style={{ padding:"9px 0", fontSize:13, fontWeight:600, border:"none", borderRadius:8, cursor:"pointer", background: tab==="neworg" ? "#FFFFFF" : "transparent", color: tab==="neworg" ? "#0B1426" : "#6B7F9F", boxShadow: tab==="neworg" ? "0 1px 3px rgba(15,42,82,0.10)" : "none" }}>New Organization</button>
            </div>

            {tab === "neworg" && (
              <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:12, marginBottom:16 }}>
                <div>
                  <label style={{ display:"block", fontSize:11, fontWeight:600, color:"#475569", marginBottom:6, letterSpacing:"0.04em", textTransform:"uppercase" }}>First Name</label>
                  <input className="tn-input" value={fname} onChange={e=>setFname(e.target.value)} style={{ width:"100%", padding:"11px 13px", background:"#FFFFFF", border:"1px solid #E2E8F0", borderRadius:9, fontSize:13, color:"#0B1426", boxSizing:"border-box" }}/>
                </div>
                <div>
                  <label style={{ display:"block", fontSize:11, fontWeight:600, color:"#475569", marginBottom:6, letterSpacing:"0.04em", textTransform:"uppercase" }}>Last Name</label>
                  <input className="tn-input" value={lname} onChange={e=>setLname(e.target.value)} style={{ width:"100%", padding:"11px 13px", background:"#FFFFFF", border:"1px solid #E2E8F0", borderRadius:9, fontSize:13, color:"#0B1426", boxSizing:"border-box" }}/>
                </div>
              </div>
            )}

            <label style={{ display:"block", fontSize:11, fontWeight:600, color:"#475569", marginBottom:6, letterSpacing:"0.04em", textTransform:"uppercase" }}>Work Email</label>
            <input className="tn-input" value={email} onChange={e=>setEmail(e.target.value)} style={{ width:"100%", padding:"11px 13px", background:"#FFFFFF", border:"1px solid #E2E8F0", borderRadius:9, fontSize:13, color:"#0B1426", marginBottom:16, boxSizing:"border-box" }}/>

            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:6 }}>
              <label style={{ fontSize:11, fontWeight:600, color:"#475569", letterSpacing:"0.04em", textTransform:"uppercase" }}>Password</label>
              {tab === "signin" && <a href="#" style={{ fontSize:12, fontWeight:500, color:"#2F8CFF", textDecoration:"none" }}>Forgot?</a>}
            </div>
            <input className="tn-input" type="password" value={pw} onChange={e=>setPw(e.target.value)} style={{ width:"100%", padding:"11px 13px", background:"#FFFFFF", border:"1px solid #E2E8F0", borderRadius:9, fontSize:13, color:"#0B1426", marginBottom:20, boxSizing:"border-box" }}/>

            {error && <p style={{ fontSize:12, color:"#DC2626", marginBottom:12, textAlign:"center" }}>{error}</p>}

            <button onClick={submit} disabled={loading} className="tn-btn-primary" style={{ width:"100%", background:"#2F8CFF", color:"#FFFFFF", fontSize:14, fontWeight:600, padding:13, borderRadius:10, border:"none", cursor:"pointer", boxShadow:"0 8px 18px -4px rgba(47,140,255,0.45)", display:"flex", alignItems:"center", justifyContent:"center", gap:8, opacity: loading ? 0.7 : 1 }}>
              {loading ? "Signing in…" : (tab === "signin" ? "Sign In to Nexus" : "Create Account")}
              {!loading && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>}
            </button>

            <div style={{ display:"flex", alignItems:"center", gap:12, margin:"20px 0" }}>
              <div style={{ flex:1, height:1, background:"#EAF0F8" }}/>
              <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:10, color:"#94A3B8", letterSpacing:"0.18em", textTransform:"uppercase" }}>or</span>
              <div style={{ flex:1, height:1, background:"#EAF0F8" }}/>
            </div>

            <button onClick={() => { setEmail("demo@triaxisai.com"); setPw("demo1234"); setTimeout(submit, 0); }} className="tn-demo" style={{ width:"100%", background:"#FFFFFF", color:"#0B1426", fontSize:13, fontWeight:600, padding:11, borderRadius:10, border:"1px solid #E2E8F0", cursor:"pointer", display:"flex", alignItems:"center", justifyContent:"center", gap:10 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#2F8CFF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
              Try Demo Account
            </button>

            <p style={{ fontSize:11, color:"#94A3B8", textAlign:"center", marginTop:18, marginBottom:0, lineHeight:1.5 }}>
              By signing in you agree to our <a href="#" style={{ color:"#2F8CFF", textDecoration:"none" }}>Terms</a> and <a href="#" style={{ color:"#2F8CFF", textDecoration:"none" }}>Privacy Policy</a>.
            </p>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer style={{ padding:"16px 40px", display:"flex", alignItems:"center", justifyContent:"space-between", borderTop:"1px solid #EAF0F8", background:"rgba(255,255,255,0.85)", backdropFilter:"blur(8px)", position:"relative", zIndex:10 }}>
        <span style={{ fontSize:11.5, color:"#94A3B8" }}>© 2026 TriAxis AI Technologies · In collaboration with Greenson Technology</span>
        <div style={{ display:"flex", alignItems:"center", gap:18, fontFamily:"'JetBrains Mono', monospace", fontSize:10, color:"#94A3B8", letterSpacing:"0.16em", textTransform:"uppercase" }}>
          <span style={{ display:"flex", alignItems:"center", gap:6 }}>
            <span style={{ width:6, height:6, borderRadius:9999, background:"#10B981", animation:"tn-pulse 2s ease-in-out infinite" }}/>
            All systems operational
          </span>
          <span>v3.0.0</span>
        </div>
      </footer>
    </div>
  );
}

export default function App() {
  const [user,       setUser]       = useState(() => { try { return JSON.parse(localStorage.getItem("user")); } catch { return null; } });
  const [authed,     setAuthed]     = useState(() => !!localStorage.getItem("access_token"));
  const [page,       setPage]       = useState("overview");
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [drawer,     setDrawer]     = useState(null);
  const [toast,      setToast]      = useState(null);
  const [alarmCount, setAlarmCount] = useState(0);
  const [dashDevice, setDashDevice] = useState(null);  // device open in device dashboard

  // Listen for map pin "Open Dashboard" clicks — dispatched by Leaflet popup
  useEffect(() => {
    const handler = async (e) => {
      const deviceId = e.detail;
      // Fetch device directly by ID — avoids allDevices scope issue
      try {
        const { deviceApi } = await import("./services/api.js");
        const device = await deviceApi.get(deviceId);
        if (device && device.id) {
          setDashDevice(device);
          setPage("device-dashboards");
        }
      } catch {
        // Fallback: navigate to device-dashboards and let user pick
        setPage("device-dashboards");
      }
    };
    window.addEventListener("taat-open-device", handler);
    return () => window.removeEventListener("taat-open-device", handler);
  }, []);

  // refreshKey drives: alarm badge count, Overview stats, manual refresh button.
  // Telemetry cards now use WebSocket (useDeviceTelemetry) so 3s polling is gone.
  // 30s is enough for alarm count and stat counters.
  useEffect(() => { const t = setInterval(() => setRefreshKey(k => k + 1), 30_000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!authed) return;
    alarmApi.list({ status: "ACTIVE_UNACK", limit: 100 }).then(as => setAlarmCount(as.length)).catch(() => {});
  }, [refreshKey, authed]);

  // useCallback ensures these functions are stable references across re-renders.
  // Without this, every App() re-render (toast, refreshKey tick, alarm count update)
  // creates new function references and invalidates React.memo on every child page.
  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setRefreshKey(k => k + 1);
    setTimeout(() => setRefreshing(false), 700);
  }, []);

  const handleLogin = useCallback(u => { setUser(u); setAuthed(true); }, []);

  const handleLogout = useCallback(async () => {
    try {
      const refreshToken = localStorage.getItem("refresh_token");
      if (refreshToken) await authApi.logout(refreshToken);
    } catch (_) {}
    localStorage.clear();
    setUser(null); setAuthed(false); setPage("overview");
  }, []);

  // Stable toast — inline arrow would create new reference on every render
  const showToast = useCallback((msg, type = "success") => {
    setToast({ msg, type });
  }, []);

  // Stable sidebar setPage handler
  const handleSetPage = useCallback(p => {
    setPage(p);
    setDrawer(null);
    if (p !== "device-dashboards") setDashDevice(null);
  }, []);

  // Stable drawer opener for DevicesPage
  const handleOpenDrawer = useCallback(device => setDrawer(device), []);

  // Stable dashDevice setter for DeviceListForDashboards
  const handleOpenDash = useCallback(d => setDashDevice(d), []);
  const handleBackDash = useCallback(() => setDashDevice(null), []);

  // Stable drawer close — new arrow fn every render would remount DeviceDrawer
  const handleCloseDrawer = useCallback(() => setDrawer(null), []);

  // Stable toast dismiss
  const handleDismissToast = useCallback(() => setToast(null), []);

  if (!authed) return <LoginPage onLogin={handleLogin} />;

  const pageTitle = (page === "device-dashboards" && dashDevice) ? dashDevice.name : PAGE_TITLES[page] || page;

  return (
    <div className="flex h-screen overflow-hidden" style={{background:"#F4F8FF"}}>
      <Sidebar page={page} setPage={handleSetPage} user={user} onLogout={handleLogout} alarmCount={alarmCount} />
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">
        <Header title={pageTitle} onRefresh={handleRefresh} refreshing={refreshing} />
        <main className={`flex-1 ${page === "user-dashboards" ? "overflow-hidden" : "overflow-y-auto p-6"}`} style={{background:"#F4F8FF"}}>
          {page === "overview"           && <OverviewPage refreshKey={refreshKey} onToast={showToast} />}
          {page === "user-dashboards"     && <UserDashboardPage onToast={showToast} user={user} />}
          {page === "device-dashboards"  && !dashDevice && <DeviceListForDashboards onOpen={handleOpenDash} />}
          {page === "device-dashboards"  && dashDevice  && <DashboardPage device={dashDevice} onBack={handleBackDash} user={user} />}
          {page === "devices"            && <DevicesPage onOpenDrawer={handleOpenDrawer} onToast={showToast} user={user} />}
          {page === "alarms"             && <AlarmsPage onToast={showToast} user={user} />}
          {page === "rule-chains"        && <RuleChainsPage onToast={showToast} user={user} />}
          {page === "customers"          && <CustomersPage onToast={showToast} user={user} />}
          {page === "users"              && <UsersPage onToast={showToast} user={user} />}
          {page === "settings"           && <SettingsPage user={user} onLogout={handleLogout} />}
          {page === "api-keys"           && <ApiKeysPage onToast={showToast} />}
          {page === "system-metrics"     && <SystemMetricsPage onToast={showToast} />}
          {page === "audit-log"          && <AuditLogPage onToast={showToast} />}
        </main>
      </div>
      {drawer && <DrawerMemo device={drawer} onClose={handleCloseDrawer} refreshKey={refreshKey} onToast={showToast} user={user} />}
      {toast  && <Toast msg={toast.msg} type={toast.type} onDone={handleDismissToast} />}
      {user?.role !== "CUSTOMER_USER" && <AIChatbot user={user} />}
    </div>
  );
}
