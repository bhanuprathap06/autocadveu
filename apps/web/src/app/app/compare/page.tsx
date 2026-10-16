"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { Download, Link2, Link2Off } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { apiPath, download, get, post } from "@/api/client";
import type { BodyMesh } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { AssetBadge, Empty, ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { Viewport, type ViewportHandle } from "@/components/viewer/Viewport";

interface Cell { value: unknown; unit?: string; basis?: string; source?: string | null; verified?: boolean; status?: string; note?: string | null }
interface Cmp {
  items: { id: string; name: string; category: string; manufacturer: string | null; part_number: string | null; standard: string | null; asset_class: string; verification_status: string;
    source_url: string | null; license: string | null; completeness: number; safety_critical: boolean }[];
  rows: { key: string; status: string; cells: Cell[]; has_unverified: boolean; spread?: { min: number; max: number; relative: number } }[];
  assessments: { a: string; b: string; verdict: string; reasons: string[]; is_interchangeability_approval: boolean }[];
  warnings: string[]; caveats: string[]; missing: Record<string, string[]>;
}

const ROW_STATUS: Record<string, [string, string]> = {
  equal: ["ok", "equal"], differs: ["warn", "differs"], equal_partial: ["info", "equal where present"], differs_partial: ["warn", "differs (partial)"],
  all_missing: ["err", "missing everywhere"], incomparable_units: ["err", "incomparable units"],
};

function Compare() {
  const ids = (useSearchParams().get("ids") ?? "").split(",").filter(Boolean).slice(0, 8);
  const [sync, setSync] = useState(true);
  const refs = useRef<(ViewportHandle | null)[]>([]);
  const cmp = useQuery({ queryKey: ["compare", ids], queryFn: () => post<Cmp>(apiPath("/api/v1/catalog/compare"), { ids }), enabled: ids.length >= 2 });
  const models = useQueries({ queries: ids.map((id) => ({ queryKey: ["catalog-model", id], queryFn: () => get<{ bodies: BodyMesh[] }>(apiPath("/api/v1/catalog/items/{item_id}/model", { item_id: id })), retry: false })) });
  if (ids.length < 2) return <div className="page"><Empty title="Select at least two components">Pick items in the <Link href="/app/catalog">component library</Link> and choose Compare.</Empty></div>;
  return (
    <div className="page col gap-16" style={{ maxWidth: 1500 }}>
      <div className="row between wrap">
        <h1>Component comparison</h1>
        <div className="row">
          <button className="btn sm" aria-pressed={sync} onClick={() => setSync(!sync)}>{sync ? <Link2 size={13} /> : <Link2Off size={13} />}{sync ? "Views synced" : "Views independent"}</button>
          <button className="btn sm" onClick={() => download("POST", apiPath("/api/v1/catalog/compare/report"), { ids })}><Download size={13} />Report (Markdown)</button>
        </div>
      </div>
      {cmp.isLoading && <Loading />}
      {cmp.error && <ErrorBox error={cmp.error} />}
      {cmp.data && (
        <>
          {cmp.data.warnings.map((w) => <div key={w} className="alert warn small">{w}</div>)}
          <div className="grid" style={{ gridTemplateColumns: `repeat(${ids.length}, minmax(0,1fr))` }}>
            {cmp.data.items.map((it, k) => (
              <div key={it.id} className="card tight col gap-4">
                <Link href={`/app/catalog/${it.id}`}><strong>{it.name}</strong></Link>
                <div className="row wrap tiny"><AssetBadge cls={it.asset_class} /><span className="faint">{it.standard ?? it.manufacturer}</span></div>
                <div style={{ height: 200, border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
                  {models[k]?.data ? (
                    <Viewport ref={(h) => { refs.current[k] = h; }} bodies={models[k].data!.bodies.map((b) => ({ key: b.name, label: b.name, mesh: b }))} showGrid={false}
                      onCameraChange={(c) => { if (!sync) return; refs.current.forEach((r, j) => { if (j !== k) r?.setCamera(c); }); }} />
                  ) : models[k]?.isLoading ? <Loading /> : <div className="empty" style={{ height: "100%", border: 0, fontSize: 11 }}>No 3D geometry</div>}
                </div>
                <div className="faint tiny">completeness {Math.round(it.completeness * 100)}% · {it.license ?? "no licence"}</div>
              </div>
            ))}
          </div>
          <section>
            <h3 style={{ marginBottom: 8 }}>Specifications (normalised units)</h3>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Property</th>{cmp.data.items.map((it) => <th key={it.id}>{it.name}</th>)}<th>Status</th></tr></thead>
              <tbody>{cmp.data.rows.map((r) => (
                <tr key={r.key}>
                  <td>{r.key.replace(/_/g, " ")}{r.has_unverified && <div className="faint tiny">contains unverified values</div>}</td>
                  {r.cells.map((c, j) => (
                    <td key={j} className="num">{c.value === null || c.value === undefined ? <span className="badge warn">missing</span>
                      : <span title={`${c.basis ?? ""}${c.source ? ` · ${c.source}` : ""}`}>{String(c.value)} {c.unit ?? ""}{!c.verified && <span className="faint"> *</span>}</span>}</td>
                  ))}
                  <td><span className={`badge ${ROW_STATUS[r.status]?.[0] ?? ""}`}>{ROW_STATUS[r.status]?.[1] ?? r.status}</span>
                    {r.spread && <span className="faint tiny"> ±{(r.spread.relative * 100).toFixed(1)}%</span>}</td>
                </tr>
              ))}</tbody>
            </table></div>
            <div className="faint tiny" style={{ marginTop: 4 }}>* unverified (nominal or derived) value. Hover a value for its basis and source.</div>
          </section>
          <section>
            <h3 style={{ marginBottom: 8 }}>Pairwise assessment</h3>
            {cmp.data.assessments.map((a, k) => (
              <div key={k} className="card tight row" style={{ alignItems: "flex-start", marginBottom: 6 }}>
                <StatusBadge status={a.verdict === "dimensional_match" ? "ok" : a.verdict === "not_established" ? "warning" : "error"} label={a.verdict.replace(/_/g, " ")} />
                <div className="grow small">
                  <strong>{cmp.data!.items.find((x) => x.id === a.a)?.name}</strong> vs <strong>{cmp.data!.items.find((x) => x.id === a.b)?.name}</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                </div>
              </div>
            ))}
            <ul className="faint small" style={{ paddingLeft: 18 }}>{cmp.data.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
          </section>
        </>
      )}
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app/catalog", label: "Component library" }, { label: "Comparison" }]}><Suspense><Compare /></Suspense></AppShell>; }
