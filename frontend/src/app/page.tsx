"use client";

import { useCallback, useEffect, useState } from "react";
import { AgentTimeline } from "@/components/AgentTimeline";
import { HistoryList } from "@/components/HistoryList";
import { ProviderBar } from "@/components/ProviderBar";
import { QueryForm } from "@/components/QueryForm";
import { ResultTabs } from "@/components/ResultTabs";
import { StatusBadge } from "@/components/StatusBadge";
import { useAnalysis } from "@/hooks/useAnalysis";
import { useAnalysisStream } from "@/hooks/useAnalysisStream";
import { api, ApiError } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { TERMINAL, type AnalysisSummary, type ProviderStatus } from "@/lib/types";

export default function Home() {
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [samples, setSamples] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [prefill, setPrefill] = useState("");

  const { events, done, transport } = useAnalysisStream(selected);
  const { record } = useAnalysis(selected, done);
  const running = !!selected && !!record && !TERMINAL.includes(record.status);

  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  // (Re)load history and provider health on mount, after submit/delete, and when a run finishes.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listAnalyses(), api.providers()])
      .then(([h, p]) => {
        if (!cancelled) {
          setHistory(h.items);
          setProviders(p.providers);
        }
      })
      .catch((e) => {
        if (!cancelled) setFormError(e instanceof ApiError ? e.message : "Cannot load history");
      });
    return () => {
      cancelled = true;
    };
  }, [tick, done]);

  useEffect(() => {
    api
      .samples()
      .then((s) => setSamples(s.queries))
      .catch(() => undefined);
  }, []);

  const submit = async (query: string, simulate: string[]) => {
    setFormError(null);
    try {
      const { id } = await api.createAnalysis(query, simulate);
      setSelected(id);
      refresh();
    } catch (e) {
      setFormError(
        e instanceof ApiError
          ? e.status === 429
            ? "You're sending questions too quickly - please wait a moment."
            : e.message
          : "Something went wrong",
      );
    }
  };

  const result = record?.result ?? null;

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <h1>Policy Data Analytics</h1>
          <p className="muted small">
            Multi-agent analysis of Singapore labour-market data · every number is computed, cited and verified
          </p>
        </div>
        <ProviderBar providers={providers} />
      </header>

      <aside className="sidebar" aria-label="Analysis history">
        <div className="row between">
          <h2>History</h2>
          <button
            type="button"
            className="btn small"
            onClick={() => {
              setSelected(null);
              setPrefill("");
              setFormKey((k) => k + 1);
            }}
          >
            New
          </button>
        </div>
        <HistoryList
          items={history}
          selected={selected}
          onSelect={setSelected}
          onDelete={async (id) => {
            await api.remove(id).catch(() => undefined);
            if (id === selected) setSelected(null);
            refresh();
          }}
        />
      </aside>

      <main className="main">
        <QueryForm key={formKey} busy={running} samples={samples} onSubmit={submit} initialQuery={prefill} />
        {formError && (
          <p role="alert" className="error">
            {formError}
          </p>
        )}

        {selected && (
          <>
            <div className="statusline row between wrap">
              <div className="row">
                {record ? <StatusBadge status={record.status} /> : <span className="muted small">Loading…</span>}
                {record?.duration_ms != null && <span className="muted small">{formatDuration(record.duration_ms)}</span>}
              </div>
              {running && (
                <button type="button" className="btn small" onClick={() => void api.cancel(selected)}>
                  Cancel
                </button>
              )}
            </div>
            <AgentTimeline events={events} running={running} transport={transport} />
          </>
        )}

        {record?.status === "needs_clarification" && result?.clarification && (
          <section className="card clarify" aria-label="Clarification needed">
            <h2>I need a little more detail</h2>
            <p>{result.clarification.question}</p>
            <div className="chips">
              {result.clarification.suggestions.map((s) => (
                <button key={s} className="chip" type="button" onClick={() => void submit(s, [])}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        )}
        {record?.status === "failed" && (
          <p role="alert" className="error">
            The analysis failed: {record.error ?? result?.error ?? "unknown error"}
          </p>
        )}
        {record?.status === "degraded" && result && result.warnings.length > 0 && (
          <aside className="notice" aria-label="Fallbacks used">
            <strong>Completed with fallbacks — disclosed in the report</strong>
            <ul>
              {result.warnings.slice(0, 6).map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </aside>
        )}
        {selected && result?.report && <ResultTabs id={selected} result={result} />}
      </main>
    </div>
  );
}
