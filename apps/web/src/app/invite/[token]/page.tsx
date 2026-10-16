"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, get, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { RequireAuth } from "@/lib/auth";

function Invite() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [error, setError] = useState<unknown>(null);
  const q = useQuery({
    queryKey: ["invite", token],
    queryFn: () => get<{ invitation: { role: string; state: string; expires_at: string; email: string }; room: { name: string }; invited_by: string; email_matches: boolean }>(
      apiPath("/api/v1/invitations/{token}", { token })),
  });
  return (
    <AuthCard title="Room invitation">
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="col gap-12">
          <p><strong>{q.data.invited_by}</strong> invited you to the private room <strong>{q.data.room.name}</strong> as <span className="badge accent">{q.data.invitation.role}</span>.</p>
          <div className="row small muted">State: <StatusBadge status={q.data.invitation.state === "pending" ? "queued" : q.data.invitation.state} label={q.data.invitation.state} /> · expires {new Date(q.data.invitation.expires_at).toLocaleString()}</div>
          {!q.data.email_matches && <div className="alert warn">This invitation was issued to a different email address than the account you are signed in with. Sign in with the invited address to accept it.</div>}
          {error ? <ErrorBox error={error} /> : null}
          <button className="btn primary" disabled={q.data.invitation.state !== "pending" || !q.data.email_matches} onClick={async () => {
            try {
              const r = await post<{ room: { id: string } }>(apiPath("/api/v1/invitations/{token}/accept", { token }));
              router.push(`/app/rooms/${r.room.id}`);
            } catch (e) { setError(e); }
          }}>Accept invitation</button>
        </div>
      )}
    </AuthCard>
  );
}

export default function Page() { return <RequireAuth><Invite /></RequireAuth>; }
