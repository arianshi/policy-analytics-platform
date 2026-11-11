import type { AnalysisResult, NarrativeItem } from "@/lib/types";
import { FactRefs } from "./FactRefs";

function Items({ items, facts, ordered }: { items: NarrativeItem[]; facts: AnalysisResult["facts"]; ordered?: boolean }) {
  const List = ordered ? "ol" : "ul";
  return (
    <List className="items">
      {items.map((it, i) => (
        <li key={i}>
          <p>{it.text}</p>
          <FactRefs ids={it.fact_ids} facts={facts} />
        </li>
      ))}
    </List>
  );
}

export function SummaryView({ result }: { result: AnalysisResult }) {
  const r = result.report;
  if (!r) return <p className="muted">No report was produced.</p>;
  return (
    <div className="summary">
      <h2>{r.title}</h2>
      {r.assumptions.length > 0 && (
        <aside className="notice" aria-label="Assumptions">
          <strong>Assumptions made</strong>
          <ul>
            {r.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </aside>
      )}
      <h3>Executive summary</h3>
      <p className="lead">{r.executive_summary.text}</p>
      <FactRefs ids={r.executive_summary.fact_ids} facts={result.facts} />
      <h3>Key findings</h3>
      {r.findings.map((f) => (
        <article key={f.id} className="finding">
          <h4>{f.title}</h4>
          <p>{f.text}</p>
          <FactRefs ids={f.fact_ids} facts={result.facts} />
        </article>
      ))}
      {r.implications.length > 0 && (
        <>
          <h3>Implications</h3>
          <Items items={r.implications} facts={result.facts} />
        </>
      )}
      {r.recommendations.length > 0 && (
        <>
          <h3>Recommendations</h3>
          <Items items={r.recommendations} facts={result.facts} ordered />
        </>
      )}
      <h3>Limitations</h3>
      <ul className="small">
        {r.limitations.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
