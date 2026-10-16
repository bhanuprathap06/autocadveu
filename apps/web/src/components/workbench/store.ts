"use client";

import { create } from "zustand";
import type { AssemblyDocument, Feature, PartDocument } from "@/api/types";

export type EditorDoc = PartDocument | AssemblyDocument;

export type SaveState = "saved" | "dirty" | "saving" | "offline" | "conflict" | "error" | "readonly";
export interface LogLine { t: number; level: "info" | "ok" | "warn" | "err"; text: string }

interface EditorState {
  docId: string | null;
  doc: EditorDoc | null;
  baseRevision: number;
  past: EditorDoc[];
  future: EditorDoc[];
  save: SaveState;
  saveError: string | null;
  selection: Set<string>;
  dialog: { type: string; featureId?: string } | null;
  sketchEdit: string | null; // feature id of sketch being edited
  rollback: string | null;
  opLabels: string[];
  log: LogLine[];
  init: (docId: string, doc: EditorDoc, base: number, readonly?: boolean) => void;
  apply: <T extends EditorDoc = PartDocument>(fn: (d: T) => T, label?: string) => void;
  undo: () => void;
  redo: () => void;
  setSaved: (rev: number) => void;
  setSave: (s: SaveState, err?: string | null) => void;
  select: (keys: string[], additive?: boolean) => void;
  clearSelection: () => void;
  openDialog: (type: string, featureId?: string) => void;
  closeDialog: () => void;
  setSketchEdit: (id: string | null) => void;
  setRollback: (id: string | null) => void;
  addLog: (level: LogLine["level"], text: string) => void;
}

const clone = <T,>(x: T): T => structuredClone(x);

export const useEditor = create<EditorState>((set, get) => ({
  docId: null,
  doc: null,
  baseRevision: 0,
  past: [],
  future: [],
  save: "saved",
  saveError: null,
  selection: new Set(),
  dialog: null,
  sketchEdit: null,
  rollback: null,
  opLabels: [],
  log: [],
  init: (docId, doc, base, readonly) =>
    set({ docId, doc: clone(doc), baseRevision: base, past: [], future: [], save: readonly ? "readonly" : "saved", saveError: null,
      selection: new Set(), dialog: null, sketchEdit: null, rollback: null, opLabels: [] }),
  apply: (fn, label) => {
    const { doc, past, save, opLabels } = get();
    if (!doc || save === "readonly") return;
    const next = (fn as unknown as (d: EditorDoc) => EditorDoc)(clone(doc));
    set({ doc: next, past: [...past.slice(-99), doc], future: [], save: save === "conflict" ? "conflict" : "dirty",
      opLabels: label ? [...opLabels, label] : opLabels });
    if (label) get().addLog("info", label);
  },
  undo: () => {
    const { doc, past, future, save } = get();
    if (!doc || !past.length || save === "readonly") return;
    set((st) => ({ doc: past[past.length - 1], past: past.slice(0, -1), future: [doc, ...future], save: st.save === "conflict" ? "conflict" : "dirty",
      opLabels: [...st.opLabels, "Undo"] }));
    get().addLog("info", "Undo");
  },
  redo: () => {
    const { doc, past, future, save } = get();
    if (!doc || !future.length || save === "readonly") return;
    set((st) => ({ doc: future[0], past: [...past, doc], future: future.slice(1), save: st.save === "conflict" ? "conflict" : "dirty",
      opLabels: [...st.opLabels, "Redo"] }));
    get().addLog("info", "Redo");
  },
  setSaved: (rev) => set({ baseRevision: rev, save: "saved", saveError: null }),
  setSave: (s, err = null) => set({ save: s, saveError: err }),
  select: (keys, additive) => set((st) => {
    const n = new Set(additive ? st.selection : []);
    for (const k of keys) { if (additive && n.has(k)) n.delete(k); else n.add(k); }
    return { selection: n };
  }),
  clearSelection: () => set({ selection: new Set() }),
  openDialog: (type, featureId) => set({ dialog: { type, featureId } }),
  closeDialog: () => set({ dialog: null }),
  setSketchEdit: (id) => set({ sketchEdit: id }),
  setRollback: (id) => set({ rollback: id }),
  addLog: (level, text) => set((st) => ({ log: [...st.log.slice(-300), { t: Date.now(), level, text }] })),
}));

export function nextFeatureId(doc: PartDocument, prefix = "f"): string {
  const ids = new Set(doc.features.map((f) => f.id));
  let i = doc.features.length + 1;
  while (ids.has(`${prefix}${i}`)) i++;
  return `${prefix}${i}`;
}

export function bodyNames(doc: PartDocument): string[] {
  const out: string[] = [];
  let n = 0;
  for (const f of doc.features) {
    const p = f.params as Record<string, any>;
    if (f.suppressed) continue;
    if ((p.operation === "new" || f.type === "import")) {
      n++;
      const name = p.body ?? `Body${n}`;
      if (!out.includes(name)) out.push(name);
    }
    if (f.type === "transform" && p.copy_to && !out.includes(p.copy_to)) out.push(p.copy_to);
  }
  return out;
}

export function upsertFeature(doc: PartDocument, f: Feature): PartDocument {
  const i = doc.features.findIndex((x) => x.id === f.id);
  if (i >= 0) doc.features[i] = f;
  else doc.features.push(f);
  return doc;
}

/** Parse "body|face:name" / "body|edge:a|b" selection keys. */
export function parseSel(k: string): { body: string; kind: "face" | "edge" | "body"; face?: string; faces?: string[] } {
  const [body, rest = ""] = k.split(/\|(.*)/s);
  if (rest.startsWith("face:")) return { body, kind: "face", face: rest.slice(5) };
  if (rest.startsWith("edge:")) return { body, kind: "edge", faces: rest.slice(5).split("|") };
  return { body, kind: "body" };
}
