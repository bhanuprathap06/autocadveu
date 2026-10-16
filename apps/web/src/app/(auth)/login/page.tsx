"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ApiError, apiPath, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Field } from "@/components/ui";

function safeNext(n: string | null): string {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/app";
}

function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [stage, setStage] = useState<"password" | "mfa">(params.get("mfa") ? "mfa" : "password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const next = safeNext(params.get("next"));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (stage === "password") {
        const r = await post<{ mfa_required: boolean }>(apiPath("/api/v1/auth/login"), { email, password });
        if (r.mfa_required) { setStage("mfa"); return; }
      } else {
        await post(apiPath("/api/v1/auth/mfa/verify"), { code });
      }
      await qc.invalidateQueries({ queryKey: ["me"] });
      router.push(next);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title={stage === "password" ? "Sign in" : "Two-factor verification"}
      footer={<>No account? <Link href="/register">Create one</Link> · <Link href="/forgot-password">Forgot password?</Link></>}>
      <form className="col gap-12" onSubmit={submit} noValidate>
        {stage === "password" ? (
          <>
            <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <Field label="Password" htmlFor="password"><input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          </>
        ) : (
          <Field label="6-digit code from your authenticator app" htmlFor="code">
            <input id="code" className="input num" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        )}
        {error ? <ErrorBox error={error} /> : null}
        {error instanceof ApiError && error.code === "rate_limited" && <div className="muted small">Too many attempts. Wait a few minutes before trying again.</div>}
        <button className="btn primary lg" type="submit" disabled={busy}>{busy ? <span className="spinner" /> : null}{stage === "password" ? "Sign in" : "Verify"}</button>
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
