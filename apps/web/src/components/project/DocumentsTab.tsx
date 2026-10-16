"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Boxes, FileInput, FilePlus2, PencilRuler, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useRef, useState } from "react";
import { apiPath, del, errorMessage, post, raw } from "@/api/client";
import type { Template } from "@/api/types";
import { Confirm, Dialog, Empty, ErrorBox, Field } from "@/components/ui";
import { toast } from "@/components/ui/toast";

export interface ProjectDoc { id: string; name: string; kind: "part" | "assembly"; head_revision: number; updated_at: string }

export function DocumentsTab({ projectId, docs, canEdit, templates }: { projectId: string; docs: ProjectDoc[]; canEdit: boolean; templates: Template[] }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [create, setCreate] = useState<"part" | "assembly" | null>(null);
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [del_, setDel] = useState<ProjectDoc | null>(null);
  const [importDiag, setImportDiag] = useState<Record<string, unknown> | null>(null);
  const cadInput = useRef<HTMLInputElement>(null);
  const nativeInput = useRef<HTMLInputElement>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["project", projectId] });

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await post<{ document: { id: string } }>(apiPath("/api/v1/projects/{project_id}/documents", { project_id: projectId }),
        { name: name.trim(), kind: create, template: create === "part" && template ? template : null });
      setCreate(null); setName(""); setTemplate(""); refresh();
      router.push(`/app/workbench/${r.document.id}`);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  const importCad = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    setBusy(true);
    try {
      const res = await raw("POST", apiPath("/api/v1/projects/{project_id}/imports", { project_id: projectId }), fd);
      const r = (await res.json()) as { document: { id: string; name: string }; diagnostics: Record<string, unknown> };
      setImportDiag({ ...r.diagnostics, document: r.document });
      refresh();
    } catch (e) { toast.err("Import failed", errorMessage(e)); } finally { setBusy(false); }
  };

  const importNative = async (f: File) => {
    setBusy(true);
    try {
      const body = JSON.parse(await f.text());
      const r = await post<{ document: { id: string } }>(apiPath("/api/v1/projects/{project_id}/documents/import-native", { project_id: projectId }), body);
      refresh();
      toast.ok("Native document imported", "Checksum verified; full parametric history restored.");
      router.push(`/app/workbench/${r.document.id}`);
    } catch (e) { toast.err("Native import failed", e instanceof SyntaxError ? "The file is not valid JSON." : errorMessage(e)); } finally { setBusy(false); }
  };

  return (
    <div className="col gap-12">
      {canEdit && (
        <div className="row wrap">
          <button className="btn primary" onClick={() => { setCreate("part"); setName("Part 1"); }}><PencilRuler size={14} />New part</button>
          <button className="btn" onClick={() => { setCreate("assembly"); setName("Assembly 1"); }}><Boxes size={14} />New assembly</button>
          <button className="btn" disabled={busy} onClick={() => cadInput.current?.click()}><Upload size={14} />Import STEP / IGES / BREP</button>
          <button className="btn" disabled={busy} onClick={() => nativeInput.current?.click()}><FileInput size={14} />Import AutoForge file</button>
          {busy && <span className="spinner" aria-label="Working" />}
          <input ref={cadInput} type="file" hidden accept=".step,.stp,.iges,.igs,.brep" onChange={(e) => { const f = e.target.files?.[0]; if (f) void importCad(f); e.target.value = ""; }} />
          <input ref={nativeInput} type="file" hidden accept=".json,.afdoc.json" onChange={(e) => { const f = e.target.files?.[0]; if (f) void importNative(f); e.target.value = ""; }} />
        </div>
      )}
      {!docs.length ? (
        <Empty icon={<FilePlus2 size={28} />} title="No documents">Create a part from scratch or a template, an assembly, or import a STEP file.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th>Type</th><th className="num">Revision</th><th>Updated</th><th aria-label="Actions" /></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td><Link href={`/app/workbench/${d.id}`}><strong>{d.name}</strong></Link></td>
                  <td><span className={`badge ${d.kind === "assembly" ? "violet" : ""}`}>{d.kind}</span></td>
                  <td className="num">r{d.head_revision}</td>
                  <td className="small muted">{new Date(d.updated_at).toLocaleString()}</td>
                  <td className="nowrap">
                    <Link className="btn sm" href={`/app/workbench/${d.id}`}>Open</Link>
                    {canEdit && <button className="btn ghost icon sm" aria-label={`Delete ${d.name}`} onClick={() => setDel(d)}><Trash2 size={13} /></button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Dialog open={!!create} onClose={() => setCreate(null)} title={create === "assembly" ? "New assembly" : "New part"}
        footer={<><button className="btn" onClick={() => setCreate(null)}>Cancel</button><button className="btn primary" disabled={!name.trim() || busy} onClick={submit}>Create</button></>}>
        <Field label="Name" htmlFor="dn"><input id="dn" className="input" autoFocus value={name} maxLength={160} onChange={(e) => setName(e.target.value)} /></Field>
        {create === "part" && (
          <Field label="Start from" htmlFor="dt" hint={templates.find((t) => t.key === template)?.caveat}>
            <select id="dt" className="select" value={template} onChange={(e) => setTemplate(e.target.value)}>
              <option value="">Empty part</option>
              {templates.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
          </Field>
        )}
        {err ? <ErrorBox error={err} /> : null}
      </Dialog>
      <Dialog open={!!importDiag} onClose={() => setImportDiag(null)} title="Import diagnostics"
        footer={<><button className="btn" onClick={() => setImportDiag(null)}>Close</button>
          <button className="btn primary" onClick={() => { const id = (importDiag?.document as { id: string }).id; setImportDiag(null); router.push(`/app/workbench/${id}`); }}>Open document</button></>}>
        <p className="small muted" style={{ margin: 0 }}>The file was parsed and healed in an isolated kernel worker. Imported solids have no feature history; add features on top of them.</p>
        <dl className="kv">
          {Object.entries(importDiag ?? {}).filter(([k]) => k !== "document").map(([k, v]) => (
            <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>
          ))}
        </dl>
      </Dialog>
      <Confirm open={!!del_} title="Delete document" danger confirmLabel="Delete"
        message={<>Delete <strong>{del_?.name}</strong>? It is soft-deleted; its revisions stay in the audit history.</>}
        onClose={() => setDel(null)}
        onConfirm={async () => {
          if (!del_) return;
          try { await del(apiPath("/api/v1/documents/{document_id}", { document_id: del_.id })); refresh(); toast.ok("Document deleted"); }
          catch (e) { toast.err("Delete failed", errorMessage(e)); }
        }} />
    </div>
  );
}
