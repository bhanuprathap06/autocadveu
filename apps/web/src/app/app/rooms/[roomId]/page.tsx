"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Copy, FolderKanban, Mail, Trash2, UserMinus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, del, errorMessage, get, post, put } from "@/api/client";
import type { Project } from "@/api/types";
import { AppShell } from "@/components/AppShell";
import { Confirm, ErrorBox, Field, Loading, StatusBadge, Tabs } from "@/components/ui";
import { toast } from "@/components/ui/toast";
import { useMe } from "@/lib/auth";

interface RoomResp {
  room: { id: string; name: string; description: string; role: string; archived: boolean };
  members: { user_id: string; display_name: string; email: string | null; role: string }[];
  projects: { id: string; name: string; domain: string | null }[];
  invitations?: { id: string; email: string; role: string; state: string; expires_at: string; created_at: string }[];
}

function Room() {
  const { roomId } = useParams<{ roomId: string }>();
  const router = useRouter();
  const me = useMe();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"projects" | "members" | "invitations" | "audit">("projects");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("reviewer");
  const [days, setDays] = useState(7);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [share, setShare] = useState("");
  const [leave, setLeave] = useState(false);
  const key = ["room", roomId];
  const q = useQuery({ queryKey: key, queryFn: () => get<RoomResp>(apiPath("/api/v1/rooms/{room_id}", { room_id: roomId })) });
  const myProjects = useQuery({ queryKey: ["projects", ""], queryFn: () => get<{ items: Project[] }>(apiPath("/api/v1/projects", {}, { limit: 200 })), enabled: q.data?.room.role === "owner" });
  const audit = useQuery({ queryKey: ["room-audit", roomId], queryFn: () => get<{ items: { id: string; at: string; actor: string | null; action: string; details: unknown }[] }>(apiPath("/api/v1/rooms/{room_id}/audit", { room_id: roomId })), enabled: tab === "audit" });
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  if (q.isLoading) return <Loading />;
  if (q.error) return <div className="page"><ErrorBox error={q.error} /></div>;
  const { room, members, projects, invitations } = q.data!;
  const owner = room.role === "owner";
  return (
    <div className="page col gap-16">
      <div className="row between wrap">
        <div><h1>{room.name}</h1><div className="small muted">{room.description || "Private room"} · your role: <strong>{room.role}</strong>{room.archived && <span className="badge warn" style={{ marginLeft: 6 }}>archived</span>}</div></div>
        <div className="row">
          {owner && !room.archived && <button className="btn sm" onClick={async () => { try { await post(apiPath("/api/v1/rooms/{room_id}/archive", { room_id: roomId })); refresh(); } catch (e) { toast.err("Archive failed", errorMessage(e)); } }}><Archive size={13} />Archive</button>}
          <button className="btn sm danger" onClick={() => setLeave(true)}><UserMinus size={13} />Leave room</button>
        </div>
      </div>
      <Tabs label="Room sections" value={tab} onChange={setTab} tabs={[
        { key: "projects", label: "Shared projects", badge: <span className="badge">{projects.length}</span> },
        { key: "members", label: "Members", badge: <span className="badge">{members.length}</span> },
        ...(owner ? [{ key: "invitations" as const, label: "Invitations" }, { key: "audit" as const, label: "Audit log" }] : []),
      ]} />
      {tab === "projects" && (
        <div className="col gap-12">
          {owner && (
            <div className="row">
              <select className="select" style={{ maxWidth: 320 }} aria-label="Project to share" value={share} onChange={(e) => setShare(e.target.value)}>
                <option value="">Share a project you own…</option>
                {myProjects.data?.items.filter((p) => p.role === "owner" && !projects.some((x) => x.id === p.id)).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button className="btn" disabled={!share} onClick={async () => {
                try { await post(apiPath("/api/v1/rooms/{room_id}/projects", { room_id: roomId }), { project_id: share }); setShare(""); refresh(); toast.ok("Project shared", "Room members now access it with their room role."); }
                catch (e) { toast.err("Share failed", errorMessage(e)); }
              }}>Share</button>
            </div>
          )}
          {projects.map((p) => (
            <div key={p.id} className="card tight row">
              <FolderKanban size={16} /><Link href={`/app/projects/${p.id}`} className="grow"><strong>{p.name}</strong></Link>
              <span className="faint small">{(p.domain ?? "").replace(/_/g, " ")}</span>
              {owner && <button className="btn ghost icon sm" aria-label={`Stop sharing ${p.name}`} onClick={async () => {
                try { await del(apiPath("/api/v1/rooms/{room_id}/projects/{project_id}", { room_id: roomId, project_id: p.id })); refresh(); } catch (e) { toast.err("Unshare failed", errorMessage(e)); }
              }}><Trash2 size={12} /></button>}
            </div>
          ))}
          {!projects.length && <div className="faint small">No projects shared yet.</div>}
        </div>
      )}
      {tab === "members" && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Name</th>{owner && <th>Email</th>}<th>Role</th><th aria-label="Actions" /></tr></thead>
          <tbody>{members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name}{m.user_id === me.data?.user.id && <span className="faint"> (you)</span>}</td>
              {owner && <td className="small">{m.email}</td>}
              <td>{owner && m.user_id !== me.data?.user.id ? (
                <select className="select" style={{ width: 120, height: 26 }} aria-label={`Role of ${m.display_name}`} value={m.role} onChange={async (e) => {
                  try { await put(apiPath("/api/v1/rooms/{room_id}/members/{user_id}", { room_id: roomId, user_id: m.user_id }), { role: e.target.value }); refresh(); } catch (err) { toast.err("Role change failed", errorMessage(err)); }
                }}>{["owner", "editor", "reviewer", "viewer"].map((r) => <option key={r}>{r}</option>)}</select>
              ) : <span className="badge">{m.role}</span>}</td>
              <td>{owner && m.user_id !== me.data?.user.id && <button className="btn ghost icon sm" aria-label={`Remove ${m.display_name}`} onClick={async () => {
                try { await del(apiPath("/api/v1/rooms/{room_id}/members/{user_id}", { room_id: roomId, user_id: m.user_id })); refresh(); } catch (e) { toast.err("Remove failed", errorMessage(e)); }
              }}><UserMinus size={12} /></button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {tab === "invitations" && owner && (
        <div className="col gap-12">
          <form className="card col gap-12" onSubmit={async (e) => {
            e.preventDefault();
            try {
              const r = await post<{ dev_link?: string }>(apiPath("/api/v1/rooms/{room_id}/invitations", { room_id: roomId }), { email, role, expires_in_days: days });
              setEmail(""); setDevLink(r.dev_link ?? null); refresh();
              toast.ok("Invitation sent", `Only ${email} can accept it, once, within ${days} days.`);
            } catch (err) { toast.err("Invitation failed", errorMessage(err)); }
          }}>
            <h3>Invite by email</h3>
            <div className="grid grid-3">
              <Field label="Email" htmlFor="ie"><input id="ie" className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
              <Field label="Role" htmlFor="ir"><select id="ir" className="select" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">Viewer — view only</option><option value="reviewer">Reviewer — view and comment</option><option value="editor">Editor — edit, simulate, export</option></select></Field>
              <Field label="Expires after (days)" htmlFor="ix"><input id="ix" className="input num" type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
            </div>
            <div><button className="btn primary" disabled={!email}><Mail size={14} />Send invitation</button></div>
            {devLink && <div className="alert info small"><div className="grow">Development mode: email delivery is simulated. Invitation link: <code style={{ overflowWrap: "anywhere" }}>{devLink}</code></div>
              <button type="button" className="btn ghost icon sm" aria-label="Copy link" onClick={() => navigator.clipboard.writeText(devLink)}><Copy size={12} /></button></div>}
          </form>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Email</th><th>Role</th><th>State</th><th>Expires</th><th aria-label="Actions" /></tr></thead>
            <tbody>{invitations?.map((i) => (
              <tr key={i.id}><td>{i.email}</td><td>{i.role}</td>
                <td><StatusBadge status={i.state === "accepted" ? "ok" : i.state === "pending" ? "queued" : "cancelled"} label={i.state} /></td>
                <td className="small muted">{new Date(i.expires_at).toLocaleString()}</td>
                <td>{i.state === "pending" && <button className="btn ghost sm" onClick={async () => {
                  try { await del(apiPath("/api/v1/rooms/{room_id}/invitations/{inv_id}", { room_id: roomId, inv_id: i.id })); refresh(); } catch (e) { toast.err("Revoke failed", errorMessage(e)); }
                }}>Revoke</button>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      {tab === "audit" && owner && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Details</th></tr></thead>
          <tbody>{audit.data?.items.map((e) => <tr key={e.id}><td className="small nowrap">{new Date(e.at).toLocaleString()}</td><td className="small">{e.actor ?? "system"}</td><td className="mono small">{e.action}</td><td className="tiny faint">{e.details ? JSON.stringify(e.details).slice(0, 200) : ""}</td></tr>)}</tbody>
        </table>{audit.isLoading && <Loading />}</div>
      )}
      <Confirm open={leave} title="Leave room" danger confirmLabel="Leave" message="You will lose access to the projects shared in this room." onClose={() => setLeave(false)}
        onConfirm={async () => {
          try { await del(apiPath("/api/v1/rooms/{room_id}/members/{user_id}", { room_id: roomId, user_id: me.data!.user.id })); router.push("/app/rooms"); }
          catch (e) { toast.err("Could not leave", errorMessage(e)); }
        }} />
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app/rooms", label: "Collaboration rooms" }, { label: "Room" }]}><Room /></AppShell>; }
