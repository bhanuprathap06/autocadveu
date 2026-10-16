"use client";

import { ChevronDown, Download } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, download, errorMessage, get, post } from "@/api/client";
import type { EditorDoc } from "./store";
import type { Revision } from "@/api/types";
import { ContextMenu, Dialog, Kbd } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import type { Presence } from "@/lib/realtime";
import { clearDraft, saveNow } from "./session";
import { useEditor } from "./store";

const SAVE_LABEL: Record<string, [string, string]> = {
  saved: ["ok", "All changes saved"], dirty: ["warn", "Unsaved changes"], saving: ["busy", "Saving…"], offline: ["err", "Offline — will retry"],
  conflict: ["err", "Save conflict"], error: ["err", "Save failed"], readonly: ["", "Read-only"],
};

export function SaveIndicator() {
  const save = useEditor((s) => s.save);
  const err = useEditor((s) => s.saveError);
  const base = useEditor((s) => s.baseRevision);
  const [cls, label] = SAVE_LABEL[save] ?? ["", save];
  return (
    <span className="save-state" role="status" aria-live="polite" title={err ?? `Head revision ${base}`}>
      <span className={`dot ${cls}`} aria-hidden="true" />{label}<span className="faint num">r{base}</span>
      {(save === "error" || save === "offline") && <button className="btn ghost sm" onClick={() => { useEditor.getState().setSave("dirty"); void saveNow(); }}>Retry</button>}
    </span>
  );
}

export function PresenceAvatars({ presence, docId }: { presence: Presence[]; docId: string }) {
  const here = presence.filter((p) => p.document_id === docId);
  const elsewhere = presence.length - here.length;
  if (!presence.length) return null;
  const uniq = new Map(here.map((p) => [p.user_id, p]));
  return (
    <div className="row gap-4" aria-label="Collaborators online">
      {[...uniq.values()].slice(0, 5).map((p) => (
        <span key={p.user_id} title={`${p.name} (${p.role}) is viewing this document${p.selection.length ? ` · ${p.selection.length} selected` : ""}`}
          style={{ width: 24, height: 24, borderRadius: 12, background: colorFor(p.user_id), color: "#04131b", fontSize: 11, fontWeight: 700,
            display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
          {p.name.split(/\s+/).map((x) => x[0]).join("").slice(0, 2).toUpperCase()}
        </span>
      ))}
      {elsewhere > 0 && <span className="faint tiny" title="Collaborators in other documents of this project">+{elsewhere} in project</span>}
    </div>
  );
}

function colorFor(id: string): string {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 62%)`;
}

const FORMATS = [
  { f: "step", label: "STEP AP214 (.step)", hint: "Exact B-rep, names" },
  { f: "iges", label: "IGES (.igs)", hint: "Exact surfaces" },
  { f: "brep", label: "OCCT BREP (.brep)", hint: "Single body" },
  { f: "stl", label: "STL (.stl)", hint: "Mesh" },
  { f: "obj", label: "OBJ (.obj)", hint: "Mesh" },
  { f: "glb", label: "glTF binary (.glb)", hint: "Mesh for viewers" },
  { f: "native", label: "AutoForge native (.afdoc.json)", hint: "Full parametric history" },
] as const;

export function ExportMenu({ docId, disabled, preview }: { docId: string; disabled?: boolean; preview?: number | null }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const run = async (fmt: string) => {
    const st = useEditor.getState();
    if (!preview && (st.save === "dirty" || st.save === "saving")) {
      await saveNow();
      if (useEditor.getState().save !== "saved") { toast.err("Export cancelled", "Save the document first; exports are produced from saved revisions."); return; }
    }
    try {
      const r = await download("POST", apiPath("/api/v1/documents/{document_id}/export", { document_id: docId }), { format: fmt, revision: preview ?? null });
      let v: Record<string, any> = {};
      try { v = JSON.parse(r.verification ?? "{}"); } catch { /* header truncated */ }
      const parts = [`SHA-256 ${r.sha256?.slice(0, 12)}…`];
      if (v.reimport_valid !== undefined) parts.push(`re-import ${v.reimport_valid ? "valid" : "INVALID"}`);
      if (typeof v.volume_rel_error === "number") parts.push(`volume error ${(v.volume_rel_error * 100).toExponential(1)}%`);
      if (v.triangles) parts.push(`${v.triangles} triangles`);
      if (v.failed_features?.length) parts.push(`${v.failed_features.length} failed feature(s) excluded`);
      (v.failed_features?.length ? toast.warn : toast.ok)(`Exported ${r.name}`, parts.join(" · "));
    } catch (e) {
      toast.err("Export failed", errorMessage(e));
    }
  };
  return (
    <>
      <button className="btn sm" disabled={disabled} aria-haspopup="menu" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setAt({ x: r.left, y: r.bottom + 4 }); }}>
        <Download size={14} />Export<ChevronDown size={12} />
      </button>
      <ContextMenu at={at} onClose={() => setAt(null)} items={FORMATS.map((x) => ({ label: x.label, hint: x.hint, onSelect: () => void run(x.f) }))} />
    </>
  );
}

/** Resolution choices for an optimistic-concurrency conflict. */
export function ConflictDialog({ docId, projectId, name, kind, onReloaded }: {
  docId: string; projectId: string; name: string; kind: "part" | "assembly"; onReloaded: () => void;
}) {
  const save = useEditor((s) => s.save);
  const err = useEditor((s) => s.saveError);
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const head = async () => get<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}", { document_id: docId }));
  return (
    <Dialog open={save === "conflict"} title="Someone else saved this document" onClose={() => { /* must choose */ }}
      footer={<span className="faint small">Your changes are kept locally until you choose.</span>}>
      <p style={{ margin: 0 }}>{err}</p>
      <p className="small muted" style={{ margin: 0 }}>AutoForge never merges CAD history silently. Choose how to continue:</p>
      <div className="col">
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            const r = await head();
            useEditor.getState().init(docId, r.revision.content as EditorDoc, r.revision.number);
            clearDraft(docId);
            onReloaded();
            toast.info("Loaded the latest revision", "Your unsaved changes were discarded.");
          } catch (e) { toast.err("Reload failed", errorMessage(e)); } finally { setBusy(false); }
        }}>Discard my changes and load the latest revision</button>
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            const st = useEditor.getState();
            const r = await post<{ document: { id: string } }>(apiPath("/api/v1/projects/{project_id}/documents", { project_id: projectId }),
              { name: `${name} (my version)`.slice(0, 160), kind, content: st.doc });
            clearDraft(docId);
            useEditor.getState().setSave("saved");
            router.push(`/app/workbench/${r.document.id}`);
            toast.ok("Saved your version as a new document");
          } catch (e) { toast.err("Could not save a copy", errorMessage(e)); } finally { setBusy(false); }
        }}>Save my version as a new document</button>
        <button className="btn danger" disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            const r = await head();
            useEditor.setState({ baseRevision: r.revision.number, save: "dirty", saveError: null });
            await saveNow("Saved over concurrent revision " + r.revision.number);
          } catch (e) { toast.err("Save failed", errorMessage(e)); } finally { setBusy(false); }
        }}>Save my version on top (their revision stays in history)</button>
      </div>
    </Dialog>
  );
}

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const rows: [string, string][] = [
    ["Ctrl+K", "Command palette"], ["Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"], ["Ctrl+S", "Save a named version"], ["F", "Fit view"],
    ["1 / 3 / 7 / 0", "Front / right / top / isometric view"], ["Esc", "Clear selection or cancel"], ["S", "New sketch"], ["E", "Extrude"],
    ["Shift/Ctrl+click", "Add to selection"], ["Right-click", "Context menu"], ["Sketch: L R C A P S", "Line, rectangle, circle, arc, point, spline"],
    ["Sketch: H / V / D", "Horizontal, vertical, smart dimension"], ["Sketch: X", "Construction toggle"], ["Sketch: Del", "Delete selection"],
  ];
  return (
    <Dialog open={open} onClose={onClose} title="Keyboard shortcuts">
      <table className="table"><tbody>{rows.map(([k, v]) => <tr key={k}><td><Kbd>{k}</Kbd></td><td>{v}</td></tr>)}</tbody></table>
    </Dialog>
  );
}
