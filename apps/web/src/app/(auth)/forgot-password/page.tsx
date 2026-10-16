"use client";

import Link from "next/link";
import { useState } from "react";
import { apiPath, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Field } from "@/components/ui";

export default function ForgotPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <AuthCard title="Reset your password" footer={<Link href="/login">Back to sign in</Link>}>
      {sent ? (
        <div className="alert ok">If an account exists for that address, a reset link valid for one hour has been sent.</div>
      ) : (
        <form className="col gap-12" onSubmit={async (e) => {
          e.preventDefault();
          try { await post(apiPath("/api/v1/auth/forgot-password"), { email }); setSent(true); } catch (err) { setError(err); }
        }}>
          <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          {error ? <ErrorBox error={error} /> : null}
          <button className="btn primary" disabled={!email}>Send reset link</button>
        </form>
      )}
    </AuthCard>
  );
}
