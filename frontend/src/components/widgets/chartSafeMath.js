/**
 * chartSafeMath.js
 *
 * Canonical NaN-safe math primitives for all SVG chart components.
 *
 * Root causes of "M0.0,NaN..." and "<circle cy=NaN>":
 *   1. parseFloat(undefined) → NaN
 *   2. Math.min(...[])       → Infinity
 *   3. Math.max(...[])       → -Infinity
 *   4. mx - mn = 0           → divide-by-zero → Infinity → NaN in SVG
 *   5. allTs=[]              → Math.min(...[]) → Infinity → px()=NaN
 */

export function safeNum(v, fallback = 0) {
  if (typeof v === "number") return Number.isFinite(v) ? v : fallback;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

export function clamp(v, lo, hi) {
  const n = safeNum(v, lo);
  return Math.min(hi, Math.max(lo, n));
}

export function safeMin(arr, fallback = 0) {
  if (!arr || arr.length === 0) return fallback;
  let min = Infinity;
  for (const v of arr) {
    const n = safeNum(v, NaN);
    if (Number.isFinite(n) && n < min) min = n;
  }
  return Number.isFinite(min) ? min : fallback;
}

export function safeMax(arr, fallback = 1) {
  if (!arr || arr.length === 0) return fallback;
  let max = -Infinity;
  for (const v of arr) {
    const n = safeNum(v, NaN);
    if (Number.isFinite(n) && n > max) max = n;
  }
  return Number.isFinite(max) ? max : fallback;
}

export function safeRange(arr, minSpread = 1) {
  const mn = safeMin(arr, 0);
  let   mx = safeMax(arr, mn + minSpread);
  if (mx - mn < minSpread) mx = mn + minSpread;
  // Add 10% padding top and bottom so sudden spikes/drops don't dominate the axis
  const spread = mx - mn;
  const pad = spread * 0.10;
  return { mn: mn - pad, mx: mx + pad, rng: (mx + pad) - (mn - pad) };
}

export function sanitizePoints(pts) {
  if (!pts || !Array.isArray(pts)) return [];
  const out = [];
  for (const p of pts) {
    if (!p || !p.ts) continue;
    const n = typeof p.value === "number" ? p.value : parseFloat(p.value);
    if (Number.isFinite(n)) out.push({ ts: p.ts, value: n });
  }
  return out;
}

export function sanitizeFlat(arr) {
  if (!arr || !Array.isArray(arr)) return [];
  return arr.filter(v => Number.isFinite(typeof v === "number" ? v : parseFloat(v)));
}

export function makePy(range, pad, h) {
  const { mn, rng } = range;
  return function py(v) {
    const n = safeNum(v, mn);
    const y = pad.t + h - ((n - mn) / rng) * h;
    return Number.isFinite(y) ? y : pad.t;
  };
}

export function makePxIndex(count, pad, w) {
  const denom = Math.max(1, count - 1);
  return function px(i) {
    const x = pad.l + (i / denom) * w;
    return Number.isFinite(x) ? x : pad.l;
  };
}

export function makePxTime(minTs, maxTs, pad, w) {
  const denom = Math.max(1, maxTs - minTs);
  return function px(ts) {
    const t = typeof ts === "number" ? ts : new Date(ts).getTime();
    const x = pad.l + ((t - minTs) / denom) * w;
    return Number.isFinite(x) ? x : pad.l;
  };
}

export function buildPath(points) {
  let d = "";
  let first = true;
  for (const { x, y } of points) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    d += `${first ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)} `;
    first = false;
  }
  return first ? "" : d.trim();
}

export function buildAreaPath(linePath, x0, xN, baseY) {
  if (!linePath) return "";
  return `${linePath} L${xN.toFixed(1)},${baseY.toFixed(1)} L${x0.toFixed(1)},${baseY.toFixed(1)} Z`;
}

export function buildArcPath(cx, cy, R, a1Deg, a2Deg) {
  const r2d = (d) => (d * Math.PI) / 180;
  const x1 = cx + R * Math.cos(r2d(a1Deg)), y1 = cy + R * Math.sin(r2d(a1Deg));
  const x2 = cx + R * Math.cos(r2d(a2Deg)), y2 = cy + R * Math.sin(r2d(a2Deg));
  if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) return "";
  const lg = Math.abs(a2Deg - a1Deg) > 180 ? 1 : 0;
  return `M${x1.toFixed(1)},${y1.toFixed(1)} A${R},${R} 0 ${lg} 1 ${x2.toFixed(1)},${y2.toFixed(1)}`;
}
