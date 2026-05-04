/**
 * widgets/index.jsx
 * Rendering components for each widget type.
 * Props: { config, liveTelem, historyData, alarms, deviceId }
 */
import { useState, useEffect } from "react";
import { telemetryApi, API_BASE } from "../../services/api.js";

// ── Shared chart primitives ───────────────────────────────────────────────────

export function Sparkline({ data = [], color = "#3b82f6", height = 36 }) {
  if (data.length < 2) return <div style={{ height, background: "#f8fafc", borderRadius: 6 }} />;
  const W = 300, H = height;
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1;
  const px = i => (i / (data.length - 1)) * W;
  const py = v => H - 2 - ((v - mn) / rng) * (H - 6);
  const d  = data.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `${d} L${px(data.length - 1)},${H} L0,${H} Z`;
  const gid = `sk${color.replace(/[^a-z0-9]/gi, "")}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height, display: "block" }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.2" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(data.length - 1)} cy={py(data[data.length - 1])} r="3" fill={color} />
    </svg>
  );
}

export function LineChartSVG({ data = [], color = "#3b82f6" }) {
  if (data.length < 2) return (
    <div style={{ height: 140, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
      <svg style={{ width: 28, height: 28, color: "#e2e8f0" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      <p style={{ fontSize: 11, color: "#94a3b8" }}>No data yet</p>
    </div>
  );
  const W = 460, H = 140, pad = { t: 8, r: 8, b: 20, l: 30 };
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const vals = data.map(p => typeof p.value === "number" ? p.value : parseFloat(p.value) || 0);
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const px = i => pad.l + (i / (vals.length - 1)) * w;
  const py = v => pad.t + h - ((v - mn) / rng) * h;
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `${path} L${px(vals.length - 1)},${pad.t + h} L${pad.l},${pad.t + h} Z`;
  const gid = `lc${color.replace(/[^a-z0-9]/gi, "")}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: H }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.14" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map(t => {
        const y = pad.t + h * t, val = (mx - rng * t).toFixed(1);
        return <g key={t}><line x1={pad.l} y1={y} x2={pad.l + w} y2={y} stroke="#f1f5f9" strokeWidth="1" /><text x={pad.l - 4} y={y + 3} fontSize="8" fill="#94a3b8" textAnchor="end" fontFamily="monospace">{val}</text></g>;
      })}
      {data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 5)) === 0 || i === data.length - 1).map(p => {
        const idx = data.indexOf(p);
        return <text key={idx} x={px(idx)} y={pad.t + h + 15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">{new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text>;
      })}
      <path d={area} fill={`url(#${gid})`} />
      <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(vals.length - 1)} cy={py(vals[vals.length - 1])} r="4" fill={color} stroke="white" strokeWidth="2" />
    </svg>
  );
}

export function GaugeSVG({ value, min = 0, max = 100, color = "#3b82f6" }) {
  const pct = Math.max(0, Math.min(1, ((value ?? min) - min) / ((max - min) || 1)));
  const r2d = d => d * Math.PI / 180;
  const cx = 60, cy = 65, R = 50, start = -210, total = 240;
  const arcPt = a => ({ x: cx + R * Math.cos(r2d(a)), y: cy + R * Math.sin(r2d(a)) });
  const arc = (a1, a2) => {
    const s = arcPt(a1), e = arcPt(a2), lg = Math.abs(a2 - a1) > 180 ? 1 : 0;
    return `M${s.x.toFixed(1)},${s.y.toFixed(1)} A${R},${R} 0 ${lg} 1 ${e.x.toFixed(1)},${e.y.toFixed(1)}`;
  };
  const needleAngle = start + pct * total;
  const nx = cx + (R - 14) * Math.cos(r2d(needleAngle));
  const ny = cy + (R - 14) * Math.sin(r2d(needleAngle));
  return (
    <svg viewBox="0 0 120 90" style={{ width: "100%", maxWidth: 180, display: "block", margin: "0 auto" }}>
      <path d={arc(start, start + total)} fill="none" stroke="#e2e8f0" strokeWidth="9" strokeLinecap="round" />
      {pct > 0 && <path d={arc(start, needleAngle)} fill="none" stroke={color} strokeWidth="9" strokeLinecap="round" />}
      <line x1={cx} y1={cy} x2={nx.toFixed(1)} y2={ny.toFixed(1)} stroke={color} strokeWidth="3" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="5" fill={color} />
      <text x={cx} y={cy + 18} textAnchor="middle" fontSize="12" fontWeight="700" fill="#1e293b" fontFamily="monospace">
        {typeof value === "number" ? value.toFixed(1) : "—"}
      </text>
    </svg>
  );
}

export function BarChartSVG({ data = [], color = "#3b82f6" }) {
  // data = [{ts, value}, ...] — time-series array, same shape as LineChartSVG
  if (!data.length) return (
    <div style={{ height: 140, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
      <svg style={{ width: 28, height: 28, color: "#e2e8f0" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 20V10M6 20V4M18 20v-4"/></svg>
      <p style={{ fontSize: 11, color: "#94a3b8" }}>No data yet</p>
    </div>
  );
  const W = 460, H = 140, pad = { t: 8, r: 8, b: 20, l: 30 };
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const vals = data.map(p => typeof p.value === "number" ? p.value : parseFloat(p.value) || 0);
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const bw = Math.max(1, w / vals.length - 1);
  const px = i => pad.l + (i / vals.length) * w + bw / 2;
  const bh = v => Math.max(1, ((v - mn) / rng) * h);
  const gid = `bc${color.replace(/[^a-z0-9]/gi, "")}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: H }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.9" />
          <stop offset="100%" stopColor={color} stopOpacity="0.4" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map(t => {
        const y = pad.t + h * t, val = (mx - rng * t).toFixed(1);
        return <g key={t}><line x1={pad.l} y1={y} x2={pad.l + w} y2={y} stroke="#f1f5f9" strokeWidth="1" /><text x={pad.l - 4} y={y + 3} fontSize="8" fill="#94a3b8" textAnchor="end" fontFamily="monospace">{val}</text></g>;
      })}
      {data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 5)) === 0 || i === data.length - 1).map(p => {
        const idx = data.indexOf(p);
        return <text key={idx} x={px(idx)} y={pad.t + h + 15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">{new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text>;
      })}
      {vals.map((v, i) => (
        <rect key={i}
          x={pad.l + (i / vals.length) * w}
          y={pad.t + h - bh(v)}
          width={bw}
          height={bh(v)}
          fill={`url(#${gid})`}
          rx="2"
        />
      ))}
      {/* Latest value dot */}
      <circle cx={px(vals.length - 1)} cy={pad.t + h - bh(vals[vals.length - 1])} r="3" fill={color} stroke="white" strokeWidth="2" />
    </svg>
  );
}

const PIE_COLORS = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16"];

export function PieChartSVG({ data = [] }) {
  const total = data.reduce((s, d) => s + Math.abs(d.value || 0), 0);
  if (!total) return null;
  const cx = 60, cy = 60, R = 52, inner = 28;
  let angle = -Math.PI / 2;
  const slices = data.map((d, i) => {
    const sweep = (Math.abs(d.value) / total) * 2 * Math.PI;
    const x1 = cx + R * Math.cos(angle),       y1 = cy + R * Math.sin(angle);
    const x2 = cx + R * Math.cos(angle + sweep), y2 = cy + R * Math.sin(angle + sweep);
    const ix1 = cx + inner * Math.cos(angle),    iy1 = cy + inner * Math.sin(angle);
    const ix2 = cx + inner * Math.cos(angle + sweep), iy2 = cy + inner * Math.sin(angle + sweep);
    const lg = sweep > Math.PI ? 1 : 0;
    const path = `M${ix1.toFixed(1)},${iy1.toFixed(1)} L${x1.toFixed(1)},${y1.toFixed(1)} A${R},${R} 0 ${lg} 1 ${x2.toFixed(1)},${y2.toFixed(1)} L${ix2.toFixed(1)},${iy2.toFixed(1)} A${inner},${inner} 0 ${lg} 0 ${ix1.toFixed(1)},${iy1.toFixed(1)} Z`;
    angle += sweep;
    return { path, color: PIE_COLORS[i % PIE_COLORS.length] };
  });
  return (
    <svg viewBox="0 0 120 120" style={{ width: "100%", maxWidth: 140, display: "block", margin: "0 auto" }}>
      {slices.map((s, i) => <path key={i} d={s.path} fill={s.color} />)}
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize="10" fontWeight="700" fill="#334155" fontFamily="monospace">
        {data.length}
      </text>
    </svg>
  );
}

// ── Widget type components ────────────────────────────────────────────────────

export function ValueCard({ config, liveTelem, historyData, deviceId }) {
  const raw  = liveTelem?.[config.key];
  const num  = typeof raw === "number" ? raw : parseFloat(raw);
  const isN  = !isNaN(num);
  const alert = config.threshold_high && isN && num > config.threshold_high;
  const history = (historyData?.[config.key] || []).slice(-20).map(p => p.value);
  const devId = deviceId || config.device_id;
  const [agg, setAgg] = useState({ avg: null, min: null, max: null });
  const [window, setWindow] = useState("1h");

  useEffect(() => {
    if (!devId || !config.key) return;
    Promise.all([
      telemetryApi.aggregate(devId, config.key, window, "avg"),
      telemetryApi.aggregate(devId, config.key, window, "min"),
      telemetryApi.aggregate(devId, config.key, window, "max"),
    ]).then(([a, mn, mx]) => setAgg({ avg: a?.result ?? null, min: mn?.result ?? null, max: mx?.result ?? null }))
      .catch(() => {});
  }, [devId, config.key, window]);

  const fmt = v => v === null ? "—" : Number(v).toFixed(config.decimals ?? 1);
  const WINDOWS = ["15m","30m","1h","6h","24h"];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 4 }}>
      {/* Window pills */}
      <div style={{ display: "flex", gap: 3, justifyContent: "center", flexShrink: 0 }}>
        {WINDOWS.map(w => (
          <button key={w} onClick={() => setWindow(w)} style={{
            padding: "1px 6px", borderRadius: 20, fontSize: 8, fontWeight: 600, cursor: "pointer",
            border: "1px solid", borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
            background: window === w ? "#2F8CFF" : "#F4F8FF",
            color: window === w ? "white" : "#6B7F9F",
          }}>{w}</button>
        ))}
      </div>
      {/* Current value */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 2 }}>
        <p style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em", color: "#94a3b8" }}>
          {config.label || config.key || "—"}
        </p>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4 }}>
          <span style={{ fontSize: 42, fontWeight: 800, lineHeight: 1, fontFamily: "ui-monospace,monospace",
            color: alert ? "#ef4444" : (config.color || "#1e293b"), transition: "color .3s" }}>
            {isN ? num.toFixed(config.decimals ?? 1) : (raw ?? "—")}
          </span>
          {config.unit && <span style={{ fontSize: 15, color: "#94a3b8", fontWeight: 500, paddingBottom: 5 }}>{config.unit}</span>}
        </div>
        {alert && <span style={{ fontSize: 10, fontWeight: 600, color: "#ef4444", background: "#fef2f2", padding: "2px 8px", borderRadius: 20 }}>⚠ Threshold exceeded</span>}
      </div>
      {/* AVG / MIN / MAX */}
      <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
        {[["AVG", agg.avg, "#2F8CFF"], ["MIN", agg.min, "#10b981"], ["MAX", agg.max, "#f59e0b"]].map(([label, val, color]) => (
          <div key={label} style={{ flex: 1, background: "#F4F8FF", borderRadius: 6, padding: "3px 0",
            display: "flex", flexDirection: "column", alignItems: "center", border: "1px solid #D8E3F3" }}>
            <span style={{ fontSize: 7, fontWeight: 700, color: "#6B7F9F", letterSpacing: ".06em" }}>{label}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color, fontFamily: "monospace" }}>{fmt(val)}</span>
          </div>
        ))}
      </div>
      {history.length > 1 && <Sparkline data={history} color={config.color || "#3b82f6"} height={28} />}
    </div>
  );
}

export function LineChartWidget({ config, historyData, deviceId }) {
  const history = historyData?.[config.key] || [];
  const [window, setWindow] = useState("1h");
  const [aggData, setAggData] = useState({ avg: null, min: null, max: null, count: 0 });
  const [aggLoading, setAggLoading] = useState(false);

  const devId = deviceId || config.device_id;
  const key   = config.key;

  // Fetch all three aggregates when window or key changes
  useEffect(() => {
    if (!devId || !key) return;
    setAggLoading(true);
    Promise.all([
      telemetryApi.aggregate(devId, key, window, "avg"),
      telemetryApi.aggregate(devId, key, window, "min"),
      telemetryApi.aggregate(devId, key, window, "max"),
      telemetryApi.aggregate(devId, key, window, "count"),
    ]).then(([a, mn, mx, ct]) => {
      setAggData({
        avg:   a?.result  ?? null,
        min:   mn?.result ?? null,
        max:   mx?.result ?? null,
        count: ct?.count  ?? 0,
      });
    }).catch(() => {}).finally(() => setAggLoading(false));
  }, [devId, key, window]);

  const fmt = v => v === null ? "—" : Number(v).toFixed(2);
  const WINDOWS = ["1m","5m","15m","30m","1h","6h","12h","24h"];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6 }}>

      {/* Window selector + stats row */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
        {/* Time window pills */}
        <div style={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
          {WINDOWS.map(w => (
            <button key={w} onClick={() => setWindow(w)} style={{
              padding: "2px 7px", borderRadius: 20, fontSize: 9, fontWeight: 600,
              cursor: "pointer", border: "1px solid",
              borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
              background: window === w ? "#2F8CFF" : "#F4F8FF",
              color: window === w ? "white" : "#6B7F9F",
              transition: "all 0.15s",
            }}>{w}</button>
          ))}
        </div>
      </div>

      {/* AVG / MIN / MAX cards */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {[["AVG", aggData.avg, "#2F8CFF"], ["MIN", aggData.min, "#10b981"], ["MAX", aggData.max, "#f59e0b"]].map(([label, val, color]) => (
          <div key={label} style={{
            flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "5px 8px",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
            border: "1px solid #D8E3F3", opacity: aggLoading ? 0.5 : 1,
            transition: "opacity 0.2s",
          }}>
            <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: "0.06em" }}>{label}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color, fontFamily: "monospace" }}>
              {aggLoading ? "…" : fmt(val)}
            </span>
          </div>
        ))}
        <div style={{
          flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "5px 8px",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
          border: "1px solid #D8E3F3", opacity: aggLoading ? 0.5 : 1,
        }}>
          <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: "0.06em" }}>PTS</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#8b5cf6", fontFamily: "monospace" }}>
            {aggLoading ? "…" : aggData.count}
          </span>
        </div>
      </div>

      {/* Chart */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <LineChartSVG data={history} color={config.color || "#3b82f6"} />
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{key}{config.unit ? ` (${config.unit})` : ""}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", flexShrink: 0, display: "inline-block" }} />
          <span style={{ fontSize: 9, color: "#94a3b8" }}>{history.length} pts · LIVE</span>
        </div>
      </div>
    </div>
  );
}

export function GaugeWidget({ config, liveTelem, deviceId }) {
  const raw = liveTelem?.[config.key];
  const num = typeof raw === "number" ? raw : parseFloat(raw);
  const devId = deviceId || config.device_id;
  const [window, setWindow] = useState("24h");
  const [agg, setAgg] = useState({ min: null, max: null, avg: null });

  useEffect(() => {
    if (!devId || !config.key) return;
    Promise.all([
      telemetryApi.aggregate(devId, config.key, window, "min"),
      telemetryApi.aggregate(devId, config.key, window, "max"),
      telemetryApi.aggregate(devId, config.key, window, "avg"),
    ]).then(([mn, mx, av]) => setAgg({ min: mn?.result ?? null, max: mx?.result ?? null, avg: av?.result ?? null }))
      .catch(() => {});
  }, [devId, config.key, window]);

  const fmt = v => v === null ? "—" : Number(v).toFixed(1);
  const WINDOWS = ["1h","6h","24h","7d"];

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", height: "100%", gap: 4 }}>
      {/* Window pills */}
      <div style={{ display: "flex", gap: 3, flexShrink: 0 }}>
        {WINDOWS.map(w => (
          <button key={w} onClick={() => setWindow(w)} style={{
            padding: "1px 7px", borderRadius: 20, fontSize: 8, fontWeight: 600, cursor: "pointer",
            border: "1px solid", borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
            background: window === w ? "#2F8CFF" : "#F4F8FF",
            color: window === w ? "white" : "#6B7F9F",
          }}>{w}</button>
        ))}
      </div>
      {/* Gauge dial */}
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <GaugeSVG value={isNaN(num) ? config.min : num} min={config.min ?? 0} max={config.max ?? 100} color={config.color || "#3b82f6"} />
      </div>
      {/* Label row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", padding: "0 8px", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.min ?? 0}{config.unit}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: config.color || "#3b82f6" }}>{config.label || config.key}</span>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.max ?? 100}{config.unit}</span>
      </div>
      {/* AVG / MIN / MAX for selected window */}
      <div style={{ display: "flex", gap: 4, width: "100%", flexShrink: 0 }}>
        {[["AVG", agg.avg, "#2F8CFF"], ["MIN", agg.min, "#10b981"], ["MAX", agg.max, "#f59e0b"]].map(([label, val, color]) => (
          <div key={label} style={{ flex: 1, background: "#F4F8FF", borderRadius: 6, padding: "3px 0",
            display: "flex", flexDirection: "column", alignItems: "center", border: "1px solid #D8E3F3" }}>
            <span style={{ fontSize: 7, fontWeight: 700, color: "#6B7F9F", letterSpacing: ".06em" }}>{label}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color, fontFamily: "monospace" }}>{fmt(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatusLight({ config, liveTelem, deviceLastSeen }) {
  const OFFLINE_THRESHOLD_MS = 5 * 60 * 1000;

  // ── Mode 1: Key-based (monitors a specific telemetry value) ──────────────
  // When config.key is set, show ON/OFF based on that key's value.
  // e.g. key="led1" → green when led1=1, red when led1=0
  const raw = config.key ? liveTelem?.[config.key] : undefined;
  const hasKey = config.key && config.key !== "";

  if (hasKey) {
    const isOn   = raw === true || raw === 1 || raw === "1" || raw === "true" || raw === "ON";
    const isNull = raw === undefined || raw === null;
    const color  = isNull ? "#f59e0b" : isOn ? (config.color || "#10b981") : "#94a3b8";
    const label  = config.label || config.key;
    const text   = isNull ? "—" : isOn ? "ON" : "OFF";

    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, height: "100%" }}>
        <div style={{
          width: 52, height: 52, borderRadius: "50%", background: color,
          boxShadow: isOn ? `0 0 20px ${color}66` : "none",
          transition: "all .5s",
        }} />
        <div style={{ textAlign: "center" }}>
          <p style={{ fontSize: 15, fontWeight: 700, color }}>{text}</p>
          <p style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>{label}</p>
        </div>
        {raw !== undefined && (
          <p style={{ fontSize: 11, color: "#64748b", fontFamily: "monospace" }}>
            {config.key}: {String(raw)}
          </p>
        )}
      </div>
    );
  }

  // ── Mode 2: Device online/offline (default when no key set) ──────────────
  const status = (() => {
    if (!deviceLastSeen) return "UNKNOWN";
    const age = Date.now() - new Date(deviceLastSeen).getTime();
    return age < OFFLINE_THRESHOLD_MS ? "ONLINE" : "OFFLINE";
  })();

  const COLOR = { ONLINE: "#10b981", OFFLINE: "#94a3b8", UNKNOWN: "#f59e0b" };
  const c = COLOR[status];

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, height: "100%" }}>
      <div style={{
        width: 52, height: 52, borderRadius: "50%", background: c,
        boxShadow: status === "ONLINE" ? `0 0 20px ${c}66` : "none",
        transition: "all .5s",
      }} />
      <div style={{ textAlign: "center" }}>
        <p style={{ fontSize: 15, fontWeight: 700, color: c }}>{status}</p>
        <p style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>{config.label || "Device Status"}</p>
      </div>
      <p style={{ fontSize: 10, color: "#94a3b8" }}>
        {deviceLastSeen
          ? `Last seen: ${new Date(deviceLastSeen).toLocaleTimeString()}`
          : "No data received yet"}
      </p>
    </div>
  );
}

export function BarChartWidget({ config, historyData, deviceId }) {
  const key = config.key || (config.keys || [])[0] || "";
  const history = historyData?.[key] || [];
  const devId = deviceId || config.device_id;
  const [window, setWindow] = useState("1h");
  const [agg, setAgg] = useState({ avg: null, min: null, max: null, count: 0 });
  const [aggLoading, setAggLoading] = useState(false);

  useEffect(() => {
    if (!devId || !key) return;
    setAggLoading(true);
    Promise.all([
      telemetryApi.aggregate(devId, key, window, "avg"),
      telemetryApi.aggregate(devId, key, window, "min"),
      telemetryApi.aggregate(devId, key, window, "max"),
      telemetryApi.aggregate(devId, key, window, "count"),
    ]).then(([a, mn, mx, ct]) => setAgg({ avg: a?.result ?? null, min: mn?.result ?? null, max: mx?.result ?? null, count: ct?.count ?? 0 }))
      .catch(() => {}).finally(() => setAggLoading(false));
  }, [devId, key, window]);

  const fmt = v => v === null ? "—" : Number(v).toFixed(2);
  const WINDOWS = ["1m","5m","15m","30m","1h","6h","12h","24h"];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
      {/* Window selector */}
      <div style={{ display: "flex", gap: 3, flexWrap: "wrap", flexShrink: 0 }}>
        {WINDOWS.map(w => (
          <button key={w} onClick={() => setWindow(w)} style={{
            padding: "2px 7px", borderRadius: 20, fontSize: 9, fontWeight: 600, cursor: "pointer",
            border: "1px solid", borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
            background: window === w ? "#2F8CFF" : "#F4F8FF",
            color: window === w ? "white" : "#6B7F9F",
          }}>{w}</button>
        ))}
      </div>
      {/* Stats */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {[["AVG", agg.avg, "#2F8CFF"], ["MIN", agg.min, "#10b981"], ["MAX", agg.max, "#f59e0b"], ["PTS", agg.count, "#8b5cf6"]].map(([label, val, color]) => (
          <div key={label} style={{ flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "4px 8px",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
            border: "1px solid #D8E3F3", opacity: aggLoading ? 0.5 : 1 }}>
            <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: ".06em" }}>{label}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color, fontFamily: "monospace" }}>{aggLoading ? "…" : (label === "PTS" ? val : fmt(val))}</span>
          </div>
        ))}
      </div>
      {/* Chart */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <BarChartSVG data={history} color={config.color || "#3b82f6"} />
      </div>
      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{key}{config.unit ? ` (${config.unit})` : ""}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", flexShrink: 0, display: "inline-block" }} />
          <span style={{ fontSize: 9, color: "#94a3b8" }}>{history.length} pts · LIVE</span>
        </div>
      </div>
    </div>
  );
}

export function AlarmListWidget({ alarms = [] }) {
  const SEV_COLOR = { CRITICAL: "#ef4444", MAJOR: "#f97316", WARNING: "#f59e0b", MINOR: "#eab308" };
  const active = alarms.filter(a => a.status?.startsWith("ACTIVE")).slice(0, 6);
  if (!active.length) return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8, color: "#94a3b8" }}>
      <svg style={{ width: 22, height: 22 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
      </svg>
      <p style={{ fontSize: 12 }}>No active alarms</p>
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, height: "100%", overflowY: "auto" }}>
      {active.map(a => (
        <div key={a.id} style={{
          padding: "6px 10px", borderRadius: 8, background: "#fafafa",
          borderLeft: `3px solid ${SEV_COLOR[a.severity] || "#94a3b8"}`,
          border: `1px solid #f1f5f9`,
        }}>
          <p style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>{a.alarm_type}</p>
          <p style={{ fontSize: 9, color: "#94a3b8", marginTop: 2 }}>
            {a.severity} · {new Date(a.start_ts).toLocaleTimeString()}
          </p>
        </div>
      ))}
    </div>
  );
}

export function TimeseriesTable({ config, historyData, deviceId }) {
  const history = [...(historyData?.[config.key] || [])].reverse().slice(0, 25);
  const devId = deviceId || config.device_id;
  const [window, setWindow] = useState("1h");
  const [agg, setAgg] = useState({ avg: null, min: null, max: null, count: 0 });

  useEffect(() => {
    if (!devId || !config.key) return;
    Promise.all([
      telemetryApi.aggregate(devId, config.key, window, "avg"),
      telemetryApi.aggregate(devId, config.key, window, "min"),
      telemetryApi.aggregate(devId, config.key, window, "max"),
      telemetryApi.aggregate(devId, config.key, window, "count"),
    ]).then(([a, mn, mx, ct]) => setAgg({ avg: a?.result ?? null, min: mn?.result ?? null, max: mx?.result ?? null, count: ct?.count ?? 0 }))
      .catch(() => {});
  }, [devId, config.key, window]);

  const fmt = v => v === null ? "—" : Number(v).toFixed(config.decimals ?? 2);
  const WINDOWS = ["15m","30m","1h","6h","24h"];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
      {/* Window pills */}
      <div style={{ display: "flex", gap: 3, flexShrink: 0 }}>
        {WINDOWS.map(w => (
          <button key={w} onClick={() => setWindow(w)} style={{
            padding: "1px 7px", borderRadius: 20, fontSize: 8, fontWeight: 600, cursor: "pointer",
            border: "1px solid", borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
            background: window === w ? "#2F8CFF" : "#F4F8FF",
            color: window === w ? "white" : "#6B7F9F",
          }}>{w}</button>
        ))}
      </div>
      {/* Table */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse" }}>
          <thead style={{ position: "sticky", top: 0, background: "white" }}>
            <tr style={{ borderBottom: "1px solid #f1f5f9" }}>
              <th style={{ textAlign: "left", padding: "4px 0", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: ".06em" }}>Time</th>
              <th style={{ textAlign: "right", padding: "4px 0", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: ".06em" }}>{config.key}</th>
            </tr>
          </thead>
          <tbody>
            {history.map((p, i) => (
              <tr key={i} style={{ borderBottom: "1px solid #f8fafc" }}>
                <td style={{ padding: "3px 0", color: "#94a3b8", fontFamily: "monospace" }}>{new Date(p.ts).toLocaleTimeString()}</td>
                <td style={{ padding: "3px 0", textAlign: "right", color: "#1e293b", fontFamily: "monospace", fontWeight: 600 }}>
                  {typeof p.value === "number" ? p.value.toFixed(config.decimals ?? 2) : String(p.value)}{config.unit}
                </td>
              </tr>
            ))}
            {!history.length && (
              <tr><td colSpan={2} style={{ padding: "16px 0", textAlign: "center", color: "#94a3b8" }}>No history yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {/* Summary footer */}
      <div style={{ display: "flex", gap: 4, borderTop: "1px solid #D8E3F3", paddingTop: 6, flexShrink: 0 }}>
        {[["AVG", agg.avg, "#2F8CFF"], ["MIN", agg.min, "#10b981"], ["MAX", agg.max, "#f59e0b"], ["PTS", agg.count, "#8b5cf6"]].map(([label, val, color]) => (
          <div key={label} style={{ flex: 1, background: "#F4F8FF", borderRadius: 6, padding: "3px 0",
            display: "flex", flexDirection: "column", alignItems: "center", border: "1px solid #D8E3F3" }}>
            <span style={{ fontSize: 7, fontWeight: 700, color: "#6B7F9F", letterSpacing: ".06em" }}>{label}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color, fontFamily: "monospace" }}>{label === "PTS" ? val : fmt(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function PieChartWidget({ config, liveTelem }) {
  // Use config.keys if any match liveTelem; otherwise fall back to all available numeric keys
  const configuredKeys = (config.keys || []).filter(k => liveTelem?.[k] !== undefined);
  const fallbackKeys   = configuredKeys.length ? configuredKeys
    : Object.keys(liveTelem || {}).filter(k => !isNaN(parseFloat(liveTelem[k]))).slice(0, 8);
  const keys = fallbackKeys;
  const data = keys.map(k => ({ key: k, value: Math.abs(parseFloat(liveTelem[k])) || 0 }));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, height: "100%" }}>
      <PieChartSVG data={data} />
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {data.map((d, i) => (
          <div key={d.key} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: PIE_COLORS[i % PIE_COLORS.length], flexShrink: 0 }} />
            <span style={{ fontSize: 10, color: "#64748b" }}>
              {d.key}: <strong style={{ fontFamily: "monospace", color: "#1e293b" }}>{d.value.toFixed(1)}</strong>
            </span>
          </div>
        ))}
        {!data.length && <p style={{ fontSize: 10, color: "#94a3b8" }}>No keys configured</p>}
      </div>
    </div>
  );
}

export function MarkdownWidget({ config }) {
  const html = (config.content || "_Empty — click Edit_")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, `<code style="background:#f1f5f9;padding:1px 4px;border-radius:3px;font-family:monospace;font-size:11px">$1</code>`)
    .replace(/\n/g, "<br/>");
  return (
    <div
      style={{ height: "100%", overflowY: "auto", fontSize: 13, lineHeight: 1.6, color: "#334155" }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function EntityTable({ liveTelem }) {
  const entries = Object.entries(liveTelem || {});
  return (
    <div style={{ height: "100%", overflowY: "auto" }}>
      <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
        <thead><tr style={{ borderBottom: "1px solid #f1f5f9" }}>
          <th style={{ textAlign: "left", padding: "4px 0", fontWeight: 600, color: "#94a3b8" }}>Key</th>
          <th style={{ textAlign: "right", padding: "4px 0", fontWeight: 600, color: "#94a3b8" }}>Value</th>
        </tr></thead>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} style={{ borderBottom: "1px solid #f8fafc" }}>
              <td style={{ padding: "4px 0", color: "#475569" }}>{k}</td>
              <td style={{ padding: "4px 0", textAlign: "right", fontFamily: "monospace", fontWeight: 600, color: "#1e293b" }}>
                {typeof v === "number" ? v.toFixed(2) : String(v)}
              </td>
            </tr>
          ))}
          {!entries.length && (
            <tr><td colSpan={2} style={{ padding: "16px 0", textAlign: "center", color: "#94a3b8" }}>No telemetry received</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function HtmlCard({ config, liveTelem }) {
  const rendered = (config.content || "<p>Configure HTML template in Edit</p>")
    .replace(/\$\{([^}]+)\}/g, (_, k) => {
      const v = liveTelem?.[k.trim()];
      if (v === undefined) return `\${${k}}`;
      return typeof v === "number" ? v.toFixed(config.decimals ?? 1) : String(v);
    });
  return (
    <div
      style={{ height: "100%", overflowY: "auto", fontSize: 13, color: "#334155" }}
      dangerouslySetInnerHTML={{ __html: rendered }}
    />
  );
}

// ── Master registry + dispatcher ──────────────────────────────────────────────

export const WIDGET_REGISTRY = [
  // ── Data widgets ─────────────────────────────────────────────────────────
  { id: "value_card",        label: "Value Card",        icon: "M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8",             desc: "Large number + sparkline",         category: "data" },
  { id: "line_chart",        label: "Line Chart",        icon: "M22 12h-4l-3 9L9 3l-3 9H2",                                            desc: "Single-key time-series",           category: "data" },
  { id: "multi_axis_chart",  label: "Multi-axis Chart",  icon: "M3 3v18h18M7 16l4-8 4 4 4-6",                                          desc: "Multiple keys on one chart",       category: "data" },
  { id: "bar_chart",         label: "Bar Chart",         icon: "M12 20V10M6 20V4M18 20v-4",                                            desc: "Bar chart over time",              category: "data" },
  { id: "gauge",             label: "Gauge",             icon: "M12 22a10 10 0 0 0 7.07-17.07M5 19.07A10 10 0 0 1 12 2",               desc: "Circular gauge with min/max",      category: "data" },
  { id: "pie_chart",         label: "Pie / Donut",       icon: "M21.21 15.89A10 10 0 1 1 8 2.83",                                      desc: "Distribution across keys",         category: "data" },
  { id: "timeseries_table",  label: "History Table",     icon: "M3 10h18M3 6h18M3 14h18M3 18h18",                                      desc: "Raw telemetry rows",               category: "data" },
  { id: "entity_table",      label: "Entity Table",      icon: "M4 6h16M4 10h16M4 14h16M4 18h16",                                      desc: "All latest key-value pairs",       category: "data" },
  // ── Status widgets ───────────────────────────────────────────────────────
  { id: "status_light",      label: "Status Light",      icon: "M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20z",                            desc: "Online / offline indicator",       category: "status" },
  { id: "device_summary",    label: "Device Summary",    icon: "M5 12h14M12 5l7 7-7 7",                                                desc: "Last seen, status + key metrics",  category: "status" },
  { id: "alarm_list",        label: "Alarm List",        icon: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0", desc: "Active alarms for device",        category: "status" },
  { id: "map",               label: "Map (GPS)",         icon: "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z", desc: "Device location from lat/lng",    category: "status" },
  { id: "fleet_map",         label: "Fleet Map",         icon: "M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7", desc: "All devices on one map",           category: "status" },
  { id: "trend_indicator",   label: "Trend Indicator",   icon: "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6",                                                                                                                                                        desc: "Rising/falling/stable trend",     category: "data"   },
  // ── Control widgets ──────────────────────────────────────────────────────
  { id: "rpc_button",        label: "RPC Button",        icon: "M13 10V3L4 14h7v7l9-11h-7z",                                           desc: "Send command on click",            category: "control" },
  { id: "rpc_toggle",        label: "RPC Toggle",        icon: "M18.36 6.64A9 9 0 1 1 5.64 17.36",                                     desc: "ON/OFF toggle command",            category: "control" },
  { id: "rpc_input",         label: "RPC Input",         icon: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z", desc: "Send value to device (setpoint override)", category: "control" },
  // ── Content widgets ──────────────────────────────────────────────────────
  { id: "markdown",          label: "Text / Markdown",   icon: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7",          desc: "Free-text notes",                  category: "content" },
  { id: "html_card",         label: "HTML Card",         icon: "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71",         desc: "Custom HTML with \${key} values",  category: "content" },
];


// ── Phase 4: Pluggable Widget Component Registry ─────────────────────────────
//
// To add a new widget type:
//   1. Create your component function in this file (or import it)
//   2. Add one entry here: "your_type": YourComponent
//   3. Add to WIDGET_REGISTRY array (for the Add Widget picker)
//   4. Add to VALID_WIDGET_TYPES in dashboard_service.py
//   5. Add to WIDGET_CONFIG_SCHEMAS in schemas.py
//
// Nothing else changes. No switch-case to update.
//
export const WIDGET_COMPONENT_MAP = {
  // ── Data ──────────────────────────────────────────────────────────────────
  value_card:        ValueCard,
  line_chart:        LineChartWidget,
  multi_axis_chart:  MultiAxisChartWidget,
  gauge:             GaugeWidget,
  bar_chart:         BarChartWidget,
  timeseries_table:  TimeseriesTable,
  pie_chart:         PieChartWidget,
  entity_table:      EntityTable,
  // ── Status ─────────────────────────────────────────────────────────────────
  status_light:      StatusLight,
  device_summary:    DeviceSummaryWidget,
  alarm_list:        AlarmListWidget,
  map:               MapWidget,
  fleet_map:         FleetMapWidget,
  trend_indicator:   TrendIndicatorWidget,
  // ── Control ────────────────────────────────────────────────────────────────
  rpc_button:        RpcButtonWidget,
  rpc_toggle:        RpcToggleWidget,
  rpc_input:         RpcInputWidget,
  // ── Content ────────────────────────────────────────────────────────────────
  markdown:          MarkdownWidget,
  html_card:         HtmlCard,
};

/**
 * WidgetRenderer — dispatches to the correct component based on widget.widget_type
 */
/**
 * WidgetRenderer
 *
 * Dispatches to the correct widget component.
 *
 * For UserDashboard widgets the caller (UserDashboardPage) pre-slices
 * liveTelem and historyData to only the relevant device:
 *   liveTelem  = liveTelem[widget.config.device_id]  || {}
 *   historyData = historyData[widget.config.device_id] || {}
 *
 * The `missingDevice` flag is set when a widget has no config.device_id
 * (backward compat for widgets saved before Critical Fix 1).
 * These widgets show a warning prompt instead of empty/broken UI.
 *
 * DashboardPage (device-scoped) passes the device-level liveTelem/historyData
 * directly — no change needed there.
 */
export function WidgetRenderer({ widget, liveTelem, historyData, alarms, missingDevice = false, deviceLastSeen = null, userRole = "TENANT_ADMIN", deviceId = null, allDevices = [], currentDevice = null }) {
  // Backward-compat: old widgets that have no device_id show a non-crashing prompt
  if (missingDevice) {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", height: "100%", gap: 8, padding: 12,
        textAlign: "center",
      }}>
        <svg style={{ width: 20, height: 20, color: "#f59e0b" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
        <p style={{ fontSize: 12, color: "#92400e", margin: 0, lineHeight: 1.4 }}>
          No device linked.<br/>
          <span style={{ color: "#64748b" }}>Click <strong>Edit</strong> and select a device.</span>
        </p>
      </div>
    );
  }

  // For map widget: find device coords from currentDevice or allDevices
  const _mapDevice = currentDevice || allDevices?.find(d => String(d.id) === String(widget.config?.device_id || deviceId));
  const effectiveConfig = widget.widget_type === "fleet_map"
    ? { ...widget.config, devices: allDevices }
    : widget.widget_type === "map"
    ? {
        ...widget.config,
        fixed_lat: widget.config?.fixed_lat ?? _mapDevice?.latitude,
        fixed_lng: widget.config?.fixed_lng ?? _mapDevice?.longitude,
      }
    : widget.config || {};
  const props = { config: effectiveConfig, liveTelem, historyData, alarms, deviceId: widget.config?.device_id || deviceId, deviceLastSeen };

  // ── Role-based widget access control ─────────────────────────────────────
  // Matches the access table exactly:
  //
  //  Widget                  ADMIN  TENANT_USER  CUSTOMER_USER
  //  ──────────────────────  ─────  ───────────  ─────────────
  //  rpc_button / rpc_toggle  ✅       ❌            ❌
  //  multi_axis_chart         ✅       ✅            ❌
  //  bar_chart                ✅       ✅            ❌
  //  timeseries_table         ✅       ✅            ❌
  //  entity_table             ✅       ✅            ❌
  //  pie_chart                ✅       ✅            ❌
  //  everything else          ✅       ✅            ✅

  const wtype = widget.widget_type;

  // RPC = TENANT_ADMIN only
  const isRpc = wtype === "rpc_button" || wtype === "rpc_toggle" || wtype === "rpc_input";
  if (isRpc && userRole !== "TENANT_ADMIN") {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", height: "100%", gap: 8, padding: 12,
        textAlign: "center",
      }}>
        <svg style={{ width: 20, height: 20, color: "#94a3b8" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <p style={{ fontSize: 11, color: "#94a3b8", margin: 0, lineHeight: 1.4 }}>
          Device control is<br/>restricted to admins
        </p>
      </div>
    );
  }

  // Technical widgets = hidden from CUSTOMER_USER only (TENANT_USER can see them)
  const customerHidden = new Set([
    "multi_axis_chart", "bar_chart", "timeseries_table", "entity_table", "pie_chart",
  ]);
  if (customerHidden.has(wtype) && userRole === "CUSTOMER_USER") {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", height: "100%", gap: 8, padding: 12,
        textAlign: "center",
      }}>
        <svg style={{ width: 20, height: 20, color: "#cbd5e1" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
        </svg>
        <p style={{ fontSize: 11, color: "#94a3b8", margin: 0 }}>Not available</p>
      </div>
    );
  }

  // Phase 4: pluggable registry lookup — O(1), no switch-case.
  // To add a new widget: import it and add one entry to WIDGET_COMPONENT_MAP.
  // No changes needed here.
  const WidgetComponent = WIDGET_COMPONENT_MAP[wtype];
  if (!WidgetComponent) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: 12, color: "#94a3b8" }}>
        Unknown widget type: {wtype}
      </div>
    );
  }
  return <WidgetComponent {...props} />;
}



// ── Phase 3: RPC Input Widget ─────────────────────────────────────────────────
// Operator types a value and hits Send — used to override device setpoints,
// thresholds, modes, or any numeric/string parameter dynamically.

export function RpcInputWidget({ config, liveTelem, deviceId }) {
  const method    = config.method    || "setValue";
  const paramKey  = config.param_key || "value";
  const label     = config.label     || paramKey;
  const inputType = config.input_type || "number";  // "number" | "text"
  const unit      = config.unit      || "";
  const currentRaw = liveTelem?.[config.key];
  const currentVal = currentRaw !== undefined
    ? (typeof currentRaw === "number" ? currentRaw.toFixed(config.decimals ?? 1) : String(currentRaw))
    : null;

  const [value,   setValue]   = useState("");
  const [state,   setState]   = useState("idle"); // idle | sending | done | error
  const [errMsg,  setErrMsg]  = useState("");

  if (!deviceId || !method) return (
    <div style={{ display:"flex",alignItems:"center",justifyContent:"center",height:"100%",fontSize:12,color:"#94a3b8" }}>
      Configure method in Edit
    </div>
  );

  const send = async () => {
    if (state === "sending" || !value.toString().trim()) return;
    setState("sending"); setErrMsg("");
    try {
      const token = localStorage.getItem("access_token");
      const BASE  = (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL) || "";
      const parsed = inputType === "number" ? parseFloat(value) : value;
      if (inputType === "number" && isNaN(parsed)) {
        setErrMsg("Enter a valid number"); setState("idle"); return;
      }
      const res = await fetch(`${BASE}/api/v1/rpc/${deviceId}`, {
        method: "POST",
        headers: { "Content-Type":"application/json", "Authorization":`Bearer ${token}` },
        body: JSON.stringify({ method, params: { [paramKey]: parsed } }),
      });
      if (!res.ok) throw new Error(await res.text());
      setState("done");
      setTimeout(() => setState("idle"), 2000);
    } catch (e) {
      setErrMsg("Send failed");
      setState("error");
      setTimeout(() => setState("idle"), 2500);
    }
  };

  const onKey = e => { if (e.key === "Enter") send(); };

  const BTN_BG = { idle:"#2F8CFF", sending:"#94a3b8", done:"#10b981", error:"#ef4444" }[state];
  const BTN_LBL = { idle:"Send", sending:"Sending…", done:"Sent ✓", error:"Error" }[state];

  return (
    <div style={{ display:"flex",flexDirection:"column",justifyContent:"center",height:"100%",gap:10,padding:"4px 2px" }}>
      {/* Current value display */}
      {config.key && (
        <div style={{ display:"flex",justifyContent:"space-between",alignItems:"baseline" }}>
          <span style={{ fontSize:11,color:"#94a3b8" }}>Current {label}</span>
          <span style={{ fontSize:15,fontWeight:700,color:"#1e293b",fontFamily:"monospace" }}>
            {currentVal !== null ? `${currentVal}${unit ? " "+unit : ""}` : "—"}
          </span>
        </div>
      )}

      {/* Input + Send */}
      <div style={{ display:"flex",gap:8,alignItems:"stretch" }}>
        <div style={{ flex:1,position:"relative" }}>
          <input
            type={inputType}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={onKey}
            placeholder={`New ${label}${unit ? " ("+unit+")" : ""}`}
            style={{
              width:"100%", boxSizing:"border-box",
              padding:"8px 12px", borderRadius:8,
              border:`1.5px solid ${errMsg ? "#ef4444" : "#e2e8f0"}`,
              fontSize:13, outline:"none", fontFamily:"monospace",
              background:"#f8fafc",
            }}
          />
        </div>
        <button
          onClick={send}
          disabled={state === "sending" || !value.toString().trim()}
          style={{
            padding:"8px 16px", borderRadius:8, border:"none",
            background: BTN_BG, color:"white",
            fontSize:13, fontWeight:600, cursor: state==="sending"?"wait":"pointer",
            transition:"background .3s", whiteSpace:"nowrap",
            opacity: !value.toString().trim() ? 0.5 : 1,
          }}
        >
          {BTN_LBL}
        </button>
      </div>

      {errMsg && <p style={{ fontSize:11,color:"#ef4444",margin:0 }}>{errMsg}</p>}

      {/* Method hint */}
      <p style={{ fontSize:10,color:"#cbd5e1",margin:0 }}>
        method: <span style={{ fontFamily:"monospace" }}>{method}</span>
        {" · param: "}<span style={{ fontFamily:"monospace" }}>{paramKey}</span>
      </p>
    </div>
  );
}

// ── Phase 3: Multi-axis Chart ─────────────────────────────────────────────────
// Shows multiple telemetry keys on one chart with a shared time axis.
// Each key gets its own colour and Y-axis label.
const MULTI_COLORS = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4"];

export function MultiAxisChartWidget({ config, historyData, deviceId }) {
  const keys = config.keys || [];
  if (!keys.length) return (
    <div style={{ display:"flex",alignItems:"center",justifyContent:"center",height:"100%",fontSize:12,color:"#94a3b8" }}>
      Configure keys in Edit
    </div>
  );
  const W=460, H=170, pad={t:8,r:48,b:28,l:44};
  const w=W-pad.l-pad.r, h=H-pad.t-pad.b;

  // Build series with individual min/max for true multi-axis
  const series = keys.map((k,i)=>{
    const pts = (historyData?.[k]||[]).map(p=>({ts:p.ts, value:typeof p.value==="number"?p.value:parseFloat(p.value)||0}));
    const vals = pts.map(p=>p.value);
    const mn = vals.length ? Math.min(...vals) : 0;
    const mx = vals.length ? Math.max(...vals) : 1;
    return { key:k, pts, color: config.colors?.[i]||MULTI_COLORS[i%MULTI_COLORS.length], mn, mx, rng: mx-mn||1 };
  }).filter(s=>s.pts.length>1);

  if (!series.length) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:6}}>
      <p style={{fontSize:11,color:"#94a3b8"}}>Waiting for data…</p>
    </div>
  );

  // Shared X domain
  const allTs = series.flatMap(s=>s.pts.map(p=>new Date(p.ts).getTime()));
  const minTs = Math.min(...allTs), maxTs = Math.max(...allTs);
  const px = ts => pad.l + ((new Date(ts).getTime()-minTs)/(maxTs-minTs||1))*w;

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",gap:4}}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{width:"100%",flex:1}}>
        {/* Grid lines */}
        {[0,0.25,0.5,0.75,1].map(t=>(
          <line key={t} x1={pad.l} y1={pad.t+h*t} x2={pad.l+w} y2={pad.t+h*t} stroke="#f1f5f9" strokeWidth="1"/>
        ))}
        {/* Y axis labels — left for series[0], right for series[1] */}
        {series.slice(0,2).map((s,si)=>(
          [0,0.5,1].map(t=>{
            const val = s.mn + (1-t)*s.rng;
            const x = si===0 ? pad.l-4 : pad.l+w+4;
            const anchor = si===0 ? "end" : "start";
            return (
              <text key={`${si}-${t}`} x={x} y={pad.t+h*t+4} fontSize="7" fill={s.color} textAnchor={anchor} fontFamily="monospace">
                {val>=1000?`${(val/1000).toFixed(1)}k`:val>=100?val.toFixed(0):val.toFixed(1)}
              </text>
            );
          })
        ))}
        {/* Each series line */}
        {series.map(s=>{
          const py = v => pad.t+h-((v-s.mn)/s.rng)*h;
          const d = s.pts.map((p,i)=>`${i===0?"M":"L"}${px(p.ts).toFixed(1)},${py(p.value).toFixed(1)}`).join(" ");
          // Fill area under line
          const first = s.pts[0], last = s.pts[s.pts.length-1];
          const area = `M${px(first.ts).toFixed(1)},${pad.t+h} ${d.slice(1)} L${px(last.ts).toFixed(1)},${pad.t+h} Z`;
          return (
            <g key={s.key}>
              <path d={area} fill={s.color} fillOpacity="0.06"/>
              <path d={d} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
            </g>
          );
        })}
        {/* Time labels */}
        {series[0].pts.filter((_,i,a)=>i===0||i===Math.floor(a.length/2)||i===a.length-1).map((p,i)=>(
          <text key={i} x={px(p.ts)} y={pad.t+h+18} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">
            {new Date(p.ts).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}
          </text>
        ))}
      </svg>
      {/* Legend */}
      <div style={{display:"flex",gap:12,flexWrap:"wrap",paddingLeft:pad.l}}>
        {series.map(s=>(
          <div key={s.key} style={{display:"flex",alignItems:"center",gap:4}}>
            <div style={{width:12,height:3,borderRadius:2,background:s.color}}/>
            <span style={{fontSize:10,color:"#64748b"}}>{s.key}</span>
            <span style={{fontSize:9,color:"#94a3b8"}}>({s.mn.toFixed(1)}–{s.mx.toFixed(1)})</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Trend Indicator Widget ────────────────────────────────────────────────────
// Shows trend direction + confidence for a telemetry key.
// Calls GET /api/v1/intelligence/trend/{deviceId}/{key}

const TREND_CONFIG = {
  RISING:   { icon: "M5 15l7-7 7 7",          color: "#ef4444", label: "Rising",   bg: "#fef2f2" },
  FALLING:  { icon: "M19 9l-7 7-7-7",         color: "#3b82f6", label: "Falling",  bg: "#eff6ff" },
  STABLE:   { icon: "M5 12h14",               color: "#10b981", label: "Stable",   bg: "#f0fdf4" },
  SPIKE:    { icon: "M12 2l3 7h7l-5.5 4 2 7L12 16l-6.5 4 2-7L2 9h7z", color: "#f59e0b", label: "Spike",   bg: "#fffbeb" },
  DROP:     { icon: "M12 22l3-7h7l-5.5-4 2-7L12 8l-6.5-4 2 7L2 15h7z", color: "#8b5cf6", label: "Drop",    bg: "#f5f3ff" },
  VOLATILE: { icon: "M2 12l4-4 4 8 4-8 4 4", color: "#f97316", label: "Volatile", bg: "#fff7ed" },
  UNKNOWN:  { icon: "M12 8v4m0 4h.01",        color: "#94a3b8", label: "No data",  bg: "#f8fafc" },
};

export function TrendIndicatorWidget({ config, deviceId, liveTelem }) {
  const key      = config.key || "";
  const label    = config.label || key || "Trend";
  const BASE     = (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL) || "";
  const [trend, setTrend]     = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchTrend = useCallback(async () => {
    if (!deviceId || !key) return;
    setLoading(true);
    try {
      const token = localStorage.getItem("access_token");
      const res = await fetch(`${BASE}/api/v1/intelligence/trend/${deviceId}/${key}?minutes=30`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setTrend(await res.json());
    } catch {}
    finally { setLoading(false); }
  }, [deviceId, key]);

  useEffect(() => { fetchTrend(); }, [fetchTrend]);

  // Refresh when live telemetry updates (new reading arrived)
  const latestVal = liveTelem?.[key];
  const prevVal   = useRef(latestVal);
  useEffect(() => {
    if (latestVal !== prevVal.current) {
      prevVal.current = latestVal;
      fetchTrend();
    }
  }, [latestVal]);

  if (!deviceId || !key) return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%",fontSize:11,color:"#94a3b8"}}>
      Configure key in Edit
    </div>
  );

  const trendKey = trend?.trend || "UNKNOWN";
  const cfg      = TREND_CONFIG[trendKey] || TREND_CONFIG.UNKNOWN;
  const conf     = trend ? Math.round((trend.confidence || 0) * 100) : 0;
  const changePct = trend?.change_pct || 0;
  const currentVal = liveTelem?.[key];

  return (
    <div style={{height:"100%", display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", gap:8, background:cfg.bg, borderRadius:8, padding:12}}>
      {/* Trend arrow */}
      <div style={{width:52, height:52, borderRadius:"50%", background:"white", display:"flex", alignItems:"center", justifyContent:"center", boxShadow:`0 2px 8px ${cfg.color}33`}}>
        {loading ? (
          <div style={{width:20, height:20, border:`2px solid ${cfg.color}`, borderTopColor:"transparent", borderRadius:"50%", animation:"spin 1s linear infinite"}}/>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{width:24, height:24}}>
            <path d={cfg.icon}/>
          </svg>
        )}
      </div>

      {/* Trend label */}
      <div style={{textAlign:"center"}}>
        <p style={{fontSize:15, fontWeight:700, color:cfg.color, margin:0}}>{cfg.label}</p>
        <p style={{fontSize:10, color:"#64748b", margin:"2px 0 0"}}>{label}</p>
      </div>

      {/* Stats row */}
      {trend && (
        <div style={{display:"flex", gap:12, fontSize:10}}>
          <div style={{textAlign:"center"}}>
            <p style={{color:"#94a3b8", margin:0}}>Change</p>
            <p style={{fontWeight:600, color:changePct>0?"#ef4444":changePct<0?"#3b82f6":"#10b981", margin:0}}>
              {changePct > 0 ? "+" : ""}{changePct.toFixed(1)}%
            </p>
          </div>
          <div style={{textAlign:"center"}}>
            <p style={{color:"#94a3b8", margin:0}}>Confidence</p>
            <p style={{fontWeight:600, color:"#64748b", margin:0}}>{conf}%</p>
          </div>
          {currentVal !== undefined && (
            <div style={{textAlign:"center"}}>
              <p style={{color:"#94a3b8", margin:0}}>Current</p>
              <p style={{fontWeight:600, color:"#0B1426", margin:0}}>
                {typeof currentVal === "number" ? currentVal.toFixed(1) : currentVal}
              </p>
            </div>
          )}
        </div>
      )}

      <p style={{fontSize:9, color:"#cbd5e1", margin:0}}>Last 30 min · {trend?.points || 0} pts</p>
    </div>
  );
}


// ── Fleet Map Widget ──────────────────────────────────────────────────────────
// Shows ALL devices with lat/lng set as pins on a single map.
// Reads device list from config.devices (passed by DashboardPage/UserDashboardPage).
// Uses OpenStreetMap static tiles — no API key needed.

export function FleetMapWidget({ config }) {
  const devices = (config.devices || []).filter(d => d.latitude && d.longitude);

  if (!devices.length) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:8}}>
      <svg style={{width:28,height:28,color:"#e2e8f0"}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"/>
      </svg>
      <p style={{fontSize:11,color:"#94a3b8",textAlign:"center"}}>
        No devices with location set.<br/>
        <span style={{fontSize:10,color:"#cbd5e1"}}>Edit a device → set Latitude & Longitude</span>
      </p>
    </div>
  );

  // Calculate bounding box for all device locations
  const lats = devices.map(d => d.latitude);
  const lngs = devices.map(d => d.longitude);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const centerLat = (minLat + maxLat) / 2;
  const centerLng = (minLng + maxLng) / 2;
  const zoom = config.zoom || 12;

  // Convert lat/lng to pixel position in SVG viewport
  const W = 460, H = 200;
  const latToY = lat => H/2 - ((lat - centerLat) / (maxLat - minLat + 0.01)) * (H * 0.7);
  const lngToX = lng => W/2 + ((lng - centerLng) / (maxLng - minLng + 0.01)) * (W * 0.7);

  const STATUS_COLOR = { ACTIVE:"#10b981", INACTIVE:"#94a3b8", DISABLED:"#ef4444" };

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",gap:4}}>
      {/* Map background */}
      <div style={{flex:1,background:"linear-gradient(135deg,#dbeafe 0%,#bfdbfe 40%,#e0f2fe 100%)",borderRadius:8,position:"relative",overflow:"hidden"}}>
        {/* Grid lines to simulate map tiles */}
        <svg style={{position:"absolute",inset:0,width:"100%",height:"100%"}} viewBox={`0 0 ${W} ${H}`}>
          {/* Background grid */}
          {Array.from({length:8},(_,i)=>(
            <line key={`h${i}`} x1={0} y1={H/8*i} x2={W} y2={H/8*i} stroke="rgba(255,255,255,0.4)" strokeWidth="1"/>
          ))}
          {Array.from({length:12},(_,i)=>(
            <line key={`v${i}`} x1={W/12*i} y1={0} x2={W/12*i} y2={H} stroke="rgba(255,255,255,0.4)" strokeWidth="1"/>
          ))}
          {/* Device pins */}
          {devices.map((d,i)=>{
            const x = devices.length === 1 ? W/2 : lngToX(d.longitude);
            const y = devices.length === 1 ? H/2 : latToY(d.latitude);
            const color = STATUS_COLOR[d.status] || "#94a3b8";
            return (
              <g key={d.id}>
                {/* Pin shadow */}
                <circle cx={x} cy={y+2} r={8} fill="rgba(0,0,0,0.15)"/>
                {/* Pin circle */}
                <circle cx={x} cy={y} r={8} fill={color} stroke="white" strokeWidth="2"/>
                {/* Device initial */}
                <text x={x} y={y+4} fontSize="7" fill="white" textAnchor="middle" fontWeight="bold">
                  {d.name.charAt(0).toUpperCase()}
                </text>
                {/* Device name label */}
                <rect x={x-30} y={y-26} width={60} height={14} rx={3} fill="rgba(11,20,38,0.75)"/>
                <text x={x} y={y-15} fontSize="7" fill="white" textAnchor="middle">{d.name.slice(0,12)}</text>
              </g>
            );
          })}
        </svg>
        {/* OSM attribution */}
        <div style={{position:"absolute",bottom:4,right:6,fontSize:8,color:"rgba(0,0,0,0.4)"}}>© OpenStreetMap</div>
      </div>
      {/* Device list */}
      <div style={{display:"flex",gap:8,flexWrap:"wrap",paddingLeft:4}}>
        {devices.map(d=>(
          <div key={d.id} style={{display:"flex",alignItems:"center",gap:4}}>
            <div style={{width:8,height:8,borderRadius:"50%",background:STATUS_COLOR[d.status]||"#94a3b8",flexShrink:0}}/>
            <span style={{fontSize:10,color:"#64748b"}}>{d.name}</span>
            <span style={{fontSize:9,color:"#cbd5e1"}}>({d.latitude?.toFixed(4)}, {d.longitude?.toFixed(4)})</span>
          </div>
        ))}
      </div>
    </div>
  );
}


// ── Phase 3: Map Widget ───────────────────────────────────────────────────────
// Displays device GPS location using lat/lng telemetry keys.
// Uses OpenStreetMap tile URL as an img src — no JS map library needed.

export function MapWidget({ config, liveTelem, deviceId }) {
  const latKey = config.lat_key || "latitude";
  const lngKey = config.lng_key || "longitude";
  const [deviceCoords, setDeviceCoords] = useState({ lat: null, lng: null });

  // Fetch device coords directly if not already in config
  useEffect(() => {
    const id = config.device_id || deviceId;
    if (id && config.fixed_lat == null && config.fixed_lng == null) {
      fetch(`${API_BASE}/devices/${id}`, {
        headers: { "Authorization": `Bearer ${localStorage.getItem("access_token")}` }
      })
      .then(r => r.json())
      .then(d => {
        if (d.latitude != null && d.longitude != null) {
          setDeviceCoords({ lat: d.latitude, lng: d.longitude });
        }
      })
      .catch(() => {});
    }
  }, [deviceId, config.device_id]);

  // Priority: 1) live telemetry, 2) config fixed coords, 3) fetched device coords
  const liveLat = parseFloat(liveTelem?.[latKey]);
  const liveLng = parseFloat(liveTelem?.[lngKey]);
  const fixedLat = parseFloat(config.fixed_lat ?? config.latitude ?? deviceCoords.lat);
  const fixedLng = parseFloat(config.fixed_lng ?? config.longitude ?? deviceCoords.lng);

  const lat = !isNaN(liveLat) ? liveLat : !isNaN(fixedLat) ? fixedLat : NaN;
  const lng = !isNaN(liveLng) ? liveLng : !isNaN(fixedLng) ? fixedLng : NaN;
  const isLive = !isNaN(liveLat) && !isNaN(liveLng);
  const zoom = config.zoom || 15;

  if (isNaN(lat) || isNaN(lng)) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:8,padding:12}}>
      <svg style={{width:28,height:28,color:"#e2e8f0"}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 0 0-8 8c0 5.25 8 14 8 14s8-8.75 8-14a8 8 0 0 0-8-8z"/>
      </svg>
      <p style={{fontSize:11,color:"#94a3b8",textAlign:"center"}}>
        No location data.<br/>Set Latitude/Longitude on the device, or send <code style={{background:"#f1f5f9",padding:"1px 4px",borderRadius:3}}>latitude</code>/<code style={{background:"#f1f5f9",padding:"1px 4px",borderRadius:3}}>longitude</code> as telemetry.
      </p>
    </div>
  );

  const mapUrl = `https://www.openstreetmap.org/export/embed.html?bbox=${lng-0.01},${lat-0.01},${lng+0.01},${lat+0.01}&layer=mapnik&marker=${lat},${lng}`;

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",borderRadius:8,overflow:"hidden",position:"relative"}}>
      <div style={{flex:1,position:"relative",overflow:"hidden"}}>
        <iframe
          src={mapUrl}
          style={{width:"100%",height:"100%",border:"none"}}
          title="Device Location"
          loading="lazy"
        />
        {isLive && (
          <div style={{position:"absolute",top:6,right:6,background:"rgba(16,185,129,0.9)",borderRadius:20,padding:"2px 8px",fontSize:9,color:"white",fontWeight:700,display:"flex",alignItems:"center",gap:3}}>
            <div style={{width:5,height:5,borderRadius:"50%",background:"white",animation:"pulse 1.5s infinite"}}/>LIVE
          </div>
        )}
      </div>
      <div style={{padding:"5px 10px",background:"white",borderTop:"1px solid #f1f5f9",display:"flex",justifyContent:"space-between",alignItems:"center",flexShrink:0}}>
        <span style={{fontSize:10,color:"#64748b",fontFamily:"monospace"}}>{lat.toFixed(5)}, {lng.toFixed(5)}</span>
        <a href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=${zoom}/${lat}/${lng}`}
           target="_blank" rel="noopener noreferrer"
           style={{fontSize:10,color:"#3b82f6",textDecoration:"none"}}>Open ↗</a>
      </div>
    </div>
  );
}

// ── Phase 3: Device Summary Widget ────────────────────────────────────────────
// Shows device metadata: status, last seen, and configurable key metrics.

export function DeviceSummaryWidget({ config, liveTelem, deviceLastSeen }) {
  const OFFLINE_MS = 5 * 60 * 1000;
  const status = !deviceLastSeen ? "UNKNOWN"
    : (Date.now() - new Date(deviceLastSeen).getTime()) < OFFLINE_MS ? "ONLINE" : "OFFLINE";
  const STATUS_COLOR = {ONLINE:"#10b981",OFFLINE:"#94a3b8",UNKNOWN:"#f59e0b"};
  const keys = config.keys || Object.keys(liveTelem||{}).slice(0,4);

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",gap:8,padding:"2px 0"}}>
      {/* Status row */}
      <div style={{display:"flex",alignItems:"center",gap:8,paddingBottom:6,borderBottom:"1px solid #f1f5f9"}}>
        <div style={{width:10,height:10,borderRadius:"50%",background:STATUS_COLOR[status],flexShrink:0}}/>
        <span style={{fontSize:13,fontWeight:600,color:STATUS_COLOR[status]}}>{status}</span>
        {deviceLastSeen && (
          <span style={{fontSize:10,color:"#94a3b8",marginLeft:"auto"}}>
            {new Date(deviceLastSeen).toLocaleTimeString()}
          </span>
        )}
      </div>
      {/* Key metrics grid */}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,flex:1}}>
        {keys.map(k=>{
          const v = liveTelem?.[k];
          return (
            <div key={k} style={{background:"#f8fafc",borderRadius:6,padding:"6px 8px"}}>
              <p style={{fontSize:9,color:"#94a3b8",textTransform:"uppercase",letterSpacing:"0.05em",margin:"0 0 2px"}}>{k}</p>
              <p style={{fontSize:14,fontWeight:700,color:"#1e293b",margin:0,fontFamily:"monospace"}}>
                {v !== undefined ? (typeof v==="number" ? v.toFixed(1) : String(v)) : "—"}
              </p>
            </div>
          );
        })}
        {!keys.length && (
          <div style={{gridColumn:"1/-1",display:"flex",alignItems:"center",justifyContent:"center",height:60}}>
            <p style={{fontSize:11,color:"#94a3b8"}}>No telemetry received yet</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Phase 3: RPC Button Widget ────────────────────────────────────────────────
// One-shot command button. Sends a named RPC method to the device.

export function RpcButtonWidget({ config, deviceId }) {
  const [state, setState] = useState("idle"); // idle | sending | done | error
  const label  = config.label  || config.method || "Send Command";
  const color  = config.color  || "#3b82f6";
  const method = config.method || "";

  if (!method || !deviceId) return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%",fontSize:12,color:"#94a3b8"}}>
      Configure method in Edit
    </div>
  );

  const send = async () => {
    if (state === "sending") return;
    setState("sending");
    try {
      const token = localStorage.getItem("access_token");
      const BASE = (typeof import.meta!=="undefined"&&import.meta.env?.VITE_API_URL)||"";
      // If method is "set", build standard params from config.param_key
      const body = method === "set"
        ? { method: "set", params: config.params || {} }
        : { method, params: config.params || {} };
      await fetch(`${BASE}/api/v1/rpc/${deviceId}`, {
        method: "POST",
        headers: {"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body: JSON.stringify(body),
      });
      setState("done");
      setTimeout(()=>setState("idle"), 2000);
    } catch {
      setState("error");
      setTimeout(()=>setState("idle"), 2000);
    }
  };

  const BG = {idle:color, sending:"#94a3b8", done:"#10b981", error:"#ef4444"}[state];
  const LABEL = {idle:label, sending:"Sending…", done:"Sent ✓", error:"Error ✗"}[state];

  return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%"}}>
      <button onClick={send} style={{
        padding:"12px 28px", borderRadius:10, border:"none", cursor:state==="sending"?"wait":"pointer",
        background:BG, color:"white", fontSize:14, fontWeight:600,
        transition:"background .3s", minWidth:120,
      }}>
        {LABEL}
      </button>
    </div>
  );
}

// ── Phase 3: RPC Toggle Widget ────────────────────────────────────────────────
// ON/OFF toggle. Reads current state from liveTelem, sends method_on/method_off.

export function RpcToggleWidget({ config, liveTelem, deviceId }) {
  // key      = telemetry key to READ current state (e.g. "led1", "pump", "relay1")
  // paramKey = RPC param key to SEND (defaults to key — usually the same)
  // This separation allows monitor key != control key if needed.
  const key      = config.key       || "";
  const paramKey = config.param_key || key;   // falls back to key if not set
  const label    = config.label     || key    || "Toggle";
  const color    = config.color     || "#10b981";

  // Backward compat: legacy method_on/method_off still work
  const legacyOn  = config.method_on  || "";
  const legacyOff = config.method_off || "";
  const useLegacy = legacyOn !== "" && legacyOff !== "";

  const rawVal = liveTelem?.[key];
  const isOn   = rawVal === true || rawVal === 1 || rawVal === "1" || rawVal === "true" || rawVal === "ON";
  const hasData = rawVal !== undefined && rawVal !== null;
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState(null); // "ok" | "err" | null

  if (!deviceId || !key) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:6}}>
      <svg style={{width:20,height:20,color:"#94a3b8"}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <p style={{fontSize:11,color:"#94a3b8",textAlign:"center",margin:0}}>
        {!deviceId ? "No device linked" : "Select a key in Edit"}
      </p>
    </div>
  );

  const toggle = async () => {
    if (sending || !paramKey) return;
    setSending(true);
    setFeedback(null);
    try {
      const token = localStorage.getItem("access_token");
      const BASE  = (typeof import.meta!=="undefined"&&import.meta.env?.VITE_API_URL)||"";
      // Standard: {"method":"set","params":{"pump":true}}
      // Legacy:   {"method":"turnOn","params":{}}
      const body = useLegacy
        ? { method: isOn ? legacyOff : legacyOn, params: {} }
        : { method: "set", params: { [paramKey]: !isOn } };
      const res = await fetch(`${BASE}/api/v1/rpc/${deviceId}`, {
        method: "POST",
        headers: {"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body: JSON.stringify(body),
      });
      setFeedback(res.ok ? "ok" : "err");
    } catch { setFeedback("err"); }
    setTimeout(() => { setSending(false); setFeedback(null); }, 2000);
  };

  // Colour logic: green=ON, grey=OFF, amber=no data yet
  const dotColor  = !hasData ? "#f59e0b" : isOn ? color : "#e2e8f0";
  const textColor = !hasData ? "#f59e0b" : isOn ? color : "#94a3b8";
  const stateText = !hasData ? "—" : isOn ? "ON" : "OFF";

  return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:10}}>
      {/* Toggle pill */}
      <button onClick={toggle} disabled={sending} style={{
        width:60, height:32, borderRadius:16, border:"none",
        cursor: sending ? "wait" : "pointer",
        background: dotColor, position:"relative", transition:"background .4s",
        opacity: sending ? 0.7 : 1,
      }}>
        <span style={{
          position:"absolute", top:4,
          left: (isOn && hasData) ? 32 : 4,
          width:24, height:24, borderRadius:"50%", background:"white",
          boxShadow:"0 1px 4px rgba(0,0,0,.25)", transition:"left .3s",
        }}/>
      </button>

      {/* State text */}
      <div style={{textAlign:"center"}}>
        <p style={{fontSize:13,fontWeight:700,color:textColor,margin:0}}>
          {sending ? "…" : feedback === "err" ? "Error" : stateText}
        </p>
        <p style={{fontSize:10,color:"#94a3b8",margin:"2px 0 0"}}>{label}</p>
        {!hasData && <p style={{fontSize:9,color:"#f59e0b",margin:"2px 0 0"}}>Waiting for data</p>}
      </div>

      {/* Param key badge — shows what key is being controlled */}
      <p style={{fontSize:9,color:"#cbd5e1",fontFamily:"monospace",margin:0}}>
        {paramKey !== key ? `${key} → ${paramKey}` : key}
      </p>
    </div>
  );
}
