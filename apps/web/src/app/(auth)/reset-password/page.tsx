"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { apiPath, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Field } from "@/components/ui";

function Reset() {
  const token = useSearchParams().get("token") ?? "";
  const [pw, setPw] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <AuthCard title="Choose a new password">
      {done ? (
        <div className="alert ok">Password updated. All other sessions were signed out. <Link href="/login">Sign in</Link></div>
      ) : (
        <form className="col gap-12" onSubmit={async (e) => {
          e.preventDefault();
          try { await post(apiPath("/api/v1/auth/reset-password"), { token, password: pw }); setDone(true); } catch (err) { setError(err); }
        }}>
          <Field label="New password" htmlFor="pw" hint="At least 10 characters">
            <input id="pw" className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          {error ? <ErrorBox error={error} /> : null}
          <button className="btn primary" disabled={pw.length < 10 || !token}>Update password</button>
        </form>
      )}
    </AuthCard>
  );
}

export default function Page() { return <Suspense><Reset /></Suspense>; }
