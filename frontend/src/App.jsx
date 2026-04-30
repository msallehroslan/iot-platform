/**
 * App.jsx — root shell + all non-dashboard pages
 * Dashboard logic lives in pages/DashboardPage.jsx
 * API calls use services/api.js + services/dashboardService.js
 */
import { useState, useEffect, useRef, useCallback } from "react";
import DashboardPage from "./pages/DashboardPage.jsx";
import UserDashboardPage from "./pages/UserDashboardPage.jsx";
import { authApi, deviceApi, telemetryApi, alarmApi, statsApi, provisioningApi } from "./services/api.js";
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
  if (data.length < 2) return (
    <div className="flex flex-col items-center justify-center h-36 gap-2">
      <svg className="w-8 h-8 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      <p className="text-xs text-slate-400">No telemetry data yet</p>
    </div>
  );
  const W = 500, H = 150, pad = { t: 10, r: 10, b: 22, l: 32 };
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const vals = data.map(p => typeof p.value === "number" ? p.value : parseFloat(p.value) || 0);
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const px = i => pad.l + (i / (vals.length - 1)) * w;
  const py = v => pad.t + h - ((v - mn) / rng) * h;
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `${path} L${px(vals.length - 1)},${pad.t + h} L${pad.l},${pad.t + h} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      <defs><linearGradient id="lcA" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.12"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>
      {[0,.25,.5,.75,1].map(t => { const y=pad.t+h*t,val=(mx-rng*t).toFixed(1); return <g key={t}><line x1={pad.l} y1={y} x2={pad.l+w} y2={y} stroke="#f1f5f9" strokeWidth="1"/><text x={pad.l-4} y={y+3} fontSize="8" fill="#94a3b8" textAnchor="end" fontFamily="monospace">{val}</text></g>; })}
      {data.filter((_,i)=>i%Math.max(1,Math.floor(data.length/5))===0||i===data.length-1).map(p=>{const idx=data.indexOf(p);return <text key={idx} x={px(idx)} y={pad.t+h+15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">{new Date(p.ts).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</text>;})}
      <path d={area} fill="url(#lcA)"/>
      <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
      <circle cx={px(vals.length-1)} cy={py(vals[vals.length-1])} r="4" fill={color} stroke="white" strokeWidth="2"/>
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
  { id:"settings",          label:"Settings",          icon:"M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm6.93-3h1.07a2 2 0 0 1 0 4h-1.07A7 7 0 0 1 17 18.93V20a2 2 0 0 1-4 0v-1.07A7 7 0 0 1 11.07 16H10a2 2 0 0 1 0-4h1.07A7 7 0 0 1 13 4.07V3a2 2 0 0 1 4 0v1.07A7 7 0 0 1 18.93 6H20a2 2 0 0 1 0 4h-1.07" },
];

function Sidebar({ page, setPage, user, onLogout, alarmCount }) {
  const [col, setCol] = useState(false);
  const ini = user ? (user.first_name?.[0]||user.email?.[0]||"U").toUpperCase() : "U";
  const name = user ? (user.first_name ? `${user.first_name} ${user.last_name||""}`.trim() : user.email) : "User";
  return (
    <aside className={`${col?"w-14":"w-56"} flex-shrink-0 flex flex-col h-screen transition-all duration-200`} style={{background:"#EAF2FF"}}>
      <div className="flex items-center gap-3 px-4 py-5 border-b border-[#D8E3F3] overflow-hidden">
        {!col && <span className="font-bold text-[#0B1426] text-sm tracking-wide truncate">TriAxis IoT</span>}
      </div>
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        {!col && <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-widest text-[#6B7F9F]">Menu</p>}
        {NAV.map(({id,label,icon}) => (
          <button key={id} onClick={() => setPage(id)} title={col?label:undefined}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${page===id?"bg-[#D7E8FF] text-[#0B4BB3] font-semibold":"text-[#334866] hover:bg-[#D7E8FF]/60 hover:text-[#0B1426]"}`}>
            <svg className="w-[17px] h-[17px] flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={icon}/></svg>
            {!col && <span className="truncate">{label}</span>}
            {!col && id==="alarms" && alarmCount>0 && <span className="ml-auto bg-red-500 text-white text-[9px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center">{alarmCount}</span>}
            {!col && id==="device-dashboards" && <span className="ml-auto text-[9px] font-bold bg-[#2F8CFF] text-white px-1.5 py-0.5 rounded-full">NEW</span>}
            {!col && page===id && !["alarms","device-dashboards"].includes(id) && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-[#2F8CFF] flex-shrink-0"/>}
          </button>
        ))}
      </nav>
      <div className="border-t border-[#D8E3F3] p-3 space-y-2">
        {!col && <div onClick={onLogout} className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer hover:bg-[#D7E8FF] transition-colors overflow-hidden"><div className="w-7 h-7 rounded-full bg-[#2F8CFF] flex items-center justify-center text-xs font-bold text-white flex-shrink-0">{ini}</div><div className="overflow-hidden"><p className="text-xs font-medium text-[#0B1426] truncate">{name}</p><p className="text-[10px] text-[#6B7F9F]">{user?.role||"TENANT_ADMIN"} · Sign out</p></div></div>}
        <button onClick={() => setCol(c=>!c)} className="w-full flex items-center justify-center py-1.5 rounded-lg text-[#6B7F9F] hover:text-[#0B1426] hover:bg-[#D7E8FF] transition-colors"><svg className={`w-4 h-4 transition-transform ${col?"rotate-180":""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg></button>
      </div>
    </aside>
  );
}

function Header({ title, onRefresh, refreshing }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setTime(new Date()), 1000); return () => clearInterval(t); }, []);
  return (
    <header className="h-16 flex-shrink-0 border-b flex items-center justify-between px-7 shadow-sm shadow-blue-100/30" style={{background:"#F4F8FF",borderColor:"#D8E3F3"}}>
      <div><h1 className="text-base font-bold text-[#0B1426]">{title}</h1><p className="text-[11px] text-[#6B7F9F] mt-0.5">{time.toLocaleDateString("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"})}</p></div>
      <div className="flex items-center gap-3">
        <button onClick={onRefresh} className="flex items-center gap-1.5 text-[11px] font-medium text-[#334866] hover:text-[#0B1426] px-3 py-1.5 rounded-lg hover:bg-[#D7E8FF] transition-colors"><svg className={`w-3.5 h-3.5 ${refreshing?"animate-spin":""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>Refresh</button>
        <div className="w-px h-4 bg-[#D8E3F3]"/>
        <span className="text-[11px] font-mono text-[#6B7F9F]">{time.toLocaleTimeString()}</span>
      </div>
    </header>
  );
}
// ── Overview page ────────────────────────────────────────────────────────────
function OverviewPage({ refreshKey, onToast }) {
  const [stats,setStats]=useState(null); const [devices,setDevices]=useState([]); const [alarms,setAlarms]=useState([]); const [loading,setLoading]=useState(true);
  const [chartDev,setChartDev]=useState(null); const [chartKey,setChartKey]=useState("temperature"); const [chartData,setChartData]=useState([]); const [chartKeys,setChartKeys]=useState([]);
  const sparkRef=useRef(Array.from({length:20},(_,i)=>i));
  const fetchAll=useCallback(async()=>{try{const[s,d,a]=await Promise.all([statsApi.get(),deviceApi.list({limit:20}),alarmApi.list({limit:5})]);setStats(s);setDevices(d);setAlarms(a);if(!chartDev&&d.length>0)setChartDev(d[0]);}catch(e){onToast(e.message,"error");}finally{setLoading(false);}}, [chartDev]);
  useEffect(()=>{fetchAll();},[refreshKey]);
  useEffect(()=>{if(!chartDev)return;telemetryApi.keys(chartDev.id).then(r=>{const ks=r?.keys||[];setChartKeys(ks);if(ks.length>0&&!ks.includes(chartKey))setChartKey(ks[0]);}).catch(()=>{});},[chartDev?.id]);
  // Chart data: fetch on device/key change. WS subscription appends live points.
  useEffect(()=>{
    if(!chartDev||!chartKey)return;
    telemetryApi.history(chartDev.id,chartKey,50).then(setChartData).catch(()=>setChartData([]));
  },[chartDev?.id,chartKey]);

  // Append live WS data to chart when the selected device+key updates
  useEffect(()=>{
    if(!chartDev?.id||!chartKey)return;
    const unsub = TelemetrySocket.subscribe(chartDev.id,[chartKey],(vals,ts)=>{
      if(!(chartKey in vals))return;
      setChartData(prev=>{ const a=[...prev,{ts,value:vals[chartKey]}]; return a.length>50?a.slice(-50):a; });
    });
    return ()=>unsub();
  },[chartDev?.id,chartKey]);
  const active=devices.filter(d=>d.status==="ACTIVE");
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {[{label:"Total Devices",value:stats?.total_devices,color:"#3b82f6",bg:"bg-[#EAF2FF]",ic:"text-[#2F8CFF]",path:"M2 3a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3zM8 21h8M12 17v4"},{label:"Active Nodes",value:stats?.active_devices,color:"#10b981",bg:"bg-emerald-50",ic:"text-emerald-500",path:"M1.42 9a16 16 0 0 1 21.16 0M5 12.55a11 11 0 0 1 14.08 0M10.83 15.76a6.06 6.06 0 0 1 2.34 0M12 20h.01"},{label:"Active Alarms",value:stats?.active_alarms,color:"#f59e0b",bg:"bg-amber-50",ic:"text-amber-500",path:"M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0"},{label:"Events Today",value:stats?.telemetry_today?.toLocaleString(),color:"#8b5cf6",bg:"bg-violet-50",ic:"text-violet-500",path:"M4 7c0-1.1.9-2 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7zm0 5h16"}].map(({label,value,color,bg,ic,path})=>(
          <div key={label} className="rounded-2xl border p-5 flex flex-col gap-3 shadow-sm shadow-blue-100/40 hover:shadow-md hover:shadow-blue-100/70 transition-shadow" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
            <div className="flex items-start justify-between"><div><p className="text-[11px] font-semibold uppercase tracking-widest text-[#6B7F9F] mb-1">{label}</p><p className="text-3xl font-bold text-[#0B1426] leading-none">{loading?"—":(value??0)}</p></div><div className={`w-11 h-11 rounded-xl ${bg} flex items-center justify-center flex-shrink-0`}><svg className={`w-5 h-5 ${ic}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={path}/></svg></div></div>
            <Sparkline data={sparkRef.current.map(i=>(value||5)+Math.sin(i*.5+label.length)*2)} color={color} height={36}/>
          </div>
        ))}
      </div>
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
      {rows.length===0?<p className="text-[11px] text-slate-400 py-2">No telemetry</p>:<div className="divide-y divide-slate-50">{rows.slice(0,5).map(r=><div key={r.key} className="flex items-center justify-between py-1.5"><span className="text-[11px] text-slate-500">{r.key}</span><span className="text-[11px] font-semibold font-mono text-slate-800">{typeof r.value==="number"?r.value.toFixed(2):String(r.value??"—")}</span></div>)}</div>}
      {ts&&<p className="text-[10px] text-slate-400 mt-3">{new Date(ts).toLocaleTimeString()}</p>}
    </div>
  );
}
// ── Device List for dashboards ───────────────────────────────────────────────
function DeviceListForDashboards({ onOpen }) {
  const [devices,setDevices]=useState([]); const [loading,setLoading]=useState(true); const [search,setSearch]=useState("");
  useEffect(()=>{deviceApi.list({limit:50}).then(setDevices).catch(()=>{}).finally(()=>setLoading(false));},[]);
  const filtered=devices.filter(d=>d.name.toLowerCase().includes(search.toLowerCase()));
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

function DevicesPage({ onOpenDrawer, onToast }) {
  const [devices,setDevices]=useState([]); const [loading,setLoading]=useState(true); const [search,setSearch]=useState(""); const [showM,setShowM]=useState(false); const [editDev,setEditDev]=useState(null); const [delId,setDelId]=useState(null);
  const fetch=useCallback(async()=>{try{setDevices(await deviceApi.list());}catch(e){onToast(e.message,"error");}finally{setLoading(false);}}, []);
  useEffect(()=>{fetch();},[]);
  const handleDel=async id=>{if(delId!==id){setDelId(id);setTimeout(()=>setDelId(null),3000);return;}try{await deviceApi.delete(id);setDevices(ds=>ds.filter(d=>d.id!==id));onToast("Device deleted");}catch(e){onToast(e.message,"error");}setDelId(null);};
  const handleSaved=dev=>{setDevices(ds=>{const i=ds.findIndex(d=>d.id===dev.id);if(i>=0){const n=[...ds];n[i]=dev;return n;}return[dev,...ds];});setShowM(false);setEditDev(null);onToast(editDev?"Device updated":"Device created");};
  const filtered=devices.filter(d=>d.name.toLowerCase().includes(search.toLowerCase())||(d.device_type||"").toLowerCase().includes(search.toLowerCase()));
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div className="relative"><svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg><input className="pl-9 pr-4 py-2 text-sm border rounded-lg bg-white text-[#334866] outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 w-64" style={{borderColor:"#D8E3F3"}} placeholder="Search…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
        <button onClick={()=>{setEditDev(null);setShowM(true);}} className="flex items-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors shadow-sm shadow-blue-500/25"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Add Device</button>
      </div>
      <div className="rounded-2xl border shadow-sm shadow-blue-100/40 overflow-hidden" style={{background:"#FFFFFF",borderColor:"#D8E3F3"}}>
        {loading?<div className="flex justify-center py-12"><Spinner/></div>:filtered.length===0?<Empty icon="M2 3a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3zM8 21h8M12 17v4" title={search?"No match":"No devices"} sub="Add your first device"/>:
        <><table className="w-full text-sm"><thead><tr className="border-b border-slate-100 bg-slate-50">{["Device","Type","Status","Token","Created",""].map(h=><th key={h} className="text-left px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-400">{h}</th>)}</tr></thead>
          <tbody>{filtered.map(d=>(
            <tr key={d.id} onClick={()=>onOpenDrawer(d)} className="border-b border-slate-50 last:border-0 hover:bg-slate-50 cursor-pointer transition-colors">
              <td className="px-5 py-3.5"><div className="flex items-center gap-3"><div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center flex-shrink-0"><svg className="w-4 h-4 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div><div><p className="font-medium text-slate-700">{d.name}</p>{d.label&&<p className="text-[11px] text-slate-400">{d.label}</p>}</div></div></td>
              <td className="px-5 py-3.5"><span className="text-[11px] font-mono bg-slate-100 text-slate-600 px-2 py-0.5 rounded">{d.device_type}</span></td>
              <td className="px-5 py-3.5"><span className={SB[d.status]||SB.INACTIVE}><span className={SD[d.status]||SD.INACTIVE}/>{d.status}</span></td>
              <td className="px-5 py-3.5 font-mono text-[11px] text-slate-400">{d.token.slice(0,8)}…</td>
              <td className="px-5 py-3.5 text-[12px] text-slate-400">{new Date(d.created_at).toLocaleDateString()}</td>
              <td className="px-5 py-3.5" onClick={e=>e.stopPropagation()}><div className="flex items-center gap-1 justify-end">
                <button onClick={()=>{setEditDev(d);setShowM(true);}} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition-colors"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
                <button onClick={()=>handleDel(d.id)} className={`p-1.5 rounded-lg transition-colors ${delId===d.id?"bg-red-50 text-red-500":"hover:bg-red-50 text-slate-400 hover:text-red-500"}`}><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button>
              </div></td>
            </tr>))}</tbody>
          </table><div className="px-5 py-2.5 bg-slate-50 border-t border-slate-100 text-[11px] text-slate-400">Showing {filtered.length} of {devices.length} devices</div></>}
      </div>
      {showM&&<DeviceModal device={editDev} onSaved={handleSaved} onClose={()=>{setShowM(false);setEditDev(null);}} onToast={onToast}/>}
    </div>
  );
}

function DeviceModal({ device, onSaved, onClose, onToast }) {
  const isEdit=!!device; const [form,setForm]=useState({name:device?.name||"",device_type:device?.device_type||"DEFAULT",label:device?.label||"",description:device?.description||"",status:device?.status||"INACTIVE"}); const [saving,setSaving]=useState(false); const [err,setErr]=useState("");
  const set=(k,v)=>setForm(f=>({...f,[k]:v}));
  const submit=async()=>{if(!form.name.trim()){setErr("Name required");return;}setSaving(true);setErr("");try{const s=isEdit?await deviceApi.update(device.id,form):await deviceApi.create({name:form.name,device_type:form.device_type,label:form.label,description:form.description});onSaved(s);}catch(e){setErr(e.message);}finally{setSaving(false);}};
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md border border-slate-100">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100"><h3 className="font-semibold text-slate-800">{isEdit?"Edit Device":"Add Device"}</h3><button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400"><svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>
        <div className="p-6 space-y-4">
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Name *</label><input className={INP} placeholder="My Sensor" value={form.name} onChange={e=>set("name",e.target.value)}/></div>
          <div className="grid grid-cols-2 gap-3"><div><label className="block text-xs font-medium text-slate-500 mb-1.5">Type</label><select className={INP+" cursor-pointer"} value={form.device_type} onChange={e=>set("device_type",e.target.value)}>{["DEFAULT","GATEWAY","SENSOR","ACTUATOR","METER","CAMERA"].map(t=><option key={t}>{t}</option>)}</select></div>{isEdit&&<div><label className="block text-xs font-medium text-slate-500 mb-1.5">Status</label><select className={INP+" cursor-pointer"} value={form.status} onChange={e=>set("status",e.target.value)}>{["ACTIVE","INACTIVE","DISABLED"].map(s=><option key={s}>{s}</option>)}</select></div>}</div>
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Label</label><input className={INP} placeholder="Building A" value={form.label} onChange={e=>set("label",e.target.value)}/></div>
          <div><label className="block text-xs font-medium text-slate-500 mb-1.5">Description</label><textarea className={INP+" resize-none"} rows={2} value={form.description} onChange={e=>set("description",e.target.value)}/></div>
          {err&&<p className="text-xs text-red-500 bg-red-50 px-3 py-2 rounded-lg">{err}</p>}
          <div className="flex gap-2 pt-1"><button onClick={submit} disabled={saving} className="flex-1 flex items-center justify-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] disabled:opacity-60 text-white font-medium text-sm py-2.5 rounded-lg">{saving&&<Spinner/>}{isEdit?"Update":"Create"}</button><button onClick={onClose} className="px-4 border border-slate-200 text-sm text-slate-500 rounded-lg hover:bg-slate-50">Cancel</button></div>
        </div>
      </div>
    </div>
  );
}
// ── Alarms page ───────────────────────────────────────────────────────────────
function AlarmsPage({ onToast }) {
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
                  {(a.status==="ACTIVE_UNACK"||a.status==="CLEARED_UNACK")&&<button onClick={()=>handleAck(a.id)} className="p-1.5 rounded-lg hover:bg-emerald-50 text-slate-400 hover:text-emerald-600"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg></button>}
                  {!a.status.startsWith("CLEARED")&&<button onClick={()=>handleClear(a.id)} className="p-1.5 rounded-lg hover:bg-blue-50 text-slate-400 hover:text-blue-500"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></button>}
                  <button onClick={()=>handleDel(a.id)} className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500"><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button>
                </div></td>
              </tr>);})}</tbody>
          </table>}
      </div>
    </div>
  );
}

// ── Device Drawer ─────────────────────────────────────────────────────────────
function DeviceDrawer({ device: initDev, onClose, refreshKey, onToast }) {
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
          <div><div className="flex items-center justify-between mb-1.5"><p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Token</p><button onClick={handleRegen} disabled={regen} className="flex items-center gap-1 text-[10px] font-medium text-slate-400 hover:text-slate-600 px-2 py-1 rounded hover:bg-slate-100"><svg className={`w-3 h-3 ${regen?"animate-spin":""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/></svg>Regenerate</button></div>
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
}

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

// ── Login page ────────────────────────────────────────────────────────────────
function LoginPage({ onLogin }) {
  const [tab,setTab]=useState("signin"); const [email,setEmail]=useState("demo@triaxisai.com"); const [pw,setPw]=useState("demo1234"); const [fname,setFname]=useState(""); const [lname,setLname]=useState(""); const [loading,setLoading]=useState(false); const [error,setError]=useState(""); const [showReset,setShowReset]=useState(false); const [showPw,setShowPw]=useState(false);
  const BASE_URL=(typeof import.meta!=="undefined"&&import.meta.env?.VITE_API_URL)||"http://localhost:8000";
  const submit=async()=>{setLoading(true);setError("");try{let d;if(tab==="signin"){d=await authApi.login(email,pw);}else{await authApi.register({email,password:pw,first_name:fname,last_name:lname});d=await authApi.login(email,pw);}localStorage.setItem("access_token",d.access_token);localStorage.setItem("user",JSON.stringify(d.user));onLogin(d.user);}catch(e){setError(e.message);}finally{setLoading(false);}};
  const demo=async()=>{setLoading(true);setError("");try{await authApi.seedDemo();const d=await authApi.login("demo@triaxisai.com","demo1234");localStorage.setItem("access_token",d.access_token);localStorage.setItem("user",JSON.stringify(d.user));onLogin(d.user);}catch(e){setError("Backend not reachable. Start it first.");}finally{setLoading(false);}};

  if (showReset) return <ResetPasswordPage onBack={() => setShowReset(false)} />;

  return (
    <div className="min-h-screen flex" style={{background:"#F4F8FF"}}>
      <div className="hidden lg:flex flex-col justify-between w-[420px] p-10 flex-shrink-0" style={{background:"#EAF2FF"}}>
        <div className="flex flex-col gap-2"><span className="font-bold text-[#0B1426] text-2xl tracking-wide">TriAxis IoT</span></div>
        <div className="max-w-xs"><h2 className="text-5xl font-bold text-[#0B1426] leading-tight mb-6">Connect,<br/>Monitor,<br/><span style={{background:"linear-gradient(135deg,#0B4BB3,#2F8CFF)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>Control.</span></h2><p className="text-[#334866] text-base leading-relaxed">Unified IoT platform for real-time visibility, intelligent alerts, and seamless device management — all in one place.</p></div>
        <div className="grid grid-cols-2 gap-3">{[["Live Devices","Fleet status"],["Smart Alerts","Faster response"],["Telemetry","Real-time data"],["TriAxis","IoT control"]].map(([v,l])=><div key={l} className="rounded-xl border p-4 shadow-sm" style={{background:"rgba(255,255,255,0.38)",borderColor:"#D8E3F3"}}><p className="text-sm font-bold text-[#0B1426]">{v}</p><p className="text-xs text-[#6B7F9F] mt-0.5">{l}</p></div>)}</div>
      </div>
      <div className="flex-1 flex items-center justify-center p-8 lg:justify-start lg:pl-40" style={{background:"#F4F8FF"}}>
        <div className="w-full max-w-md rounded-2xl border bg-white/70 p-7 shadow-sm" style={{borderColor:"#D8E3F3"}}>
          <h1 className="text-2xl font-bold text-[#0B1426] mb-1">{tab==="signin"?"Welcome back":"Create account"}</h1>
          <p className="text-sm text-[#6B7F9F] mb-6">{tab==="signin"?"Sign in to continue to TriAxis IoT":"Register a new account"}</p>
          <div className="flex gap-1 p-1 rounded-lg mb-6" style={{background:"#EAF2FF"}}>{[["signin","Sign In"],["register","Register"]].map(([id,lbl])=><button key={id} onClick={()=>setTab(id)} className={`flex-1 py-2 rounded-md text-xs font-semibold transition-all ${tab===id?"bg-white text-[#0B1426] shadow-sm":"text-[#334866] hover:text-[#0B1426]"}`}>{lbl}</button>)}</div>
          <div className="space-y-3">
            {tab==="register"&&<div className="grid grid-cols-2 gap-3"><div><label className="block text-xs font-medium text-[#334866] mb-1.5">First Name</label><input className={INP} value={fname} onChange={e=>setFname(e.target.value)}/></div><div><label className="block text-xs font-medium text-[#334866] mb-1.5">Last Name</label><input className={INP} value={lname} onChange={e=>setLname(e.target.value)}/></div></div>}
            <div><label className="block text-xs font-medium text-[#334866] mb-1.5">Email</label><input type="email" value={email} onChange={e=>setEmail(e.target.value)} className={INP}/></div>
            <div><div className="flex items-center justify-between mb-1.5"><label className="block text-xs font-medium text-[#334866]">Password</label>{tab==="signin"&&<button onClick={()=>setShowReset(true)} className="text-xs font-medium text-[#2F8CFF] hover:underline">Forgot password?</button>}</div><input type="password" value={pw} onChange={e=>setPw(e.target.value)} className={INP}/></div>
          </div>
          {error&&<p className="mt-3 text-xs text-red-500 bg-red-50 px-3 py-2 rounded-lg">{error}</p>}
          <button onClick={submit} disabled={loading} className="w-full mt-5 flex items-center justify-center gap-2 bg-[#2F8CFF] hover:bg-[#0B4BB3] disabled:opacity-60 text-white font-semibold text-sm py-3 rounded-lg shadow-sm shadow-blue-500/20">{loading&&<Spinner/>}{tab==="signin"?"Sign In":"Create Account"}</button>
          <div className="flex items-center gap-3 my-5"><div className="flex-1 border-t border-slate-200"/><span className="text-xs text-[#6B7F9F]">or</span><div className="flex-1 border-t border-slate-200"/></div>
          <button onClick={demo} disabled={loading} className="w-full flex items-center justify-center gap-2 border border-[#D8E3F3] text-[#334866] hover:bg-[#EAF2FF] disabled:opacity-60 font-medium text-sm py-3 rounded-lg transition-colors">{loading&&<Spinner/>}🚀 Try Demo Account</button>
        </div>
      </div>
    </div>
  );
}

// ── App root ──────────────────────────────────────────────────────────────────
const PAGE_TITLES = {
  overview:"Overview", "user-dashboards":"My Dashboards", "device-dashboards":"Device Dashboards",
  devices:"Devices", alarms:"Alarms",
  "rule-chains":"Rule Chains", customers:"Customers", settings:"Settings",
};

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

  // refreshKey drives: alarm badge count, Overview stats, manual refresh button.
  // Telemetry cards now use WebSocket (useDeviceTelemetry) so 3s polling is gone.
  // 30s is enough for alarm count and stat counters.
  useEffect(() => { const t = setInterval(() => setRefreshKey(k => k + 1), 30_000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!authed) return;
    alarmApi.list({ status: "ACTIVE_UNACK", limit: 100 }).then(as => setAlarmCount(as.length)).catch(() => {});
  }, [refreshKey, authed]);

  const handleRefresh = () => { setRefreshing(true); setRefreshKey(k => k + 1); setTimeout(() => setRefreshing(false), 700); };
  const handleLogin   = u => { setUser(u); setAuthed(true); };
  const handleLogout  = () => {
    // Clear everything — prevents stale data leaking to next login session
    localStorage.clear();
    setUser(null); setAuthed(false); setPage("overview");
  };
  const showToast     = (msg, type = "success") => setToast({ msg, type });

  if (!authed) return <LoginPage onLogin={handleLogin} />;

  const pageTitle = (page === "device-dashboards" && dashDevice) ? dashDevice.name : PAGE_TITLES[page] || page;

  return (
    <div className="flex h-screen overflow-hidden" style={{background:"#F4F8FF"}}>
      <Sidebar page={page} setPage={p => { setPage(p); setDrawer(null); if (p !== "device-dashboards") setDashDevice(null); }} user={user} onLogout={handleLogout} alarmCount={alarmCount} />
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">
        <Header title={pageTitle} onRefresh={handleRefresh} refreshing={refreshing} />
        <main className={`flex-1 ${page === "user-dashboards" ? "overflow-hidden" : "overflow-y-auto p-6"}`} style={{background:"#F4F8FF"}}>
          {page === "overview"           && <OverviewPage refreshKey={refreshKey} onToast={showToast} />}
          {page === "user-dashboards"     && <UserDashboardPage onToast={showToast} />}
          {page === "device-dashboards"  && !dashDevice && <DeviceListForDashboards onOpen={d => setDashDevice(d)} />}
          {page === "device-dashboards"  && dashDevice  && <DashboardPage device={dashDevice} onBack={() => setDashDevice(null)} />}
          {page === "devices"            && <DevicesPage onOpenDrawer={setDrawer} onToast={showToast} />}
          {page === "alarms"             && <AlarmsPage onToast={showToast} />}
          {page === "rule-chains"        && <ComingSoon label="Rule Chains"  desc="Define automated workflows triggered by device telemetry." icon="M6 3v12m12-9a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6m12-9a9 9 0 0 1-9 9"/>}
          {page === "customers"          && <ComingSoon label="Customers"    desc="Manage customer accounts and assign devices per tenant." icon="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm14 2v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>}
          {page === "settings"           && <SettingsPage user={user} onLogout={handleLogout} />}
        </main>
      </div>
      {drawer && <DeviceDrawer device={drawer} onClose={() => setDrawer(null)} refreshKey={refreshKey} onToast={showToast} />}
      {toast  && <Toast msg={toast.msg} type={toast.type} onDone={() => setToast(null)} />}
    </div>
  );
}
