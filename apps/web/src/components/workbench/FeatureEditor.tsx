"use client";

import { Crosshair, PencilRuler, Plus, Trash2, X } from "lucide-react";
import type { ReactNode } from "react";
import type { Feature, FeatureStatus, PartDocument, PartModel } from "@/api/types";
import { StatusBadge } from "@/components/ui";
import { FEATURE_LABEL, edgeRef } from "./defaults";
import {
  AxisRefEditor, BodySelect, ExprInput, ExprVec, PlaneRefEditor, Row, Sel, VecInput, selectedEdges, selectedFaces, sketchIds,
} from "./fields";

interface Props {
  feature: Feature;
  isNew: boolean;
  doc: PartDocument;
  model: PartModel | null;
  bodies: string[];
  selection: Set<string>;
  unit: string;
  status?: FeatureStatus;
  readonly: boolean;
  onChange: (f: Feature) => void;
  onOk: () => void;
  onCancel: () => void;
  onEditSketch: (id: string) => void;
}

const OPS = [{ value: "new", label: "New body" }, { value: "join", label: "Join (add)" }, { value: "cut", label: "Cut (subtract)" },
  { value: "intersect", label: "Intersect" }] as const;

export function FeatureEditor({ feature, isNew, doc, model, bodies, selection, unit, status, readonly, onChange, onOk, onCancel, onEditSketch }: Props) {
  const f = feature;
  const id = (k: string) => `fe-${f.id}-${k}`;
  const set = (patch: Record<string, unknown>) => onChange({ ...f, params: { ...(f.params as object), ...patch } } as Feature);
  const sketches = sketchIds(doc, isNew ? undefined : f.id).filter((s) => s.id !== f.id);
  const regionsOf = (sid: string) => model?.sketches[sid]?.profile.regions ?? [];
  const priorFeatures = (() => {
    const out: { id: string; label: string }[] = [];
    for (const x of doc.features) { if (x.id === f.id) break; if (x.type !== "sketch" && !x.type.startsWith("datum")) out.push({ id: x.id, label: x.name || x.id }); }
    return out;
  })();

  const solidOps = (p: { operation: string; body: string | null }) => (
    <>
      <Row label="Result" htmlFor={id("op")}>
        <Sel id={id("op")} value={p.operation as (typeof OPS)[number]["value"]} options={[...OPS]} onChange={(v) => set({ operation: v, body: v === "new" ? null : p.body ?? bodies[0] ?? null })} />
      </Row>
      {p.operation === "new" ? (
        <Row label="New body name" htmlFor={id("body")} hint="Leave empty for an automatic name (Body1, Body2 …)">
          <input id={id("body")} className="input" value={p.body ?? ""} maxLength={64} onChange={(e) => set({ body: e.target.value || null })} />
        </Row>
      ) : (
        <Row label="Target body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
      )}
    </>
  );

  const sketchPick = (key: string, value: string, label = "Profile sketch") => (
    <Row label={label} htmlFor={id(key)}>
      <select id={id(key)} className="select" value={value} onChange={(e) => set({ [key]: e.target.value, ...(key === "sketch" ? { regions: null } : {}) })}>
        <option value="">Select a sketch…</option>
        {sketches.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </select>
    </Row>
  );

  const regionPick = (sid: string, value: string[] | null | undefined) => {
    const regions = regionsOf(sid);
    if (!sid || !regions.length) return null;
    const defaults = model?.sketches[sid]?.profile.default_regions ?? [];
    return (
      <div className="field">
        <span className="label">Regions</span>
        <label className="checkbox"><input type="checkbox" checked={value == null} onChange={(e) => set({ regions: e.target.checked ? null : defaults })} />
          Automatic (outer regions with holes, {defaults.length} of {regions.length})</label>
        {value != null && regions.map((r) => (
          <label key={r.id} className="checkbox small">
            <input type="checkbox" checked={value.includes(r.id)} onChange={(e) => set({ regions: e.target.checked ? [...value, r.id] : value.filter((x) => x !== r.id) })} />
            <code className="ellipsis" title={r.id}>{r.id}</code><span className="faint num">{r.area.toFixed(1)} mm²</span>
          </label>
        ))}
      </div>
    );
  };

  const featureScope = (value: string[] | null | undefined) => (
    <div className="field">
      <span className="label">Pattern / mirror scope</span>
      <label className="checkbox"><input type="checkbox" checked={value == null} onChange={(e) => set({ features: e.target.checked ? null : [] })} />Whole body</label>
      {value != null && priorFeatures.map((x) => (
        <label key={x.id} className="checkbox small">
          <input type="checkbox" checked={value.includes(x.id)} onChange={(e) => set({ features: e.target.checked ? [...value, x.id] : value.filter((v) => v !== x.id) })} />{x.label}
        </label>
      ))}
    </div>
  );

  let body: ReactNode = null;
  switch (f.type) {
    case "sketch": {
      const p = f.params;
      body = (
        <>
          <Row label="Sketch plane"><PlaneRefEditor idPrefix={id("plane")} value={p.plane} onChange={(v) => set({ plane: v })} doc={doc} before={f.id} selection={selection} unit={unit} /></Row>
          <div className="small muted">{p.entities.length} entities · {p.constraints.length} constraints
            {model?.sketches[f.id] && <> · <StatusBadge status={model.sketches[f.id].solve.status} /> DOF {model.sketches[f.id].solve.dof}</>}</div>
          {!isNew && <button className="btn" onClick={() => onEditSketch(f.id)} disabled={readonly}><PencilRuler size={14} />Edit sketch geometry</button>}
        </>
      );
      break;
    }
    case "extrude": {
      const p = f.params;
      body = (
        <>
          {sketchPick("sketch", p.sketch)}
          {regionPick(p.sketch, p.regions)}
          <Row label="Extent" htmlFor={id("extent")}>
            <Sel id={id("extent")} value={p.extent} options={[{ value: "blind", label: "Blind (distance)" }, { value: "through_all", label: "Through all" }]} onChange={(v) => set({ extent: v })} />
          </Row>
          {p.extent === "blind" && <Row label={`Distance (${unit})`} htmlFor={id("dist")}><ExprInput id={id("dist")} value={p.distance} unit={unit} onChange={(v) => set({ distance: v })} /></Row>}
          <Row label="Direction" htmlFor={id("dir")}>
            <Sel id={id("dir")} value={p.direction} options={[{ value: "normal", label: "Along plane normal" }, { value: "reverse", label: "Reversed" }, { value: "symmetric", label: "Symmetric (mid-plane)" }]} onChange={(v) => set({ direction: v })} />
          </Row>
          {solidOps(p)}
        </>
      );
      break;
    }
    case "revolve": {
      const p = f.params;
      body = (
        <>
          {sketchPick("sketch", p.sketch)}
          {regionPick(p.sketch, p.regions)}
          <Row label="Axis"><AxisRefEditor idPrefix={id("axis")} value={p.axis} onChange={(v) => set({ axis: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} /></Row>
          <Row label="Angle (deg)" htmlFor={id("ang")}><ExprInput id={id("ang")} value={p.angle} unit="deg" onChange={(v) => set({ angle: v })} /></Row>
          {solidOps(p)}
        </>
      );
      break;
    }
    case "sweep": {
      const p = f.params;
      body = (
        <>
          {sketchPick("sketch", p.sketch)}
          {regionPick(p.sketch, p.regions)}
          {sketchPick("path_sketch", p.path_sketch, "Path sketch")}
          {solidOps(p)}
        </>
      );
      break;
    }
    case "loft": {
      const p = f.params;
      body = (
        <>
          <div className="field">
            <span className="label">Section sketches (in order)</span>
            {p.sketches.map((s, i) => (
              <div className="row" key={i}>
                <select className="select" aria-label={`Section ${i + 1}`} value={s} onChange={(e) => { const v = [...p.sketches]; v[i] = e.target.value; set({ sketches: v }); }}>
                  {sketches.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                </select>
                <button className="btn ghost icon sm" aria-label="Remove section" onClick={() => set({ sketches: p.sketches.filter((_, j) => j !== i) })}><X size={13} /></button>
              </div>
            ))}
            <button className="btn sm" disabled={!sketches.length} onClick={() => set({ sketches: [...p.sketches, sketches.find((x) => !p.sketches.includes(x.id))?.id ?? sketches[0].id] })}>
              <Plus size={13} />Add section</button>
          </div>
          <label className="checkbox"><input type="checkbox" checked={p.ruled} onChange={(e) => set({ ruled: e.target.checked })} />Ruled (straight) surfaces</label>
          {solidOps(p)}
        </>
      );
      break;
    }
    case "hole": {
      const p = f.params;
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
          <Row label="Placement plane" hint="Holes are drilled against the plane normal."><PlaneRefEditor idPrefix={id("plane")} value={p.plane} onChange={(v) => set({ plane: v })} doc={doc} before={f.id} selection={selection} unit={unit} /></Row>
          <div className="field">
            <span className="label">Hole centres (plane coordinates, {unit})</span>
            {p.positions.map((pos, i) => (
              <div className="row" key={i}>
                <ExprInput ariaLabel={`Hole ${i + 1} x`} value={pos[0]} unit={unit} onChange={(v) => { const ps = p.positions.map((q) => [...q]); ps[i][0] = v; set({ positions: ps }); }} />
                <ExprInput ariaLabel={`Hole ${i + 1} y`} value={pos[1]} unit={unit} onChange={(v) => { const ps = p.positions.map((q) => [...q]); ps[i][1] = v; set({ positions: ps }); }} />
                <button className="btn ghost icon sm" aria-label={`Remove hole ${i + 1}`} disabled={p.positions.length <= 1} onClick={() => set({ positions: p.positions.filter((_, j) => j !== i) })}><Trash2 size={13} /></button>
              </div>
            ))}
            <button className="btn sm" onClick={() => set({ positions: [...p.positions, [0, 0]] })}><Plus size={13} />Add hole</button>
          </div>
          <Row label={`Diameter (${unit})`} htmlFor={id("dia")}><ExprInput id={id("dia")} value={p.diameter} unit={unit} onChange={(v) => set({ diameter: v })} /></Row>
          <Row label="End condition" htmlFor={id("end")}>
            <Sel id={id("end")} value={p.end} options={[{ value: "through_all", label: "Through all" }, { value: "blind", label: "Blind" }]} onChange={(v) => set({ end: v, depth: v === "blind" ? p.depth ?? 10 : null })} />
          </Row>
          {p.end === "blind" && <Row label={`Depth (${unit})`} htmlFor={id("depth")}><ExprInput id={id("depth")} value={p.depth ?? 10} unit={unit} onChange={(v) => set({ depth: v })} /></Row>}
          <div className="field">
            <span className="label">Head</span>
            <Sel ariaLabel="Hole head" value={p.cbore_diameter != null ? "cbore" : p.csink_diameter != null ? "csink" : "simple"}
              options={[{ value: "simple", label: "Simple" }, { value: "cbore", label: "Counterbore" }, { value: "csink", label: "Countersink" }]}
              onChange={(v) => set(v === "cbore" ? { cbore_diameter: 11, cbore_depth: 6.5, csink_diameter: null } : v === "csink" ? { csink_diameter: 13, cbore_diameter: null, cbore_depth: null }
                : { cbore_diameter: null, cbore_depth: null, csink_diameter: null })} />
          </div>
          {p.cbore_diameter != null && (
            <div className="grid grid-2">
              <Row label="C'bore ⌀"><ExprInput value={p.cbore_diameter} unit={unit} onChange={(v) => set({ cbore_diameter: v })} /></Row>
              <Row label="C'bore depth"><ExprInput value={p.cbore_depth ?? 0} unit={unit} onChange={(v) => set({ cbore_depth: v })} /></Row>
            </div>
          )}
          {p.csink_diameter != null && (
            <div className="grid grid-2">
              <Row label="C'sink ⌀"><ExprInput value={p.csink_diameter} unit={unit} onChange={(v) => set({ csink_diameter: v })} /></Row>
              <Row label="C'sink angle (deg)"><ExprInput value={p.csink_angle} unit="deg" onChange={(v) => set({ csink_angle: v })} /></Row>
            </div>
          )}
        </>
      );
      break;
    }
    case "fillet":
    case "chamfer": {
      const p = f.params;
      const picked = selectedEdges(selection, model, p.body || undefined);
      const faces = selectedFaces(selection, p.body || undefined);
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v, edges: [] })} /></Row>
          <div className="field">
            <span className="label">Edges ({p.edges.length})</span>
            <div className="col gap-4" style={{ maxHeight: 160, overflow: "auto" }}>
              {p.edges.map((e, i) => (
                <div className="row small" key={i}>
                  <code className="grow ellipsis" title={JSON.stringify(e)}>{e.kind === "between" ? (e.faces ?? []).join(" | ") : e.kind === "of_face" ? `all edges of ${e.face}` : e.kind}</code>
                  <button className="btn ghost icon sm" aria-label="Remove edge" onClick={() => set({ edges: p.edges.filter((_, j) => j !== i) })}><X size={12} /></button>
                </div>
              ))}
              {!p.edges.length && <span className="faint small">Switch the viewport to edge selection, pick edges, then add them.</span>}
            </div>
            <div className="row wrap">
              <button className="btn sm" disabled={!picked.length} onClick={() => set({ body: p.body || picked[0].body, edges: [...p.edges, ...picked.map((x) => x.ref)] })}>
                <Crosshair size={13} />Add {picked.length || ""} selected edge{picked.length === 1 ? "" : "s"}</button>
              <button className="btn sm" disabled={!faces.length} onClick={() => set({ body: p.body || faces[0].body, edges: [...p.edges, ...faces.map((x) => edgeRef({ kind: "of_face", face: x.face }))] })}>
                Edges of selected face</button>
              <button className="btn sm" onClick={() => set({ edges: [edgeRef({ kind: "all" })] })}>All edges</button>
            </div>
          </div>
          {f.type === "fillet"
            ? <Row label={`Radius (${unit})`} htmlFor={id("r")}><ExprInput id={id("r")} value={(p as any).radius} unit={unit} onChange={(v) => set({ radius: v })} /></Row>
            : <Row label={`Distance (${unit})`} htmlFor={id("d")}><ExprInput id={id("d")} value={(p as any).distance} unit={unit} onChange={(v) => set({ distance: v })} /></Row>}
        </>
      );
      break;
    }
    case "shell": {
      const p = f.params;
      const faces = selectedFaces(selection, p.body || undefined);
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v, remove_faces: [] })} /></Row>
          <div className="field">
            <span className="label">Open faces ({p.remove_faces.length})</span>
            {p.remove_faces.map((x) => (
              <div className="row small" key={x}><code className="grow ellipsis">{x}</code>
                <button className="btn ghost icon sm" aria-label="Remove face" onClick={() => set({ remove_faces: p.remove_faces.filter((y) => y !== x) })}><X size={12} /></button></div>
            ))}
            <button className="btn sm" disabled={!faces.length} onClick={() => set({ body: p.body || faces[0].body, remove_faces: [...new Set([...p.remove_faces, ...faces.map((x) => x.face)])] })}>
              <Crosshair size={13} />Add selected faces</button>
          </div>
          <Row label={`Wall thickness (${unit})`} htmlFor={id("t")}><ExprInput id={id("t")} value={p.thickness} unit={unit} onChange={(v) => set({ thickness: v })} /></Row>
        </>
      );
      break;
    }
    case "boolean": {
      const p = f.params;
      body = (
        <>
          <Row label="Operation" htmlFor={id("op")}><Sel id={id("op")} value={p.operation} options={["join", "cut", "intersect"] as const} onChange={(v) => set({ operation: v })} /></Row>
          <Row label="Target body" htmlFor={id("t")}><BodySelect id={id("t")} bodies={bodies} value={p.target} onChange={(v) => set({ target: v, tools: p.tools.filter((x) => x !== v) })} /></Row>
          <div className="field">
            <span className="label">Tool bodies</span>
            {bodies.filter((b) => b !== p.target).map((b) => (
              <label key={b} className="checkbox"><input type="checkbox" checked={p.tools.includes(b)} onChange={(e) => set({ tools: e.target.checked ? [...p.tools, b] : p.tools.filter((x) => x !== b) })} />{b}</label>
            ))}
          </div>
          <label className="checkbox"><input type="checkbox" checked={p.keep_tools} onChange={(e) => set({ keep_tools: e.target.checked })} />Keep tool bodies</label>
        </>
      );
      break;
    }
    case "linear_pattern": {
      const p = f.params;
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
          {featureScope(p.features)}
          <Row label="Direction 1"><AxisRefEditor idPrefix={id("d1")} value={p.direction} onChange={(v) => set({ direction: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} /></Row>
          <div className="grid grid-2">
            <Row label="Count"><ExprInput value={p.count} unit="" onChange={(v) => set({ count: v })} /></Row>
            <Row label={`Spacing (${unit})`}><ExprInput value={p.spacing} unit={unit} onChange={(v) => set({ spacing: v })} /></Row>
          </div>
          <label className="checkbox"><input type="checkbox" checked={p.direction2 != null} onChange={(e) => set({ direction2: e.target.checked ? { ...p.direction, name: "Y", kind: "standard" } : null, count2: e.target.checked ? 2 : 1 })} />Second direction</label>
          {p.direction2 && (
            <>
              <AxisRefEditor idPrefix={id("d2")} value={p.direction2} onChange={(v) => set({ direction2: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} />
              <div className="grid grid-2">
                <Row label="Count 2"><ExprInput value={p.count2} unit="" onChange={(v) => set({ count2: v })} /></Row>
                <Row label={`Spacing 2 (${unit})`}><ExprInput value={p.spacing2} unit={unit} onChange={(v) => set({ spacing2: v })} /></Row>
              </div>
            </>
          )}
        </>
      );
      break;
    }
    case "circular_pattern": {
      const p = f.params;
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
          {featureScope(p.features)}
          <Row label="Axis"><AxisRefEditor idPrefix={id("ax")} value={p.axis} onChange={(v) => set({ axis: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} /></Row>
          <div className="grid grid-2">
            <Row label="Count"><ExprInput value={p.count} unit="" onChange={(v) => set({ count: v })} /></Row>
            <Row label="Total angle (deg)"><ExprInput value={p.angle} unit="deg" onChange={(v) => set({ angle: v })} /></Row>
          </div>
        </>
      );
      break;
    }
    case "mirror": {
      const p = f.params;
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
          {featureScope(p.features)}
          <Row label="Mirror plane"><PlaneRefEditor idPrefix={id("plane")} value={p.plane} onChange={(v) => set({ plane: v })} doc={doc} before={f.id} selection={selection} unit={unit} /></Row>
        </>
      );
      break;
    }
    case "datum_plane":
      body = <Row label="Plane"><PlaneRefEditor idPrefix={id("plane")} value={f.params.plane} onChange={(v) => set({ plane: v })} doc={doc} before={f.id} selection={selection} unit={unit} /></Row>;
      break;
    case "datum_axis":
      body = <Row label="Axis"><AxisRefEditor idPrefix={id("ax")} value={f.params.axis} onChange={(v) => set({ axis: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} /></Row>;
      break;
    case "primitive": {
      const p = f.params;
      const labels: Record<string, string[]> = { box: ["Length X", "Width Y", "Height Z"], cylinder: ["Radius", "Height", "—"], sphere: ["Radius", "—", "—"], cone: ["Base radius", "Top radius", "Height"] };
      body = (
        <>
          <Row label="Shape" htmlFor={id("shape")}><Sel id={id("shape")} value={p.shape} options={["box", "cylinder", "sphere", "cone"] as const} onChange={(v) => set({ shape: v })} /></Row>
          <ExprVec label={`Size: ${labels[p.shape].join(", ")} (${unit})`} value={p.size} unit={unit} onChange={(v) => set({ size: v })} />
          <ExprVec label={`Origin (${unit})`} value={p.origin} unit={unit} onChange={(v) => set({ origin: v })} />
          {p.shape !== "box" && p.shape !== "sphere" && <VecInput label="Axis direction" value={p.axis} onChange={(v) => set({ axis: v })} />}
          {solidOps(p)}
        </>
      );
      break;
    }
    case "transform": {
      const p = f.params;
      body = (
        <>
          <Row label="Body" htmlFor={id("body")}><BodySelect id={id("body")} bodies={bodies} value={p.body} onChange={(v) => set({ body: v })} /></Row>
          <ExprVec label={`Translate (${unit})`} value={p.translate} unit={unit} onChange={(v) => set({ translate: v })} />
          <label className="checkbox"><input type="checkbox" checked={p.rotate_axis != null} onChange={(e) => set({ rotate_axis: e.target.checked ? { kind: "standard", name: "Z", datum: null, body: null, face: null, edge: null, sketch: null, entity: null, origin: null, direction: null, reverse: false } : null })} />Rotate</label>
          {p.rotate_axis && (
            <>
              <AxisRefEditor idPrefix={id("rot")} value={p.rotate_axis} onChange={(v) => set({ rotate_axis: v })} doc={doc} before={isNew ? undefined : f.id} selection={selection} model={model} />
              <Row label="Angle (deg)"><ExprInput value={p.rotate_angle} unit="deg" onChange={(v) => set({ rotate_angle: v })} /></Row>
            </>
          )}
          <Row label="Copy to new body" hint="Leave empty to move the body itself">
            <input className="input" value={p.copy_to ?? ""} maxLength={64} onChange={(e) => set({ copy_to: e.target.value || null })} />
          </Row>
        </>
      );
      break;
    }
    case "import": {
      const p = f.params;
      body = (
        <dl className="kv">
          <dt>File</dt><dd>{p.filename}</dd><dt>Format</dt><dd>{p.format.toUpperCase()}</dd><dt>SHA-256</dt><dd>{p.sha256.slice(0, 16)}…</dd>
          <dt>Body</dt><dd>{p.body ?? "auto"}</dd>
        </dl>
      );
      break;
    }
  }

  const sketchMissing = (f.type === "extrude" || f.type === "revolve" || f.type === "sweep") && !f.params.sketch;
  return (
    <div className="col" style={{ height: "100%" }}>
      <div className="pane-head">
        <span>{isNew ? "New " : "Edit "}{FEATURE_LABEL[f.type]}</span>
        {status && <StatusBadge status={status.status} />}
      </div>
      <div className="pane-body col gap-12">
        <Row label="Name" htmlFor={id("name")}>
          <input id={id("name")} className="input" value={f.name} maxLength={128} onChange={(e) => onChange({ ...f, name: e.target.value })} />
        </Row>
        {body}
        {status && status.status === "error" && <div className="alert err" role="alert"><div><strong>{status.code || "Feature failed"}</strong><div className="small">{status.message}</div></div></div>}
        {status?.warnings?.map((w, i) => <div key={i} className="alert warn small">{w}</div>)}
        {sketchMissing && <div className="alert info small">Choose a sketch with at least one closed region.</div>}
        <div className="small faint">Changes preview live. Values accept expressions such as <code>width/2</code> or <code>2 in</code>.</div>
      </div>
      <div className="row" style={{ padding: 8, borderTop: "1px solid var(--border)", justifyContent: "flex-end" }}>
        <button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn primary" onClick={onOk} disabled={readonly}>{isNew ? "Create" : "Apply"}</button>
      </div>
    </div>
  );
}
