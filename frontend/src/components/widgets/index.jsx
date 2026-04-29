/**
 * widgets/index.jsx
 * Pure rendering components for each widget type.
 * Props: { config, liveTelem, historyData, alarms }
 * No API calls, no state — data is passed from the parent.
 */

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
  if (!data.length) return (
    <div style={{ height: 80, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6 }}>
      <svg style={{ width: 22, height: 22, color: "#e2e8f0" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 20V10M6 20V4M18 20v-4"/></svg>
      <p style={{ fontSize: 11, color: "#94a3b8", margin: 0 }}>No data yet</p>
    </div>
  );
  const W = 280, H = 80, pad = { t: 4, r: 4, b: 20, l: 4 };
  const maxV = Math.max(...data.map(d => d.value), 1);
  const bw   = (W - pad.l - pad.r) / data.length - 4;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: H }}>
      {data.map((d, i) => {
        const bh = ((d.value / maxV) * (H - pad.t - pad.b));
        const bx = pad.l + i * ((W - pad.l - pad.r) / data.length);
        return (
          <g key={d.key}>
            <rect x={bx} y={H - pad.b - bh} width={bw} height={bh} rx="3" fill={color} opacity="0.85" />
            <text x={bx + bw / 2} y={H - 5} textAnchor="middle" fontSize="7" fill="#94a3b8" fontFamily="monospace">
              {d.key.slice(0, 6)}
            </text>
          </g>
        );
      })}
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

export function ValueCard({ config, liveTelem, historyData }) {
  const raw  = liveTelem?.[config.key];
  const num  = typeof raw === "number" ? raw : parseFloat(raw);
  const isN  = !isNaN(num);
  const alert = config.threshold_high && isN && num > config.threshold_high;
  const history = (historyData?.[config.key] || []).slice(-20).map(p => p.value);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 4 }}>
        <p style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em", color: "#94a3b8" }}>
          {config.label || config.key || "—"}
        </p>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4 }}>
          <span style={{
            fontSize: 44, fontWeight: 800, lineHeight: 1, fontFamily: "ui-monospace,monospace",
            color: alert ? "#ef4444" : (config.color || "#1e293b"),
            transition: "color .3s",
          }}>
            {isN ? num.toFixed(config.decimals ?? 1) : (raw ?? "—")}
          </span>
          {config.unit && (
            <span style={{ fontSize: 16, color: "#94a3b8", fontWeight: 500, paddingBottom: 6 }}>
              {config.unit}
            </span>
          )}
        </div>
        {alert && (
          <span style={{ fontSize: 10, fontWeight: 600, color: "#ef4444", background: "#fef2f2", padding: "2px 8px", borderRadius: 20 }}>
            ⚠ Threshold exceeded
          </span>
        )}
      </div>
      {history.length > 1 && <Sparkline data={history} color={config.color || "#3b82f6"} height={36} />}
    </div>
  );
}

export function LineChartWidget({ config, historyData }) {
  const history = historyData?.[config.key] || [];
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <LineChartSVG data={history} color={config.color || "#3b82f6"} />
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 6, marginTop: 4 }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", flexShrink: 0 }} />
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{history.length} pts · {config.key}</span>
      </div>
    </div>
  );
}

export function GaugeWidget({ config, liveTelem }) {
  const raw = liveTelem?.[config.key];
  const num = typeof raw === "number" ? raw : parseFloat(raw);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 6 }}>
      <GaugeSVG value={isNaN(num) ? config.min : num} min={config.min ?? 0} max={config.max ?? 100} color={config.color || "#3b82f6"} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", padding: "0 8px" }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.min ?? 0}{config.unit}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: config.color || "#3b82f6" }}>{config.label || config.key}</span>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.max ?? 100}{config.unit}</span>
      </div>
    </div>
  );
}

export function StatusLight({ config, liveTelem }) {
  const raw      = liveTelem?.[config.key];
  const isOnline = raw !== undefined;
  const c        = isOnline ? "#10b981" : "#94a3b8";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, height: "100%" }}>
      <div style={{
        width: 52, height: 52, borderRadius: "50%", background: c,
        boxShadow: isOnline ? `0 0 20px ${c}66` : "none",
        transition: "all .5s",
      }} />
      <div style={{ textAlign: "center" }}>
        <p style={{ fontSize: 15, fontWeight: 700, color: c }}>{isOnline ? "ONLINE" : "OFFLINE"}</p>
        <p style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>{config.label || config.key || "Status"}</p>
      </div>
      {raw !== undefined && (
        <p style={{ fontSize: 11, color: "#64748b", fontFamily: "monospace" }}>
          {config.key}: {String(raw)}
        </p>
      )}
    </div>
  );
}

export function BarChartWidget({ config, liveTelem }) {
  // Use config.keys if any match liveTelem; otherwise fall back to all available keys
  const configuredKeys = (config.keys || []).filter(k => liveTelem?.[k] !== undefined);
  const fallbackKeys   = configuredKeys.length ? configuredKeys
    : Object.keys(liveTelem || {}).filter(k => !isNaN(parseFloat(liveTelem[k]))).slice(0, 8);
  const keys = fallbackKeys;
  const data = keys.map(k => ({ key: k, value: parseFloat(liveTelem[k]) || 0 }));

  // No liveTelem received yet — show a waiting state
  if (!liveTelem || Object.keys(liveTelem).length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8 }}>
        <svg style={{ width: 24, height: 24, color: "#e2e8f0" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 20V10M6 20V4M18 20v-4"/></svg>
        <p style={{ fontSize: 11, color: "#94a3b8", margin: 0 }}>Waiting for data…</p>
        {(config.keys || []).length > 0 && (
          <p style={{ fontSize: 10, color: "#cbd5e1", margin: 0 }}>Keys: {config.keys.join(", ")}</p>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%" }}>
      <BarChartSVG data={data} color={config.color || "#3b82f6"} />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
        {data.map(d => (
          <span key={d.key} style={{ fontSize: 9, background: "#f1f5f9", color: "#64748b", padding: "1px 5px", borderRadius: 4, fontFamily: "monospace" }}>
            {d.key}: {d.value.toFixed(1)}
          </span>
        ))}
        {data.length === 0 && <span style={{ fontSize: 10, color: "#f59e0b" }}>No matching keys in telemetry</span>}
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

export function TimeseriesTable({ config, historyData }) {
  const history = [...(historyData?.[config.key] || [])].reverse().slice(0, 25);
  return (
    <div style={{ height: "100%", overflowY: "auto" }}>
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
  { id: "value_card",       label: "Value Card",      icon: "M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8",             desc: "Large number + sparkline" },
  { id: "line_chart",       label: "Line Chart",      icon: "M22 12h-4l-3 9L9 3l-3 9H2",                                            desc: "Time-series history" },
  { id: "gauge",            label: "Gauge",           icon: "M12 22a10 10 0 0 0 7.07-17.07M5 19.07A10 10 0 0 1 12 2",              desc: "Circular gauge with min/max" },
  { id: "status_light",     label: "Status Light",    icon: "M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20z",                           desc: "Online / offline indicator" },
  { id: "bar_chart",        label: "Bar Chart",       icon: "M12 20V10M6 20V4M18 20v-4",                                           desc: "Multi-key comparison bars" },
  { id: "alarm_list",       label: "Alarm List",      icon: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9m-4.73 13a2 2 0 0 1-3.46 0", desc: "Active alarms" },
  { id: "timeseries_table", label: "History Table",   icon: "M3 10h18M3 6h18M3 14h18M3 18h18",                                     desc: "Raw telemetry rows" },
  { id: "pie_chart",        label: "Pie / Donut",     icon: "M21.21 15.89A10 10 0 1 1 8 2.83",                                     desc: "Distribution of keys" },
  { id: "markdown",         label: "Text / Markdown", icon: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7",         desc: "Free-text notes" },
  { id: "entity_table",     label: "Entity Table",    icon: "M4 6h16M4 10h16M4 14h16M4 18h16",                                     desc: "All keys + live values" },
  { id: "html_card",        label: "HTML Card",       icon: "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71",        desc: "Custom HTML with ${key}" },
];

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
export function WidgetRenderer({ widget, liveTelem, historyData, alarms, missingDevice = false }) {
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

  const props = { config: widget.config || {}, liveTelem, historyData, alarms };

  switch (widget.widget_type) {
    case "value_card":       return <ValueCard       {...props} />;
    case "line_chart":       return <LineChartWidget {...props} />;
    case "gauge":            return <GaugeWidget     {...props} />;
    case "status_light":     return <StatusLight     {...props} />;
    case "bar_chart":        return <BarChartWidget  {...props} />;
    case "alarm_list":       return <AlarmListWidget {...props} />;
    case "timeseries_table": return <TimeseriesTable {...props} />;
    case "pie_chart":        return <PieChartWidget  {...props} />;
    case "markdown":         return <MarkdownWidget  {...props} />;
    case "entity_table":     return <EntityTable     {...props} />;
    case "html_card":        return <HtmlCard        {...props} />;
    default:
      return (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: 12, color: "#94a3b8" }}>
          Unknown type: {widget.widget_type}
        </div>
      );
  }
}
