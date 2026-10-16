import Link from "next/link";
import type { ReactNode } from "react";
import { Logo } from "./AppShell";

export function AuthCard({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="blueprint" style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <main id="main" className="card" style={{ width: "min(420px, 100%)", padding: 28 }}>
        <Link href="/" className="row" style={{ color: "var(--text)", marginBottom: 18 }}>
          <Logo size={26} /> <strong style={{ fontSize: 15 }}>AutoForge Engineering Studio</strong>
        </Link>
        <h1 style={{ fontSize: 20, marginBottom: 14 }}>{title}</h1>
        {children}
        {footer && <div className="muted small" style={{ marginTop: 16 }}>{footer}</div>}
      </main>
    </div>
  );
}
