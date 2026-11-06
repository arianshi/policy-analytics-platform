import { formatCost } from "@/lib/format";
import type { AnalysisResult, Citation, DQReport, Fact } from "@/lib/types";

export function DataQualityView({ result }: { result: AnalysisResult }) {
  const byKey = new Map(result.datasets.map((d) => [d.key, d]));
  return (
    <div className="grid2">
      {result.data_quality.map((d: DQReport) => {
        const ds = byKey.get(d.dataset_key);
        return (
          <section key={d.dataset_key} className="card dq">
            <header className="row between">
              <h3>{d.dataset_key}</h3>
              <span className={`score ${d.score >= 90 ? "good" : d.score >= 70 ? "ok" : "bad"}`} title="Data-quality score">{d.score}</span>
            </header>
            <p className="small muted">
              {d.rows_in.toLocaleString()} → {d.rows_out.toLocaleString()} rows · completeness {(d.completeness * 100).toFixed(1)}% · {d.year_min}–{d.year_max}
              {ds && <> · <span className={`badge mode-${ds.mode}`}>{ds.mode}</span>{ds.degraded && <span className="badge warn">fallback</span>}{ds.is_mock && <span className="badge mock">MOCK</span>}</>}
            </p>
            <ul className="issues">
              {d.issues.filter((i) => i.code !== "normalised").map((i) => (
                <li key={i.code + i.message} className={`sev-${i.severity}`}>
                  <span className="badge">{i.severity}</span> {i.message}
                  {i.action && <span className="muted"> — {i.action}</span>}
                  {i.examples.length > 0 && <div className="small muted">e.g. {i.examples.join("; ")}</div>}
                </li>
              ))}
              {d.issues.filter((i) => i.code !== "normalised").length === 0 && <li className="muted small">No issues found.</li>}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function SourcesView({ citations }: { citations: Citation[] }) {
  return (
    <ol className="sources">
      {citations.map((c) => (
        <li key={c.id} id={`src-${c.id}`}>
          <strong>{c.id}</strong> {c.source} — <a href={c.url} target="_blank" rel="noreferrer noopener">{c.title}</a>
          <div className="small muted">
            Retrieved {c.retrieved_at.slice(0, 10)} · <span className={`badge mode-${c.mode}`}>{c.mode}</span>
            {c.degraded && <span className="badge warn">fallback snapshot</span>}{c.is_mock && <span className="badge mock">MOCK</span>} · {c.licence}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function FactsView({ facts, filter }: { facts: Fact[]; filter: string }) {
  const rows = filter === "all" ? facts : facts.filter((f) => f.kind === filter);
  return (
    <div className="scroll">
      <table className="data small">
        <thead><tr><th>ID</th><th>Kind</th><th>Statement</th><th>Sources</th></tr></thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.id}><td>{f.id}</td><td>{f.kind}</td><td>{f.statement}{f.mock && <span className="badge mock">MOCK</span>}</td><td>{f.citations.join(", ")}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrustView({ result }: { result: AnalysisResult }) {
  const v = result.verification;
  const l = result.llm;
  return (
    <div className="grid2">
      <section className="card">
        <h3>Hallucination check</h3>
        {v ? (
          <>
            <p><span className={`badge ${v.passed ? "status-completed" : "status-failed"}`}>{v.passed ? "Passed" : "Failed"}</span> {v.numbers_checked} numbers checked against {result.facts.length} computed facts · grounding {v.grounding_score}</p>
            <p className="small muted">Every number, year, source name and citation in the narrative is verified against the computed facts.
              {v.revisions ? ` The model was asked to revise ${v.revisions}×.` : ""}{v.template_fallback ? " The rule-based draft replaced an unverifiable model draft." : ""}</p>
            <ul className="issues">
              {v.issues.filter((i) => i.severity !== "info").map((i, n) => <li key={n} className={`sev-${i.severity}`}><span className="badge">{i.code}</span> {i.item}: “{i.text}” {i.detail}</li>)}
            </ul>
          </>
        ) : <p className="muted">Not available.</p>}
      </section>
      <section className="card">
        <h3>LLM usage</h3>
        <dl className="stats">
          <div><dt>Calls</dt><dd>{l.calls}</dd></div>
          <div><dt>Failed attempts</dt><dd>{l.failed_attempts}</dd></div>
          <div><dt>Tokens in / out</dt><dd>{l.input_tokens.toLocaleString()} / {l.output_tokens.toLocaleString()}</dd></div>
          <div><dt>Est. cost</dt><dd>{formatCost(l.cost_usd)}</dd></div>
        </dl>
        <p className="small">Answered by: {Object.entries(l.by_provider).map(([p, n]) => `${p} ×${n}`).join(", ") || "-"}</p>
        {l.fallback_events.length > 0 && (
          <details><summary className="small">{l.fallback_events.length} provider fallback(s)</summary>
            <ul className="small">{l.fallback_events.map((f, i) => <li key={i}>{f.task}: {f.provider} — {f.error}</li>)}</ul>
          </details>
        )}
      </section>
    </div>
  );
}
