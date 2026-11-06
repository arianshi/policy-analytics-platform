"use client";

import { useState } from "react";

export const MAX_QUERY = 600;
export const FAILURE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "None (normal run)" },
  { value: "source:mom:rate_limited", label: "MOM / Data.gov.sg rate-limited (429)" },
  { value: "source:singstat:schema", label: "SingStat API schema drift" },
  { value: "source:internal", label: "Internal database down (optional source)" },
  { value: "llm:openai", label: "OpenAI outage (fallback to next provider)" },
  { value: "llm:*", label: "Every LLM down (rule-based fallback)" },
];

interface Props {
  busy: boolean;
  samples: string[];
  onSubmit: (query: string, simulate: string[]) => void;
  initialQuery?: string;
}

export function QueryForm({ busy, samples, onSubmit, initialQuery = "" }: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [failure, setFailure] = useState("");
  const trimmed = query.trim();
  const valid = trimmed.length >= 3 && trimmed.length <= MAX_QUERY;

  return (
    <form
      className="card query-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !busy) onSubmit(trimmed, failure ? [failure] : []);
      }}
    >
      <label htmlFor="query" className="label">Research question</label>
      <textarea
        id="query"
        value={query}
        rows={3}
        maxLength={MAX_QUERY}
        placeholder="e.g. Analyse employment trends in the technology sector from 2020-2024"
        onChange={(e) => setQuery(e.target.value)}
        aria-describedby="query-hint"
      />
      <div className="row between" id="query-hint">
        <div className="chips" aria-label="Sample questions">
          {samples.slice(0, 4).map((s) => (
            <button type="button" key={s} className="chip" onClick={() => setQuery(s)} title={s}>
              {s.length > 52 ? `${s.slice(0, 50)}…` : s}
            </button>
          ))}
        </div>
        <span className="muted small" aria-live="polite">{trimmed.length}/{MAX_QUERY}</span>
      </div>
      <details className="demo">
        <summary>Demo: simulate a failure</summary>
        <label htmlFor="failure" className="small muted">Inject a fault to watch the agents recover</label>
        <select id="failure" value={failure} onChange={(e) => setFailure(e.target.value)}>
          {FAILURE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </details>
      <div className="row end">
        <button type="submit" className="primary" disabled={!valid || busy}>{busy ? "Analysing…" : "Run analysis"}</button>
      </div>
    </form>
  );
}
