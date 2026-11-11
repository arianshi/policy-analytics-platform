"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AnalysisResult } from "@/lib/types";
import { ChartCard } from "./ChartCard";
import { DataQualityView, FactsView, SourcesView, TrustView } from "./DataViews";
import { SummaryView } from "./SummaryView";

const TABS = ["Summary", "Charts", "Data quality", "Sources", "Facts", "Trust & cost"] as const;
type Tab = (typeof TABS)[number];

export function ResultTabs({ id, result }: { id: string; result: AnalysisResult }) {
  const [tab, setTab] = useState<Tab>("Summary");
  const [kind, setKind] = useState("all");
  const kinds = ["all", ...Array.from(new Set(result.facts.map((f) => f.kind)))];

  return (
    <section className="card results" aria-label="Results">
      <div className="row between wrap">
        <div role="tablist" aria-label="Result sections" className="tabs">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              id={`tab-${t}`}
              aria-selected={tab === t}
              aria-controls="tabpanel"
              className={tab === t ? "on" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="exports" aria-label="Export">
          {(["md", "html", "json", "csv"] as const).map((f) => (
            <a key={f} className="btn small" href={api.exportUrl(id, f)} download>
              {f.toUpperCase()}
            </a>
          ))}
        </div>
      </div>
      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`} className="panel">
        {tab === "Summary" && <SummaryView result={result} />}
        {tab === "Charts" &&
          (result.charts.length ? (
            <div className="grid2">
              {result.charts.map((c) => (
                <ChartCard key={c.id} spec={c} />
              ))}
            </div>
          ) : (
            <p className="muted">No charts.</p>
          ))}
        {tab === "Data quality" && <DataQualityView result={result} />}
        {tab === "Sources" && result.report && <SourcesView citations={result.report.citations} />}
        {tab === "Facts" && (
          <>
            <label className="small muted" htmlFor="kind">
              Filter by kind{" "}
            </label>
            <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              {kinds.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <FactsView facts={result.facts} filter={kind} />
          </>
        )}
        {tab === "Trust & cost" && <TrustView result={result} />}
      </div>
    </section>
  );
}
