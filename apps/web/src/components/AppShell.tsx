"use client";

import {
  Activity, Boxes, Calculator, ClipboardList, Cpu, FolderKanban, Gauge, LayoutDashboard, LogOut, Settings, Shield, Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { RequireAuth, useLogout, useMe } from "@/lib/auth";

function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="6" fill="#111820" stroke="#27b6ec" strokeWidth="1.5" />
      <path d="M8 23 L16 7 L24 23 M11.5 17 H20.5" fill="none" stroke="#27b6ec" strokeWidth="2.4" strokeLinejoin="round" />
      <circle cx="16" cy="23" r="2.2" fill="#e7ebf1" />
    </svg>
  );
}
export { Logo };

const NAV: { href: string; label: string; icon: ReactNode; group?: string; admin?: boolean }[] = [
  { href: "/app", label: "Dashboard", icon: <LayoutDashboard size={16} /> },
  { href: "/app/projects", label: "Projects", icon: <FolderKanban size={16} /> },
  { href: "/app/catalog", label: "Component library", icon: <Boxes size={16} />, group: "Engineering" },
  { href: "/app/compare", label: "Comparison", icon: <ClipboardList size={16} /> },
  { href: "/app/calculators", label: "Calculators", icon: <Calculator size={16} /> },
  { href: "/app/rooms", label: "Collaboration rooms", icon: <Users size={16} />, group: "Team" },
  { href: "/app/settings", label: "Settings", icon: <Settings size={16} /> },
  { href: "/app/admin", label: "Administration", icon: <Shield size={16} />, admin: true },
  { href: "/status", label: "Feature status", icon: <Activity size={16} />, group: "Reference" },
];

export function AppShell({ children, crumbs, actions }: { children: ReactNode; crumbs?: { href?: string; label: string }[]; actions?: ReactNode }) {
  return (
    <RequireAuth>
      <ShellInner crumbs={crumbs} actions={actions}>{children}</ShellInner>
    </RequireAuth>
  );
}

function ShellInner({ children, crumbs, actions }: { children: ReactNode; crumbs?: { href?: string; label: string }[]; actions?: ReactNode }) {
  const path = usePathname() ?? "";
  const me = useMe();
  const logout = useLogout();
  const isAdmin = me.data?.user.is_admin;
  return (
    <div className="shell">
      <nav className="sidenav" aria-label="Primary">
        <div className="brand"><Logo /> AutoForge <span className="badge accent" style={{ marginLeft: "auto" }}>Studio</span></div>
        {NAV.filter((n) => !n.admin || isAdmin).map((n) => (
          <div key={n.href}>
            {n.group && <div className="group">{n.group}</div>}
            <Link href={n.href} aria-current={(n.href === "/app" ? path === "/app" : path.startsWith(n.href)) ? "page" : undefined}>
              {n.icon}{n.label}
            </Link>
          </div>
        ))}
        <div className="grow" />
        <div className="card tight" style={{ fontSize: 12 }}>
          <div className="row"><Cpu size={14} className="muted" /><span className="ellipsis">{me.data?.user.display_name}</span></div>
          <div className="faint tiny ellipsis">{me.data?.user.email}</div>
          {me.data && !me.data.user.email_verified && <div className="badge warn" style={{ marginTop: 6 }}>Email not verified</div>}
          <button className="btn ghost sm" style={{ marginTop: 6, width: "100%" }} onClick={logout}><LogOut size={14} />Sign out</button>
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <nav className="breadcrumbs grow" aria-label="Breadcrumb">
            <Gauge size={14} />
            {(crumbs ?? []).map((c, i) => (
              <span key={i} className="row gap-4">
                {i > 0 && <span className="faint">/</span>}
                {c.href ? <Link href={c.href}>{c.label}</Link> : <strong>{c.label}</strong>}
              </span>
            ))}
          </nav>
          {actions}
        </header>
        <main id="main">{children}</main>
      </div>
    </div>
  );
}
