"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { ApiError, get, post } from "@/api/client";
import type { Me } from "@/api/types";

export function useMe() {
  return useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: () => get<Me>("/api/v1/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogout() {
  const qc = useQueryClient();
  const router = useRouter();
  return async () => {
    try {
      await post("/api/v1/auth/logout");
    } finally {
      qc.clear();
      router.push("/login");
    }
  };
}

/** Client-side guard: redirects to /login (or MFA) when the session is missing. Server enforces all access. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (me.error && me.error.status === 401) {
      const next = encodeURIComponent(path ?? "/app");
      router.replace(me.error.code === "mfa_required" ? `/login?mfa=1&next=${next}` : `/login?next=${next}`);
    }
  }, [me.error, path, router]);
  if (me.isLoading)
    return (
      <div className="row" style={{ height: "100vh", justifyContent: "center" }} role="status">
        <span className="spinner" /> <span className="muted">Loading session…</span>
      </div>
    );
  if (me.error) {
    if (me.error.status === 401) return null;
    return (
      <div className="page">
        <div className="alert err" role="alert">
          Could not load your session: {me.error.message}
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
