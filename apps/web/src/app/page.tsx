import { Activity, Boxes, Calculator, FileText, Gauge, Lock, PencilRuler, Sparkles, Users } from "lucide-react";
import Link from "next/link";
import { Logo } from "@/components/AppShell";
import { FEATURES } from "@/content/featureStatus";
import { Showcase } from "./Showcase";

const TILES = [
  { icon: <PencilRuler size={18} />, title: "Parametric CAD on OCCT", body: "Constraint-driven sketches, full feature history, persistent face naming, parameters and expressions. Exact B-rep geometry with verified STEP/IGES export." },
  { icon: <Boxes size={18} />, title: "Assemblies & kinematics", body: "Mates with live DOF reporting, exact interference checks, exploded views, BOM, and quasi-static motion studies with per-step collision detection." },
  { icon: <Gauge size={18} />, title: "Real solvers", body: "Linear static and steady-state thermal FEA with CalculiX on Gmsh tetrahedral meshes, equilibrium checks and convergence studies. CFD is marked as an external integration, never faked." },
  { icon: <Activity size={18} />, title: "Sourced component library", body: "Standard-derived fasteners, bearings and cell formats with provenance, licence and completeness. Missing manufacturer data stays missing." },
  { icon: <FileText size={18} />, title: "Drawings & GD&T", body: "Hidden-line projections, automatic dimensions, hole callouts, ISO 1101/ASME Y14.5 frame checks, title blocks, BOM balloons; PDF, DXF and SVG output." },
  { icon: <Calculator size={18} />, title: "Engineering calculators", body: "Shafts, gears, springs, beams, thermal paths, battery packs, ISO 286 fits and tolerance stacks — each with formulas, assumptions and units." },
  { icon: <Users size={18} />, title: "Private collaboration rooms", body: "Email-bound single-use invitations, owner/editor/reviewer/viewer roles, pinned comments, presence and immutable revision history." },
  { icon: <Sparkles size={18} />, title: "Accountable AI assistant", body: "Turns problem statements into requirements, concepts and parametric proposals. Every claim is classified; nothing reaches your model without validation and approval." },
  { icon: <Lock size={18} />, title: "Security first", body: "Argon2id, server-side sessions, CSRF protection, TOTP MFA, rate limits, isolated kernel workers and an audit trail. Your CAD never leaves without consent." },
];

export default function Landing() {
  const verified = FEATURES.filter((f) => f.status === "implemented_and_verified").length;
  return (
    <div className="landing" style={{ minHeight: "100vh" }}>
      <header className="row" style={{ maxWidth: 1280, margin: "0 auto", padding: "16px 24px" }}>
        <Logo size={26} /><strong style={{ fontSize: 15 }}>AutoForge Engineering Studio</strong>
        <div className="grow" />
        <nav className="row" aria-label="Site">
          <Link href="/status" className="btn ghost sm">Feature status</Link>
          <Link href="/login" className="btn ghost sm">Sign in</Link>
          <Link href="/register" className="btn primary sm">Create account</Link>
        </nav>
      </header>
      <main id="main">
        <section className="hero">
          <div className="col gap-16">
            <span className="badge accent" style={{ alignSelf: "flex-start" }}>Automotive CAD · simulation · engineering</span>
            <h1>Design, verify and review vehicle components in the browser.</h1>
            <p className="lead">
              A parametric modeller on a real B-rep kernel, assemblies with mates and motion, CalculiX simulation, associative
              drawings and a sourced component library — with every result traceable to the solver or source that produced it.
            </p>
            <div className="row wrap">
              <Link href="/register" className="btn primary lg">Start a project</Link>
              <Link href="/app" className="btn lg">Open the studio</Link>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              {verified} of {FEATURES.length} listed capabilities are implemented and covered by automated tests against the real engines.
              The rest are labelled with their limits on the <Link href="/status">feature status</Link> page.
            </p>
          </div>
          <Showcase />
        </section>
        <section style={{ maxWidth: 1280, margin: "0 auto", padding: "16px 24px 48px" }}>
          <div className="grid grid-3">
            {TILES.map((t) => (
              <article key={t.title} className="feature-tile">
                <span style={{ color: "var(--accent)" }}>{t.icon}</span>
                <h3>{t.title}</h3>
                <p className="small muted" style={{ margin: 0 }}>{t.body}</p>
              </article>
            ))}
          </div>
          <div className="card" style={{ marginTop: 24 }}>
            <h2 style={{ marginBottom: 8 }}>Engineering results you can defend</h2>
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
              <li>Simulation reports state the solver, version, mesh statistics, convergence and every assumption — or the run is reported as failed.</li>
              <li>Catalog values carry their source, basis and verification status; comparisons show “missing” instead of guessing.</li>
              <li>AI output is labelled as fact, calculation, hypothesis, recommendation or unverified suggestion, and must pass schema and kernel validation.</li>
              <li>Results are decision support for qualified engineers, not certification. Safety-critical parts require physical validation.</li>
            </ul>
          </div>
        </section>
      </main>
      <footer className="faint small" style={{ textAlign: "center", padding: "24px 16px", borderTop: "1px solid var(--border)" }}>
        AutoForge Engineering Studio · OCCT · CalculiX · Gmsh · <Link href="/status">Feature status</Link>
      </footer>
    </div>
  );
}
