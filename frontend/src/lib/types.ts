export type Status = "queued" | "running" | "completed" | "degraded" | "failed" | "needs_clarification" | "cancelled";
export const TERMINAL: Status[] = ["completed", "degraded", "failed", "needs_clarification", "cancelled"];
export const AGENTS = ["coordinator", "extraction", "analytics", "reporter"] as const;
export type AgentName = (typeof AGENTS)[number];

export interface AgentEvent {
  analysis_id: string;
  seq: number;
  ts: string;
  agent: string;
  type: string; // thought | action | observation | delegation | status | warning | error | llm | result | done | ping
  message: string;
  data: Record<string, unknown>;
}

export interface FactValue {
  v: number;
  kind: string;
}
export interface Fact {
  id: string;
  kind: string;
  sector: string | null;
  metric: string | null;
  period: [number, number] | null;
  statement: string;
  values: Record<string, FactValue>;
  sources: string[];
  caveats: string[];
  importance: number;
  mock: boolean;
  citations: string[];
}

export interface ChartPoint {
  x: number | string;
  y: number;
  flag?: boolean;
  label?: string;
  highlight?: boolean;
}
export interface ChartSeries {
  name: string;
  kind: string;
  dashed?: boolean;
  data: ChartPoint[];
}
export interface ChartSpec {
  id: string;
  title: string;
  type: "line" | "bar" | "scatter";
  x: { label: string; type: string };
  y: { label: string; unit: string };
  series: ChartSeries[];
  range: [number, number] | null;
  sources: string[];
  note: string;
}

export interface NarrativeItem {
  text: string;
  fact_ids: string[];
  title?: string;
  id?: string;
}
export interface Citation {
  id: string;
  dataset_key: string;
  title: string;
  source: string;
  dataset_id: string;
  url: string;
  retrieved_at: string;
  mode: string;
  degraded: boolean;
  is_mock: boolean;
  licence: string;
  notes: string[];
  used_by: string[];
}
export interface VerificationIssue {
  severity: string;
  code: string;
  item: string;
  text: string;
  detail: string;
}
export interface Verification {
  passed: boolean;
  items_checked: number;
  numbers_checked: number;
  grounding_score: number;
  error_count: number;
  warning_count: number;
  issues: VerificationIssue[];
  revisions?: number;
  template_fallback?: boolean;
}
export interface Report {
  title: string;
  query: string;
  window: [number, number];
  executive_summary: NarrativeItem;
  findings: NarrativeItem[];
  implications: NarrativeItem[];
  recommendations: NarrativeItem[];
  assumptions: string[];
  limitations: string[];
  methodology: string[];
  citations: Citation[];
  narrative_source: string;
  verification: Verification;
  generated_at: string;
}
export interface DQIssue {
  code: string;
  severity: string;
  message: string;
  rows: number;
  action: string;
  examples: string[];
}
export interface DQReport {
  dataset_key: string;
  rows_in: number;
  rows_out: number;
  completeness: number;
  score: number;
  year_min: number | null;
  year_max: number | null;
  sectors: number;
  issues: DQIssue[];
}
export interface DatasetInfo {
  key: string;
  title: string;
  source: string;
  mode: string;
  degraded: boolean;
  is_mock: boolean;
  rows: number;
  score: number;
  retrieved_at: string;
  url: string;
}
export interface LlmUsage {
  calls: number;
  failed_attempts: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  by_provider: Record<string, number>;
  fallback_events: { task: string; provider: string; error: string }[];
}
export interface AnalysisResult {
  status: Status;
  error: string;
  query: string;
  duration_ms: number;
  intent: {
    kind: string;
    sectors: string[];
    metrics: string[];
    start_year: number;
    end_year: number;
    assumptions: string[];
  } | null;
  plan: { required: string[]; optional: string[]; steps: { tool: string; why: string }[] } | null;
  clarification: { question: string; suggestions: string[] } | null;
  datasets: DatasetInfo[];
  skipped_datasets: string[];
  data_quality: DQReport[];
  facts: Fact[];
  charts: ChartSpec[];
  report: Report | null;
  verification: Verification | null;
  llm: LlmUsage;
  warnings: string[];
  stages: Record<string, string>;
}
export interface AnalysisSummary {
  id: string;
  query: string;
  status: Status;
  title: string | null;
  error: string | null;
  facts: number;
  duration_ms: number | null;
  created_at: string;
  completed_at: string | null;
}
export interface AnalysisRecord extends AnalysisSummary {
  options: Record<string, unknown>;
  result: AnalysisResult | null;
}
export interface ProviderStatus {
  name: string;
  model: string;
  configured: boolean;
  circuit: string;
  calls: number;
  failures: number;
  skipped: number;
  cost_usd: number;
  avg_latency_ms: number;
  last_error: string;
}
