"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { apiPath, del, errorMessage, get, patch } from "@/api/client";
import type { DocMeta, Project, Template } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { DocumentsTab } from "@/components/project/DocumentsTab";
import { ActivityTab, ComponentsTab, DrawingsTab, FilesTab, MembersTab, ReportTab, SimulationsTab } from "@/components/project/ProjectTabs";
import { RequirementsTab } from "@/components/project/RequirementsTab";
import { Confirm, ErrorBox, Loading, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { useProjectChannel } from "@/lib/realtime";

type Tab = "documents" | "requirements" | "simulations" | "drawings" | "components" | "files" | "members" | "activity" | "report";
interface ProjectResp { project: Project; documents: DocMeta[]; members: { user_id: string; email: string; display_name: string; role: string }[] }

function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>((sp.get("tab") as Tab) || "documents");
  const [confirmDel, setConfirmDel] = useState(false);
  const q = useQuery({ queryKey: ["project", projectId], queryFn: () => get<ProjectResp>(apiPath("/api/v1/projects/{project_id}", { project_id: projectId })) });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => get<{ items: Template[] }>(apiPath("/api/v1/templates")), staleTime: Infinity });
  const channel = useProjectChannel(projectId);
  if (q.isLoading) return <Loading />;
  if (q.error) return <div className="page"><ErrorBox error={q.error} onRetry={() => q.refetch()} /></div>;
  const { project: p, documents, members } = q.data!;
  const role = p.role;
  const canEdit = role === "owner" || role === "editor";
  const isOwner = role === "owner";
  const change = (k: Tab) => { setTab(k); router.replace(`/app/projects/${projectId}?tab=${k}`, { scroll: false }); };
  return (
    <div className="page col gap-16" style={{ maxWidth: 1400 }}>
      <div className="row between wrap">
        <div className="grow">
          <input className="input" style={{ fontSize: 22, fontWeight: 600, height: 38, background: "transparent", borderColor: "transparent", padding: 0 }} defaultValue={p.name} key={p.name}
            aria-label="Project name" disabled={!canEdit} maxLength={160}
            onBlur={async (e) => { const n = e.target.value.trim(); if (n && n !== p.name) { try { await patch(apiPath("/api/v1/projects/{project_id}", { project_id: projectId }), { name: n }); q.refetch(); } catch (err) { toast.err("Rename failed", errorMessage(err)); } } }} />
          <div className="small muted">{p.description || "No description"} · {(p.domain ?? "no domain").replace(/_/g, " ")} · your role: <strong>{role}</strong></div>
        </div>
        <div className="row">
          <span className="small muted" title={channel.connected ? "Live updates connected" : "Live updates offline"}><span className={`dot ${channel.connected ? "ok" : ""}`} /> {channel.presence.length} other{channel.presence.length === 1 ? "" : "s"} online</span>
          {isOwner && <button className="btn danger sm" onClick={() => setConfirmDel(true)}><Trash2 size={13} />Delete project</button>}
        </div>
      </div>
      <Tabs<Tab> label="Project sections" value={tab} onChange={change} tabs={[
        { key: "documents", label: "Documents", badge: <span className="badge">{documents.length}</span> },
        { key: "requirements", label: "Requirements & AI" }, { key: "simulations", label: "Simulations" }, { key: "drawings", label: "Drawings" },
        { key: "components", label: "Components" }, { key: "files", label: "Files & exports" }, { key: "members", label: "Members" },
        ...(isOwner ? [{ key: "activity" as Tab, label: "Activity" }] : []), { key: "report", label: "Report" },
      ]} />
      {tab === "documents" && <DocumentsTab projectId={projectId} docs={documents} canEdit={canEdit} templates={templates.data?.items ?? []} />}
      {tab === "requirements" && <RequirementsTab projectId={projectId} canEdit={canEdit} templates={templates.data?.items ?? []} docs={documents} />}
      {tab === "simulations" && <SimulationsTab projectId={projectId} docs={documents} canRun={canEdit} />}
      {tab === "drawings" && <DrawingsTab projectId={projectId} docs={documents} canEdit={canEdit} />}
      {tab === "components" && <ComponentsTab projectId={projectId} canEdit={canEdit} />}
      {tab === "files" && <FilesTab projectId={projectId} canEdit={canEdit} />}
      {tab === "members" && <MembersTab projectId={projectId} members={members} canManage={isOwner} />}
      {tab === "activity" && isOwner && <ActivityTab projectId={projectId} />}
      {tab === "report" && <ReportTab projectId={projectId} />}
      <Confirm open={confirmDel} title="Delete project" danger confirmLabel="Delete project"
        message={<>Delete <strong>{p.name}</strong>? It is moved to trash and can be restored by an owner until the retention period ends.</>}
        onClose={() => setConfirmDel(false)}
        onConfirm={async () => {
          try {
            await del(apiPath("/api/v1/projects/{project_id}", { project_id: projectId }));
            qc.invalidateQueries({ queryKey: ["projects"] });
            router.push(`/app/projects?deleted=${projectId}&name=${encodeURIComponent(p.name)}`);
          } catch (e) { toast.err("Delete failed", errorMessage(e)); }
        }} />
    </div>
  );
}

export default function Page() {
  return <AppShell crumbs={[{ href: "/app/projects", label: "Projects" }, { label: "Project" }]}><Suspense><ProjectPage /></Suspense></AppShell>;
}
