"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, FlaskConical, Plus, Trash2, Upload, UserPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { apiPath, del, download, errorMessage, get, post, raw } from "@/api/client";
import type { CatalogItem, DocMeta, SimSummary } from "@/api/types";
import { SimulationWizard } from "@/components/sim/SimulationWizard";
import { AssetBadge, Confirm, Dialog, Empty, ErrorBox, Field, Loading, StatusBadge } from "@/components/ui";
import { toast } from "@/components/ui/toast";

export function SimulationsTab({ projectId, docs, canRun }: { projectId: string; docs: DocMeta[]; canRun: boolean }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["simulations", projectId],
    queryFn: () => get<{ items: SimSummary[] }>(apiPath("/api/v1/projects/{project_id}/simulations", { project_id: projectId })),
    refetchInterval: (qq) => (qq.state.data?.items.some((s) => s.status === "queued" || s.status === "running") ? 4000 : false),
  });
  return (
    <div className="col gap-12">
      <div className="row">
        {canRun && <button className="btn primary" disabled={!docs.some((d) => d.kind === "part")} onClick={() => setOpen(true)}><FlaskConical size={14} />New simulation</button>}
        <span className="faint small">Linear static and steady-state thermal analyses run in CalculiX. CFD requires an external solver integration.</span>
      </div>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && !q.data.items.length && <Empty icon={<FlaskConical size={28} />} title="No simulations yet">Set up a study on a part: material, supports, loads and mesh.</Empty>}
      {q.data && q.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th>Analysis</th><th>Model</th><th>Status</th><th>Submitted</th><th>Duration</th></tr></thead>
            <tbody>
              {q.data.items.map((s) => (
                <tr key={s.id}>
                  <td><Link href={`/app/simulations/${s.id}`}><strong>{s.name}</strong></Link></td>
                  <td className="small">{s.analysis}</td>
                  <td className="small">{docs.find((d) => d.id === s.document_id)?.name ?? "—"} r{s.revision} · {s.body}</td>
                  <td><StatusBadge status={s.status} />{s.status === "running" && s.progress && <span className="faint tiny"> {s.progress.stage}</span>}</td>
                  <td className="small muted">{new Date(s.created_at).toLocaleString()}</td>
                  <td className="num small">{s.started_at && s.finished_at ? `${((new Date(s.finished_at).getTime() - new Date(s.started_at).getTime()) / 1000).toFixed(1)} s` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <SimulationWizard open={open} onClose={() => setOpen(false)} projectId={projectId} docs={docs} />}
    </div>
  );
}

interface DrawingRow { id: string; name: string; document_id: string; document_name: string; document_kind: string; revision_pin: number | null; version: number; updated_at: string; head_revision: number }

export function DrawingsTab({ projectId, docs, canEdit }: { projectId: string; docs: DocMeta[]; canEdit: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [docId, setDocId] = useState(docs[0]?.id ?? "");
  const [name, setName] = useState("");
  const [pin, setPin] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const q = useQuery({ queryKey: ["drawings", projectId], queryFn: () => get<{ items: DrawingRow[] }>(apiPath("/api/v1/projects/{project_id}/drawings", { project_id: projectId })) });
  return (
    <div className="col gap-12">
      {canEdit && <div><button className="btn primary" disabled={!docs.length} onClick={() => { setOpen(true); setName(`${docs.find((d) => d.id === docId)?.name ?? "Drawing"} – drawing`); }}><Plus size={14} />New drawing</button></div>}
      {q.isLoading && <Loading />}
      {q.data && !q.data.items.length && <Empty icon={<FileText size={28} />} title="No drawings">Create a drawing from a part or assembly; views, dimensions and the title block are generated from the model.</Empty>}
      {q.data && q.data.items.length > 0 && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Drawing</th><th>Model</th><th>Model revision</th><th>Version</th><th>Updated</th></tr></thead>
          <tbody>{q.data.items.map((d) => (
            <tr key={d.id}>
              <td><Link href={`/app/drawings/${d.id}`}><strong>{d.name}</strong></Link></td>
              <td className="small">{d.document_name} <span className="badge">{d.document_kind}</span></td>
              <td className="small">{d.revision_pin ? <>pinned r{d.revision_pin}{d.revision_pin !== d.head_revision && <span className="badge warn" style={{ marginLeft: 4 }}>model changed (r{d.head_revision})</span>}</> : "follows head"}</td>
              <td className="num">v{d.version}</td>
              <td className="small muted">{new Date(d.updated_at).toLocaleString()}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <Dialog open={open} onClose={() => setOpen(false)} title="New drawing"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={!docId || !name.trim()} onClick={async () => {
          try {
            const head = docs.find((d) => d.id === docId)?.head_revision;
            const r = await post<{ drawing: { id: string } }>(apiPath("/api/v1/projects/{project_id}/drawings", { project_id: projectId }), { document_id: docId, name: name.trim(), revision_pin: pin ? head : null });
            router.push(`/app/drawings/${r.drawing.id}`);
          } catch (e) { setErr(e); }
        }}>Create</button></>}>
        <Field label="Model" htmlFor="dm"><select id="dm" className="select" value={docId} onChange={(e) => setDocId(e.target.value)}>{docs.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.kind})</option>)}</select></Field>
        <Field label="Drawing name" htmlFor="dname"><input id="dname" className="input" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} /></Field>
        <label className="checkbox"><input type="checkbox" checked={pin} onChange={(e) => setPin(e.target.checked)} />Pin to the current model revision (released drawings should be pinned)</label>
        {err ? <ErrorBox error={err} /> : null}
      </Dialog>
    </div>
  );
}

export function ComponentsTab({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["project-components", projectId], queryFn: () => get<{ items: (CatalogItem & { note: string })[] }>(apiPath("/api/v1/projects/{project_id}/components", { project_id: projectId })) });
  return (
    <div className="col gap-12">
      <div className="row"><Link href="/app/catalog" className="btn"><Plus size={14} />Add from component library</Link>
        {q.data && q.data.items.length >= 2 && <Link className="btn" href={`/app/compare?ids=${q.data.items.slice(0, 4).map((i) => i.id).join(",")}`}>Compare</Link>}</div>
      {q.data && !q.data.items.length && <Empty title="No shortlisted components">Shortlist standard parts and manufacturer components for this project from the library.</Empty>}
      <div className="grid grid-3">
        {q.data?.items.map((i) => (
          <div key={i.id} className="card tight">
            <div className="row between"><Link href={`/app/catalog/${i.id}`}><strong>{i.name}</strong></Link>
              {canEdit && <button className="btn ghost icon sm" aria-label={`Remove ${i.name}`} onClick={async () => {
                try { await del(apiPath("/api/v1/projects/{project_id}/components/{item_id}", { project_id: projectId, item_id: i.id })); qc.invalidateQueries({ queryKey: ["project-components", projectId] }); }
                catch (e) { toast.err("Remove failed", errorMessage(e)); }
              }}><Trash2 size={12} /></button>}</div>
            <div className="row wrap tiny" style={{ marginTop: 4 }}><AssetBadge cls={i.asset_class} /><span className="faint">{i.standard ?? i.manufacturer}</span><span className="faint">completeness {Math.round(i.completeness * 100)}%</span></div>
            {i.note && <div className="small muted" style={{ marginTop: 4 }}>{i.note}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

interface FileRow { id: string; filename: string; content_type: string; size: number; sha256: string; kind: string; scan_status: string; diagnostics: Record<string, any> | null; created_at: string }

export function FilesTab({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [rm, setRm] = useState<FileRow | null>(null);
  const q = useQuery({ queryKey: ["files", projectId], queryFn: () => get<{ items: FileRow[] }>(apiPath("/api/v1/projects/{project_id}/files", { project_id: projectId })) });
  const upload = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    setBusy(true);
    try { await raw("POST", apiPath("/api/v1/projects/{project_id}/files", { project_id: projectId }), fd); qc.invalidateQueries({ queryKey: ["files", projectId] }); toast.ok(`Uploaded ${f.name}`); }
    catch (e) { toast.err("Upload rejected", errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="col gap-12">
      {canEdit && <div className="row"><button className="btn" disabled={busy} onClick={() => input.current?.click()}><Upload size={14} />Upload reference file</button>
        <span className="faint small">PDF, images, CSV, text, JSON, DXF and CAD/mesh files. Executable and active content is rejected.</span>
        <input ref={input} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} /></div>}
      {q.data && !q.data.items.length && <Empty title="No files">Reference documents, imported CAD and exports appear here with their checksums.</Empty>}
      {q.data && q.data.items.length > 0 && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>File</th><th>Kind</th><th className="num">Size</th><th>SHA-256</th><th>Scan</th><th>Added</th><th aria-label="Actions" /></tr></thead>
          <tbody>{q.data.items.map((f) => (
            <tr key={f.id}>
              <td>{f.filename}{f.kind === "export" && f.diagnostics && <div className="faint tiny">rev {f.diagnostics.revision} · {f.diagnostics.format}{f.diagnostics.verification?.reimport_valid !== undefined ? ` · re-import ${f.diagnostics.verification.reimport_valid ? "valid" : "invalid"}` : ""}</div>}</td>
              <td><span className="badge">{f.kind.replace(/_/g, " ")}</span></td>
              <td className="num small">{(f.size / 1024).toFixed(1)} KB</td>
              <td className="mono tiny" title={f.sha256}>{f.sha256.slice(0, 12)}…</td>
              <td><span className={`badge ${f.scan_status === "clean" ? "ok" : "warn"}`} title="Malware scanning is an external integration">{f.scan_status.replace(/_/g, " ")}</span></td>
              <td className="small muted">{new Date(f.created_at).toLocaleString()}</td>
              <td className="nowrap">
                <button className="btn ghost icon sm" aria-label={`Download ${f.filename}`} onClick={() => download("GET", apiPath("/api/v1/files/{file_id}/download", { file_id: f.id }), undefined, f.filename)}><Download size={13} /></button>
                {canEdit && <button className="btn ghost icon sm" aria-label={`Delete ${f.filename}`} onClick={() => setRm(f)}><Trash2 size={13} /></button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <Confirm open={!!rm} title="Delete file" danger confirmLabel="Delete" message={<>Delete <strong>{rm?.filename}</strong>?</>} onClose={() => setRm(null)}
        onConfirm={async () => { try { await del(apiPath("/api/v1/files/{file_id}", { file_id: rm!.id })); qc.invalidateQueries({ queryKey: ["files", projectId] }); } catch (e) { toast.err("Delete failed", errorMessage(e)); } }} />
    </div>
  );
}

interface Member { user_id: string; email: string; display_name: string; role: string }

export function MembersTab({ projectId, members, canManage }: { projectId: string; members: Member[]; canManage: boolean }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  return (
    <div className="col gap-12">
      <div className="alert info small">Project members belong to your organisation. To work with people outside it, share the project through a <Link href="/app/rooms">private collaboration room</Link> with an email-bound invitation.</div>
      {canManage && (
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          try { await post(apiPath("/api/v1/projects/{project_id}/members", { project_id: projectId }), { email, role }); setEmail(""); qc.invalidateQueries({ queryKey: ["project", projectId] }); toast.ok("Member added"); }
          catch (err) { toast.err("Could not add member", errorMessage(err)); }
        }}>
          <input className="input" type="email" placeholder="colleague@company.com" aria-label="Member email" value={email} onChange={(e) => setEmail(e.target.value)} style={{ maxWidth: 300 }} />
          <select className="select" style={{ width: 120 }} aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>
            {["viewer", "reviewer", "editor", "owner"].map((r) => <option key={r}>{r}</option>)}
          </select>
          <button className="btn" disabled={!email}><UserPlus size={14} />Add</button>
        </form>
      )}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Email</th><th>Role</th><th aria-label="Actions" /></tr></thead>
        <tbody>{members.map((m) => (
          <tr key={m.user_id}><td>{m.display_name}</td><td className="small">{m.email}</td><td><span className="badge">{m.role}</span></td>
            <td>{canManage && <button className="btn ghost icon sm" aria-label={`Remove ${m.display_name}`} onClick={async () => {
              try { await del(apiPath("/api/v1/projects/{project_id}/members/{user_id}", { project_id: projectId, user_id: m.user_id })); qc.invalidateQueries({ queryKey: ["project", projectId] }); }
              catch (e) { toast.err("Could not remove", errorMessage(e)); }
            }}><Trash2 size={12} /></button>}</td></tr>
        ))}</tbody>
      </table></div>
      <div className="faint small">Roles: owner (manage, delete), editor (model, simulate, export), reviewer (view, comment), viewer (view only).</div>
    </div>
  );
}

interface AuditEvent { id: string; at: string; actor: string | null; action: string; target_type: string | null; target_id: string | null; request_id: string | null; details: Record<string, unknown> | null }

export function ActivityTab({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: ["audit", projectId], queryFn: () => get<{ items: AuditEvent[] }>(apiPath("/api/v1/projects/{project_id}/audit", { project_id: projectId }, { limit: 200 })) });
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
        <tbody>{q.data?.items.map((e) => (
          <tr key={e.id}><td className="small nowrap">{new Date(e.at).toLocaleString()}</td><td className="small">{e.actor ?? "system"}</td><td className="mono small">{e.action}</td>
            <td className="small">{e.target_type}{e.target_id ? ` ${e.target_id.slice(0, 8)}…` : ""}</td>
            <td className="tiny faint" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>{e.details ? JSON.stringify(e.details).slice(0, 240) : ""}</td></tr>
        ))}</tbody>
      </table>
      {q.isLoading && <Loading />}
    </div>
  );
}

export function ReportTab({ projectId }: { projectId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (fmt: "md" | "pdf") => {
    setBusy(fmt);
    try { await download("GET", apiPath("/api/v1/projects/{project_id}/report", { project_id: projectId }, { format: fmt })); }
    catch (e) { toast.err("Report failed", errorMessage(e)); } finally { setBusy(null); }
  };
  return (
    <div className="card col gap-12" style={{ maxWidth: 720 }}>
      <h3>Design review report</h3>
      <p className="small muted" style={{ margin: 0 }}>
        Generated from live project data: documents with revision hashes and kernel status, mass properties, requirements with verification evidence,
        simulation results with their setups and warnings, drawings and GD&T validation, and catalog provenance. Assumptions and limitations are stated;
        the report is not a certification.
      </p>
      <div className="row">
        <button className="btn primary" disabled={!!busy} onClick={() => run("pdf")}>{busy === "pdf" ? <span className="spinner" /> : <FileText size={14} />}Download PDF</button>
        <button className="btn" disabled={!!busy} onClick={() => run("md")}>{busy === "md" ? <span className="spinner" /> : <Download size={14} />}Download Markdown</button>
      </div>
    </div>
  );
}
