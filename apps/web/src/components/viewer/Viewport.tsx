"use client";

import { GizmoHelper, GizmoViewport, Html, OrbitControls, OrthographicCamera, PerspectiveCamera } from "@react-three/drei";
import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import type { BodyMesh, EdgeMeta, FaceMeta } from "@/api/types";
import { f32, u32 } from "@/lib/mesh";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export type DisplayMode = "shaded" | "shaded_edges" | "wireframe" | "xray";
export type PickMode = "face" | "edge" | "body";
export type NamedView = "front" | "back" | "top" | "bottom" | "right" | "left" | "iso";

export interface ViewBody {
  key: string; // unique key (body name or instance id)
  label: string;
  mesh: BodyMesh;
  matrix?: number[][]; // 4x4 row-major placement
  color?: string;
  visible?: boolean;
  dim?: boolean;
  explodeOffset?: [number, number, number];
}

export interface PickInfo {
  bodyKey: string;
  kind: "face" | "edge" | "body";
  face?: FaceMeta;
  edge?: EdgeMeta;
  point: [number, number, number];
  screen: { x: number; y: number };
}

export interface Section { enabled: boolean; axis: "x" | "y" | "z"; offset: number; flip: boolean }

export interface ViewportHandle {
  fit: () => void;
  setView: (v: NamedView) => void;
  getCamera: () => { position: number[]; target: number[]; zoom: number };
  setCamera: (c: { position: number[]; target: number[]; zoom?: number }) => void;
  bounds: () => THREE.Box3;
}

interface Props {
  bodies: ViewBody[];
  display?: DisplayMode;
  ortho?: boolean;
  pickMode?: PickMode;
  selected?: Set<string>; // "bodyKey|face:name" or "bodyKey|edge:a|b"
  section?: Section;
  onPick?: (p: PickInfo | null, ev: { shift: boolean; button: number }) => void;
  onHover?: (p: PickInfo | null) => void;
  onContextMenu?: (p: PickInfo | null, at: { x: number; y: number }) => void;
  onCameraChange?: (c: { position: number[]; target: number[] }) => void;
  children?: ReactNode;
  showGrid?: boolean;
  autoFit?: boolean;
  background?: string;
  interactive?: boolean;
  autoRotate?: boolean;
}

export function faceKey(body: string, face: string) { return `${body}|face:${face}`; }
export function edgeKey(body: string, faces: string[]) { return `${body}|edge:${[...faces].sort().join("|")}`; }

function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

export const Viewport = forwardRef<ViewportHandle, Props>(function Viewport(props, ref) {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => setOk(webglAvailable()), []);
  if (ok === null) return <div style={{ width: "100%", height: "100%" }} />;
  if (!ok)
    return (
      <div className="empty" style={{ height: "100%", margin: 12 }} role="alert">
        <strong>3D view unavailable</strong>
        <div className="small">WebGL is disabled or unsupported in this browser. Modelling data, feature history, exports and
          reports remain available; enable hardware acceleration or use a current Chrome, Edge, Firefox or Safari to view geometry.</div>
      </div>
    );
  return (
    <Canvas
      gl={{ antialias: true, preserveDrawingBuffer: false, localClippingEnabled: true } as any}
      dpr={[1, 2]}
      style={{ background: props.background ?? "#0c1015" }}
      onCreated={({ gl }) => { gl.localClippingEnabled = true; }}
      frameloop={props.autoRotate ? "always" : "demand"}
    >
      <Scene {...props} handleRef={ref} />
    </Canvas>
  );
});

function toMatrix(m?: number[][]): THREE.Matrix4 {
  const M = new THREE.Matrix4();
  if (m) M.set(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3], m[2][0], m[2][1], m[2][2], m[2][3], 0, 0, 0, 1);
  return M;
}

const VIEW_DIRS: Record<NamedView, [number, number, number]> = {
  front: [0, -1, 0], back: [0, 1, 0], top: [0, 0, 1], bottom: [0, 0, -1], right: [1, 0, 0], left: [-1, 0, 0], iso: [1, -1, 0.8],
};

function Scene(props: Props & { handleRef: React.ForwardedRef<ViewportHandle> }) {
  const { bodies, display = "shaded_edges", ortho = false, section, showGrid = true, autoFit = true } = props;
  const controls = useRef<any>(null);
  const group = useRef<THREE.Group>(null);
  const { camera, size, invalidate, raycaster } = useThree();
  const fitted = useRef<string>("");

  const clip = useMemo(() => {
    if (!section?.enabled) return [] as THREE.Plane[];
    const n = new THREE.Vector3(section.axis === "x" ? 1 : 0, section.axis === "y" ? 1 : 0, section.axis === "z" ? 1 : 0);
    if (section.flip) n.negate();
    return [new THREE.Plane(n.clone().negate(), section.flip ? -section.offset : section.offset)];
  }, [section?.enabled, section?.axis, section?.offset, section?.flip]);

  const meshBounds = () => {
    const b = new THREE.Box3();
    group.current?.updateMatrixWorld(true);
    group.current?.traverse((o) => { if (((o as THREE.Mesh).isMesh || (o as THREE.LineSegments).isLineSegments) && o.visible) b.expandByObject(o); });
    return b;
  };
  const bounds = () => {
    const b = meshBounds();
    if (b.isEmpty()) b.set(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50));
    return b;
  };

  const place = (dir: [number, number, number]) => {
    const b = bounds();
    const c = b.getCenter(new THREE.Vector3());
    const r = Math.max(b.getSize(new THREE.Vector3()).length() / 2, 1);
    const d = new THREE.Vector3(...dir).normalize();
    const dist = r / Math.sin(((camera as THREE.PerspectiveCamera).fov ?? 40) * Math.PI / 360) * 1.1;
    camera.position.copy(c.clone().add(d.multiplyScalar(ortho ? r * 4 : dist)));
    camera.up.set(0, 0, 1);
    if (Math.abs(dir[2]) > 0.99 && dir[0] === 0 && dir[1] === 0) camera.up.set(0, 1, 0);
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      const oc = camera as THREE.OrthographicCamera;
      oc.zoom = Math.min(size.width, size.height) / (2.3 * r);
      oc.near = -r * 50;
      oc.far = r * 50;
    } else {
      (camera as THREE.PerspectiveCamera).near = Math.max(r / 1000, 0.01);
      (camera as THREE.PerspectiveCamera).far = r * 200;
    }
    camera.lookAt(c);
    camera.updateProjectionMatrix();
    if (controls.current) { controls.current.target.copy(c); controls.current.update(); }
    invalidate();
  };

  useImperativeHandle(props.handleRef, () => ({
    fit: () => {
      const dir = camera.position.clone().sub(controls.current?.target ?? new THREE.Vector3()).normalize();
      place([dir.x, dir.y, dir.z]);
    },
    setView: (v) => place(VIEW_DIRS[v]),
    getCamera: () => ({ position: camera.position.toArray(), target: (controls.current?.target ?? new THREE.Vector3()).toArray(), zoom: camera.zoom }),
    setCamera: (c) => {
      camera.position.fromArray(c.position);
      if (c.zoom) { camera.zoom = c.zoom; camera.updateProjectionMatrix(); }
      controls.current?.target.fromArray(c.target);
      controls.current?.update();
      invalidate();
    },
    bounds,
  }));

  // Auto-fit when the set of bodies changes (not on every edit of the same body set).
  const sig = bodies.map((b) => b.key).join(",");
  useEffect(() => {
    if (!autoFit || !bodies.length) return;
    if (fitted.current !== sig) {
      fitted.current = sig;
      // Geometry is attached by child components; wait (bounded) until it is in the scene graph before fitting.
      let tries = 0;
      const attempt = () => {
        if (meshBounds().isEmpty() && tries++ < 60) { requestAnimationFrame(attempt); return; }
        place(VIEW_DIRS.iso);
      };
      requestAnimationFrame(attempt);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, ortho]);

  useEffect(() => { raycaster.params.Line = { threshold: 0.5 }; }, [raycaster]);

  return (
    <>
      {ortho ? <OrthographicCamera makeDefault position={[200, -200, 160]} zoom={2} near={-10000} far={10000} />
        : <PerspectiveCamera makeDefault position={[200, -200, 160]} fov={40} near={0.1} far={50000} />}
      <OrbitControls ref={controls} makeDefault enableDamping={false} autoRotate={props.autoRotate} autoRotateSpeed={0.6}
        enabled={props.interactive !== false}
        onEnd={() => props.onCameraChange?.({ position: camera.position.toArray(), target: controls.current.target.toArray() })} />
      <hemisphereLight args={["#dfe9f5", "#20262e", 0.85]} />
      <directionalLight position={[300, -400, 600]} intensity={1.6} />
      <directionalLight position={[-400, 300, -200]} intensity={0.55} />
      <ambientLight intensity={0.15} />
      {showGrid && <FloorGrid />}
      <group ref={group}>
        {bodies.filter((b) => b.visible !== false).map((b) => (
          <BodyObject key={b.key} body={b} display={display} clip={clip} props={props} />
        ))}
      </group>
      {props.children}
      {props.interactive !== false && (
        <GizmoHelper alignment="bottom-right" margin={[64, 64]} onUpdate={() => invalidate()}>
          <GizmoViewport axisColors={["#f0585d", "#3ccf8e", "#27b6ec"]} labelColor="#0a0c0f" />
        </GizmoHelper>
      )}
    </>
  );
}

/** XY floor grid (10 mm cells, 100 mm sections) drawn as ordinary depth-tested lines. */
function FloorGrid() {
  const [cells, sections] = useMemo(() => {
    const a = new THREE.GridHelper(1000, 100, "#18202a", "#18202a");
    const b = new THREE.GridHelper(2000, 20, "#2a3644", "#2a3644");
    for (const g of [a, b]) {
      g.rotation.x = Math.PI / 2;
      const m = g.material as THREE.LineBasicMaterial;
      m.transparent = true;
      m.opacity = 0.9;
      m.depthWrite = false;
    }
    b.position.z = 0.01;
    return [a, b];
  }, []);
  useEffect(() => () => { [cells, sections].forEach((g) => { g.geometry.dispose(); (g.material as THREE.Material).dispose(); }); }, [cells, sections]);
  return <><primitive object={cells} renderOrder={-2} /><primitive object={sections} renderOrder={-1} /></>;
}

function findFace(faces: FaceMeta[], tri: number): FaceMeta | undefined {
  let lo = 0, hi = faces.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const f = faces[mid];
    if (tri < f.start) hi = mid - 1;
    else if (tri >= f.start + f.count) lo = mid + 1;
    else return f;
  }
  return undefined;
}

function BodyObject({ body, display, clip, props }: { body: ViewBody; display: DisplayMode; clip: THREE.Plane[]; props: Props }) {
  const { invalidate } = useThree();
  const [hover, setHover] = useState<string | null>(null);
  const m = body.mesh;
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(f32(m.positions), 3));
    g.setAttribute("normal", new THREE.BufferAttribute(f32(m.normals), 3));
    g.setIndex(new THREE.BufferAttribute(u32(m.indices), 1));
    g.computeBoundingSphere();
    return g;
  }, [m.positions, m.normals, m.indices]);
  const edges = useMemo(() => m.edges.map((e) => {
    const pts = f32(e.points);
    const segs: number[] = [];
    for (let i = 0; i < e.n - 1; i++) segs.push(pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2], pts[i * 3 + 3], pts[i * 3 + 4], pts[i * 3 + 5]);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(segs, 3));
    return { meta: e, geom: g, key: edgeKey(body.key, e.ref.faces) };
  }), [m.edges, body.key]);
  useEffect(() => () => { geom.dispose(); edges.forEach((e) => e.geom.dispose()); }, [geom, edges]);

  const selected = props.selected ?? new Set<string>();
  const base = body.color ?? "#9aa6b6";
  const mats = useMemo(() => {
    const mk = (color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
      new THREE.MeshStandardMaterial({ color, metalness: 0.35, roughness: 0.5, side: THREE.DoubleSide, clippingPlanes: clip,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1, ...opts });
    const xr = display === "xray" || body.dim;
    const t = xr ? { transparent: true, opacity: body.dim ? 0.18 : 0.28, depthWrite: false } : {};
    return [mk(base, t), mk("#f2b73f", t), mk("#27b6ec", t)];
  }, [base, display, clip, body.dim]);
  useEffect(() => () => mats.forEach((x) => x.dispose()), [mats]);

  // Per-face material groups: 0 normal, 1 hover, 2 selected.
  useEffect(() => {
    geom.clearGroups();
    for (const f of m.faces) {
      const k = faceKey(body.key, f.name);
      const idx = selected.has(k) || selected.has(`${body.key}|body`) ? 2 : hover === k ? 1 : 0;
      geom.addGroup(f.start * 3, f.count * 3, idx);
    }
    invalidate();
  }, [geom, m.faces, selected, hover, body.key, invalidate]);

  const pickFace = (e: ThreeEvent<PointerEvent | MouseEvent>): PickInfo | null => {
    const tri = e.faceIndex ?? -1;
    const f = tri >= 0 ? findFace(m.faces, tri) : undefined;
    if (!f) return null;
    return { bodyKey: body.key, kind: props.pickMode === "body" ? "body" : "face", face: f, point: e.point.toArray() as [number, number, number],
      screen: { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY } };
  };

  const matrix = useMemo(() => {
    const M = toMatrix(body.matrix);
    if (body.explodeOffset) M.premultiply(new THREE.Matrix4().makeTranslation(...body.explodeOffset));
    return M;
  }, [body.matrix, body.explodeOffset]);
  const edgeColor = display === "wireframe" ? "#c9d3df" : "#0d1116";

  return (
    <group matrix={matrix} matrixAutoUpdate={false}>
      {display !== "wireframe" && (
        <mesh geometry={geom} material={mats}
          onPointerMove={(e) => {
            if (props.pickMode === "edge") return;
            e.stopPropagation();
            const p = pickFace(e);
            const k = p?.face ? faceKey(body.key, p.face.name) : null;
            if (k !== hover) { setHover(k); props.onHover?.(p); }
          }}
          onPointerOut={() => { setHover(null); props.onHover?.(null); }}
          onClick={(e) => {
            if (props.pickMode === "edge" || e.delta > 4) return;
            e.stopPropagation();
            props.onPick?.(pickFace(e), { shift: e.nativeEvent.shiftKey || e.nativeEvent.ctrlKey || e.nativeEvent.metaKey, button: 0 });
          }}
          onContextMenu={(e) => {
            e.stopPropagation();
            e.nativeEvent.preventDefault();
            props.onContextMenu?.(pickFace(e), { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY });
          }} />
      )}
      {(display !== "shaded") && edges.map((e) => {
        const sel = selected.has(e.key);
        const hov = hover === e.key;
        return (
          <lineSegments key={e.key} geometry={e.geom}
            onPointerMove={(ev) => {
              if (props.pickMode !== "edge") return;
              ev.stopPropagation();
              if (hover !== e.key) { setHover(e.key); props.onHover?.({ bodyKey: body.key, kind: "edge", edge: e.meta, point: ev.point.toArray() as any, screen: { x: 0, y: 0 } }); }
            }}
            onPointerOut={() => props.pickMode === "edge" && setHover(null)}
            onClick={(ev) => {
              if (props.pickMode !== "edge" || ev.delta > 4) return;
              ev.stopPropagation();
              props.onPick?.({ bodyKey: body.key, kind: "edge", edge: e.meta, point: ev.point.toArray() as any,
                screen: { x: ev.nativeEvent.clientX, y: ev.nativeEvent.clientY } }, { shift: ev.nativeEvent.shiftKey || ev.nativeEvent.ctrlKey, button: 0 });
            }}>
            <lineBasicMaterial color={sel ? "#27b6ec" : hov ? "#f2b73f" : edgeColor} clippingPlanes={clip} linewidth={1} />
          </lineSegments>
        );
      })}
    </group>
  );
}

/** Text label anchored in 3D (measurement readouts, comment pins). */
export function Label3D({ at, children }: { at: [number, number, number]; children: ReactNode }) {
  return (
    <Html position={at} center style={{ pointerEvents: "none" }}>
      <div style={{ background: "rgba(20,24,29,.92)", border: "1px solid #353e4a", borderRadius: 4, padding: "2px 6px", fontSize: 11,
        color: "#e7ebf1", whiteSpace: "nowrap", fontFamily: "var(--mono)" }}>{children}</div>
    </Html>
  );
}

/** Straight measurement line between two world points. */
export function MeasureLine({ a, b, label }: { a: number[]; b: number[]; label: string }) {
  const g = useMemo(() => new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]), [a, b]);
  useEffect(() => () => g.dispose(), [g]);
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2] as [number, number, number];
  return (
    <>
      <lineSegments geometry={g}><lineBasicMaterial color="#f2b73f" depthTest={false} /></lineSegments>
      <Label3D at={mid}>{label}</Label3D>
    </>
  );
}
