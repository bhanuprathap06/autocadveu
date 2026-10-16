"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, Boxes, Calculator, FolderKanban, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { apiPath, get } from "@/api/client";
import type { CatalogItem, Project, Template } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { NewProjectDialog } from "@/components/project/NewProjectDialog";
import { AssetBadge, Empty, ErrorBox, Loading } from "@/components/ui";
import { useMe } from "@/lib/auth";

interface SysStatus { kernel: string; solvers: { calculix: string | null }; ai: { provider: string; configured: boolean } }

function Dashboard() {
  const me = useMe();
  const [newOpen, setNewOpen] = useState<string | null>(null);
  const projects = useQuery({ queryKey: ["projects", ""], queryFn: () => get<{ items: Project[]; total: number }>(apiPath("/api/v1/projects", {}, { limit: 8 })) });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => get<{ items: Template[] }>(apiPath("/api/v1/templates")), staleTime: Infinity });
  const favs = useQuery({ queryKey: ["favorites"], queryFn: () => get<{ items: CatalogItem[] }>(apiPath("/api/v1/catalog/favorites")) });
  const sys = useQuery({ queryKey: ["system-status"], queryFn: () => get<SysStatus>(apiPath("/api/v1/status")) });
  return (
    <div className="page col gap-24">
      <div className="row between wrap">
        <div>
          <h1>Welcome{me.data ? `, ${me.data.user.display_name}` : ""}</h1>
          <p className="muted" style={{ margin: "4px 0 0" }}>Continue a project or start from a parametric template.</p>
        </div>
        <button className="btn primary" onClick={() => setNewOpen("")}><Plus size={15} />New project</button>
      </div>
      {me.data && !me.data.user.email_verified && (
        <div className="alert warn">Your email address is not verified. Verification is required to share projects in production deployments.
          <Link href="/verify-email" style={{ marginLeft: 6 }}>Verify now</Link></div>
      )}
      <section>
        <div className="row between" style={{ marginBottom: 10 }}><h2>Recent projects</h2><Link href="/app/projects" className="small">All projects →</Link></div>
        {projects.isLoading && <Loading />}
        {projects.error && <ErrorBox error={projects.error} onRetry={() => projects.refetch()} />}
        {projects.data && !projects.data.items.length && (
          <Empty icon={<FolderKanban size={28} />} title="No projects yet" action={<button className="btn primary sm" onClick={() => setNewOpen("")}>Create your first project</button>}>
            Projects hold part and assembly documents, simulations, drawings, requirements and files.
          </Empty>
        )}
        <div className="grid grid-4">
          {projects.data?.items.map((p) => (
            <Link key={p.id} href={`/app/projects/${p.id}`} className="card hover" style={{ color: "var(--text)", textDecoration: "none" }}>
              <strong className="ellipsis" style={{ display: "block" }}>{p.name}</strong>
              <div className="small muted ellipsis">{p.description || (p.domain ?? "").replace(/_/g, " ") || "No description"}</div>
              <div className="row tiny faint" style={{ marginTop: 8 }}>
                <span>{p.document_count ?? 0} documents</span><span>·</span><span>{p.role}</span><span>·</span><span>{new Date(p.updated_at).toLocaleDateString()}</span>
              </div>
            </Link>
          ))}
        </div>
      </section>
      <section>
        <h2 style={{ marginBottom: 10 }}>Start from a template</h2>
        <div className="grid grid-4">
          {templates.data?.items.map((t) => (
            <button key={t.key} className="card hover" style={{ textAlign: "left", cursor: "pointer", color: "var(--text)" }} onClick={() => setNewOpen(t.key)}>
              <strong>{t.name}</strong>
              <div className="small muted">{t.description}</div>
              <div className="tiny faint" style={{ marginTop: 6 }}>{t.domain.replace(/_/g, " ")}</div>
            </button>
          ))}
        </div>
      </section>
      <div className="grid grid-3">
        <section className="card">
          <div className="card-title"><h3>Favourite components</h3><Link href="/app/catalog" className="small">Library →</Link></div>
          {favs.data?.items.length ? favs.data.items.slice(0, 6).map((i) => (
            <Link key={i.id} href={`/app/catalog/${i.id}`} className="row small" style={{ padding: "4px 0" }}><Boxes size={13} /><span className="grow ellipsis">{i.name}</span><AssetBadge cls={i.asset_class} /></Link>
          )) : <div className="faint small">Star components in the library to keep them here.</div>}
        </section>
        <section className="card">
          <div className="card-title"><h3>Tools</h3></div>
          <div className="col">
            <Link href="/app/calculators" className="row"><Calculator size={14} />Engineering calculators</Link>
            <Link href="/app/compare" className="row"><Boxes size={14} />Compare components</Link>
            <Link href="/app/rooms" className="row"><Users size={14} />Collaboration rooms</Link>
            <Link href="/status" className="row"><Activity size={14} />Feature status</Link>
          </div>
        </section>
        <section className="card">
          <div className="card-title"><h3>Engines</h3></div>
          {sys.data ? (
            <dl className="kv">
              <dt>CAD kernel</dt><dd>{sys.data.kernel}</dd>
              <dt>FEA solver</dt><dd>{sys.data.solvers.calculix ?? <span style={{ color: "var(--warn)" }}>CalculiX not installed</span>}</dd>
              <dt>AI assistant</dt><dd>{sys.data.ai.configured ? `${sys.data.ai.provider} (opt-in per request)` : "rules engine (local)"}</dd>
            </dl>
          ) : <Loading />}
        </section>
      </div>
      <NewProjectDialog open={newOpen !== null} initialTemplate={newOpen ?? undefined} key={newOpen ?? "closed"} onClose={() => setNewOpen(null)} />
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ label: "Dashboard" }]}><Dashboard /></AppShell>; }
