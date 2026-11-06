import type { ChartSeries } from "./types";

export type Row = Record<string, number | string | boolean | undefined>;

/** Merge several series into Recharts rows: one row per x value, one key per series. */
export function pivotSeries(series: ChartSeries[]): { rows: Row[]; flagged: Set<string> } {
  const byX = new Map<string, Row>();
  const flagged = new Set<string>();
  for (const s of series) {
    for (const p of s.data) {
      const key = String(p.x);
      const row = byX.get(key) ?? { x: p.x };
      row[s.name] = p.y;
      byX.set(key, row);
      if (p.flag) flagged.add(`${s.name}|${key}`);
    }
  }
  const rows = [...byX.values()].sort((a, b) => (typeof a.x === "number" && typeof b.x === "number" ? a.x - b.x : String(a.x).localeCompare(String(b.x))));
  return { rows, flagged };
}

export const SERIES_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];
export const colorFor = (i: number) => SERIES_COLORS[i % SERIES_COLORS.length] as string;
