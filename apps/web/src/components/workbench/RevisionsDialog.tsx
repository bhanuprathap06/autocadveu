"use client";

import { useQuery } from "@tanstack/react-query";
import { Eye, GitCompare, History, RotateCcw } from "lucide-react";
import { useState } from "react";
import { apiPath, get } from "@/api/client";
import type { Revision } from "@/api/types";
import { Dialog, ErrorBox, Loading, StatusBadge } from "@/components/ui";

interface Diff {
  parameters: { added: string[]; removed: string[]; changed: { name: string; from: string; to: string }[] };
  features: { added: { id: string; type: string; name: string }[]; removed: { id: string; type: string; name: string }[];
    changed: { id: string; name: string; type: string; params: string[]; moved: boolean; suppression: boolean; renamed: boolean }[] };
  materials: string[];
  instances: { added: string[]; removed: string[]; changed: string[] } | null;
  mates: { added: string[]; removed: string[]; changed: string[] } | null;
}

export function RevisionsDialog({ open, onClose, docId, head, canEdit, onPreview, onRestore }: {
  open: boolean; onClose: () => void; docId: string; head: number; canEdit: boolean;
  onPreview: (n: number) => void; onRestore: (n: number) => void;
}) {
  const [cmp, setCmp] = useState<number | null>(null);
  const list = useQuery({
    queryKey: ["revisions", docId],
    queryFn: () => get<{ items: Revision[]; total: number; head: number }>(apiPath("/api/v1/documents/{document_id}/revisions", { document_id: docId }, { limit: 200 })),
    enabled: open,
  });
  const diff = useQuery({
    queryKey: ["diff", docId, cmp, head],
    queryFn: () => get<{ diff: Diff }>(apiPath("/api/v1/documents/{document_id}/diff", { document_id: docId }, { from: cmp!, to: head })),
    enabled: open && cmp !== null && cmp !== head,
  });
  return (
    <Dialog open={open} onClose={onClose} title="Revision history" wide>
      <p className="small muted" style={{ margin: 0 }}>
        Every save is an immutable revision with its content hash and kernel recompute status. Restoring creates a new revision; history is never rewritten.
      </p>
      {list.isLoading && <Loading />}
      {list.error && <ErrorBox error={list.error} />}
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <div className="table-wrap" style={{ maxHeight: "52vh" }}>
          <table className="table">
            <thead><tr><th>#</th><th>Message</th><th>Author</th><th>Model</th><th aria-label="Actions" /></tr></thead>
            <tbody>
              {list.data?.items.map((r) => (
                <tr key={r.number} style={{ background: cmp === r.number ? "var(--accent-soft)" : undefined }}>
                  <td className="num">{r.number}{r.number === head && <span className="badge accent" style={{ marginLeft: 4 }}>head</span>}</td>
                  <td>
                    <div className="ellipsis" style={{ maxWidth: 220 }} title={r.message}>{r.message || <span className="faint">(no message)</span>}</div>
                    <div className="faint tiny">{new Date(r.created_at).toLocaleString()} · {r.content_sha256.slice(0, 10)}{r.restored_from ? ` · restored from #${r.restored_from}` : ""}</div>
                  </td>
                  <td className="small">{r.author ?? "—"}</td>
                  <td>{r.ok === null ? <span className="faint">—</span> : <StatusBadge status={r.ok ? "ok" : "error"} label={r.ok ? "OK" : "Errors"} />}</td>
                  <td className="nowrap">
                    <button className="btn ghost icon sm" title="Compare with head" aria-label={`Compare revision ${r.number} with head`} disabled={r.number === head} onClick={() => setCmp(r.number)}><GitCompare size={13} /></button>
                    <button className="btn ghost icon sm" title="Open read-only" aria-label={`Preview revision ${r.number}`} onClick={() => { onPreview(r.number); onClose(); }}><Eye size={13} /></button>
                    <button className="btn ghost icon sm" title="Restore as new revision" aria-label={`Restore revision ${r.number}`} disabled={!canEdit || r.number === head}
                      onClick={() => { onRestore(r.number); onClose(); }}><RotateCcw size={13} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card tight" style={{ minHeight: 200 }}>
          <div className="row"><History size={14} /><strong className="small">{cmp ? `Changes from #${cmp} to head #${head}` : "Select a revision to compare"}</strong></div>
          {diff.isLoading && <Loading />}
          {diff.error && <ErrorBox error={diff.error} />}
          {diff.data && <DiffView d={diff.data.diff} />}
        </div>
      </div>
    </Dialog>
  );
}

function DiffView({ d }: { d: Diff }) {
  const empty = !d.parameters.added.length && !d.parameters.removed.length && !d.parameters.changed.length && !d.features.added.length
    && !d.features.removed.length && !d.features.changed.length && !d.materials.length && !(d.instances?.added.length || d.instances?.removed.length || d.instances?.changed.length)
    && !(d.mates?.added.length || d.mates?.removed.length || d.mates?.changed.length);
  if (empty) return <div className="faint small" style={{ marginTop: 8 }}>No content differences.</div>;
  return (
    <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
      {d.parameters.changed.map((p) => <li key={p.name}>Parameter <code>{p.name}</code>: <code>{p.from}</code> → <code>{p.to}</code></li>)}
      {d.parameters.added.map((p) => <li key={p} style={{ color: "var(--ok)" }}>Parameter added: <code>{p}</code></li>)}
      {d.parameters.removed.map((p) => <li key={p} style={{ color: "var(--err)" }}>Parameter removed: <code>{p}</code></li>)}
      {d.features.added.map((f) => <li key={f.id} style={{ color: "var(--ok)" }}>Added {f.type} <strong>{f.name || f.id}</strong></li>)}
      {d.features.removed.map((f) => <li key={f.id} style={{ color: "var(--err)" }}>Removed {f.type} <strong>{f.name || f.id}</strong></li>)}
      {d.features.changed.map((f) => (
        <li key={f.id}>Changed {f.type} <strong>{f.name || f.id}</strong>
          {f.params.length > 0 && <>: {f.params.join(", ")}</>}{f.moved && " · reordered"}{f.suppression && " · suppression"}{f.renamed && " · renamed"}</li>
      ))}
      {d.materials.length > 0 && <li>Materials changed on: {d.materials.join(", ")}</li>}
      {d.instances && (d.instances.added.length + d.instances.removed.length + d.instances.changed.length) > 0 && (
        <li>Instances: +{d.instances.added.length} −{d.instances.removed.length} ~{d.instances.changed.length}</li>)}
      {d.mates && (d.mates.added.length + d.mates.removed.length + d.mates.changed.length) > 0 && (
        <li>Mates: +{d.mates.added.length} −{d.mates.removed.length} ~{d.mates.changed.length}</li>)}
    </ul>
  );
}
