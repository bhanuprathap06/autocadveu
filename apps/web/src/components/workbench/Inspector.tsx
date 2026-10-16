"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, MessageSquare, Ruler, X } from "lucide-react";
import { useState } from "react";
import { apiPath, get, patch, post } from "@/api/client";
import type { BodyMesh, Comment, MaterialAssignment, PartDocument, PartModel } from "@/api/types";
import { ErrorBox, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { fmt, LENGTH_UNITS } from "@/lib/mesh";
import { parseSel } from "./store";

export interface MeasureResult { [k: string]: unknown; distance_mm?: number; point_a?: number[]; point_b?: number[]; angle_deg?: number }
interface MaterialRow {
  key: string; name: string; category: string; standard: string | null; density_kg_m3: number; youngs_modulus_mpa: number | null; poisson_ratio: number | null;
  yield_strength_mpa: number | null; thermal_conductivity_w_mk: number | null; specific_heat_j_kgk: number | null; value_basis: string; source: string;
  verification_status: string;
}

const len = (mm: number | undefined | null, unit: string) => (mm == null ? "–" : `${fmt(mm / LENGTH_UNITS[unit], 6)} ${unit}`);

function selRef(key: string, model: PartModel | null): Record<string, unknown> | null {
  const s = parseSel(key);
  if (s.kind === "face") return { kind: "face", body: s.body, name: s.face };
  if (s.kind === "edge") {
    const bm = model?.bodies.find((b) => b.name === s.body);
    const em = bm?.edges.find((e) => [...e.ref.faces].sort().join("|") === [...(s.faces ?? [])].sort().join("|"));
    return { kind: "edge", body: s.body, ref: { kind: "between", faces: s.faces, hint: em?.ref.hint ?? null } };
  }
  return { kind: "body", body: s.body };
}

export function Inspector({ doc, docId, projectId, model, selection, unit, readonly, canComment, revision, onSetMaterial, onMeasure, onFocusAnchor, getCamera }: {
  doc: PartDocument; docId: string; projectId: string; model: PartModel | null; selection: Set<string>; unit: string; readonly: boolean;
  canComment: boolean; revision: number;
  onSetMaterial: (body: string, m: MaterialAssignment | null) => void;
  onMeasure: (r: MeasureResult | null) => void;
  onFocusAnchor: (a: Record<string, any>) => void;
  getCamera: () => Record<string, unknown> | null;
}) {
  const [tab, setTab] = useState<"selection" | "bodies" | "comments">("selection");
  return (
    <div className="col" style={{ height: "100%", gap: 0 }}>
      <Tabs label="Inspector" value={tab} onChange={setTab} tabs={[
        { key: "selection", label: "Selection", badge: selection.size ? <span className="badge accent">{selection.size}</span> : null },
        { key: "bodies", label: "Bodies" },
        { key: "comments", label: <><MessageSquare size={13} />Comments</> },
      ]} />
      <div className="pane-body">
        {tab === "selection" && <SelectionTab doc={doc} docId={docId} model={model} selection={selection} unit={unit} onMeasure={onMeasure} />}
        {tab === "bodies" && <BodiesTab doc={doc} model={model} unit={unit} readonly={readonly} onSetMaterial={onSetMaterial} />}
        {tab === "comments" && <CommentsTab projectId={projectId} docId={docId} selection={selection} canComment={canComment} revision={revision}
          onFocusAnchor={onFocusAnchor} getCamera={getCamera} />}
      </div>
    </div>
  );
}

function SelectionTab({ doc, docId, model, selection, unit, onMeasure }: {
  doc: PartDocument; docId: string; model: PartModel | null; selection: Set<string>; unit: string; onMeasure: (r: MeasureResult | null) => void;
}) {
  const [result, setResult] = useState<MeasureResult | null>(null);
  const keys = [...selection];
  const m = useMutation({
    mutationFn: () => post<MeasureResult>(apiPath("/api/v1/documents/{document_id}/measure", { document_id: docId }), {
      content: doc, a: selRef(keys[0], model), b: keys[1] ? selRef(keys[1], model) : null,
    }),
    onSuccess: (r) => { setResult(r); onMeasure(r); },
    onError: () => onMeasure(null),
  });
  if (!keys.length) return <div className="faint small">Click faces in the viewport to inspect them (Shift/Ctrl adds to the selection). Use the edge filter to pick edges. Select one or two entities to measure.</div>;
  return (
    <div className="col gap-12">
      {keys.slice(0, 6).map((k) => {
        const s = parseSel(k);
        const body = model?.bodies.find((b) => b.name === s.body);
        if (s.kind === "face") {
          const f = body?.faces.find((x) => x.name === s.face);
          const g = f?.geometry;
          return (
            <div key={k} className="card tight">
              <div className="row between"><strong className="small">Face</strong>{f && !f.stable && <span className="badge warn" title="This face has no stable history-based name; references to it may break after edits.">Unstable name</span>}</div>
              <dl className="kv" style={{ marginTop: 6 }}>
                <dt>Body</dt><dd>{s.body}</dd>
                <dt>Persistent name</dt><dd>{s.face}</dd>
                {f && f.names.length > 1 && <><dt>Also named</dt><dd>{f.names.filter((n) => n !== f.name).join(", ")}</dd></>}
                {g && <><dt>Surface</dt><dd>{g.surface}</dd><dt>Area</dt><dd>{fmt(g.area / LENGTH_UNITS[unit] ** 2, 6)} {unit}²</dd></>}
                {g?.radius != null && <><dt>Radius</dt><dd>{len(g.radius, unit)}</dd></>}
                {g?.normal && <><dt>Normal</dt><dd>[{g.normal.map((x) => fmt(x, 4)).join(", ")}]</dd></>}
              </dl>
            </div>
          );
        }
        if (s.kind === "edge") {
          const e = body?.edges.find((x) => [...x.ref.faces].sort().join("|") === [...(s.faces ?? [])].sort().join("|"));
          return (
            <div key={k} className="card tight">
              <strong className="small">Edge</strong>
              <dl className="kv" style={{ marginTop: 6 }}>
                <dt>Body</dt><dd>{s.body}</dd>
                <dt>Between faces</dt><dd>{(s.faces ?? []).join(" | ")}</dd>
                {e && <><dt>Curve</dt><dd>{e.curve}</dd><dt>Length</dt><dd>{len(e.length, unit)}</dd></>}
                {e?.radius != null && <><dt>Radius</dt><dd>{len(e.radius, unit)}</dd></>}
              </dl>
            </div>
          );
        }
        return <div key={k} className="card tight small">Body {s.body}</div>;
      })}
      {keys.length > 6 && <div className="faint small">+{keys.length - 6} more selected</div>}
      <button className="btn" disabled={keys.length > 2 || m.isPending} onClick={() => m.mutate()}>
        <Ruler size={14} />{keys.length === 2 ? "Measure distance & angle" : "Measure"}{m.isPending && <span className="spinner" />}
      </button>
      {keys.length > 2 && <div className="faint tiny">Select one or two entities to measure.</div>}
      {m.error && <ErrorBox error={m.error} />}
      {result && (
        <div className="card tight">
          <div className="row between"><strong className="small">Measurement (exact B-rep)</strong>
            <button className="btn ghost icon sm" aria-label="Clear measurement" onClick={() => { setResult(null); onMeasure(null); }}><X size={12} /></button></div>
          <dl className="kv" style={{ marginTop: 6 }}>
            {result.distance_mm != null && <><dt>Minimum distance</dt><dd>{len(result.distance_mm as number, unit)}</dd></>}
            {result.angle_deg != null && <><dt>Angle</dt><dd>{fmt(result.angle_deg as number, 6)}°</dd></>}
            {result.area_mm2 != null && <><dt>Area</dt><dd>{fmt((result.area_mm2 as number) / LENGTH_UNITS[unit] ** 2, 6)} {unit}²</dd></>}
            {result.length_mm != null && <><dt>Length</dt><dd>{len(result.length_mm as number, unit)}</dd></>}
            {result.radius_mm != null && <><dt>Radius</dt><dd>{len(result.radius_mm as number, unit)}</dd></>}
            {result.diameter_mm != null && <><dt>Diameter</dt><dd>{len(result.diameter_mm as number, unit)}</dd></>}
            {result.volume_mm3 != null && <><dt>Volume</dt><dd>{fmt((result.volume_mm3 as number) / LENGTH_UNITS[unit] ** 3, 6)} {unit}³</dd></>}
          </dl>
        </div>
      )}
    </div>
  );
}

function BodiesTab({ doc, model, unit, readonly, onSetMaterial }: {
  doc: PartDocument; model: PartModel | null; unit: string; readonly: boolean; onSetMaterial: (body: string, m: MaterialAssignment | null) => void;
}) {
  const mats = useQuery({ queryKey: ["materials"], queryFn: () => get<{ items: MaterialRow[] }>(apiPath("/api/v1/materials")), staleTime: 3600_000 });
  if (!model?.bodies.length) return <div className="faint small">No solid bodies yet.</div>;
  return (
    <div className="col gap-12">
      {model.bodies.map((b: BodyMesh) => {
        const mp = b.mass;
        const assigned = doc.materials[b.name];
        return (
          <div key={b.name} className="card tight">
            <div className="row between"><strong>{b.name}</strong>{!mp.valid && <span className="badge err">Invalid solid</span>}</div>
            <div className="field" style={{ marginTop: 8 }}>
              <label htmlFor={`mat-${b.name}`}>Material</label>
              <select id={`mat-${b.name}`} className="select" disabled={readonly} value={assigned?.material_id ?? (assigned ? "__custom" : "")}
                onChange={(e) => {
                  const k = e.target.value;
                  if (!k) { onSetMaterial(b.name, null); return; }
                  const m = mats.data?.items.find((x) => x.key === k);
                  if (m) onSetMaterial(b.name, {
                    name: m.name, density_kg_m3: m.density_kg_m3, youngs_modulus_mpa: m.youngs_modulus_mpa, poisson_ratio: m.poisson_ratio,
                    yield_strength_mpa: m.yield_strength_mpa, thermal_conductivity_w_mk: m.thermal_conductivity_w_mk, specific_heat_j_kgk: m.specific_heat_j_kgk,
                    source: `${m.source} (${m.value_basis} values; ${m.verification_status})`.slice(0, 500), material_id: m.key,
                  });
                }}>
                <option value="">No material (mass unavailable)</option>
                {assigned && !assigned.material_id && <option value="__custom">{assigned.name}</option>}
                {(mats.data?.items ?? []).map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
              </select>
              {assigned?.source && <span className="hint">Source: {assigned.source}</span>}
            </div>
            <dl className="kv" style={{ marginTop: 8 }}>
              <dt>Volume</dt><dd>{fmt(mp.volume_mm3 / LENGTH_UNITS[unit] ** 3, 6)} {unit}³</dd>
              <dt>Surface area</dt><dd>{fmt(mp.surface_area_mm2 / LENGTH_UNITS[unit] ** 2, 6)} {unit}²</dd>
              <dt>Mass</dt><dd>{mp.mass_kg != null ? `${fmt(mp.mass_kg, 5)} kg` : "assign a material"}</dd>
              <dt>Centroid</dt><dd>[{mp.centroid_mm.map((x) => fmt(x / LENGTH_UNITS[unit], 5)).join(", ")}] {unit}</dd>
              {mp.bbox_mm && <><dt>Bounding box</dt><dd>{mp.bbox_mm[1].map((x, i) => fmt((x - mp.bbox_mm![0][i]) / LENGTH_UNITS[unit], 5)).join(" × ")} {unit}</dd></>}
              {mp.principal_moments_kg_mm2 && <><dt>Principal moments</dt><dd>{mp.principal_moments_kg_mm2.map((x) => fmt(x, 4)).join(", ")} kg·mm²</dd></>}
              <dt>Solids</dt><dd>{mp.solids}</dd>
              <dt>Tessellation</dt><dd>{b.triangle_count.toLocaleString()} triangles · {b.faces.length} faces · {b.edges.length} edges</dd>
            </dl>
          </div>
        );
      })}
      <div className="faint tiny">Mass properties are computed from the exact B-rep (OCCT GProp), not the display mesh. Material values are typical/nominal reference values; verify against your supplier&apos;s certified data.</div>
    </div>
  );
}

function CommentsTab({ projectId, docId, selection, canComment, revision, onFocusAnchor, getCamera }: {
  projectId: string; docId: string; selection: Set<string>; canComment: boolean; revision: number;
  onFocusAnchor: (a: Record<string, any>) => void; getCamera: () => Record<string, unknown> | null;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [anchorSel, setAnchorSel] = useState(true);
  const key = ["comments", projectId, "document", docId];
  const q = useQuery({
    queryKey: key,
    queryFn: () => get<{ items: Comment[] }>(apiPath("/api/v1/projects/{project_id}/comments", { project_id: projectId }, { target_type: "document", target_id: docId })),
  });
  const first = [...selection].map(parseSel).find((s) => s.kind === "face");
  const create = useMutation({
    mutationFn: (body: string) => post(apiPath("/api/v1/projects/{project_id}/comments", { project_id: projectId }), {
      target_type: "document", target_id: docId, body,
      anchor: { face: anchorSel && first ? first.face : null, body: anchorSel && first ? first.body : null, camera: getCamera(), revision },
    }),
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["comments", projectId] }); },
    onError: (e) => toast.err("Comment not posted", String((e as Error).message)),
  });
  const resolve = useMutation({
    mutationFn: ({ id, resolved }: { id: string; resolved: boolean }) => patch(apiPath("/api/v1/comments/{comment_id}", { comment_id: id }), { resolved }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comments", projectId] }),
  });
  const items = q.data?.items ?? [];
  const roots = items.filter((c) => !c.parent_id);
  return (
    <div className="col gap-12">
      {canComment && (
        <form className="col gap-4" onSubmit={(e) => { e.preventDefault(); if (text.trim()) create.mutate(text.trim()); }}>
          <textarea className="textarea" rows={3} placeholder="Add a review comment…" aria-label="New comment" value={text} onChange={(e) => setText(e.target.value)} maxLength={10000} />
          <div className="row between">
            <label className="checkbox small"><input type="checkbox" checked={anchorSel} onChange={(e) => setAnchorSel(e.target.checked)} disabled={!first} />
              {first ? <>Pin to face <code>{first.face}</code></> : "Select a face to pin the comment"}</label>
            <button className="btn primary sm" disabled={!text.trim() || create.isPending}>Comment</button>
          </div>
        </form>
      )}
      {q.error && <ErrorBox error={q.error} />}
      {roots.map((c) => (
        <div key={c.id} className="card tight" style={{ opacity: c.resolved ? 0.6 : 1 }}>
          <div className="row between">
            <strong className="small">{c.author.display_name}</strong>
            <span className="faint tiny">{new Date(c.created_at).toLocaleString()}</span>
          </div>
          <div style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{c.body}</div>
          <div className="row">
            {c.anchor && (c.anchor.face || c.anchor.camera) && (
              <button className="btn ghost sm" onClick={() => onFocusAnchor(c.anchor!)}>Show{c.anchor.face ? ` ${c.anchor.face}` : " view"}{c.anchor.revision ? ` (r${c.anchor.revision})` : ""}</button>
            )}
            <div className="grow" />
            {canComment && <button className="btn ghost sm" onClick={() => resolve.mutate({ id: c.id, resolved: !c.resolved })}><Check size={12} />{c.resolved ? "Reopen" : "Resolve"}</button>}
          </div>
          {items.filter((r) => r.parent_id === c.id).map((r) => (
            <div key={r.id} className="small" style={{ borderLeft: "2px solid var(--border)", paddingLeft: 8, marginTop: 6 }}>
              <strong>{r.author.display_name}</strong>: {r.body}
            </div>
          ))}
        </div>
      ))}
      {!roots.length && !q.isLoading && <div className="faint small">No comments on this model yet.</div>}
    </div>
  );
}
