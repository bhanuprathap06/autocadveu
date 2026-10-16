"use client";

import {
  Circle, CircleDot, Equal, Grid3x3, LocateFixed, Maximize, MousePointer2, Move, PenLine, Ruler, Spline, Square, Trash2, Waypoints,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiPath, errorMessage, post } from "@/api/client";
import type { Parameter, ProfileInfo, SkConstraint, SketchEntity, SketchParams, SketchSolve } from "@/api/types";
import { StatusBadge } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { fmt, uid } from "@/lib/mesh";

type Pt = [number, number];
type Tool = "select" | "line" | "rect" | "circle" | "arc" | "point" | "spline";
type CType = SkConstraint["type"];
interface SolveOut { solve: SketchSolve; profile: ProfileInfo; sketch: SketchParams; parameter_errors: Record<string, string> }
interface View { cx: number; cy: number; s: number }

type Draft =
  | { kind: "line"; chain: string[] }
  | { kind: "rect"; a: Pt; aId?: string }
  | { kind: "circle"; c: Pt; cId?: string }
  | { kind: "arc"; c: Pt; cId?: string; s?: Pt; sId?: string }
  | { kind: "spline"; ids: string[] }
  | null;

type Drag =
  | { kind: "point"; id: string; moved: boolean }
  | { kind: "radius"; id: string; moved: boolean }
  | { kind: "label"; id: string; moved: boolean }
  | { kind: "pan"; sx: number; sy: number; view: View; moved: boolean; button: number }
  | { kind: "box"; a: Pt; b: Pt }
  | null;

const GLYPH: Partial<Record<CType, string>> = {
  horizontal: "H", vertical: "V", parallel: "∥", perpendicular: "⊥", tangent: "T", concentric: "◎", equal: "=",
  symmetric: "⇋", midpoint: "M", coincident: "•", point_on_line: "•", point_on_circle: "•", fix: "⚓",
};
const DIMENSIONAL = new Set<CType>(["coordinate_x", "coordinate_y", "distance", "horizontal_distance", "vertical_distance", "length",
  "radius", "diameter", "angle"]);

const d2 = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function segDist(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
function lineDist(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const n = Math.hypot(dx, dy) || 1;
  return Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / n;
}
function arcSpan(c: Pt, s: Pt, e: Pt): [number, number] {
  const a0 = Math.atan2(s[1] - c[1], s[0] - c[0]);
  let a1 = Math.atan2(e[1] - c[1], e[0] - c[0]);
  if (a1 <= a0 + 1e-12) a1 += 2 * Math.PI;
  return [a0, a1];
}
function inSpan(t: number, a0: number, a1: number): boolean {
  while (t < a0) t += 2 * Math.PI;
  while (t > a0 + 2 * Math.PI) t -= 2 * Math.PI;
  return t <= a1;
}
function catmull(pts: Pt[], closed: boolean): Pt[] {
  if (pts.length < 3 && !closed) return pts;
  const P = closed ? [pts[pts.length - 1], ...pts, pts[0], pts[1]] : [[2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1]] as Pt, ...pts,
    [2 * pts[pts.length - 1][0] - pts[pts.length - 2][0], 2 * pts[pts.length - 1][1] - pts[pts.length - 2][1]] as Pt];
  const out: Pt[] = [];
  const segs = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < segs; i++) {
    const [p0, p1, p2, p3] = [P[i], P[i + 1], P[i + 2], P[i + 3]];
    for (let k = 0; k < 16; k++) {
      const t = k / 16, t2 = t * t, t3 = t2 * t;
      out.push([0, 1].map((j) => 0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
        + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)) as Pt);
    }
  }
  out.push(closed ? pts[0] : pts[pts.length - 1]);
  return out;
}

function pointsOf(sk: SketchParams): Record<string, Pt> {
  const m: Record<string, Pt> = {};
  for (const e of sk.entities) if (e.type === "point") m[e.id] = [e.x, e.y];
  return m;
}
function entityPointIds(e: SketchEntity): string[] {
  switch (e.type) {
    case "line": return [e.p1, e.p2];
    case "circle": return [e.center];
    case "arc": return [e.center, e.start, e.end];
    case "ellipse": return [e.center];
    case "spline": return [...e.points];
    default: return [];
  }
}

async function solveRemote(docId: string, sketch: SketchParams, parameters: Parameter[], drag?: Record<string, Pt>): Promise<SolveOut> {
  return post<SolveOut>(apiPath("/api/v1/documents/{document_id}/sketch/solve", { document_id: docId }), {
    sketch, parameters: parameters.map((p) => ({ name: p.name, expression: p.expression })), drag: drag ?? null,
  });
}

export interface SketchEditorProps {
  docId: string;
  params: SketchParams;
  parameters: Parameter[];
  readonly: boolean;
  title: string;
  planeLabel: string;
  onCommit: (p: SketchParams, label: string) => void;
  onClose: () => void;
}

export function SketchEditor({ docId, params, parameters, readonly, title, planeLabel, onCommit, onClose }: SketchEditorProps) {
  const [sk, setSk] = useState<SketchParams>(params);
  const [res, setRes] = useState<SolveOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [solveError, setSolveError] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [construction, setConstruction] = useState(false);
  const [gridSnap, setGridSnap] = useState(true);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<string | null>(null);
  const [cursor, setCursor] = useState<{ p: Pt; snapId?: string; on?: string } | null>(null);
  const [draft, setDraft] = useState<Draft>(null);
  const [drag, setDrag] = useState<Drag>(null);
  const [view, setView] = useState<View>({ cx: 0, cy: 0, s: 2 });
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [dimEdit, setDimEdit] = useState<{ id: string; text: string; at: Pt } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  const committed = useRef<string>(JSON.stringify(params));
  const space = useRef(false);
  const dragSolve = useRef<{ inflight: boolean; pending: Record<string, Pt> | null }>({ inflight: false, pending: null });

  const P = useMemo(() => pointsOf(sk), [sk]);
  const ent = useMemo(() => Object.fromEntries(sk.entities.map((e) => [e.id, e])), [sk]);
  const taken = useCallback(() => new Set([...sk.entities.map((e) => e.id), ...sk.constraints.map((c) => c.id)]), [sk]);

  /* ------------------------------------------------------------ view transforms */
  const toS = useCallback((p: Pt): Pt => [(p[0] - view.cx) * view.s + size.w / 2, size.h / 2 - (p[1] - view.cy) * view.s], [view, size]);
  const toW = useCallback((sx: number, sy: number): Pt => [(sx - size.w / 2) / view.s + view.cx, (size.h / 2 - sy) / view.s + view.cy], [view, size]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.max(200, e.contentRect.width), h: Math.max(200, e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback((s: SketchParams = sk) => {
    const pts = Object.values(pointsOf(s));
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const grow = (x: number, y: number, r = 0) => { x0 = Math.min(x0, x - r); y0 = Math.min(y0, y - r); x1 = Math.max(x1, x + r); y1 = Math.max(y1, y + r); };
    pts.forEach(([x, y]) => grow(x, y));
    for (const e of s.entities) if (e.type === "circle") { const c = pointsOf(s)[e.center]; if (c) grow(c[0], c[1], e.r); }
    if (!Number.isFinite(x0)) { x0 = -100; y0 = -100; x1 = 100; y1 = 100; }
    const w = Math.max(x1 - x0, 20), h = Math.max(y1 - y0, 20);
    const el = wrapRef.current?.getBoundingClientRect();
    const W = el?.width ?? size.w, H = el?.height ?? size.h;
    setView({ cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, s: Math.min(W / (w * 1.35), H / (h * 1.35)) });
  }, [sk, size]);

  /* ------------------------------------------------------------ solving */
  const runSolve = useCallback(async (next: SketchParams, label: string | null, opts: { revertTo?: SketchParams; added?: string[]; drag?: Record<string, Pt> } = {}) => {
    const my = ++seq.current;
    setBusy(true);
    try {
      const out = await solveRemote(docId, next, parameters, opts.drag);
      if (my !== seq.current) return out;
      setSolveError(null);
      const bad = (opts.added ?? []).filter((id) => out.solve.conflicting.includes(id) || out.solve.redundant.includes(id) || out.solve.constraint_errors[id]);
      if (bad.length && opts.revertTo) {
        const why = bad.map((id) => out.solve.constraint_errors[id] ?? (out.solve.conflicting.includes(id) ? "conflicts with existing constraints" : "is redundant (already implied)")).join("; ");
        toast.warn("Constraint not added", `The new constraint ${why}. Remove a conflicting constraint or edit a dimension instead.`);
        setSk(opts.revertTo);
        void runSolve(opts.revertTo, null);
        return out;
      }
      setRes(out);
      const solved = out.solve.converged ? out.sketch : next;
      setSk(solved);
      if (label && !readonly) {
        committed.current = JSON.stringify(solved);
        onCommit(solved, label);
      }
      return out;
    } catch (e) {
      if (my === seq.current) setSolveError(errorMessage(e));
      return null;
    } finally {
      if (my === seq.current) setBusy(false);
    }
  }, [docId, parameters, onCommit, readonly]);

  // External changes (undo/redo, collaborator reload) replace the working copy.
  useEffect(() => {
    const s = JSON.stringify(params);
    if (s === committed.current) return;
    committed.current = s;
    setSk(params);
    void runSolve(params, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  useEffect(() => {
    void runSolve(params, null);
    requestAnimationFrame(() => fit(params));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mutate = useCallback((fn: (s: SketchParams) => void, label: string, added?: string[]) => {
    if (readonly) return;
    const before = sk;
    const next = structuredClone(sk);
    fn(next);
    setSk(next);
    void runSolve(next, label, { revertTo: added?.length ? before : undefined, added });
  }, [sk, readonly, runSolve]);

  /* ------------------------------------------------------------ hit testing */
  const hitTest = useCallback((sx: number, sy: number, exclude: Set<string> = new Set()): string | null => {
    const tol = 7;
    let best: string | null = null, bd = tol;
    for (const e of sk.entities) {
      if (e.type !== "point" || exclude.has(e.id)) continue;
      const s = toS([e.x, e.y]);
      const d = Math.hypot(s[0] - sx, s[1] - sy);
      if (d < bd) { bd = d; best = e.id; }
    }
    if (best) return best;
    const w = toW(sx, sy);
    bd = tol / view.s;
    for (const e of sk.entities) {
      if (exclude.has(e.id)) continue;
      let d = Infinity;
      if (e.type === "line" && P[e.p1] && P[e.p2]) d = segDist(w, P[e.p1], P[e.p2]);
      else if (e.type === "circle" && P[e.center]) d = Math.abs(d2(w, P[e.center]) - e.r);
      else if (e.type === "arc" && P[e.center] && P[e.start] && P[e.end]) {
        const c = P[e.center], r = d2(c, P[e.start]);
        const [a0, a1] = arcSpan(c, P[e.start], P[e.end]);
        if (inSpan(Math.atan2(w[1] - c[1], w[0] - c[0]), a0, a1)) d = Math.abs(d2(w, c) - r);
      } else if (e.type === "ellipse" && P[e.center]) {
        const c = P[e.center], rot = (e.rotation * Math.PI) / 180;
        const x = (w[0] - c[0]) * Math.cos(-rot) - (w[1] - c[1]) * Math.sin(-rot);
        const y = (w[0] - c[0]) * Math.sin(-rot) + (w[1] - c[1]) * Math.cos(-rot);
        d = Math.abs(Math.hypot(x / e.rx, y / e.ry) - 1) * Math.min(e.rx, e.ry);
      } else if (e.type === "spline") {
        const pts = e.points.map((p) => P[p]).filter(Boolean);
        const poly = catmull(pts, e.closed);
        for (let i = 0; i < poly.length - 1; i++) d = Math.min(d, segDist(w, poly[i], poly[i + 1]));
      }
      if (d < bd) { bd = d; best = e.id; }
    }
    return best;
  }, [sk, P, toS, toW, view.s]);

  const gridStep = useMemo(() => {
    const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
    return steps.find((g) => g * view.s >= 10) ?? 5000;
  }, [view.s]);

  const snap = useCallback((sx: number, sy: number, exclude?: Set<string>) => {
    const h = hitTest(sx, sy, exclude);
    if (h && ent[h]?.type === "point") return { p: P[h], snapId: h };
    let p = toW(sx, sy);
    const on = h && ent[h] && ent[h].type !== "point" ? h : undefined;
    if (gridSnap && !on) p = [Math.round(p[0] / gridStep) * gridStep, Math.round(p[1] / gridStep) * gridStep];
    return { p, on };
  }, [hitTest, ent, P, toW, gridSnap, gridStep]);

  /* ------------------------------------------------------------ creation helpers */
  const addPoint = (s: SketchParams, ids: Set<string>, p: Pt, existing?: string, on?: string, cons?: SkConstraint[]): string => {
    if (existing) return existing;
    const id = uid("p", ids); ids.add(id);
    s.entities.push({ id, type: "point", x: p[0], y: p[1], construction: false });
    if (on && cons) {
      const t = ent[on]?.type;
      const cid = uid("c", ids); ids.add(cid);
      if (t === "line" || t === "circle" || t === "arc") cons.push({ id: cid, type: "coincident", refs: [id, on], value: null, label: null });
    }
    return id;
  };

  const finishDraft = useCallback(() => {
    if (draft?.kind === "spline" && draft.ids.length >= 2) {
      mutate((s) => {
        const ids = taken();
        s.entities.push({ id: uid("s", ids), type: "spline", points: draft.ids, closed: false, construction });
      }, "Add spline");
    } else if ((draft?.kind === "spline" || draft?.kind === "line") && sk) {
      // Drop provisional points that never became part of a curve.
      const pending = new Set(draft.kind === "spline" ? draft.ids : draft.chain);
      const used = new Set([...sk.entities.flatMap(entityPointIds), ...sk.constraints.flatMap((c) => c.refs)]);
      const orphan = [...pending].filter((p) => !used.has(p));
      if (orphan.length) {
        const next = { ...sk, entities: sk.entities.filter((e) => !orphan.includes(e.id)) };
        setSk(next);
        void runSolve(next, null);
      }
    }
    setDraft(null);
  }, [draft, mutate, taken, construction, sk, runSolve]);

  const click = (sx: number, sy: number, shift: boolean) => {
    const sn = snap(sx, sy);
    if (tool === "select") {
      const h = hitTest(sx, sy);
      if (!h) { if (!shift) setSel(new Set()); return; }
      setSel((cur) => { const n = new Set(shift ? cur : []); if (shift && n.has(h)) n.delete(h); else n.add(h); return n; });
      return;
    }
    if (readonly) return;
    if (tool === "point") {
      mutate((s) => {
        const ids = taken();
        const cons: SkConstraint[] = [];
        const id = addPoint(s, ids, sn.p, undefined, sn.on, cons);
        (s.entities.find((e) => e.id === id) as any).construction = construction;
        s.constraints.push(...cons);
      }, "Add point");
      return;
    }
    if (tool === "line") {
      const chain = draft?.kind === "line" ? draft.chain : [];
      const ids = taken();
      const next = structuredClone(sk);
      const cons: SkConstraint[] = [];
      const pid = addPoint(next, ids, sn.p, sn.snapId, sn.on, cons);
      const last = chain[chain.length - 1];
      if (last && last === pid) return;
      if (last) {
        const a = pointsOf(next)[last], b = pointsOf(next)[pid];
        const lid = uid("l", ids); ids.add(lid);
        next.entities.push({ id: lid, type: "line", p1: last, p2: pid, construction });
        const dx = Math.abs(b[0] - a[0]), dy = Math.abs(b[1] - a[1]);
        if (dx > 1e-9 && dy / dx < Math.tan((2.5 * Math.PI) / 180)) cons.push({ id: uid("c", ids), type: "horizontal", refs: [lid], value: null, label: null });
        else if (dy > 1e-9 && dx / dy < Math.tan((2.5 * Math.PI) / 180)) cons.push({ id: uid("c", ids), type: "vertical", refs: [lid], value: null, label: null });
      }
      next.constraints.push(...cons);
      setSk(next);
      void runSolve(next, last ? "Add line" : null);
      const closed = chain.length > 1 && pid === chain[0];
      setDraft(closed ? null : { kind: "line", chain: [...chain, pid] });
      return;
    }
    if (tool === "rect") {
      if (draft?.kind !== "rect") { setDraft({ kind: "rect", a: sn.p, aId: sn.snapId }); return; }
      const a = draft.a, b = sn.p;
      if (Math.abs(a[0] - b[0]) < 1e-6 || Math.abs(a[1] - b[1]) < 1e-6) return;
      mutate((s) => {
        const ids = taken();
        const p1 = addPoint(s, ids, a, draft.aId);
        const p2 = addPoint(s, ids, [b[0], a[1]]);
        const p3 = addPoint(s, ids, b, sn.snapId);
        const p4 = addPoint(s, ids, [a[0], b[1]]);
        const ls = [[p1, p2], [p2, p3], [p3, p4], [p4, p1]].map(([u, v]) => {
          const id = uid("l", ids); ids.add(id);
          s.entities.push({ id, type: "line", p1: u, p2: v, construction });
          return id;
        });
        (["horizontal", "vertical", "horizontal", "vertical"] as CType[]).forEach((t, i) => {
          const id = uid("c", ids); ids.add(id);
          s.constraints.push({ id, type: t, refs: [ls[i]], value: null, label: null });
        });
      }, "Add rectangle");
      setDraft(null);
      return;
    }
    if (tool === "circle") {
      if (draft?.kind !== "circle") { setDraft({ kind: "circle", c: sn.p, cId: sn.snapId }); return; }
      const r = d2(draft.c, sn.p);
      if (r < 1e-6) return;
      mutate((s) => {
        const ids = taken();
        const c = addPoint(s, ids, draft.c, draft.cId);
        s.entities.push({ id: uid("k", ids), type: "circle", center: c, r, construction });
      }, "Add circle");
      setDraft(null);
      return;
    }
    if (tool === "arc") {
      if (draft?.kind !== "arc") { setDraft({ kind: "arc", c: sn.p, cId: sn.snapId }); return; }
      if (!draft.s) { if (d2(draft.c, sn.p) > 1e-6) setDraft({ ...draft, s: sn.p, sId: sn.snapId }); return; }
      const r = d2(draft.c, draft.s);
      const ang = Math.atan2(sn.p[1] - draft.c[1], sn.p[0] - draft.c[0]);
      const e: Pt = sn.snapId ? sn.p : [draft.c[0] + r * Math.cos(ang), draft.c[1] + r * Math.sin(ang)];
      mutate((s) => {
        const ids = taken();
        const c = addPoint(s, ids, draft.c, draft.cId);
        const st = addPoint(s, ids, draft.s!, draft.sId);
        const en = addPoint(s, ids, e, sn.snapId);
        s.entities.push({ id: uid("a", ids), type: "arc", center: c, start: st, end: en, construction });
      }, "Add arc");
      setDraft(null);
      return;
    }
    if (tool === "spline") {
      const cur = draft?.kind === "spline" ? draft.ids : [];
      if (cur.length >= 2 && sn.snapId === cur[0]) {
        mutate((s) => {
          const ids = taken();
          s.entities.push({ id: uid("s", ids), type: "spline", points: cur, closed: true, construction });
        }, "Add closed spline");
        setDraft(null);
        return;
      }
      const ids = taken();
      const next = structuredClone(sk);
      const pid = addPoint(next, ids, sn.p, sn.snapId);
      (next.entities.find((e) => e.id === pid) as any).construction = true;
      setSk(next);
      void runSolve(next, null);
      setDraft({ kind: "spline", ids: [...cur, pid] });
    }
  };

  /* ------------------------------------------------------------ constraints */
  const selIds = [...sel].filter((id) => ent[id]);
  const kinds = selIds.map((id) => ent[id].type);
  const isCurve = (t: string) => t === "circle" || t === "arc";
  const can: Partial<Record<CType, boolean>> = {
    coincident: kinds.length === 2 && kinds.includes("point") && (kinds[0] === "point" ? true : kinds[1] === "point") && !kinds.includes("spline"),
    horizontal: (kinds.length === 1 && kinds[0] === "line") || (kinds.length === 2 && kinds.every((k) => k === "point")),
    vertical: (kinds.length === 1 && kinds[0] === "line") || (kinds.length === 2 && kinds.every((k) => k === "point")),
    parallel: kinds.length === 2 && kinds.every((k) => k === "line"),
    perpendicular: kinds.length === 2 && kinds.every((k) => k === "line"),
    tangent: kinds.length === 2 && kinds.some(isCurve) && kinds.every((k) => k === "line" || isCurve(k)),
    concentric: kinds.length === 2 && kinds.every(isCurve),
    equal: kinds.length === 2 && (kinds.every((k) => k === "line") || kinds.every(isCurve)),
    symmetric: kinds.length === 3 && kinds.filter((k) => k === "point").length === 2 && kinds.includes("line"),
    midpoint: kinds.length === 2 && kinds.includes("point") && kinds.includes("line"),
    fix: kinds.length >= 1,
  };

  const addConstraint = (type: CType) => {
    if (!can[type]) return;
    let refs = selIds;
    if (type === "coincident" || type === "midpoint") refs = [...selIds].sort((a, b) => (ent[a].type === "point" ? -1 : 1) - (ent[b].type === "point" ? -1 : 1));
    if (type === "symmetric") refs = [...selIds.filter((i) => ent[i].type === "point"), ...selIds.filter((i) => ent[i].type === "line")];
    const ids = taken();
    const id = uid("c", ids);
    mutate((s) => { s.constraints.push({ id, type, refs, value: null, label: null }); }, `Add ${type.replace(/_/g, " ")} constraint`, [id]);
    setSel(new Set());
  };

  const measureFor = (type: CType, refs: string[]): number => {
    const e = refs.map((r) => ent[r]);
    const L = (x: SketchEntity) => (x.type === "line" ? [P[x.p1], P[x.p2]] as [Pt, Pt] : null);
    switch (type) {
      case "length": { const l = L(e[0])!; return d2(l[0], l[1]); }
      case "radius": case "diameter": {
        const x = e[0];
        const r = x.type === "circle" ? x.r : x.type === "arc" ? d2(P[x.center], P[x.start]) : 0;
        return type === "radius" ? r : 2 * r;
      }
      case "coordinate_x": return P[refs[0]][0];
      case "coordinate_y": return P[refs[0]][1];
      case "horizontal_distance": return Math.abs(P[refs[1]][0] - P[refs[0]][0]);
      case "vertical_distance": return Math.abs(P[refs[1]][1] - P[refs[0]][1]);
      case "angle": {
        const a = L(e[0])!, b = L(e[1])!;
        const d1 = [a[1][0] - a[0][0], a[1][1] - a[0][1]], dd = [b[1][0] - b[0][0], b[1][1] - b[0][1]];
        return (Math.atan2(d1[0] * dd[1] - d1[1] * dd[0], d1[0] * dd[0] + d1[1] * dd[1]) * 180) / Math.PI;
      }
      case "distance": {
        if (e[0].type === "point" && e[1].type === "point") return d2(P[refs[0]], P[refs[1]]);
        if (e[0].type === "line" && e[1].type === "line") { const a = L(e[0])!; return lineDist(P[(e[1] as any).p1], a[0], a[1]); }
        const pt = e[0].type === "point" ? refs[0] : refs[1];
        const ln = L(e[0].type === "line" ? e[0] : e[1])!;
        return lineDist(P[pt], ln[0], ln[1]);
      }
      default: return 0;
    }
  };

  const smartDimType = (): CType | null => {
    if (kinds.length === 1 && kinds[0] === "line") return "length";
    if (kinds.length === 1 && kinds[0] === "circle") return "diameter";
    if (kinds.length === 1 && kinds[0] === "arc") return "radius";
    if (kinds.length === 2 && kinds.every((k) => k === "point")) return "distance";
    if (kinds.length === 2 && kinds.includes("point") && kinds.includes("line")) return "distance";
    if (kinds.length === 2 && kinds.every((k) => k === "line")) {
      const [a, b] = selIds.map((i) => ent[i] as Extract<SketchEntity, { type: "line" }>);
      const u = [P[a.p2][0] - P[a.p1][0], P[a.p2][1] - P[a.p1][1]], v = [P[b.p2][0] - P[b.p1][0], P[b.p2][1] - P[b.p1][1]];
      const cr = Math.abs(u[0] * v[1] - u[1] * v[0]) / ((Math.hypot(u[0], u[1]) * Math.hypot(v[0], v[1])) || 1);
      return cr < 1e-6 ? "distance" : "angle";
    }
    return null;
  };

  const addDimension = (type: CType | null) => {
    if (!type || readonly) return;
    const refs = type === "distance" && kinds.includes("line") && kinds.includes("point")
      ? [...selIds].sort((a) => (ent[a].type === "point" ? -1 : 1)) : selIds;
    const v = Math.round(measureFor(type, refs) * 1000) / 1000;
    const ids = taken();
    const id = uid("c", ids);
    mutate((s) => { s.constraints.push({ id, type, refs, value: v, label: null }); }, `Add ${type.replace(/_/g, " ")} dimension`, [id]);
    setSel(new Set());
    setTimeout(() => setDimEdit({ id, text: String(v), at: [0, 0] }), 0);
  };

  const deleteSelection = useCallback(() => {
    if (readonly || !sel.size) return;
    mutate((s) => {
      const delE = new Set([...sel].filter((id) => s.entities.some((e) => e.id === id)));
      const delC = new Set([...sel].filter((id) => s.constraints.some((c) => c.id === id)));
      // Deleting a point removes every entity that uses it.
      for (const e of s.entities) if (e.type !== "point" && entityPointIds(e).some((p) => delE.has(p))) delE.add(e.id);
      const remaining = s.entities.filter((e) => !delE.has(e.id));
      const used = new Set(remaining.flatMap(entityPointIds));
      // Orphaned points of deleted curves go too (unless used elsewhere or explicitly kept as standalone points).
      for (const e of s.entities) if (delE.has(e.id) && e.type !== "point") for (const p of entityPointIds(e)) if (!used.has(p)) delE.add(p);
      s.entities = s.entities.filter((e) => !delE.has(e.id));
      s.constraints = s.constraints.filter((c) => !delC.has(c.id) && !c.refs.some((r) => delE.has(r)));
    }, `Delete ${sel.size} item${sel.size > 1 ? "s" : ""}`);
    setSel(new Set());
  }, [sel, mutate, readonly]);

  const toggleConstruction = () => {
    if (!selIds.length) { setConstruction((c) => !c); return; }
    mutate((s) => { for (const e of s.entities) if (sel.has(e.id)) (e as any).construction = !e.construction; }, "Toggle construction");
  };

  const commitDim = (id: string, text: string) => {
    const t = text.trim();
    if (!t) { setDimEdit(null); return; }
    const n = Number(t);
    const value = /^-?\d*\.?\d+([eE][-+]?\d+)?$/.test(t) && Number.isFinite(n) ? n : t;
    mutate((s) => { const c = s.constraints.find((x) => x.id === id); if (c) c.value = value; }, "Edit dimension", [id]);
    setDimEdit(null);
  };

  /* ------------------------------------------------------------ keyboard */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if (e.key === " ") { space.current = true; return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (e.key === "Escape") { if (draft) finishDraft(); else if (sel.size) setSel(new Set()); else if (tool !== "select") setTool("select"); else onClose(); }
      else if (e.key === "Enter") finishDraft();
      else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelection(); }
      else if (readonly) return;
      else if (k === "l") { setDraft(null); setTool("line"); }
      else if (k === "r") { setDraft(null); setTool("rect"); }
      else if (k === "c") { setDraft(null); setTool("circle"); }
      else if (k === "a") { setDraft(null); setTool("arc"); }
      else if (k === "p") { setDraft(null); setTool("point"); }
      else if (k === "s") { setDraft(null); setTool("spline"); }
      else if (k === "x") toggleConstruction();
      else if (k === "h") addConstraint("horizontal");
      else if (k === "v") addConstraint("vertical");
      else if (k === "d") addDimension(smartDimType());
      else if (k === "f") fit();
    };
    const up = (e: KeyboardEvent) => { if (e.key === " ") space.current = false; };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("keyup", up); };
  });

  /* ------------------------------------------------------------ pointer handling */
  const local = (e: React.PointerEvent | React.MouseEvent | React.WheelEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as Pt;
  };

  const pumpDragSolve = useCallback((dragTargets: Record<string, Pt>, base: SketchParams) => {
    const st = dragSolve.current;
    st.pending = dragTargets;
    if (st.inflight) return;
    const go = async () => {
      if (!st.pending) return;
      const d = st.pending;
      st.pending = null;
      st.inflight = true;
      try { await runSolve(base, null, { drag: d }); } finally { st.inflight = false; if (st.pending) void go(); }
    };
    void go();
  }, [runSolve]);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const [sx, sy] = local(e);
    if (e.button === 1 || e.button === 2 || space.current) {
      setDrag({ kind: "pan", sx, sy, view, moved: false, button: e.button });
      return;
    }
    if (e.button !== 0) return;
    const lab = (e.target as Element).getAttribute?.("data-dim");
    if (lab && !readonly) { setDrag({ kind: "label", id: lab, moved: false }); return; }
    if (tool === "select") {
      const h = hitTest(sx, sy);
      if (h && !readonly && ent[h]?.type === "point") { setDrag({ kind: "point", id: h, moved: false }); return; }
      if (h && !readonly && ent[h]?.type === "circle") { setDrag({ kind: "radius", id: h, moved: false }); return; }
      if (!h) { setDrag({ kind: "box", a: [sx, sy], b: [sx, sy] }); return; }
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const [sx, sy] = local(e);
    if (drag?.kind === "pan") {
      const dx = sx - drag.sx, dy = sy - drag.sy;
      setView({ ...drag.view, cx: drag.view.cx - dx / drag.view.s, cy: drag.view.cy + dy / drag.view.s });
      if (Math.abs(dx) + Math.abs(dy) > 3 && !drag.moved) setDrag({ ...drag, moved: true });
      return;
    }
    if (drag?.kind === "box") { setDrag({ ...drag, b: [sx, sy] }); return; }
    if (drag?.kind === "point") {
      const sn = snap(sx, sy, new Set([drag.id]));
      const next = structuredClone(sk);
      const p = next.entities.find((x) => x.id === drag.id) as Extract<SketchEntity, { type: "point" }>;
      p.x = sn.p[0]; p.y = sn.p[1];
      setSk(next);
      if (!drag.moved) setDrag({ ...drag, moved: true });
      pumpDragSolve({ [drag.id]: sn.p }, next);
      return;
    }
    if (drag?.kind === "radius") {
      const c = ent[drag.id] as Extract<SketchEntity, { type: "circle" }>;
      const r = Math.max(d2(toW(sx, sy), P[c.center]), 1e-3);
      const next = structuredClone(sk);
      (next.entities.find((x) => x.id === drag.id) as any).r = r;
      setSk(next);
      if (!drag.moved) setDrag({ ...drag, moved: true });
      pumpDragSolve({}, next);
      return;
    }
    if (drag?.kind === "label") {
      const w = toW(sx, sy);
      setSk((s) => ({ ...s, constraints: s.constraints.map((c) => (c.id === drag.id ? { ...c, label: w } : c)) }));
      if (!drag.moved) setDrag({ ...drag, moved: true });
      return;
    }
    const sn = tool === "select" ? { p: toW(sx, sy) as Pt } : snap(sx, sy);
    setCursor(sn);
    setHover(hitTest(sx, sy));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const [sx, sy] = local(e);
    const d = drag;
    setDrag(null);
    if (d?.kind === "pan") {
      if (!d.moved && d.button === 2) { if (draft) finishDraft(); else setTool("select"); }
      return;
    }
    if (d?.kind === "box") {
      const [x0, x1] = [Math.min(d.a[0], d.b[0]), Math.max(d.a[0], d.b[0])];
      const [y0, y1] = [Math.min(d.a[1], d.b[1]), Math.max(d.a[1], d.b[1])];
      if (x1 - x0 < 4 && y1 - y0 < 4) { click(sx, sy, e.shiftKey || e.ctrlKey || e.metaKey); return; }
      const inside = (p: Pt) => { const s = toS(p); return s[0] >= x0 && s[0] <= x1 && s[1] >= y0 && s[1] <= y1; };
      const picked = sk.entities.filter((en) => {
        if (en.type === "point") return inside([en.x, en.y]);
        if (en.type === "circle") { const c = P[en.center]; return [[c[0] - en.r, c[1] - en.r], [c[0] + en.r, c[1] + en.r]].every((q) => inside(q as Pt)); }
        return entityPointIds(en).every((p) => P[p] && inside(P[p]));
      }).map((en) => en.id);
      setSel((cur) => new Set([...(e.shiftKey ? cur : []), ...picked]));
      return;
    }
    if (d?.kind === "point" || d?.kind === "radius") {
      if (!d.moved) { click(sx, sy, e.shiftKey || e.ctrlKey || e.metaKey); return; }
      const drag2 = d.kind === "point" ? { [d.id]: P[d.id] } : undefined;
      dragSolve.current.pending = null;
      void runSolve(sk, d.kind === "point" ? "Drag point" : "Drag radius", { drag: drag2 });
      return;
    }
    if (d?.kind === "label") {
      if (!d.moved) { const c = sk.constraints.find((x) => x.id === d.id); if (c) setDimEdit({ id: c.id, text: String(c.value ?? ""), at: [sx, sy] }); return; }
      committed.current = JSON.stringify(sk);
      onCommit(sk, "Move dimension label");
      return;
    }
    if (e.button === 0) click(sx, sy, e.shiftKey || e.ctrlKey || e.metaKey);
  };

  const onWheel = (e: React.WheelEvent) => {
    const [sx, sy] = local(e);
    const w = toW(sx, sy);
    const s = Math.min(Math.max(view.s * Math.exp(-e.deltaY * 0.0015), 0.002), 5000);
    setView({ s, cx: w[0] - (sx - size.w / 2) / s, cy: w[1] + (sy - size.h / 2) / s });
  };

  /* ------------------------------------------------------------ rendering helpers */
  const free = new Set(res?.solve.free_entities ?? []);
  const conflictC = new Set([...(res?.solve.conflicting ?? []), ...(res?.solve.unsatisfied ?? []), ...Object.keys(res?.solve.constraint_errors ?? {})]);
  const redundantC = new Set(res?.solve.redundant ?? []);
  const conflictE = new Set(sk.constraints.filter((c) => conflictC.has(c.id)).flatMap((c) => c.refs));
  const cls = (id: string, base: string, construction?: boolean) => {
    const parts = [base];
    if (construction) parts.push("construction");
    if (conflictE.has(id)) parts.push("conflict");
    else if (free.has(id)) parts.push("free");
    if (sel.has(id)) parts.push("sel");
    else if (hover === id) parts.push("hover");
    return parts.join(" ");
  };

  const pathOf = (e: SketchEntity): string | null => {
    if (e.type === "line") {
      if (!P[e.p1] || !P[e.p2]) return null;
      const a = toS(P[e.p1]), b = toS(P[e.p2]);
      return `M${a[0]},${a[1]}L${b[0]},${b[1]}`;
    }
    if (e.type === "circle") {
      if (!P[e.center]) return null;
      const c = toS(P[e.center]), r = e.r * view.s;
      return `M${c[0] - r},${c[1]}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`;
    }
    if (e.type === "arc") {
      const c = P[e.center], s = P[e.start], en = P[e.end];
      if (!c || !s || !en) return null;
      const r = d2(c, s);
      const [a0, a1] = arcSpan(c, s, en);
      const e2: Pt = [c[0] + r * Math.cos(a1), c[1] + r * Math.sin(a1)];
      const A = toS(s), B = toS(e2);
      return `M${A[0]},${A[1]}A${r * view.s},${r * view.s} 0 ${a1 - a0 > Math.PI ? 1 : 0},0 ${B[0]},${B[1]}`;
    }
    if (e.type === "ellipse") {
      if (!P[e.center]) return null;
      const n = 72, c = P[e.center], rot = (e.rotation * Math.PI) / 180;
      const pts = Array.from({ length: n + 1 }, (_, i) => {
        const t = (2 * Math.PI * i) / n;
        const x = e.rx * Math.cos(t), y = e.ry * Math.sin(t);
        return toS([c[0] + x * Math.cos(rot) - y * Math.sin(rot), c[1] + x * Math.sin(rot) + y * Math.cos(rot)]);
      });
      return "M" + pts.map((p) => `${p[0]},${p[1]}`).join("L");
    }
    if (e.type === "spline") {
      const pts = e.points.map((p) => P[p]).filter(Boolean);
      if (pts.length < 2) return null;
      return "M" + catmull(pts, e.closed).map((p) => toS(p)).map((p) => `${p[0]},${p[1]}`).join("L");
    }
    return null;
  };

  const anchorOf = (c: SkConstraint): Pt | null => {
    const e = ent[c.refs[0]];
    if (!e) return null;
    const mid = (x: SketchEntity): Pt | null => {
      if (x.type === "point") return [x.x, x.y];
      if (x.type === "line" && P[x.p1] && P[x.p2]) return [(P[x.p1][0] + P[x.p2][0]) / 2, (P[x.p1][1] + P[x.p2][1]) / 2];
      if (x.type === "circle" && P[x.center]) return [P[x.center][0] + x.r * 0.707, P[x.center][1] + x.r * 0.707];
      if (x.type === "arc" && P[x.center] && P[x.start]) {
        const cc = P[x.center], r = d2(cc, P[x.start]);
        const [a0, a1] = arcSpan(cc, P[x.start], P[x.end]);
        const m = (a0 + a1) / 2;
        return [cc[0] + r * Math.cos(m), cc[1] + r * Math.sin(m)];
      }
      if ("center" in x && P[(x as any).center]) return P[(x as any).center];
      return null;
    };
    if (c.refs.length >= 2 && ent[c.refs[1]]) {
      const a = mid(e), b = mid(ent[c.refs[1]]);
      if (a && b && (c.type === "distance" || c.type === "horizontal_distance" || c.type === "vertical_distance" || c.type === "symmetric"))
        return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      if (a && c.type === "angle" && b) return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    }
    return mid(e);
  };

  const dimText = (c: SkConstraint) => {
    const v = typeof c.value === "number" ? fmt(c.value, 6) : String(c.value ?? "");
    switch (c.type) {
      case "radius": return `R ${v}`;
      case "diameter": return `⌀ ${v}`;
      case "angle": return `${v}°`;
      case "coordinate_x": return `X ${v}`;
      case "coordinate_y": return `Y ${v}`;
      case "horizontal_distance": return `↔ ${v}`;
      case "vertical_distance": return `↕ ${v}`;
      default: return v;
    }
  };

  // Grid lines in view.
  const grid = useMemo(() => {
    const [x0, y1] = toW(0, 0), [x1, y0] = toW(size.w, size.h);
    const lines: { d: string; major: boolean }[] = [];
    const n = (x1 - x0) / gridStep + (y1 - y0) / gridStep;
    if (n > 600) return lines;
    for (let x = Math.ceil(x0 / gridStep) * gridStep; x <= x1; x += gridStep) {
      const s = toS([x, 0])[0];
      lines.push({ d: `M${s},0V${size.h}`, major: Math.abs(Math.round(x / gridStep)) % 5 === 0 });
    }
    for (let y = Math.ceil(y0 / gridStep) * gridStep; y <= y1; y += gridStep) {
      const s = toS([0, y])[1];
      lines.push({ d: `M0,${s}H${size.w}`, major: Math.abs(Math.round(y / gridStep)) % 5 === 0 });
    }
    return lines;
  }, [toW, toS, size, gridStep]);

  // Glyph offsets so several constraints on one entity do not overlap.
  const glyphs = useMemo(() => {
    const count: Record<string, number> = {};
    return sk.constraints.filter((c) => !DIMENSIONAL.has(c.type) && GLYPH[c.type] && !(c.type === "coincident" && c.refs.every((r) => ent[r]?.type === "point")))
      .map((c) => {
        const a = anchorOf(c);
        if (!a) return null;
        const key = c.refs[0];
        const i = (count[key] = (count[key] ?? 0) + 1) - 1;
        const s = toS(a);
        return { c, x: s[0] + 10 + i * 13, y: s[1] - 10 };
      }).filter(Boolean) as { c: SkConstraint; x: number; y: number }[];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sk, toS, ent]);

  const preview = (() => {
    if (!cursor || readonly) return null;
    const c = cursor.p;
    if (draft?.kind === "line" && draft.chain.length) {
      const a = toS(P[draft.chain[draft.chain.length - 1]] ?? c), b = toS(c);
      return <path className="preview" d={`M${a[0]},${a[1]}L${b[0]},${b[1]}`} />;
    }
    if (draft?.kind === "rect") {
      const a = toS(draft.a), b = toS(c);
      return <rect className="preview" x={Math.min(a[0], b[0])} y={Math.min(a[1], b[1])} width={Math.abs(b[0] - a[0])} height={Math.abs(b[1] - a[1])} />;
    }
    if (draft?.kind === "circle") {
      const a = toS(draft.c);
      return <circle className="preview" cx={a[0]} cy={a[1]} r={d2(draft.c, c) * view.s} />;
    }
    if (draft?.kind === "arc") {
      const a = toS(draft.c);
      if (!draft.s) return <path className="preview" d={`M${a[0]},${a[1]}L${toS(c)[0]},${toS(c)[1]}`} />;
      const r = d2(draft.c, draft.s);
      const ang = Math.atan2(c[1] - draft.c[1], c[0] - draft.c[0]);
      const e: Pt = [draft.c[0] + r * Math.cos(ang), draft.c[1] + r * Math.sin(ang)];
      const [a0, a1] = arcSpan(draft.c, draft.s, e);
      const S = toS(draft.s), E = toS(e);
      return <path className="preview" d={`M${S[0]},${S[1]}A${r * view.s},${r * view.s} 0 ${a1 - a0 > Math.PI ? 1 : 0},0 ${E[0]},${E[1]}`} />;
    }
    if (draft?.kind === "spline" && draft.ids.length) {
      const pts = [...draft.ids.map((i) => P[i]).filter(Boolean), c];
      return <path className="preview" d={"M" + catmull(pts, false).map((p) => toS(p)).map((p) => `${p[0]},${p[1]}`).join("L")} />;
    }
    return null;
  })();

  const status = res?.solve.status ?? "empty";
  const dims = sk.constraints.filter((c) => DIMENSIONAL.has(c.type));
  const cursorReadout = cursor ? `${fmt(cursor.p[0], 5)}, ${fmt(cursor.p[1], 5)} mm` : "";

  const ToolBtn = ({ t, icon, label, k }: { t: Tool; icon: ReactNode; label: string; k: string }) => (
    <button className="btn ghost sm" aria-pressed={tool === t} title={`${label} (${k})`} disabled={readonly && t !== "select"}
      onClick={() => { setDraft(null); setTool(t); }}>{icon}<span className="nowrap">{label}</span></button>
  );
  const ConBtn = ({ t, label, glyph }: { t: CType; label: string; glyph: string }) => (
    <button className="btn ghost sm" disabled={readonly || !can[t]} title={`${label} constraint`} onClick={() => addConstraint(t)}>
      <span className="mono" style={{ width: 12, textAlign: "center" }}>{glyph}</span>{label}
    </button>
  );
  const dimType = smartDimType();

  return (
    <div style={{ display: "grid", gridTemplateRows: "auto auto 1fr auto", height: "100%", minHeight: 0 }}>
      <div className="row wrap" style={{ padding: "6px 8px", gap: 2, borderBottom: "1px solid var(--border)", background: "var(--panel)" }} role="toolbar" aria-label="Sketch tools">
        <strong className="small" style={{ marginRight: 8 }}>{title}</strong>
        <span className="badge" title="Sketch plane">{planeLabel}</span>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--border)", margin: "0 6px" }} />
        <ToolBtn t="select" icon={<MousePointer2 size={14} />} label="Select" k="Esc" />
        <ToolBtn t="line" icon={<PenLine size={14} />} label="Line" k="L" />
        <ToolBtn t="rect" icon={<Square size={14} />} label="Rectangle" k="R" />
        <ToolBtn t="circle" icon={<Circle size={14} />} label="Circle" k="C" />
        <ToolBtn t="arc" icon={<CircleDot size={14} />} label="Arc" k="A" />
        <ToolBtn t="point" icon={<LocateFixed size={14} />} label="Point" k="P" />
        <ToolBtn t="spline" icon={<Spline size={14} />} label="Spline" k="S" />
        <span className="sep" style={{ width: 1, height: 20, background: "var(--border)", margin: "0 6px" }} />
        <button className="btn ghost sm" aria-pressed={construction} disabled={readonly} onClick={toggleConstruction}
          title={selIds.length ? "Toggle construction on selection (X)" : "Draw construction geometry (X)"}><Waypoints size={14} />Construction</button>
        <button className="btn ghost sm" aria-pressed={gridSnap} onClick={() => setGridSnap((g) => !g)} title={`Snap to grid (${gridStep} mm)`}><Grid3x3 size={14} />Snap</button>
        <button className="btn ghost sm" onClick={() => fit()} title="Fit sketch (F)"><Maximize size={14} />Fit</button>
        <div className="grow" />
        {busy && <span className="spinner" aria-label="Solving" />}
        <StatusBadge status={status === "empty" ? "skipped" : status} label={status === "empty" ? "Empty sketch" : undefined} />
        {res && status !== "empty" && <span className="badge" title="Remaining degrees of freedom">DOF {res.solve.dof}</span>}
        <button className="btn primary sm" onClick={onClose}>Finish sketch</button>
      </div>
      <div className="row wrap" style={{ padding: "4px 8px", gap: 2, borderBottom: "1px solid var(--border)", background: "var(--bg-elev)" }} role="toolbar" aria-label="Constraints">
        <ConBtn t="coincident" label="Coincident" glyph="•" />
        <ConBtn t="horizontal" label="Horizontal" glyph="H" />
        <ConBtn t="vertical" label="Vertical" glyph="V" />
        <ConBtn t="parallel" label="Parallel" glyph="∥" />
        <ConBtn t="perpendicular" label="Perpendicular" glyph="⊥" />
        <ConBtn t="tangent" label="Tangent" glyph="T" />
        <ConBtn t="concentric" label="Concentric" glyph="◎" />
        <ConBtn t="equal" label="Equal" glyph="=" />
        <ConBtn t="symmetric" label="Symmetric" glyph="⇋" />
        <ConBtn t="midpoint" label="Midpoint" glyph="M" />
        <ConBtn t="fix" label="Fix" glyph="⚓" />
        <span className="sep" style={{ width: 1, height: 20, background: "var(--border)", margin: "0 6px" }} />
        <button className="btn ghost sm" disabled={readonly || !dimType} onClick={() => addDimension(dimType)} title="Smart dimension (D)"><Ruler size={14} />Dimension</button>
        <button className="btn ghost sm" disabled={readonly || !(kinds.length === 2 && kinds.every((k) => k === "point"))} onClick={() => addDimension("horizontal_distance")}>↔ Horizontal</button>
        <button className="btn ghost sm" disabled={readonly || !(kinds.length === 2 && kinds.every((k) => k === "point"))} onClick={() => addDimension("vertical_distance")}>↕ Vertical</button>
        <button className="btn ghost sm" disabled={readonly || !(kinds.length === 1 && kinds[0] === "point")} onClick={() => addDimension("coordinate_x")}>X</button>
        <button className="btn ghost sm" disabled={readonly || !(kinds.length === 1 && kinds[0] === "point")} onClick={() => addDimension("coordinate_y")}>Y</button>
        <button className="btn ghost sm" disabled={readonly || !(kinds.length === 1 && kinds[0] === "circle")} onClick={() => addDimension("radius")}>R</button>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--border)", margin: "0 6px" }} />
        <button className="btn ghost sm danger" disabled={readonly || !sel.size} onClick={deleteSelection} title="Delete selection (Del)"><Trash2 size={14} />Delete</button>
      </div>
      <div ref={wrapRef} style={{ position: "relative", minHeight: 0, overflow: "hidden" }}>
        <svg ref={svgRef} className="sketch-svg" width={size.w} height={size.h} role="application"
          aria-label={`Sketch editor for ${title}. Status ${status}, ${res?.solve.dof ?? "unknown"} degrees of freedom remaining.`}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onWheel={onWheel}
          onDoubleClick={() => finishDraft()} onContextMenu={(e) => e.preventDefault()}
          onPointerLeave={() => { setCursor(null); setHover(null); }}
          style={{ cursor: drag?.kind === "pan" ? "grabbing" : tool === "select" ? (hover ? "pointer" : "default") : "crosshair" }}>
          {grid.map((g, i) => <path key={i} d={g.d} className={g.major ? "grid-major" : "grid-minor"} />)}
          <path className="axis-x" d={`M0,${toS([0, 0])[1]}H${size.w}`} />
          <path className="axis-y" d={`M${toS([0, 0])[0]},0V${size.h}`} />
          {(res?.profile.regions ?? []).map((r) => {
            const ring = (pts: number[][]) => "M" + pts.map((p) => toS(p as Pt)).map((p) => `${p[0]},${p[1]}`).join("L") + "Z";
            return <path key={r.id} className="region" fillRule="evenodd" d={ring(r.polygon) + r.hole_polygons.map(ring).join("")}
              style={{ fill: r.depth % 2 === 0 ? undefined : "transparent" }} />;
          })}
          {sk.entities.filter((e) => e.type !== "point").map((e) => {
            const d = pathOf(e);
            return d ? <path key={e.id} d={d} className={cls(e.id, "ent", e.construction)} /> : null;
          })}
          {dims.map((c) => {
            const a = anchorOf(c);
            if (!a) return null;
            const A = toS(a);
            const L = c.label ? toS(c.label as Pt) : [A[0] + 18, A[1] - 18];
            const bad = conflictC.has(c.id), red = redundantC.has(c.id);
            return (
              <g key={c.id}>
                <path className="dim-line" d={`M${A[0]},${A[1]}L${L[0]},${L[1]}`} />
                <text data-dim={c.id} x={L[0]} y={L[1]} className="dim-text" fontSize={12} textAnchor="middle" dominantBaseline="middle"
                  style={{ cursor: readonly ? "default" : "move", fill: bad ? "var(--err)" : red ? "var(--warn)" : sel.has(c.id) ? "var(--accent)" : undefined }}
                  onClick={(ev) => { if (ev.shiftKey) setSel((s) => new Set([...s, c.id])); }}>
                  {dimText(c)}{bad ? " ⚠" : ""}
                </text>
              </g>
            );
          })}
          {glyphs.map(({ c, x, y }) => (
            <text key={c.id} x={x} y={y} fontSize={10} className="glyph" textAnchor="middle" dominantBaseline="middle"
              style={{ cursor: "pointer", fill: conflictC.has(c.id) ? "var(--err)" : redundantC.has(c.id) ? "var(--warn)" : sel.has(c.id) ? "var(--accent)" : undefined }}
              onPointerDown={(ev) => { ev.stopPropagation(); setSel((s) => new Set([...(ev.shiftKey ? s : []), c.id])); }}>
              <title>{`${c.type.replace(/_/g, " ")} (${c.refs.join(", ")})${res?.solve.constraint_errors[c.id] ? ": " + res.solve.constraint_errors[c.id] : ""}`}</title>
              {GLYPH[c.type]}
            </text>
          ))}
          {sk.entities.filter((e) => e.type === "point").map((e) => {
            const s = toS([(e as any).x, (e as any).y]);
            const usedByCurve = sk.entities.some((x) => x.type !== "point" && entityPointIds(x).includes(e.id));
            return <circle key={e.id} cx={s[0]} cy={s[1]} r={sel.has(e.id) || hover === e.id ? 4.5 : usedByCurve ? 3 : 3.5}
              className={`pt${free.has(e.id) ? " free" : ""}${sel.has(e.id) ? " sel" : hover === e.id ? " hover" : ""}`}
              style={e.construction && !usedByCurve ? { fill: "#6f88a8" } : undefined} />;
          })}
          {preview}
          {cursor && tool !== "select" && (() => { const s = toS(cursor.p); return <circle cx={s[0]} cy={s[1]} r={cursor.snapId ? 6 : 3} fill="none" stroke={cursor.snapId ? "var(--ok)" : cursor.on ? "var(--warn)" : "var(--accent)"} />; })()}
          {drag?.kind === "box" && (
            <rect x={Math.min(drag.a[0], drag.b[0])} y={Math.min(drag.a[1], drag.b[1])} width={Math.abs(drag.b[0] - drag.a[0])} height={Math.abs(drag.b[1] - drag.a[1])}
              fill="rgba(39,182,236,.08)" stroke="var(--accent)" strokeDasharray="3 2" />
          )}
        </svg>
        {dimEdit && (() => {
          const c = sk.constraints.find((x) => x.id === dimEdit.id);
          const a = c ? anchorOf(c) : null;
          const L = c?.label ? toS(c.label as Pt) : a ? [toS(a)[0] + 18, toS(a)[1] - 18] : dimEdit.at;
          return (
            <div style={{ position: "absolute", left: L[0] - 70, top: L[1] - 16, width: 140 }}>
              <input autoFocus className="input mono" aria-label="Dimension value or expression" value={dimEdit.text}
                onChange={(e) => setDimEdit({ ...dimEdit, text: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") commitDim(dimEdit.id, dimEdit.text); if (e.key === "Escape") setDimEdit(null); e.stopPropagation(); }}
                onBlur={() => commitDim(dimEdit.id, dimEdit.text)} />
            </div>
          );
        })()}
        {draft && (
          <div className="vp-status" style={{ left: "50%", transform: "translateX(-50%)", bottom: 8 }}>
            {draft.kind === "line" && "Click to add points · click the first point to close · Esc / right-click / double-click to finish"}
            {draft.kind === "rect" && "Click the opposite corner"}
            {draft.kind === "circle" && "Click to set the radius"}
            {draft.kind === "arc" && (draft.s ? "Click the arc end point (counter-clockwise)" : "Click the arc start point")}
            {draft.kind === "spline" && "Click to add fit points · click the first point to close · Enter to finish"}
          </div>
        )}
      </div>
      <div className="row wrap small" style={{ padding: "4px 10px", borderTop: "1px solid var(--border)", background: "var(--panel)", minHeight: 28 }} role="status">
        <span className="num muted">{cursorReadout}</span>
        <span className="faint">·</span>
        <span className="muted">{sk.entities.length} entities, {sk.constraints.length} constraints</span>
        {res && <><span className="faint">·</span><span className="muted">{res.profile.regions.length} closed region{res.profile.regions.length === 1 ? "" : "s"}</span></>}
        {res && <><span className="faint">·</span><span className="muted num">solved in {fmt(res.solve.elapsed_ms, 3)} ms</span></>}
        {res?.solve.redundant.length ? <span className="badge warn">Redundant: {res.solve.redundant.join(", ")}</span> : null}
        {res?.solve.conflicting.length ? <span className="badge err">Conflicting: {res.solve.conflicting.join(", ")}</span> : null}
        {res?.profile.errors.map((e, i) => <span key={i} className="badge err" title={e.message}>{e.code.replace(/_/g, " ")}</span>)}
        {res?.profile.warnings.slice(0, 3).map((w, i) => <span key={i} className="badge warn" title={w.message}>{w.code.replace(/_/g, " ")}</span>)}
        {Object.entries(res?.parameter_errors ?? {}).map(([k, v]) => <span key={k} className="badge err" title={v}>Parameter {k}</span>)}
        {solveError && <span className="badge err" title={solveError}>Solver request failed</span>}
        <div className="grow" />
        {readonly ? <span className="badge">Read-only</span> : <span className="faint tiny"><Move size={11} /> Middle/right-drag or Space+drag to pan · wheel to zoom</span>}
      </div>
    </div>
  );
}
