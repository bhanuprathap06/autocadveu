"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Download, Plus, RefreshCw, Save, Trash2, ZoomIn, ZoomOut } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiPath, download, errorMessage, get, put } from "@/api/client";
import type { DrawingContent } from "@/api/types";
import { Dialog, ErrorBox, Field, Loading, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { RequireAuth } from "@/lib/auth";

interface DrawingResp {
  drawing: { id: string; project_id: string; document_id: string; document_name: string; document_kind: string; name: string; revision_pin: number | null; version: number; content: DrawingContent; head_revision: number };
  svg: string; issues: { id: string; severity: string; message: string }[]; warnings: string[]; scale: number; projection: string; model_revision: number; stale: boolean;
  sheet: { width: number; height: number }; views: Record<string, { center: number[]; view_center: number[]; scale: number }>;
}
type Annotation = DrawingContent["annotations"][number];
type Place = { kind: "gdt" | "datum" | "note" | "surface_finish" } | null;

const CHARS = ["straightness", "flatness", "circularity", "cylindricity", "profile_line", "profile_surface", "parallelism", "perpendicularity", "angularity",
  "position", "concentricity", "symmetry", "circular_runout", "total_runout"];
const VIEW_KINDS = ["front", "top", "right", "left", "bottom", "back", "iso"] as const;

function DrawingEditor() {
  const { drawingId } = useParams<{ drawingId: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["drawing", drawingId], queryFn: () => get<DrawingResp>(apiPath("/api/v1/drawings/{drawing_id}", { drawing_id: drawingId })), staleTime: Infinity });
  const [content, setContent] = useState<DrawingContent | null>(null);
  const [version, setVersion] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [place, setPlace] = useState<Place>(null);
  const [tab, setTab] = useState<"sheet" | "views" | "annotations" | "revisions">("sheet");
  const [editAnn, setEditAnn] = useState<Annotation | null>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (q.data && !dirty) { setContent(q.data.drawing.content); setVersion(q.data.drawing.version); } }, [q.data, dirty]);
  // Fit the sheet to the available width the first time it renders.
  const fitted = useRef(false);
  useEffect(() => {
    if (!q.data || fitted.current || !sheetRef.current?.parentElement) return;
    fitted.current = true;
    const w = sheetRef.current.parentElement.clientWidth - 32;
    setZoom(Math.max(0.3, Math.min(2, w / (q.data.sheet.width * 3.2))));
  }, [q.data]);

  const save = async (opts: { unpin?: boolean; pin?: number } = {}) => {
    if (!content) return;
    setSaving(true);
    try {
      const r = await put<{ drawing: DrawingResp["drawing"]; issues: DrawingResp["issues"] }>(apiPath("/api/v1/drawings/{drawing_id}", { drawing_id: drawingId }),
        { content, version, unpin: !!opts.unpin, revision_pin: opts.pin ?? null });
      setVersion(r.drawing.version); setDirty(false);
      await qc.invalidateQueries({ queryKey: ["drawing", drawingId] });
      toast.ok(`Saved drawing v${r.drawing.version}`, r.issues.length ? `${r.issues.length} GD&T issue(s) to resolve` : undefined);
    } catch (e) {
      if (e instanceof ApiError && e.code === "version_conflict") toast.err("Someone else saved this drawing", "Reload to see their version; your edits are still on screen.");
      else toast.err("Save failed", errorMessage(e));
    } finally { setSaving(false); }
  };
  const edit = (fn: (c: DrawingContent) => void) => { setContent((c) => { if (!c) return c; const n = structuredClone(c); fn(n); return n; }); setDirty(true); };

  // Map a click on the sheet to a view and its model coordinates.
  const locate = (ev: React.MouseEvent) => {
    const svg = sheetRef.current?.querySelector("svg");
    if (!svg || !q.data) return null;
    const r = svg.getBoundingClientRect();
    const sx = ((ev.clientX - r.left) / r.width) * q.data.sheet.width;
    const sy = q.data.sheet.height - ((ev.clientY - r.top) / r.height) * q.data.sheet.height;
    let best: { id: string; d: number } | null = null;
    for (const [id, v] of Object.entries(q.data.views)) {
      const d = Math.hypot(sx - v.center[0], sy - v.center[1]);
      if (!best || d < best.d) best = { id, d };
    }
    if (!best) return null;
    const v = q.data.views[best.id];
    return { view: best.id, at: [v.view_center[0] + (sx - v.center[0]) / v.scale, v.view_center[1] + (sy - v.center[1]) / v.scale] as [number, number], sheet: [sx, sy] as [number, number] };
  };

  const nextId = (p: string) => { const ids = new Set((content?.annotations ?? []).map((a) => a.id)); let i = 1; while (ids.has(`${p}${i}`)) i++; return `${p}${i}`; };
  const issues = q.data?.issues ?? [];
  const errors = issues.filter((i) => i.severity === "error");
  const svgHtml = useMemo(() => q.data?.svg ?? "", [q.data?.svg]);

  if (q.isLoading || !content) return <div style={{ height: "100vh", display: "grid", placeItems: "center" }}>{q.error ? <ErrorBox error={q.error} /> : <Loading label="Projecting views (hidden-line removal)…" />}</div>;
  const d = q.data!;
  return (
    <div className="wb">
      <header className="wb-top">
        <Link href={`/app/projects/${d.drawing.project_id}?tab=drawings`} className="btn ghost sm" aria-label="Back to project"><ArrowLeft size={14} /></Link>
        <strong className="small">{d.drawing.name}</strong>
        <span className="badge">v{version}</span>
        <span className="small muted">of <Link href={`/app/workbench/${d.drawing.document_id}`}>{d.drawing.document_name}</Link> r{d.model_revision}</span>
        {d.stale && <span className="badge warn" title="The drawing is pinned to an older model revision"><AlertTriangle size={11} />Model changed (head r{d.drawing.head_revision})</span>}
        <span className="sep" />
        <button className="btn primary sm" disabled={!dirty || saving} onClick={() => save()}><Save size={13} />{saving ? "Saving…" : dirty ? "Save & regenerate" : "Saved"}</button>
        {d.stale && <button className="btn sm" onClick={() => save({ pin: d.drawing.head_revision })}><RefreshCw size={13} />Update to r{d.drawing.head_revision}</button>}
        <div className="grow" />
        <button className="btn ghost icon sm" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.3, z / 1.25))}><ZoomOut size={14} /></button>
        <span className="num small">{Math.round(zoom * 100)}%</span>
        <button className="btn ghost icon sm" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(4, z * 1.25))}><ZoomIn size={14} /></button>
        <span className="sep" />
        {(["pdf", "dxf", "svg"] as const).map((f) => (
          <button key={f} className="btn sm" disabled={errors.length > 0 || dirty} title={errors.length ? "Fix GD&T errors before exporting" : dirty ? "Save first" : `Export ${f.toUpperCase()}`}
            onClick={async () => { try { await download("GET", apiPath("/api/v1/drawings/{drawing_id}/export", { drawing_id: drawingId }, { format: f })); } catch (e) { toast.err("Export failed", errorMessage(e)); } }}>
            <Download size={13} />{f.toUpperCase()}</button>
        ))}
      </header>
      <div className="wb-body" style={{ gridTemplateColumns: "minmax(0,1fr) 360px" }}>
        <section className="wb-center" aria-label="Drawing sheet">
          <div className="sheet-wrap" onClick={(ev) => {
            if (!place) return;
            const loc = locate(ev);
            if (!loc) return;
            if (place.kind === "gdt") edit((c) => { c.annotations.push({ kind: "gdt", id: nextId("fcf"), view: loc.view, at: loc.at, characteristic: "position", tolerance: 0.1, diameter_zone: true, modifier: null, datums: [{ letter: "A", modifier: null }], standard: null, target_face: null }); });
            if (place.kind === "datum") edit((c) => { const used = new Set(c.annotations.filter((a) => a.kind === "datum").map((a) => (a as { letter: string }).letter)); const L = "ABCDEFGHJKLMN".split("").find((x) => !used.has(x)) ?? "P"; c.annotations.push({ kind: "datum", id: nextId("dat"), view: loc.view, at: loc.at, letter: L, target_face: null }); });
            if (place.kind === "note") edit((c) => { c.annotations.push({ kind: "note", id: nextId("note"), text: "NOTE", position: loc.sheet, view: null, at: null }); });
            if (place.kind === "surface_finish") edit((c) => { c.annotations.push({ kind: "surface_finish", id: nextId("sf"), view: loc.view, at: loc.at, ra_um: 1.6, process: "removal_required", standard: null }); });
            setPlace(null);
            setTab("annotations");
          }} style={{ cursor: place ? "crosshair" : "default" }}>
            <div ref={sheetRef} style={{ width: `${d.sheet.width * 3.2 * zoom}px`, margin: "0 auto" }}
              // The SVG is produced by the server-side renderer from typed primitives with escaped text; it contains no scripts.
              dangerouslySetInnerHTML={{ __html: svgHtml.replace(/width="[\d.]+mm" height="[\d.]+mm"/, 'width="100%"') }} />
          </div>
          {place && <div className="vp-status" style={{ left: "50%", transform: "translateX(-50%)" }}>Click on the sheet near a view to place the {place.kind.replace("_", " ")} · <button className="btn ghost sm" onClick={() => setPlace(null)}>Cancel</button></div>}
          {dirty && <div className="vp-status" style={{ left: 8, bottom: 40, color: "var(--warn)" }}>Unsaved edits — the sheet regenerates on save.</div>}
        </section>
        <aside className="wb-right" aria-label="Drawing properties">
          <Tabs label="Drawing properties" value={tab} onChange={setTab} tabs={[{ key: "sheet", label: "Sheet" }, { key: "views", label: "Views & dims" },
            { key: "annotations", label: "GD&T & notes", badge: errors.length ? <span className="badge err">{errors.length}</span> : null }, { key: "revisions", label: "Revisions" }]} />
          <div className="pane-body col gap-12">
            {(issues.length > 0 || d.warnings.length > 0) && (
              <div className="col gap-4">
                {issues.map((i, k) => <div key={k} className={`alert ${i.severity === "error" ? "err" : "warn"} small`}><strong>{i.id}</strong>&nbsp;{i.message}</div>)}
                {d.warnings.map((w, k) => <div key={`w${k}`} className="alert warn small">{w}</div>)}
              </div>
            )}
            {tab === "sheet" && (
              <>
                <div className="grid grid-2">
                  <Field label="Sheet size"><select className="select" value={content.sheet.size} onChange={(e) => edit((c) => { c.sheet.size = e.target.value as typeof c.sheet.size; })}>{["A4", "A3", "A2", "A1", "A0"].map((s) => <option key={s}>{s}</option>)}</select></Field>
                  <Field label="Orientation"><select className="select" value={content.sheet.orientation} onChange={(e) => edit((c) => { c.sheet.orientation = e.target.value as "landscape" | "portrait"; })}><option>landscape</option><option>portrait</option></select></Field>
                  <Field label="Standard"><select className="select" value={content.sheet.standard} onChange={(e) => edit((c) => { c.sheet.standard = e.target.value as "ISO" | "ASME"; })}><option value="ISO">ISO (1101, 128, 129)</option><option value="ASME">ASME Y14.5-2018</option></select></Field>
                  <Field label="Projection"><select className="select" value={content.sheet.projection ?? ""} onChange={(e) => edit((c) => { c.sheet.projection = (e.target.value || null) as typeof c.sheet.projection; })}>
                    <option value="">Default ({content.sheet.standard === "ASME" ? "third" : "first"} angle)</option><option value="first_angle">First angle</option><option value="third_angle">Third angle</option></select></Field>
                  <Field label="Scale" hint={`Current ${d.scale >= 1 ? `${d.scale}:1` : `1:${Math.round(1 / d.scale)}`}`}><select className="select" value={content.sheet.scale ?? ""} onChange={(e) => edit((c) => { c.sheet.scale = e.target.value ? Number(e.target.value) : null; })}>
                    <option value="">Automatic</option>{[10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01].map((s) => <option key={s} value={s}>{s >= 1 ? `${s}:1` : `1:${Math.round(1 / s)}`}</option>)}</select></Field>
                  <Field label="General tolerance"><input className="input" value={content.sheet.general_tolerance} maxLength={80} onChange={(e) => edit((c) => { c.sheet.general_tolerance = e.target.value; })} /></Field>
                </div>
                <h4>Title block</h4>
                <div className="grid grid-2">
                  {(Object.keys(content.title_block) as (keyof DrawingContent["title_block"])[]).map((k) => (
                    <Field key={k} label={k.replace(/_/g, " ")}><input className="input" value={content.title_block[k]} onChange={(e) => edit((c) => { c.title_block[k] = e.target.value; })} /></Field>
                  ))}
                </div>
                <label className="checkbox"><input type="checkbox" checked={content.show_bom} onChange={(e) => edit((c) => { c.show_bom = e.target.checked; })} />Bill of materials and balloons (assemblies)</label>
                {d.drawing.revision_pin ? <button className="btn sm" onClick={() => save({ unpin: true })}>Unpin (follow latest model revision)</button>
                  : <button className="btn sm" onClick={() => save({ pin: d.drawing.head_revision })}>Pin to model revision r{d.drawing.head_revision}</button>}
              </>
            )}
            {tab === "views" && (
              <>
                {content.views.map((v, i) => (
                  <div key={v.id} className="row">
                    <code>{v.id}</code>
                    <select className="select" aria-label={`View ${v.id} direction`} value={v.kind} onChange={(e) => edit((c) => { c.views[i].kind = e.target.value as typeof v.kind; })}>
                      {VIEW_KINDS.map((k) => <option key={k} disabled={k !== v.kind && content.views.some((x) => x.kind === k)}>{k}</option>)}
                    </select>
                    <label className="checkbox small"><input type="checkbox" checked={v.show_hidden} onChange={(e) => edit((c) => { c.views[i].show_hidden = e.target.checked; })} />Hidden lines</label>
                    <button className="btn ghost icon sm" aria-label={`Remove view ${v.id}`} disabled={content.views.length <= 1} onClick={() => edit((c) => { c.views.splice(i, 1); c.dimensions = c.dimensions.filter((x) => x.view !== v.id); c.annotations = c.annotations.filter((a) => !("view" in a) || a.view !== v.id); })}><Trash2 size={12} /></button>
                  </div>
                ))}
                <button className="btn sm" disabled={content.views.length >= VIEW_KINDS.length} onClick={() => edit((c) => {
                  const k = VIEW_KINDS.find((x) => !c.views.some((v) => v.kind === x))!; let n = 1; while (c.views.some((v) => v.id === `v${n}`)) n++;
                  c.views.push({ id: `v${n}`, kind: k, show_hidden: k !== "iso", offset: [0, 0] });
                })}><Plus size={13} />Add view</button>
                <label className="checkbox"><input type="checkbox" checked={content.auto_dimensions} onChange={(e) => edit((c) => { c.auto_dimensions = e.target.checked; })} />Automatic overall dimensions and hole callouts</label>
                <div className="faint small">Dimensions are measured from the projected model geometry, so they update when the model changes. Add toleranced dimensions through the API or by editing below.</div>
                {content.dimensions.map((dm, i) => (
                  <div key={dm.id} className="card tight small row">
                    <span className="grow">{dm.id}: {dm.kind} in {dm.view}{dm.tolerance ? ` · ${dm.tolerance.kind}` : ""}</span>
                    <button className="btn ghost icon sm" aria-label={`Remove dimension ${dm.id}`} onClick={() => edit((c) => { c.dimensions.splice(i, 1); })}><Trash2 size={12} /></button>
                  </div>
                ))}
              </>
            )}
            {tab === "annotations" && (
              <>
                <div className="row wrap">
                  <button className="btn sm" aria-pressed={place?.kind === "gdt"} onClick={() => setPlace({ kind: "gdt" })}>⌖ Feature control frame</button>
                  <button className="btn sm" aria-pressed={place?.kind === "datum"} onClick={() => setPlace({ kind: "datum" })}>Datum</button>
                  <button className="btn sm" aria-pressed={place?.kind === "surface_finish"} onClick={() => setPlace({ kind: "surface_finish" })}>√ Surface finish</button>
                  <button className="btn sm" aria-pressed={place?.kind === "note"} onClick={() => setPlace({ kind: "note" })}>Note</button>
                </div>
                {content.annotations.map((a, i) => {
                  const bad = issues.filter((x) => x.id === a.id || (a.kind === "datum" && x.id === (a as { letter: string }).letter));
                  return (
                    <div key={a.id} className="card tight small" style={{ borderColor: bad.some((b) => b.severity === "error") ? "var(--err)" : undefined }}>
                      <div className="row">
                        <strong className="grow">{a.kind === "gdt" ? `${a.characteristic.replace(/_/g, " ")} ${a.diameter_zone ? "⌀" : ""}${a.tolerance}${a.modifier ? ` (${a.modifier})` : ""} | ${a.datums.map((x) => x.letter).join("|")}`
                          : a.kind === "datum" ? `Datum ${a.letter}` : a.kind === "note" ? `Note: ${a.text.slice(0, 40)}` : `Ra ${a.ra_um} µm`}</strong>
                        <button className="btn ghost sm" onClick={() => setEditAnn(structuredClone(a))}>Edit</button>
                        <button className="btn ghost icon sm" aria-label={`Delete ${a.id}`} onClick={() => edit((c) => { c.annotations.splice(i, 1); })}><Trash2 size={12} /></button>
                      </div>
                      {bad.map((b, k) => <div key={k} style={{ color: b.severity === "error" ? "var(--err)" : "var(--warn)" }}>{b.message}</div>)}
                    </div>
                  );
                })}
                {!content.annotations.length && <div className="faint small">No annotations. Define datum features first, then reference them in feature control frames.</div>}
                <div className="faint tiny">Checks follow {content.sheet.standard === "ASME" ? "ASME Y14.5-2018" : "ISO 1101:2017"} structural rules (datum requirements, modifiers, zone symbols). They do not judge whether a tolerance is functionally adequate.</div>
              </>
            )}
            {tab === "revisions" && (
              <>
                {content.revisions.map((r, i) => (
                  <div key={i} className="row small">
                    <input className="input" style={{ width: 50 }} aria-label="Revision" value={r.rev} onChange={(e) => edit((c) => { c.revisions[i].rev = e.target.value; })} />
                    <input className="input" style={{ width: 96 }} aria-label="Date" value={r.date} onChange={(e) => edit((c) => { c.revisions[i].date = e.target.value; })} />
                    <input className="input" aria-label="Description" value={r.description} onChange={(e) => edit((c) => { c.revisions[i].description = e.target.value; })} />
                    <input className="input" style={{ width: 70 }} aria-label="By" value={r.by} onChange={(e) => edit((c) => { c.revisions[i].by = e.target.value; })} />
                    <button className="btn ghost icon sm" aria-label="Remove revision row" onClick={() => edit((c) => { c.revisions.splice(i, 1); })}><Trash2 size={12} /></button>
                  </div>
                ))}
                <button className="btn sm" onClick={() => edit((c) => { c.revisions.push({ rev: String.fromCharCode(65 + c.revisions.length), date: new Date().toISOString().slice(0, 10), description: "", by: "" }); })}><Plus size={13} />Add revision row</button>
              </>
            )}
          </div>
        </aside>
      </div>
      <AnnotationDialog ann={editAnn} letters={content.annotations.filter((a) => a.kind === "datum").map((a) => (a as { letter: string }).letter)} onClose={() => setEditAnn(null)}
        onSave={(a) => { edit((c) => { const i = c.annotations.findIndex((x) => x.id === a.id); if (i >= 0) c.annotations[i] = a; }); setEditAnn(null); }} />
    </div>
  );
}

function AnnotationDialog({ ann, letters, onClose, onSave }: { ann: Annotation | null; letters: string[]; onClose: () => void; onSave: (a: Annotation) => void }) {
  const [a, setA] = useState<Annotation | null>(ann);
  useEffect(() => setA(ann), [ann]);
  if (!a) return null;
  return (
    <Dialog open={!!ann} onClose={onClose} title="Edit annotation" footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => onSave(a)}>Apply</button></>}>
      {a.kind === "gdt" && (
        <>
          <Field label="Characteristic"><select className="select" value={a.characteristic} onChange={(e) => setA({ ...a, characteristic: e.target.value })}>{CHARS.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select></Field>
          <div className="grid grid-3">
            <Field label="Tolerance (mm)"><input className="input num" type="number" step="any" value={a.tolerance} onChange={(e) => setA({ ...a, tolerance: Number(e.target.value) })} /></Field>
            <Field label="Material condition"><select className="select" value={a.modifier ?? ""} onChange={(e) => setA({ ...a, modifier: (e.target.value || null) as "M" | "L" | null })}><option value="">RFS (none)</option><option value="M">MMC (M)</option><option value="L">LMC (L)</option></select></Field>
            <label className="checkbox" style={{ alignSelf: "end" }}><input type="checkbox" checked={a.diameter_zone} onChange={(e) => setA({ ...a, diameter_zone: e.target.checked })} />⌀ zone</label>
          </div>
          <div className="field"><span className="label">Datum references (primary, secondary, tertiary)</span>
            <div className="row">{[0, 1, 2].map((i) => (
              <select key={i} className="select" aria-label={`Datum ${i + 1}`} value={a.datums[i]?.letter ?? ""} onChange={(e) => {
                const ds = [...a.datums]; if (e.target.value) ds[i] = { letter: e.target.value, modifier: null }; else ds.splice(i);
                setA({ ...a, datums: ds.filter(Boolean) });
              }}>
                <option value="">—</option>{[...new Set([...letters, ...a.datums.map((x) => x.letter)])].map((l) => <option key={l}>{l}</option>)}
              </select>
            ))}</div>
          </div>
        </>
      )}
      {a.kind === "datum" && <Field label="Datum letter"><input className="input" value={a.letter} maxLength={2} onChange={(e) => setA({ ...a, letter: e.target.value.toUpperCase() })} /></Field>}
      {a.kind === "note" && <Field label="Text"><textarea className="textarea" value={a.text} maxLength={2000} onChange={(e) => setA({ ...a, text: e.target.value })} /></Field>}
      {a.kind === "surface_finish" && (
        <div className="grid grid-2">
          <Field label="Ra (µm)"><input className="input num" type="number" step="any" value={a.ra_um} onChange={(e) => setA({ ...a, ra_um: Number(e.target.value) })} /></Field>
          <Field label="Process"><select className="select" value={a.process} onChange={(e) => setA({ ...a, process: e.target.value as typeof a.process })}><option value="any">Any process</option><option value="removal_required">Material removal required</option><option value="removal_prohibited">Removal prohibited</option></select></Field>
        </div>
      )}
    </Dialog>
  );
}

export default function Page() { return <RequireAuth><DrawingEditor /></RequireAuth>; }
