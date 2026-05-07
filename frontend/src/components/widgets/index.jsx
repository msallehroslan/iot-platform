/**
 * widgets/index.jsx
 * Rendering components for each widget type.
 * Props: { config, liveTelem, historyData, alarms, deviceId }
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import { telemetryApi, intelligenceApi, widgetApi, API_BASE } from "../../services/api.js";
// useTelemSlice: subscribes to exactly ONE key — prevents unrelated renders
import { useTelemSlice } from "../../hooks/useTelemetry.js";

// ── Intent context hook (Phase 10) ────────────────────────────────────────────
// ── Module-level intel cache ──────────────────────────────────────────────────
// Prevents N widgets on the same device from each firing their own
// intelligenceApi.unified() call. One fetch per device per 60 seconds.
// Stored outside React so it survives re-renders and is shared across widgets.
const _intelCache    = new Map(); // deviceId → { data, ts }
const _intelPending  = new Map(); // deviceId → Promise
const INTEL_CACHE_MS = 60_000;    // reuse cached result for 60 seconds

function _fetchIntelCached(deviceId) {
  const cached = _intelCache.get(deviceId);
  if (cached && Date.now() - cached.ts < INTEL_CACHE_MS) {
    return Promise.resolve(cached.data);
  }
  if (_intelPending.has(deviceId)) {
    return _intelPending.get(deviceId);
  }
  const promise = intelligenceApi.unified(deviceId)
    .then(data => {
      _intelCache.set(deviceId, { data, ts: Date.now() });
      _intelPending.delete(deviceId);
      return data;
    })
    .catch(err => {
      _intelPending.delete(deviceId);
      throw err;
    });
  _intelPending.set(deviceId, promise);
  return promise;
}

// useIntelContext — shared cached fetch, one API call per device per 60s.
// Multiple widgets on the same device all receive the same cached result.
function useIntelContext(deviceId, key) {
  const [intel,    setIntel]    = useState(() => _intelCache.get(deviceId)?.data || null);
  const [keyIntel, setKeyIntel] = useState(null);
  const [loading,  setLoading]  = useState(false);

  useEffect(() => {
    if (!deviceId) return;

    // If already cached, set immediately — zero network call
    const cached = _intelCache.get(deviceId);
    if (cached && Date.now() - cached.ts < INTEL_CACHE_MS) {
      setIntel(cached.data);
      if (key && cached.data?.enriched_keys) {
        const kd = cached.data.enriched_keys?.find(k2 => k2.key === key);
        if (kd) setKeyIntel(kd);
      }
      return;
    }

    setLoading(true);
    const keyParam = key ? `?key=${encodeURIComponent(key)}` : "";
    _fetchIntelCached(deviceId)
      .then(data => {
        setIntel(data);
        // Extract key intel from enriched_keys if present
        if (key && data?.enriched_keys) {
          const kd = data.enriched_keys.find(k2 => k2.key === key);
          if (kd) setKeyIntel(kd);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [deviceId, key]);

  return { intel, keyIntel, loading };
}

// ── Baseline context badge (Phase 10) ─────────────────────────────────────────
// Returns badge config for a value vs its baseline, or null.
function getBaselineBadge(value, key, baselineKeys) {
  if (!baselineKeys || !baselineKeys[key]) return null;
  const b = baselineKeys[key];
  if (b.mean === undefined || b.stddev === undefined) return null;
  const { mean, stddev, upper, lower } = b;

  if (upper !== null && value > upper)
    return { label: `↑ above normal (>${Number(upper).toFixed(1)})`, color: "#ef4444", bg: "#fef2f2", direction: "high" };
  if (lower !== null && value < lower)
    return { label: `↓ below normal (<${Number(lower).toFixed(1)})`, color: "#3b82f6", bg: "#eff6ff", direction: "low" };

  const sigma = stddev || 0;
  if (sigma > 0 && value > mean + sigma)
    return { label: `↑ above mean (μ ${Number(mean).toFixed(1)})`, color: "#f59e0b", bg: "#fffbeb", direction: "high_soft" };
  if (sigma > 0 && value < mean - sigma)
    return { label: `↓ below mean (μ ${Number(mean).toFixed(1)})`, color: "#f59e0b", bg: "#fffbeb", direction: "low_soft" };

  return { label: `✓ normal  (μ ${Number(mean).toFixed(1)})`, color: "#10b981", bg: "#f0fdf4", direction: "normal" };
}

// ── Badge from KeyIntelligence schema (Gap 1 standard shape) ────────────────
// Used when widget receives the new enriched per-key response.
// Backward-compat: old getBaselineBadge still works for raw baseline.keys shape.
function getBadgeFromKeyIntel(keyIntel) {
  if (!keyIntel) return null;
  const { status, risk, reason, baseline_min, baseline_max, baseline_mean, value, unit } = keyIntel;

  if (status === "CRITICAL") {
    return { label: `${reason}`, color: "#ef4444", bg: "#fef2f2", direction: "critical" };
  }
  if (status === "WARNING") {
    return { label: `${reason}`, color: "#f59e0b", bg: "#fffbeb", direction: "warning" };
  }
  if (status === "NORMAL" && baseline_min !== null && baseline_max !== null) {
    return {
      label: `✓ normal  (${baseline_min?.toFixed(1)}–${baseline_max?.toFixed(1)}${unit ? " "+unit : ""})`,
      color: "#10b981", bg: "#f0fdf4", direction: "normal",
    };
  }
  return null;
}

// ── Trend mini-icon lookup (Phase 10) ─────────────────────────────────────────
const TREND_META = {
  RISING:   { icon: "↑",  color: "#ef4444" },
  FALLING:  { icon: "↓",  color: "#3b82f6" },
  STABLE:   { icon: "→",  color: "#10b981" },
  SPIKE:    { icon: "⚡", color: "#f59e0b" },
  DROP:     { icon: "⬇", color: "#8b5cf6" },
  VOLATILE: { icon: "〜", color: "#f97316" },
  UNKNOWN:  { icon: "?",     color: "#94a3b8" },
};

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
      {data
        .map((p, idx) => ({ p, idx }))
        .filter(({ idx }) => idx % Math.max(1, Math.floor(data.length / 5)) === 0 || idx === data.length - 1)
        .map(({ p, idx }) => (
          <text key={idx} x={px(idx)} y={pad.t + h + 15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">
            {new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </text>
        ))}
      <path d={area} fill={`url(#${gid})`} />
      <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(vals.length - 1)} cy={py(vals[vals.length - 1])} r="4" fill={color} stroke="white" strokeWidth="2" />
    </svg>
  );
}

export function LineChartSVGBanded({ data = [], color = "#3b82f6" }) {
  // Renders bucketed data with a min/max band behind the avg line.
  // data shape: [{ ts, value (avg), min, max }, ...]
  if (data.length < 2) return (
    <div style={{ height: 140, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
      <svg style={{ width: 28, height: 28, color: "#e2e8f0" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      <p style={{ fontSize: 11, color: "#94a3b8" }}>No data yet</p>
    </div>
  );

  const W = 460, H = 140, pad = { t: 8, r: 8, b: 20, l: 30 };
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;

  const avgs = data.map((p) =>
    typeof p.value === "number" ? p.value : parseFloat(p.value) || 0
  );

  const mins = data.map((p, idx) =>
    typeof p.min === "number" ? p.min : avgs[idx]
  );

  const maxs = data.map((p, idx) =>
    typeof p.max === "number" ? p.max : avgs[idx]
  );

  const allVals = [...avgs, ...mins, ...maxs];
  const mn = Math.min(...allVals), mx = Math.max(...allVals), rng = mx - mn || 1;

  const px = (i) => pad.l + (i / (avgs.length - 1)) * w;
  const py = (v) => pad.t + h - ((v - mn) / rng) * h;

  const avgPath = avgs
    .map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`)
    .join(" ");

  const bandPath = [
    ...maxs.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`),
    ...mins.slice().reverse().map((v, i) => `L${px(mins.length - 1 - i).toFixed(1)},${py(v).toFixed(1)}`),
    "Z",
  ].join(" ");

  const gid = `band${color.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: H }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.14" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>

      {[0, 0.25, 0.5, 0.75, 1].map((t) => {
        const y = pad.t + h * t;
        const val = (mx - rng * t).toFixed(1);
        return (
          <g key={t}>
            <line x1={pad.l} y1={y} x2={pad.l + w} y2={y} stroke="#f1f5f9" strokeWidth="1" />
            <text x={pad.l - 4} y={y + 3} fontSize="8" fill="#94a3b8" textAnchor="end" fontFamily="monospace">{val}</text>
          </g>
        );
      })}

      {data
        .map((p, idx) => ({ p, idx }))
        .filter(({ idx }) => idx % Math.max(1, Math.floor(data.length / 5)) === 0 || idx === data.length - 1)
        .map(({ p, idx }) => (
          <text key={idx} x={px(idx)} y={pad.t + h + 15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">
            {new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </text>
        ))}

      <path d={bandPath} fill={color} fillOpacity="0.08" />
      <path d={avgPath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(avgs.length - 1)} cy={py(avgs[avgs.length - 1])} r="4" fill={color} stroke="white" strokeWidth="2" />
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
      {data
        .map((p, idx) => ({ p, idx }))
        .filter(({ idx }) => idx % Math.max(1, Math.floor(data.length / 5)) === 0 || idx === data.length - 1)
        .map(({ p, idx }) => (
          <text key={idx} x={px(idx)} y={pad.t + h + 15} fontSize="7" fill="#cbd5e1" textAnchor="middle" fontFamily="monospace">
            {new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </text>
        ))}
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

function ValueCard({ config, liveTelem, historyData, deviceId, intelligence }) {
  const devId = deviceId || config.device_id;
  // Use liveTelem prop directly — data comes from dashboard preload + WS flush.
  // Do NOT call useTelemSlice here: it fires its own API calls per-widget
  // which exhausts the DB connection pool on dashboards with many widgets.
  const raw   = liveTelem?.[config.key];
  const num   = typeof raw === "number" ? raw : parseFloat(raw);
  const isN   = !isNaN(num);
  const alert = config.threshold_high && isN && num > config.threshold_high;
  const history = (historyData?.[config.key] || []).slice(-20).map(p => p.value);
  const [window, setWindow] = useState("1h");

  // Intelligence comes from DashboardRuntime prop. useIntelContext as fallback
  // (for device-scoped dashboard, or if preload intelligence wasn't available).
  const { intel: intelFallback, keyIntel } = useIntelContext(
    intelligence ? null : devId,  // skip fetch if we have it from runtime
    config.key
  );
  const intel = intelligence || intelFallback;
  // Prefer enriched KeyIntelligence schema (Gap 1) when available
  const badge = keyIntel
    ? getBadgeFromKeyIntel(keyIntel)
    : isN ? getBaselineBadge(num, config.key, intel?.baseline?.keys || {}) : null;
  const trend  = keyIntel?.trend ?? (intel?.trends || {})[config.key];
  const trendM = trend ? TREND_META[trend] : null;
  const hasAnomaly  = keyIntel ? keyIntel.anomaly : (
    (intel?.anomaly?.anomaly_count ?? 0) > 0 &&
    intel?.anomaly?.most_anomalous_key === config.key
  );
  const intentColor = badge ? badge.color : alert ? "#ef4444" : (config.color || "#1e293b");
  // Recommended action from KeyIntelligence (shown in tooltip / detail)
  const recommendedAction = keyIntel?.recommended_action || null;

  // Compute agg from historyData prop — no API call needed.
  // historyData is hydrated by dashboard preload + WS updates.
  const aggData = historyData?.[config.key] || [];
  const agg = (() => {
    const vals = aggData.map(p => p.value).filter(v => v !== null && !isNaN(v));
    if (!vals.length) return { avg: null, min: null, max: null };
    const sum = vals.reduce((a, b) => a + b, 0);
    return {
      avg: Math.round((sum / vals.length) * 100) / 100,
      min: Math.round(Math.min(...vals) * 100) / 100,
      max: Math.round(Math.max(...vals) * 100) / 100,
    };
  })();

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
          {trendM && (
            <span style={{ fontSize: 14, color: trendM.color, paddingBottom: 8, lineHeight: 1 }}>{trendM.icon}</span>
          )}
          <span style={{
            fontSize: 42, fontWeight: 800, lineHeight: 1, fontFamily: "ui-monospace,monospace",
            color: intentColor, transition: "color .3s",
          }}>
            {isN ? num.toFixed(config.decimals ?? 1) : (raw ?? "—")}
          </span>
          {config.unit && <span style={{ fontSize: 15, color: "#94a3b8", fontWeight: 500, paddingBottom: 5 }}>{config.unit}</span>}
        </div>

        {/* Phase 10 intent badges */}
        {badge && (
          <div style={{
            display: "flex", alignItems: "center", gap: 4,
            padding: "3px 10px", borderRadius: 20, marginTop: 2,
            background: badge.bg, border: `1px solid ${badge.color}33`,
          }}>
            <span style={{ fontSize: 10, fontWeight: 600, color: badge.color }}>{badge.label}</span>
          </div>
        )}
        {hasAnomaly && (
          <div style={{
            padding: "2px 8px", borderRadius: 20, marginTop: 2,
            background: "#fef2f2", border: "1px solid #fca5a5",
          }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: "#dc2626" }}>
              ⚠ anomaly detected
            </span>
          </div>
        )}
        {recommendedAction && badge && badge.direction !== "normal" && (
          <div style={{
            padding: "2px 8px", borderRadius: 6, marginTop: 2,
            background: "#F4F8FF", border: "1px solid #D8E3F3",
            maxWidth: "100%",
          }}>
            <span style={{ fontSize: 9, color: "#334866" }}>
              💡 {recommendedAction}
            </span>
          </div>
        )}
        {alert && !badge && (
          <span style={{ fontSize: 10, fontWeight: 600, color: "#ef4444", background: "#fef2f2", padding: "2px 8px", borderRadius: 20 }}>
            ⚠ Threshold exceeded
          </span>
        )}
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
      {history.length > 1 && <Sparkline data={history} color={intentColor} height={28} />}
    </div>
  );
}

export function LineChartWidget({ config, historyData, deviceId }) {
  const devId = deviceId || config.device_id;
  const key   = config.key;

  // ── Phase 10 #4: resolution-aware history ─────────────────────────────────
  // Windows map to sensible resolutions to avoid querying millions of raw rows.
  //   ≤1h  → raw (max 200 pts)
  //   ≤12h → 5min buckets
  //   ≤3d  → 1h  buckets
  //   >3d  → 1d  buckets
  const WINDOW_CONFIG = {
    "1m":  { hours: 0.016, resolution: "raw"  },
    "5m":  { hours: 0.083, resolution: "raw"  },
    "15m": { hours: 0.25,  resolution: "raw"  },
    "30m": { hours: 0.5,   resolution: "raw"  },
    "1h":  { hours: 1,     resolution: "raw"  },
    "6h":  { hours: 6,     resolution: "5min" },
    "12h": { hours: 12,    resolution: "5min" },
    "24h": { hours: 24,    resolution: "1h"   },
    "7d":  { hours: 168,   resolution: "1h"   },
    "30d": { hours: 720,   resolution: "1d"   },
  };
  const WINDOWS = ["1m","5m","15m","30m","1h","6h","12h","24h","7d","30d"];

  const [window, setWindow]       = useState("1h");
  const [chartData, setChartData] = useState([]);
  const [stats, setStats]         = useState({ avg: null, min: null, max: null, count: 0 });
  const [loading, setLoading]     = useState(false);

  // Live WebSocket points appended to raw view only
  const livePoints = historyData?.[key] || [];

  const round2 = v => Math.round(v * 100) / 100;

  useEffect(() => {
    if (!devId || !key) return;

    const { hours, resolution } = WINDOW_CONFIG[window] || {
      hours: 1,
      resolution: "raw",
    };

    setLoading(true);

    widgetApi
      .lineChart(devId, key, {
        hours,
        limit: 300,
        resolution,
      })
      .then(res => {
        const pts = res?.points || [];
        setChartData(pts);

        if (pts.length) {
          const vals = pts.map(p => p.value).filter(v => v !== null);
          const sum = vals.reduce((a, b) => a + b, 0);

          setStats({
            avg: vals.length ? round2(sum / vals.length) : null,
            min: vals.length ? round2(Math.min(...vals)) : null,
            max: vals.length ? round2(Math.max(...vals)) : null,
            count: pts.length,
          });
        } else {
          setStats({ avg: null, min: null, max: null, count: 0 });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [devId, key, window]);

  // For raw windows, append live WebSocket points on top of fetched data
  const isRaw = (WINDOW_CONFIG[window]?.resolution ?? "raw") === "raw";
  const displayData = isRaw && livePoints.length > chartData.length
    ? livePoints
    : chartData;

  const { resolution } = WINDOW_CONFIG[window] || {};
  const fmt  = v => v === null ? "—" : Number(v).toFixed(2);

  const RES_LABEL = { raw: "raw", "5min": "5 min avg", "1h": "1 h avg", "1d": "1 day avg" };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6 }}>

      {/* Window selector */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 3, flexWrap: "wrap", flex: 1 }}>
          {WINDOWS.map(w => (
            <button key={w} onClick={() => setWindow(w)} style={{
              padding: "2px 7px", borderRadius: 20, fontSize: 9, fontWeight: 600,
              cursor: "pointer", border: "1px solid",
              borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
              background:  window === w ? "#2F8CFF" : "#F4F8FF",
              color:       window === w ? "white" : "#6B7F9F",
              transition:  "all 0.15s",
            }}>{w}</button>
          ))}
        </div>
        {/* Resolution badge */}
        <span style={{
          fontSize: 8, padding: "2px 6px", borderRadius: 20, flexShrink: 0,
          background: resolution === "raw" ? "#f0fdf4" : "#EAF2FF",
          color:      resolution === "raw" ? "#166534" : "#1d4ed8",
          border:     resolution === "raw" ? "1px solid #bbf7d0" : "1px solid #bfdbfe",
          fontWeight: 600,
        }}>
          {RES_LABEL[resolution] || resolution}
        </span>
      </div>

      {/* Stats row */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {[["AVG", stats.avg, "#2F8CFF"], ["MIN", stats.min, "#10b981"], ["MAX", stats.max, "#f59e0b"]].map(([label, val, color]) => (
          <div key={label} style={{
            flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "5px 8px",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
            border: "1px solid #D8E3F3", opacity: loading ? 0.5 : 1, transition: "opacity 0.2s",
          }}>
            <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: "0.06em" }}>{label}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color, fontFamily: "monospace" }}>
              {loading ? "…" : fmt(val)}
            </span>
          </div>
        ))}
        <div style={{
          flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "5px 8px",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
          border: "1px solid #D8E3F3", opacity: loading ? 0.5 : 1,
        }}>
          <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: "0.06em" }}>PTS</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#8b5cf6", fontFamily: "monospace" }}>
            {loading ? "…" : stats.count}
          </span>
        </div>
      </div>

      {/* Chart — range band for bucketed data, plain line for raw */}
      <div style={{ flex: 1, minHeight: 0 }}>
        {resolution !== "raw" && displayData.some(p => p.min !== undefined)
          ? <LineChartSVGBanded data={displayData} color={config.color || "#3b82f6"} />
          : <LineChartSVG data={displayData} color={config.color || "#3b82f6"} />
        }
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{key}{config.unit ? ` (${config.unit})` : ""}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {isRaw && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", flexShrink: 0, display: "inline-block" }} />}
          <span style={{ fontSize: 9, color: "#94a3b8" }}>
            {displayData.length} pts{isRaw ? " · LIVE" : ""}
          </span>
        </div>
      </div>
    </div>
  );
}

export function GaugeWidget({ config, liveTelem, deviceId, intelligence }) {
  const raw   = liveTelem?.[config.key];
  const num   = typeof raw === "number" ? raw : parseFloat(raw);
  const devId = deviceId || config.device_id;
  const [window, setWindow] = useState("24h");
  const [agg, setAgg]       = useState({ min: null, max: null, avg: null });

  // Intelligence comes from DashboardRuntime prop. useIntelContext as fallback
  // (for device-scoped dashboard, or if preload intelligence wasn't available).
  const { intel: intelFallback, keyIntel } = useIntelContext(
    intelligence ? null : devId,  // skip fetch if we have it from runtime
    config.key
  );
  const intel = intelligence || intelFallback;
  const badge = keyIntel
    ? getBadgeFromKeyIntel(keyIntel)
    : !isNaN(num) ? getBaselineBadge(num, config.key, intel?.baseline?.keys || {}) : null;
  const trend  = keyIntel?.trend ?? (intel?.trends || {})[config.key];
  const trendM = trend ? TREND_META[trend] : null;
  const gaugeColor = badge ? badge.color : (config.color || "#3b82f6");

  useEffect(() => {
    if (!devId || !config.key) return;

    const WINDOW_HOURS = {
      "1h": 1,
      "6h": 6,
      "24h": 24,
      "7d": 168,
    };

    widgetApi
      .lineChart(devId, config.key, {
        hours: WINDOW_HOURS[window] || 24,
        limit: 300,
        resolution: "raw",
      })
      .then(res => {
        const vals = (res?.points || [])
          .map(p => p.value)
          .filter(v => v !== null && !isNaN(v));

        if (vals.length) {
          const sum = vals.reduce((a, b) => a + b, 0);

          setAgg({
            avg: Math.round((sum / vals.length) * 100) / 100,
            min: Math.round(Math.min(...vals) * 100) / 100,
            max: Math.round(Math.max(...vals) * 100) / 100,
          });
        }
      })
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

      {/* Gauge dial — colour driven by intent */}
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <GaugeSVG
          value={isNaN(num) ? config.min : num}
          min={config.min ?? 0}
          max={config.max ?? 100}
          color={gaugeColor}
        />
      </div>

      {/* Label row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", padding: "0 8px", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.min ?? 0}{config.unit}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {trendM && <span style={{ fontSize: 11, color: trendM.color }}>{trendM.icon}</span>}
          <span style={{ fontSize: 10, fontWeight: 600, color: gaugeColor }}>{config.label || config.key}</span>
        </div>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{config.max ?? 100}{config.unit}</span>
      </div>

      {/* Phase 10 intent badge */}
      {badge && (
        <div style={{
          padding: "3px 10px", borderRadius: 20, flexShrink: 0,
          background: badge.bg, border: `1px solid ${badge.color}33`,
        }}>
          <span style={{ fontSize: 9, fontWeight: 600, color: badge.color }}>{badge.label}</span>
        </div>
      )}

      {/* AVG / MIN / MAX */}
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
  const key   = config.key || (config.keys || [])[0] || "";
  const devId = deviceId || config.device_id;

  // ── Phase 10 #4: resolution-aware history (same map as LineChartWidget) ───
  const WINDOW_CONFIG = {
    "1m":  { hours: 0.016, resolution: "raw"  },
    "5m":  { hours: 0.083, resolution: "raw"  },
    "15m": { hours: 0.25,  resolution: "raw"  },
    "30m": { hours: 0.5,   resolution: "raw"  },
    "1h":  { hours: 1,     resolution: "raw"  },
    "6h":  { hours: 6,     resolution: "5min" },
    "12h": { hours: 12,    resolution: "5min" },
    "24h": { hours: 24,    resolution: "1h"   },
    "7d":  { hours: 168,   resolution: "1h"   },
    "30d": { hours: 720,   resolution: "1d"   },
  };
  const WINDOWS = ["1m","5m","15m","30m","1h","6h","12h","24h","7d","30d"];

  const [window, setWindow]       = useState("1h");
  const [chartData, setChartData] = useState([]);
  const [stats, setStats]         = useState({ avg: null, min: null, max: null, count: 0 });
  const [loading, setLoading]     = useState(false);

  const livePoints = historyData?.[key] || [];

  useEffect(() => {
    if (!devId || !key) return;

    const { hours, resolution } = WINDOW_CONFIG[window] || {
      hours: 1,
      resolution: "raw",
    };

    setLoading(true);

    widgetApi
      .barChart(devId, key, {
        hours,
        limit: 300,
        resolution,
      })
      .then(res => {
        const pts = res?.points || [];
        setChartData(pts);

        if (pts.length) {
          const vals = pts.map(p => p.value).filter(v => v !== null);
          const sum = vals.reduce((a, b) => a + b, 0);

          setStats({
            avg: vals.length ? Math.round((sum / vals.length) * 100) / 100 : null,
            min: vals.length ? Math.round(Math.min(...vals) * 100) / 100 : null,
            max: vals.length ? Math.round(Math.max(...vals) * 100) / 100 : null,
            count: pts.length,
          });
        } else {
          setStats({ avg: null, min: null, max: null, count: 0 });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [devId, key, window]);

  const isRaw = (WINDOW_CONFIG[window]?.resolution ?? "raw") === "raw";
  const displayData = isRaw && livePoints.length > chartData.length ? livePoints : chartData;
  const { resolution } = WINDOW_CONFIG[window] || {};
  const fmt = v => v === null ? "—" : Number(v).toFixed(2);
  const RES_LABEL = { raw: "raw", "5min": "5 min avg", "1h": "1 h avg", "1d": "1 day avg" };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
      {/* Window selector */}
      <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap", flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 3, flexWrap: "wrap", flex: 1 }}>
          {WINDOWS.map(w => (
            <button key={w} onClick={() => setWindow(w)} style={{
              padding: "2px 7px", borderRadius: 20, fontSize: 9, fontWeight: 600, cursor: "pointer",
              border: "1px solid", borderColor: window === w ? "#2F8CFF" : "#D8E3F3",
              background: window === w ? "#2F8CFF" : "#F4F8FF",
              color: window === w ? "white" : "#6B7F9F",
            }}>{w}</button>
          ))}
        </div>
        <span style={{
          fontSize: 8, padding: "2px 6px", borderRadius: 20, flexShrink: 0,
          background: resolution === "raw" ? "#f0fdf4" : "#EAF2FF",
          color:      resolution === "raw" ? "#166534" : "#1d4ed8",
          border:     resolution === "raw" ? "1px solid #bbf7d0" : "1px solid #bfdbfe",
          fontWeight: 600,
        }}>
          {RES_LABEL[resolution] || resolution}
        </span>
      </div>

      {/* Stats */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {[["AVG", stats.avg, "#2F8CFF"], ["MIN", stats.min, "#10b981"], ["MAX", stats.max, "#f59e0b"], ["PTS", stats.count, "#8b5cf6"]].map(([label, val, color]) => (
          <div key={label} style={{ flex: 1, background: "#F4F8FF", borderRadius: 8, padding: "4px 8px",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 1,
            border: "1px solid #D8E3F3", opacity: loading ? 0.5 : 1 }}>
            <span style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", letterSpacing: ".06em" }}>{label}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color, fontFamily: "monospace" }}>
              {loading ? "…" : (label === "PTS" ? val : fmt(val))}
            </span>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <BarChartSVG data={displayData} color={config.color || "#3b82f6"} />
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>{key}{config.unit ? ` (${config.unit})` : ""}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {isRaw && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", flexShrink: 0, display: "inline-block" }} />}
          <span style={{ fontSize: 9, color: "#94a3b8" }}>{displayData.length} pts{isRaw ? " · LIVE" : ""}</span>
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
  const devId = deviceId || config.device_id;
  const [window, setWindow] = useState("1h");
  const [points, setPoints] = useState([]);
  const [agg, setAgg]       = useState({ avg: null, min: null, max: null, count: 0 });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!devId || !config.key) return;

    const WINDOW_HOURS = {
      "15m": 0.25,
      "30m": 0.5,
      "1h": 1,
      "6h": 6,
      "24h": 24,
    };

    const hours = WINDOW_HOURS[window] || 1;
    setLoading(true);

    widgetApi
      .timeseriesTable(devId, config.key, {
        hours,
        limit: 100,
      })
      .then(res => {
        const pts = res?.points || [];
        setPoints(pts);

        const vals = pts
          .map(p => p.value)
          .filter(v => v !== null && !isNaN(v));

        if (vals.length) {
          const sum = vals.reduce((a, b) => a + b, 0);

          setAgg({
            avg: Math.round((sum / vals.length) * 100) / 100,
            min: Math.round(Math.min(...vals) * 100) / 100,
            max: Math.round(Math.max(...vals) * 100) / 100,
            count: pts.length,
          });
        } else {
          setAgg({ avg: null, min: null, max: null, count: 0 });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [devId, config.key, window]);

  // Fall back to historyData prop during initial load
  const history = points.length > 0
    ? [...points].reverse().slice(0, 100)
    : [...(historyData?.[config.key] || [])].reverse().slice(0, 25);

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


// ── Anomaly Score Widget ──────────────────────────────────────────────────────
export function AnomalyWidget({ config, deviceId, intelligence }) {
  const [fetched, setFetched] = useState(null);
  // Primary: intelligence slice from DashboardRuntime (zero fetch).
  // Fallback: fetch once for device-scoped DashboardPage where intelligence prop is null.
  useEffect(() => {
    if (intelligence || !deviceId) return;
    import("../../services/api.js").then(({ widgetApi }) => {
      widgetApi.anomalyScore(deviceId, config.key || "", 24)
        .then(d => setFetched(d)).catch(() => {});
    });
  }, [deviceId, config.key, !!intelligence]);
  const anomalyData = intelligence?.anomaly || fetched || null;
  const data    = anomalyData;
  const loading = !anomalyData;

  const label = config.key || "all";

  if (loading) return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%"}}>
      <div style={{fontSize:11,color:"#94a3b8"}}>Loading anomaly data...</div>
    </div>
  );

  // widgetApi returns data_service shape: { anomaly_count, most_anomalous_key, recent_anomalies, status }
  // Backward compat: also handles old { summary, anomalies } shape
  const summary      = data?.summary || data || {};
  const anomalyCount = data?.anomaly_count ?? summary?.anomaly_count ?? 0;
  const rawAnomalies = data?.recent_anomalies || data?.anomalies || [];
  const allScores    = label === "all" ? rawAnomalies : rawAnomalies.filter(s => s.key === label);
  const recent       = allScores.slice(0, 20);
  const sampleCount  = summary?.samples_available || 0;
  const minNeeded    = summary?.min_samples_needed || 20;
  const dataStatus   = data?.status || summary?.status || "learning";
  const learning     = dataStatus === "learning" && anomalyCount === 0;

  if (learning) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:10,padding:16}}>
      <div style={{width:48,height:48,borderRadius:"50%",background:"linear-gradient(135deg,#667eea,#764ba2)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:22}}>🧠</div>
      <p style={{fontSize:12,fontWeight:700,color:"#334866",margin:0}}>Learning Mode</p>
      <p style={{fontSize:10,color:"#94a3b8",textAlign:"center",margin:0}}>Collecting baseline data.<br/>Anomaly scoring starts after 20 readings.</p>
      <div style={{width:"80%",height:4,background:"#e2e8f0",borderRadius:2,overflow:"hidden"}}>
        <div style={{width:`${Math.min(100,sampleCount/minNeeded*100)}%`,height:"100%",background:"linear-gradient(90deg,#667eea,#764ba2)",borderRadius:2,transition:"width 1s"}}/>
      </div>
      <p style={{fontSize:9,color:"#94a3b8",margin:0}}>{sampleCount} / {minNeeded} readings</p>
    </div>
  );

  const mostAnomalous = data?.most_anomalous_key || summary?.most_anomalous_key || "";
  const maxScore = recent.length > 0 ? Math.max(...recent.map(s => Math.abs(s.z_score || s.score || 0))) : 0;
  const anomalyColor = anomalyCount > 0 ? "#ef4444" : "#10b981";
  const anomalyLabel = anomalyCount > 0 ? `${anomalyCount} ANOMALIES` : "NORMAL";

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",padding:"10px 12px",gap:8}}>
      {/* Header */}
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <span style={{fontSize:11,fontWeight:700,color:"#334866"}}>Anomaly Score</span>
        <span style={{fontSize:9,padding:"2px 8px",borderRadius:20,background:`${anomalyColor}15`,color:anomalyColor,fontWeight:700,border:`1px solid ${anomalyColor}30`}}>{anomalyLabel}</span>
      </div>

      {/* Score bar */}
      <div style={{display:"flex",flexDirection:"column",gap:6}}>
        {[...new Set(recent.map(s => s.key))].slice(0,4).map(k => {
          const kScores = recent.filter(s => s.key === k);
          const latest = kScores[0];
          const z = Math.abs(latest?.z_score || latest?.score || 0);
          const pct = Math.min(100, z / 5 * 100);
          const col = z > 3 ? "#ef4444" : z > 2 ? "#f59e0b" : "#10b981";
          return (
            <div key={k}>
              <div style={{display:"flex",justifyContent:"space-between",marginBottom:2}}>
                <span style={{fontSize:9,color:"#64748b",fontFamily:"monospace"}}>{k}</span>
                <span style={{fontSize:9,fontWeight:600,color:col}}>z={z.toFixed(2)}</span>
              </div>
              <div style={{height:5,background:"#f1f5f9",borderRadius:3,overflow:"hidden"}}>
                <div style={{width:`${pct}%`,height:"100%",background:col,borderRadius:3,transition:"width 0.5s"}}/>
              </div>
            </div>
          );
        })}
      </div>

      {/* Last anomaly */}
      {maxScore > 2 && (
        <div style={{marginTop:"auto",padding:"5px 8px",borderRadius:6,background:"#fef2f2",border:"1px solid #fecaca"}}>
          <p style={{margin:0,fontSize:9,color:"#dc2626"}}>⚠️ Unusual reading detected — z-score {maxScore.toFixed(2)}</p>
        </div>
      )}
    </div>
  );
}

// ── Baseline / Adaptive Threshold Widget ──────────────────────────────────────
export function BaselineWidget({ config, deviceId, intelligence }) {
  const [fetched, setFetched] = useState(null);
  useEffect(() => {
    if (intelligence || !deviceId) return;
    import("../../services/api.js").then(({ widgetApi }) => {
      widgetApi.baseline(deviceId, config.key || "")
        .then(d => setFetched(d)).catch(() => {});
    });
  }, [deviceId, config.key, !!intelligence]);
  const baselineData = intelligence?.baseline || fetched || null;
  const data    = baselineData;
  const loading = !baselineData;

  const key = config.key;

  if (loading) return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%"}}>
      <div style={{fontSize:11,color:"#94a3b8"}}>Loading baseline...</div>
    </div>
  );

  // widgetApi returns data_service shape: { status, current_hour, keys: { key: { mean, stddev, upper, lower, samples } } }
  // "active" = baseline ready, "learning" = still accumulating data
  const baselineStatus = data?.status || "learning";
  const baselines = data?.keys || {};  // { key: { mean, stddev, upper, lower, min, max, samples } }
  const keyData   = key ? baselines[key] : null;
  const hasData   = baselineStatus === "active" && Object.keys(baselines).length > 0;
  const daysCovered = hasData ? 30 : 0;   // active means 30+ days satisfied
  const needed = 30;

  if (!hasData) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:10,padding:16}}>
      <div style={{width:48,height:48,borderRadius:"50%",background:"linear-gradient(135deg,#f59e0b,#ef4444)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:22}}>📊</div>
      <p style={{fontSize:12,fontWeight:700,color:"#334866",margin:0}}>Building Baseline</p>
      <p style={{fontSize:10,color:"#94a3b8",textAlign:"center",margin:0}}>Adaptive thresholds available after 30 days of data.</p>
      <div style={{width:"80%",height:4,background:"#e2e8f0",borderRadius:2,overflow:"hidden"}}>
        <div style={{width:`${Math.min(100,daysCovered/needed*100)}%`,height:"100%",background:"linear-gradient(90deg,#f59e0b,#ef4444)",borderRadius:2}}/>
      </div>
      <p style={{fontSize:9,color:"#94a3b8",margin:0}}>{daysCovered} / {needed} days</p>
    </div>
  );

  const displayKeys = key ? (keyData ? { [key]: keyData } : {}) : Object.entries(baselines).slice(0, 4).reduce((a, [k, v]) => ({ ...a, [k]: v }), {});

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",padding:"10px 12px",gap:8}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <span style={{fontSize:11,fontWeight:700,color:"#334866"}}>Adaptive Baselines</span>
        <span style={{fontSize:9,color:"#94a3b8"}}>{daysCovered}d data</span>
      </div>
      {Object.entries(displayKeys).map(([k, stats]) => {
        const mean = stats?.mean || 0;
        const std = stats?.std || 0;
        const upper = (mean + 3 * std).toFixed(1);
        const lower = (mean - 3 * std).toFixed(1);
        return (
          <div key={k} style={{background:"#f8faff",borderRadius:8,padding:"7px 10px",border:"1px solid #e2e8f0"}}>
            <div style={{display:"flex",justifyContent:"space-between",marginBottom:4}}>
              <span style={{fontSize:10,fontWeight:600,color:"#334866",fontFamily:"monospace"}}>{k}</span>
              <span style={{fontSize:9,color:"#10b981",fontWeight:600}}>μ={mean.toFixed(1)} σ={std.toFixed(1)}</span>
            </div>
            <div style={{display:"flex",gap:4}}>
              <span style={{fontSize:9,padding:"1px 6px",borderRadius:4,background:"#dbeafe",color:"#1d4ed8"}}>Lower: {lower}</span>
              <span style={{fontSize:9,padding:"1px 6px",borderRadius:4,background:"#dcfce7",color:"#166534"}}>Upper: {upper}</span>
            </div>
          </div>
        );
      })}
      <p style={{fontSize:9,color:"#94a3b8",marginTop:"auto",margin:"auto 0 0"}}>Suggested alarm thresholds (±3σ)</p>
    </div>
  );
}

// ── Health Score Widget ───────────────────────────────────────────────────────
export function HealthScoreWidget({ config, deviceId, intelligence }) {
  const [fetched, setFetched] = useState(null);
  useEffect(() => {
    if (intelligence || !deviceId) return;
    import("../../services/api.js").then(({ widgetApi }) => {
      widgetApi.healthScore(deviceId)
        .then(d => setFetched(d)).catch(() => {});
    });
  }, [deviceId, !!intelligence]);
  const healthRaw = intelligence?.health || fetched || null;
  const data    = healthRaw ? { ...healthRaw, health: healthRaw } : null;
  const loading = !healthRaw;

  if (loading) return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%"}}>
      <div style={{fontSize:11,color:"#94a3b8"}}>Loading health score...</div>
    </div>
  );

  // widgetApi.healthScore returns data_service shape (flat):
  // { health_score, health_label, uptime_score, alarm_score, stability_score, freshness_score, maintenance_due }
  // Backward compat: also handles old nested { health: {...} } shape
  const healthData = data?.health || data || {};
  const score = healthData?.health_score ?? healthData?.score ?? null;
  const components = {
    uptime: healthData?.uptime_score,
    alarm: healthData?.alarm_score,
    stability: healthData?.stability_score,
    freshness: healthData?.freshness_score,
    ...( healthData?.components || {} )
  };
  const status = healthData?.health_label || (score === null ? "UNKNOWN" : score >= 80 ? "HEALTHY" : score >= 60 ? "WARNING" : "CRITICAL");
  const color = status === "HEALTHY" ? "#10b981" : status === "WARNING" ? "#f59e0b" : status === "UNKNOWN" ? "#94a3b8" : "#ef4444";
  const maintenance = healthData?.maintenance_due;

  // Gauge arc calculation
  const radius = 40;
  const cx = 60, cy = 65;
  const startAngle = -210, endAngle = 30;
  const range = endAngle - startAngle;
  const scoreAngle = score !== null ? startAngle + (score / 100) * range : startAngle;
  const toRad = a => (a * Math.PI) / 180;
  const arcX = (angle) => cx + radius * Math.cos(toRad(angle));
  const arcY = (angle) => cy + radius * Math.sin(toRad(angle));
  const bgPath = `M ${arcX(startAngle)} ${arcY(startAngle)} A ${radius} ${radius} 0 1 1 ${arcX(endAngle)} ${arcY(endAngle)}`;
  const fillPath = score !== null ? `M ${arcX(startAngle)} ${arcY(startAngle)} A ${radius} ${radius} 0 ${Math.abs(scoreAngle - startAngle) > 180 ? 1 : 0} 1 ${arcX(scoreAngle)} ${arcY(scoreAngle)}` : null;

  const compLabels = { uptime: "Uptime", alarm: "Alarm", stability: "Stability", freshness: "Freshness" };

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",padding:"8px 12px",gap:6}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <span style={{fontSize:11,fontWeight:700,color:"#334866"}}>Device Health</span>
        <span style={{fontSize:9,padding:"2px 8px",borderRadius:20,background:`${color}15`,color,fontWeight:700,border:`1px solid ${color}30`}}>{status}</span>
      </div>

      {/* Gauge */}
      <div style={{display:"flex",justifyContent:"center"}}>
        <svg width="120" height="80" viewBox="0 0 120 80">
          <path d={bgPath} fill="none" stroke="#e2e8f0" strokeWidth="8" strokeLinecap="round"/>
          {fillPath && <path d={fillPath} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"/>}
          <text x={cx} y={cy+2} textAnchor="middle" fontSize="20" fontWeight="700" fill={color}>{score !== null ? Math.round(score) : "?"}</text>
          <text x={cx} y={cy+14} textAnchor="middle" fontSize="7" fill="#94a3b8">/100</text>
        </svg>
      </div>

      {/* Components */}
      {Object.keys(components).length > 0 && (
        <div style={{display:"flex",flexDirection:"column",gap:3}}>
          {Object.entries(components).map(([k, v]) => (
            <div key={k} style={{display:"flex",alignItems:"center",gap:6}}>
              <span style={{fontSize:9,color:"#64748b",width:60}}>{compLabels[k]||k}</span>
              <div style={{flex:1,height:4,background:"#f1f5f9",borderRadius:2,overflow:"hidden"}}>
                <div style={{width:`${v||0}%`,height:"100%",background:v>=80?"#10b981":v>=60?"#f59e0b":"#ef4444",borderRadius:2}}/>
              </div>
              <span style={{fontSize:9,fontWeight:600,color:"#334866",width:24,textAlign:"right"}}>{Math.round(v||0)}</span>
            </div>
          ))}
        </div>
      )}

      {maintenance && (
        <div style={{padding:"4px 8px",borderRadius:6,background:"#fef3c7",border:"1px solid #fde68a"}}>
          <p style={{margin:0,fontSize:9,color:"#92400e"}}>⚠️ {healthData?.maintenance_reason || "Maintenance recommended"}</p>
        </div>
      )}
    </div>
  );
}

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
  // ── Intelligence widgets ─────────────────────────────────────────────────
  { id: "anomaly_score",     label: "Anomaly Score",     icon: "M13 10V3L4 14h7v7l9-11h-7z",                                           desc: "Z-score anomaly detection",       category: "intelligence" },
  { id: "baseline",          label: "Baseline / Thresholds", icon: "M3 3v18h18M7 12l4-4 4 4 4-4",                                    desc: "Adaptive threshold suggestions",  category: "intelligence" },
  { id: "health_score",      label: "Health Score",      icon: "M22 12h-4l-3 9L9 3l-3 9H2",                                           desc: "Device health 0-100 gauge",       category: "intelligence" },
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
// ── Priority 1: Custom equality comparators ──────────────────────────────────
// Each widget only re-renders when its OWN data changes.
// liveTelem: compare only the key this widget cares about (config.key)
// historyData: compare only the key's array length + last point ts
// config: shallow compare (configs are stable objects from dashboard state)
// alarms: compare count + first alarm id

function _configEq(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.key === b.key &&
    a.keys === b.keys &&
    a.device_id === b.device_id &&
    a.color === b.color &&
    a.min === b.min &&
    a.max === b.max &&
    a.label === b.label &&
    a.unit === b.unit &&
    a.decimals === b.decimals &&
    a.threshold_high === b.threshold_high
  );
}

function _telemKeyEq(key) {
  // Returns comparator that only checks prevTelem[key] vs nextTelem[key]
  return (prev, next) => prev?.[key] === next?.[key];
}

function _histKeyEq(key) {
  // Returns comparator: history changed only if array length or last ts changed
  return (prev, next) => {
    const pa = prev?.[key]; const na = next?.[key];
    if (pa === na) return true;
    if (!pa || !na) return pa === na;
    if (pa.length !== na.length) return false;
    if (pa.length === 0) return true;
    return pa[pa.length - 1]?.ts === na[na.length - 1]?.ts;
  };
}

function _alarmsEq(prev, next) {
  if (prev === next) return true;
  if (!prev || !next) return prev === next;
  if (prev.length !== next.length) return false;
  if (prev.length === 0) return true;
  return prev[0]?.id === next[0]?.id && prev[0]?.status === next[0]?.status;
}

// ValueCard: rerenders when its specific key value changes
export const MemoValueCard = React.memo(ValueCard, (prev, next) =>
  _configEq(prev.config, next.config) &&
  prev.liveTelem?.[prev.config?.key] === next.liveTelem?.[next.config?.key] &&
  _histKeyEq(prev.config?.key)(prev.historyData, next.historyData) &&
  prev.deviceId === next.deviceId
);

// LineChartWidget: rerenders when its key's history changes
export const MemoLineChartWidget = React.memo(LineChartWidget, (prev, next) =>
  _configEq(prev.config, next.config) &&
  _histKeyEq(prev.config?.key)(prev.historyData, next.historyData) &&
  prev.deviceId === next.deviceId
);

// GaugeWidget: rerenders when its key's live value or history changes
export const MemoGaugeWidget = React.memo(GaugeWidget, (prev, next) =>
  _configEq(prev.config, next.config) &&
  prev.liveTelem?.[prev.config?.key] === next.liveTelem?.[next.config?.key] &&
  _histKeyEq(prev.config?.key)(prev.historyData, next.historyData) &&
  prev.deviceId === next.deviceId
);

// TimeseriesTable: rerenders when history length changes for its key
export const MemoTimeseriesTable = React.memo(TimeseriesTable, (prev, next) =>
  _configEq(prev.config, next.config) &&
  _histKeyEq(prev.config?.key)(prev.historyData, next.historyData) &&
  prev.deviceId === next.deviceId
);

// BarChartWidget: rerenders when its key's history changes
export const MemoBarChartWidget = React.memo(BarChartWidget, (prev, next) =>
  _configEq(prev.config, next.config) &&
  _histKeyEq(prev.config?.key)(prev.historyData, next.historyData) &&
  prev.deviceId === next.deviceId
);

// MultiAxisChart: rerenders when ANY of its keys' history changes
export const MemoMultiAxisChart = React.memo(MultiAxisChartWidget, (prev, next) => {
  if (!_configEq(prev.config, next.config)) return false;
  if (prev.deviceId !== next.deviceId) return false;
  const keys = prev.config?.keys || [];
  return keys.every(k => _histKeyEq(k)(prev.historyData, next.historyData));
});

// TrendIndicator: rerenders when its key's live value changes
export const MemoTrendIndicator = React.memo(TrendIndicatorWidget, (prev, next) =>
  _configEq(prev.config, next.config) &&
  prev.liveTelem?.[prev.config?.key] === next.liveTelem?.[next.config?.key] &&
  prev.deviceId === next.deviceId
);

// TaatInsightWidget: rerenders only when deviceId or key changes (fetches own data)
export const MemoTaatInsightWidget = React.memo(TaatInsightWidget, (prev, next) =>
  prev.deviceId === next.deviceId &&
  prev.config?.key === next.config?.key &&
  _configEq(prev.config, next.config)
);

// DeviceSummary: rerenders when any live value changes (shows multiple keys)
export const MemoDeviceSummary = React.memo(DeviceSummaryWidget, (prev, next) => {
  if (!_configEq(prev.config, next.config)) return false;
  if (prev.deviceId !== next.deviceId) return false;
  if (prev.deviceLastSeen !== next.deviceLastSeen) return false;
  // Compare all values in the liveTelem object shallowly
  const pk = Object.keys(prev.liveTelem || {});
  const nk = Object.keys(next.liveTelem || {});
  if (pk.length !== nk.length) return false;
  return pk.every(k => prev.liveTelem[k] === next.liveTelem[k]);
});

// StatusLight, AlarmList, RpcToggle — also memo with appropriate comparators
export const MemoStatusLight = React.memo(StatusLight, (prev, next) =>
  _configEq(prev.config, next.config) &&
  prev.liveTelem?.[prev.config?.key] === next.liveTelem?.[next.config?.key] &&
  prev.deviceLastSeen === next.deviceLastSeen
);

export const MemoAlarmList = React.memo(AlarmListWidget, (prev, next) =>
  _alarmsEq(prev.alarms, next.alarms)
);

export const MemoRpcToggle = React.memo(RpcToggleWidget, (prev, next) =>
  _configEq(prev.config, next.config) &&
  prev.liveTelem?.[prev.config?.key] === next.liveTelem?.[next.config?.key] &&
  prev.deviceId === next.deviceId
);

export const MemoPieChart = React.memo(PieChartWidget, (prev, next) => {
  if (!_configEq(prev.config, next.config)) return false;
  const keys = prev.config?.keys || [];
  return keys.every(k => prev.liveTelem?.[k] === next.liveTelem?.[k]);
});

export const MemoEntityTable = React.memo(EntityTable, (prev, next) => {
  const pk = Object.keys(prev.liveTelem || {});
  const nk = Object.keys(next.liveTelem || {});
  if (pk.length !== nk.length) return false;
  return pk.every(k => prev.liveTelem[k] === next.liveTelem[k]);
});

export const WIDGET_COMPONENT_MAP = {
  // ── Data ──────────────────────────────────────────────────────────────────
  value_card:        MemoValueCard,
  line_chart:        MemoLineChartWidget,
  multi_axis_chart:  MemoMultiAxisChart,
  gauge:             MemoGaugeWidget,
  bar_chart:         MemoBarChartWidget,
  timeseries_table:  MemoTimeseriesTable,
  pie_chart:         MemoPieChart,
  entity_table:      MemoEntityTable,
  // ── Status ─────────────────────────────────────────────────────────────────
  status_light:      MemoStatusLight,
  device_summary:    MemoDeviceSummary,
  taat_insight:      MemoTaatInsightWidget,
  alarm_list:        MemoAlarmList,
  map:               MapWidget,
  fleet_map:         FleetMapWidget,
  trend_indicator:   MemoTrendIndicator,
  // ── Control ────────────────────────────────────────────────────────────────
  rpc_button:        RpcButtonWidget,
  rpc_toggle:        MemoRpcToggle,
  rpc_input:         RpcInputWidget,
  // ── Intelligence ───────────────────────────────────────────────────────────
  anomaly_score:     AnomalyWidget,
  baseline:          BaselineWidget,
  health_score:      HealthScoreWidget,
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
export function WidgetRenderer({ widget, liveTelem, historyData, alarms, intelligence = null, missingDevice = false, deviceLastSeen = null, userRole = "TENANT_ADMIN", deviceId = null, allDevices = [], currentDevice = null }) {
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
  const props = { config: effectiveConfig, liveTelem, historyData, alarms, intelligence, deviceId: widget.config?.device_id || deviceId, deviceLastSeen };

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
      const parsed = inputType === "number" ? parseFloat(value) : value;
      if (inputType === "number" && isNaN(parsed)) {
        setErrMsg("Enter a valid number"); setState("idle"); return;
      }
      const { rpcApi } = await import("../../services/api.js");
      const res = await rpcApi.send(deviceId, { method, params: { [paramKey]: parsed } });
      if (!res) throw new Error("Send failed");
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
  const devId = deviceId || config.device_id;

  // Self-fetch history for all keys — same lazy pattern as LineChartWidget.
  // This fires once on mount and when keys/devId change.
  // historyData prop (from WS updates) overlays on top.
  const [fetchedHistory, setFetchedHistory] = useState({});
  useEffect(() => {
    if (!devId || !keys.length) return;
    let cancelled = false;
    Promise.allSettled(
      keys.map(k =>
        widgetApi.lineChart(devId, k, { hours: 1, limit: 100, resolution: "raw" })
          .then(res => ({ key: k, pts: res?.points || [] }))
          .catch(() => ({ key: k, pts: [] }))
      )
    ).then(results => {
      if (cancelled) return;
      const hist = {};
      results.forEach(r => {
        if (r.status === "fulfilled") hist[r.value.key] = r.value.pts;
      });
      setFetchedHistory(hist);
    });
    return () => { cancelled = true; };
  }, [devId, keys.join(",")]);

  if (!keys.length) return (
    <div style={{ display:"flex",alignItems:"center",justifyContent:"center",height:"100%",fontSize:12,color:"#94a3b8" }}>
      Configure keys in Edit
    </div>
  );
  const W=460, H=170, pad={t:8,r:48,b:28,l:44};
  const w=W-pad.l-pad.r, h=H-pad.t-pad.b;

  // Merge fetched history with WS live updates from historyData prop
  const mergedHistory = { ...fetchedHistory };
  if (historyData) {
    Object.entries(historyData).forEach(([k, pts]) => {
      if (pts?.length) mergedHistory[k] = pts;
    });
  }

  // Build series with individual min/max for true multi-axis
  const series = keys.map((k,i)=>{
    const pts = (mergedHistory[k]||[]).map(p=>({ts:p.ts, value:typeof p.value==="number"?p.value:parseFloat(p.value)||0}));
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

export function TrendIndicatorWidget({ config, deviceId, liveTelem, intelligence }) {
  const key      = config.key || "";
  const label    = config.label || key || "Trend";
  const [trendFetched, setTrendFetched] = useState(null);
  const [loading, setLoading] = useState(false);

  // Primary: read trend from intelligence slice (DashboardRuntime — zero fetch).
  // Fallback: fetch once if intelligence not provided (device-scoped DashboardPage).
  const trendFromIntel = intelligence?.trends?.[key] || null;

  useEffect(() => {
    if (trendFromIntel || !deviceId || !key) return; // already have it
    setLoading(true);
    const token = localStorage.getItem("access_token") || "";
    let base = "/api/v1";
    import("../../services/api.js").then(({ API_BASE }) => { base = API_BASE; }).catch(() => {});
    fetch(`${base}/intelligence/trend/${deviceId}/${key}?minutes=30`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setTrendFetched(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [deviceId, key, !!trendFromIntel]);

  const trend = trendFromIntel
    ? { trend: trendFromIntel, confidence: 0.7, change_pct: 0 }
    : trendFetched;

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
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);

  // Fetch all tenant devices with locations dynamically — don't rely on config.devices only
  const [allDevices, setAllDevices] = useState(config.devices || []);
  useEffect(() => {
    import("../../services/api.js").then(({ apiFetch }) => {
      apiFetch("/devices/?limit=100")
        .then(r => {
          // Handle both {items:[]} and plain array responses
          const list = Array.isArray(r) ? r : (r?.items || []);
          setAllDevices(list);
        })
        .catch(() => {});
    });
  }, []);

  // Show devices with either lat/lng from device record OR from config
  const devices = allDevices.filter(d =>
    (d.latitude != null && d.longitude != null) ||
    (d.lat != null && d.lng != null)
  ).map(d => ({
    ...d,
    latitude:  d.latitude ?? d.lat,
    longitude: d.longitude ?? d.lng,
  }));

  useEffect(() => {
    if (!devices.length) return;

    // Invalidate map size whenever devices list changes (handles container resize)
    if (mapInstanceRef.current) {
      setTimeout(() => mapInstanceRef.current?.invalidateSize(), 100);
      return;
    }

    // Load Leaflet CSS
    if (!document.getElementById("leaflet-css")) {
      const link = document.createElement("link");
      link.id = "leaflet-css";
      link.rel = "stylesheet";
      link.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css";
      document.head.appendChild(link);
    }

    // Load Leaflet JS then init map
    const initMap = () => {
      if (!mapRef.current || mapInstanceRef.current) return;
      const L = window.L;

      const lats = devices.map(d => parseFloat(d.latitude));
      const lngs = devices.map(d => parseFloat(d.longitude));
      const centerLat = lats.reduce((a, b) => a + b, 0) / lats.length;
      const centerLng = lngs.reduce((a, b) => a + b, 0) / lngs.length;

      const map = L.map(mapRef.current, { zoomControl: true, scrollWheelZoom: true });
      mapInstanceRef.current = map;

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
        maxZoom: 19,
      }).addTo(map);

      const STATUS_COLOR = { ACTIVE: "#10b981", INACTIVE: "#94a3b8", DISABLED: "#ef4444" };

      devices.forEach(d => {
        const color = STATUS_COLOR[d.status] || "#6B7F9F";
        const icon = L.divIcon({
          className: "",
          html: `<div style="
            width:20px;height:20px;border-radius:50%;
            background:${color};border:2px solid white;
            box-shadow:0 2px 5px rgba(0,0,0,0.35);
            display:flex;align-items:center;justify-content:center;
          "><span style="color:white;font-size:8px;font-weight:700;line-height:1">
            ${d.name.charAt(0).toUpperCase()}
          </span></div>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
          popupAnchor: [0, -12],
        });

        const marker = L.marker([parseFloat(d.latitude), parseFloat(d.longitude)], { icon });
        const statusLabel = d.status === "ACTIVE" ? "🟢 Online" : "⚫ Offline";
        marker.bindPopup(`
          <div style="min-width:140px;font-family:sans-serif">
            <strong style="font-size:13px">${d.name}</strong><br/>
            <span style="font-size:11px;color:#64748b">${statusLabel}</span><br/>
            <span style="font-size:10px;color:#94a3b8">${parseFloat(d.latitude).toFixed(5)}, ${parseFloat(d.longitude).toFixed(5)}</span><br/>
            <a href="#" onclick="window.dispatchEvent(new CustomEvent('taat-open-device',{detail:'${d.id}'}));return false;"
               style="font-size:11px;color:#2F8CFF;text-decoration:none;font-weight:600">
              → Open Dashboard
            </a>
          </div>
        `);
        marker.addTo(map);
      });

      // Fit all markers
      if (devices.length === 1) {
        map.setView([lats[0], lngs[0]], config.zoom || 14);
      } else {
        map.fitBounds(devices.map(d => [parseFloat(d.latitude), parseFloat(d.longitude)]), { padding: [24, 24] });
      }
      // Ensure tiles load correctly after container renders
      setTimeout(() => map.invalidateSize(), 150);
    };

    if (window.L) {
      initMap();
    } else {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js";
      script.onload = initMap;
      document.head.appendChild(script);
    }

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, [devices.length]);

  // Force Leaflet to recalculate size after widget renders
  useEffect(() => {
    if (mapInstanceRef.current) {
      setTimeout(() => mapInstanceRef.current?.invalidateSize(), 200);
      setTimeout(() => mapInstanceRef.current?.invalidateSize(), 600);
    }
  });

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

  return (
    <div style={{height:"100%",display:"flex",flexDirection:"column",gap:0}}>
      <div ref={mapRef} style={{flex:1,borderRadius:8,overflow:"hidden",minHeight:0}} />
      <div style={{display:"flex",gap:8,flexWrap:"wrap",padding:"4px 2px",flexShrink:0}}>
        {devices.map(d => (
          <div key={d.id} style={{display:"flex",alignItems:"center",gap:4}}>
            <div style={{width:7,height:7,borderRadius:"50%",background:d.status==="ACTIVE"?"#10b981":"#94a3b8",flexShrink:0}}/>
            <span style={{fontSize:10,color:"#64748b"}}>{d.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}


export function MapWidget({ config, liveTelem, deviceId }) {
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const markerRef = useRef(null);
  const latKey = config.lat_key || "latitude";
  const lngKey = config.lng_key || "longitude";
  const [deviceInfo, setDeviceInfo] = useState({ lat: null, lng: null, name: null, lastSeen: null, id: null });

  // Device lat/lng comes from config (set by WidgetRenderer from allDevices/currentDevice).
  // Last seen status comes from liveTelem updates — no independent device fetch needed.
  useEffect(() => {
    // Seed from config if available (WidgetRenderer injects fixed_lat/fixed_lng)
    const lat = config.fixed_lat ?? config.latitude ?? null;
    const lng = config.fixed_lng ?? config.longitude ?? null;
    if (lat !== null && lng !== null) {
      setDeviceInfo(prev => ({ ...prev, lat, lng }));
    }
  }, [config.fixed_lat, config.fixed_lng, config.latitude, config.longitude]);

  const liveLat = parseFloat(liveTelem?.[latKey]);
  const liveLng = parseFloat(liveTelem?.[lngKey]);
  const lat = !isNaN(liveLat) ? liveLat : parseFloat(deviceInfo.lat);
  const lng = !isNaN(liveLng) ? liveLng : parseFloat(deviceInfo.lng);
  const isLive = !isNaN(liveLat) && !isNaN(liveLng);
  const OFFLINE_MS = 5 * 60 * 1000;
  const isOnline = deviceInfo.lastSeen ? (Date.now() - new Date(deviceInfo.lastSeen).getTime()) < OFFLINE_MS : null;
  const statusColor = isOnline === true ? "#10b981" : isOnline === false ? "#94a3b8" : "#f59e0b";
  const zoom = config.zoom || 15;

  useEffect(() => {
    if (isNaN(lat) || isNaN(lng) || !mapRef.current) return;

    const initMap = () => {
      if (!mapRef.current) return;
      const L = window.L;

      if (mapInstanceRef.current) {
        // Update existing marker position (live GPS tracking)
        if (markerRef.current) markerRef.current.setLatLng([lat, lng]);
        mapInstanceRef.current.setView([lat, lng]);
        return;
      }

      const map = L.map(mapRef.current, { zoomControl: true, scrollWheelZoom: true });
      mapInstanceRef.current = map;

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
        maxZoom: 19,
      }).addTo(map);

      const icon = L.divIcon({
        className: "",
        html: `<div style="
          width:20px;height:20px;border-radius:50%;
          background:${statusColor};border:2px solid white;
          box-shadow:0 2px 5px rgba(0,0,0,0.35);
        "></div>`,
        iconSize: [20, 20],
        iconAnchor: [10, 10],
        popupAnchor: [0, -12],
      });

      const marker = L.marker([lat, lng], { icon }).addTo(map);
      markerRef.current = marker;

      const name = deviceInfo.name || config.device_name || "Device";
      const statusLabel = isOnline === true ? "🟢 Online" : isOnline === false ? "⚫ Offline" : "🟡 Unknown";
      const devId = deviceInfo.id || config.device_id || deviceId;
      marker.bindPopup(`
        <div style="min-width:140px;font-family:sans-serif">
          <strong style="font-size:13px">${name}</strong><br/>
          <span style="font-size:11px;color:#64748b">${statusLabel}</span><br/>
          <span style="font-size:10px;color:#94a3b8">${lat.toFixed(5)}, ${lng.toFixed(5)}</span>
          ${isLive ? '<br/><span style="font-size:10px;color:#10b981">📡 Live GPS</span>' : ""}
          ${devId ? `<br/><a href="#" onclick="window.dispatchEvent(new CustomEvent('taat-open-device',{detail:'${devId}'}));return false;" style="font-size:11px;color:#2F8CFF;text-decoration:none;font-weight:600">→ Open Dashboard</a>` : ""}
        </div>
      `);

      map.setView([lat, lng], zoom);
      // Ensure map renders correctly inside widget container
      setTimeout(() => map.invalidateSize(), 150);
    };

    if (!document.getElementById("leaflet-css")) {
      const link = document.createElement("link");
      link.id = "leaflet-css";
      link.rel = "stylesheet";
      link.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css";
      document.head.appendChild(link);
    }

    if (window.L) {
      initMap();
    } else {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js";
      script.onload = initMap;
      document.head.appendChild(script);
    }

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
        markerRef.current = null;
      }
    };
  }, [lat, lng]);

  if (isNaN(lat) || isNaN(lng)) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:8,padding:12}}>
      {deviceInfo.name && (
        <div style={{display:"flex",alignItems:"center",gap:6}}>
          <div style={{width:7,height:7,borderRadius:"50%",background:statusColor}}/>
          <span style={{fontSize:11,fontWeight:600,color:"#334866"}}>{deviceInfo.name}</span>
        </div>
      )}
      <svg style={{width:24,height:24,color:"#e2e8f0"}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 0 0-8 8c0 5.25 8 14 8 14s8-8.75 8-14a8 8 0 0 0-8-8z"/>
      </svg>
      <p style={{fontSize:11,color:"#94a3b8",textAlign:"center"}}>
        No location set.<br/>Edit the device and add Latitude/Longitude.
      </p>
    </div>
  );

  return <div ref={mapRef} style={{height:"100%",width:"100%",borderRadius:8,overflow:"hidden"}} />;
}


export function DeviceSummaryWidget({ config, liveTelem, deviceLastSeen, deviceId, intelligence }) {
  const devId = deviceId || config.device_id;
  const OFFLINE_MS = 5 * 60 * 1000;
  const connStatus = !deviceLastSeen ? "UNKNOWN"
    : (Date.now() - new Date(deviceLastSeen).getTime()) < OFFLINE_MS ? "ONLINE" : "OFFLINE";
  const CONN_COLOR = { ONLINE: "#10b981", OFFLINE: "#94a3b8", UNKNOWN: "#f59e0b" };
  const keys = config.keys || Object.keys(liveTelem || {}).slice(0, 4);

  // Use intelligence from DashboardRuntime prop first. Cache fallback for device dashboard.
  const { intel: intelFallback } = useIntelContext(intelligence ? null : devId, "");
  const intel = intelligence || intelFallback;
  const intelStatus = intel?.status;
  const intelRisk   = intel?.risk;
  const intelReason = intel?.reason;
  const intelRec    = intel?.recommendation;
  const RISK_COLOR  = { LOW: "#10b981", MEDIUM: "#f59e0b", HIGH: "#ef4444", CRITICAL: "#dc2626" };
  const STATUS_COLOR = { HEALTHY: "#10b981", WARNING: "#f59e0b", CRITICAL: "#ef4444", OFFLINE: "#94a3b8" };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 6, padding: "2px 0" }}>
      {/* Connection + intelligence status row */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, paddingBottom: 6, borderBottom: "1px solid #f1f5f9", flexShrink: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: CONN_COLOR[connStatus], flexShrink: 0 }}/>
        <span style={{ fontSize: 11, fontWeight: 600, color: CONN_COLOR[connStatus] }}>{connStatus}</span>
        {intelStatus && (
          <span style={{
            marginLeft: 4, padding: "1px 8px", borderRadius: 20, fontSize: 9, fontWeight: 700,
            background: `${STATUS_COLOR[intelStatus] || "#94a3b8"}18`,
            color: STATUS_COLOR[intelStatus] || "#94a3b8",
            border: `1px solid ${STATUS_COLOR[intelStatus] || "#94a3b8"}40`,
          }}>{intelStatus}</span>
        )}
        {intelRisk && intelRisk !== "LOW" && (
          <span style={{
            padding: "1px 8px", borderRadius: 20, fontSize: 9, fontWeight: 700,
            background: `${RISK_COLOR[intelRisk]}18`,
            color: RISK_COLOR[intelRisk],
            border: `1px solid ${RISK_COLOR[intelRisk]}40`,
          }}>{intelRisk}</span>
        )}
        {deviceLastSeen && (
          <span style={{ fontSize: 9, color: "#94a3b8", marginLeft: "auto" }}>
            {new Date(deviceLastSeen).toLocaleTimeString()}
          </span>
        )}
      </div>

      {/* Reason from intelligence */}
      {intelReason && intelStatus !== "HEALTHY" && (
        <div style={{ padding: "4px 8px", borderRadius: 6, background: "#FFF8F0", border: "1px solid #f59e0b40", flexShrink: 0 }}>
          <p style={{ fontSize: 10, color: "#92400e", margin: 0, lineHeight: 1.4 }}>{intelReason}</p>
        </div>
      )}

      {/* Key metrics grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 5, flex: 1 }}>
        {keys.map(k => {
          const v = liveTelem?.[k];
          return (
            <div key={k} style={{ background: "#f8fafc", borderRadius: 6, padding: "5px 7px" }}>
              <p style={{ fontSize: 8, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.05em", margin: "0 0 1px" }}>{k}</p>
              <p style={{ fontSize: 13, fontWeight: 700, color: "#1e293b", margin: 0, fontFamily: "monospace" }}>
                {v !== undefined ? (typeof v === "number" ? v.toFixed(1) : String(v)) : "—"}
              </p>
            </div>
          );
        })}
        {!keys.length && (
          <div style={{ gridColumn: "1/-1", display: "flex", alignItems: "center", justifyContent: "center", height: 50 }}>
            <p style={{ fontSize: 10, color: "#94a3b8" }}>No telemetry yet</p>
          </div>
        )}
      </div>

      {/* Recommendation */}
      {intelRec && intelStatus !== "HEALTHY" && (
        <div style={{ padding: "4px 8px", borderRadius: 6, background: "#EAF2FF", border: "1px solid #2F8CFF40", flexShrink: 0 }}>
          <p style={{ fontSize: 9, color: "#2F8CFF", margin: 0, fontWeight: 600 }}>💡 {intelRec}</p>
        </div>
      )}
    </div>
  );
}

// ── TAAT v2: Insight Card Widget ──────────────────────────────────────────────
// The "WOW" widget — shows status + reason + risk + recommended action
// for a specific key or the whole device, powered by KeyIntelligence.

export function TaatInsightWidget({ config, deviceId, intelligence }) {
  const devId = deviceId || config.device_id;
  const key   = config.key || "";

  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);

  // Use intelligence slice from DashboardRuntime.
  // If intelligence prop is available (from preload), use it immediately.
  // Fallback: fetch once if intelligence not yet in runtime (e.g. device dashboard).
  useEffect(() => {
    if (!devId) return;
    if (intelligence) {
      setData(intelligence);
      setLoading(false);
      return;
    }
    // Fallback for device-scoped dashboard (not using DashboardRuntime)
    setLoading(true);
    _fetchIntelCached(devId)
      .then(res => setData(res))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [devId, key, intelligence]);

  const RISK_COLOR  = { LOW: "#10b981", MEDIUM: "#f59e0b", HIGH: "#ef4444", CRITICAL: "#dc2626" };
  const STATUS_COLOR = { NORMAL: "#10b981", WARNING: "#f59e0b", CRITICAL: "#ef4444", HEALTHY: "#10b981", OFFLINE: "#94a3b8", UNKNOWN: "#94a3b8" };

  if (loading) return (
    <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: 20, height: 20, border: "2px solid #D8E3F3", borderTopColor: "#2F8CFF", borderRadius: "50%", animation: "spin 0.8s linear infinite" }}/>
    </div>
  );

  if (!data) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: 11, color: "#94a3b8" }}>
      No intelligence data yet
    </div>
  );

  const status = data.status || "UNKNOWN";
  const risk   = data.risk   || "LOW";
  const reason = data.reason || "";
  const action = data.recommended_action || data.recommendation || null;
  const value  = data.value !== undefined ? data.value : null;
  const unit   = data.unit  || config.unit || "";

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: "2px 0" }}>

      {/* Header: status + risk badges */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
        <span style={{
          padding: "2px 10px", borderRadius: 20, fontSize: 10, fontWeight: 700,
          background: `${STATUS_COLOR[status] || "#94a3b8"}18`,
          color: STATUS_COLOR[status] || "#94a3b8",
          border: `1px solid ${STATUS_COLOR[status] || "#94a3b8"}40`,
        }}>
          {status}
        </span>
        <span style={{
          padding: "2px 10px", borderRadius: 20, fontSize: 10, fontWeight: 700,
          background: `${RISK_COLOR[risk] || "#94a3b8"}18`,
          color: RISK_COLOR[risk] || "#94a3b8",
          border: `1px solid ${RISK_COLOR[risk] || "#94a3b8"}40`,
        }}>
          {risk} RISK
        </span>
        {key && <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: "monospace", color: "#6B7F9F" }}>{key}</span>}
      </div>

      {/* Value (if key-level) */}
      {value !== null && (
        <div style={{ flexShrink: 0 }}>
          <span style={{ fontSize: 28, fontWeight: 700, fontFamily: "monospace", color: STATUS_COLOR[status] || "#334866" }}>
            {typeof value === "number" ? value.toFixed(1) : String(value)}
          </span>
          {unit && <span style={{ fontSize: 13, color: "#94a3b8", marginLeft: 4 }}>{unit}</span>}
          {data.baseline_min !== null && data.baseline_max !== null && (
            <span style={{ fontSize: 10, color: "#94a3b8", marginLeft: 8 }}>
              normal {data.baseline_min?.toFixed(1)}–{data.baseline_max?.toFixed(1)} {unit}
            </span>
          )}
        </div>
      )}

      {/* Reason */}
      {reason && (
        <div style={{
          padding: "6px 10px", borderRadius: 8, flexShrink: 0,
          background: `${STATUS_COLOR[status] || "#6B7F9F"}10`,
          border: `1px solid ${STATUS_COLOR[status] || "#6B7F9F"}30`,
        }}>
          <p style={{ fontSize: 11, color: "#334866", margin: 0, lineHeight: 1.5 }}>{reason}</p>
        </div>
      )}

      {/* Health + anomalies (device mode) */}
      {data.health_score !== undefined && (
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <div style={{ flex: 1, background: "#F4F8FF", borderRadius: 6, padding: "4px 8px", border: "1px solid #D8E3F3" }}>
            <p style={{ fontSize: 8, fontWeight: 700, color: "#6B7F9F", margin: "0 0 2px", textTransform: "uppercase", letterSpacing: "0.06em" }}>Health</p>
            <p style={{ fontSize: 13, fontWeight: 700, color: data.health_score >= 70 ? "#10b981" : data.health_score >= 40 ? "#f59e0b" : "#ef4444", margin: 0, fontFamily: "monospace" }}>
              {data.health_score?.toFixed(0)}
            </p>
          </div>
          {data.anomaly_count > 0 && (
            <div style={{ flex: 1, background: "#fef2f2", borderRadius: 6, padding: "4px 8px", border: "1px solid #fca5a5" }}>
              <p style={{ fontSize: 8, fontWeight: 700, color: "#ef4444", margin: "0 0 2px", textTransform: "uppercase", letterSpacing: "0.06em" }}>Anomalies</p>
              <p style={{ fontSize: 13, fontWeight: 700, color: "#dc2626", margin: 0, fontFamily: "monospace" }}>{data.anomaly_count}</p>
            </div>
          )}
        </div>
      )}

      {/* Recommended action */}
      {action && status !== "NORMAL" && status !== "HEALTHY" && (
        <div style={{
          marginTop: "auto", padding: "6px 10px", borderRadius: 8, flexShrink: 0,
          background: "#EAF2FF", border: "1px solid #2F8CFF40",
        }}>
          <p style={{ fontSize: 9, fontWeight: 700, color: "#2F8CFF", margin: "0 0 2px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            💡 Recommended
          </p>
          <p style={{ fontSize: 11, color: "#334866", margin: 0 }}>{action}</p>
        </div>
      )}
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
      const body = method === "set"
        ? { method: "set", params: config.params || {} }
        : { method, params: config.params || {} };
      const { rpcApi } = await import("../../services/api.js");
      await rpcApi.send(deviceId, body);
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
      // Standard: {"method":"set","params":{"pump":true}}
      // Legacy:   {"method":"turnOn","params":{}}
      const body = useLegacy
        ? { method: isOn ? legacyOff : legacyOn, params: {} }
        : { method: "set", params: { [paramKey]: !isOn } };
      const { rpcApi } = await import("../../services/api.js");
      const res = await rpcApi.send(deviceId, body);
      setFeedback(res ? "ok" : "err");
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
