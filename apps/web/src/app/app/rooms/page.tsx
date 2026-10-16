"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { apiPath, get, post } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import { Dialog, Empty, ErrorBox, Field, Loading } from "@/components/ui";

interface Room { id: string; name: string; description: string; role: string; archived: boolean; created_at: string }

function Rooms() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const q = useQuery({ queryKey: ["rooms"], queryFn: () => get<{ items: Room[] }>(apiPath("/api/v1/rooms")) });
  return (
    <div className="page col gap-16">
      <div className="row between wrap">
        <div><h1>Collaboration rooms</h1><p className="muted small" style={{ margin: "4px 0 0" }}>Private spaces for sharing projects with named people outside your organisation. Access is by single-use, email-bound invitation only.</p></div>
        <button className="btn primary" onClick={() => setOpen(true)}><Plus size={14} />New room</button>
      </div>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && !q.data.items.length && <Empty icon={<Lock size={28} />} title="No rooms yet">Create a room, share projects into it, then invite reviewers or partners by email.</Empty>}
      <div className="grid grid-3">
        {q.data?.items.map((r) => (
          <Link key={r.id} href={`/app/rooms/${r.id}`} className="card hover" style={{ color: "var(--text)", textDecoration: "none", opacity: r.archived ? 0.6 : 1 }}>
            <div className="row between"><strong><Lock size={13} /> {r.name}</strong><span className="badge">{r.role}</span></div>
            <div className="small muted" style={{ marginTop: 4 }}>{r.description || "No description"}</div>
            {r.archived && <span className="badge warn" style={{ marginTop: 6 }}>archived</span>}
          </Link>
        ))}
      </div>
      <Dialog open={open} onClose={() => setOpen(false)} title="New collaboration room"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={!name.trim()} onClick={async () => {
          try { await post(apiPath("/api/v1/rooms"), { name: name.trim(), description: desc }); setOpen(false); setName(""); setDesc(""); qc.invalidateQueries({ queryKey: ["rooms"] }); }
          catch (e) { setErr(e); }
        }}>Create room</button></>}>
        <Field label="Room name" htmlFor="rn"><input id="rn" className="input" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description" htmlFor="rd"><textarea id="rd" className="textarea" rows={3} value={desc} maxLength={2000} onChange={(e) => setDesc(e.target.value)} /></Field>
        {err ? <ErrorBox error={err} /> : null}
      </Dialog>
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Collaboration rooms" }]}><Rooms /></AppShell>; }
