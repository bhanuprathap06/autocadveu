"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, apiPath, errorMessage, post } from "@/api/client";
import type { AssemblyDocument, PartDocument, Revision } from "@/api/types";
import { useEditor } from "./store";

/** Debounced server recompute of the working document. Stale responses are discarded. */
export function useLiveModel<T>(docId: string, content: unknown, opts: { rollback?: string | null; enabled?: boolean; delay?: number } = {}) {
  const [model, setModel] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [ms, setMs] = useState<number | null>(null);
  const seq = useRef(0);
  const key = content ? JSON.stringify(content) : "";
  useEffect(() => {
    if (opts.enabled === false || !content) return;
    const my = ++seq.current;
    setBusy(true);
    const t = setTimeout(async () => {
      const t0 = performance.now();
      try {
        const m = await post<T>(apiPath("/api/v1/documents/{document_id}/recompute", { document_id: docId }),
          { content, rollback_to: opts.rollback ?? null, quality: "normal" });
        if (my !== seq.current) return;
        setModel(m);
        setError(null);
        setMs(performance.now() - t0);
      } catch (e) {
        if (my === seq.current) setError(e);
      } finally {
        if (my === seq.current) setBusy(false);
      }
    }, opts.delay ?? 220);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, opts.rollback, docId, opts.enabled]);
  return { model, busy, error, ms };
}

const draftKey = (docId: string) => `af:draft:${docId}`;
export interface LocalDraft { base: number; content: PartDocument | AssemblyDocument; at: number }

export function readDraft(docId: string): LocalDraft | null {
  try {
    const raw = localStorage.getItem(draftKey(docId));
    return raw ? (JSON.parse(raw) as LocalDraft) : null;
  } catch {
    return null;
  }
}
export function clearDraft(docId: string) {
  try { localStorage.removeItem(draftKey(docId)); } catch { /* storage unavailable */ }
}
function writeDraft(docId: string, d: LocalDraft) {
  try { localStorage.setItem(draftKey(docId), JSON.stringify(d)); } catch { /* quota or private mode: autosave still runs */ }
}

let saving: Promise<void> | null = null;

/**
 * Save the working document as a new immutable revision. Uses optimistic concurrency (base_revision) and an
 * idempotency key, so a retried request never creates a duplicate revision.
 */
export async function saveNow(message?: string): Promise<void> {
  if (saving) return saving;
  const st = useEditor.getState();
  if (!st.docId || !st.doc || st.save === "readonly" || st.save === "conflict") return;
  if (st.save === "saved" && !message) return;
  const docId = st.docId;
  const snapshot = st.doc;
  const labels = st.opLabels;
  const op = `op-${crypto.randomUUID()}`;
  const msg = message ?? (labels.length ? labels.slice(-3).join("; ") + (labels.length > 3 ? ` (+${labels.length - 3} more)` : "") : "Autosave");
  st.setSave("saving");
  saving = (async () => {
    let attempt = 0;
    for (;;) {
      try {
        const r = await post<{ revision: Revision; duplicate: boolean }>(apiPath("/api/v1/documents/{document_id}/revisions", { document_id: docId }),
          { base_revision: st.baseRevision, content: snapshot, op_id: op, message: msg.slice(0, 500) });
        const now = useEditor.getState();
        if (now.docId !== docId) return;
        const clean = now.doc === snapshot;
        useEditor.setState({ baseRevision: r.revision.number, save: clean ? "saved" : "dirty", saveError: null,
          opLabels: now.opLabels.slice(labels.length) });
        if (clean) clearDraft(docId);
        now.addLog(r.revision.ok === false ? "warn" : "ok",
          `Saved revision ${r.revision.number}${r.revision.ok === false ? " (model has feature errors)" : ""}`);
        return;
      } catch (e) {
        const err = e as ApiError;
        if (err instanceof ApiError && err.status === 409 && err.code === "revision_conflict") {
          useEditor.getState().setSave("conflict", err.message);
          useEditor.getState().addLog("warn", err.message);
          return;
        }
        if (err instanceof ApiError && (err.status === 0 || err.status >= 502) && attempt < 3) {
          attempt++;
          useEditor.getState().setSave("offline", "Connection lost; retrying…");
          await new Promise((res) => setTimeout(res, 1500 * 2 ** attempt));
          continue; // same op_id: idempotent retry
        }
        const offline = err instanceof ApiError && (err.status === 0 || err.status >= 502);
        useEditor.getState().setSave(offline ? "offline" : "error", errorMessage(e));
        useEditor.getState().addLog("err", `Save failed: ${errorMessage(e)}`);
        return;
      }
    }
  })().finally(() => { saving = null; });
  return saving;
}

/** Autosave after a short idle period; keeps a local recovery draft until the server confirms the save. */
export function useAutosave(delay = 1800) {
  const doc = useEditor((s) => s.doc);
  const save = useEditor((s) => s.save);
  const docId = useEditor((s) => s.docId);
  const base = useEditor((s) => s.baseRevision);
  useEffect(() => {
    if (!docId || !doc || save !== "dirty") return;
    writeDraft(docId, { base, content: doc, at: Date.now() });
    const t = setTimeout(() => void saveNow(), delay);
    return () => clearTimeout(t);
  }, [doc, save, docId, base, delay]);
  useEffect(() => {
    if (save !== "offline") return;
    const t = setTimeout(() => { useEditor.getState().setSave("dirty"); }, 10_000);
    return () => clearTimeout(t);
  }, [save]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      const s = useEditor.getState().save;
      if (s === "dirty" || s === "saving" || s === "offline" || s === "conflict" || s === "error") { e.preventDefault(); }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
}
