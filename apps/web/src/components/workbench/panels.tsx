"use client";

import {
  AlertTriangle, ArrowDown, ArrowUp, Axis3d, Box, CheckCircle2, Circle, CircleX, Combine, Copy, Cylinder, Eye, EyeOff, FileInput, FlipHorizontal,
  Grid2x2, Layers, Move3d, PencilRuler, Plus, RotateCcw, RotateCw, Spline, SquareDashed, Trash2, Undo2, Waypoints,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Feature, FeatureType, PartDocument, PartModel, Parameter } from "@/api/types";
import { Confirm, ContextMenu, type MenuItem } from "@/components/ui";
import { fmt } from "@/lib/mesh";
import { FEATURE_LABEL, dependentsOf } from "./defaults";
import type { LogLine } from "./store";

export const FEATURE_ICON: Record<FeatureType, ReactNode> = {
  sketch: <PencilRuler size={14} />, extrude: <Box size={14} />, revolve: <RotateCw size={14} />, sweep: <Spline size={14} />,
  loft: <Layers size={14} />, hole: <Circle size={14} />, fillet: <Waypoints size={14} />, chamfer: <SquareDashed size={14} />,
  shell: <Box size={14} strokeDasharray="2 2" />, boolean: <Combine size={14} />, linear_pattern: <Grid2x2 size={14} />,
  circular_pattern: <RotateCcw size={14} />, mirror: <FlipHorizontal size={14} />, datum_plane: <Layers size={14} />,
  datum_axis: <Axis3d size={14} />, primitive: <Cylinder size={14} />, transform: <Move3d size={14} />, import: <FileInput size={14} />,
};

export function FeatureTree({ doc, model, rollback, selected, readonly, onSelect, onEdit, onEditSketch, onRename, onToggleSuppress,
  onRollback, onMove, onDelete, onDuplicate }: {
  doc: PartDocument; model: PartModel | null; rollback: string | null; selected: string | null; readonly: boolean;
  onSelect: (id: string) => void; onEdit: (id: string) => void; onEditSketch: (id: string) => void; onRename: (id: string, name: string) => void;
  onToggleSuppress: (id: string) => void; onRollback: (id: string | null) => void; onMove: (id: string, dir: -1 | 1) => void;
  onDelete: (id: string) => void; onDuplicate: (id: string) => void;
}) {
  const [menu, setMenu] = useState<{ at: { x: number; y: number }; id: string } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<{ id: string; deps: string[] } | null>(null);
  const status = Object.fromEntries((model?.features ?? []).map((f) => [f.id, f]));
  const rbIndex = rollback ? doc.features.findIndex((f) => f.id === rollback) : -1;

  const items = (f: Feature, i: number): MenuItem[] => [
    { label: "Edit feature", onSelect: () => onEdit(f.id), disabled: readonly, hint: "Enter" },
    ...(f.type === "sketch" ? [{ label: "Edit sketch geometry", onSelect: () => onEditSketch(f.id), disabled: readonly, icon: <PencilRuler size={13} /> }] : []),
    { label: "Rename", onSelect: () => setRenaming(f.id), disabled: readonly, hint: "F2" },
    { label: f.suppressed ? "Unsuppress" : "Suppress", onSelect: () => onToggleSuppress(f.id), disabled: readonly, icon: f.suppressed ? <Eye size={13} /> : <EyeOff size={13} /> },
    { label: "Duplicate", onSelect: () => onDuplicate(f.id), disabled: readonly, icon: <Copy size={13} /> },
    { separator: true, label: "" },
    { label: "Roll back to here", onSelect: () => onRollback(f.id), icon: <Undo2 size={13} /> },
    ...(rollback ? [{ label: "Roll to end", onSelect: () => onRollback(null) }] : []),
    { separator: true, label: "" },
    { label: "Move up", onSelect: () => onMove(f.id, -1), disabled: readonly || i === 0, icon: <ArrowUp size={13} /> },
    { label: "Move down", onSelect: () => onMove(f.id, 1), disabled: readonly || i === doc.features.length - 1, icon: <ArrowDown size={13} /> },
    { separator: true, label: "" },
    { label: "Delete", onSelect: () => setConfirmDel({ id: f.id, deps: dependentsOf(doc, f.id) }), disabled: readonly, icon: <Trash2 size={13} />, hint: "Del" },
  ];

  return (
    <>
      <ul className="tree" role="tree" aria-label="Feature history">
        <li role="treeitem" aria-selected={false}>
          <div className="node muted" style={{ cursor: "default" }}><span className="ico"><Axis3d size={14} /></span>Origin (XY, XZ, YZ planes · X, Y, Z axes)</div>
        </li>
        {doc.features.map((f, i) => {
          const st = status[f.id];
          const afterRb = rbIndex >= 0 && i > rbIndex;
          const cls = ["node", f.suppressed ? "suppressed" : "", st?.status === "error" ? "error" : ""].join(" ");
          return (
            <li key={f.id} role="treeitem" aria-selected={selected === f.id} style={{ opacity: afterRb ? 0.4 : 1 }}>
              <div className={cls} aria-selected={selected === f.id} tabIndex={0} title={st?.message || `${FEATURE_LABEL[f.type]} · ${f.id}`}
                onClick={() => onSelect(f.id)}
                onDoubleClick={() => (f.type === "sketch" ? onEditSketch(f.id) : onEdit(f.id))}
                onContextMenu={(e) => { e.preventDefault(); onSelect(f.id); setMenu({ at: { x: e.clientX, y: e.clientY }, id: f.id }); }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (f.type === "sketch" ? onEditSketch(f.id) : onEdit(f.id));
                  if (e.key === "F2") setRenaming(f.id);
                  if (e.key === "Delete" && !readonly) setConfirmDel({ id: f.id, deps: dependentsOf(doc, f.id) });
                  if (e.key === "ArrowDown") (e.currentTarget.parentElement?.nextElementSibling?.querySelector(".node") as HTMLElement | null)?.focus();
                  if (e.key === "ArrowUp") (e.currentTarget.parentElement?.previousElementSibling?.querySelector(".node") as HTMLElement | null)?.focus();
                  if (e.key === "ContextMenu") { const r = e.currentTarget.getBoundingClientRect(); setMenu({ at: { x: r.left + 40, y: r.bottom }, id: f.id }); }
                }}>
                <span className="ico">{FEATURE_ICON[f.type]}</span>
                {renaming === f.id ? (
                  <input autoFocus className="input" style={{ height: 22 }} defaultValue={f.name} aria-label="Feature name" maxLength={128}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") { onRename(f.id, (e.target as HTMLInputElement).value); setRenaming(null); } if (e.key === "Escape") setRenaming(null); }}
                    onBlur={(e) => { onRename(f.id, e.target.value); setRenaming(null); }} />
                ) : <span className="grow ellipsis">{f.name || `${FEATURE_LABEL[f.type]} ${f.id}`}</span>}
                {st?.status === "error" && <CircleX size={13} color="var(--err)" aria-label="Error" />}
                {st?.status === "warning" && <AlertTriangle size={13} color="var(--warn)" aria-label="Warning" />}
                {st?.status === "ok" && st.cached === false && <CheckCircle2 size={12} color="var(--text-faint)" aria-hidden="true" />}
                {f.suppressed && <EyeOff size={12} aria-label="Suppressed" />}
              </div>
              {rollback === f.id && (
                <div className="row tiny" style={{ padding: "2px 8px", color: "var(--warn)", borderTop: "2px solid var(--warn)" }}>
                  Rollback bar — later features are not computed
                  <button className="btn ghost sm" onClick={() => onRollback(null)}>Roll to end</button>
                </div>
              )}
            </li>
          );
        })}
        {!doc.features.length && <li className="faint small" style={{ padding: 8 }}>No features yet. Start with a sketch or a primitive.</li>}
      </ul>
      <ContextMenu at={menu?.at ?? null} items={menu ? items(doc.features.find((f) => f.id === menu.id)!, doc.features.findIndex((f) => f.id === menu.id)) : []}
        onClose={() => setMenu(null)} />
      <Confirm open={!!confirmDel} title="Delete feature" danger confirmLabel="Delete"
        message={confirmDel && (
          <div className="col">
            <span>Delete <strong>{doc.features.find((f) => f.id === confirmDel.id)?.name || confirmDel.id}</strong>?</span>
            {confirmDel.deps.length > 0 && <div className="alert warn small">These features reference it and will fail until edited: {confirmDel.deps.join(", ")}</div>}
            <span className="small muted">You can undo this (Ctrl+Z) or restore an earlier revision.</span>
          </div>
        )}
        onConfirm={() => confirmDel && onDelete(confirmDel.id)} onClose={() => setConfirmDel(null)} />
    </>
  );
}

export function ParametersPanel({ doc, model, readonly, onChange }: {
  doc: PartDocument; model: PartModel | null; readonly: boolean; onChange: (params: Parameter[], label: string) => void;
}) {
  const [draft, setDraft] = useState({ name: "", expression: "", kind: "length" as Parameter["kind"] });
  const [err, setErr] = useState<string | null>(null);
  const params = doc.parameters;
  const used = (name: string) => {
    const re = new RegExp(`\\b${name}\\b`);
    const strs: string[] = [];
    const walk = (v: unknown) => { if (typeof v === "string") strs.push(v); else if (Array.isArray(v)) v.forEach(walk); else if (v && typeof v === "object") Object.values(v).forEach(walk); };
    walk(doc.features.map((f) => f.params));
    walk(params.filter((p) => p.name !== name).map((p) => p.expression));
    return strs.some((s) => re.test(s));
  };
  const add = () => {
    const n = draft.name.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(n)) { setErr("Names start with a letter or _ and contain only letters, digits and _"); return; }
    if (params.some((p) => p.name === n)) { setErr("A parameter with that name exists"); return; }
    if (!draft.expression.trim()) { setErr("Enter an expression, e.g. 120 or width/2"); return; }
    onChange([...params, { name: n, expression: draft.expression.trim(), kind: draft.kind, description: "" }], `Add parameter ${n}`);
    setDraft({ name: "", expression: "", kind: "length" });
    setErr(null);
  };
  return (
    <div className="col gap-4">
      <table className="table">
        <thead><tr><th>Name</th><th>Expression</th><th className="num">Value</th><th aria-label="Actions" /></tr></thead>
        <tbody>
          {params.map((p, i) => {
            const e = model?.parameter_errors?.[p.name];
            const v = model?.parameters?.[p.name];
            return (
              <tr key={p.name}>
                <td className="mono" title={p.description || p.kind}>{p.name}</td>
                <td>
                  <input className="input mono" style={{ height: 24 }} defaultValue={p.expression} key={p.expression} disabled={readonly} aria-label={`Expression for ${p.name}`}
                    aria-invalid={!!e || undefined} title={e ?? undefined}
                    onKeyDown={(ev) => { if (ev.key === "Enter") (ev.target as HTMLInputElement).blur(); }}
                    onBlur={(ev) => {
                      const x = ev.target.value.trim();
                      if (x && x !== p.expression) { const next = [...params]; next[i] = { ...p, expression: x }; onChange(next, `Set ${p.name} = ${x}`); }
                    }} />
                  {e && <div className="tiny" style={{ color: "var(--err)" }}>{e}</div>}
                </td>
                <td className="num">{v === undefined ? "–" : `${fmt(v, 6)}${p.kind === "angle" ? "°" : ""}`}</td>
                <td>
                  <button className="btn ghost icon sm" aria-label={`Delete ${p.name}`} disabled={readonly}
                    title={used(p.name) ? "Referenced by features or parameters" : "Delete"}
                    onClick={() => {
                      if (used(p.name) && !window.confirm(`${p.name} is referenced. Deleting it will make those expressions fail. Delete anyway?`)) return;
                      onChange(params.filter((x) => x.name !== p.name), `Delete parameter ${p.name}`);
                    }}><Trash2 size={12} /></button>
                </td>
              </tr>
            );
          })}
          {!params.length && <tr><td colSpan={4} className="faint">No parameters. Add named values to drive dimensions (e.g. <code>width = 120</code>).</td></tr>}
        </tbody>
      </table>
      {!readonly && (
        <form className="col gap-4" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <div className="row">
            <input className="input mono" placeholder="name" aria-label="New parameter name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <input className="input mono" placeholder="expression" aria-label="New parameter expression" value={draft.expression} onChange={(e) => setDraft({ ...draft, expression: e.target.value })} />
          </div>
          <div className="row">
            <select className="select" aria-label="Parameter kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Parameter["kind"] })}>
              <option value="length">Length (mm)</option><option value="angle">Angle (deg)</option><option value="count">Count</option><option value="unitless">Unitless</option>
            </select>
            <button className="btn sm"><Plus size={13} />Add</button>
          </div>
          {err && <span className="tiny" style={{ color: "var(--err)" }}>{err}</span>}
        </form>
      )}
    </div>
  );
}

export function Console({ log, model, onOpen }: { log: LogLine[]; model: PartModel | null; onOpen: (id: string) => void }) {
  const issues = (model?.features ?? []).filter((f) => f.status === "error" || f.status === "warning" || f.warnings.length);
  return (
    <div className="console">
      {issues.map((f) => (
        <div key={f.id} className="line" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={() => onOpen(f.id)} onKeyDown={(e) => e.key === "Enter" && onOpen(f.id)}>
          <span className="t">model</span>
          <span className={f.status === "error" ? "err" : "warn"}>{f.id} [{f.type}] {f.status === "error" ? `${f.code}: ${f.message}` : f.warnings.join("; ") || f.message}</span>
        </div>
      ))}
      {[...log].reverse().map((l, i) => (
        <div key={i} className="line">
          <span className="t">{new Date(l.t).toLocaleTimeString()}</span>
          <span className={l.level === "info" ? "" : l.level}>{l.text}</span>
        </div>
      ))}
      {!log.length && !issues.length && <div className="faint">Operation log is empty.</div>}
    </div>
  );
}
