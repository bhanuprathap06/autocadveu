"use client";

import { GizmoHelper, GizmoViewport, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { fmt } from "@/lib/mesh";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export interface Field {
  positions: number[];
  indices: number[];
  displacement?: number[];
  displacement_range?: [number, number];
  von_mises?: number[];
  von_mises_range?: [number, number];
  temperature?: number[];
  temperature_range?: [number, number];
}

const STOPS = ["#2c3ec9", "#1fa2d6", "#33c48b", "#e4d13f", "#e8792c", "#d6263c"].map((c) => new THREE.Color(c));
export function colormap(t: number, out = new THREE.Color()): THREE.Color {
  const x = Math.min(1, Math.max(0, t)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  return out.copy(STOPS[i]).lerp(STOPS[i + 1], x - i);
}

export type Quantity = "von_mises" | "displacement" | "temperature";

function Fit({ geom }: { geom: THREE.BufferGeometry }) {
  const { camera, controls } = useThree() as unknown as { camera: THREE.PerspectiveCamera; controls: { target: THREE.Vector3; update: () => void } | null };
  useEffect(() => {
    geom.computeBoundingSphere();
    const s = geom.boundingSphere!;
    const d = s.radius / Math.sin((camera.fov * Math.PI) / 360) * 1.15;
    camera.position.copy(s.center.clone().add(new THREE.Vector3(1, -1, 0.8).normalize().multiplyScalar(d)));
    camera.near = s.radius / 500; camera.far = s.radius * 100;
    camera.up.set(0, 0, 1);
    camera.lookAt(s.center);
    camera.updateProjectionMatrix();
    if (controls) { controls.target.copy(s.center); controls.update(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geom.uuid, controls]);
  return null;
}

export function FieldViewer({ field, quantity, deform, range, onProbe }: {
  field: Field; quantity: Quantity; deform: number; range: [number, number]; onProbe?: (v: { value: number; at: number[] } | null) => void;
}) {
  const n = field.positions.length / 3;
  const values = useMemo(() => {
    if (quantity === "displacement" && field.displacement) {
      const v = new Float32Array(n);
      for (let i = 0; i < n; i++) v[i] = Math.hypot(field.displacement[3 * i], field.displacement[3 * i + 1], field.displacement[3 * i + 2]);
      return v;
    }
    return Float32Array.from((field[quantity] as number[] | undefined) ?? new Array(n).fill(0));
  }, [field, quantity, n]);

  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const P = Float32Array.from(field.positions);
    if (deform && field.displacement) for (let i = 0; i < P.length; i++) P[i] += field.displacement[i] * deform;
    g.setAttribute("position", new THREE.BufferAttribute(P, 3));
    g.setIndex(field.indices);
    g.computeVertexNormals();
    return g;
  }, [field, deform]);

  useEffect(() => {
    const c = new Float32Array(n * 3);
    const tmp = new THREE.Color();
    const span = range[1] - range[0] || 1;
    for (let i = 0; i < n; i++) { colormap((values[i] - range[0]) / span, tmp); c[3 * i] = tmp.r; c[3 * i + 1] = tmp.g; c[3 * i + 2] = tmp.b; }
    geom.setAttribute("color", new THREE.BufferAttribute(c, 3));
  }, [geom, values, range, n]);
  useEffect(() => () => geom.dispose(), [geom]);

  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => { try { const c = document.createElement("canvas"); setOk(!!(c.getContext("webgl2") || c.getContext("webgl"))); } catch { setOk(false); } }, []);
  if (ok === false) return <div className="empty" style={{ height: "100%" }}>WebGL is unavailable; use the summary values and downloadable result files.</div>;
  if (!ok) return null;

  return (
    <Canvas dpr={[1, 2]} camera={{ fov: 35, position: [200, -200, 160] }} style={{ background: "#0c1015" }}>
      <hemisphereLight args={["#ffffff", "#30363e", 0.9]} />
      <directionalLight position={[300, -400, 600]} intensity={0.9} />
      <mesh geometry={geom}
        onPointerMove={(e) => {
          if (!onProbe || e.face == null) return;
          e.stopPropagation();
          const { a, b, c } = e.face;
          const pos = geom.getAttribute("position") as THREE.BufferAttribute;
          let best = a, bd = Infinity;
          for (const k of [a, b, c]) {
            const d = new THREE.Vector3(pos.getX(k), pos.getY(k), pos.getZ(k)).distanceTo(e.point);
            if (d < bd) { bd = d; best = k; }
          }
          onProbe({ value: values[best], at: [field.positions[3 * best], field.positions[3 * best + 1], field.positions[3 * best + 2]] });
        }}
        onPointerOut={() => onProbe?.(null)}>
        <meshStandardMaterial vertexColors roughness={0.65} metalness={0.05} side={THREE.DoubleSide} />
      </mesh>
      <Fit geom={geom} />
      <OrbitControls makeDefault enableDamping={false} />
      <GizmoHelper alignment="bottom-right" margin={[56, 56]}><GizmoViewport axisColors={["#f0585d", "#3ccf8e", "#27b6ec"]} labelColor="#0a0c0f" /></GizmoHelper>
    </Canvas>
  );
}

export function Legend({ range, unit, label }: { range: [number, number]; unit: string; label: string }) {
  const ticks = [1, 0.75, 0.5, 0.25, 0].map((t) => range[0] + (range[1] - range[0]) * t);
  return (
    <div className="legend" aria-label={`${label} colour scale from ${fmt(range[0])} to ${fmt(range[1])} ${unit}`}>
      <strong>{label}</strong>
      <div className="row" style={{ alignItems: "stretch", gap: 6 }}>
        <div className="bar" />
        <div className="col" style={{ justifyContent: "space-between", gap: 0 }}>{ticks.map((t, i) => <span key={i} className="num">{fmt(t, 4)}</span>)}</div>
      </div>
      <span className="faint">{unit}</span>
    </div>
  );
}
