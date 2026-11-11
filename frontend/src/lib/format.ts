export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  return usd < 1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`; // LLM calls cost fractions of a cent: keep the precision
}

export function timeAgo(iso: string, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function compactNumber(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 100) return v.toFixed(0);
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  degraded: "Completed with fallbacks",
  failed: "Failed",
  needs_clarification: "Needs clarification",
  cancelled: "Cancelled",
};
