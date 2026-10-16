"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Anchor, ArrowLeft, Boxes, Command as CmdIcon, Eye, EyeOff, History, Link2, Maximize, Pause, Play, Plus, Redo2, Save, Search, ShieldAlert,
  Trash2, Undo2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { apiPath, errorMessage, get, patch, post } from "@/api/client";
import type { AssemblyDocument, AssemblyInstance, AssemblyModel, CatalogItem, Mate, MateRef, Revision } from "@/api/types";
import { AssetBadge, CommandPalette, ContextMenu, Dialog, Empty, ErrorBox, Loading, Splitter, StatusBadge, Tabs, useSplit, type Command } from "@/components/ui";
import { LineChart } from "@/components/ui/chart";
import { toast } from "@/components/ui/toast";
import { Viewport, edgeKey, faceKey, type DisplayMode, type PickMode, type ViewBody, type ViewportHandle } from "@/components/viewer/Viewport";
import { fmt, uid } from "@/lib/mesh";
import { useProjectChannel } from "@/lib/realtime";
import { ConflictDialog, ExportMenu, PresenceAvatars, SaveIndicator } from "./chrome";
import { RevisionsDialog } from "./RevisionsDialog";
import { saveNow, useAutosave, useLiveModel } from "./session";
import { parseSel, useEditor } from "./store";
import type { WorkbenchProps } from "./PartWorkbench";

const MATE_TYPES: { type: Mate["type"]; label: string; needs: number; value?: "mm" | "deg"; help: string }[] = [
  { type: "fixed", label: "Fixed", needs: 1, help: "Locks the instance in place (or rigidly to a second instance)." },
  { type: "coincident", label: "Coincident", needs: 2, help: "Planar faces touch (opposed normals; Flip to align)." },
  { type: "concentric", label: "Concentric", needs: 2, help: "Cylindrical/conical axes or circular edges share an axis." },
  { type: "parallel", label: "Parallel", needs: 2, help: "Plane normals or axes are parallel." },
  { type: "perpendicular", label: "Perpendicular", needs: 2, help: "Plane normals or axes are perpendicular." },
  { type: "distance", label: "Distance", needs: 2, value: "mm", help: "Planar faces are offset by a distance." },
  { type: "angle", label: "Angle", needs: 2, value: "deg", help: "Angle between plane normals or axes." },
  { type: "revolute", label: "Revolute (hinge)", needs: 2, value: "mm", help: "Shared axis, free rotation; value is the axial offset. Optional limits in degrees." },
  { type: "slider", label: "Slider", needs: 2, help: "Shared axis direction, free translation along it. Optional limits in mm." },
];

const bodyKeyOf = (inst: string, body: string) => `${inst}::${body}`;
const splitBodyKey = (k: string) => { const i = k.indexOf("::"); return { inst: k.slice(0, i), body: k.slice(i + 2) }; };

function refFromSel(key: string, model: AssemblyModel | null): MateRef | null {
  const s = parseSel(key);
  const { inst, body } = splitBodyKey(s.body);
  const base: MateRef = { instance: inst, kind: "face", face: null, edge: null, body, axis: null, plane: null, point: false };
  if (s.kind === "face") return { ...base, face: s.face ?? null };
  if (s.kind === "edge") {
    const im = model?.instances.find((i) => i.id === inst);
    const em = im?.part_key ? model?.parts[im.part_key]?.bodies.find((b) => b.name === body)?.edges.find((e) => [...e.ref.faces].sort().join("|") === [...(s.faces ?? [])].sort().join("|")) : undefined;
    return { ...base, kind: "edge", edge: { kind: "between", faces: s.faces ?? null, face: null, hint: (em?.ref.hint as [number, number, number]) ?? null } };
  }
  return null;
}

const refLabel = (r: MateRef, names: Record<string, string>) =>
  `${names[r.instance] ?? r.instance} · ${r.kind === "face" ? r.face : r.kind === "edge" ? (r.edge?.faces ?? []).join("|") : `origin ${r.plane ?? r.axis ?? ""}`}`;

export function AssemblyWorkbench({ docId, name: initialName, project, role, head }: WorkbenchProps) {
  const doc = useEditor((s) => s.doc) as AssemblyDocument | null;
  const selection = useEditor((s) => s.selection);
  const save = useEditor((s) => s.save);
  const baseRevision = useEditor((s) => s.baseRevision);
  const past = useEditor((s) => s.past.length);
  const future = useEditor((s) => s.future.length);
  const ed = useEditor.getState;
  const canEdit = role === "owner" || role === "editor";
  const [preview, setPreview] = useState<{ number: number; content: AssemblyDocument } | null>(null);
  const readonly = !canEdit || save === "readonly" || !!preview;
  const [name, setName] = useState(initialName);
  const [tab, setTab] = useState<"mates" | "instance" | "check" | "motion" | "bom">("instance");
  const [pickMode, setPickMode] = useState<PickMode>("face");
  const [display, setDisplay] = useState<DisplayMode>("shaded_edges");
  const [explode, setExplode] = useState(0);
  const [insertOpen, setInsertOpen] = useState(false);
  const [revisions, setRevisions] = useState(false);
  const [palette, setPalette] = useState(false);
  const [focusInst, setFocusInst] = useState<string | null>(null);
  const [mateDraft, setMateDraft] = useState<Mate | null>(null);
  const [frameOverride, setFrameOverride] = useState<Record<string, number[][]> | null>(null);
  const [ctx, setCtx] = useState<{ x: number; y: number } | null>(null);
  const [left, setLeft] = useSplit(280, 200, 480, "af:asm:left");
  const [right, setRight] = useSplit(360, 260, 600, "af:asm:right");
  const vp = useRef<ViewportHandle>(null);
  useAutosave();

  const shown = preview?.content ?? doc;
  const live = useLiveModel<AssemblyModel>(docId, shown);
  const model = live.model;
  const names = useMemo(() => Object.fromEntries((shown?.instances ?? []).map((i) => [i.id, i.name])), [shown]);
  const channel = useProjectChannel(project.id);
  useEffect(() => { channel.send({ type: "presence", document_id: docId, selection: [...selection].slice(0, 50), camera: null }); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection, channel.connected]);

  /* ------------------------------------------------------------- view bodies with explode & motion frames */
  const viewBodies: ViewBody[] = useMemo(() => {
    if (!model) return [];
    const out: ViewBody[] = [];
    const centers: Record<string, THREE.Vector3> = {};
    const all = new THREE.Box3();
    for (const inst of model.instances) {
      if (inst.suppressed || !inst.part_key) continue;
      const part = model.parts[inst.part_key];
      const src = shown?.instances.find((i) => i.id === inst.id)?.source;
      const M = new THREE.Matrix4();
      const m = frameOverride?.[inst.id] ?? inst.matrix;
      M.set(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3], m[2][0], m[2][1], m[2][2], m[2][3], 0, 0, 0, 1);
      const box = new THREE.Box3();
      for (const b of part?.bodies ?? []) if ((!src?.body || b.name === src.body) && b.bbox) { box.expandByPoint(new THREE.Vector3(...b.bbox[0])); box.expandByPoint(new THREE.Vector3(...b.bbox[1])); }
      if (!box.isEmpty()) { box.applyMatrix4(M); centers[inst.id] = box.getCenter(new THREE.Vector3()); all.union(box); }
    }
    const c = all.isEmpty() ? new THREE.Vector3() : all.getCenter(new THREE.Vector3());
    for (const inst of model.instances) {
      if (inst.suppressed || !inst.part_key) continue;
      const part = model.parts[inst.part_key];
      const src = shown?.instances.find((i) => i.id === inst.id)?.source;
      const off = centers[inst.id] ? centers[inst.id].clone().sub(c).multiplyScalar(explode) : new THREE.Vector3();
      for (const b of part?.bodies ?? []) {
        if (src?.body && b.name !== src.body) continue;
        out.push({ key: bodyKeyOf(inst.id, b.name), label: `${inst.name} / ${b.name}`, mesh: b, matrix: frameOverride?.[inst.id] ?? inst.matrix,
          color: inst.color ?? undefined, dim: focusInst !== null && focusInst !== inst.id, explodeOffset: explode ? [off.x, off.y, off.z] : undefined });
      }
    }
    return out;
  }, [model, shown, explode, frameOverride, focusInst]);

  const selInstances = useMemo(() => [...new Set([...selection].map((k) => splitBodyKey(parseSel(k).body).inst))], [selection]);
  const activeInst = focusInst ?? selInstances[0] ?? null;

  const apply = (fn: (d: AssemblyDocument) => AssemblyDocument, label: string) => ed().apply<AssemblyDocument>(fn, label);

  const solveAndApply = async (content: AssemblyDocument, label: string) => {
    try {
      const r = await post<{ solve: AssemblyModel["solve"]; instance_errors: Record<string, string>; content: AssemblyDocument }>(
        apiPath("/api/v1/documents/{document_id}/assembly/solve", { document_id: docId }), { content });
      apply(() => r.content, label);
      if (!r.solve.converged) toast.warn("Mates not satisfied", r.solve.status + (r.solve.conflicting.length ? ` · conflicting: ${r.solve.conflicting.join(", ")}` : ""));
    } catch (e) {
      apply(() => content, label);
      toast.err("Solve failed", errorMessage(e));
    }
  };

  const addInstance = (src: AssemblyInstance["source"], label: string) => {
    if (!doc) return;
    const ids = new Set(doc.instances.map((i) => i.id));
    const id = uid("i", ids);
    // Place new components beside the existing ones (by stored placement and, when known, part extent) so they never
    // start overlapping; mates then position them exactly.
    let x = 0;
    for (const inst of doc.instances) {
      const mi = model?.instances.find((m) => m.id === inst.id);
      const bodies = mi?.part_key ? model?.parts[mi.part_key]?.bodies ?? [] : [];
      const maxX = bodies.reduce((a, b) => Math.max(a, b.bbox ? b.bbox[1][0] : 0), 0);
      const width = bodies.length ? maxX : 150;
      x = Math.max(x, inst.placement.position[0] + width + 40);
    }
    apply((d) => {
      d.instances.push({ id, name: label.slice(0, 160), source: src, placement: { position: [x, 0, 0], rotation: [0, 0, 0] }, group: null,
        fixed: d.instances.length === 0, suppressed: false, color: null });
      return d;
    }, `Insert ${label}`);
    toast.ok(`Inserted ${label}`, doc.instances.length === 0 ? "First component is fixed as the assembly base." : "Add mates to position it.");
  };

  const startMate = (type: Mate["type"]) => {
    if (!doc) return;
    const refs = [...selection].map((k) => refFromSel(k, model)).filter(Boolean) as MateRef[];
    const ids = new Set(doc.mates.map((m) => m.id));
    const meta = MATE_TYPES.find((m) => m.type === type)!;
    setMateDraft({ id: uid("m", ids), name: `${meta.label} ${doc.mates.filter((m) => m.type === type).length + 1}`, type,
      a: refs[0] ?? { instance: doc.instances[0]?.id ?? "", kind: "origin", face: null, edge: null, body: null, axis: null, plane: "XY", point: false },
      b: meta.needs === 2 ? refs[1] ?? null : null, value: meta.value ? 0 : null, flip: false, limits: null, suppressed: false });
    setTab("mates");
  };

  const commands: Command[] = [
    { id: "insert", group: "Assembly", title: "Insert component…", run: () => setInsertOpen(true), disabled: readonly },
    ...MATE_TYPES.map((m) => ({ id: `mate-${m.type}`, group: "Mate", title: m.label, run: () => startMate(m.type), disabled: readonly })),
    { id: "solve", group: "Assembly", title: "Solve mates and store placements", run: () => doc && void solveAndApply(doc, "Solve mates"), disabled: readonly },
    { id: "fit", group: "View", title: "Fit all", run: () => vp.current?.fit() },
    { id: "history", group: "Document", title: "Revision history", run: () => setRevisions(true) },
    { id: "undo", group: "Edit", title: "Undo", run: () => ed().undo(), disabled: readonly || !past },
    { id: "redo", group: "Edit", title: "Redo", run: () => ed().redo(), disabled: readonly || !future },
  ];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); return; }
      if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); void saveNow("Named version"); return; }
      if (typing) return;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) ed().redo(); else ed().undo(); }
      else if (!mod && (e.key === "f" || e.key === "F")) vp.current?.fit();
      else if (e.key === "Escape") { ed().clearSelection(); setFocusInst(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!doc || !shown) return null;
  const solve = model?.solve;
  const instErr = Object.fromEntries((model?.instances ?? []).filter((i) => i.error).map((i) => [i.id, i.error!]));

  return (
    <div className="wb">
      <header className="wb-top" aria-label="Assembly toolbar">
        <Link href={`/app/projects/${project.id}`} className="btn ghost sm" aria-label="Back to project"><ArrowLeft size={14} /></Link>
        <span className="small muted ellipsis" style={{ maxWidth: 160 }}>{project.name}</span><span className="faint">/</span>
        <input className="input" style={{ width: 220, height: 26, background: "transparent", borderColor: "transparent", fontWeight: 600 }} defaultValue={name} key={name}
          aria-label="Document name" disabled={!canEdit} maxLength={160}
          onBlur={async (e) => { const n = e.target.value.trim(); if (n && n !== name) { try { await patch(apiPath("/api/v1/documents/{document_id}", { document_id: docId }), { name: n }); setName(n); } catch (err) { toast.err("Rename failed", errorMessage(err)); } } }} />
        <span className="badge violet">Assembly</span>
        <SaveIndicator />
        <span className="sep" />
        <button className="btn ghost icon sm" aria-label="Undo" disabled={readonly || !past} onClick={() => ed().undo()}><Undo2 size={15} /></button>
        <button className="btn ghost icon sm" aria-label="Redo" disabled={readonly || !future} onClick={() => ed().redo()}><Redo2 size={15} /></button>
        <button className="btn ghost sm" disabled={readonly} onClick={() => void saveNow("Named version")}><Save size={14} />Version</button>
        <button className="btn ghost sm" onClick={() => setRevisions(true)}><History size={14} />History</button>
        <span className="sep" />
        <button className="btn primary sm" disabled={readonly} onClick={() => setInsertOpen(true)}><Plus size={14} />Insert component</button>
        <button className="btn sm" disabled={readonly} onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setCtx({ x: r.left, y: r.bottom + 4 }); }}><Link2 size={14} />Add mate</button>
        <div className="grow" />
        {solve && <span className="badge" title="Remaining degrees of freedom of unfixed instances">DOF {solve.dof}</span>}
        {solve && <StatusBadge status={solve.converged ? (solve.dof === 0 ? "fully_constrained" : "under_constrained") : "conflicting"} label={solve.status.replace(/_/g, " ")} />}
        <PresenceAvatars presence={channel.presence} docId={docId} />
        <ExportMenu docId={docId} preview={preview?.number ?? null} />
        <button className="btn ghost icon sm" aria-label="Command palette" onClick={() => setPalette(true)}><CmdIcon size={15} /></button>
      </header>
      <div className="wb-body" style={{ gridTemplateColumns: `${left}px 4px minmax(0,1fr) 4px ${right}px` }}>
        <aside className="wb-left" aria-label="Assembly structure">
          <div className="pane-head"><span>Components ({shown.instances.length})</span></div>
          <div className="pane-body">
            <ul className="tree" role="tree" aria-label="Instances">
              {shown.instances.map((i) => {
                const mi = model?.instances.find((x) => x.id === i.id);
                return (
                  <li key={i.id} role="treeitem" aria-selected={activeInst === i.id}>
                    <div className={`node${i.suppressed ? " suppressed" : ""}${instErr[i.id] ? " error" : ""}`} aria-selected={activeInst === i.id} tabIndex={0}
                      title={instErr[i.id] ?? `${i.source.kind === "catalog" ? "Catalog item" : "Part document"}${i.source.revision ? ` pinned to r${i.source.revision}` : " (follows head)"}`}
                      onClick={() => { setFocusInst(null); ed().select((model?.parts[mi?.part_key ?? ""]?.bodies ?? []).map((b) => `${bodyKeyOf(i.id, b.name)}|body`)); setTab("instance"); }}
                      onDoubleClick={() => setFocusInst(focusInst === i.id ? null : i.id)}>
                      <span className="ico">{i.fixed ? <Anchor size={13} /> : <Boxes size={13} />}</span>
                      <span className="grow ellipsis">{i.name}</span>
                      {mi?.dof != null && !i.fixed && <span className="badge" title="Remaining DOF">{mi.dof}</span>}
                      {instErr[i.id] && <ShieldAlert size={13} color="var(--err)" />}
                      <button className="btn ghost icon sm" aria-label={i.suppressed ? `Show ${i.name}` : `Suppress ${i.name}`} disabled={readonly}
                        onClick={(e) => { e.stopPropagation(); apply((d) => { const x = d.instances.find((y) => y.id === i.id)!; x.suppressed = !x.suppressed; return d; }, `Toggle ${i.name}`); }}>
                        {i.suppressed ? <EyeOff size={12} /> : <Eye size={12} />}</button>
                    </div>
                  </li>
                );
              })}
              {!shown.instances.length && <li className="faint small" style={{ padding: 8 }}>Empty assembly. Insert parts from this project or the component library.</li>}
            </ul>
            <div className="pane-head" style={{ marginTop: 12 }}><span>Mates ({shown.mates.length})</span></div>
            <ul className="tree" aria-label="Mates">
              {shown.mates.map((m) => {
                const bad = solve?.conflicting.includes(m.id) || solve?.errors[m.id];
                const red = solve?.redundant.includes(m.id);
                return (
                  <li key={m.id}>
                    <div className={`node${m.suppressed ? " suppressed" : ""}${bad ? " error" : ""}`} tabIndex={0} title={solve?.errors[m.id] ?? (red ? "Redundant (already implied by other mates)" : m.type)}
                      onClick={() => { setMateDraft(structuredClone(m)); setTab("mates"); }}>
                      <span className="ico"><Link2 size={13} /></span>
                      <span className="grow ellipsis">{m.name || m.id}</span>
                      <span className="faint tiny">{m.type}</span>
                      {red && <span className="badge warn">redundant</span>}
                      {solve?.joint_values[m.id] != null && <span className="num tiny">{fmt(solve.joint_values[m.id], 4)}</span>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </aside>
        <Splitter dir="v" label="Resize structure panel" onDrag={(d) => setLeft((w) => Math.min(480, Math.max(200, w + d)))} />
        <section className="wb-center" aria-label="3D assembly view">
          <Viewport ref={vp} bodies={viewBodies} display={display} pickMode={pickMode} selected={selection}
            onPick={(p, ev) => {
              if (!p) { if (!ev.shift) ed().clearSelection(); return; }
              const key = p.kind === "edge" && p.edge ? edgeKey(p.bodyKey, p.edge.ref.faces) : p.kind === "body" ? `${p.bodyKey}|body` : faceKey(p.bodyKey, p.face!.name);
              ed().select([key], ev.shift);
            }} />
          <div className="vp-toolbar" role="toolbar" aria-label="View tools">
            {(["face", "edge", "body"] as PickMode[]).map((m) => <button key={m} className="btn ghost sm" aria-pressed={pickMode === m} onClick={() => setPickMode(m)}>{m}</button>)}
            <select className="select" style={{ height: 24, width: 128 }} aria-label="Display mode" value={display} onChange={(e) => setDisplay(e.target.value as DisplayMode)}>
              <option value="shaded_edges">Shaded + edges</option><option value="shaded">Shaded</option><option value="wireframe">Wireframe</option><option value="xray">X-ray</option>
            </select>
            <label className="row small" style={{ padding: "0 6px" }}>Explode
              <input type="range" min={0} max={1.5} step={0.01} value={explode} aria-label="Explode factor" onChange={(e) => setExplode(Number(e.target.value))} style={{ width: 110 }} /></label>
            <button className="btn ghost icon sm" aria-label="Fit view" onClick={() => vp.current?.fit()}><Maximize size={13} /></button>
          </div>
          <div className="vp-status">
            {live.busy ? "Solving…" : model ? `${model.instances.length} instances · ${Object.keys(model.parts).length} unique parts · round-trip ${fmt(live.ms ?? 0, 3)} ms` : "Loading…"}
            {live.error ? <span style={{ color: "var(--err)" }}> · {errorMessage(live.error)}</span> : null}
            {frameOverride && <span style={{ color: "var(--accent)" }}> · showing motion frame</span>}
          </div>
          {preview && (
            <div className="alert info" style={{ position: "absolute", top: 48, left: 8, right: 8, maxWidth: 640, margin: "0 auto" }}>
              <Eye size={16} /><div className="grow">Viewing revision {preview.number} (read-only).</div>
              <button className="btn sm" onClick={() => setPreview(null)}>Back to latest</button>
            </div>
          )}
          {model && !viewBodies.length && !live.busy && (
            <div className="empty" style={{ position: "absolute", left: "50%", top: "40%", transform: "translate(-50%,-50%)", background: "rgba(15,18,22,.85)" }}>
              <strong style={{ color: "var(--text)" }}>No components</strong>
              {!readonly && <button className="btn primary sm" onClick={() => setInsertOpen(true)}><Plus size={13} />Insert component</button>}
            </div>
          )}
        </section>
        <Splitter dir="v" label="Resize inspector" onDrag={(d) => setRight((w) => Math.min(600, Math.max(260, w - d)))} />
        <aside className="wb-right" aria-label="Assembly inspector">
          <Tabs label="Assembly inspector" value={tab} onChange={setTab} tabs={[
            { key: "instance", label: "Instance" }, { key: "mates", label: "Mate" }, { key: "check", label: "Interference" },
            { key: "motion", label: "Motion" }, { key: "bom", label: "BOM" },
          ]} />
          <div className="pane-body">
            {tab === "instance" && <InstancePanel doc={shown} model={model} instId={activeInst} readonly={readonly} apply={apply} />}
            {tab === "mates" && <MatePanel draft={mateDraft} setDraft={setMateDraft} doc={shown} names={names} selection={selection} model={model} readonly={readonly}
              solveStatus={solve} onSave={(m) => {
                const next = structuredClone(doc);
                const i = next.mates.findIndex((x) => x.id === m.id);
                if (i >= 0) next.mates[i] = m; else next.mates.push(m);
                setMateDraft(null);
                void solveAndApply(next, `${i >= 0 ? "Edit" : "Add"} mate ${m.name || m.id}`);
              }} onDelete={(id) => { apply((d) => { d.mates = d.mates.filter((x) => x.id !== id); return d; }, `Delete mate ${id}`); setMateDraft(null); }} />}
            {tab === "check" && <InterferencePanel docId={docId} doc={shown} names={names} onShow={(a, b) => { setFocusInst(null); ed().select(viewBodies.filter((v) => v.key.startsWith(a + "::") || v.key.startsWith(b + "::")).map((v) => `${v.key}|body`)); }} />}
            {tab === "motion" && <MotionPanel docId={docId} doc={shown} names={names} onFrame={setFrameOverride} />}
            {tab === "bom" && <BomPanel model={model} name={name} />}
          </div>
        </aside>
      </div>
      <ContextMenu at={ctx} onClose={() => setCtx(null)} items={MATE_TYPES.map((m) => ({ label: m.label, hint: `${m.needs} ref${m.needs > 1 ? "s" : ""}`, onSelect: () => startMate(m.type) }))} />
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} />
      <InsertDialog open={insertOpen} onClose={() => setInsertOpen(false)} projectId={project.id} selfId={docId} onInsert={addInstance} />
      <RevisionsDialog open={revisions} onClose={() => setRevisions(false)} docId={docId} head={baseRevision || head} canEdit={canEdit}
        onPreview={async (n) => {
          try { const r = await get<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}/revisions/{number}", { document_id: docId, number: n })); setPreview({ number: n, content: r.revision.content as AssemblyDocument }); }
          catch (e) { toast.err("Could not open revision", errorMessage(e)); }
        }}
        onRestore={async (n) => {
          try {
            const r = await post<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}/restore", { document_id: docId }), { revision: n, base_revision: baseRevision, op_id: `restore-${crypto.randomUUID()}` });
            ed().init(docId, r.revision.content as AssemblyDocument, r.revision.number, !canEdit); setPreview(null); toast.ok(`Restored revision ${n}`);
          } catch (e) { toast.err("Restore failed", errorMessage(e)); }
        }} />
      <ConflictDialog docId={docId} projectId={project.id} name={name} kind="assembly" onReloaded={() => setMateDraft(null)} />
    </div>
  );
}

function InstancePanel({ doc, model, instId, readonly, apply }: {
  doc: AssemblyDocument; model: AssemblyModel | null; instId: string | null; readonly: boolean;
  apply: (fn: (d: AssemblyDocument) => AssemblyDocument, label: string) => void;
}) {
  const inst = doc.instances.find((i) => i.id === instId);
  if (!inst) return <div className="faint small">Select a component in the tree or the viewport.</div>;
  const mi = model?.instances.find((i) => i.id === inst.id);
  const upd = (fn: (i: AssemblyInstance) => void, label: string) => apply((d) => { fn(d.instances.find((x) => x.id === inst.id)!); return d; }, label);
  const vec = (k: "position" | "rotation", label: string) => (
    <div className="field">
      <span className="label">{label}</span>
      <div className="inline-fields">
        {[0, 1, 2].map((j) => (
          <input key={`${inst.id}-${k}-${j}-${inst.placement[k][j]}`} className="input num mono" type="number" step="any" disabled={readonly} aria-label={`${label} ${"XYZ"[j]}`}
            defaultValue={Number(inst.placement[k][j].toFixed(6))}
            onBlur={(e) => { const v = Number(e.target.value); if (Number.isFinite(v) && v !== inst.placement[k][j]) upd((x) => { x.placement[k][j] = v; }, `Move ${inst.name}`); }} />
        ))}
      </div>
    </div>
  );
  return (
    <div className="col gap-12">
      <div className="field"><label htmlFor="inst-name">Name</label>
        <input id="inst-name" key={inst.id} className="input" defaultValue={inst.name} disabled={readonly} maxLength={160}
          onBlur={(e) => e.target.value !== inst.name && upd((x) => { x.name = e.target.value; }, `Rename ${inst.id}`)} /></div>
      {mi?.error && <div className="alert err small">{mi.error}</div>}
      <dl className="kv">
        <dt>Source</dt><dd>{inst.source.kind === "catalog" ? `Catalog item ${inst.source.item_id?.slice(0, 8)}…` : `Document ${inst.source.document_id?.slice(0, 8)}…`}</dd>
        <dt>Revision</dt><dd>{inst.source.revision ? `pinned r${inst.source.revision}` : "follows head"}</dd>
        {mi?.dof != null && <><dt>Remaining DOF</dt><dd>{mi.dof}</dd></>}
        {mi?.provenance && <><dt>Provenance</dt><dd>{String((mi.provenance as any).label ?? (mi.provenance as any).name ?? (mi.provenance as any).kind)}</dd></>}
      </dl>
      {inst.source.kind === "document" && (
        <div className="row">
          <label className="small" htmlFor="pin">Pin revision</label>
          <input id="pin" className="input num" type="number" min={1} style={{ width: 90 }} disabled={readonly} defaultValue={inst.source.revision ?? ""} placeholder="head"
            onBlur={(e) => { const v = e.target.value ? Number(e.target.value) : null; if (v !== inst.source.revision) upd((x) => { x.source.revision = v; }, `Pin ${inst.name} to ${v ?? "head"}`); }} />
        </div>
      )}
      {vec("position", "Position (mm)")}
      {vec("rotation", "Rotation XYZ (deg)")}
      <div className="row wrap">
        <label className="checkbox"><input type="checkbox" checked={inst.fixed} disabled={readonly} onChange={(e) => upd((x) => { x.fixed = e.target.checked; }, `${e.target.checked ? "Fix" : "Float"} ${inst.name}`)} />Fixed</label>
        <label className="checkbox"><input type="checkbox" checked={inst.suppressed} disabled={readonly} onChange={(e) => upd((x) => { x.suppressed = e.target.checked; }, `Toggle ${inst.name}`)} />Suppressed</label>
        <label className="row small">Colour<input type="color" value={inst.color ?? "#9aa6b6"} disabled={readonly} aria-label="Instance colour"
          onChange={(e) => upd((x) => { x.color = e.target.value; }, `Colour ${inst.name}`)} /></label>
      </div>
      <button className="btn danger sm" disabled={readonly} onClick={() => {
        const used = doc.mates.filter((m) => m.a.instance === inst.id || m.b?.instance === inst.id).length;
        if (used && !window.confirm(`${inst.name} is used by ${used} mate(s), which will also be removed. Continue?`)) return;
        apply((d) => { d.instances = d.instances.filter((x) => x.id !== inst.id); d.mates = d.mates.filter((m) => m.a.instance !== inst.id && m.b?.instance !== inst.id); return d; }, `Remove ${inst.name}`);
      }}><Trash2 size={13} />Remove instance</button>
    </div>
  );
}

function MatePanel({ draft, setDraft, doc, names, selection, model, readonly, solveStatus, onSave, onDelete }: {
  draft: Mate | null; setDraft: (m: Mate | null) => void; doc: AssemblyDocument; names: Record<string, string>; selection: Set<string>;
  model: AssemblyModel | null; readonly: boolean; solveStatus: AssemblyModel["solve"] | undefined; onSave: (m: Mate) => void; onDelete: (id: string) => void;
}) {
  if (!draft) return (
    <div className="col gap-12">
      <div className="faint small">Select one or two faces/edges on different components, then choose a mate type from “Add mate”, or pick an existing mate in the tree.</div>
      {solveStatus && (
        <dl className="kv">
          <dt>Status</dt><dd>{solveStatus.status}</dd><dt>DOF</dt><dd>{solveStatus.dof}</dd><dt>Residual</dt><dd>{fmt(solveStatus.residual, 3)}</dd>
          {solveStatus.redundant.length > 0 && <><dt>Redundant</dt><dd>{solveStatus.redundant.join(", ")}</dd></>}
          {solveStatus.conflicting.length > 0 && <><dt>Conflicting</dt><dd>{solveStatus.conflicting.join(", ")}</dd></>}
          {solveStatus.limit_violations.length > 0 && <><dt>Limit violations</dt><dd>{solveStatus.limit_violations.map((l) => `${l.mate}=${fmt(l.value, 4)}`).join(", ")}</dd></>}
        </dl>
      )}
    </div>
  );
  const meta = MATE_TYPES.find((m) => m.type === draft.type)!;
  const existing = doc.mates.some((m) => m.id === draft.id);
  const refs = [...selection].map((k) => refFromSel(k, model)).filter(Boolean) as MateRef[];
  const refEditor = (k: "a" | "b") => {
    const r = draft[k];
    return (
      <div className="field">
        <span className="label">Reference {k.toUpperCase()}</span>
        <code className="ellipsis" title={r ? JSON.stringify(r) : ""}>{r ? refLabel(r, names) : "— not set —"}</code>
        <div className="row wrap">
          <button className="btn sm" disabled={!refs.length} onClick={() => setDraft({ ...draft, [k]: refs[k === "a" ? 0 : Math.min(1, refs.length - 1)] })}>Use selection</button>
          <select className="select" style={{ width: 160 }} aria-label={`Origin reference ${k}`} value="" onChange={(e) => {
            const [inst, kind, v] = e.target.value.split("|");
            if (!inst) return;
            setDraft({ ...draft, [k]: { instance: inst, kind: "origin", face: null, edge: null, body: null, axis: kind === "axis" ? v as MateRef["axis"] : null, plane: kind === "plane" ? v as MateRef["plane"] : null, point: false } });
          }}>
            <option value="">Origin plane/axis…</option>
            {doc.instances.map((i) => ["XY", "XZ", "YZ"].map((p) => <option key={`${i.id}p${p}`} value={`${i.id}|plane|${p}`}>{i.name} · plane {p}</option>)
              .concat(["X", "Y", "Z"].map((a) => <option key={`${i.id}a${a}`} value={`${i.id}|axis|${a}`}>{i.name} · axis {a}</option>)))}
          </select>
          {r && <label className="checkbox small"><input type="checkbox" checked={r.point} onChange={(e) => setDraft({ ...draft, [k]: { ...r, point: e.target.checked } })} />Use point</label>}
        </div>
      </div>
    );
  };
  return (
    <div className="col gap-12">
      <div className="row between"><strong>{existing ? "Edit" : "New"} {meta.label} mate</strong>{solveStatus?.errors[draft.id] && <span className="badge err">error</span>}</div>
      <div className="small muted">{meta.help}</div>
      <div className="field"><label htmlFor="mate-name">Name</label><input id="mate-name" className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></div>
      <div className="field"><label htmlFor="mate-type">Type</label>
        <select id="mate-type" className="select" value={draft.type} onChange={(e) => {
          const t = MATE_TYPES.find((m) => m.type === e.target.value)!;
          setDraft({ ...draft, type: t.type, value: t.value ? draft.value ?? 0 : null, b: t.needs === 2 ? draft.b : draft.b });
        }}>{MATE_TYPES.map((m) => <option key={m.type} value={m.type}>{m.label}</option>)}</select></div>
      {refEditor("a")}
      {(meta.needs === 2 || draft.b) && refEditor("b")}
      {meta.value && (
        <div className="field"><label htmlFor="mate-val">{draft.type === "revolute" ? "Axial offset (mm)" : `Value (${meta.value})`}</label>
          <input id="mate-val" className="input mono" value={draft.value ?? ""} onChange={(e) => { const n = Number(e.target.value); setDraft({ ...draft, value: e.target.value === "" ? null : Number.isFinite(n) ? n : e.target.value }); }} /></div>
      )}
      {(draft.type === "coincident" || draft.type === "distance" || draft.type === "concentric") && (
        <label className="checkbox"><input type="checkbox" checked={draft.flip} onChange={(e) => setDraft({ ...draft, flip: e.target.checked })} />Flip (aligned instead of opposed)</label>
      )}
      {(draft.type === "revolute" || draft.type === "slider") && (
        <div className="field">
          <label className="checkbox"><input type="checkbox" checked={!!draft.limits} onChange={(e) => setDraft({ ...draft, limits: e.target.checked ? [draft.type === "revolute" ? -90 : 0, draft.type === "revolute" ? 90 : 50] : null })} />
            Limits ({draft.type === "revolute" ? "deg" : "mm"})</label>
          {draft.limits && (
            <div className="row">
              <input className="input num" type="number" aria-label="Lower limit" value={draft.limits[0]} onChange={(e) => setDraft({ ...draft, limits: [Number(e.target.value), draft.limits![1]] })} />
              <input className="input num" type="number" aria-label="Upper limit" value={draft.limits[1]} onChange={(e) => setDraft({ ...draft, limits: [draft.limits![0], Number(e.target.value)] })} />
            </div>
          )}
        </div>
      )}
      {solveStatus?.errors[draft.id] && <div className="alert err small">{solveStatus.errors[draft.id]}</div>}
      <div className="row">
        {existing && <button className="btn danger sm" disabled={readonly} onClick={() => onDelete(draft.id)}><Trash2 size={13} />Delete</button>}
        <div className="grow" />
        <button className="btn sm" onClick={() => setDraft(null)}>Cancel</button>
        <button className="btn primary sm" disabled={readonly || !draft.a?.instance || (meta.needs === 2 && !draft.b?.instance)} onClick={() => onSave(draft)}>{existing ? "Apply" : "Add mate"}</button>
      </div>
    </div>
  );
}

function InterferencePanel({ docId, doc, names, onShow }: { docId: string; doc: AssemblyDocument; names: Record<string, string>; onShow: (a: string, b: string) => void }) {
  const [clearance, setClearance] = useState(0);
  const [res, setRes] = useState<{ pairs: any[]; interfering: any[]; clearance_violations?: any[]; method?: string; note?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try { setRes(await post(apiPath("/api/v1/documents/{document_id}/assembly/interference", { document_id: docId }), { content: doc, clearance_mm: clearance })); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <div className="col gap-12">
      <div className="row">
        <label className="small nowrap" htmlFor="clr">Required clearance (mm)</label>
        <input id="clr" className="input num" type="number" min={0} step="any" value={clearance} onChange={(e) => setClearance(Math.max(0, Number(e.target.value)))} style={{ width: 90 }} />
        <button className="btn primary sm" onClick={run} disabled={busy}>{busy ? <span className="spinner" /> : <ShieldAlert size={13} />}Check</button>
      </div>
      {err ? <ErrorBox error={err} /> : null}
      {res?.note && <div className="alert info small">{res.note}</div>}
      {res && (
        <>
          <div className={`alert ${res.interfering.length ? "err" : "ok"} small`}>
            {res.interfering.length ? `${res.interfering.length} interfering pair(s)` : "No interference"} of {res.pairs.length} pairs checked
            {res.clearance_violations && ` · ${res.clearance_violations.length} below ${clearance} mm clearance`}
          </div>
          <div className="table-wrap" style={{ maxHeight: 360 }}>
            <table className="table">
              <thead><tr><th>A</th><th>B</th><th className="num">Overlap mm³</th><th className="num">Min gap mm</th><th /></tr></thead>
              <tbody>
                {res.pairs.filter((p) => p.interferes || p.clearance_ok === false || p.touching).concat(res.pairs.filter((p) => !(p.interferes || p.clearance_ok === false || p.touching)).slice(0, 30)).map((p, i) => (
                  <tr key={i} style={{ color: p.interferes ? "var(--err)" : p.clearance_ok === false ? "var(--warn)" : undefined }}>
                    <td>{names[p.a] ?? p.a}</td><td>{names[p.b] ?? p.b}</td>
                    <td className="num">{fmt(p.volume_mm3, 4)}</td><td className="num">{p.min_distance_mm != null ? fmt(p.min_distance_mm, 4) : p.checked === "bbox" ? "> bbox" : "–"}</td>
                    <td><button className="btn ghost sm" onClick={() => onShow(p.a, p.b)}>Show</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {res.method && <div className="faint tiny">{res.method}</div>}
        </>
      )}
    </div>
  );
}

function MotionPanel({ docId, doc, names, onFrame }: { docId: string; doc: AssemblyDocument; names: Record<string, string>; onFrame: (f: Record<string, number[][]> | null) => void }) {
  const joints = doc.mates.filter((m) => (m.type === "revolute" || m.type === "slider") && !m.suppressed);
  const [mate, setMate] = useState(joints[0]?.id ?? "");
  const j = joints.find((m) => m.id === mate);
  const [range, setRange] = useState<[number, number]>([0, 90]);
  const [steps, setSteps] = useState(24);
  const [duration, setDuration] = useState(1);
  const [collide, setCollide] = useState(true);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [track, setTrack] = useState<string>("");
  useEffect(() => { if (j?.limits) setRange([j.limits[0], j.limits[1]]); else if (j) setRange(j.type === "revolute" ? [0, 90] : [0, 50]); }, [j]);
  useEffect(() => () => onFrame(null), [onFrame]);
  useEffect(() => {
    if (!playing || !res) return;
    const t = setInterval(() => setFrame((f) => (f + 1) % res.frames.length), Math.max(30, (res.dt_s ?? 0.05) * 1000));
    return () => clearInterval(t);
  }, [playing, res]);
  useEffect(() => { const f = res?.frames[frame]; onFrame(f?.ok ? f.placements : null); }, [frame, res, onFrame]);

  if (!joints.length) return <Empty title="No driven joints">Add a revolute or slider mate, then run a quasi-static motion study here.</Empty>;
  const run = async () => {
    setBusy(true); setErr(null); setPlaying(false);
    try {
      const r = await post<any>(apiPath("/api/v1/documents/{document_id}/assembly/kinematics", { document_id: docId }),
        { content: doc, study: { mate, start: range[0], end: range[1], steps, duration_s: duration, check_collisions: collide } });
      setRes(r); setFrame(0);
      setTrack(Object.keys(r.kinematics)[0] ?? "");
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const kin = res?.kinematics?.[track];
  const times = res ? res.values.map((_: number, i: number) => i * res.dt_s) : [];
  return (
    <div className="col gap-12">
      <div className="field"><label htmlFor="jm">Driven joint</label>
        <select id="jm" className="select" value={mate} onChange={(e) => setMate(e.target.value)}>{joints.map((m) => <option key={m.id} value={m.id}>{m.name || m.id} ({m.type})</option>)}</select></div>
      <div className="grid grid-2">
        <div className="field"><label>Start ({j?.type === "revolute" ? "deg" : "mm"})</label><input className="input num" type="number" value={range[0]} onChange={(e) => setRange([Number(e.target.value), range[1]])} /></div>
        <div className="field"><label>End</label><input className="input num" type="number" value={range[1]} onChange={(e) => setRange([range[0], Number(e.target.value)])} /></div>
        <div className="field"><label>Steps</label><input className="input num" type="number" min={2} max={120} value={steps} onChange={(e) => setSteps(Math.min(120, Math.max(2, Number(e.target.value))))} /></div>
        <div className="field"><label>Duration (s)</label><input className="input num" type="number" min={0.01} step="any" value={duration} onChange={(e) => setDuration(Math.max(0.01, Number(e.target.value)))} /></div>
      </div>
      <label className="checkbox"><input type="checkbox" checked={collide} onChange={(e) => setCollide(e.target.checked)} />Check collisions at every step (exact B-rep)</label>
      <button className="btn primary" onClick={run} disabled={busy || !mate}>{busy ? <span className="spinner" /> : <Play size={14} />}Run motion study</button>
      {err ? <ErrorBox error={err} /> : null}
      {res && (
        <>
          <div className="row">
            <button className="btn sm" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause size={13} /> : <Play size={13} />}</button>
            <input type="range" min={0} max={res.frames.length - 1} value={frame} onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)); }} aria-label="Motion frame" className="grow" />
            <span className="num small">{fmt(res.values[frame], 4)} {res.unit}</span>
          </div>
          {res.failed_steps.length > 0 && <div className="alert warn small">Solver did not converge at {res.failed_steps.length} step(s): {res.failed_steps.slice(0, 6).map((v: number) => fmt(v, 4)).join(", ")}</div>}
          {res.limit_violations.length > 0 && <div className="alert warn small">Joint limits exceeded at {res.limit_violations.length} step(s).</div>}
          {res.collisions && (res.collisions.length ? (
            <div className="alert err small">Collisions at {new Set(res.collisions.map((c: any) => c.value)).size} of {res.collision_frames_checked} positions, e.g. {names[res.collisions[0].a]} × {names[res.collisions[0].b]} at {fmt(res.collisions[0].value, 4)} {res.unit} ({fmt(res.collisions[0].volume_mm3, 3)} mm³)</div>
          ) : <div className="alert ok small">No collisions in {res.collision_frames_checked} checked positions.</div>)}
          <div className="field"><label htmlFor="trk">Track component</label>
            <select id="trk" className="select" value={track} onChange={(e) => setTrack(e.target.value)}>{Object.keys(res.kinematics).map((k) => <option key={k} value={k}>{names[k] ?? k}</option>)}</select></div>
          {kin && (
            <LineChart xLabel="time (s)" yLabel="speed (mm/s)" series={[
              { name: "speed |v|", color: "#27b6ec", points: kin.velocity_mm_s.map((v: number[], i: number) => [times[i], Math.hypot(...v)]) },
              { name: "|a| / 10", color: "#f2b73f", points: kin.acceleration_mm_s2.map((a: number[], i: number) => [times[i], Math.hypot(...a) / 10]) },
            ]} markers={[{ x: times[frame], label: "now", color: "#3ccf8e" }]} />
          )}
          <div className="faint tiny">{res.method}</div>
        </>
      )}
    </div>
  );
}

function BomPanel({ model, name }: { model: AssemblyModel | null; name: string }) {
  if (!model) return <Loading />;
  const rows = model.bom;
  const total = rows.reduce((a, r) => a + (r.total_mass_kg ?? 0), 0);
  const missing = rows.filter((r) => r.total_mass_kg == null);
  const csv = () => {
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [["Item", "Name", "Qty", "Part number", "Manufacturer", "Material", "Unit mass kg", "Total mass kg", "Mass basis"].map(esc).join(",")]
      .concat(rows.map((r) => [r.item, r.name, r.quantity, r.part_number, r.manufacturer, r.material, r.unit_mass_kg, r.total_mass_kg, r.mass_basis].map(esc).join(",")));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = `${name.replace(/[^\w-]+/g, "_")}_BOM.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  };
  return (
    <div className="col gap-12">
      <div className="row between"><strong className="small">{rows.length} line items</strong><button className="btn sm" onClick={csv}>Download CSV</button></div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>#</th><th>Name</th><th className="num">Qty</th><th>Material</th><th className="num">Mass kg</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.item}><td>{r.item}</td><td>{r.name}{r.part_number && <div className="faint tiny">{r.manufacturer} {r.part_number}</div>}</td><td className="num">{r.quantity}</td>
              <td className="small">{r.material ?? <span className="faint">—</span>}</td>
              <td className="num" title={r.mass_basis ?? "No material assigned"}>{r.total_mass_kg != null ? fmt(r.total_mass_kg, 4) : "—"}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <div className="small">Total mass: <strong className="num">{fmt(total, 5)} kg</strong>{missing.length > 0 && <span className="badge warn" style={{ marginLeft: 6 }}>incomplete: {missing.length} item(s) without mass</span>}</div>
      <div className="faint tiny">Masses are computed from CAD volume × assigned density, or quoted from a cited catalog record. Items without a material have no mass.</div>
    </div>
  );
}

function InsertDialog({ open, onClose, projectId, selfId, onInsert }: {
  open: boolean; onClose: () => void; projectId: string; selfId: string; onInsert: (src: AssemblyInstance["source"], label: string) => void;
}) {
  const [tab, setTab] = useState<"project" | "catalog">("project");
  const [q, setQ] = useState("");
  const proj = useQuery({ queryKey: ["project", projectId], queryFn: () => get<{ documents: { id: string; name: string; kind: string; head_revision: number }[] }>(apiPath("/api/v1/projects/{project_id}", { project_id: projectId })), enabled: open });
  const cat = useQuery({ queryKey: ["catalog-cad", q], queryFn: () => get<{ items: CatalogItem[] }>(apiPath("/api/v1/catalog/items", {}, { q: q || undefined, has_cad: true, limit: 40 })), enabled: open && tab === "catalog" });
  return (
    <Dialog open={open} onClose={onClose} title="Insert component" wide>
      <Tabs label="Component source" value={tab} onChange={setTab} tabs={[{ key: "project", label: "Project parts" }, { key: "catalog", label: "Component library" }]} />
      {tab === "project" && (
        <div className="col">
          {proj.isLoading && <Loading />}
          {proj.data?.documents.filter((d) => d.kind === "part" && d.id !== selfId).map((d) => (
            <div key={d.id} className="row card tight">
              <Boxes size={16} /><div className="grow"><strong>{d.name}</strong><div className="faint tiny">head r{d.head_revision} · instance follows head unless pinned</div></div>
              <button className="btn sm primary" onClick={() => { onInsert({ kind: "document", document_id: d.id, revision: null, body: null, item_id: null }, d.name); onClose(); }}>Insert</button>
            </div>
          ))}
          {proj.data && !proj.data.documents.some((d) => d.kind === "part" && d.id !== selfId) && <Empty title="No part documents">Create parts in this project first.</Empty>}
        </div>
      )}
      {tab === "catalog" && (
        <div className="col">
          <div className="row"><Search size={14} /><input className="input" placeholder="Search parts with CAD geometry (e.g. M8, 6204, cell)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search catalog" /></div>
          {cat.isLoading && <Loading />}
          <div className="col" style={{ maxHeight: "50vh", overflow: "auto" }}>
            {cat.data?.items.map((i) => (
              <div key={i.id} className="row card tight">
                <div className="grow">
                  <strong>{i.name}</strong>
                  <div className="row wrap tiny"><AssetBadge cls={i.asset_class} /><span className="faint">{i.standard ?? i.manufacturer ?? ""}</span><span className="faint">{i.license}</span></div>
                </div>
                <button className="btn sm primary" onClick={() => { onInsert({ kind: "catalog", item_id: i.id, document_id: null, revision: null, body: null }, i.name); onClose(); }}>Insert</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </Dialog>
  );
}
