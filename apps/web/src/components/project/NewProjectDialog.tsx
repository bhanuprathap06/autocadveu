"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, get, post } from "@/api/client";
import type { Project, Template } from "@/api/types";
import { Dialog, ErrorBox, Field } from "@/components/ui";

export function NewProjectDialog({ open, onClose, initialTemplate }: { open: boolean; onClose: () => void; initialTemplate?: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [domain, setDomain] = useState("");
  const [template, setTemplate] = useState(initialTemplate ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const domains = useQuery({ queryKey: ["domains"], queryFn: () => get<{ items: { key: string; name: string }[] }>(apiPath("/api/v1/domains")), enabled: open, staleTime: Infinity });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => get<{ items: Template[] }>(apiPath("/api/v1/templates")), enabled: open, staleTime: Infinity });
  const t = templates.data?.items.find((x) => x.key === template);
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const r = await post<{ project: Project; document: { id: string } | null }>(apiPath("/api/v1/projects"), {
        name: name.trim(), description, domain: domain || t?.domain || null, template: template || null,
      });
      onClose();
      router.push(r.document ? `/app/workbench/${r.document.id}` : `/app/projects/${r.project.id}`);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onClose={onClose} title="New project" wide
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!name.trim() || busy} onClick={submit}>{busy && <span className="spinner" />}Create project</button></>}>
      <div className="grid grid-2">
        <Field label="Project name" htmlFor="pn"><input id="pn" className="input" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
        <Field label="Engineering domain" htmlFor="pd">
          <select id="pd" className="select" value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">{t ? `From template (${t.domain})` : "Not specified"}</option>
            {domains.data?.items.map((d) => <option key={d.key} value={d.key}>{d.name}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Description" htmlFor="pdesc"><textarea id="pdesc" className="textarea" rows={2} value={description} maxLength={5000} onChange={(e) => setDescription(e.target.value)} /></Field>
      <div className="field">
        <span className="label">Start from</span>
        <div className="grid grid-3">
          <button type="button" className={`card tight hover${template === "" ? "" : ""}`} aria-pressed={template === ""} onClick={() => setTemplate("")}
            style={{ textAlign: "left", cursor: "pointer", borderColor: template === "" ? "var(--accent)" : undefined }}>
            <strong>Empty project</strong><div className="small muted">Create parts and assemblies yourself.</div>
          </button>
          {templates.data?.items.map((x) => (
            <button type="button" key={x.key} className="card tight hover" aria-pressed={template === x.key} onClick={() => setTemplate(x.key)}
              style={{ textAlign: "left", cursor: "pointer", borderColor: template === x.key ? "var(--accent)" : undefined }}>
              <strong>{x.name}</strong>
              <div className="small muted">{x.description}</div>
              <div className="tiny faint" style={{ marginTop: 4 }}>{x.domain.replace(/_/g, " ")} · parametric</div>
            </button>
          ))}
        </div>
      </div>
      {t && <div className="alert info small">{t.caveat}</div>}
      {error ? <ErrorBox error={error} /> : null}
    </Dialog>
  );
}
