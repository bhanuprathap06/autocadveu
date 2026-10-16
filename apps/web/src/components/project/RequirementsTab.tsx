"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCheck, FlaskConical, Lightbulb, Plus, ShieldCheck, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, errorMessage, get, patch, post } from "@/api/client";
import type { Problem, Requirement, SimSummary, Template } from "@/api/types";
import { AssetBadge, ClaimBadge, Dialog, Empty, ErrorBox, Field, Loading, StatusBadge } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import type { ProjectDoc } from "./DocumentsTab";

interface AIStatus { configured_provider: string; external_available: boolean; model: string | null; fallback: string; data_policy: string }
interface ClaimT { text: string; claim_class: string; reference: string | null }
interface ConceptT { title: string; description: string; template: string | null; catalog_queries: string[]; claims: ClaimT[]; assumptions: string[]; catalog_matches: any[] }
interface ProposalT { template: string; name: string; parameters: { name: string; expression: string; rationale: string }[]; material_key: string | null; claims: ClaimT[] }

export function RequirementsTab({ projectId, canEdit, templates, docs }: { projectId: string; canEdit: boolean; templates: Template[]; docs: ProjectDoc[] }) {
  const qc = useQueryClient();
  const key = ["problems", projectId];
  const problems = useQuery({ queryKey: key, queryFn: () => get<{ items: Problem[] }>(apiPath("/api/v1/projects/{project_id}/problems", { project_id: projectId })) });
  const ai = useQuery({ queryKey: ["ai-status"], queryFn: () => get<AIStatus>(apiPath("/api/v1/ai/status")), staleTime: 600_000 });
  const [newOpen, setNewOpen] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const current = problems.data?.items.find((p) => p.id === sel) ?? problems.data?.items[0];
  return (
    <div className="col gap-12">
      <div className="alert info small">
        <Sparkles size={15} style={{ flex: "none" }} />
        <div>
          The assistant turns a problem statement into reviewable requirements, concepts and parametric proposals.
          Default engine: <strong>{ai.data?.fallback ?? "rules engine"}</strong> (runs locally).
          {ai.data?.external_available ? <> External model <strong>{ai.data.model}</strong> is available and is used only when you tick “Use external AI” for a request. </> : " No external AI provider is configured. "}
          {ai.data?.data_policy}
        </div>
      </div>
      <div className="row">
        <select className="select" style={{ maxWidth: 360 }} aria-label="Problem" value={current?.id ?? ""} onChange={(e) => setSel(e.target.value)}>
          {problems.data?.items.map((p) => <option key={p.id} value={p.id}>{p.title} ({p.status})</option>)}
          {!problems.data?.items.length && <option value="">No problem statements yet</option>}
        </select>
        {canEdit && <button className="btn primary sm" onClick={() => setNewOpen(true)}><Plus size={13} />New problem statement</button>}
      </div>
      {problems.isLoading && <Loading />}
      {problems.error && <ErrorBox error={problems.error} />}
      {problems.data && !problems.data.items.length && (
        <Empty icon={<Lightbulb size={28} />} title="Describe the engineering problem">
          e.g. “Design a bracket to hold a 2 kg sensor; mass under 0.3 kg; must fit within 120 × 80 × 60 mm; aluminium preferred.”
        </Empty>
      )}
      {current && <ProblemView key={current.id} problem={current} projectId={projectId} canEdit={canEdit} templates={templates} docs={docs}
        externalAvailable={!!ai.data?.external_available} refresh={() => qc.invalidateQueries({ queryKey: key })} />}
      <NewProblem open={newOpen} onClose={() => setNewOpen(false)} projectId={projectId} onCreated={(id) => { setSel(id); qc.invalidateQueries({ queryKey: key }); }} />
    </div>
  );
}

function NewProblem({ open, onClose, projectId, onCreated }: { open: boolean; onClose: () => void; projectId: string; onCreated: (id: string) => void }) {
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [err, setErr] = useState<unknown>(null);
  return (
    <Dialog open={open} onClose={onClose} title="New problem statement" wide
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!title.trim() || statement.trim().length < 10} onClick={async () => {
          try {
            const r = await post<{ problem: Problem }>(apiPath("/api/v1/projects/{project_id}/problems", { project_id: projectId }), { title, statement });
            onCreated(r.problem.id); onClose(); setTitle(""); setStatement("");
          } catch (e) { setErr(e); }
        }}>Create</button></>}>
      <Field label="Title" htmlFor="pt"><input id="pt" className="input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Problem statement" htmlFor="ps" hint="Include loads, limits, environment, materials and envelope. Numbers with units are extracted as measurable requirements.">
        <textarea id="ps" className="textarea" rows={7} value={statement} maxLength={20000} onChange={(e) => setStatement(e.target.value)} />
      </Field>
      {err ? <ErrorBox error={err} /> : null}
    </Dialog>
  );
}

function ProblemView({ problem: p, projectId, canEdit, templates, docs, externalAvailable, refresh }: {
  problem: Problem; projectId: string; canEdit: boolean; templates: Template[]; docs: ProjectDoc[]; externalAvailable: boolean; refresh: () => void;
}) {
  const router = useRouter();
  const [external, setExternal] = useState(false);
  const [concepts, setConcepts] = useState<{ provider: string; concepts: ConceptT[]; note: string } | null>(null);
  const [tpl, setTpl] = useState(templates[0]?.key ?? "");
  const [proposal, setProposal] = useState<{ provider: string; proposal: ProposalT; validation: { valid: boolean; errors: string[]; bodies?: { name: string; mass?: { mass_kg?: number } }[] } } | null>(null);
  const [verifyFor, setVerifyFor] = useState<Requirement | null>(null);
  const extract = useMutation({ mutationFn: () => post(apiPath("/api/v1/problems/{problem_id}/extract", { problem_id: p.id }), { allow_external_ai: external }), onSuccess: refresh,
    onError: (e) => toast.err("Extraction failed", errorMessage(e)) });
  const approve = useMutation({ mutationFn: () => post(apiPath("/api/v1/problems/{problem_id}/approve", { problem_id: p.id })), onSuccess: refresh,
    onError: (e) => toast.err("Cannot approve yet", errorMessage(e)) });
  const genConcepts = useMutation({ mutationFn: () => post<{ provider: string; concepts: ConceptT[]; note: string }>(apiPath("/api/v1/problems/{problem_id}/concepts", { problem_id: p.id }), { allow_external_ai: external }),
    onSuccess: setConcepts, onError: (e) => toast.err("Concept generation failed", errorMessage(e)) });
  const propose = useMutation({ mutationFn: () => post<NonNullable<typeof proposal>>(apiPath("/api/v1/problems/{problem_id}/proposal", { problem_id: p.id }), { template: tpl, allow_external_ai: external }),
    onSuccess: setProposal, onError: (e) => toast.err("Proposal failed", errorMessage(e)) });
  const setStatus = async (r: Requirement, status: Requirement["status"]) => {
    try { await patch(apiPath("/api/v1/requirements/{req_id}", { req_id: r.id }), { status }); refresh(); } catch (e) { toast.err("Update failed", errorMessage(e)); }
  };
  const pending = p.requirements.filter((r) => r.status === "proposed").length;
  return (
    <div className="col gap-16">
      <section className="card">
        <div className="card-title"><h3>{p.title}</h3><StatusBadge status={p.status === "approved" ? "approved" : p.status === "extracted" ? "proposed" : "queued"} label={p.status} /></div>
        <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{p.statement}</p>
        {p.extraction?.summary && <div className="small muted" style={{ marginTop: 8 }}>Summary ({p.extraction_provider}): {p.extraction.summary}</div>}
        {canEdit && (
          <div className="row wrap" style={{ marginTop: 12 }}>
            <label className="checkbox small" title={externalAvailable ? "Sends the problem statement and requirements (never CAD files) to the configured provider" : "No external provider configured"}>
              <input type="checkbox" checked={external} disabled={!externalAvailable} onChange={(e) => setExternal(e.target.checked)} />Use external AI for this request
            </label>
            <button className="btn sm" disabled={extract.isPending || p.status === "approved"} onClick={() => extract.mutate()}>
              {extract.isPending ? <span className="spinner" /> : <Sparkles size={13} />}{p.status === "draft" ? "Extract requirements" : "Re-extract"}</button>
            <button className="btn primary sm" disabled={approve.isPending || p.status === "approved" || !p.requirements.length} onClick={() => approve.mutate()}>
              <CheckCheck size={13} />Approve requirements{pending ? ` (${pending} to review)` : ""}</button>
          </div>
        )}
        {p.extraction?.clarifying_questions?.length ? (
          <div className="alert warn small" style={{ marginTop: 10 }}><div><strong>Open questions</strong><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {p.extraction.clarifying_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></div></div>
        ) : null}
      </section>

      <section>
        <h3 style={{ marginBottom: 8 }}>Requirements</h3>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Kind</th><th>Requirement</th><th>Target</th><th>Source / claim</th><th>Status</th><th>Verification</th><th aria-label="Actions" /></tr></thead>
            <tbody>
              {p.requirements.map((r) => (
                <tr key={r.id}>
                  <td className="small">{r.kind.replace(/_/g, " ")}</td>
                  <td>{r.text}</td>
                  <td className="num small">{r.comparator ? `${r.metric ?? ""} ${r.comparator} ${r.target_value} ${r.unit ?? ""}` : "—"}</td>
                  <td><span className="badge">{r.source}</span> <ClaimBadge cls={r.claim_class} /></td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="small">{r.verification ? <><StatusBadge status={r.verification.status} /> <span className="num">{Number(r.verification.value).toPrecision(5)} {r.verification.unit ?? ""}</span></> : "—"}</td>
                  <td className="nowrap">
                    {canEdit && r.status === "proposed" && <>
                      <button className="btn ghost icon sm" aria-label="Approve requirement" onClick={() => setStatus(r, "approved")}><Check size={13} /></button>
                      <button className="btn ghost icon sm" aria-label="Reject requirement" onClick={() => setStatus(r, "rejected")}><X size={13} /></button>
                    </>}
                    {canEdit && r.status === "approved" && r.comparator && r.target_value != null &&
                      <button className="btn ghost sm" onClick={() => setVerifyFor(r)}><ShieldCheck size={13} />Verify</button>}
                  </td>
                </tr>
              ))}
              {!p.requirements.length && <tr><td colSpan={7} className="faint">No requirements yet. Run extraction or add them manually.</td></tr>}
            </tbody>
          </table>
        </div>
        {canEdit && p.status !== "approved" && <AddRequirement projectId={projectId} problemId={p.id} onAdded={refresh} />}
      </section>

      {p.status === "approved" && (
        <section className="card">
          <div className="card-title"><h3>Concepts and proposals</h3>
            <button className="btn sm" disabled={genConcepts.isPending} onClick={() => genConcepts.mutate()}>{genConcepts.isPending ? <span className="spinner" /> : <Lightbulb size={13} />}Generate concepts</button></div>
          {concepts && (
            <div className="col gap-12">
              <div className="faint small">{concepts.note} Provider: {concepts.provider}.</div>
              <div className="grid grid-2">
                {concepts.concepts.map((c, i) => (
                  <div key={i} className="card tight">
                    <strong>{c.title}</strong>
                    <p className="small" style={{ margin: "6px 0" }}>{c.description}</p>
                    {c.template && <div className="small">Template: <code>{c.template}</code></div>}
                    {c.claims.map((cl, k) => <div key={k} className="small row" style={{ alignItems: "flex-start", marginTop: 4 }}><ClaimBadge cls={cl.claim_class} /><span>{cl.text}</span></div>)}
                    {c.assumptions.length > 0 && <div className="faint tiny" style={{ marginTop: 6 }}>Assumptions: {c.assumptions.join("; ")}</div>}
                    {c.catalog_matches.length > 0 && <div className="col" style={{ marginTop: 6 }}>
                      <span className="label">Catalog matches</span>
                      {c.catalog_matches.slice(0, 4).map((m) => <a key={m.id} href={`/app/catalog/${m.id}`} className="row small"><AssetBadge cls={m.asset_class} />{m.name}</a>)}
                    </div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="divider" />
          <div className="row wrap">
            <span className="small">Parametric proposal from template</span>
            <select className="select" style={{ width: 260 }} aria-label="Template" value={tpl} onChange={(e) => setTpl(e.target.value)}>
              {templates.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
            <button className="btn sm" disabled={!canEdit || propose.isPending || !tpl} onClick={() => propose.mutate()}>{propose.isPending ? <span className="spinner" /> : <FlaskConical size={13} />}Propose parameters</button>
          </div>
          {proposal && (
            <div className="col gap-8" style={{ marginTop: 12 }}>
              <div className={`alert ${proposal.validation.valid ? "ok" : "err"} small`}>
                {proposal.validation.valid ? "The proposal regenerates cleanly in the CAD kernel." : <>Kernel validation failed: {proposal.validation.errors.join("; ")}</>}
                {proposal.validation.bodies?.map((b) => b.mass?.mass_kg != null && <span key={b.name}> · {b.name} mass {b.mass.mass_kg.toFixed(3)} kg (calc.)</span>)}
              </div>
              <table className="table">
                <thead><tr><th>Parameter</th><th>Expression</th><th>Rationale</th></tr></thead>
                <tbody>{proposal.proposal.parameters.map((x) => <tr key={x.name}><td className="mono">{x.name}</td><td className="mono">{x.expression}</td><td className="small">{x.rationale}</td></tr>)}</tbody>
              </table>
              {proposal.proposal.material_key && <div className="small">Material: <code>{proposal.proposal.material_key}</code></div>}
              {proposal.proposal.claims.map((cl, k) => <div key={k} className="small row"><ClaimBadge cls={cl.claim_class} /><span>{cl.text}</span></div>)}
              <div className="row">
                <span className="faint small grow">Nothing is added to the project until you approve it.</span>
                <button className="btn primary sm" disabled={!proposal.validation.valid || !canEdit} onClick={async () => {
                  try {
                    const r = await post<{ document: { id: string } }>(apiPath("/api/v1/problems/{problem_id}/proposal/apply", { problem_id: p.id }), { proposal: proposal.proposal });
                    toast.ok("Proposal applied", "A new part document was created from the validated proposal.");
                    router.push(`/app/workbench/${r.document.id}`);
                  } catch (e) { toast.err("Could not apply", errorMessage(e)); }
                }}>Approve and create part</button>
              </div>
            </div>
          )}
        </section>
      )}
      <VerifyDialog req={verifyFor} onClose={() => setVerifyFor(null)} projectId={projectId} docs={docs} onDone={refresh} />
    </div>
  );
}

function AddRequirement({ projectId, problemId, onAdded }: { projectId: string; problemId: string; onAdded: () => void }) {
  const [r, setR] = useState({ kind: "target_metric", text: "", metric: "", comparator: "<=", target_value: "", unit: "" });
  return (
    <form className="row wrap" style={{ marginTop: 8 }} onSubmit={async (e) => {
      e.preventDefault();
      try {
        await post(apiPath("/api/v1/projects/{project_id}/requirements", { project_id: projectId }), {
          problem_id: problemId, kind: r.kind, text: r.text, metric: r.metric || null, comparator: r.target_value ? r.comparator : null,
          target_value: r.target_value ? Number(r.target_value) : null, unit: r.unit || null,
        });
        setR({ ...r, text: "", target_value: "" });
        onAdded();
      } catch (err) { toast.err("Could not add", errorMessage(err)); }
    }}>
      <select className="select" style={{ width: 140 }} aria-label="Kind" value={r.kind} onChange={(e) => setR({ ...r, kind: e.target.value })}>
        {["functional", "constraint", "target_metric", "assumption", "open_question"].map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
      </select>
      <input className="input grow" style={{ minWidth: 200, width: "auto" }} placeholder="Requirement text" aria-label="Requirement text" value={r.text} onChange={(e) => setR({ ...r, text: e.target.value })} />
      <input className="input" style={{ width: 90 }} placeholder="metric" aria-label="Metric" value={r.metric} onChange={(e) => setR({ ...r, metric: e.target.value })} />
      <select className="select" style={{ width: 60 }} aria-label="Comparator" value={r.comparator} onChange={(e) => setR({ ...r, comparator: e.target.value })}>
        {["<=", ">=", "=", "<", ">"].map((c) => <option key={c}>{c}</option>)}
      </select>
      <input className="input num" style={{ width: 80 }} placeholder="value" aria-label="Target value" value={r.target_value} onChange={(e) => setR({ ...r, target_value: e.target.value })} />
      <input className="input" style={{ width: 60 }} placeholder="unit" aria-label="Unit" value={r.unit} onChange={(e) => setR({ ...r, unit: e.target.value })} />
      <button className="btn sm" disabled={!r.text.trim()}><Plus size={13} />Add</button>
    </form>
  );
}

function VerifyDialog({ req, onClose, projectId, docs, onDone }: { req: Requirement | null; onClose: () => void; projectId: string; docs: ProjectDoc[]; onDone: () => void }) {
  const sims = useQuery({ queryKey: ["simulations", projectId], queryFn: () => get<{ items: SimSummary[] }>(apiPath("/api/v1/projects/{project_id}/simulations", { project_id: projectId })), enabled: !!req });
  const [evidence, setEvidence] = useState("");
  const [err, setErr] = useState<unknown>(null);
  return (
    <Dialog open={!!req} onClose={onClose} title="Verify requirement against evidence"
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!evidence} onClick={async () => {
          const [kind, id] = evidence.split(":");
          try {
            await post(apiPath("/api/v1/requirements/{req_id}/verify", { req_id: req!.id }), kind === "sim" ? { simulation_id: id } : { document_id: id });
            onDone(); onClose();
          } catch (e) { setErr(e); }
        }}>Check</button></>}>
      {req && <p style={{ margin: 0 }}><strong>{req.text}</strong> — {req.metric} {req.comparator} {req.target_value} {req.unit}</p>}
      <Field label="Evidence" htmlFor="ev" hint="Mass and size are checked against computed CAD geometry; stress, displacement and temperature against a finished simulation.">
        <select id="ev" className="select" value={evidence} onChange={(e) => setEvidence(e.target.value)}>
          <option value="">Choose…</option>
          <optgroup label="CAD documents (head revision)">{docs.filter((d) => d.kind === "part").map((d) => <option key={d.id} value={`doc:${d.id}`}>{d.name}</option>)}</optgroup>
          <optgroup label="Finished simulations">{sims.data?.items.filter((s) => s.status === "succeeded").map((s) => <option key={s.id} value={`sim:${s.id}`}>{s.name} ({s.analysis})</option>)}</optgroup>
        </select>
      </Field>
      {err ? <ErrorBox error={err} /> : null}
    </Dialog>
  );
}
