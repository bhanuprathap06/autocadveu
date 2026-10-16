"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPath, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Field } from "@/components/ui";

export default function RegisterPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [form, setForm] = useState({ display_name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const weak = form.password.length > 0 && form.password.length < 10;

  return (
    <AuthCard title="Create your account" footer={<>Already registered? <Link href="/login">Sign in</Link></>}>
      <form className="col gap-12" noValidate onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true); setError(null);
        try {
          await post(apiPath("/api/v1/auth/register"), form);
          await qc.invalidateQueries({ queryKey: ["me"] });
          router.push("/app?welcome=1");
        } catch (err) { setError(err); } finally { setBusy(false); }
      }}>
        <Field label="Name" htmlFor="name"><input id="name" className="input" autoComplete="name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
        <Field label="Work email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Password" htmlFor="pw" hint="At least 10 characters; avoid common passwords." error={weak ? "Password must be at least 10 characters" : null}>
          <input id="pw" className="input" type="password" autoComplete="new-password" aria-invalid={weak} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        {error ? <ErrorBox error={error} /> : null}
        <button className="btn primary lg" disabled={busy || weak || !form.email || !form.display_name}>{busy && <span className="spinner" />}Create account</button>
        <p className="faint tiny">We will send a verification email. Engineering files you upload stay private to your workspace and are never sent to external AI services without your explicit consent.</p>
      </form>
    </AuthCard>
  );
}
