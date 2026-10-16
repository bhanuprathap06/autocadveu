"use client";

import { useState } from "react";
import { fmt } from "@/lib/mesh";

export interface Series { name: string; color: string; points: [number, number][] }

/** Minimal accessible SVG line chart (no external dependency). Data table fallback via <details>. */
export function LineChart({ series, xLabel, yLabel, height = 200, markers }: {
  series: Series[]; xLabel: string; yLabel: string; height?: number; markers?: { x: number; label: string; color?: string }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, H = height, pad = { l: 56, r: 12, t: 10, b: 34 };
  const all = series.flatMap((s) => s.points);
  if (!all.length) return <div className="faint small">No data</div>;
  let [x0, x1] = [Math.min(...all.map((p) => p[0])), Math.max(...all.map((p) => p[0]))];
  let [y0, y1] = [Math.min(...all.map((p) => p[1])), Math.max(...all.map((p) => p[1]))];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const yPad = (y1 - y0) * 0.08;
  y0 -= yPad; y1 += yPad;
  const sx = (x: number) => pad.l + ((x - x0) / (x1 - x0)) * (W - pad.l - pad.r);
  const sy = (y: number) => H - pad.b - ((y - y0) / (y1 - y0)) * (H - pad.t - pad.b);
  const ticks = (a: number, b: number, n = 5) => Array.from({ length: n + 1 }, (_, i) => a + ((b - a) * i) / n);
  const xs = series[0]?.points.map((p) => p[0]) ?? [];
  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${yLabel} versus ${xLabel}`}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const x = x0 + (((e.clientX - r.left) / r.width) * W - pad.l) / (W - pad.l - pad.r) * (x1 - x0);
          let best = 0;
          xs.forEach((v, i) => { if (Math.abs(v - x) < Math.abs(xs[best] - x)) best = i; });
          setHover(xs.length ? best : null);
        }} onMouseLeave={() => setHover(null)}>
        {ticks(y0, y1).map((t, i) => (
          <g key={`y${i}`}>
            <line x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} stroke="#1c242e" />
            <text x={pad.l - 6} y={sy(t)} fontSize={10} fill="#8a95a5" textAnchor="end" dominantBaseline="middle">{fmt(t, 3)}</text>
          </g>
        ))}
        {ticks(x0, x1).map((t, i) => (
          <text key={`x${i}`} x={sx(t)} y={H - pad.b + 14} fontSize={10} fill="#8a95a5" textAnchor="middle">{fmt(t, 3)}</text>
        ))}
        <text x={(pad.l + W - pad.r) / 2} y={H - 4} fontSize={11} fill="#a3adbb" textAnchor="middle">{xLabel}</text>
        <text x={12} y={(pad.t + H - pad.b) / 2} fontSize={11} fill="#a3adbb" textAnchor="middle" transform={`rotate(-90 12 ${(pad.t + H - pad.b) / 2})`}>{yLabel}</text>
        {markers?.map((m, i) => (
          <g key={i}><line x1={sx(m.x)} x2={sx(m.x)} y1={pad.t} y2={H - pad.b} stroke={m.color ?? "#f0585d"} strokeDasharray="3 3" />
            <text x={sx(m.x) + 3} y={pad.t + 10} fontSize={9} fill={m.color ?? "#f0585d"}>{m.label}</text></g>
        ))}
        {series.map((s) => (
          <polyline key={s.name} fill="none" stroke={s.color} strokeWidth={1.8} points={s.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")} />
        ))}
        {hover !== null && xs[hover] !== undefined && (
          <g>
            <line x1={sx(xs[hover])} x2={sx(xs[hover])} y1={pad.t} y2={H - pad.b} stroke="#45505e" />
            {series.map((s) => s.points[hover] && <circle key={s.name} cx={sx(s.points[hover][0])} cy={sy(s.points[hover][1])} r={3} fill={s.color} />)}
          </g>
        )}
      </svg>
      <figcaption className="row wrap small" style={{ gap: 12 }}>
        {series.map((s) => <span key={s.name} className="row gap-4"><span style={{ width: 10, height: 3, background: s.color, display: "inline-block" }} />{s.name}</span>)}
        {hover !== null && <span className="num muted">{xLabel} = {fmt(xs[hover], 4)}: {series.map((s) => `${s.name} ${fmt(s.points[hover]?.[1], 4)}`).join(" · ")}</span>}
      </figcaption>
      <details className="small"><summary className="faint">Data table</summary>
        <div className="table-wrap" style={{ maxHeight: 200 }}>
          <table className="table"><thead><tr><th>{xLabel}</th>{series.map((s) => <th key={s.name} className="num">{s.name}</th>)}</tr></thead>
            <tbody>{xs.map((x, i) => <tr key={i}><td className="num">{fmt(x, 5)}</td>{series.map((s) => <td key={s.name} className="num">{fmt(s.points[i]?.[1], 5)}</td>)}</tr>)}</tbody></table>
        </div>
      </details>
    </figure>
  );
}
