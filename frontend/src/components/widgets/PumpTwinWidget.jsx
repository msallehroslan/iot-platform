/**
 * PumpTwinWidget_final.jsx
 * 
 * MERGED: Lovable 3D model + original full tab content
 * 
 * 3D Model: Industrial end-suction centrifugal pump (Lovable — ISO 2858 style)
 *   - Volute casing, flanges, bolt rings, bearing pedestal
 *   - Coupling guard, TEFC motor with fins, terminal box
 *   - Fan cowl with spinning fan, ribbed baseplate
 * 
 * Tabs: Full original content
 *   - 3D View    : Lovable model + legend
 *   - Intel      : enriched_keys from intelligence layer
 *   - Health     : score arc, component bars, fault diagnosis, alarms
 *   - Efficiency : live formula, RUL, degradation rate, scale bar
 */

import React, { useRef, useState, useMemo, Suspense } from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Html } from "@react-three/drei";

// ─────────────────────────────────────────────────────────────────────────────
// COLOUR HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function statusColor(s) {
  switch ((s || "").toUpperCase()) {
    case "CRITICAL": return "#ef4444";
    case "WARNING":  return "#f59e0b";
    case "NORMAL":
    case "HEALTHY":  return "#10b981";
    default:         return "#94a3b8";
  }
}
function riskColor(r) {
  switch ((r || "").toUpperCase()) {
    case "CRITICAL": return "#ef4444";
    case "HIGH":     return "#f97316";
    case "MEDIUM":   return "#f59e0b";
    case "LOW":      return "#10b981";
    default:         return "#94a3b8";
  }
}
function efficiencyColor(p) {
  if (p == null) return "#94a3b8";
  if (p >= 80) return "#a5b4fc";
  if (p >= 65) return "#10b981";
  if (p >= 50) return "#f59e0b";
  return "#ef4444";
}
function trendIcon(t) {
  switch ((t || "").toUpperCase()) {
    case "RISING": return "↑"; case "FALLING": return "↓";
    case "SPIKE":  return "⚡"; case "DROP":    return "⬇";
    case "VOLATILE": return "〜"; case "STABLE": return "→";
    default: return "–";
  }
}
function trendColor(t) {
  switch ((t || "").toUpperCase()) {
    case "SPIKE": case "VOLATILE": return "#ef4444";
    case "RISING":                 return "#f59e0b";
    case "DROP": case "FALLING":   return "#3b82f6";
    case "STABLE":                 return "#10b981";
    default:                       return "#94a3b8";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// EFFICIENCY FORMULA — η = (H×g) / (Cp×ΔT + H×g) × 100
// ─────────────────────────────────────────────────────────────────────────────

function calcEfficiency(T1, T2, H, Cp = 4186) {
  if (T1 == null || T2 == null || H == null) return null;
  const dT = T2 - T1;
  if (dT <= 0 || H <= 0) return null;
  const Hg = H * 9.81;
  return Math.min(99, Math.max(1, (Hg / (Cp * dT + Hg)) * 100));
}

// ─────────────────────────────────────────────────────────────────────────────
// FAULT DIAGNOSIS — 4 separate components
// ndeStatus, deMotorStatus, dePumpStatus, ppStatus = worst-of per component
// Individual sensor statuses for precise cause identification
// ─────────────────────────────────────────────────────────────────────────────

function diagnoseFaults(
  ndeStatus, deMotorStatus, dePumpStatus, ppStatus,
  ndeTempS, ndeVibS,
  deTempS,  deVibS,
  dePumpTempS, dePumpVibS,
) {
  const nde    = (ndeStatus     || "").toUpperCase();
  const deM    = (deMotorStatus || "").toUpperCase();
  const deP    = (dePumpStatus  || "").toUpperCase();
  const pp     = (ppStatus      || "").toUpperCase();
  const faults = [];

  const isBad = s => s === "WARNING" || s === "CRITICAL";

  // ── NDE motor bearing ────────────────────────────────────────────────────
  if (isBad(nde) && !isBad(deM) && !isBad(deP) && !isBad(pp)) {
    if (isBad(ndeVibS))
      faults.push({ component: "NDE motor", message: "NDE vibration elevated — rotor imbalance or loose cooling fan. Inspect motor internally.", severity: ndeVibS });
    else if (isBad(ndeTempS))
      faults.push({ component: "NDE motor", message: "NDE bearing overheating — lubrication failure or blocked fan cowl. Check grease and airflow.", severity: ndeTempS });
    else
      faults.push({ component: "NDE motor", message: "NDE bearing fault — unloaded end. Check lubrication and cooling.", severity: nde });
  }

  // ── DE motor bearing ─────────────────────────────────────────────────────
  if (isBad(deM) && !isBad(nde) && !isBad(deP) && !isBad(pp)) {
    if (isBad(deVibS))
      faults.push({ component: "DE motor", message: "DE motor vibration elevated — coupling misalignment or motor DE bearing wear under load.", severity: deVibS });
    else if (isBad(deTempS))
      faults.push({ component: "DE motor", message: "DE motor bearing overheating — check coupling alignment and bearing grease.", severity: deTempS });
    else
      faults.push({ component: "DE motor", message: "DE motor bearing fault. Check coupling and bearing condition.", severity: deM });
  }

  // ── DE pump bearing ──────────────────────────────────────────────────────
  if (isBad(deP) && !isBad(nde) && !isBad(deM) && !isBad(pp)) {
    if (isBad(dePumpVibS))
      faults.push({ component: "DE pump", message: "Pump bearing vibration elevated — impeller side bearing wear or hydraulic unbalance.", severity: dePumpVibS });
    else if (isBad(dePumpTempS))
      faults.push({ component: "DE pump", message: "Pump bearing overheating — check pump-side bearing lubrication and seal condition.", severity: dePumpTempS });
    else
      faults.push({ component: "DE pump", message: "Pump bearing housing fault. Inspect pump-side bearing.", severity: deP });
  }

  // ── PP pump end ──────────────────────────────────────────────────────────
  if (isBad(pp) && !isBad(nde) && !isBad(deM) && !isBad(deP))
    faults.push({ component: "PP", message: "Pump end vibration elevated — cavitation or impeller damage. Check suction pressure and inlet valve.", severity: pp });

  // ── NDE combinations ─────────────────────────────────────────────────────
  if (isBad(nde) && isBad(deM) && !isBad(deP) && !isBad(pp))
    faults.push({ component: "NDE + DE motor", message: "Both motor bearings elevated — possible rotor imbalance or motor-wide lubrication failure.", severity: nde === "CRITICAL" || deM === "CRITICAL" ? "CRITICAL" : "WARNING" });

  if (isBad(nde) && isBad(pp) && !isBad(deM) && !isBad(deP))
    faults.push({ component: "NDE + PP", message: "Motor NDE and pump end both elevated — possible shaft resonance or soft-foot foundation issue.", severity: nde === "CRITICAL" || pp === "CRITICAL" ? "CRITICAL" : "WARNING" });

  if (isBad(nde) && isBad(deP) && !isBad(deM) && !isBad(pp))
    faults.push({ component: "NDE + DE pump", message: "Motor NDE and pump bearing elevated — check shaft straightness and bearing preload.", severity: nde === "CRITICAL" || deP === "CRITICAL" ? "CRITICAL" : "WARNING" });

  // ── NDE + two-point combinations ─────────────────────────────────────────
  if (isBad(nde) && isBad(deM) && isBad(pp) && !isBad(deP))
    faults.push({ component: "NDE + DE motor + PP", message: "Both motor ends and pump end elevated — severe rotor unbalance or resonance across the drivetrain. Stop pump for full inspection.", severity: "CRITICAL" });

  if (isBad(nde) && isBad(deP) && isBad(pp) && !isBad(deM))
    faults.push({ component: "NDE + DE pump + PP", message: "Motor NDE, pump bearing, and pump end all elevated — possible bent shaft or severe impeller imbalance. Stop pump immediately.", severity: "CRITICAL" });

  if (isBad(nde) && isBad(deM) && isBad(deP) && !isBad(pp))
    faults.push({ component: "NDE + DE motor + DE pump", message: "All three bearing points elevated — shaft-wide misalignment or foundation resonance. Perform alignment check before restarting.", severity: nde === "CRITICAL" || deM === "CRITICAL" || deP === "CRITICAL" ? "CRITICAL" : "WARNING" });

  // ── Combined patterns — shaft / alignment ────────────────────────────────
  if (isBad(deM) && isBad(deP) && !isBad(nde) && !isBad(pp))
    faults.push({ component: "DE motor + DE pump", message: "Both DE bearings elevated — shaft misalignment between motor and pump. Re-align coupling immediately.", severity: deM === "CRITICAL" || deP === "CRITICAL" ? "CRITICAL" : "WARNING" });

  if (isBad(deP) && isBad(pp) && !isBad(nde) && !isBad(deM))
    faults.push({ component: "DE pump + PP", message: "Pump bearing and pump end both elevated — possible impeller imbalance or bent shaft.", severity: deP === "CRITICAL" || pp === "CRITICAL" ? "CRITICAL" : "WARNING" });

  if (isBad(deM) && isBad(deP) && isBad(pp) && !isBad(nde))
    faults.push({ component: "DE motor + DE pump + PP", message: "All pump-side points critical — severe misalignment or pump mechanical failure. Stop pump for inspection.", severity: "CRITICAL" });

  // ── All four — catastrophic ───────────────────────────────────────────────
  if (isBad(nde) && isBad(deM) && isBad(deP) && isBad(pp))
    faults.push({ component: "ALL", message: "All four monitoring points critical — foundation resonance or catastrophic failure. Stop pump immediately.", severity: "CRITICAL" });

  return faults;
}

// ─────────────────────────────────────────────────────────────────────────────
// 3D PUMP MODEL — Lovable industrial ISO 2858 style
// ─────────────────────────────────────────────────────────────────────────────

function BoltRing({ count = 8, radius = 0.55, z = 0, boltR = 0.05 }) {
  return (
    <group position={[0, 0, z]}>
      {Array.from({ length: count }).map((_, i) => {
        const a = (i / count) * Math.PI * 2;
        return (
          <mesh key={i} position={[Math.cos(a) * radius, Math.sin(a) * radius, 0]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[boltR, boltR, 0.08, 6]} />
            <meshStandardMaterial color="#0f172a" metalness={0.85} roughness={0.35} />
          </mesh>
        );
      })}
    </group>
  );
}

function Flange({ outer = 0.6, inner = 0.35, thickness = 0.1, bolts = 8 }) {
  return (
    <group>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[outer, outer, thickness, 48]} />
        <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.4} />
      </mesh>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[inner, inner, thickness + 0.02, 32]} />
        <meshStandardMaterial color="#1e293b" metalness={0.3} roughness={0.8} />
      </mesh>
      <BoltRing count={bolts} radius={(outer + inner) / 2} z={thickness / 2} />
      <BoltRing count={bolts} radius={(outer + inner) / 2} z={-thickness / 2} />
    </group>
  );
}

function PumpModel({ ndeColor, deMotorColor, dePumpColor, ppColor, rpm }) {
  const groupRef = useRef();
  const impRef   = useRef();
  const fanRef   = useRef();
  const spin     = ((rpm || 1450) / 1450) * 4;

  useFrame((_, dt) => {
    if (impRef.current) impRef.current.rotation.x += dt * spin;
    if (fanRef.current) fanRef.current.rotation.x += dt * spin * 0.9;
  });

  const cast = (color, m = 0.45, r = 0.55) => (
    <meshStandardMaterial color={color} metalness={m} roughness={r} />
  );

  return (
    <group ref={groupRef} position={[0, -0.4, 0]}>
      {/* Baseplate */}
      <mesh position={[0.2, -1.05, 0]} castShadow receiveShadow>
        <boxGeometry args={[5.4, 0.18, 1.9]} />
        <meshStandardMaterial color="#0b1220" metalness={0.4} roughness={0.7} />
      </mesh>
      {[-2.4, -0.4, 1.6, 2.4].map((x, i) => (
        <mesh key={i} position={[x + 0.2, -0.95, 0]}>
          <boxGeometry args={[0.06, 0.06, 1.85]} />
          <meshStandardMaterial color="#1e293b" metalness={0.5} roughness={0.6} />
        </mesh>
      ))}
      {[[-2.5, 0.8], [-2.5, -0.8], [2.7, 0.8], [2.7, -0.8], [0.1, 0.8], [0.1, -0.8]].map(([x, z], i) => (
        <mesh key={i} position={[x, -0.92, z]}>
          <cylinderGeometry args={[0.07, 0.07, 0.08, 8]} />
          <meshStandardMaterial color="#0f172a" metalness={0.9} roughness={0.3} />
        </mesh>
      ))}

      {/* Pump volute casing */}
      <group position={[-2.0, -0.05, 0]}>
        <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
          <torusGeometry args={[0.75, 0.42, 24, 48]} />
          {cast(ppColor)}
        </mesh>
        <mesh position={[0, 0, -0.5]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[1.15, 1.15, 0.12, 48]} />
          {cast(ppColor, 0.4, 0.5)}
        </mesh>
        <mesh position={[0, 0, 0.42]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[1.1, 1.1, 0.1, 48]} />
          {cast(ppColor, 0.45, 0.5)}
        </mesh>
        <BoltRing count={12} radius={0.95} z={0.48} boltR={0.055} />
        <BoltRing count={12} radius={0.95} z={-0.56} boltR={0.055} />

        {/* Impeller */}
        <group ref={impRef} position={[0, 0, -0.35]}>
          <mesh rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.6, 0.6, 0.18, 32]} />
            {cast("#1e293b", 0.85, 0.2)}
          </mesh>
          {Array.from({ length: 7 }).map((_, i) => {
            const a = (i / 7) * Math.PI * 2;
            return (
              <mesh key={i} position={[Math.cos(a) * 0.32, Math.sin(a) * 0.32, 0]} rotation={[0, 0, a + Math.PI / 4]}>
                <boxGeometry args={[0.42, 0.05, 0.16]} />
                {cast("#334155", 0.9, 0.2)}
              </mesh>
            );
          })}
          <mesh rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.12, 0.12, 0.22, 16]} />
            {cast("#0f172a", 0.9, 0.2)}
          </mesh>
        </group>

        {/* Suction nozzle */}
        <group position={[0, 0, -0.8]}>
          <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
            <cylinderGeometry args={[0.42, 0.42, 0.7, 32]} />
            {cast(ppColor, 0.45, 0.5)}
          </mesh>
          <group position={[0, 0, -0.35]} rotation={[Math.PI / 2, 0, 0]}>
            <Flange outer={0.62} inner={0.4} thickness={0.1} bolts={8} />
          </group>
        </group>

        {/* Discharge nozzle */}
        <group position={[0, 1.0, 0]}>
          <mesh castShadow>
            <cylinderGeometry args={[0.32, 0.36, 0.7, 32]} />
            {cast(ppColor, 0.45, 0.5)}
          </mesh>
          <group position={[0, 0.4, 0]}>
            <mesh>
              <cylinderGeometry args={[0.5, 0.5, 0.1, 32]} />
              {cast("#475569", 0.7, 0.4)}
            </mesh>
            {Array.from({ length: 8 }).map((_, i) => {
              const a = (i / 8) * Math.PI * 2;
              return (
                <mesh key={i} position={[Math.cos(a) * 0.4, 0.05, Math.sin(a) * 0.4]}>
                  <cylinderGeometry args={[0.05, 0.05, 0.09, 6]} />
                  <meshStandardMaterial color="#0f172a" metalness={0.9} roughness={0.3} />
                </mesh>
              );
            })}
          </group>
        </group>
        <mesh position={[0, -0.95, 0]} castShadow>
          <boxGeometry args={[1.0, 0.18, 0.7]} />
          <meshStandardMaterial color="#1e293b" metalness={0.4} roughness={0.6} />
        </mesh>
      </group>

      {/* ═══ DE PUMP BEARING PEDESTAL — separate from motor DE ═══ */}
      {/* This is the pump-side bearing housing, between volute and coupling */}
      {/* Uses dePumpColor — independent from motor DE bearing */}
      <group position={[-0.6, -0.05, 0]}>
        {/* Stuffing box / seal chamber */}
        <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.36, 0.40, 0.30, 32]} />
          {cast(dePumpColor, 0.5, 0.45)}
        </mesh>
        {/* Main pump bearing housing body */}
        <mesh position={[0.35, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.38, 0.38, 0.65, 32]} />
          {cast(dePumpColor, 0.5, 0.5)}
        </mesh>
        {/* Cooling ribs */}
        {[0.15, 0.30, 0.48].map((x, i) => (
          <mesh key={i} position={[x, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.44, 0.44, 0.05, 32]} />
            {cast(dePumpColor, 0.45, 0.55)}
          </mesh>
        ))}
        {/* End cap */}
        <mesh position={[0.70, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.32, 0.32, 0.10, 24]} />
          {cast("#334155", 0.7, 0.4)}
        </mesh>
        <BoltRing count={6} radius={0.27} z={0} boltR={0.04} />
        {/* Coloured band ring */}
        <mesh position={[0.35, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.42, 0.42, 0.08, 32]} />
          {cast(dePumpColor, 0.3, 0.3)}
        </mesh>
        {/* Pedestal foot */}
        <mesh position={[0.35, -0.58, 0]} castShadow>
          <boxGeometry args={[0.75, 0.42, 0.65]} />
          <meshStandardMaterial color="#1e293b" metalness={0.4} roughness={0.6} />
        </mesh>
      </group>

      {/* Coupling guard */}
      <group position={[-0.05, 0.0, 0]}>
        {Array.from({ length: 10 }).map((_, i) => {
          const a = (i / 10) * Math.PI * 2;
          return (
            <mesh key={i} position={[0, Math.sin(a) * 0.32, Math.cos(a) * 0.32]} rotation={[0, 0, Math.PI / 2]}>
              <boxGeometry args={[0.7, 0.025, 0.04]} />
              <meshStandardMaterial color="#fbbf24" metalness={0.4} roughness={0.55} />
            </mesh>
          );
        })}
        {[-0.36, 0.36].map((x, i) => (
          <mesh key={i} position={[x, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <torusGeometry args={[0.32, 0.025, 8, 32]} />
            <meshStandardMaterial color="#f59e0b" metalness={0.5} roughness={0.4} />
          </mesh>
        ))}
        <mesh position={[-0.12, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.18, 0.18, 0.18, 16]} />
          {cast("#0f172a", 0.95, 0.2)}
        </mesh>
        <mesh position={[0.12, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.18, 0.18, 0.18, 16]} />
          {cast("#0f172a", 0.95, 0.2)}
        </mesh>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.07, 0.07, 0.85, 16]} />
          <meshStandardMaterial color="#cbd5e1" metalness={0.95} roughness={0.15} />
        </mesh>
      </group>

      {/* TEFC Motor */}
      <group position={[1.4, 0.0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.55, 0.55, 1.7, 48]} />
          {cast("#1e3a5f", 0.55, 0.4)}
        </mesh>
        {Array.from({ length: 28 }).map((_, i) => {
          const a = (i / 28) * Math.PI * 2;
          return (
            <mesh key={i} position={[0, Math.sin(a) * 0.575, Math.cos(a) * 0.575]} rotation={[a, 0, 0]}>
              <boxGeometry args={[1.55, 0.06, 0.05]} />
              <meshStandardMaterial color="#1e40af" metalness={0.5} roughness={0.45} />
            </mesh>
          );
        })}
        {/* DE motor bell housing — motor drive end, deMotorColor */}
        <mesh position={[-0.9, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.48, 0.57, 0.28, 32]} />
          {cast(deMotorColor, 0.55, 0.35)}
        </mesh>
        {/* DE motor label band */}
        <mesh position={[-0.9, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.58, 0.58, 0.08, 32]} />
          {cast(deMotorColor, 0.3, 0.25)}
        </mesh>
        {/* NDE motor bell housing — non-drive end, ndeColor */}
        <mesh position={[0.88, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.57, 0.48, 0.28, 32]} />
          {cast(ndeColor, 0.55, 0.35)}
        </mesh>
        {/* NDE motor label band */}
        <mesh position={[0.88, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.58, 0.58, 0.08, 32]} />
          {cast(ndeColor, 0.3, 0.25)}
        </mesh>
        {/* Terminal box */}
        <group position={[0, 0.62, 0]}>
          <mesh castShadow>
            <boxGeometry args={[0.55, 0.32, 0.5]} />
            <meshStandardMaterial color="#0f172a" metalness={0.5} roughness={0.55} />
          </mesh>
          <mesh position={[0, 0.05, 0.3]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.06, 0.08, 0.12, 12]} />
            <meshStandardMaterial color="#475569" metalness={0.85} roughness={0.3} />
          </mesh>
        </group>
        {/* NDE fan cowl */}
        <group position={[1.05, 0, 0]}>
          <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
            <cylinderGeometry args={[0.5, 0.5, 0.28, 32]} />
            {cast(ndeColor, 0.4, 0.6)}
          </mesh>
          <mesh position={[0.16, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.5, 0.5, 0.04, 32]} />
            <meshStandardMaterial color="#0f172a" metalness={0.3} roughness={0.7} />
          </mesh>
          {Array.from({ length: 12 }).map((_, i) => {
            const a = (i / 12) * Math.PI * 2;
            return (
              <mesh key={i} position={[0.18, Math.sin(a) * 0.3, Math.cos(a) * 0.3]} rotation={[a, 0, 0]}>
                <boxGeometry args={[0.015, 0.15, 0.04]} />
                <meshStandardMaterial color={ndeColor} metalness={0.5} roughness={0.5} />
              </mesh>
            );
          })}
          <group ref={fanRef} position={[0.05, 0, 0]}>
            {Array.from({ length: 6 }).map((_, i) => {
              const a = (i / 6) * Math.PI * 2;
              return (
                <mesh key={i} position={[0, Math.sin(a) * 0.2, Math.cos(a) * 0.2]} rotation={[a, 0.4, 0]}>
                  <boxGeometry args={[0.04, 0.3, 0.06]} />
                  <meshStandardMaterial color="#1e293b" metalness={0.6} roughness={0.5} />
                </mesh>
              );
            })}
          </group>
        </group>
        {[-0.55, 0.55].map((x, i) => (
          <mesh key={i} position={[x, -0.62, 0]} castShadow>
            <boxGeometry args={[0.32, 0.18, 0.9]} />
            <meshStandardMaterial color="#1e293b" metalness={0.5} roughness={0.55} />
          </mesh>
        ))}
      </group>

      {/* ═══ FLOATING LABELS — 4 separate monitoring points ═══ */}
      <Html position={[-2.0, 1.7, 0]} center distanceFactor={6}>
        <div style={{ background:"rgba(15,23,42,0.88)", color:"#e2e8f0", fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:20, border:`2px solid ${ppColor}`, whiteSpace:"nowrap", letterSpacing:"0.5px" }}>PP</div>
      </Html>
      <Html position={[-0.25, 1.1, 0]} center distanceFactor={6}>
        <div style={{ background:"rgba(15,23,42,0.88)", color:"#e2e8f0", fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:20, border:`2px solid ${dePumpColor}`, whiteSpace:"nowrap", letterSpacing:"0.5px" }}>DE pump</div>
      </Html>
      <Html position={[0.5, 1.1, 0]} center distanceFactor={6}>
        <div style={{ background:"rgba(15,23,42,0.88)", color:"#e2e8f0", fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:20, border:`2px solid ${deMotorColor}`, whiteSpace:"nowrap", letterSpacing:"0.5px" }}>DE motor</div>
      </Html>
      <Html position={[2.5, 1.1, 0]} center distanceFactor={6}>
        <div style={{ background:"rgba(15,23,42,0.88)", color:"#e2e8f0", fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:20, border:`2px solid ${ndeColor}`, whiteSpace:"nowrap", letterSpacing:"0.5px" }}>NDE</div>
      </Html>
    </group>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// UI SUB-COMPONENTS
// ─────────────────────────────────────────────────────────────────────────────

function Tab({ label, active, onClick, badgeCount }) {
  return (
    <button onClick={onClick} style={{
      flex: 1, padding: "8px 4px", fontSize: 12, fontWeight: 600,
      border: "none", borderBottom: active ? "2px solid #3b82f6" : "2px solid transparent",
      background: "transparent", color: active ? "#0f172a" : "#64748b",
      cursor: "pointer", position: "relative",
    }}>
      {label}
      {badgeCount > 0 && (
        <span style={{
          marginLeft: 4, fontSize: 10, background: "#ef4444", color: "#fff",
          borderRadius: 8, padding: "1px 6px",
        }}>{badgeCount > 9 ? "9+" : badgeCount}</span>
      )}
    </button>
  );
}

function HealthArc({ score, color, size = 80, label = "/100" }) {
  const r = 28, cx = size / 2, cy = size / 2 + 4;
  const sa = -210, ea = 30;
  const toRad = a => a * Math.PI / 180;
  const ax = a => cx + r * Math.cos(toRad(a));
  const ay = a => cy + r * Math.sin(toRad(a));
  const pct = Math.min(100, Math.max(0, score || 0));
  const angle = sa + (pct / 100) * (ea - sa);
  const bg   = `M${ax(sa)},${ay(sa)} A${r},${r} 0 1 1 ${ax(ea)},${ay(ea)}`;
  const fill = `M${ax(sa)},${ay(sa)} A${r},${r} 0 ${Math.abs(angle - sa) > 180 ? 1 : 0} 1 ${ax(angle)},${ay(angle)}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <path d={bg}   fill="none" stroke="#e2e8f0" strokeWidth="6" strokeLinecap="round" />
      {score != null && score > 0 && <path d={fill} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />}
      <text x={cx} y={cy + 2}  textAnchor="middle" fontSize="13" fontWeight="700" fill={score != null ? color : "#94a3b8"}>{score != null ? Math.round(score) : "?"}</text>
      <text x={cx} y={cy + 13} textAnchor="middle" fontSize="7"  fill="#94a3b8">{label}</text>
    </svg>
  );
}

function KeyIntelRow({ keyIntel, liveTelem }) {
  if (!keyIntel) return null;
  const { key, value, unit, status, trend, trend_change_pct, anomaly, reason } = keyIntel;
  const display = value ?? liveTelem?.[key];
  const color = statusColor(status);
  const isBad = status === "CRITICAL" || status === "WARNING";
  return (
    <div style={{
      padding: "5px 8px", borderRadius: 6, marginBottom: 4,
      background: status === "CRITICAL" ? "#fef2f2" : status === "WARNING" ? "#fffbeb" : "#f8fafc",
      border: `1px solid ${status === "CRITICAL" ? "#fecaca" : status === "WARNING" ? "#fde68a" : "#e2e8f0"}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0 }} />
          <span style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>{key}</span>
          {anomaly && <span style={{ fontSize: 9, background: "#fef2f2", color: "#dc2626", padding: "1px 5px", borderRadius: 10, border: "1px solid #fecaca" }}>anomaly</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {trend && <span style={{ fontSize: 10, color: trendColor(trend), fontWeight: 600 }}>{trendIcon(trend)}{trend_change_pct ? ` ${Math.abs(trend_change_pct).toFixed(1)}%` : ""}</span>}
          <span style={{ fontSize: 12, fontWeight: 700, color }}>{display != null ? `${parseFloat(display).toFixed(1)} ${unit || ""}` : "—"}</span>
        </div>
      </div>
      {reason && isBad && <div style={{ fontSize: 10, color: "#64748b", marginTop: 2, lineHeight: 1.5 }}>{reason}</div>}
    </div>
  );
}

function ScoreBar({ label, score }) {
  if (score == null) return null;
  const color = score >= 80 ? "#10b981" : score >= 60 ? "#f59e0b" : "#ef4444";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
      <span style={{ fontSize: 10, color: "#64748b", width: 68, textTransform: "capitalize" }}>{label}</span>
      <div style={{ flex: 1, height: 5, background: "#f1f5f9", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 10, fontWeight: 600, color, width: 24, textAlign: "right" }}>{Math.round(score)}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN WIDGET
// ─────────────────────────────────────────────────────────────────────────────

export default function PumpTwinWidget({
  config = {}, liveTelem = {}, alarms = [], intelligence = null,
}) {
  const [activeTab, setActiveTab] = useState("3d");

  const health       = intelligence?.health        || {};
  const anomaly      = intelligence?.anomaly       || {};
  const baseline     = intelligence?.baseline      || {};
  const trends       = intelligence?.trends        || {};
  const enrichedKeys = intelligence?.enriched_keys || [];
  const efficiency   = intelligence?.efficiency    || null;

  const overallStatus         = intelligence?.status         || "UNKNOWN";
  const overallRisk           = intelligence?.risk           || "LOW";
  const overallReason         = intelligence?.reason         || null;
  const overallRecommendation = intelligence?.recommendation || null;
  const healthScore           = health?.health_score         ?? null;

  // Get status for a single sensor key
  // Priority 1: enriched_keys from intelligence layer (most accurate)
  // Priority 2: liveTelem + ISO thresholds (works without baseline)
  const getKeyStatus = (configKey) => {
    const sensorKey = config?.[configKey];
    if (!sensorKey) return "UNKNOWN";

    // Priority 1 — use enriched_keys if available
    if (enrichedKeys.length > 0) {
      const found = enrichedKeys.find(x => x.key === sensorKey);
      if (found) return found.status;
    }

    // Priority 2 — fallback to liveTelem with ISO thresholds
    const val = liveTelem?.[sensorKey];
    if (val === undefined || val === null) return "UNKNOWN";
    const n = typeof val === "number" ? val : parseFloat(val);
    if (!isFinite(n)) return "UNKNOWN";

    // Vibration keys (mm/s) — ISO 10816-3
    if (configKey.startsWith("key_vib")) {
      if (n >= 4.5) return "CRITICAL";
      if (n >= 2.3) return "WARNING";
      return "NORMAL";
    }
    // Bearing temp keys (°C)
    if (configKey.startsWith("key_temp_nde") || configKey.startsWith("key_temp_de")) {
      if (n >= 80) return "CRITICAL";
      if (n >= 65) return "WARNING";
      return "NORMAL";
    }
    // Fluid inlet/outlet temp
    if (configKey.startsWith("key_temp_inlet") || configKey.startsWith("key_temp_outlet")) {
      if (n >= 50) return "WARNING";
      return "NORMAL";
    }
    // Discharge pressure (kPa)
    if (configKey.startsWith("key_pressure_out")) {
      if (n < 370) return "CRITICAL";
      if (n < 400) return "WARNING";
      return "NORMAL";
    }
    // Suction pressure (kPa) — low suction = cavitation risk
    if (configKey.startsWith("key_pressure_in")) {
      if (n < 200) return "CRITICAL";
      if (n < 250) return "WARNING";
      return "NORMAL";
    }
    return "NORMAL";
  };

  // Worst-of logic — bearing health = worst across all its sensors
  // Priority: CRITICAL > WARNING > NORMAL > UNKNOWN
  const statusRank = { CRITICAL: 3, WARNING: 2, NORMAL: 1, UNKNOWN: 0 };
  const worstStatus = (...statuses) => {
    return statuses.reduce((worst, s) => {
      const su = (s || "UNKNOWN").toUpperCase();
      return statusRank[su] > statusRank[worst] ? su : worst;
    }, "UNKNOWN");
  };

  // ── 4 separate component statuses ───────────────────────────────────────
  // NDE motor  = worst of: NDE temp + NDE vib
  const ndeStatus = worstStatus(
    getKeyStatus("key_temp_nde"),
    getKeyStatus("key_vib_nde"),
  );

  // DE motor   = worst of: DE motor temp + DE motor vib
  const deMotorStatus = worstStatus(
    getKeyStatus("key_temp_de"),
    getKeyStatus("key_vib_de"),
  );

  // DE pump    = worst of: DE pump temp + DE pump vib  ← NEW separate point
  const dePumpStatus = worstStatus(
    getKeyStatus("key_temp_de_pump"),
    getKeyStatus("key_vib_de_pump"),
  );

  // PP pump    = worst of: PP vib + inlet temp + suction pressure (cavitation)
  const ppStatus = worstStatus(
    getKeyStatus("key_vib_pp"),
    getKeyStatus("key_temp_inlet"),
    getKeyStatus("key_pressure_in"),
  );

  const getRaw = (k) => {
    const key = config?.[k]; if (!key) return null;
    const v = liveTelem?.[key];
    return (v != null && !isNaN(parseFloat(v))) ? parseFloat(v) : null;
  };

  const RPM  = getRaw("key_speed") || 1450;
  const T1   = getRaw("key_temp_inlet");
  const T2   = getRaw("key_temp_outlet");
  const PIN  = getRaw("key_pressure_in");
  const POUT = getRaw("key_pressure_out");
  const Cp   = config?.fluid_cp || 4186;
  const H    = (PIN != null && POUT != null) ? (POUT - PIN) * 0.10197 : (config?.head_m || null);
  const liveEff = useMemo(() => calcEfficiency(T1, T2, H, Cp), [T1, T2, H, Cp]);
  const dT   = (T1 != null && T2 != null) ? Math.abs(T2 - T1) : null;

  const rulDays         = efficiency?.rul_days         ?? null;
  const degradationRate = efficiency?.degradation_rate ?? null;
  const rulConfidence   = efficiency?.rul_confidence   ?? null;

  const activeAlarms = (alarms || []).filter(a =>
    ["ACTIVE", "ACTIVE_UNACK", "ACTIVE_ACK"].includes(a.status)
  );
  const faults = diagnoseFaults(
    ndeStatus, deMotorStatus, dePumpStatus, ppStatus,
    getKeyStatus("key_temp_nde"),     getKeyStatus("key_vib_nde"),
    getKeyStatus("key_temp_de"),      getKeyStatus("key_vib_de"),
    getKeyStatus("key_temp_de_pump"), getKeyStatus("key_vib_de_pump"),
  );
  const headerColor = statusColor(overallStatus);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", background: "transparent", fontFamily: "system-ui,sans-serif" }}>

      {/* Header */}
      <div style={{ padding: "10px 14px", borderBottom: "1px solid #e2e8f0", display: "flex", alignItems: "center", justifyContent: "space-between", background: "linear-gradient(180deg,#f8fafc,#fff)", flexShrink: 0 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "#0f172a" }}>Pump Twin</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {healthScore != null && <span style={{ fontSize: 11, color: "#64748b" }}>Health <b style={{ color: "#0f172a" }}>{Math.round(healthScore)}</b></span>}
          <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, fontWeight: 700, background: `${headerColor}22`, color: headerColor }}>{overallStatus}</span>
          <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, fontWeight: 700, background: `${riskColor(overallRisk)}22`, color: riskColor(overallRisk) }}>{overallRisk} risk</span>
        </div>
      </div>

      {/* Reason strip */}
      {overallReason && (
        <div style={{ margin: "4px 10px", padding: "4px 8px", borderRadius: 6, background: overallStatus === "CRITICAL" ? "#fef2f2" : overallStatus === "WARNING" ? "#fffbeb" : "#f0fdf4", borderLeft: `3px solid ${headerColor}`, fontSize: 10, color: "#334155", lineHeight: 1.5, flexShrink: 0 }}>
          {overallReason}
          {overallRecommendation && <span style={{ color: "#3b82f6", marginLeft: 4 }}>→ {overallRecommendation}</span>}
        </div>
      )}

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid #e2e8f0", flexShrink: 0 }}>
        <Tab label="3D View"    active={activeTab === "3d"}         onClick={() => setActiveTab("3d")} />
        <Tab label="Intel"      active={activeTab === "intel"}      onClick={() => setActiveTab("intel")} badgeCount={anomaly?.anomaly_count || 0} />
        <Tab label="Health"     active={activeTab === "health"}     onClick={() => setActiveTab("health")} />
        <Tab label="Efficiency" active={activeTab === "efficiency"} onClick={() => setActiveTab("efficiency")} />
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>

        {/* 3D TAB */}
        {activeTab === "3d" && (
          <div style={{ height: "100%", position: "relative" }}>
            <Canvas
              shadows={{ type: 2 }}
              camera={{ position: [3.5, 2.2, 5.5], fov: 38 }}
              gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
              onCreated={({ gl, scene }) => {
                gl.shadowMap.type = 2;
                scene.background = new THREE.Color("#0b1220");
              }}
            >
              <ambientLight intensity={0.4} />
              <directionalLight position={[5, 8, 4]} intensity={1.1} castShadow
                shadow-mapSize={[512, 512]}
              />
              <directionalLight position={[-4, 3, -3]} intensity={0.4} color="#60a5fa" />
              <Suspense fallback={null}>
                <PumpModel
                  ndeColor={statusColor(ndeStatus)}
                  deMotorColor={statusColor(deMotorStatus)}
                  dePumpColor={statusColor(dePumpStatus)}
                  ppColor={statusColor(ppStatus)}
                  rpm={RPM}
                />
              </Suspense>
              <OrbitControls enablePan={false} minDistance={4} maxDistance={12} />
            </Canvas>
            {/* Legend — 4 separate monitoring points */}
            <div style={{ position: "absolute", top: 10, left: 10, display: "flex", flexDirection: "column", gap: 5, background: "rgba(15,23,42,0.75)", backdropFilter: "blur(6px)", padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(148,163,184,0.2)" }}>
              {[
                {
                  label: "NDE motor",
                  color: statusColor(ndeStatus),
                  sensors: [
                    { name: "Temp", status: getKeyStatus("key_temp_nde"), trend: trends?.[config?.key_temp_nde] },
                    { name: "Vib",  status: getKeyStatus("key_vib_nde"),  trend: trends?.[config?.key_vib_nde]  },
                  ],
                },
                {
                  label: "DE motor",
                  color: statusColor(deMotorStatus),
                  sensors: [
                    { name: "Temp", status: getKeyStatus("key_temp_de"), trend: trends?.[config?.key_temp_de] },
                    { name: "Vib",  status: getKeyStatus("key_vib_de"),  trend: trends?.[config?.key_vib_de]  },
                  ],
                },
                {
                  label: "DE pump",
                  color: statusColor(dePumpStatus),
                  sensors: [
                    { name: "Temp", status: getKeyStatus("key_temp_de_pump"), trend: trends?.[config?.key_temp_de_pump] },
                    { name: "Vib",  status: getKeyStatus("key_vib_de_pump"),  trend: trends?.[config?.key_vib_de_pump]  },
                  ],
                },
                {
                  label: "PP pump",
                  color: statusColor(ppStatus),
                  sensors: [
                    { name: "Vib",  status: getKeyStatus("key_vib_pp"),    trend: trends?.[config?.key_vib_pp]    },
                    { name: "Temp", status: getKeyStatus("key_temp_inlet"), trend: trends?.[config?.key_temp_inlet] },
                  ],
                },
              ].map(({ label, color, sensors }) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                  <span style={{ width: 9, height: 9, borderRadius: 99, background: color, boxShadow: `0 0 7px ${color}`, flexShrink: 0 }} />
                  <span style={{ fontSize: 11, color: "#e2e8f0", width: 72 }}>{label}</span>
                  <div style={{ display: "flex", gap: 3 }}>
                    {sensors.map(s => (
                      <span key={s.name} style={{
                        fontSize: 9, padding: "1px 5px", borderRadius: 10,
                        background: `${statusColor(s.status)}33`,
                        color: statusColor(s.status),
                        border: `1px solid ${statusColor(s.status)}55`,
                        fontWeight: 600,
                      }}>
                        {s.name}{s.trend && s.trend !== "STABLE" && s.trend !== "UNKNOWN" ? ` ${trendIcon(s.trend)}` : ""}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ position: "absolute", bottom: 8, right: 10, fontSize: 10, color: "#94a3b8", background: "rgba(15,23,42,0.6)", padding: "3px 8px", borderRadius: 6 }}>
              Drag · Zoom · {RPM} RPM
            </div>
          </div>
        )}

        {/* INTEL TAB */}
        {activeTab === "intel" && (
          <div style={{ padding: "8px 10px", height: "100%", overflowY: "auto" }}>
            {anomaly?.anomaly_count > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", marginBottom: 8, background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, fontSize: 11, color: "#dc2626" }}>
                <span style={{ fontWeight: 700 }}>⚡ {anomaly.anomaly_count} {anomaly.anomaly_count === 1 ? "anomaly" : "anomalies"} detected</span>
                {anomaly.most_anomalous_key && <span style={{ color: "#7f1d1d" }}>Most critical: {anomaly.most_anomalous_key}</span>}
              </div>
            )}
            {enrichedKeys.length > 0
              ? [...enrichedKeys].sort((a, b) => ({ CRITICAL: 0, WARNING: 1, NORMAL: 2, UNKNOWN: 3 }[a.status] ?? 3) - ({ CRITICAL: 0, WARNING: 1, NORMAL: 2, UNKNOWN: 3 }[b.status] ?? 3)).map(k => <KeyIntelRow key={k.key} keyIntel={k} liveTelem={liveTelem} />)
              : <div style={{ fontSize: 11, color: "#94a3b8", textAlign: "center", padding: "20px 0", lineHeight: 1.7 }}>Intelligence data loading…<br /><span style={{ fontSize: 10 }}>Updates every 60 seconds</span></div>
            }
            <div style={{ marginTop: 8, padding: "5px 8px", borderRadius: 6, background: "#f8fafc", border: "1px solid #e2e8f0", fontSize: 10, color: "#64748b" }}>
              Baseline: <strong style={{ color: baseline?.status === "active" ? "#10b981" : "#f59e0b" }}>{baseline?.status || "learning"}</strong>
              {baseline?.current_hour != null && ` · Hour ${baseline.current_hour}`}
            </div>
          </div>
        )}

        {/* HEALTH TAB */}
        {activeTab === "health" && (
          <div style={{ padding: "8px 10px", height: "100%", overflowY: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
              <HealthArc score={healthScore} color={headerColor} size={80} />
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: headerColor }}>{health?.health_label || overallStatus}</div>
                {health?.maintenance_due && <div style={{ fontSize: 10, color: "#92400e", background: "#fef3c7", padding: "2px 6px", borderRadius: 4, marginTop: 3 }}>⚠️ {health?.maintenance_reason || "Maintenance recommended"}</div>}
              </div>
            </div>
            <ScoreBar label="Uptime"    score={health?.uptime_score} />
            <ScoreBar label="Alarm"     score={health?.alarm_score} />
            <ScoreBar label="Stability" score={health?.stability_score} />
            <ScoreBar label="Freshness" score={health?.freshness_score} />
            {faults.length > 0 && (
              <>
                <div style={{ height: 1, background: "#f1f5f9", margin: "8px 0" }} />
                <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 5 }}>Fault diagnosis</div>
                {faults.map((f, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 6, padding: "5px 8px", borderRadius: 6, marginBottom: 4, background: f.severity === "CRITICAL" ? "#fef2f2" : "#fffbeb", border: `1px solid ${f.severity === "CRITICAL" ? "#fecaca" : "#fde68a"}`, fontSize: 11, color: f.severity === "CRITICAL" ? "#dc2626" : "#92400e" }}>
                    <span style={{ fontWeight: 700, flexShrink: 0 }}>[{f.component}]</span>
                    <span>{f.message}</span>
                  </div>
                ))}
              </>
            )}
            {activeAlarms.length > 0 && (
              <>
                <div style={{ height: 1, background: "#f1f5f9", margin: "8px 0" }} />
                <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 5 }}>Active alarms ({activeAlarms.length})</div>
                {activeAlarms.slice(0, 5).map((a, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", borderRadius: 6, marginBottom: 3, background: ["CRITICAL","MAJOR"].includes(a.severity) ? "#fef2f2" : "#fffbeb", border: `1px solid ${["CRITICAL","MAJOR"].includes(a.severity) ? "#fecaca" : "#fde68a"}`, fontSize: 11, color: a.severity === "CRITICAL" ? "#dc2626" : "#92400e" }}>
                    <div style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, background: a.severity === "CRITICAL" ? "#ef4444" : a.severity === "MAJOR" ? "#f97316" : "#f59e0b" }} />
                    <span style={{ flex: 1 }}>{a.alarm_name || a.alarm_type || a.message}</span>
                    <span style={{ fontSize: 9, opacity: 0.7 }}>{a.severity}</span>
                  </div>
                ))}
              </>
            )}
            {faults.length === 0 && activeAlarms.length === 0 && (
              <div style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "#f0fdf4", border: "1px solid #bbf7d0", fontSize: 11, color: "#166534" }}>
                ✓ No faults detected — all parameters within normal range
              </div>
            )}
          </div>
        )}

        {/* EFFICIENCY TAB */}
        {activeTab === "efficiency" && (
          <div style={{ padding: "8px 10px", height: "100%", overflowY: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
              <HealthArc score={liveEff} color={efficiencyColor(liveEff)} size={80} label="%" />
              <div>
                <div style={{ fontSize: 20, fontWeight: 700, color: efficiencyColor(liveEff), lineHeight: 1.1 }}>{liveEff != null ? `${liveEff.toFixed(1)}%` : "—"}</div>
                <div style={{ fontSize: 10, color: "#64748b" }}>Thermodynamic efficiency</div>
                <div style={{ fontSize: 10, fontWeight: 600, color: efficiencyColor(liveEff) }}>{liveEff == null ? "Configure sensor keys" : liveEff >= 80 ? "Excellent" : liveEff >= 65 ? "Good" : liveEff >= 50 ? "Warning — degrading" : "Critical — overhaul needed"}</div>
              </div>
            </div>

            {rulDays != null && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 10px", borderRadius: 8, background: rulDays < 14 ? "#fef2f2" : rulDays < 30 ? "#fffbeb" : "#f0fdf4", border: `1px solid ${rulDays < 14 ? "#fecaca" : rulDays < 30 ? "#fde68a" : "#bbf7d0"}`, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.5px" }}>Remaining Useful Life</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: rulDays < 14 ? "#ef4444" : rulDays < 30 ? "#f59e0b" : "#10b981" }}>~{Math.round(rulDays)} days</div>
                  {degradationRate != null && <div style={{ fontSize: 10, color: "#64748b" }}>Declining {Math.abs(degradationRate).toFixed(2)}% / day</div>}
                </div>
                {rulConfidence && <span style={{ fontSize: 9, fontWeight: 700, padding: "2px 8px", borderRadius: 20, background: "#e0f2fe", color: "#0369a1", border: "1px solid #bae6fd" }}>{rulConfidence} confidence</span>}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 5, marginBottom: 8 }}>
              {[{ label: "Inlet T1", value: T1, unit: "°C" }, { label: "Outlet T2", value: T2, unit: "°C" }, { label: "ΔT", value: dT, unit: "°C" }, { label: "Head H", value: H, unit: "m" }].map(({ label, value, unit }) => (
                <div key={label} style={{ padding: "5px 8px", background: "#f8fafc", borderRadius: 7, border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: 9, color: "#94a3b8" }}>{label}</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: value != null ? "#0f172a" : "#cbd5e1" }}>{value != null ? `${parseFloat(value).toFixed(2)} ${unit}` : `— ${unit}`}</div>
                </div>
              ))}
            </div>

            <div style={{ padding: "6px 8px", background: "#f0f9ff", borderRadius: 7, border: "1px solid #bae6fd", fontSize: 10, color: "#0369a1", fontFamily: "monospace", marginBottom: 8 }}>
              η = (H·g) / (Cp·ΔT + H·g)
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#94a3b8", marginBottom: 3 }}>
              <span style={{ color: "#ef4444" }}>Poor &lt;50%</span>
              <span style={{ color: "#f59e0b" }}>Fair 50–65%</span>
              <span style={{ color: "#10b981" }}>Good 65–80%</span>
              <span style={{ color: "#a5b4fc" }}>Excellent &gt;80%</span>
            </div>
            <div style={{ height: 6, background: "#f1f5f9", borderRadius: 3, overflow: "hidden", marginBottom: 8 }}>
              <div style={{ height: "100%", borderRadius: 3, transition: "width 0.5s", width: `${liveEff || 0}%`, background: efficiencyColor(liveEff) }} />
            </div>

            <div style={{ padding: "7px 9px", borderRadius: 7, fontSize: 10, lineHeight: 1.7, background: liveEff == null ? "#f8fafc" : liveEff >= 65 ? "#f0fdf4" : liveEff >= 50 ? "#fffbeb" : "#fef2f2", border: `1px solid ${liveEff == null ? "#e2e8f0" : liveEff >= 65 ? "#bbf7d0" : liveEff >= 50 ? "#fde68a" : "#fecaca"}`, color: liveEff == null ? "#94a3b8" : liveEff >= 65 ? "#166534" : liveEff >= 50 ? "#92400e" : "#dc2626" }}>
              {liveEff == null ? "Set key_temp_inlet and key_temp_outlet in widget config to enable thermodynamic efficiency calculation."
                : liveEff >= 80 ? "Pump is near-peak condition. Continue routine monitoring."
                : liveEff >= 65 ? "Operating within normal range. Monitor monthly for degradation trend."
                : liveEff >= 50 ? "⚠️ Efficiency degrading. Likely impeller wear or internal leakage. Inspect within 2 weeks."
                : "🔴 Severe efficiency loss. Impeller damage or seal failure. Schedule overhaul immediately."}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
