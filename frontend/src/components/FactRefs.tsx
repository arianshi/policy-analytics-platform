"use client";

import { useState } from "react";
import type { Fact } from "@/lib/types";

/** Clickable fact citations: shows the exact computed statement, its sources and caveats. */
export function FactRefs({ ids, facts }: { ids: string[]; facts: Fact[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const byId = new Map(facts.map((f) => [f.id, f]));
  const sel = open ? byId.get(open) : null;
  if (!ids.length) return null;
  return (
    <div className="factrefs">
      {ids.map((id) => {
        const f = byId.get(id);
        return (
          <button key={id} type="button" className={`chip fact ${open === id ? "on" : ""} ${f?.mock ? "mock" : ""}`} aria-expanded={open === id}
            onClick={() => setOpen(open === id ? null : id)} title={f?.statement}>
            {id}{f?.citations?.length ? ` · ${f.citations.join(",")}` : ""}
          </button>
        );
      })}
      {sel && (
        <div className="factdetail" role="note">
          <p>{sel.statement}</p>
          {sel.caveats.length > 0 && <ul className="small muted">{sel.caveats.map((c) => <li key={c}>{c}</li>)}</ul>}
          <p className="small muted">Sources: {sel.sources.join(", ") || "-"}{sel.mock ? " · MOCK data" : ""}</p>
        </div>
      )}
    </div>
  );
}
