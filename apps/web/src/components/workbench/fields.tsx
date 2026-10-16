"use client";

import { Crosshair } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { AxisRef, EdgeRef, PartDocument, PartModel, PlaneRef } from "@/api/types";
import { toStoredExpr } from "@/lib/mesh";
import { axisRef, edgeRef, planeRef } from "./defaults";
import { parseSel } from "./store";

type Expr = number | string;

/** Expression input: accepts numbers, unit literals (`2 in`) and parameter expressions (`width/2`). */
export function ExprInput({ id, value, onChange, unit = "mm", disabled, invalid, placeholder, ariaLabel }: {
  id?: string; value: Expr | null | undefined; onChange: (v: Expr) => void; unit?: string; disabled?: boolean; invalid?: boolean;
  placeholder?: string; ariaLabel?: string;
}) {
  const shown = value === null || value === undefined ? "" : String(value);
  // While focused the raw text is kept so unit conversion ("2" -> "2 in") never moves the caret.
  const [text, setText] = useState<string | null>(null);
  return (
    <input id={id} className="input mono" value={text ?? shown} disabled={disabled} aria-invalid={invalid || undefined} placeholder={placeholder}
      aria-label={ariaLabel} spellCheck={false}
      onFocus={() => setText(shown)}
      onBlur={() => setText(null)}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        const s = unit === "deg" || unit === "" ? raw.trim() : toStoredExpr(raw, unit);
        const n = Number(s);
        onChange(s !== "" && /^-?\d*\.?\d+([eE][-+]?\d+)?$/.test(s) && Number.isFinite(n) ? n : s);
      }} />
  );
}

export function Row({ label, children, hint, htmlFor }: { label: string; children: ReactNode; hint?: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function Sel<T extends string>({ value, onChange, options, id, disabled, ariaLabel }: {
  value: T | null | undefined; onChange: (v: T) => void; options: (T | { value: T; label: string })[]; id?: string; disabled?: boolean; ariaLabel?: string;
}) {
  return (
    <select id={id} className="select" value={value ?? ""} disabled={disabled} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const l = typeof o === "string" ? o.replace(/_/g, " ") : o.label;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

export function BodySelect({ bodies, value, onChange, id, allowEmpty }: {
  bodies: string[]; value: string | null | undefined; onChange: (v: string) => void; id?: string; allowEmpty?: string;
}) {
  return (
    <select id={id} className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
      {allowEmpty !== undefined && <option value="">{allowEmpty}</option>}
      {!bodies.length && allowEmpty === undefined && <option value="">No bodies yet</option>}
      {bodies.map((b) => <option key={b} value={b}>{b}</option>)}
      {value && !bodies.includes(value) && <option value={value}>{value} (missing)</option>}
    </select>
  );
}

export function datumIds(doc: PartDocument, kind: "datum_plane" | "datum_axis", before?: string): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const f of doc.features) {
    if (before && f.id === before) break;
    if (f.type === kind) out.push({ id: f.id, label: f.name || f.id });
  }
  return out;
}

export function sketchIds(doc: PartDocument, before?: string): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const f of doc.features) {
    if (before && f.id === before) break;
    if (f.type === "sketch") out.push({ id: f.id, label: f.name || f.id });
  }
  return out;
}

/** First selected face (as body + persistent face name), if any. */
export function selectedFace(selection: Set<string>): { body: string; face: string } | null {
  for (const k of selection) {
    const s = parseSel(k);
    if (s.kind === "face" && s.face) return { body: s.body, face: s.face };
  }
  return null;
}

export function selectedFaces(selection: Set<string>, body?: string): { body: string; face: string }[] {
  const out: { body: string; face: string }[] = [];
  for (const k of selection) {
    const s = parseSel(k);
    if (s.kind === "face" && s.face && (!body || s.body === body)) out.push({ body: s.body, face: s.face });
  }
  return out;
}

/** Selected edges as persistent EdgeRefs (face pair + geometric hint from the tessellation). */
export function selectedEdges(selection: Set<string>, model: PartModel | null, body?: string): { body: string; ref: EdgeRef }[] {
  const out: { body: string; ref: EdgeRef }[] = [];
  for (const k of selection) {
    const s = parseSel(k);
    if (s.kind !== "edge" || !s.faces || (body && s.body !== body)) continue;
    const bm = model?.bodies.find((b) => b.name === s.body);
    const em = bm?.edges.find((e) => [...e.ref.faces].sort().join("|") === [...s.faces!].sort().join("|"));
    out.push({ body: s.body, ref: edgeRef({ kind: "between", faces: s.faces, hint: (em?.ref.hint as [number, number, number] | undefined) ?? null }) });
  }
  return out;
}

export function PlaneRefEditor({ value, onChange, doc, before, selection, unit, idPrefix }: {
  value: PlaneRef; onChange: (p: PlaneRef) => void; doc: PartDocument; before?: string; selection: Set<string>; unit: string; idPrefix: string;
}) {
  const datums = datumIds(doc, "datum_plane", before);
  const face = selectedFace(selection);
  return (
    <div className="col gap-4">
      <div className="row">
        <Sel id={`${idPrefix}-kind`} ariaLabel="Plane type" value={value.kind ?? "standard"} options={[
          { value: "standard", label: "Standard plane" }, { value: "face", label: "Planar face" }, { value: "datum", label: "Datum plane" },
        ]} onChange={(k) => {
          const keep = { offset: value.offset ?? 0, flip: value.flip ?? false };
          onChange(k === "standard" ? planeRef({ ...keep, kind: "standard", name: "XY" })
            : k === "datum" ? planeRef({ ...keep, kind: "datum", name: null, datum: datums[0]?.id ?? "" })
            : planeRef({ ...keep, kind: "face", name: null, body: face?.body ?? "", face: face?.face ?? "" }));
        }} />
        {value.kind === "standard" && (
          <Sel ariaLabel="Standard plane" value={value.name ?? "XY"} options={[
            { value: "XY", label: "XY (top)" }, { value: "XZ", label: "XZ (front)" }, { value: "YZ", label: "YZ (right)" },
          ]} onChange={(n) => onChange({ ...value, name: n })} />
        )}
        {value.kind === "datum" && (
          <select className="select" aria-label="Datum plane" value={value.datum ?? ""} onChange={(e) => onChange({ ...value, datum: e.target.value })}>
            {!datums.length && <option value="">No datum planes before this feature</option>}
            {datums.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          </select>
        )}
      </div>
      {value.kind === "face" && (
        <div className="row">
          <code className="grow ellipsis" title={`${value.body} · ${value.face}`}>{value.face ? `${value.body} · ${value.face}` : "No face picked"}</code>
          <button type="button" className="btn sm" disabled={!face} title="Use the face selected in the viewport"
            onClick={() => face && onChange({ ...value, body: face.body, face: face.face })}><Crosshair size={13} />Use selected</button>
        </div>
      )}
      <div className="grid grid-2">
        <Row label="Offset" htmlFor={`${idPrefix}-off`}>
          <ExprInput id={`${idPrefix}-off`} value={value.offset ?? 0} unit={unit} onChange={(v) => onChange({ ...value, offset: v })} />
        </Row>
        <label className="checkbox" style={{ alignSelf: "end", height: 30 }}>
          <input type="checkbox" checked={!!value.flip} onChange={(e) => onChange({ ...value, flip: e.target.checked })} />Flip normal
        </label>
      </div>
    </div>
  );
}

export function AxisRefEditor({ value, onChange, doc, before, selection, model, idPrefix }: {
  value: AxisRef; onChange: (a: AxisRef) => void; doc: PartDocument; before?: string; selection: Set<string>; model: PartModel | null; idPrefix: string;
}) {
  const datums = datumIds(doc, "datum_axis", before);
  const sketches = sketchIds(doc, before);
  const face = selectedFace(selection);
  const edge = selectedEdges(selection, model)[0];
  const lines = (sid: string | null | undefined) => {
    const f = doc.features.find((x) => x.id === sid);
    if (!f || f.type !== "sketch") return [];
    return f.params.entities.filter((e) => e.type === "line").map((e) => ({ id: e.id, construction: !!e.construction }));
  };
  const kind = value.kind ?? "standard";
  return (
    <div className="col gap-4">
      <div className="row">
        <Sel id={`${idPrefix}-kind`} ariaLabel="Axis type" value={kind} options={[
          { value: "standard", label: "Standard axis" }, { value: "sketch_line", label: "Sketch line" }, { value: "edge", label: "Model edge" },
          { value: "face", label: "Cylinder/cone axis" }, { value: "datum", label: "Datum axis" }, { value: "custom", label: "Custom vector" },
        ]} onChange={(k) => {
          const base = { reverse: value.reverse ?? false };
          if (k === "standard") onChange(axisRef({ ...base, kind: "standard", name: "Z" }));
          else if (k === "sketch_line") onChange(axisRef({ ...base, kind: "sketch_line", name: null, sketch: sketches[sketches.length - 1]?.id ?? "", entity: "" }));
          else if (k === "edge") onChange(axisRef({ ...base, kind: "edge", name: null, body: edge?.body ?? "", edge: edge?.ref ?? null }));
          else if (k === "face") onChange(axisRef({ ...base, kind: "face", name: null, body: face?.body ?? "", face: face?.face ?? "" }));
          else if (k === "datum") onChange(axisRef({ ...base, kind: "datum", name: null, datum: datums[0]?.id ?? "" }));
          else onChange(axisRef({ ...base, kind: "custom", name: null, origin: [0, 0, 0], direction: [0, 0, 1] }));
        }} />
        {kind === "standard" && <Sel ariaLabel="Standard axis" value={value.name ?? "Z"} options={["X", "Y", "Z"] as const} onChange={(n) => onChange({ ...value, name: n })} />}
        {kind === "datum" && (
          <select className="select" aria-label="Datum axis" value={value.datum ?? ""} onChange={(e) => onChange({ ...value, datum: e.target.value })}>
            {!datums.length && <option value="">No datum axes before this feature</option>}
            {datums.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          </select>
        )}
      </div>
      {kind === "sketch_line" && (
        <div className="row">
          <select className="select" aria-label="Sketch" value={value.sketch ?? ""} onChange={(e) => onChange({ ...value, sketch: e.target.value, entity: "" })}>
            <option value="">Sketch…</option>
            {sketches.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <select className="select" aria-label="Line" value={value.entity ?? ""} onChange={(e) => onChange({ ...value, entity: e.target.value })}>
            <option value="">Line…</option>
            {lines(value.sketch).map((l) => <option key={l.id} value={l.id}>{l.id}{l.construction ? " (construction)" : ""}</option>)}
          </select>
        </div>
      )}
      {kind === "edge" && (
        <div className="row">
          <code className="grow ellipsis">{value.edge?.faces ? `${value.body} · ${value.edge.faces.join(" | ")}` : "No edge picked"}</code>
          <button type="button" className="btn sm" disabled={!edge} onClick={() => edge && onChange({ ...value, body: edge.body, edge: edge.ref })}>
            <Crosshair size={13} />Use selected</button>
        </div>
      )}
      {kind === "face" && (
        <div className="row">
          <code className="grow ellipsis">{value.face ? `${value.body} · ${value.face}` : "No face picked"}</code>
          <button type="button" className="btn sm" disabled={!face} onClick={() => face && onChange({ ...value, body: face.body, face: face.face })}>
            <Crosshair size={13} />Use selected</button>
        </div>
      )}
      {kind === "custom" && (
        <>
          <VecInput label="Origin (mm)" value={value.origin ?? [0, 0, 0]} onChange={(v) => onChange({ ...value, origin: v })} />
          <VecInput label="Direction" value={value.direction ?? [0, 0, 1]} onChange={(v) => onChange({ ...value, direction: v })} />
        </>
      )}
      <label className="checkbox"><input type="checkbox" checked={!!value.reverse} onChange={(e) => onChange({ ...value, reverse: e.target.checked })} />Reverse direction</label>
    </div>
  );
}

export function VecInput({ label, value, onChange }: { label: string; value: number[]; onChange: (v: [number, number, number]) => void }) {
  return (
    <div className="field">
      <span className="label">{label}</span>
      <div className="inline-fields">
        {[0, 1, 2].map((i) => (
          <input key={i} className="input num mono" type="number" step="any" aria-label={`${label} ${"XYZ"[i]}`} value={value[i] ?? 0}
            onChange={(e) => { const v = [...value] as [number, number, number]; v[i] = Number(e.target.value); onChange(v); }} />
        ))}
      </div>
    </div>
  );
}

export function ExprVec({ label, value, onChange, unit }: { label: string; value: Expr[]; onChange: (v: [Expr, Expr, Expr]) => void; unit: string }) {
  return (
    <div className="field">
      <span className="label">{label}</span>
      <div className="inline-fields">
        {[0, 1, 2].map((i) => (
          <ExprInput key={i} ariaLabel={`${label} ${i + 1}`} value={value[i] ?? 0} unit={unit}
            onChange={(x) => { const v = [...value] as [Expr, Expr, Expr]; v[i] = x; onChange(v); }} />
        ))}
      </div>
    </div>
  );
}
