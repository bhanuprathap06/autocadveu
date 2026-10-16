"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiPath, get, post } from "@/api/client";
import type { DocMeta, PartModel, Revision } from "@/api/types";
import { Dialog, ErrorBox, Field, Loading, StatusBadge } from "@/components/ui";
import { Viewport, faceKey } from "@/components/viewer/Viewport";
import { fmt } from "@/lib/mesh";

type Analysis = "static" | "thermal";
interface BC { kind: "fixed" | "pressure" | "force" | "gravity" | "temperature" | "heat_flux" | "convection"; faces: string[]; a: number; b: number; c: number }
interface Capability { key: string; name: string; solver: string; status: string; limits: string }
interface MaterialRow { key: string; name: string; density_kg_m3: number; youngs_modulus_mpa: number | null; poisson_ratio: number | null; yield_strength_mpa: number | null; thermal_conductivity_w_mk: number | null; source: string; value_basis: string }

const BC_META: Record<BC["kind"], { label: string; analysis: Analysis; needsFaces: boolean; fields: string[]; defaults: [number, number, number] }> = {
  fixed: { label: "Fixed support", analysis: "static", needsFaces: true, fields: [], defaults: [0, 0, 0] },
  pressure: { label: "Pressure", analysis: "static", needsFaces: true, fields: ["Pressure (MPa, + pushes in)"], defaults: [1, 0, 0] },
  force: { label: "Force (total, N)", analysis: "static", needsFaces: true, fields: ["Fx (N)", "Fy (N)", "Fz (N)"], defaults: [0, 0, -1000] },
  gravity: { label: "Gravity", analysis: "static", needsFaces: false, fields: ["gx (mm/s²)", "gy (mm/s²)", "gz (mm/s²)"], defaults: [0, 0, -9806.65] },
  temperature: { label: "Fixed temperature", analysis: "thermal", needsFaces: true, fields: ["T (°C)"], defaults: [20, 0, 0] },
  heat_flux: { label: "Heat flux", analysis: "thermal", needsFaces: true, fields: ["q (W/mm², + into body)"], defaults: [0.01, 0, 0] },
  convection: { label: "Convection", analysis: "thermal", needsFaces: true, fields: ["h (W/mm²K)", "Ambient (°C)"], defaults: [0.00001, 20, 0] },
};

export function SimulationWizard({ open, onClose, projectId, docs }: { open: boolean; onClose: () => void; projectId: string; docs: DocMeta[] }) {
  const router = useRouter();
  const parts = docs.filter((d) => d.kind === "part");
  const [step, setStep] = useState(0);
  const [docId, setDocId] = useState(parts[0]?.id ?? "");
  const [body, setBody] = useState("");
  const [analysis, setAnalysis] = useState<Analysis>("static");
  const [mat, setMat] = useState({ name: "", youngs_modulus_mpa: "", poisson_ratio: "", density_kg_m3: "", yield_strength_mpa: "", thermal_conductivity_w_mk: "", source: "" });
  const [bcs, setBcs] = useState<BC[]>([]);
  const [active, setActive] = useState(0);
  const [mesh, setMesh] = useState({ auto: true, size: "", convergence: false });
  const [name, setName] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [idem] = useState(() => `wiz-${crypto.randomUUID()}`);

  const caps = useQuery({ queryKey: ["sim-caps"], queryFn: () => get<{ items: Capability[]; calculix_available: boolean }>(apiPath("/api/v1/simulation/capabilities")), enabled: open });
  const rev = useQuery({ queryKey: ["document", docId, "head"], queryFn: () => get<{ revision: Revision; document: DocMeta }>(apiPath("/api/v1/documents/{document_id}", { document_id: docId })), enabled: open && !!docId });
  const model = useQuery({
    queryKey: ["sim-model", docId, rev.data?.revision.number],
    queryFn: () => post<PartModel>(apiPath("/api/v1/documents/{document_id}/recompute", { document_id: docId }), { revision: rev.data!.revision.number, quality: "normal" }),
    enabled: open && !!rev.data,
  });
  const mats = useQuery({ queryKey: ["materials"], queryFn: () => get<{ items: MaterialRow[] }>(apiPath("/api/v1/materials")), enabled: open, staleTime: 3600_000 });
  const bm = model.data?.bodies.find((b) => b.name === body);

  useEffect(() => { if (model.data?.bodies.length && !model.data.bodies.some((b) => b.name === body)) setBody(model.data.bodies[0].name); }, [model.data, body]);
  useEffect(() => {
    const m = (rev.data?.revision.content as { materials?: Record<string, any> } | undefined)?.materials?.[body];
    if (m) setMat({ name: m.name, youngs_modulus_mpa: m.youngs_modulus_mpa ?? "", poisson_ratio: m.poisson_ratio ?? "", density_kg_m3: m.density_kg_m3 ?? "",
      yield_strength_mpa: m.yield_strength_mpa ?? "", thermal_conductivity_w_mk: m.thermal_conductivity_w_mk ?? "", source: m.source ?? "" });
  }, [rev.data, body]);
  useEffect(() => { setBcs(analysis === "static" ? [{ kind: "fixed", faces: [], a: 0, b: 0, c: 0 }, { kind: "force", faces: [], a: 0, b: 0, c: -1000 }]
    : [{ kind: "temperature", faces: [], a: 20, b: 0, c: 0 }, { kind: "heat_flux", faces: [], a: 0.01, b: 0, c: 0 }]); setActive(0); }, [analysis, body]);

  const selected = useMemo(() => new Set((bcs[active]?.faces ?? []).map((f) => faceKey(body, f))), [bcs, active, body]);
  const num = (s: string | number) => (s === "" || s === null || s === undefined ? null : Number(s));

  const setup = () => {
    const m = { name: mat.name || "Unnamed material", youngs_modulus_mpa: num(mat.youngs_modulus_mpa), poisson_ratio: num(mat.poisson_ratio), density_kg_m3: num(mat.density_kg_m3),
      yield_strength_mpa: num(mat.yield_strength_mpa), thermal_conductivity_w_mk: num(mat.thermal_conductivity_w_mk), source: mat.source };
    const meshS = { size_mm: mesh.auto ? null : num(mesh.size), min_size_mm: null, order: 2, optimize: true };
    if (analysis === "static") {
      return { analysis, body, material: m, mesh: meshS, convergence_study: mesh.convergence,
        supports: bcs.filter((b) => b.kind === "fixed").map((b) => ({ type: "fixed", faces: b.faces })),
        loads: bcs.filter((b) => b.kind === "pressure" || b.kind === "force" || b.kind === "gravity").map((b) =>
          b.kind === "pressure" ? { type: "pressure", faces: b.faces, value_mpa: b.a } : b.kind === "force" ? { type: "force", faces: b.faces, vector_n: [b.a, b.b, b.c] }
            : { type: "gravity", vector_mm_s2: [b.a, b.b, b.c] }) };
    }
    return { analysis, body, material: m, mesh: meshS, convergence_study: mesh.convergence,
      temperatures: bcs.filter((b) => b.kind === "temperature").map((b) => ({ type: "temperature", faces: b.faces, value_c: b.a })),
      fluxes: bcs.filter((b) => b.kind === "heat_flux").map((b) => ({ type: "heat_flux", faces: b.faces, value_w_mm2: b.a })),
      convection: bcs.filter((b) => b.kind === "convection").map((b) => ({ type: "convection", faces: b.faces, h_w_mm2k: b.a, ambient_c: b.b })) };
  };

  const problems: string[] = [];
  if (analysis === "static") {
    if (!num(mat.youngs_modulus_mpa) || num(mat.poisson_ratio) === null) problems.push("Static analysis needs Young's modulus and Poisson's ratio.");
    if (!bcs.some((b) => b.kind === "fixed" && b.faces.length)) problems.push("Add at least one fixed support with faces.");
    if (!bcs.some((b) => b.kind !== "fixed" && (b.faces.length || b.kind === "gravity"))) problems.push("Add at least one load.");
    if (bcs.some((b) => b.kind === "gravity") && !num(mat.density_kg_m3)) problems.push("Gravity needs material density.");
  } else {
    if (!num(mat.thermal_conductivity_w_mk)) problems.push("Thermal analysis needs thermal conductivity.");
    if (!bcs.some((b) => b.kind === "temperature" && b.faces.length)) problems.push("Add at least one fixed-temperature face.");
  }
  if (bcs.some((b) => BC_META[b.kind].needsFaces && !b.faces.length)) problems.push("Every boundary condition needs at least one face (or remove it).");
  if (!mat.source.trim()) problems.push("State the source of the material values.");

  const steps = ["Model", "Analysis & material", "Boundary conditions", "Mesh & submit"];
  return (
    <Dialog open={open} onClose={onClose} title={`New simulation — ${steps[step]}`} wide
      footer={<>
        <span className="faint small grow">Step {step + 1} of {steps.length}</span>
        <button className="btn" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        {step < steps.length - 1
          ? <button className="btn primary" disabled={(step === 0 && !bm)} onClick={() => setStep(step + 1)}>Next</button>
          : <button className="btn primary" disabled={busy || problems.length > 0 || !name.trim() || caps.data?.calculix_available === false} onClick={async () => {
              setBusy(true); setErr(null);
              try {
                const r = await post<{ simulation: { id: string } }>(apiPath("/api/v1/projects/{project_id}/simulations", { project_id: projectId }),
                  { document_id: docId, revision: rev.data?.revision.number, name: name.trim(), setup: setup(), idempotency_key: idem });
                onClose(); router.push(`/app/simulations/${r.simulation.id}`);
              } catch (e) { setErr(e); } finally { setBusy(false); }
            }}>{busy && <span className="spinner" />}Submit to CalculiX</button>}
      </>}>
      {step === 0 && (
        <div className="col gap-12">
          <Field label="Part document" htmlFor="sd">
            <select id="sd" className="select" value={docId} onChange={(e) => setDocId(e.target.value)}>
              {parts.map((d) => <option key={d.id} value={d.id}>{d.name} (r{d.head_revision})</option>)}
            </select>
          </Field>
          {(rev.isLoading || model.isLoading) && <Loading label="Recomputing model…" />}
          {model.error && <ErrorBox error={model.error} />}
          {model.data && !model.data.ok && <div className="alert err small">The model has failing features; fix them before simulating.</div>}
          {model.data && (
            <Field label="Body" htmlFor="sb" hint={`Simulation runs on revision ${rev.data?.revision.number}; results stay linked to it.`}>
              <select id="sb" className="select" value={body} onChange={(e) => setBody(e.target.value)}>
                {model.data.bodies.map((b) => <option key={b.name} value={b.name}>{b.name} — {fmt(b.mass.volume_mm3 / 1000, 4)} cm³</option>)}
              </select>
            </Field>
          )}
        </div>
      )}
      {step === 1 && (
        <div className="col gap-12">
          <div className="grid grid-2">
            {caps.data?.items.filter((c) => c.key === "static" || c.key === "thermal" || c.key === "cfd" || c.key === "modal").map((c) => (
              <button key={c.key} type="button" className="card tight hover" disabled={c.key !== "static" && c.key !== "thermal"}
                aria-pressed={analysis === c.key} onClick={() => (c.key === "static" || c.key === "thermal") && setAnalysis(c.key)}
                style={{ textAlign: "left", cursor: c.key === "static" || c.key === "thermal" ? "pointer" : "not-allowed", borderColor: analysis === c.key ? "var(--accent)" : undefined, opacity: c.key === "static" || c.key === "thermal" ? 1 : 0.6 }}>
                <div className="row between"><strong>{c.name}</strong><StatusBadge status={c.status} /></div>
                <div className="small muted">{c.solver} · {c.limits}</div>
              </button>
            ))}
          </div>
          {caps.data && !caps.data.calculix_available && <div className="alert err small">CalculiX is not installed on this server. Submitting would fail; ask an administrator to install it.</div>}
          <div className="row">
            <span className="label">Material library</span>
            <select className="select" style={{ width: 320 }} aria-label="Load material from library" value="" onChange={(e) => {
              const m = mats.data?.items.find((x) => x.key === e.target.value);
              if (m) setMat({ name: m.name, youngs_modulus_mpa: String(m.youngs_modulus_mpa ?? ""), poisson_ratio: String(m.poisson_ratio ?? ""), density_kg_m3: String(m.density_kg_m3),
                yield_strength_mpa: String(m.yield_strength_mpa ?? ""), thermal_conductivity_w_mk: String(m.thermal_conductivity_w_mk ?? ""), source: `${m.source} (${m.value_basis} values)`.slice(0, 500) });
            }}>
              <option value="">Load from library…</option>
              {mats.data?.items.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
            </select>
          </div>
          <div className="grid grid-3">
            <Field label="Material name"><input className="input" value={mat.name} onChange={(e) => setMat({ ...mat, name: e.target.value })} /></Field>
            <Field label="Young's modulus (MPa)"><input className="input num" value={mat.youngs_modulus_mpa} onChange={(e) => setMat({ ...mat, youngs_modulus_mpa: e.target.value })} /></Field>
            <Field label="Poisson's ratio"><input className="input num" value={mat.poisson_ratio} onChange={(e) => setMat({ ...mat, poisson_ratio: e.target.value })} /></Field>
            <Field label="Density (kg/m³)"><input className="input num" value={mat.density_kg_m3} onChange={(e) => setMat({ ...mat, density_kg_m3: e.target.value })} /></Field>
            <Field label="Yield strength (MPa)"><input className="input num" value={mat.yield_strength_mpa} onChange={(e) => setMat({ ...mat, yield_strength_mpa: e.target.value })} /></Field>
            <Field label="Conductivity (W/m·K)"><input className="input num" value={mat.thermal_conductivity_w_mk} onChange={(e) => setMat({ ...mat, thermal_conductivity_w_mk: e.target.value })} /></Field>
          </div>
          <Field label="Source of material values" hint="Required. Results are only as good as these numbers."><input className="input" value={mat.source} maxLength={500} onChange={(e) => setMat({ ...mat, source: e.target.value })} /></Field>
        </div>
      )}
      {step === 2 && bm && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: 12, height: "56vh" }}>
          <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden", position: "relative" }}>
            <Viewport bodies={[{ key: body, label: body, mesh: bm }]} selected={selected} pickMode="face"
              onPick={(p) => {
                if (!p?.face || !bcs[active] || !BC_META[bcs[active].kind].needsFaces) return;
                const f = p.face.name;
                setBcs(bcs.map((b, i) => i !== active ? b : { ...b, faces: b.faces.includes(f) ? b.faces.filter((x) => x !== f) : [...b.faces, f] }));
              }} />
            <div className="vp-status">Click faces to add/remove them from the highlighted condition.</div>
          </div>
          <div className="col" style={{ overflow: "auto" }}>
            {bcs.map((b, i) => (
              <div key={i} className="card tight" style={{ borderColor: i === active ? "var(--accent)" : undefined, cursor: "pointer" }} onClick={() => setActive(i)}>
                <div className="row between">
                  <select className="select" style={{ height: 24 }} aria-label="Condition type" value={b.kind} onChange={(e) => {
                    const k = e.target.value as BC["kind"]; const d = BC_META[k].defaults;
                    setBcs(bcs.map((x, j) => j === i ? { kind: k, faces: BC_META[k].needsFaces ? x.faces : [], a: d[0], b: d[1], c: d[2] } : x));
                  }}>
                    {(Object.keys(BC_META) as BC["kind"][]).filter((k) => BC_META[k].analysis === analysis).map((k) => <option key={k} value={k}>{BC_META[k].label}</option>)}
                  </select>
                  <button className="btn ghost icon sm" aria-label="Remove condition" onClick={(e) => { e.stopPropagation(); setBcs(bcs.filter((_, j) => j !== i)); setActive(0); }}><Trash2 size={12} /></button>
                </div>
                {BC_META[b.kind].fields.map((label, k) => (
                  <label key={k} className="row small" style={{ marginTop: 4 }}><span className="grow">{label}</span>
                    <input className="input num" style={{ width: 110, height: 24 }} type="number" step="any" value={[b.a, b.b, b.c][k]}
                      onChange={(e) => setBcs(bcs.map((x, j) => j === i ? { ...x, [["a", "b", "c"][k]]: Number(e.target.value) } : x))} /></label>
                ))}
                {BC_META[b.kind].needsFaces && <div className="tiny faint" style={{ marginTop: 4 }}>{b.faces.length ? b.faces.join(", ") : "No faces selected"}</div>}
              </div>
            ))}
            <button className="btn sm" onClick={() => { const k: BC["kind"] = analysis === "static" ? "pressure" : "convection"; const d = BC_META[k].defaults; setBcs([...bcs, { kind: k, faces: [], a: d[0], b: d[1], c: d[2] }]); setActive(bcs.length); }}>
              <Plus size={13} />Add condition</button>
          </div>
        </div>
      )}
      {step === 3 && (
        <div className="col gap-12">
          <Field label="Simulation name" htmlFor="sn"><input id="sn" className="input" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} placeholder={`${analysis === "static" ? "Static" : "Thermal"} – ${body}`} /></Field>
          <div className="grid grid-2">
            <label className="checkbox"><input type="checkbox" checked={mesh.auto} onChange={(e) => setMesh({ ...mesh, auto: e.target.checked })} />Automatic element size (bounding-box diagonal / 25)</label>
            {!mesh.auto && <Field label="Target element size (mm)"><input className="input num" value={mesh.size} onChange={(e) => setMesh({ ...mesh, size: e.target.value })} /></Field>}
          </div>
          <label className="checkbox"><input type="checkbox" checked={mesh.convergence} onChange={(e) => setMesh({ ...mesh, convergence: e.target.checked })} />
            Run a mesh-sensitivity check (second solve with 1.6× finer mesh; roughly triples run time)</label>
          <div className="small muted">Elements: quadratic tetrahedra (C3D10) meshed by Gmsh. Solver: CalculiX. The run is queued and executed in an isolated worker with time and memory limits.</div>
          {problems.length > 0 && <div className="alert warn small"><ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul></div>}
          {err ? <ErrorBox error={err} /> : null}
        </div>
      )}
    </Dialog>
  );
}
