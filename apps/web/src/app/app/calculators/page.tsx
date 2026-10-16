"use client";

import { useQuery } from "@tanstack/react-query";
import { Calculator } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { apiPath, errorMessage, get, post } from "@/api/client";
import { AppShell } from "@/components/AppShell";
import { ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { fmt } from "@/lib/mesh";

interface CalcDef { key: string; name: string; domain: string; doc: string; parameters: { name: string; required: boolean; default: unknown }[] }
interface CalcOut { key: string; inputs: Record<string, unknown>; outputs: Record<string, unknown>; intermediate: Record<string, unknown>; formula: string;
  assumptions: string[]; warnings: string[]; references: string[]; status: string }

/** Worked example inputs (editable); structured inputs are entered as JSON. */
const EXAMPLES: Record<string, Record<string, unknown>> = {
  unit_convert: { value: 100, from_unit: "N*m", to_unit: "lbf*ft" },
  shaft_power: { torque_nm: 250, speed_rpm: 3000 },
  shaft_torsion: { torque_nm: 250, diameter_mm: 25, bore_mm: 0, length_mm: 300, shear_modulus_mpa: 79300 },
  gear_train: { teeth: [17, 51, 19, 57], input_speed_rpm: 3000, input_torque_nm: 50, efficiency_per_mesh: 0.98 },
  helical_spring: { wire_d_mm: 4, mean_d_mm: 32, active_coils: 8, shear_modulus_mpa: 79300, force_n: 200 },
  beam: { case: "cantilever_end_load", length_mm: 500, load: 1000, youngs_modulus_mpa: 200000, section: { type: "rectangle", b_mm: 20, h_mm: 40 } },
  thermal_resistance: { layers: [{ type: "slab", thickness_mm: 2, k_w_mk: 160, area_mm2: 10000 }, { type: "convection", h_w_m2k: 50, area_mm2: 10000 }], t_hot_c: 60, t_cold_c: 25 },
  battery_pack: { cells_series: 96, cells_parallel: 4, cell_nominal_v: 3.6, cell_capacity_ah: 5, cell_max_continuous_a: 10, cell_mass_g: 68, cell_resistance_mohm: 20 },
  iso_fit: { nominal_mm: 25, hole: "H7", shaft: "g6" },
  tolerance_stack: { contributors: [{ name: "housing", nominal: 50, plus: 0.1, minus: 0.1, direction: 1 }, { name: "bearing", nominal: 15, plus: 0, minus: 0.12, direction: -1 },
    { name: "spacer", nominal: 34.5, plus: 0.05, minus: 0.05, direction: -1 }], spec_min: 0.2, spec_max: 0.8 },
  mass_from_volume: { volume_mm3: 125000, density_kg_m3: 2700 },
};

function show(v: unknown): string {
  if (typeof v === "number") return fmt(v, 6);
  if (Array.isArray(v)) return v.map(show).join(", ");
  if (v && typeof v === "object") return JSON.stringify(v);
  return String(v ?? "—");
}

function Calculators() {
  const list = useQuery({ queryKey: ["calculators"], queryFn: () => get<{ items: CalcDef[]; note: string }>(apiPath("/api/v1/calculators")), staleTime: Infinity });
  const [key, setKey] = useState("shaft_power");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [out, setOut] = useState<CalcOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const def = list.data?.items.find((c) => c.key === key);
  useEffect(() => {
    if (!def) return;
    const ex = EXAMPLES[key] ?? {};
    setInputs(Object.fromEntries(def.parameters.map((p) => {
      const v = ex[p.name] ?? p.default;
      return [p.name, v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v, null, 1) : String(v)];
    })));
    setOut(null); setErr(null);
  }, [def, key]);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = {};
      for (const p of def!.parameters) {
        const s = (inputs[p.name] ?? "").trim();
        if (!s) continue;
        if (/^[[{]/.test(s)) body[p.name] = JSON.parse(s);
        else body[p.name] = /^-?\d*\.?\d+([eE][-+]?\d+)?$/.test(s) ? Number(s) : s;
      }
      setOut(await post<CalcOut>(apiPath("/api/v1/calculators/{key}", { key }), body));
    } catch (e) { setOut(null); setErr(e instanceof SyntaxError ? `Invalid JSON input: ${e.message}` : errorMessage(e)); } finally { setBusy(false); }
  };
  if (list.isLoading) return <Loading />;
  if (list.error) return <div className="page"><ErrorBox error={list.error} /></div>;
  return (
    <div className="page" style={{ maxWidth: 1300 }}>
      <h1>Engineering calculators</h1>
      <p className="muted small">{list.data?.note}</p>
      <div style={{ display: "grid", gridTemplateColumns: "240px minmax(0,1fr) minmax(0,1fr)", gap: 16, marginTop: 12 }}>
        <nav className="col gap-4" aria-label="Calculators">
          {list.data?.items.map((c) => (
            <button key={c.key} className="btn ghost" style={{ justifyContent: "flex-start", background: key === c.key ? "var(--accent-soft)" : undefined, color: key === c.key ? "var(--accent)" : undefined }}
              aria-pressed={key === c.key} onClick={() => setKey(c.key)}><Calculator size={13} />{c.name}</button>
          ))}
        </nav>
        {def && (
          <form className="card col gap-12" onSubmit={(e) => { e.preventDefault(); void run(); }}>
            <div><h2>{def.name}</h2><div className="faint small">{def.domain.replace(/_/g, " ")}</div></div>
            {def.doc && <p className="small muted" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{def.doc}</p>}
            {def.parameters.map((p) => {
              const v = inputs[p.name] ?? "";
              const structured = /^[[{]/.test(v.trim()) || ["teeth", "section", "layers", "contributors"].includes(p.name);
              return (
                <div className="field" key={p.name}>
                  <label htmlFor={`c-${p.name}`}>{p.name.replace(/_/g, " ")}{p.required ? " *" : ""}</label>
                  {structured
                    ? <textarea id={`c-${p.name}`} className="textarea mono" rows={Math.min(8, v.split("\n").length + 1)} value={v} onChange={(e) => setInputs({ ...inputs, [p.name]: e.target.value })} />
                    : <input id={`c-${p.name}`} className="input mono" value={v} placeholder={p.required ? "required" : "optional"} onChange={(e) => setInputs({ ...inputs, [p.name]: e.target.value })} />}
                </div>
              );
            })}
            <button className="btn primary" disabled={busy}>{busy && <span className="spinner" />}Calculate</button>
            {err && <div className="alert err small" role="alert">{err}</div>}
          </form>
        )}
        <section className="col gap-12" aria-live="polite">
          {out ? (
            <>
              <div className="card">
                <div className="card-title"><h3>Results</h3><StatusBadge status={out.status} /></div>
                <dl className="kv">{Object.entries(out.outputs).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{show(v)}</dd></Fragment>)}</dl>
                {Object.keys(out.intermediate).length > 0 && (
                  <details style={{ marginTop: 8 }}><summary className="small muted">Intermediate values</summary>
                    <dl className="kv" style={{ marginTop: 6 }}>{Object.entries(out.intermediate).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{show(v)}</dd></Fragment>)}</dl>
                  </details>
                )}
              </div>
              <div className="card">
                <h3 style={{ marginBottom: 6 }}>Formula</h3>
                <code style={{ whiteSpace: "pre-wrap" }}>{out.formula}</code>
                {out.references.length > 0 && <div className="small muted" style={{ marginTop: 8 }}>References: {out.references.join("; ")}</div>}
              </div>
              {out.warnings.length > 0 && <div className="alert warn small"><ul style={{ margin: 0, paddingLeft: 18 }}>{out.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}
              <div className="card"><h3 style={{ marginBottom: 6 }}>Assumptions</h3><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{out.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>
            </>
          ) : <div className="empty">Enter inputs and calculate. Each result lists its formula, assumptions and limits.</div>}
        </section>
      </div>
    </div>
  );
}

export default function Page() { return <AppShell crumbs={[{ href: "/app", label: "Dashboard" }, { label: "Calculators" }]}><Calculators /></AppShell>; }
