"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import type { SketchFeature, SketchState } from "@/api/types";

/** Draws solved sketch curves on their 3D planes (display only; geometry comes from the server solve). */
export function SketchOverlay({ feature, state, highlight }: { feature: SketchFeature; state: SketchState; highlight?: boolean }) {
  const geom = useMemo(() => {
    const { origin, x_dir: xd, y_dir: yd } = state.plane;
    const to3 = (u: number, v: number) => [origin[0] + u * xd[0] + v * yd[0], origin[1] + u * xd[1] + v * yd[1], origin[2] + u * xd[2] + v * yd[2]];
    const P = state.points;
    const solid: number[] = [], cons: number[] = [];
    const seg = (out: number[], a: number[], b: number[]) => out.push(...to3(a[0], a[1]), ...to3(b[0], b[1]));
    const poly = (out: number[], pts: number[][]) => { for (let i = 0; i < pts.length - 1; i++) seg(out, pts[i], pts[i + 1]); };
    const ring = (c: number[], r: number, a0 = 0, a1 = 2 * Math.PI) => {
      const n = Math.max(12, Math.ceil(((a1 - a0) / (2 * Math.PI)) * 72));
      return Array.from({ length: n + 1 }, (_, i) => { const t = a0 + ((a1 - a0) * i) / n; return [c[0] + r * Math.cos(t), c[1] + r * Math.sin(t)]; });
    };
    for (const e of feature.params.entities) {
      const out = e.construction ? cons : solid;
      if (e.type === "line" && P[e.p1] && P[e.p2]) seg(out, P[e.p1], P[e.p2]);
      else if (e.type === "circle" && P[e.center]) poly(out, ring(P[e.center], state.radii[e.id] ?? e.r));
      else if (e.type === "arc" && P[e.center] && P[e.start] && P[e.end]) {
        const c = P[e.center], s = P[e.start], en = P[e.end];
        const r = Math.hypot(s[0] - c[0], s[1] - c[1]);
        const a0 = Math.atan2(s[1] - c[1], s[0] - c[0]);
        let a1 = Math.atan2(en[1] - c[1], en[0] - c[0]);
        if (a1 <= a0) a1 += 2 * Math.PI;
        poly(out, ring(c, r, a0, a1));
      } else if (e.type === "spline") poly(out, e.points.map((p) => P[p]).filter(Boolean));
      else if (e.type === "ellipse" && P[e.center]) {
        const c = P[e.center], rot = (e.rotation * Math.PI) / 180;
        poly(out, Array.from({ length: 73 }, (_, i) => {
          const t = (2 * Math.PI * i) / 72, x = e.rx * Math.cos(t), y = e.ry * Math.sin(t);
          return [c[0] + x * Math.cos(rot) - y * Math.sin(rot), c[1] + x * Math.sin(rot) + y * Math.cos(rot)];
        }));
      }
    }
    const mk = (arr: number[]) => { const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3)); return g; };
    return { solid: mk(solid), cons: mk(cons) };
  }, [feature, state]);
  useEffect(() => () => { geom.solid.dispose(); geom.cons.dispose(); }, [geom]);
  return (
    <group renderOrder={2}>
      <lineSegments geometry={geom.solid}><lineBasicMaterial color={highlight ? "#27b6ec" : "#8fb4ff"} depthTest={!highlight} transparent opacity={highlight ? 1 : 0.8} /></lineSegments>
      <lineSegments geometry={geom.cons}><lineDashedMaterial color="#6f88a8" dashSize={2} gapSize={1.5} transparent opacity={0.6} /></lineSegments>
    </group>
  );
}
