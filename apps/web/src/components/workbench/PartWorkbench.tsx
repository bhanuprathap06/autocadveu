"use client";

import {
  ArrowLeft, Bookmark, Box, Circle, Combine, Command as CmdIcon, Eye, FlipHorizontal, Grid2x2, History, Keyboard, Layers, Maximize,
  MousePointerClick, Move3d, PanelBottom, PanelLeft, PanelRight, PencilRuler, Redo2, RotateCcw, RotateCw, Save, Scissors, Spline, SquareDashed,
  Undo2, Waypoints, Axis3d, Cylinder,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { apiPath, errorMessage, get, patch, post } from "@/api/client";
import type { Feature, FeatureType, MaterialAssignment, PartDocument, PartModel, Revision } from "@/api/types";
import { CommandPalette, ContextMenu, Dialog, Splitter, Tabs, useSplit, type Command, type MenuItem } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { SketchOverlay } from "@/components/viewer/SketchOverlay";
import { MeasureLine, Viewport, edgeKey, faceKey, type DisplayMode, type NamedView, type PickMode, type Section, type ViewportHandle } from "@/components/viewer/Viewport";
import { fmt, LENGTH_UNITS } from "@/lib/mesh";
import { useMe } from "@/lib/auth";
import { useProjectChannel } from "@/lib/realtime";
import { ConflictDialog, ExportMenu, PresenceAvatars, SaveIndicator, ShortcutsDialog } from "./chrome";
import { FEATURE_LABEL, newFeature, planeRef } from "./defaults";
import { FeatureEditor } from "./FeatureEditor";
import { selectedEdges, selectedFace, selectedFaces } from "./fields";
import { Inspector, type MeasureResult } from "./Inspector";
import { Console, FeatureTree, ParametersPanel } from "./panels";
import { RevisionsDialog } from "./RevisionsDialog";
import { saveNow, useAutosave, useLiveModel } from "./session";
import { SketchEditor } from "./SketchEditor";
import { bodyNames, nextFeatureId, parseSel, useEditor } from "./store";

export interface WorkbenchProps {
  docId: string;
  name: string;
  project: { id: string; name: string };
  role: string | null;
  head: number;
}

const TOOLS: { type: FeatureType; icon: ReactNode; key?: string; needsBody?: boolean }[] = [
  { type: "sketch", icon: <PencilRuler size={16} />, key: "S" },
  { type: "extrude", icon: <Box size={16} />, key: "E" },
  { type: "revolve", icon: <RotateCw size={16} /> },
  { type: "sweep", icon: <Spline size={16} /> },
  { type: "loft", icon: <Layers size={16} /> },
  { type: "hole", icon: <Circle size={16} />, needsBody: true },
  { type: "fillet", icon: <Waypoints size={16} />, needsBody: true },
  { type: "chamfer", icon: <SquareDashed size={16} />, needsBody: true },
  { type: "shell", icon: <Scissors size={16} />, needsBody: true },
  { type: "boolean", icon: <Combine size={16} />, needsBody: true },
  { type: "linear_pattern", icon: <Grid2x2 size={16} />, needsBody: true },
  { type: "circular_pattern", icon: <RotateCcw size={16} />, needsBody: true },
  { type: "mirror", icon: <FlipHorizontal size={16} />, needsBody: true },
  { type: "datum_plane", icon: <Layers size={16} /> },
  { type: "datum_axis", icon: <Axis3d size={16} /> },
  { type: "primitive", icon: <Cylinder size={16} /> },
  { type: "transform", icon: <Move3d size={16} />, needsBody: true },
];

interface Editing { feature: Feature; isNew: boolean; insertAt: number }

function useBookmarks(docId: string) {
  const key = `af:views:${docId}`;
  const [list, setList] = useState<{ name: string; cam: { position: number[]; target: number[]; zoom: number } }[]>([]);
  useEffect(() => { try { setList(JSON.parse(localStorage.getItem(key) ?? "[]")); } catch { setList([]); } }, [key]);
  const save = (l: typeof list) => { setList(l); try { localStorage.setItem(key, JSON.stringify(l)); } catch { /* storage unavailable */ } };
  return [list, save] as const;
}

export function PartWorkbench({ docId, name: initialName, project, role, head }: WorkbenchProps) {
  const doc = useEditor((s) => s.doc) as PartDocument | null;
  const selection = useEditor((s) => s.selection);
  const rollback = useEditor((s) => s.rollback);
  const sketchEdit = useEditor((s) => s.sketchEdit);
  const save = useEditor((s) => s.save);
  const baseRevision = useEditor((s) => s.baseRevision);
  const log = useEditor((s) => s.log);
  const past = useEditor((s) => s.past.length);
  const future = useEditor((s) => s.future.length);
  const ed = useEditor.getState;
  const canEdit = role === "owner" || role === "editor";
  const canComment = canEdit || role === "reviewer";

  const [name, setName] = useState(initialName);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [treeSel, setTreeSel] = useState<string | null>(null);
  const [pickMode, setPickMode] = useState<PickMode>("face");
  const [display, setDisplay] = useState<DisplayMode>("shaded_edges");
  const [ortho, setOrtho] = useState(false);
  const [section, setSection] = useState<Section>({ enabled: false, axis: "z", offset: 0, flip: false });
  const [showSketches, setShowSketches] = useState(false);
  const [measure, setMeasure] = useState<MeasureResult | null>(null);
  const [ctx, setCtx] = useState<{ at: { x: number; y: number } } | null>(null);
  const [palette, setPalette] = useState(false);
  const [revisions, setRevisions] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const [versionDlg, setVersionDlg] = useState(false);
  const [versionMsg, setVersionMsg] = useState("");
  const [preview, setPreview] = useState<{ number: number; content: PartDocument } | null>(null);
  const [leftTab, setLeftTab] = useState<"tree" | "params">("tree");
  const [panels, setPanels] = useState({ left: true, right: true, bottom: true });
  const [left, setLeft] = useSplit(280, 200, 520, "af:wb:left");
  const [right, setRight] = useSplit(320, 240, 560, "af:wb:right");
  const [bottom, setBottom] = useSplit(150, 60, 420, "af:wb:bottom");
  const [bookmarks, setBookmarks] = useBookmarks(docId);
  const vp = useRef<ViewportHandle>(null);
  const readonly = !canEdit || save === "readonly" || !!preview;

  useAutosave();

  // Working content shown in the viewport: revision preview, or the document with the feature being edited applied.
  const shown = useMemo<PartDocument | null>(() => {
    if (preview) return preview.content;
    if (!doc) return null;
    if (!editing) return doc;
    const feats = [...doc.features];
    if (editing.isNew) feats.splice(editing.insertAt, 0, editing.feature);
    else feats[feats.findIndex((f) => f.id === editing.feature.id)] = editing.feature;
    return { ...doc, features: feats };
  }, [doc, editing, preview]);

  const live = useLiveModel<PartModel>(docId, shown, { rollback: preview ? null : rollback });
  const model = live.model;
  const unit = doc?.units.length ?? "mm";
  const bodies = useMemo(() => [...new Set([...(model?.bodies.map((b) => b.name) ?? []), ...(doc ? bodyNames(doc) : [])])], [model, doc]);

  /* ---------------------------------------------------------------- realtime */
  const me = useMe();
  const channel = useProjectChannel(project.id, (ev) => {
    // Our own saves echo back over the socket; concurrency for our other tabs is enforced by the 409 path.
    if (ev.type === "revision.created" && ev.document_id === docId && ev.by !== me.data?.user.id) {
      const st = ed();
      if (ev.revision > st.baseRevision && st.save === "saved" && !preview) {
        get<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}", { document_id: docId })).then((r) => {
          if (ed().save !== "saved") return;
          ed().init(docId, r.revision.content as PartDocument, r.revision.number, !canEdit);
          toast.info(`Updated to revision ${r.revision.number}`, `${ev.name ?? "A collaborator"} saved changes.`);
        }).catch(() => { /* next save reports the conflict */ });
      } else if (ev.revision > st.baseRevision && st.save !== "saved") {
        ed().addLog("warn", `${ev.name ?? "A collaborator"} saved revision ${ev.revision}; your next save will ask how to resolve the conflict.`);
      }
    }
  });
  const lastSent = useRef(0);
  useEffect(() => {
    const now = Date.now();
    if (now - lastSent.current < 250) return;
    lastSent.current = now;
    channel.send({ type: "presence", document_id: docId, selection: [...selection].slice(0, 50), camera: vp.current?.getCamera() ?? null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, docId, channel.connected]);

  /* ---------------------------------------------------------------- feature operations */
  const insertIndex = () => {
    if (!doc) return 0;
    if (rollback) return doc.features.findIndex((f) => f.id === rollback) + 1;
    return doc.features.length;
  };

  const startFeature = useCallback((type: FeatureType) => {
    if (!doc || readonly) return;
    const face = selectedFace(selection);
    const firstSel = [...selection].map(parseSel)[0];
    const body = firstSel?.body ?? bodies[0] ?? null;
    const prefix = type === "sketch" ? "sk" : type.startsWith("datum") ? "d" : "f";
    const id = nextFeatureId(doc, prefix);
    const count = doc.features.filter((f) => f.type === type).length + 1;
    const f = newFeature(type, id, { doc, body: type === "primitive" || type === "sketch" || type === "datum_plane" || type === "datum_axis" ? null : body, name: `${FEATURE_LABEL[type]} ${count}` });
    const planar = face && model?.bodies.find((b) => b.name === face.body)?.faces.find((x) => x.name === face.face)?.geometry?.surface === "plane";
    if ((f.type === "sketch" || f.type === "hole") && face && planar) f.params.plane = planeRef({ kind: "face", name: null, body: face.body, face: face.face });
    if ((f.type === "fillet" || f.type === "chamfer")) {
      const es = selectedEdges(selection, model);
      if (es.length) { f.params.body = es[0].body; f.params.edges = es.filter((e) => e.body === es[0].body).map((e) => e.ref); }
    }
    if (f.type === "shell") {
      const fs = selectedFaces(selection);
      if (fs.length) { f.params.body = fs[0].body; f.params.remove_faces = fs.filter((x) => x.body === fs[0].body).map((x) => x.face); }
    }
    if (f.type === "extrude" && treeSel && doc.features.find((x) => x.id === treeSel)?.type === "sketch") f.params.sketch = treeSel;
    if (f.type === "boolean") { f.params.target = bodies[0] ?? ""; f.params.tools = bodies.slice(1, 2); }
    if (f.type === "extrude" || f.type === "revolve" || f.type === "sweep" || f.type === "loft" || f.type === "primitive") {
      f.params.operation = bodies.length ? "join" : "new";
      f.params.body = bodies.length ? bodies[0] : null;
    }
    setEditing({ feature: f, isNew: true, insertAt: insertIndex() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, readonly, selection, bodies, model, treeSel, rollback]);

  const editFeature = (id: string) => {
    const f = doc?.features.find((x) => x.id === id);
    if (!f) return;
    setEditing({ feature: structuredClone(f), isNew: false, insertAt: 0 });
  };

  const commitEditing = () => {
    if (!editing || readonly) return;
    const e = editing;
    ed().apply((d) => {
      if (e.isNew) d.features.splice(e.insertAt, 0, e.feature);
      else d.features[d.features.findIndex((f) => f.id === e.feature.id)] = e.feature;
      return d;
    }, `${e.isNew ? "Add" : "Edit"} ${e.feature.name || FEATURE_LABEL[e.feature.type]}`);
    if (e.isNew && rollback) ed().setRollback(e.feature.id);
    setEditing(null);
    setTreeSel(e.feature.id);
    if (e.isNew && e.feature.type === "sketch") ed().setSketchEdit(e.feature.id);
  };

  const mutateFeature = (id: string, fn: (f: Feature) => Feature, label: string) =>
    ed().apply((d) => { const i = d.features.findIndex((f) => f.id === id); if (i >= 0) d.features[i] = fn(d.features[i]); return d; }, label);

  /* ---------------------------------------------------------------- selection & picking */
  const onPick: React.ComponentProps<typeof Viewport>["onPick"] = (p, ev) => {
    if (!p) { if (!ev.shift) ed().clearSelection(); return; }
    const key = p.kind === "edge" && p.edge ? edgeKey(p.bodyKey, p.edge.ref.faces) : p.kind === "body" ? `${p.bodyKey}|body` : faceKey(p.bodyKey, p.face!.name);
    ed().select([key], ev.shift);
    if (p.face) {
      const owner = p.face.name.split(":")[0];
      if (doc?.features.some((f) => f.id === owner)) setTreeSel(owner);
    }
  };

  const viewBodies = useMemo(() => (model?.bodies ?? []).map((b) => ({ key: b.name, label: b.name, mesh: b,
    color: b.material ? (b.material.density_kg_m3 < 3000 ? "#b4bcc8" : b.material.density_kg_m3 > 7000 ? "#8d97a5" : "#a3adbb") : undefined })), [model]);

  const bbox = useMemo(() => {
    const b = new THREE.Box3();
    for (const x of model?.bodies ?? []) if (x.bbox) { b.expandByPoint(new THREE.Vector3(...x.bbox[0])); b.expandByPoint(new THREE.Vector3(...x.bbox[1])); }
    return b.isEmpty() ? null : b;
  }, [model]);

  /* ---------------------------------------------------------------- actions */
  const doRestore = async (n: number) => {
    if (save !== "saved" && !window.confirm("You have unsaved changes. Restoring will discard them. Continue?")) return;
    try {
      const r = await post<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}/restore", { document_id: docId }),
        { revision: n, base_revision: baseRevision, op_id: `restore-${crypto.randomUUID()}` });
      ed().init(docId, r.revision.content as PartDocument, r.revision.number, !canEdit);
      setPreview(null);
      toast.ok(`Restored revision ${n}`, `Saved as new revision ${r.revision.number}.`);
    } catch (e) { toast.err("Restore failed", errorMessage(e)); }
  };
  const doPreview = async (n: number) => {
    try {
      const r = await get<{ revision: Revision }>(apiPath("/api/v1/documents/{document_id}/revisions/{number}", { document_id: docId, number: n }));
      setEditing(null);
      ed().setSketchEdit(null);
      setPreview({ number: n, content: r.revision.content as PartDocument });
    } catch (e) { toast.err("Could not open revision", errorMessage(e)); }
  };
  const rename = async (n: string) => {
    if (!n.trim() || n === name) return;
    try { await patch(apiPath("/api/v1/documents/{document_id}", { document_id: docId }), { name: n.trim() }); setName(n.trim()); }
    catch (e) { toast.err("Rename failed", errorMessage(e)); }
  };
  const setView = (v: NamedView) => vp.current?.setView(v);

  const commands: Command[] = useMemo(() => [
    ...TOOLS.map((t) => ({ id: `new-${t.type}`, group: "Create", title: FEATURE_LABEL[t.type], shortcut: t.key, run: () => startFeature(t.type),
      disabled: readonly || (t.needsBody && !bodies.length) })),
    { id: "undo", group: "Edit", title: "Undo", shortcut: "Ctrl+Z", run: () => ed().undo(), disabled: readonly || !past },
    { id: "redo", group: "Edit", title: "Redo", shortcut: "Ctrl+Shift+Z", run: () => ed().redo(), disabled: readonly || !future },
    { id: "save", group: "Document", title: "Save named version…", shortcut: "Ctrl+S", run: () => setVersionDlg(true), disabled: readonly },
    { id: "history", group: "Document", title: "Revision history", run: () => setRevisions(true) },
    { id: "fit", group: "View", title: "Fit all", shortcut: "F", run: () => vp.current?.fit() },
    ...(["front", "back", "top", "bottom", "right", "left", "iso"] as NamedView[]).map((v) => ({ id: `view-${v}`, group: "View", title: `${v[0].toUpperCase()}${v.slice(1)} view`, run: () => setView(v) })),
    { id: "ortho", group: "View", title: ortho ? "Perspective projection" : "Orthographic projection", run: () => setOrtho((o) => !o) },
    { id: "section", group: "View", title: section.enabled ? "Disable section view" : "Section view", run: () => setSection((s) => ({ ...s, enabled: !s.enabled })) },
    { id: "sketches", group: "View", title: showSketches ? "Hide sketches" : "Show sketches", run: () => setShowSketches((s) => !s) },
    ...(["shaded", "shaded_edges", "wireframe", "xray"] as DisplayMode[]).map((d) => ({ id: `disp-${d}`, group: "Display", title: d.replace("_", " + "), run: () => setDisplay(d) })),
    ...(["face", "edge", "body"] as PickMode[]).map((m) => ({ id: `pick-${m}`, group: "Select", title: `Select ${m}s`, run: () => setPickMode(m) })),
    { id: "clear", group: "Select", title: "Clear selection", shortcut: "Esc", run: () => ed().clearSelection() },
    { id: "keys", group: "Help", title: "Keyboard shortcuts", run: () => setShortcuts(true) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [readonly, bodies.length, past, future, ortho, section.enabled, showSketches, startFeature]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); return; }
      if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); if (!readonly) setVersionDlg(true); return; }
      if (typing) return;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) ed().redo(); else ed().undo(); return; }
      if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); ed().redo(); return; }
      if (mod || e.altKey || sketchEdit) return;
      if (e.key === "Escape") { if (editing) setEditing(null); else ed().clearSelection(); }
      else if (e.key === "f" || e.key === "F") vp.current?.fit();
      else if (e.key === "1") setView("front");
      else if (e.key === "3") setView("right");
      else if (e.key === "7") setView("top");
      else if (e.key === "0") setView("iso");
      else if (e.key === "s" || e.key === "S") startFeature("sketch");
      else if (e.key === "e" || e.key === "E") startFeature("extrude");
      else if (e.key === "?") setShortcuts(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!doc) return null;
  const status = Object.fromEntries((model?.features ?? []).map((f) => [f.id, f]));
  const sketchFeature = sketchEdit ? doc.features.find((f) => f.id === sketchEdit) : null;
  const sketchState = sketchEdit ? model?.sketches[sketchEdit] : null;
  const errors = (model?.features ?? []).filter((f) => f.status === "error").length;
  const tris = (model?.bodies ?? []).reduce((a, b) => a + b.triangle_count, 0);

  const ctxItems: MenuItem[] = (() => {
    const face = selectedFace(selection);
    const fGeom = face ? model?.bodies.find((b) => b.name === face.body)?.faces.find((x) => x.name === face.face)?.geometry : null;
    const edges = selectedEdges(selection, model);
    return [
      { label: "Sketch on face", disabled: readonly || fGeom?.surface !== "plane", onSelect: () => startFeature("sketch"), icon: <PencilRuler size={13} /> },
      { label: "Hole on face", disabled: readonly || fGeom?.surface !== "plane", onSelect: () => startFeature("hole"), icon: <Circle size={13} /> },
      { label: `Fillet ${edges.length || ""} edge${edges.length === 1 ? "" : "s"}`, disabled: readonly || !edges.length, onSelect: () => startFeature("fillet"), icon: <Waypoints size={13} /> },
      { label: "Chamfer edges", disabled: readonly || !edges.length, onSelect: () => startFeature("chamfer") },
      { label: "Shell (open this face)", disabled: readonly || !face, onSelect: () => startFeature("shell"), icon: <Scissors size={13} /> },
      { separator: true, label: "" },
      { label: "Fit view", hint: "F", onSelect: () => vp.current?.fit(), icon: <Maximize size={13} /> },
      { label: "Clear selection", hint: "Esc", onSelect: () => ed().clearSelection() },
    ];
  })();

  const cols = `${panels.left ? `${left}px 4px` : ""} minmax(0,1fr) ${panels.right ? `4px ${right}px` : ""}`.trim();
  return (
    <div className="wb">
      <header className="wb-top" aria-label="Workbench toolbar">
        <Link href={`/app/projects/${project.id}`} className="btn ghost sm" aria-label="Back to project"><ArrowLeft size={14} /></Link>
        <span className="small muted ellipsis" style={{ maxWidth: 160 }}>{project.name}</span>
        <span className="faint">/</span>
        <input className="input" style={{ width: 220, height: 26, background: "transparent", borderColor: "transparent", fontWeight: 600 }}
          defaultValue={name} key={name} aria-label="Document name" disabled={!canEdit} maxLength={160}
          onBlur={(e) => void rename(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
        <span className="badge">Part</span>
        <SaveIndicator />
        <span className="sep" />
        <button className="btn ghost icon sm" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={readonly || !past} onClick={() => ed().undo()}><Undo2 size={15} /></button>
        <button className="btn ghost icon sm" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={readonly || !future} onClick={() => ed().redo()}><Redo2 size={15} /></button>
        <button className="btn ghost sm" disabled={readonly} onClick={() => setVersionDlg(true)} title="Save a named version (Ctrl+S)"><Save size={14} />Version</button>
        <button className="btn ghost sm" onClick={() => setRevisions(true)}><History size={14} />History</button>
        <span className="sep" />
        <label className="sr-only" htmlFor="units">Display units</label>
        <select id="units" className="select" style={{ width: 70, height: 26 }} value={unit} disabled={readonly}
          onChange={(e) => ed().apply((d) => { d.units.length = e.target.value as PartDocument["units"]["length"]; return d; }, `Units: ${e.target.value}`)}>
          {Object.keys(LENGTH_UNITS).map((u) => <option key={u}>{u}</option>)}
        </select>
        <div className="grow" />
        <PresenceAvatars presence={channel.presence} docId={docId} />
        <span className={`dot ${channel.connected ? "ok" : ""}`} title={channel.connected ? "Realtime connected" : "Realtime disconnected (changes still save)"} />
        <ExportMenu docId={docId} preview={preview?.number ?? null} />
        <button className="btn ghost icon sm" aria-label="Command palette" title="Command palette (Ctrl+K)" onClick={() => setPalette(true)}><CmdIcon size={15} /></button>
        <button className="btn ghost icon sm" aria-label="Keyboard shortcuts" onClick={() => setShortcuts(true)}><Keyboard size={15} /></button>
        <span className="sep" />
        <button className="btn ghost icon sm" aria-pressed={panels.left} aria-label="Toggle feature panel" onClick={() => setPanels((p) => ({ ...p, left: !p.left }))}><PanelLeft size={15} /></button>
        <button className="btn ghost icon sm" aria-pressed={panels.bottom} aria-label="Toggle console" onClick={() => setPanels((p) => ({ ...p, bottom: !p.bottom }))}><PanelBottom size={15} /></button>
        <button className="btn ghost icon sm" aria-pressed={panels.right} aria-label="Toggle inspector" onClick={() => setPanels((p) => ({ ...p, right: !p.right }))}><PanelRight size={15} /></button>
      </header>

      <div className="wb-body" style={{ gridTemplateColumns: cols }}>
        {panels.left && (
          <>
            <aside className="wb-left" aria-label="Features and parameters">
              <div className="palette" role="toolbar" aria-label="Create features">
                {TOOLS.map((t) => (
                  <button key={t.type} className="tool" disabled={readonly || (t.needsBody && !bodies.length) || !!sketchEdit}
                    title={`${FEATURE_LABEL[t.type]}${t.key ? ` (${t.key})` : ""}`} onClick={() => startFeature(t.type)}>
                    {t.icon}<span className="ellipsis" style={{ maxWidth: "100%" }}>{FEATURE_LABEL[t.type].replace(" pattern", "")}</span>
                  </button>
                ))}
              </div>
              <Tabs label="Model data" value={leftTab} onChange={setLeftTab} tabs={[
                { key: "tree", label: "History", badge: errors ? <span className="badge err">{errors}</span> : null },
                { key: "params", label: "Parameters", badge: <span className="badge">{doc.parameters.length}</span> },
              ]} />
              <div className="pane-body">
                {leftTab === "tree" ? (
                  <FeatureTree doc={preview?.content ?? doc} model={model} rollback={rollback} selected={treeSel} readonly={readonly}
                    onSelect={setTreeSel} onEdit={editFeature} onEditSketch={(id) => { setEditing(null); ed().setSketchEdit(id); }}
                    onRename={(id, n) => { const f = doc.features.find((x) => x.id === id); if (f && n !== f.name) mutateFeature(id, (x) => ({ ...x, name: n }), `Rename ${id}`); }}
                    onToggleSuppress={(id) => mutateFeature(id, (x) => ({ ...x, suppressed: !x.suppressed }), `Toggle suppression of ${id}`)}
                    onRollback={(id) => ed().setRollback(id)}
                    onMove={(id, dir) => ed().apply((d) => { const i = d.features.findIndex((f) => f.id === id); const j = i + dir;
                      if (j < 0 || j >= d.features.length) return d; [d.features[i], d.features[j]] = [d.features[j], d.features[i]]; return d; }, `Reorder ${id}`)}
                    onDelete={(id) => { ed().apply((d) => { d.features = d.features.filter((f) => f.id !== id); return d; }, `Delete ${id}`); if (rollback === id) ed().setRollback(null); }}
                    onDuplicate={(id) => ed().apply((d) => {
                      const i = d.features.findIndex((f) => f.id === id);
                      const copy = structuredClone(d.features[i]);
                      copy.id = nextFeatureId(d, copy.type === "sketch" ? "sk" : "f");
                      copy.name = `${copy.name || id} copy`;
                      d.features.splice(i + 1, 0, copy);
                      return d;
                    }, `Duplicate ${id}`)} />
                ) : (
                  <ParametersPanel doc={doc} model={model} readonly={readonly}
                    onChange={(ps, label) => ed().apply((d) => { d.parameters = ps; return d; }, label)} />
                )}
              </div>
            </aside>
            <Splitter dir="v" label="Resize feature panel" onDrag={(d) => setLeft((w) => Math.min(520, Math.max(200, w + d)))} />
          </>
        )}

        <div style={{ display: "grid", gridTemplateRows: panels.bottom ? `minmax(0,1fr) 4px ${bottom}px` : "minmax(0,1fr)", minWidth: 0, minHeight: 0 }}>
          <section className="wb-center" aria-label={sketchEdit ? "Sketch editor" : "3D viewport"}>
            {sketchEdit && sketchFeature?.type === "sketch" ? (
              <SketchEditor key={sketchEdit} docId={docId} params={sketchFeature.params} parameters={doc.parameters} readonly={readonly}
                title={sketchFeature.name || sketchFeature.id}
                planeLabel={sketchFeature.params.plane.kind === "standard" ? `${sketchFeature.params.plane.name} plane` : sketchFeature.params.plane.kind === "face" ? `Face ${sketchFeature.params.plane.face}` : `Datum ${sketchFeature.params.plane.datum}`}
                onCommit={(p, label) => mutateFeature(sketchEdit, (f) => ({ ...f, params: p } as Feature), `${sketchFeature.name || sketchEdit}: ${label}`)}
                onClose={() => ed().setSketchEdit(null)} />
            ) : (
              <>
                <Viewport ref={vp} bodies={viewBodies} display={display} ortho={ortho} pickMode={pickMode} selected={selection} section={section}
                  onPick={onPick} onContextMenu={(p, at) => { if (p?.face && !selection.has(faceKey(p.bodyKey, p.face.name))) ed().select([faceKey(p.bodyKey, p.face.name)]); setCtx({ at }); }}
                  onCameraChange={(c) => channel.send({ type: "presence", document_id: docId, selection: [...selection].slice(0, 50), camera: c })}>
                  {(showSketches || treeSel) && doc.features.filter((f) => f.type === "sketch" && !f.suppressed && model?.sketches[f.id] && (showSketches || f.id === treeSel))
                    .map((f) => f.type === "sketch" && <SketchOverlay key={f.id} feature={f} state={model!.sketches[f.id]} highlight={f.id === treeSel} />)}
                  {Object.entries(model?.datums ?? {}).map(([id, d]) => <DatumGlyph key={id} d={d} />)}
                  {measure?.point_a && measure.point_b && (
                    <MeasureLine a={measure.point_a} b={measure.point_b} label={`${fmt((measure.distance_mm ?? 0) / LENGTH_UNITS[unit], 6)} ${unit}`} />
                  )}
                </Viewport>
                <div className="vp-toolbar" role="toolbar" aria-label="View tools">
                  {(["face", "edge", "body"] as PickMode[]).map((m) => (
                    <button key={m} className="btn ghost sm" aria-pressed={pickMode === m} onClick={() => setPickMode(m)} title={`Select ${m}s`}>
                      <MousePointerClick size={13} />{m[0].toUpperCase() + m.slice(1)}</button>
                  ))}
                  <span className="sep" style={{ width: 1, background: "var(--border)", margin: "2px 4px" }} />
                  <select className="select" style={{ height: 24, width: 128 }} aria-label="Display mode" value={display} onChange={(e) => setDisplay(e.target.value as DisplayMode)}>
                    <option value="shaded_edges">Shaded + edges</option><option value="shaded">Shaded</option><option value="wireframe">Wireframe</option><option value="xray">X-ray</option>
                  </select>
                  <select className="select" style={{ height: 24, width: 92 }} aria-label="Standard view" value="" onChange={(e) => {
                    const v = e.target.value;
                    if (v.startsWith("bm:")) { const b = bookmarks[Number(v.slice(3))]; if (b) vp.current?.setCamera(b.cam); }
                    else if (v === "save") { const n = window.prompt("Bookmark name", `View ${bookmarks.length + 1}`); const cam = vp.current?.getCamera(); if (n && cam) setBookmarks([...bookmarks, { name: n.slice(0, 40), cam }]); }
                    else if (v) setView(v as NamedView);
                  }}>
                    <option value="">Views…</option>
                    {(["iso", "front", "back", "top", "bottom", "right", "left"] as NamedView[]).map((v) => <option key={v} value={v}>{v}</option>)}
                    {bookmarks.map((b, i) => <option key={i} value={`bm:${i}`}>★ {b.name}</option>)}
                    <option value="save">Save current view…</option>
                  </select>
                  <button className="btn ghost sm" aria-pressed={ortho} onClick={() => setOrtho((o) => !o)} title="Orthographic projection">Ortho</button>
                  <button className="btn ghost sm" aria-pressed={section.enabled} onClick={() => setSection((s) => ({ ...s, enabled: !s.enabled, offset: s.enabled ? s.offset : bbox ? (bbox.min.z + bbox.max.z) / 2 : 0 }))} title="Section view">Section</button>
                  <button className="btn ghost sm" aria-pressed={showSketches} onClick={() => setShowSketches((s) => !s)} title="Show sketches"><PencilRuler size={13} /></button>
                  <button className="btn ghost icon sm" onClick={() => vp.current?.fit()} aria-label="Fit view" title="Fit (F)"><Maximize size={13} /></button>
                  <button className="btn ghost icon sm" aria-label="Save view bookmark" title="Bookmark this view" onClick={() => { const cam = vp.current?.getCamera(); if (cam) setBookmarks([...bookmarks, { name: `View ${bookmarks.length + 1}`, cam }]); toast.info("View bookmarked"); }}><Bookmark size={13} /></button>
                </div>
                {section.enabled && (
                  <div className="card tight" style={{ position: "absolute", top: 48, left: "50%", transform: "translateX(-50%)", display: "flex", gap: 8, alignItems: "center" }}>
                    <span className="small">Section</span>
                    <select className="select" style={{ width: 60, height: 24 }} aria-label="Section axis" value={section.axis} onChange={(e) => {
                      const a = e.target.value as Section["axis"];
                      setSection((s) => ({ ...s, axis: a, offset: bbox ? (bbox.min[a] + bbox.max[a]) / 2 : 0 }));
                    }}><option value="x">X</option><option value="y">Y</option><option value="z">Z</option></select>
                    <input type="range" aria-label="Section offset" style={{ width: 200 }} min={bbox ? bbox.min[section.axis] - 1 : -100} max={bbox ? bbox.max[section.axis] + 1 : 100}
                      step="any" value={section.offset} onChange={(e) => setSection((s) => ({ ...s, offset: Number(e.target.value) }))} />
                    <span className="num small" style={{ width: 70 }}>{fmt(section.offset / LENGTH_UNITS[unit], 4)} {unit}</span>
                    <label className="checkbox small"><input type="checkbox" checked={section.flip} onChange={(e) => setSection((s) => ({ ...s, flip: e.target.checked }))} />Flip</label>
                  </div>
                )}
                <div className="vp-status" aria-live="polite">
                  {live.busy ? <><span className="spinner" style={{ width: 10, height: 10, verticalAlign: -1 }} /> Recomputing…</> : model ? (
                    <>
                      {model.ok ? "Model OK" : `${errors} feature error${errors === 1 ? "" : "s"}`} · kernel {fmt(model.elapsed_ms, 3)} ms
                      {model.timing_ms && <> (tess. {fmt(model.timing_ms.tessellate, 3)} ms)</>} · round-trip {fmt(live.ms ?? 0, 3)} ms
                      {model.cache && <> · cache {model.cache}</>} · {tris.toLocaleString()} tris
                    </>
                  ) : "Waiting for kernel…"}
                  {live.error ? <span style={{ color: "var(--err)" }}> · {errorMessage(live.error)}</span> : null}
                </div>
                {!model?.bodies.length && model && !live.busy && (
                  <div className="empty" style={{ position: "absolute", left: "50%", top: "40%", transform: "translate(-50%,-50%)", background: "rgba(15,18,22,.85)" }}>
                    <strong style={{ color: "var(--text)" }}>No solid bodies yet</strong>
                    <span className="small">Create a sketch (S) on a standard plane, close a profile, then extrude it (E).</span>
                    {!readonly && <button className="btn primary sm" onClick={() => startFeature("sketch")}><PencilRuler size={13} />New sketch</button>}
                  </div>
                )}
              </>
            )}
            {preview && (
              <div className="alert info" style={{ position: "absolute", top: 8, left: 8, right: 8, margin: "0 auto", maxWidth: 680, zIndex: 6 }}>
                <Eye size={16} /><div className="grow">Viewing revision {preview.number} (read-only).</div>
                <button className="btn sm" onClick={() => setPreview(null)}>Back to latest</button>
                {canEdit && <button className="btn primary sm" onClick={() => void doRestore(preview.number)}>Restore this revision</button>}
              </div>
            )}
          </section>
          {panels.bottom && (
            <>
              <Splitter dir="h" label="Resize console" onDrag={(d) => setBottom((h) => Math.min(420, Math.max(60, h - d)))} />
              <section className="wb-bottom" aria-label="Console">
                <div className="pane-head"><span>Operations & diagnostics</span><span className="faint">{model?.kernel}</span></div>
                <div className="pane-body"><Console log={log} model={model} onOpen={(id) => { setTreeSel(id); editFeature(id); }} /></div>
              </section>
            </>
          )}
        </div>

        {panels.right && (
          <>
            <Splitter dir="v" label="Resize inspector" onDrag={(d) => setRight((w) => Math.min(560, Math.max(240, w - d)))} />
            <aside className="wb-right" aria-label={editing ? "Feature properties" : "Inspector"}>
              {editing ? (
                <FeatureEditor feature={editing.feature} isNew={editing.isNew} doc={doc} model={model} bodies={bodies} selection={selection} unit={unit}
                  status={status[editing.feature.id]} readonly={readonly} onChange={(f) => setEditing({ ...editing, feature: f })}
                  onOk={commitEditing} onCancel={() => setEditing(null)} onEditSketch={(id) => { setEditing(null); ed().setSketchEdit(id); }} />
              ) : (
                <Inspector doc={doc} docId={docId} projectId={project.id} model={model} selection={selection} unit={unit} readonly={readonly}
                  canComment={canComment} revision={baseRevision}
                  onSetMaterial={(b, m: MaterialAssignment | null) => ed().apply((d) => { if (m) d.materials[b] = m; else delete d.materials[b]; return d; }, `Material of ${b}: ${m?.name ?? "none"}`)}
                  onMeasure={setMeasure}
                  onFocusAnchor={(a) => {
                    if (a.camera?.position && a.camera?.target) vp.current?.setCamera(a.camera);
                    if (a.face && a.body) ed().select([faceKey(a.body, a.face)]);
                  }}
                  getCamera={() => vp.current?.getCamera() ?? null} />
              )}
            </aside>
          </>
        )}
      </div>

      <ContextMenu at={ctx?.at ?? null} items={ctxItems} onClose={() => setCtx(null)} />
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} />
      <ShortcutsDialog open={shortcuts} onClose={() => setShortcuts(false)} />
      <RevisionsDialog open={revisions} onClose={() => setRevisions(false)} docId={docId} head={baseRevision || head} canEdit={canEdit}
        onPreview={(n) => void doPreview(n)} onRestore={(n) => void doRestore(n)} />
      <ConflictDialog docId={docId} projectId={project.id} name={name} kind="part" onReloaded={() => setEditing(null)} />
      <Dialog open={versionDlg} onClose={() => setVersionDlg(false)} title="Save named version"
        footer={<><button className="btn" onClick={() => setVersionDlg(false)}>Cancel</button>
          <button className="btn primary" onClick={async () => { setVersionDlg(false); await saveNow(versionMsg.trim() || "Named version"); setVersionMsg(""); }}>Save version</button></>}>
        <label className="label" htmlFor="vmsg">Version message</label>
        <input id="vmsg" className="input" value={versionMsg} maxLength={500} placeholder="e.g. Released for prototype review" onChange={(e) => setVersionMsg(e.target.value)}
          onKeyDown={async (e) => { if (e.key === "Enter") { setVersionDlg(false); await saveNow(versionMsg.trim() || "Named version"); setVersionMsg(""); } }} />
        <span className="faint small">Creates an immutable revision even if nothing changed since the last autosave.</span>
      </Dialog>
    </div>
  );
}

function DatumGlyph({ d }: { d: { kind: string; origin: number[]; normal?: number[]; direction?: number[] } }) {
  const geom = useMemo(() => {
    const o = new THREE.Vector3(...d.origin);
    const g = new THREE.BufferGeometry();
    if (d.kind === "axis" && d.direction) {
      const v = new THREE.Vector3(...d.direction).normalize().multiplyScalar(80);
      g.setFromPoints([o.clone().sub(v), o.clone().add(v)]);
    } else if (d.normal) {
      const n = new THREE.Vector3(...d.normal).normalize();
      const u = new THREE.Vector3(1, 0, 0);
      if (Math.abs(n.dot(u)) > 0.9) u.set(0, 1, 0);
      const a = u.clone().cross(n).normalize().multiplyScalar(40), b = n.clone().cross(a).normalize().multiplyScalar(40);
      const c = [o.clone().add(a).add(b), o.clone().sub(a).add(b), o.clone().sub(a).sub(b), o.clone().add(a).sub(b)];
      g.setFromPoints([c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0]]);
    }
    return g;
  }, [d]);
  useEffect(() => () => geom.dispose(), [geom]);
  return <lineSegments geometry={geom}><lineDashedMaterial color="#f2b73f" dashSize={3} gapSize={2} transparent opacity={0.7} /></lineSegments>;
}
