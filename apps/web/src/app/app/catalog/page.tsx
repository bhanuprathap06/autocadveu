"use client";

import { useQuery } from "@tanstack/react-query";
import { Boxes, Search, Star } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDeferredValue, useState } from "react";
import { apiPath, get } from "@/api/client";
import type { CatalogItem } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { AssetBadge, Empty, ErrorBox, Loading, StatusBadge } from "@/components/ui";

interface Facets { [k: string]: { value: string; count: number }[] }

function Catalog() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const dq = useDeferredValue(q);
  const [f, setF] = useState<Record<string, string>>({});
  const [hasCad, setHasCad] = useState(false);
  const [minComp, setMinComp] = useState(0);
  const [spec, setSpec] = useState("");
  const [sort, setSort] = useState("relevance");
  const [picked, setPicked] = useState<string[]>([]);
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => get<Facets>(apiPath("/api/v1/catalog/facets")), staleTime: 600_000 });
  const list = useQuery({
    queryKey: ["catalog", dq, f, hasCad, minComp, spec, sort],
    queryFn: () => get<{ items: CatalogItem[]; total: number }>(apiPath("/api/v1/catalog/items", {}, {
      q: dq || undefined, ...f, has_cad: hasCad || undefined, min_completeness: minComp || undefined, spec: spec ? spec.split(/\s+/).filter(Boolean) : undefined, sort, limit: 100,
    })),
  });
  const facetSel = (k: string, label: string) => (
    <div className="field" key={k}>
      <label htmlFor={`f-${k}`}>{label}</label>
      <select id={`f-${k}`} className="select" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })}>
        <option value="">All</option>
        {(facets.data?.[k === "verification" ? "verification_status" : k] ?? []).map((x) => <option key={x.value} value={x.value}>{x.value.replace(/_/g, " ")} ({x.count})</option>)}
      </select>
    </div>
  );
  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <div className="row between wrap" style={{ marginBottom: 12 }}>
        <div><h1>Component library</h1><p className="muted small" style={{ margin: "4px 0 0" }}>Every value shows its source and verification status. Missing manufacturer data is shown as missing.</p></div>
        <div className="row">
          {picked.length >= 2 && <button className="btn primary" onClick={() => router.push(`/app/compare?ids=${picked.join(",")}`)}>Compare {picked.length}</button>}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "240px minmax(0,1fr)", gap: 16 }}>
        <aside className="col gap-12" aria-label="Filters">
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: 8, color: "var(--text-faint)" }} />
            <input className="input" style={{ paddingLeft: 28 }} placeholder="Name, part no., standard…" aria-label="Search catalog" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {facetSel("category", "Category")}
          {facetSel("domain", "Domain")}
          {facetSel("asset_class", "Asset class")}
          {facetSel("verification", "Verification")}
          <label className="checkbox small"><input type="checkbox" checked={hasCad} onChange={(e) => setHasCad(e.target.checked)} />Has 3D geometry</label>
          <div className="field"><label htmlFor="mc">Minimum data completeness: {Math.round(minComp * 100)}%</label>
            <input id="mc" type="range" min={0} max={1} step={0.1} value={minComp} onChange={(e) => setMinComp(Number(e.target.value))} /></div>
          <div className="field"><label htmlFor="spec">Spec ranges</label>
            <input id="spec" className="input mono" placeholder="bore_d:20..30 width:..15" value={spec} onChange={(e) => setSpec(e.target.value)} />
            <span className="hint">key:min..max, space-separated</span></div>
          <div className="field"><label htmlFor="sort">Sort</label>
            <select id="sort" className="select" value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="relevance">Relevance</option><option value="name">Name</option><option value="completeness">Completeness</option><option value="updated">Recently updated</option>
            </select></div>
          <Link href="/app" className="small row"><Star size={13} />Your favourites are on the dashboard</Link>
        </aside>
        <section>
          {list.isLoading && <Loading />}
          {list.error && <ErrorBox error={list.error} />}
          {list.data && <div className="faint small" style={{ marginBottom: 8 }}>{list.data.total} item(s)</div>}
          {list.data && !list.data.items.length && <Empty icon={<Boxes size={28} />} title="No components match">Relax the filters. Organisations can add their own sourced records.</Empty>}
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th aria-label="Select for comparison" /><th>Component</th><th>Category</th><th>Source</th><th>Verification</th><th className="num">Completeness</th><th>3D</th><th>Licence</th></tr></thead>
              <tbody>
                {list.data?.items.map((i) => (
                  <tr key={i.id}>
                    <td><input type="checkbox" aria-label={`Select ${i.name} for comparison`} checked={picked.includes(i.id)} disabled={!picked.includes(i.id) && picked.length >= 8}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, i.id] : picked.filter((x) => x !== i.id))} /></td>
                    <td><Link href={`/app/catalog/${i.id}`}><strong>{i.name}</strong></Link>
                      <div className="faint tiny">{[i.manufacturer, i.part_number, i.standard].filter(Boolean).join(" · ")}</div>
                      {i.safety_critical && <span className="badge warn">safety-critical</span>}</td>
                    <td className="small">{i.category.replace(/_/g, " ")}{i.subcategory ? ` / ${i.subcategory.replace(/_/g, " ")}` : ""}</td>
                    <td><AssetBadge cls={i.asset_class} /></td>
                    <td><StatusBadge status={i.verification_status === "verified" ? "ok" : i.verification_status === "unverified" ? "warning" : "proposed"} label={i.verification_status.replace(/_/g, " ")} /></td>
                    <td className="num">{Math.round(i.completeness * 100)}%</td>
                    <td>{i.has_cad ? "✓" : <span className="faint">—</span>}</td>
                    <td className="small">{i.license ?? <span className="faint">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Component library" }]}><Catalog /></AppShell>; }
