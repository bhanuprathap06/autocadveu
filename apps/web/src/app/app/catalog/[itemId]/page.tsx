"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, Star } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { apiPath, del, errorMessage, get, post } from "@/api/client";
import type { BodyMesh, CatalogItem, Project } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { AssetBadge, Dialog, ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { Viewport } from "@/components/viewer/Viewport";

function Item() {
  const { itemId } = useParams<{ itemId: string }>();
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const q = useQuery({ queryKey: ["catalog-item", itemId], queryFn: () => get<{ item: CatalogItem; favorite: boolean; similar: CatalogItem[] }>(apiPath("/api/v1/catalog/items/{item_id}", { item_id: itemId })) });
  const model = useQuery({ queryKey: ["catalog-model", itemId], queryFn: () => get<{ bodies: BodyMesh[] }>(apiPath("/api/v1/catalog/items/{item_id}/model", { item_id: itemId })), enabled: !!q.data?.item.has_cad, retry: false });
  if (q.isLoading) return <Loading />;
  if (q.error) return <div className="page"><ErrorBox error={q.error} /></div>;
  const { item: i, favorite, similar } = q.data!;
  return (
    <div className="page col gap-16" style={{ maxWidth: 1300 }}>
      <div className="row between wrap">
        <div>
          <h1>{i.name}</h1>
          <div className="row wrap small" style={{ marginTop: 6 }}>
            <AssetBadge cls={i.asset_class} />
            <StatusBadge status={i.verification_status === "verified" ? "ok" : "warning"} label={i.verification_status.replace(/_/g, " ")} />
            {i.safety_critical && <span className="badge warn">Safety-critical</span>}
            <span className="muted">{[i.manufacturer, i.part_number, i.standard].filter(Boolean).join(" · ")}</span>
          </div>
        </div>
        <div className="row">
          <button className="btn" onClick={async () => {
            try { if (favorite) await del(apiPath("/api/v1/catalog/items/{item_id}/favorite", { item_id: itemId })); else await post(apiPath("/api/v1/catalog/items/{item_id}/favorite", { item_id: itemId }));
              qc.invalidateQueries({ queryKey: ["catalog-item", itemId] }); qc.invalidateQueries({ queryKey: ["favorites"] }); }
            catch (e) { toast.err("Could not update favourites", errorMessage(e)); }
          }}><Star size={14} fill={favorite ? "currentColor" : "none"} />{favorite ? "Favourite" : "Add to favourites"}</button>
          <button className="btn primary" onClick={() => setAddOpen(true)}><Plus size={14} />Add to project</button>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 420px", gap: 16 }}>
        <div className="col gap-16">
          <div className="card" style={{ padding: 0, height: 380, overflow: "hidden", position: "relative" }}>
            {!i.has_cad ? <div className="empty" style={{ height: "100%", border: 0 }}>No redistributable 3D geometry. {i.source_url && <a href={i.source_url} target="_blank" rel="noopener noreferrer">Get it from the source</a>}</div>
              : model.isLoading ? <Loading label="Generating geometry…" /> : model.error ? <ErrorBox error={model.error} />
              : <Viewport bodies={(model.data?.bodies ?? []).map((b) => ({ key: b.name, label: b.name, mesh: b }))} interactive />}
            {i.has_cad && <div className="vp-status">{i.asset_class === "generated" ? "App-generated geometry from standard dimensions — not a manufacturer model" : "Source geometry"}</div>}
          </div>
          {i.description && <p style={{ margin: 0 }}>{i.description}</p>}
          <section>
            <h3 style={{ marginBottom: 8 }}>Specifications</h3>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Property</th><th className="num">Value</th><th>Basis</th><th>Source</th><th>Verified</th></tr></thead>
              <tbody>{Object.entries(i.specs ?? {}).map(([k, s]) => (
                <tr key={k}>
                  <td>{k.replace(/_/g, " ")}</td>
                  <td className="num">{s.value === null || s.value === undefined ? <span className="badge warn">missing</span> : `${String(s.value)} ${s.unit ?? ""}`}</td>
                  <td className="small">{s.basis}</td>
                  <td className="small" style={{ maxWidth: 260, overflowWrap: "anywhere" }}>{s.source ?? <span className="faint">—</span>}{s.note && <div className="faint tiny">{s.note}</div>}</td>
                  <td>{s.verified ? <span className="badge ok">yes</span> : <span className="badge">no</span>}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </section>
          {i.compatibility_notes && <div className="alert info small">{i.compatibility_notes}</div>}
        </div>
        <aside className="col gap-12">
          <section className="card">
            <h3 style={{ marginBottom: 8 }}>Provenance</h3>
            <dl className="kv">
              <dt>Asset class</dt><dd>{i.asset_class.replace(/_/g, " ")}</dd>
              <dt>Spec source</dt><dd>{i.spec_source ?? "—"}</dd>
              <dt>Source URL</dt><dd>{i.source_url ? <a href={i.source_url} target="_blank" rel="noopener noreferrer">{i.source_url} <ExternalLink size={11} /></a> : "—"}</dd>
              <dt>Retrieved</dt><dd>{i.source_retrieved_at ? new Date(i.source_retrieved_at).toLocaleDateString() : "—"}</dd>
              <dt>Measurement basis</dt><dd>{i.measurement_basis ?? "—"}</dd>
              <dt>Material</dt><dd>{i.material ?? "—"}</dd>
              <dt>Mass</dt><dd>{i.mass_kg != null ? `${i.mass_kg} kg` : "not stated"}</dd>
              <dt>Completeness</dt><dd>{Math.round(i.completeness * 100)}% of expected fields</dd>
            </dl>
          </section>
          {i.license_detail && (
            <section className="card">
              <h3 style={{ marginBottom: 8 }}>Licence</h3>
              <dl className="kv">
                <dt>Licence</dt><dd>{i.license_detail.url ? <a href={i.license_detail.url} target="_blank" rel="noopener noreferrer">{i.license_detail.name}</a> : i.license_detail.name}</dd>
                <dt>Redistribution</dt><dd>{i.license_detail.redistribution_allowed ? "allowed" : "not allowed"}</dd>
                <dt>Attribution</dt><dd>{i.license_detail.attribution_required ? "required" : "not required"}</dd>
                <dt>Commercial use</dt><dd>{i.license_detail.commercial_use_allowed ? "allowed" : "not allowed"}</dd>
              </dl>
              {i.license_detail.notes && <div className="faint tiny" style={{ marginTop: 6 }}>{i.license_detail.notes}</div>}
            </section>
          )}
          {similar.length > 0 && (
            <section className="card">
              <h3 style={{ marginBottom: 8 }}>Similar items</h3>
              {similar.map((s) => (
                <div key={s.id} className="row small" style={{ padding: "3px 0" }}>
                  <Link href={`/app/catalog/${s.id}`} className="grow ellipsis">{s.name}</Link>
                  <span className="faint tiny" title={`Compared on ${s.compared_on?.join(", ")}`}>Δ {s.distance?.toFixed(2)}</span>
                  <Link className="btn ghost sm" href={`/app/compare?ids=${i.id},${s.id}`}>Compare</Link>
                </div>
              ))}
            </section>
          )}
        </aside>
      </div>
      <AddToProject open={addOpen} onClose={() => setAddOpen(false)} itemId={itemId} />
    </div>
  );
}

function AddToProject({ open, onClose, itemId }: { open: boolean; onClose: () => void; itemId: string }) {
  const projects = useQuery({ queryKey: ["projects", ""], queryFn: () => get<{ items: Project[] }>(apiPath("/api/v1/projects", {}, { limit: 200 })), enabled: open });
  const [pid, setPid] = useState("");
  const [note, setNote] = useState("");
  const editable = projects.data?.items.filter((p) => p.role === "owner" || p.role === "editor") ?? [];
  return (
    <Dialog open={open} onClose={onClose} title="Add to project shortlist"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!pid} onClick={async () => {
        try { await post(apiPath("/api/v1/projects/{project_id}/components", { project_id: pid }), { item_id: itemId, note }); toast.ok("Added to project"); onClose(); }
        catch (e) { toast.err("Could not add", errorMessage(e)); }
      }}>Add</button></>}>
      <select className="select" aria-label="Project" value={pid} onChange={(e) => setPid(e.target.value)}>
        <option value="">Choose a project…</option>{editable.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <textarea className="textarea" rows={2} placeholder="Why this component (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <span className="faint small">To place it in a model, insert it from an assembly workbench (components with geometry only).</span>
    </Dialog>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app/catalog", label: "Component library" }, { label: "Item" }]}><Item /></AppShell>; }
