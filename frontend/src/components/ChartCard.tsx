"use client";

import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { colorFor, pivotSeries } from "@/lib/charts";
import { compactNumber } from "@/lib/format";
import type { ChartSpec } from "@/lib/types";

const AXIS = { stroke: "var(--muted)", fontSize: 12 };
const shorten = (v: string | number) => { const t = String(v); return t.length > 27 ? `${t.slice(0, 26)}…` : t; };

function LineView({ spec }: { spec: ChartSpec }) {
  const { rows, flagged } = pivotSeries(spec.series);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
        <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} allowDecimals={false} tick={AXIS} />
        <YAxis tickFormatter={compactNumber} tick={AXIS} width={52} />
        <Tooltip formatter={(v) => (typeof v === "number" ? v.toLocaleString() : String(v))} />
        {spec.series.length > 1 && <Legend />}
        {spec.range && <ReferenceArea x1={spec.range[0]} x2={spec.range[1]} fill="var(--accent)" fillOpacity={0.08} />}
        {spec.series.map((s, i) => (
          <Line key={s.name} dataKey={s.name} stroke={colorFor(i)} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} connectNulls
            dot={(p: { cx?: number; cy?: number; payload?: { x: number | string } }) => {
              const hit = flagged.has(`${s.name}|${p.payload?.x}`);
              return <circle key={`${s.name}-${p.payload?.x}`} cx={p.cx} cy={p.cy} r={hit ? 6 : 3} fill={hit ? "var(--bad)" : colorFor(i)} stroke="var(--surface)" strokeWidth={1} />;
            }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function BarView({ spec }: { spec: ChartSpec }) {
  const s = spec.series[0];
  const categorical = spec.x.type === "category";
  const data = (s?.data ?? []).map((p) => ({ x: p.x, y: p.y, highlight: p.highlight }));
  return (
    <ResponsiveContainer width="100%" height={categorical ? Math.max(280, data.length * 30) : 260}>
      <BarChart data={data} layout={categorical ? "vertical" : "horizontal"} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
        {categorical ? (
          <>
            <XAxis type="number" tick={AXIS} tickFormatter={compactNumber} />
            <YAxis type="category" dataKey="x" tick={{ ...AXIS, fontSize: 11 }} width={176} interval={0} tickFormatter={shorten} />
          </>
        ) : (
          <>
            <XAxis dataKey="x" tick={AXIS} />
            <YAxis tick={AXIS} width={44} tickFormatter={compactNumber} />
          </>
        )}
        <Tooltip />
        <Bar dataKey="y" radius={[3, 3, 3, 3]}>
          {data.map((d, i) => <Cell key={i} fill={d.highlight ? "var(--accent)" : "var(--c3)"} fillOpacity={d.highlight ? 1 : 0.55} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function ScatterView({ spec }: { spec: ChartSpec }) {
  const data = (spec.series[0]?.data ?? []).map((p) => ({ x: Number(p.x), y: p.y, label: p.label }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 14, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
        <XAxis type="number" dataKey="x" name={spec.x.label} tick={AXIS} height={44} label={{ value: spec.x.label, position: "insideBottom", offset: -4, fill: "var(--muted)", fontSize: 11 }} />
        <YAxis type="number" dataKey="y" name={spec.y.label} tick={AXIS} width={52} label={{ value: spec.y.label, angle: -90, position: "insideLeft", offset: 6, fill: "var(--muted)", fontSize: 11, style: { textAnchor: "middle" } }} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} formatter={(v, n) => [String(v), n]} labelFormatter={() => ""} />
        <Scatter data={data} fill="var(--c1)" />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function ChartCard({ spec }: { spec: ChartSpec }) {
  return (
    <figure className="card chart" aria-labelledby={`${spec.id}-t`}>
      <figcaption id={`${spec.id}-t`}>{spec.title}</figcaption>
      {spec.type === "line" && <LineView spec={spec} />}
      {spec.type === "bar" && <BarView spec={spec} />}
      {spec.type === "scatter" && <ScatterView spec={spec} />}
      <p className="small muted">{spec.note || spec.y.label}{spec.sources.length ? ` · Source: ${spec.sources.join(", ")}` : ""}</p>
      <details>
        <summary className="small">View data table</summary>
        <table className="data small">
          <thead><tr><th>{spec.x.label}</th>{spec.series.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
          <tbody>
            {pivotSeries(spec.series).rows.map((r) => (
              <tr key={String(r.x)}><td>{String(r.x)}</td>{spec.series.map((s) => <td key={s.name}>{r[s.name] == null ? "" : Number(r[s.name]).toLocaleString()}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
