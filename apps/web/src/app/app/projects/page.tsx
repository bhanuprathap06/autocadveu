"use client";

import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Plus, RotateCcw, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useDeferredValue, useState } from "react";
import { apiPath, errorMessage, get, post } from "@/api/client";
import { toast } from "@/components/ui/toast";
import type { Project } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { NewProjectDialog } from "@/components/project/NewProjectDialog";
import { Empty, ErrorBox, Loading } from "@/components/ui";

function Projects() {
  const [q, setQ] = useState("");
  const dq = useDeferredValue(q);
  const [open, setOpen] = useState(false);
  const sp = useSearchParams();
  const router = useRouter();
  const deleted = sp.get("deleted");
  const list = useQuery({
    queryKey: ["projects", dq],
    queryFn: () => get<{ items: Project[]; total: number }>(apiPath("/api/v1/projects", {}, { q: dq || undefined, limit: 200 })),
  });
  return (
    <div className="page col gap-16">
      <div className="row between wrap">
        <h1>Projects</h1>
        <div className="row">
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: 8, color: "var(--text-faint)" }} />
            <input className="input" style={{ paddingLeft: 28, width: 260 }} placeholder="Search projects" aria-label="Search projects" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <button className="btn primary" onClick={() => setOpen(true)}><Plus size={15} />New project</button>
        </div>
      </div>
      {deleted && (
        <div className="alert info" role="status">
          <div className="grow">Project <strong>{sp.get("name") ?? ""}</strong> was moved to trash.</div>
          <button className="btn sm" onClick={async () => {
            try { await post(apiPath("/api/v1/projects/{project_id}/restore", { project_id: deleted })); toast.ok("Project restored"); router.push(`/app/projects/${deleted}`); }
            catch (e) { toast.err("Restore failed", errorMessage(e)); }
          }}><RotateCcw size={13} />Undo delete</button>
        </div>
      )}
      {list.isLoading && <Loading />}
      {list.error && <ErrorBox error={list.error} onRetry={() => list.refetch()} />}
      {list.data && !list.data.items.length && (
        <Empty icon={<FolderKanban size={28} />} title={dq ? "No matching projects" : "No projects yet"}
          action={!dq && <button className="btn primary sm" onClick={() => setOpen(true)}>New project</button>}>
          {dq ? "Try a different search." : "Create a project to start modelling."}
        </Empty>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th>Domain</th><th className="num">Documents</th><th>Your role</th><th>Updated</th></tr></thead>
            <tbody>
              {list.data.items.map((p) => (
                <tr key={p.id}>
                  <td><Link href={`/app/projects/${p.id}`}><strong>{p.name}</strong></Link><div className="faint tiny ellipsis" style={{ maxWidth: 420 }}>{p.description}</div></td>
                  <td className="small">{(p.domain ?? "—").replace(/_/g, " ")}</td>
                  <td className="num">{p.document_count ?? 0}</td>
                  <td><span className="badge">{p.role}</span></td>
                  <td className="small muted">{new Date(p.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="faint small">{list.data?.total ?? 0} project(s) you can access directly, through your organisation, or through collaboration rooms.</div>
      <NewProjectDialog open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Projects" }]}><Suspense><Projects /></Suspense></AppShell>; }
