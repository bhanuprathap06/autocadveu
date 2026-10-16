"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, FileText, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { apiPath, download, errorMessage, get, post } from "@/api/client";
import type { SimSummary } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { FieldViewer, Legend, type Field, type Quantity } from "@/components/sim/FieldViewer";
import { ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { LineChart } from "@/components/ui/chart";
import { toast } from "@/components/ui/toast";
import { fmt } from "@/lib/mesh";
import { useProjectChannel } from "@/lib/realtime";

function Sim() {
  const { simId } = useParams<{ simId: string }>();
  const q = useQuery({
    queryKey: ["simulation", simId],
    queryFn: () => get<{ simulation: SimSummary & { project_id?: string } }>(apiPath("/api/v1/simulations/{sim_id}", { sim_id: simId })),
    refetchInterval: (query) => (["queued", "running"].includes(query.state.data?.simulation.status ?? "") ? 2500 : false),
  });
  const s = q.data?.simulation;
  const done = s?.status === "succeeded";
  const field = useQuery({ queryKey: ["sim-field", simId], queryFn: () => get<Field>(apiPath("/api/v1/simulations/{sim_id}/field", { sim_id: simId })), enabled: done, staleTime: Infinity });
  const [quantity, setQuantity] = useState<Quantity | null>(null);
  const [deform, setDeform] = useState(0);
  const [probe, setProbe] = useState<{ value: number; at: number[] } | null>(null);
  const qty: Quantity = quantity ?? (s?.analysis === "thermal" ? "temperature" : "von_mises");
  const range = useMemo<[number, number]>(() => (field.data?.[`${qty}_range` as const] as [number, number] | undefined) ?? [0, 1], [field.data, qty]);
  const autoScale = useMemo(() => {
    if (!field.data?.displacement_range || !field.data.positions.length) return 1;
    const P = field.data.positions; let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < P.length; i++) { lo = Math.min(lo, P[i]); hi = Math.max(hi, P[i]); }
    return field.data.displacement_range[1] > 0 ? (0.08 * (hi - lo)) / field.data.displacement_range[1] : 1;
  }, [field.data]);
  const sum = s?.result?.summary ?? {};
  const unit = qty === "temperature" ? "°C" : qty === "von_mises" ? "MPa" : "mm";
  return (
    <div className="page col gap-16" style={{ maxWidth: 1400 }}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {s && (
        <>
          <div className="row between wrap">
            <div>
              <h1>{s.name}</h1>
              <div className="small muted">{s.analysis === "static" ? "Linear static" : "Steady-state thermal"} · body <code>{s.body}</code> · model revision r{s.revision} ·
                {" "}<Link href={`/app/workbench/${s.document_id}`}>open model</Link> · solver {s.solver_version ?? s.solver}</div>
            </div>
            <div className="row">
              <StatusBadge status={s.status} />
              {(s.status === "queued" || s.status === "running") && (
                <button className="btn danger sm" disabled={s.cancel_requested} onClick={async () => {
                  try { await post(apiPath("/api/v1/simulations/{sim_id}/cancel", { sim_id: simId })); q.refetch(); } catch (e) { toast.err("Cancel failed", errorMessage(e)); }
                }}><XCircle size={13} />{s.cancel_requested ? "Cancelling…" : "Cancel"}</button>
              )}
              <button className="btn sm" onClick={() => download("GET", apiPath("/api/v1/simulations/{sim_id}/report", { sim_id: simId }))}><FileText size={13} />Report</button>
            </div>
          </div>
          {(s.status === "queued" || s.status === "running") && (
            <div className="card">
              <div className="row between small"><span>{s.progress?.stage ?? "Waiting for a worker"}</span><span className="num">{Math.round((s.progress?.fraction ?? 0) * 100)}%</span></div>
              <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(s.progress?.fraction ?? 0.02) * 100}%` }} /></div>
              <div className="faint tiny" style={{ marginTop: 6 }}>The job runs in an isolated worker; you can leave this page. Results appear here when the solver finishes.</div>
            </div>
          )}
          {(s.status === "failed" || s.status === "timed_out" || s.status === "cancelled") && (
            <div className="alert err"><div><strong>{s.status === "cancelled" ? "Cancelled" : "The simulation did not produce results."}</strong>
              {s.error && <div className="small" style={{ marginTop: 4 }}>{s.error}</div>}
              <div className="small muted" style={{ marginTop: 4 }}>No results are shown for failed runs. Check the solver log for details.</div></div></div>
          )}
          {done && (
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 340px", gap: 16 }}>
              <div className="card" style={{ padding: 0, height: "62vh", position: "relative", overflow: "hidden" }}>
                {field.isLoading && <Loading label="Loading result field…" />}
                {field.error && <ErrorBox error={field.error} />}
                {field.data && <FieldViewer field={field.data} quantity={qty} deform={qty === "temperature" ? 0 : deform * autoScale} range={range} onProbe={setProbe} />}
                <div className="vp-toolbar">
                  {s.analysis === "static" ? (
                    <>
                      <button className="btn ghost sm" aria-pressed={qty === "von_mises"} onClick={() => setQuantity("von_mises")}>Von Mises stress</button>
                      <button className="btn ghost sm" aria-pressed={qty === "displacement"} onClick={() => setQuantity("displacement")}>Displacement</button>
                      <label className="row small" style={{ padding: "0 6px" }}>Deformation ×{fmt(deform * autoScale, 3)}
                        <input type="range" min={0} max={1} step={0.01} value={deform} onChange={(e) => setDeform(Number(e.target.value))} aria-label="Deformation scale" /></label>
                    </>
                  ) : <span className="small" style={{ padding: "0 8px" }}>Temperature</span>}
                </div>
                <div style={{ position: "absolute", left: 10, top: 48, background: "rgba(20,24,29,.85)", padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}>
                  <Legend range={range} unit={unit} label={qty === "von_mises" ? "Von Mises (nodal)" : qty === "displacement" ? "|u|" : "Temperature"} />
                </div>
                <div className="vp-status">{probe ? <>Probe: <strong className="num">{fmt(probe.value, 5)} {unit}</strong> at ({probe.at.map((x) => fmt(x, 4)).join(", ")}) mm</> : "Hover the model to probe values"}
                  {deform > 0 && qty !== "temperature" && <span style={{ color: "var(--warn)" }}> · deformation exaggerated</span>}</div>
              </div>
              <div className="col gap-12">
                <section className="card">
                  <h3 style={{ marginBottom: 8 }}>Results</h3>
                  <dl className="kv">
                    {s.analysis === "static" ? <>
                      <dt>Max von Mises</dt><dd>{fmt(sum.max_von_mises_mpa, 5)} MPa</dd>
                      {sum.max_surface_von_mises_mpa != null && <><dt>Max surface von Mises</dt><dd>{fmt(sum.max_surface_von_mises_mpa, 5)} MPa</dd></>}
                      <dt>Max displacement</dt><dd>{fmt(sum.max_displacement_mm, 5)} mm</dd>
                      {sum.safety_factor_vs_yield != null && <><dt>Yield / peak stress</dt><dd>{fmt(sum.safety_factor_vs_yield, 3)}</dd></>}
                      {sum.equilibrium && <><dt>Equilibrium</dt><dd>{sum.equilibrium.ok ? "balanced" : "NOT balanced"} ({fmt(sum.equilibrium.relative_imbalance * 100, 2)}% imbalance)</dd></>}
                    </> : <>
                      <dt>Max temperature</dt><dd>{fmt(sum.max_temperature_c, 5)} °C</dd>
                      <dt>Min temperature</dt><dd>{fmt(sum.min_temperature_c, 5)} °C</dd>
                      {sum.applied_heat_w != null && <><dt>Applied heat</dt><dd>{fmt(sum.applied_heat_w, 5)} W</dd></>}
                    </>}
                  </dl>
                  {sum.safety_factor_note && <div className="faint tiny" style={{ marginTop: 6 }}>{sum.safety_factor_note}</div>}
                  {sum.stress_note && <div className="faint tiny" style={{ marginTop: 6 }}>{sum.stress_note}</div>}
                </section>
                {s.result?.mesh && (
                  <section className="card">
                    <h3 style={{ marginBottom: 8 }}>Mesh</h3>
                    <dl className="kv">
                      <dt>Elements</dt><dd>{s.result.mesh.elements?.toLocaleString()} × {s.result.mesh.element_type}</dd>
                      <dt>Nodes</dt><dd>{s.result.mesh.nodes?.toLocaleString()}</dd>
                      <dt>Target size</dt><dd>{fmt(s.result.mesh.size_mm, 4)} mm</dd>
                      <dt>Quality ({s.result.mesh.quality_metric})</dt><dd>min {fmt(s.result.mesh.quality_min, 3)} · mean {fmt(s.result.mesh.quality_mean, 3)}</dd>
                    </dl>
                  </section>
                )}
                {s.result?.convergence && (
                  <section className="card">
                    <h3 style={{ marginBottom: 8 }}>Mesh sensitivity</h3>
                    <StatusBadge status={s.result.convergence.converged_5pct ? "ok" : "warning"} label={s.result.convergence.converged_5pct ? "Changes < 5 %" : "Not converged (≥ 5 % change)"} />
                    <LineChart xLabel="element size (mm)" yLabel="relative change (%)" height={150} series={Object.entries(s.result.convergence.changes as Record<string, { relative_change: number }>).map(([k, v], i) => ({
                      name: k, color: ["#27b6ec", "#f2b73f", "#3ccf8e"][i % 3],
                      points: [[s.result!.convergence!.sizes_mm[0], 0], [s.result!.convergence!.sizes_mm[1], 100 * v.relative_change]] as [number, number][],
                    }))} />
                  </section>
                )}
                {s.result?.warnings?.length ? <div className="alert warn small"><ul style={{ margin: 0, paddingLeft: 18 }}>{s.result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div> : null}
              </div>
            </div>
          )}
          <section className="card">
            <h3 style={{ marginBottom: 8 }}>Setup and solver files</h3>
            <div className="grid grid-2">
              <pre className="mono small" style={{ margin: 0, maxHeight: 260, overflow: "auto", background: "var(--bg-elev)", padding: 10, borderRadius: 6 }}>{JSON.stringify(s.setup, null, 2)}</pre>
              <div className="col">
                {Object.entries(s.result?.artifacts ?? {}).map(([name, a]) => (
                  <button key={name} className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => download("GET", apiPath("/api/v1/simulations/{sim_id}/artifacts/{name}", { sim_id: simId, name }))}>
                    <Download size={13} />{name}<span className="faint tiny">{(a.size / 1024).toFixed(1)} KB · {a.sha256.slice(0, 10)}…</span></button>
                ))}
                {!Object.keys(s.result?.artifacts ?? {}).length && <span className="faint small">No solver files yet.</span>}
                <div className="faint tiny">The CalculiX input deck (.inp), results (.frd/.dat) and solver log are kept so results can be reproduced independently.</div>
              </div>
            </div>
          </section>
          <div className="faint small">Linear analysis results depend on the mesh, idealised boundary conditions and stated material data. They are not a certification of strength, durability or safety.</div>
          <ProjectRefresh simId={simId} documentId={s.document_id} />
        </>
      )}
    </div>
  );
}

/** Subscribes to the owning project's channel so job status changes refresh this page without polling delay. */
function ProjectRefresh({ simId, documentId }: { simId: string; documentId: string }) {
  const doc = useQuery({ queryKey: ["document", documentId, "meta"], queryFn: () => get<{ project: { id: string } }>(apiPath("/api/v1/documents/{document_id}", { document_id: documentId })), staleTime: Infinity });
  useProjectChannel(doc.data?.project.id);
  void simId;
  return null;
}

export default function Page() {
  return <AppShell crumbs={[{ href: "/app/projects", label: "Projects" }, { label: "Simulation" }]}><Sim /></AppShell>;
}
