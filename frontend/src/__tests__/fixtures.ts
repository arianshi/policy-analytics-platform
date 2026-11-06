import type { AgentEvent, AnalysisRecord, AnalysisResult, Fact } from "@/lib/types";

export const fact = (id: string, over: Partial<Fact> = {}): Fact => ({
  id, kind: "growth", sector: "ict", metric: "employed_residents", period: [2020, 2024],
  statement: `Statement for ${id}: employed residents rose from 111,600 in 2020 to 139,200 in 2024.`,
  values: {}, sources: ["mom.employed_residents"], caveats: ["Residents only."], importance: 0.9, mock: false, citations: ["S1"], ...over,
});

export const ev = (seq: number, agent: string, type: string, message: string, data: Record<string, unknown> = {}): AgentEvent => ({
  analysis_id: "a1", seq, ts: "2026-10-30T10:00:00Z", agent, type, message, data,
});

export const result = (over: Partial<AnalysisResult> = {}): AnalysisResult => ({
  status: "completed", error: "", query: "q", duration_ms: 1234, intent: null, plan: null, clarification: null,
  datasets: [{ key: "mom.employed_residents", title: "Employed", source: "MOM", mode: "live", degraded: false, is_mock: false, rows: 224, score: 100, retrieved_at: "2026-10-30T00:00:00Z", url: "https://x" }],
  skipped_datasets: [],
  data_quality: [{ dataset_key: "mom.employed_residents", rows_in: 5376, rows_out: 224, completeness: 1, score: 100, year_min: 2010, year_max: 2025, sectors: 14,
    issues: [{ code: "missing_values", severity: "info", message: "1 suppressed value", rows: 1, action: "treated as missing", examples: ["'-' x1"] }] }],
  facts: [fact("F1"), fact("F2", { kind: "comparison" })],
  charts: [{ id: "c1", title: "Employed residents", type: "line", x: { label: "Year", type: "year" }, y: { label: "persons", unit: "persons" }, range: [2020, 2024],
    series: [{ name: "employed residents", kind: "line", data: [{ x: 2020, y: 111600 }, { x: 2021, y: 120300 }, { x: 2022, y: 132900, flag: true }] }], sources: ["mom.employed_residents"], note: "" }],
  report: {
    title: "ICT: employment trends, 2020-2024", query: "q", window: [2020, 2024],
    executive_summary: { text: "Employment rose strongly.", fact_ids: ["F1"] },
    findings: [{ id: "K1", title: "Employment", text: "Employed residents rose 24.7%.", fact_ids: ["F1"] }],
    implications: [{ text: "Watch 2025.", fact_ids: ["F2"] }], recommendations: [{ text: "Track quarterly.", fact_ids: ["F1"] }],
    assumptions: ["No period given; assumed the latest 5 years."], limitations: ["Residents only."], methodology: ["m"],
    citations: [{ id: "S1", dataset_key: "mom.employed_residents", title: "Employed Residents", source: "MOM (via Data.gov.sg)", dataset_id: "d_1", url: "https://data.gov.sg/x",
      retrieved_at: "2026-10-30T00:00:00Z", mode: "snapshot", degraded: true, is_mock: false, licence: "SODL", notes: [], used_by: ["F1"] }],
    narrative_source: "offline-rule-based", generated_at: "2026-10-30T00:00:00Z",
    verification: { passed: true, items_checked: 3, numbers_checked: 12, grounding_score: 1, error_count: 0, warning_count: 0, issues: [] },
  },
  verification: { passed: true, items_checked: 3, numbers_checked: 12, grounding_score: 1, error_count: 0, warning_count: 0, issues: [] },
  llm: { calls: 20, failed_attempts: 2, input_tokens: 1000, output_tokens: 400, cost_usd: 0.0123, by_provider: { openai: 12, offline: 8 }, fallback_events: [{ task: "react_step", provider: "openai", error: "429" }] },
  warnings: ["mom.job_vacancy_rate: using bundled snapshot"], stages: {},
  ...over,
});

export const record = (over: Partial<AnalysisRecord> = {}): AnalysisRecord => ({
  id: "a1", query: "q", status: "completed", title: "ICT: employment trends", error: null, facts: 2, duration_ms: 1234, created_at: "2026-10-30T10:00:00Z",
  completed_at: "2026-10-30T10:00:02Z", options: {}, result: result(), ...over,
});
