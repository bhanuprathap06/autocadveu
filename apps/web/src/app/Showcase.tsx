"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import manifest from "../../public/models/manifest.json";

const ShowcaseViewer = dynamic(() => import("@/components/viewer/ShowcaseViewer").then((m) => m.ShowcaseViewer), {
  ssr: false, loading: () => <div className="skeleton" style={{ height: "100%" }} />,
});

export function Showcase() {
  const [i, setI] = useState(0);
  const m = manifest.models[i];
  return (
    <div className="col gap-8">
      <div className="hero-view">
        <ShowcaseViewer url={`/models/${m.file}`} label={`${m.name}, rendered from kernel output`} />
        <div className="vp-status" style={{ maxWidth: "calc(100% - 16px)" }}>
          <strong style={{ color: "var(--text)" }}>{m.name}</strong> · {m.features} features · {m.parameters} parameters ·
          volume {(m.volume_mm3 / 1000).toFixed(1)} cm³ · SHA-256 {m.sha256.slice(0, 10)}…
        </div>
      </div>
      <div className="row wrap" role="tablist" aria-label="Showcase models">
        {manifest.models.map((x, k) => (
          <button key={x.key} role="tab" aria-selected={k === i} className="btn sm" aria-pressed={k === i} onClick={() => setI(k)}>{x.name}</button>
        ))}
      </div>
      <p className="faint tiny" style={{ margin: 0 }}>
        These are starter templates rebuilt from their parametric definitions and tessellated by {manifest.kernel} at build time
        (scripts/export_showcase.py). They are illustrative geometry, not validated production parts.
      </p>
    </div>
  );
}
