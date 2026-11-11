import type { AgentEvent, AnalysisRecord, AnalysisSummary, ProviderStatus } from "./types";

// An empty NEXT_PUBLIC_API_URL means the backend serves this page, so the API is on the same origin.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

function wsBase(): string {
  if (API_URL) return API_URL.replace(/^http/, "ws");
  return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: { "content-type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError(0, "network_error", `Cannot reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!res.ok) {
    let body: { error?: { code?: string; message?: string; details?: unknown } } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      res.status,
      body.error?.code ?? "http_error",
      body.error?.message ?? `Request failed (${res.status})`,
      body.error?.details,
    );
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  samples: () => request<{ queries: string[] }>("/api/samples"),
  providers: () => request<{ providers: ProviderStatus[] }>("/api/providers"),
  createAnalysis: (query: string, simulate_failures: string[] = []) =>
    request<{ id: string; status: string }>("/api/analyses", {
      method: "POST",
      body: JSON.stringify({ query, simulate_failures }),
    }),
  listAnalyses: () => request<{ items: AnalysisSummary[] }>("/api/analyses?limit=30"),
  getAnalysis: (id: string) => request<AnalysisRecord>(`/api/analyses/${id}`),
  getEvents: (id: string, after = 0) => request<{ events: AgentEvent[] }>(`/api/analyses/${id}/events?after=${after}`),
  cancel: (id: string) => request<{ id: string }>(`/api/analyses/${id}/cancel`, { method: "POST" }),
  remove: (id: string) => request<void>(`/api/analyses/${id}`, { method: "DELETE" }),
  exportUrl: (id: string, format: "md" | "html" | "json" | "csv") => `${API_URL}/api/analyses/${id}/export?format=${format}`,
  wsUrl: (id: string) => `${wsBase()}/ws/analyses/${id}`,
};
