import { timeAgo } from "@/lib/format";
import type { AnalysisSummary } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  items: AnalysisSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  now?: number;
}

export function HistoryList({ items, selected, onSelect, onDelete, now }: Props) {
  if (!items.length) return <p className="muted small">No analyses yet. Run your first question.</p>;
  return (
    <ul className="history">
      {items.map((a) => (
        <li key={a.id} className={a.id === selected ? "on" : ""}>
          <button type="button" className="hist-main" onClick={() => onSelect(a.id)} aria-current={a.id === selected}>
            <span className="hist-title">{a.title ?? a.query}</span>
            <span className="small muted">
              {timeAgo(a.created_at, now)} · {a.facts} facts
            </span>
            <StatusBadge status={a.status} />
          </button>
          <button
            type="button"
            className="icon"
            aria-label={`Delete analysis: ${a.title ?? a.query}`}
            onClick={() => onDelete(a.id)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}
