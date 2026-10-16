"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiPath, errorMessage, get, post } from "@/api/client";
import type { DocMeta, Revision } from "@/api/types";
import { Dialog, ErrorBox, Loading } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { AssemblyWorkbench } from "@/components/workbench/AssemblyWorkbench";
import { PartWorkbench } from "@/components/workbench/PartWorkbench";
import { clearDraft, readDraft, type LocalDraft } from "@/components/workbench/session";
import { useEditor, type EditorDoc } from "@/components/workbench/store";
import { RequireAuth } from "@/lib/auth";

interface DocResponse { document: DocMeta & { role: string | null }; project: { id: string; name: string }; revision: Revision }

function Workbench() {
  const { docId } = useParams<{ docId: string }>();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [recover, setRecover] = useState<{ draft: LocalDraft; head: number } | null>(null);
  const q = useQuery({
    queryKey: ["document", docId],
    queryFn: () => get<DocResponse>(apiPath("/api/v1/documents/{document_id}", { document_id: docId })),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: (n, e) => n < 2 && (e as { status?: number }).status !== 404,
  });

  useEffect(() => {
    if (!q.data) return;
    const { document: d, revision } = q.data;
    const readonly = !(d.role === "owner" || d.role === "editor");
    useEditor.getState().init(docId, revision.content as EditorDoc, revision.number, readonly);
    const draft = readDraft(docId);
    if (draft && !readonly && JSON.stringify(draft.content) !== JSON.stringify(revision.content)) setRecover({ draft, head: revision.number });
    else if (draft) clearDraft(docId);
    setReady(true);
    document.title = `${d.name} · AutoForge`;
  }, [q.data, docId]);

  if (q.isLoading) return <div style={{ height: "100vh", display: "grid", placeItems: "center" }}><Loading label="Opening document and running the kernel…" /></div>;
  if (q.error) return (
    <div className="page" style={{ maxWidth: 640 }}>
      <ErrorBox error={q.error} onRetry={() => q.refetch()} />
      <button className="btn" style={{ marginTop: 12 }} onClick={() => router.push("/app/projects")}>Back to projects</button>
    </div>
  );
  if (!q.data || !ready) return null;
  const { document: d, project } = q.data;
  const props = { docId, name: d.name, project, role: d.role, head: d.head_revision };
  return (
    <>
      {d.kind === "assembly" ? <AssemblyWorkbench {...props} /> : <PartWorkbench {...props} />}
      <Dialog open={!!recover} onClose={() => { if (recover) clearDraft(docId); setRecover(null); }} title="Recover unsaved changes?"
        footer={<>
          <button className="btn" onClick={() => { clearDraft(docId); setRecover(null); }}>Discard draft</button>
          {recover && recover.draft.base !== recover.head && (
            <button className="btn" onClick={async () => {
              try {
                const r = await post<{ document: { id: string } }>(apiPath("/api/v1/projects/{project_id}/documents", { project_id: project.id }),
                  { name: `${d.name} (recovered draft)`.slice(0, 160), kind: d.kind, content: recover.draft.content });
                clearDraft(docId); setRecover(null); router.push(`/app/workbench/${r.document.id}`);
              } catch (e) { toast.err("Could not save the draft as a copy", errorMessage(e)); }
            }}>Save draft as a new document</button>
          )}
          <button className="btn primary" onClick={() => {
            if (!recover) return;
            useEditor.setState({ doc: recover.draft.content, save: "dirty", opLabels: ["Recovered local draft"] });
            setRecover(null);
            toast.info("Draft restored", recover.draft.base !== recover.head ? "It will be saved on top of the latest revision; the other changes stay in history." : "Saving now.");
          }}>Restore draft</button>
        </>}>
        {recover && (
          <div className="col">
            <p style={{ margin: 0 }}>This browser holds changes to <strong>{d.name}</strong> from {new Date(recover.draft.at).toLocaleString()} that never reached the server.</p>
            {recover.draft.base !== recover.head
              ? <div className="alert warn small">The draft was based on revision {recover.draft.base}; the document is now at revision {recover.head}. Restoring it replaces the newer changes in the working copy (they stay in history), or save it as a separate document.</div>
              : <div className="alert info small">The draft is based on the current head revision {recover.head}.</div>}
          </div>
        )}
      </Dialog>
    </>
  );
}

export default function Page() { return <RequireAuth><Workbench /></RequireAuth>; }
