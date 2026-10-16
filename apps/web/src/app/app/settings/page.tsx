"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { apiPath, del, errorMessage, get, post, put } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import { ErrorBox, Field, Loading, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { useMe } from "@/lib/auth";

function Settings() {
  const [tab, setTab] = useState<"profile" | "security" | "sessions" | "activity">("profile");
  return (
    <div className="page col gap-16" style={{ maxWidth: 920 }}>
      <h1>Settings</h1>
      <Tabs label="Settings sections" value={tab} onChange={setTab} tabs={[{ key: "profile", label: "Profile & preferences" }, { key: "security", label: "Password & MFA" },
        { key: "sessions", label: "Sessions" }, { key: "activity", label: "My activity" }]} />
      {tab === "profile" && <Profile />}
      {tab === "security" && <Security />}
      {tab === "sessions" && <Sessions />}
      {tab === "activity" && <MyActivity />}
    </div>
  );
}

function Profile() {
  const me = useMe();
  const qc = useQueryClient();
  const prefs = (me.data?.user.preferences ?? {}) as Record<string, unknown>;
  const set = async (k: string, v: unknown) => {
    try { await put(apiPath("/api/v1/auth/preferences"), { preferences: { [k]: v } }); qc.invalidateQueries({ queryKey: ["me"] }); toast.ok("Preference saved"); }
    catch (e) { toast.err("Could not save", errorMessage(e)); }
  };
  if (!me.data) return <Loading />;
  return (
    <div className="card col gap-12">
      <dl className="kv">
        <dt>Name</dt><dd>{me.data.user.display_name}</dd>
        <dt>Email</dt><dd>{me.data.user.email} {me.data.user.email_verified ? <span className="badge ok">verified</span> : <span className="badge warn">not verified</span>}</dd>
        <dt>Organisations</dt><dd>{me.data.organizations.map((o) => `${o.name} (${o.role})`).join(", ")}</dd>
      </dl>
      {!me.data.user.email_verified && <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={async () => {
        try { await post(apiPath("/api/v1/auth/resend-verification")); toast.ok("Verification email sent"); } catch (e) { toast.err("Could not send", errorMessage(e)); }
      }}>Resend verification email</button>}
      <div className="grid grid-2">
        <Field label="Default length unit" htmlFor="pu"><select id="pu" className="select" value={String(prefs.units ?? "mm")} onChange={(e) => set("units", e.target.value)}>{["mm", "cm", "m", "in"].map((u) => <option key={u}>{u}</option>)}</select></Field>
        <Field label="3D navigation" htmlFor="pn"><select id="pn" className="select" value={String(prefs.navigation ?? "orbit")} onChange={(e) => set("navigation", e.target.value)}><option value="orbit">Orbit (left drag rotates)</option><option value="cad">CAD (middle drag pans)</option></select></Field>
      </div>
      <label className="checkbox"><input type="checkbox" checked={!!prefs.reduced_motion} onChange={(e) => set("reduced_motion", e.target.checked)} />Reduce motion (also follows your OS setting)</label>
    </div>
  );
}

function Security() {
  const me = useMe();
  const qc = useQueryClient();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { if (setup) QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 200 }).then(setQr).catch(() => setQr(null)); }, [setup]);
  if (!me.data) return <Loading />;
  return (
    <div className="col gap-16">
      <form className="card col gap-12" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { await post(apiPath("/api/v1/auth/change-password"), { current_password: cur, new_password: next }); setCur(""); setNext(""); toast.ok("Password changed", "Your other sessions were signed out."); }
        catch (er) { setErr(er); }
      }}>
        <h3><KeyRound size={15} /> Change password</h3>
        <div className="grid grid-2">
          <Field label="Current password" htmlFor="cp"><input id="cp" className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
          <Field label="New password" htmlFor="np" hint="At least 10 characters; avoid reused passwords"><input id="np" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        </div>
        {err ? <ErrorBox error={err} /> : null}
        <div><button className="btn" disabled={!cur || next.length < 10}>Change password</button></div>
      </form>
      <section className="card col gap-12">
        <h3><ShieldCheck size={15} /> Two-factor authentication (TOTP)</h3>
        {me.data.user.mfa_enabled ? (
          <>
            <div className="alert ok small">MFA is enabled. Sign-ins require a code from your authenticator app.</div>
            <div className="row"><input className="input" style={{ width: 140 }} inputMode="numeric" placeholder="123456" aria-label="Current code to disable MFA" value={code} onChange={(e) => setCode(e.target.value)} />
              <button className="btn danger sm" disabled={code.length < 6} onClick={async () => {
                try { await post(apiPath("/api/v1/auth/mfa/disable"), { code }); setCode(""); qc.invalidateQueries({ queryKey: ["me"] }); toast.warn("MFA disabled"); } catch (e) { toast.err("Could not disable", errorMessage(e)); }
              }}>Disable MFA</button></div>
          </>
        ) : setup ? (
          <>
            <p className="small" style={{ margin: 0 }}>Scan the QR code with an authenticator app (or enter the secret manually), then enter the 6-digit code to confirm.</p>
            <div className="row" style={{ alignItems: "flex-start" }}>
              {qr ? <img src={qr} alt="TOTP enrolment QR code" width={200} height={200} style={{ background: "#fff", borderRadius: 6 }} /> : <Loading />}
              <div className="col">
                <span className="label">Secret</span><code style={{ overflowWrap: "anywhere" }}>{setup.secret}</code>
                <input className="input" style={{ width: 160 }} inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code" aria-label="Verification code" value={code} onChange={(e) => setCode(e.target.value)} />
                <button className="btn primary sm" disabled={code.length < 6} onClick={async () => {
                  try { await post(apiPath("/api/v1/auth/mfa/enable"), { code }); setSetup(null); setCode(""); qc.invalidateQueries({ queryKey: ["me"] }); toast.ok("MFA enabled"); } catch (e) { toast.err("Code not accepted", errorMessage(e)); }
                }}>Confirm and enable</button>
              </div>
            </div>
          </>
        ) : (
          <div><button className="btn" onClick={async () => { try { setSetup(await post(apiPath("/api/v1/auth/mfa/setup"))); } catch (e) { toast.err("Setup failed", errorMessage(e)); } }}>Set up authenticator app</button></div>
        )}
      </section>
    </div>
  );
}

function Sessions() {
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => get<{ items: { id: string; created_at: string; last_seen_at: string; ip: string | null; user_agent: string | null; current: boolean }[] }>(apiPath("/api/v1/auth/sessions")) });
  if (q.isLoading) return <Loading />;
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Device</th><th>IP</th><th>Signed in</th><th>Last active</th><th aria-label="Actions" /></tr></thead>
      <tbody>{q.data?.items.map((s) => (
        <tr key={s.id}><td className="small" style={{ maxWidth: 320 }}>{s.user_agent ?? "unknown"}{s.current && <span className="badge accent" style={{ marginLeft: 6 }}>this device</span>}</td>
          <td className="mono small">{s.ip ?? "—"}</td><td className="small">{new Date(s.created_at).toLocaleString()}</td><td className="small">{new Date(s.last_seen_at).toLocaleString()}</td>
          <td>{!s.current && <button className="btn ghost sm" onClick={async () => { try { await del(apiPath("/api/v1/auth/sessions/{session_id}", { session_id: s.id })); q.refetch(); } catch (e) { toast.err("Could not revoke", errorMessage(e)); } }}><LogOut size={12} />Sign out</button>}</td></tr>
      ))}</tbody>
    </table></div>
  );
}

function MyActivity() {
  const q = useQuery({ queryKey: ["my-audit"], queryFn: () => get<{ items: { id: string; at: string; action: string; target_type: string | null; details: unknown }[] }>(apiPath("/api/v1/me/audit")) });
  if (q.isLoading) return <Loading />;
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>When</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
      <tbody>{q.data?.items.map((e) => <tr key={e.id}><td className="small nowrap">{new Date(e.at).toLocaleString()}</td><td className="mono small">{e.action}</td><td className="small">{e.target_type ?? ""}</td><td className="tiny faint">{e.details ? JSON.stringify(e.details).slice(0, 160) : ""}</td></tr>)}</tbody>
    </table></div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Settings" }]}><Settings /></AppShell>; }
