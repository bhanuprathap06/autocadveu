"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, CircleX, Info, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "@/api/client";

/* ------------------------------------------------------------------ Dialog */
export function Dialog({ title, open, onClose, children, footer, wide }: {
  title: string; open: boolean; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && ref.current) {
        const f = ref.current.querySelectorAll<HTMLElement>("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        const a = f[0], z = f[f.length - 1];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [open, onClose]);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header>
          <h3>{title}</h3>
          <button className="btn ghost icon sm" aria-label="Close dialog" data-close onClick={onClose}><X size={15} /></button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function Confirm({ open, title, message, confirmLabel = "Confirm", danger, onConfirm, onClose }: {
  open: boolean; title: string; message: ReactNode; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Dialog open={open} title={title} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className={`btn ${danger ? "danger" : "primary"}`} onClick={() => { onConfirm(); onClose(); }}>{confirmLabel}</button></>}>
      <div>{message}</div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ Context menu */
export interface MenuItem { label: string; icon?: ReactNode; onSelect?: () => void; disabled?: boolean; separator?: boolean; hint?: string }
export function ContextMenu({ at, items, onClose }: { at: { x: number; y: number } | null; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!at) return;
    ref.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const bs = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
        const i = bs.indexOf(document.activeElement as HTMLButtonElement);
        bs[(i + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length]?.focus();
      }
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", key); };
  }, [at, onClose]);
  if (!at || typeof document === "undefined") return null;
  const x = Math.min(at.x, window.innerWidth - 220), y = Math.min(at.y, window.innerHeight - items.length * 30 - 10);
  return createPortal(
    <div className="menu" style={{ left: x, top: y }} ref={ref} role="menu">
      {items.map((it, i) => it.separator ? <div className="sep" key={i} /> : (
        <button key={i} role="menuitem" disabled={it.disabled} onClick={() => { it.onSelect?.(); onClose(); }}>
          <span style={{ width: 16, display: "inline-flex" }}>{it.icon}</span>
          <span className="grow">{it.label}</span>
          {it.hint && <span className="kbd">{it.hint}</span>}
        </button>
      ))}
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------------ Tabs */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { key: T; label: ReactNode; badge?: ReactNode }[]; value: T; onChange: (k: T) => void; label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.key} role="tab" className="tab" aria-selected={value === t.key} tabIndex={value === t.key ? 0 : -1}
          onClick={() => onChange(t.key)}
          onKeyDown={(e) => {
            const i = tabs.findIndex((x) => x.key === value);
            if (e.key === "ArrowRight") onChange(tabs[(i + 1) % tabs.length].key);
            if (e.key === "ArrowLeft") onChange(tabs[(i - 1 + tabs.length) % tabs.length].key);
          }}>
          {t.label}{t.badge}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Status */
const STATUS: Record<string, { cls: string; label: string; icon: ReactNode }> = {
  ok: { cls: "ok", label: "OK", icon: <CheckCircle2 size={12} /> },
  succeeded: { cls: "ok", label: "Succeeded", icon: <CheckCircle2 size={12} /> },
  approved: { cls: "ok", label: "Approved", icon: <CheckCircle2 size={12} /> },
  pass: { cls: "ok", label: "Pass", icon: <CheckCircle2 size={12} /> },
  fully_constrained: { cls: "ok", label: "Fully constrained", icon: <CheckCircle2 size={12} /> },
  warning: { cls: "warn", label: "Warning", icon: <AlertTriangle size={12} /> },
  under_constrained: { cls: "info", label: "Under-constrained", icon: <CircleDashed size={12} /> },
  over_constrained: { cls: "warn", label: "Redundant", icon: <AlertTriangle size={12} /> },
  proposed: { cls: "info", label: "Proposed", icon: <CircleDashed size={12} /> },
  queued: { cls: "info", label: "Queued", icon: <CircleDashed size={12} /> },
  running: { cls: "accent", label: "Running", icon: <Loader2 size={12} className="spin" /> },
  error: { cls: "err", label: "Error", icon: <CircleX size={12} /> },
  failed: { cls: "err", label: "Failed", icon: <CircleX size={12} /> },
  fail: { cls: "err", label: "Fail", icon: <CircleX size={12} /> },
  conflicting: { cls: "err", label: "Conflicting", icon: <CircleX size={12} /> },
  rejected: { cls: "", label: "Rejected", icon: <CircleX size={12} /> },
  timed_out: { cls: "err", label: "Timed out", icon: <CircleX size={12} /> },
  cancelled: { cls: "", label: "Cancelled", icon: <CircleX size={12} /> },
  suppressed: { cls: "", label: "Suppressed", icon: <CircleDashed size={12} /> },
  skipped: { cls: "", label: "Skipped", icon: <CircleDashed size={12} /> },
  implemented_and_verified: { cls: "ok", label: "Implemented & verified", icon: <CheckCircle2 size={12} /> },
  implemented_with_limitations: { cls: "warn", label: "With limitations", icon: <AlertTriangle size={12} /> },
  prototype: { cls: "violet", label: "Prototype", icon: <Info size={12} /> },
  external_integration_required: { cls: "info", label: "External integration required", icon: <Info size={12} /> },
  planned: { cls: "", label: "Planned", icon: <CircleDashed size={12} /> },
  unsupported: { cls: "err", label: "Unsupported", icon: <CircleX size={12} /> },
};
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const s = STATUS[status] ?? { cls: "", label: status.replace(/_/g, " "), icon: null };
  return <span className={`badge ${s.cls}`}>{s.icon}{label ?? s.label}</span>;
}

const CLAIMS: Record<string, [string, string]> = {
  source_supported_fact: ["ok", "Source-supported fact"],
  calculation: ["accent", "Calculation"],
  engineering_hypothesis: ["violet", "Engineering hypothesis"],
  design_recommendation: ["info", "Design recommendation"],
  unverified_suggestion: ["warn", "Unverified suggestion"],
};
export function ClaimBadge({ cls }: { cls: string | null }) {
  const [c, l] = CLAIMS[cls ?? ""] ?? ["", cls ?? "unclassified"];
  return <span className={`badge ${c}`} title="Claim classification">{l}</span>;
}

const ASSET: Record<string, [string, string]> = {
  verified_public: ["ok", "Verified public CAD"],
  manufacturer: ["accent", "Manufacturer data"],
  user_upload: ["violet", "User upload"],
  generated: ["info", "App-generated geometry"],
};
export function AssetBadge({ cls }: { cls: string }) {
  const [c, l] = ASSET[cls] ?? ["", cls];
  return <span className={`badge ${c}`}>{l}</span>;
}

/* ------------------------------------------------------------------ Empty / error / loading */
export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <strong style={{ color: "var(--text)" }}>{title}</strong>
      {children && <div className="small" style={{ maxWidth: 460 }}>{children}</div>}
      {action}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error as ApiError;
  return (
    <div className="alert err" role="alert">
      <CircleX size={16} style={{ flex: "none", marginTop: 1 }} />
      <div className="grow">
        <div><strong>{e?.message ?? "Something went wrong"}</strong></div>
        {e?.code && <div className="faint tiny">Code: {e.code}{e.requestId ? ` · Request ${e.requestId}` : ""}</div>}
        {Array.isArray(e?.details) && (e.details as any[]).slice(0, 4).map((d, i) => (
          <div key={i} className="tiny muted">{typeof d === "string" ? d : d.message ?? `${(d.loc ?? []).join(".")}: ${d.msg ?? JSON.stringify(d)}`}</div>
        ))}
      </div>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="row muted" role="status" style={{ padding: 16 }}><span className="spinner" />{label}</div>;
}

export function Field({ label, children, hint, error, htmlFor }: { label: string; children: ReactNode; hint?: ReactNode; error?: string | null; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? <span className="error" role="alert">{error}</span> : hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ Splitter */
export function useSplit(initial: number, min: number, max: number, storageKey?: string) {
  const [size, setSize] = useState(() => {
    if (typeof window === "undefined" || !storageKey) return initial;
    try { const v = Number(localStorage.getItem(storageKey)); return v >= min && v <= max ? v : initial; } catch { return initial; }
  });
  useEffect(() => { if (storageKey) try { localStorage.setItem(storageKey, String(size)); } catch { /* storage unavailable */ } }, [size, storageKey]);
  return [size, setSize] as const;
}

export function Splitter({ dir, onDrag, label }: { dir: "v" | "h"; onDrag: (delta: number) => void; label: string }) {
  const [active, setActive] = useState(false);
  return (
    <div className={`splitter ${dir}${active ? " active" : ""}`} role="separator" aria-orientation={dir === "v" ? "vertical" : "horizontal"}
      aria-label={label} tabIndex={0}
      onKeyDown={(e) => {
        if (dir === "v" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) onDrag(e.key === "ArrowLeft" ? -16 : 16);
        if (dir === "h" && (e.key === "ArrowUp" || e.key === "ArrowDown")) onDrag(e.key === "ArrowUp" ? -16 : 16);
      }}
      onPointerDown={(e) => {
        setActive(true);
        let last = dir === "v" ? e.clientX : e.clientY;
        const move = (ev: PointerEvent) => { const cur = dir === "v" ? ev.clientX : ev.clientY; onDrag(cur - last); last = cur; };
        const up = () => { setActive(false); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      }} />
  );
}

/* ------------------------------------------------------------------ Command palette */
export interface Command { id: string; title: string; group?: string; shortcut?: string; run: () => void; disabled?: boolean }
export function CommandPalette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: Command[] }) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const list = useMemo(() => {
    const t = q.toLowerCase().trim();
    return commands.filter((c) => !c.disabled && (!t || `${c.group ?? ""} ${c.title}`.toLowerCase().includes(t))).slice(0, 40);
  }, [q, commands]);
  useEffect(() => { if (open) { setQ(""); setI(0); } }, [open]);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-label="Command palette">
        <input autoFocus placeholder="Type a command…" value={q} aria-label="Search commands" role="combobox" aria-expanded aria-controls="cmdk-list"
          onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(x + 1, list.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(x - 1, 0)); }
            if (e.key === "Enter" && list[i]) { onClose(); list[i].run(); }
          }} />
        <ul id="cmdk-list" role="listbox">
          {list.map((c, k) => (
            <li key={c.id} role="option" aria-selected={k === i} onMouseEnter={() => setI(k)} onClick={() => { onClose(); c.run(); }}>
              <span>{c.group && <span className="faint">{c.group} › </span>}{c.title}</span>
              {c.shortcut && <span className="kbd">{c.shortcut}</span>}
            </li>
          ))}
          {!list.length && <li className="muted">No matching commands</li>}
        </ul>
      </div>
    </div>,
    document.body,
  );
}

export function Kbd({ children }: { children: ReactNode }) { return <span className="kbd">{children}</span>; }
