"use client";

import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { apiPath, errorMessage, get, post } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import { ErrorBox, Loading, StatusBadge, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";

function Admin() {
  const [tab, setTab] = useState<"overview" | "jobs" | "users" | "catalog" | "outbox" | "audit">("overview");
  const [action, setAction] = useState("");
  const overview = useQuery({ queryKey: ["admin", "overview"], queryFn: () => get<{ ready: boolean; checks: Record<string, any>; counts: Record<string, any> }>(apiPath("/api/v1/admin/overview")), refetchInterval: 15_000 });
  const jobs = useQuery({ queryKey: ["admin", "jobs"], queryFn: () => get<{ items: any[] }>(apiPath("/api/v1/admin/jobs")), enabled: tab === "jobs", refetchInterval: 5000 });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => get<{ items: any[] }>(apiPath("/api/v1/admin/users")), enabled: tab === "users" });
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => get<{ items: any[] }>(apiPath("/api/v1/admin/catalog/review")), enabled: tab === "catalog" });
  const outbox = useQuery({ queryKey: ["admin", "outbox"], queryFn: () => get<{ items: any[] }>(apiPath("/api/v1/admin/outbox")), enabled: tab === "outbox" });
  const audit = useQuery({ queryKey: ["admin", "audit", action], queryFn: () => get<{ items: any[] }>(apiPath("/api/v1/admin/audit", {}, { action: action || undefined })), enabled: tab === "audit" });
  if (overview.error) return <div className="page"><ErrorBox error={overview.error} /></div>;
  return (
    <div className="page col gap-16" style={{ maxWidth: 1400 }}>
      <h1>Administration</h1>
      <Tabs label="Admin sections" value={tab} onChange={setTab} tabs={["overview", "jobs", "users", "catalog", "outbox", "audit"].map((k) => ({ key: k as typeof tab, label: k[0].toUpperCase() + k.slice(1) }))} />
      {tab === "overview" && (overview.isLoading ? <Loading /> : (
        <div className="grid grid-2">
          <section className="card">
            <div className="card-title"><h3>Readiness</h3><StatusBadge status={overview.data!.ready ? "ok" : "error"} label={overview.data!.ready ? "Ready" : "Degraded"} /></div>
            <table className="table"><tbody>{Object.entries(overview.data!.checks).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td><StatusBadge status={v.ok ? "ok" : "error"} /></td><td className="tiny faint">{JSON.stringify({ ...v, ok: undefined }).slice(0, 160)}</td></tr>
            ))}</tbody></table>
          </section>
          <section className="card">
            <h3 style={{ marginBottom: 8 }}>Counts</h3>
            <dl className="kv">{Object.entries(overview.data!.counts).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>)}</dl>
          </section>
        </div>
      ))}
      {tab === "jobs" && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Kind</th><th>Status</th><th>Attempts</th><th>Worker</th><th>Created</th><th>Error</th><th aria-label="Actions" /></tr></thead>
          <tbody>{jobs.data?.items.map((j) => (
            <tr key={j.id}><td>{j.kind}</td><td><StatusBadge status={j.status} />{j.progress?.stage && <span className="faint tiny"> {j.progress.stage}</span>}</td><td className="num">{j.attempts}</td>
              <td className="mono tiny">{j.locked_by ?? "—"}</td><td className="small">{new Date(j.created_at).toLocaleString()}</td><td className="tiny" style={{ maxWidth: 300 }}>{j.error}</td>
              <td>{(j.status === "queued" || j.status === "running") && <button className="btn ghost sm" onClick={async () => { try { await post(apiPath("/api/v1/admin/jobs/{job_id}/cancel", { job_id: j.id })); jobs.refetch(); } catch (e) { toast.err("Cancel failed", errorMessage(e)); } }}>Cancel</button>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {tab === "users" && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Email</th><th>Name</th><th>Verified</th><th>MFA</th><th>Admin</th><th>Status</th><th aria-label="Actions" /></tr></thead>
          <tbody>{users.data?.items.map((u) => (
            <tr key={u.id}><td>{u.email}</td><td>{u.display_name}</td><td>{u.verified ? "yes" : "no"}</td><td>{u.mfa ? "yes" : "no"}</td><td>{u.admin ? "yes" : ""}</td>
              <td>{u.disabled ? <span className="badge err">disabled</span> : <span className="badge ok">active</span>}</td>
              <td>{!u.disabled && !u.admin && <button className="btn ghost sm danger" onClick={async () => {
                if (!window.confirm(`Disable ${u.email}? Their sessions are revoked immediately.`)) return;
                try { await post(apiPath("/api/v1/admin/users/{user_id}/disable", { user_id: u.id })); users.refetch(); } catch (e) { toast.err("Failed", errorMessage(e)); }
              }}>Disable</button>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {tab === "catalog" && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Item</th><th>Class</th><th>Verification</th><th className="num">Completeness</th><th>Scope</th></tr></thead>
          <tbody>{catalog.data?.items.map((i) => <tr key={i.id}><td><a href={`/app/catalog/${i.id}`}>{i.name}</a></td><td>{i.asset_class}</td><td>{i.verification_status}</td><td className="num">{Math.round(i.completeness * 100)}%</td><td>{i.org_scoped ? "organisation" : "global"}</td></tr>)}</tbody>
        </table></div>
      )}
      {tab === "outbox" && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>To</th><th>Subject</th><th>Kind</th><th>Sent</th><th>Attempts</th><th>Error</th></tr></thead>
          <tbody>{outbox.data?.items.map((e, k) => <tr key={k}><td>{e.to}</td><td>{e.subject}</td><td>{e.kind}</td><td>{e.sent ? "yes" : "pending"}</td><td className="num">{e.attempts}</td><td className="tiny">{e.error}</td></tr>)}</tbody>
        </table></div>
      )}
      {tab === "audit" && (
        <>
          <input className="input" style={{ maxWidth: 320 }} placeholder="Filter by action prefix, e.g. auth. or document." aria-label="Action filter" value={action} onChange={(e) => setAction(e.target.value)} />
          <div className="table-wrap"><table className="table">
            <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Target</th><th>IP</th><th>Request</th></tr></thead>
            <tbody>{audit.data?.items.map((e, k) => <tr key={k}><td className="small nowrap">{new Date(e.at).toLocaleString()}</td><td className="mono small">{e.action}</td><td className="mono tiny">{e.actor_id?.slice(0, 8) ?? "—"}</td><td className="tiny">{e.target}</td><td className="mono tiny">{e.ip}</td><td className="mono tiny">{e.request_id?.slice(0, 8)}</td></tr>)}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Administration" }]}><Admin /></AppShell>; }
