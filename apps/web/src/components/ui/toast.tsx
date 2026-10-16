"use client";

import { X } from "lucide-react";
import { create } from "zustand";

type Kind = "ok" | "err" | "warn" | "info";
interface Toast { id: number; kind: Kind; title: string; detail?: string }
interface ToastStore { items: Toast[]; push: (k: Kind, title: string, detail?: string) => void; dismiss: (id: number) => void }

let n = 0;
export const useToasts = create<ToastStore>((set) => ({
  items: [],
  push: (kind, title, detail) => {
    const id = ++n;
    set((s) => ({ items: [...s.items.slice(-4), { id, kind, title, detail }] }));
    setTimeout(() => set((s) => ({ items: s.items.filter((t) => t.id !== id) })), kind === "err" ? 9000 : 4500);
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
}));

export const toast = {
  ok: (t: string, d?: string) => useToasts.getState().push("ok", t, d),
  err: (t: string, d?: string) => useToasts.getState().push("err", t, d),
  warn: (t: string, d?: string) => useToasts.getState().push("warn", t, d),
  info: (t: string, d?: string) => useToasts.getState().push("info", t, d),
};

export function Toasts() {
  const { items, dismiss } = useToasts();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <div className="row between">
            <strong>{t.title}</strong>
            <button className="btn ghost icon sm" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}>
              <X size={14} />
            </button>
          </div>
          {t.detail && <div className="muted small" style={{ marginTop: 4 }}>{t.detail}</div>}
        </div>
      ))}
    </div>
  );
}
