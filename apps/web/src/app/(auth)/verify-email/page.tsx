"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { apiPath, post } from "@/api/client";
import { AuthCard } from "@/components/AuthCard";
import { ErrorBox, Loading } from "@/components/ui";

function Verify() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "done" | "error">("working");
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!token) { setState("error"); setError(new Error("The verification link is incomplete.")); return; }
    post(apiPath("/api/v1/auth/verify-email"), { token }).then(() => setState("done")).catch((e) => { setError(e); setState("error"); });
  }, [token]);
  return (
    <AuthCard title="Email verification">
      {state === "working" && <Loading label="Verifying…" />}
      {state === "done" && <div className="alert ok">Your email address is verified. <Link href="/app">Continue to the dashboard</Link></div>}
      {state === "error" && <><ErrorBox error={error} /><p className="small muted">Sign in and request a new link from Settings.</p></>}
    </AuthCard>
  );
}

export default function Page() { return <Suspense><Verify /></Suspense>; }
