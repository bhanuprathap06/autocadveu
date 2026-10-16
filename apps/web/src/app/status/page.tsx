"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { apiPath, get } from "@/api/client";
import { Logo } from "@/components/AppShell";
import { StatusBadge } from "@/components/ui";
import { FEATURES, STATUS_HELP, type Status } from "@/content/featureStatus";

interface Live { env: string; kernel: string; solvers: { calculix: string | null }; ai: { provider: string; configured: boolean }; smtp_configured: boolean; malware_scanning: string }

export default function StatusPage() {
  const [filter, setFilter] = useState<Status | "all">("all");
  const live = useQuery({ queryKey: ["system-status"], queryFn: () => get<Live>(apiPath("/api/v1/status")), retry: false });
  const areas = useMemo(() => [...new Set(FEATURES.map((f) => f.area))], []);
  const counts = useMemo(() => FEATURES.reduce<Record<string, number>>((a, f) => ({ ...a, [f.status]: (a[f.status] ?? 0) + 1 }), {}), []);
  return (
    <div className="landing" style={{ minHeight: "100vh" }}>
      <header className="row" style={{ maxWidth: 1100, margin: "0 auto", padding: "16px 24px" }}>
        <Link href="/" className="row" style={{ color: "var(--text)" }}><Logo size={24} /><strong>AutoForge</strong></Link>
        <div className="grow" /><Link href="/app" className="btn sm">Open the studio</Link>
      </header>
      <main id="main" className="page" style={{ maxWidth: 1100 }}>
        <h1>Feature status</h1>
        <p className="muted">What works, how it is verified, and where the limits are. This list is maintained with the code; nothing here is marketing.</p>
        <section className="card" aria-label="Live deployment status" style={{ marginBottom: 16 }}>
          <h2 style={{ marginBottom: 8 }}>This deployment</h2>
          {live.isLoading && <span className="muted small">Checking services…</span>}
          {live.error ? <div className="alert warn small">The API is not reachable from this page, so live solver availability is unknown.</div> : null}
          {live.data && (
            <dl className="kv">
              <dt>Geometry kernel</dt><dd>{live.data.kernel}</dd>
              <dt>CalculiX</dt><dd>{live.data.solvers.calculix ?? "not installed — simulations will fail with a clear error"}</dd>
              <dt>AI provider</dt><dd>{live.data.ai.configured ? `${live.data.ai.provider} (external, per-request consent)` : "built-in rules engine (no external AI configured)"}</dd>
              <dt>Email delivery</dt><dd>{live.data.smtp_configured ? "SMTP configured" : "development outbox only"}</dd>
              <dt>Malware scanning</dt><dd>{live.data.malware_scanning}</dd>
              <dt>Environment</dt><dd>{live.data.env}</dd>
            </dl>
          )}
        </section>
        <div className="row wrap" role="group" aria-label="Filter by status" style={{ marginBottom: 12 }}>
          <button className="btn sm" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All ({FEATURES.length})</button>
          {(Object.keys(STATUS_HELP) as Status[]).map((s) => (
            <button key={s} className="btn sm" aria-pressed={filter === s} onClick={() => setFilter(s)} title={STATUS_HELP[s]}>
              <StatusBadge status={s} /> {counts[s] ?? 0}
            </button>
          ))}
        </div>
        {areas.map((area) => {
          const rows = FEATURES.filter((f) => f.area === area && (filter === "all" || f.status === filter));
          if (!rows.length) return null;
          return (
            <section key={area} style={{ marginBottom: 20 }}>
              <h2 style={{ margin: "12px 0 8px" }}>{area}</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th style={{ width: "34%" }}>Capability</th><th style={{ width: 190 }}>Status</th><th>Evidence and limits</th></tr></thead>
                  <tbody>
                    {rows.map((f) => (
                      <tr key={f.feature}>
                        <td>{f.feature}</td>
                        <td><StatusBadge status={f.status} /></td>
                        <td className="small">{f.evidence}{f.limits && <div className="faint" style={{ marginTop: 4 }}>Limits: {f.limits}</div>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })}
        <p className="faint small">Engineering results from this platform support, but do not replace, qualified engineering judgement, physical testing and certification.</p>
      </main>
    </div>
  );
}
