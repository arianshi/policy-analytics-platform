"""Assemble the structured report: narrative + evidence + citations + limitations + methodology."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..data_quality import DQReport, ProcessedDataset
from ..ontology import sector_name

METHODOLOGY = [
    "Data are retrieved by source adapters (Data.gov.sg CSV, SingStat JSON API, internal Excel extract), normalised to a common sector ontology and cleaned; every cleaning action is logged in the data-quality report.",
    "Growth is reported as total and compound annual change; trends are ordinary least squares on annual values; rates are compared in percentage points.",
    "Correlations use year-on-year changes (not levels) to avoid spurious correlation between trending series; Pearson and Spearman coefficients are reported with sample size.",
    "Anomalies are year-on-year moves with a robust (median/MAD) z-score of at least 3 relative to the series' own history.",
    "Projections are linear extrapolations with an 80% prediction interval and are withheld when the latest observation breaks the preceding trend.",
    "All numbers in the narrative are computed by code and checked against the underlying facts; narrative that fails verification is replaced by a rule-based draft.",
]


def build_citations(processed: dict[str, ProcessedDataset], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    used_by: dict[str, list[str]] = {}
    for f in facts:
        for key in f["sources"]:
            if key in processed:
                if key not in order:
                    order.append(key)
                used_by.setdefault(key, []).append(f["id"])
    out = []
    for i, key in enumerate(order, 1):
        p = processed[key].prov
        out.append(
            {
                "id": f"S{i}",
                "dataset_key": key,
                "title": p.title,
                "source": p.source_name,
                "dataset_id": p.dataset_id,
                "url": p.url,
                "retrieved_at": p.retrieved_at,
                "mode": p.mode,
                "degraded": p.degraded,
                "is_mock": p.is_mock,
                "licence": p.licence,
                "notes": p.notes,
                "used_by": used_by[key],
            }
        )
    return out


def build_limitations(facts: list[dict[str, Any]], dq: dict[str, DQReport], processed: dict[str, ProcessedDataset]) -> list[str]:
    out: list[str] = []
    for f in facts:
        if f["kind"] == "limitation":
            out.append(f["statement"])
    seen: set[str] = set()
    for f in facts:
        for c in f["caveats"]:
            if c not in seen:
                seen.add(c)
                out.append(c)
    for key, p in processed.items():
        if p.prov.degraded:
            out.append(
                f"{p.prov.title}: the live source was unavailable, so a bundled snapshot retrieved {p.prov.retrieved_at[:10]} was used; recent revisions may be missing."
            )
        r = dq.get(key)
        if r:
            for i in r.issues:
                if (
                    i.code
                    in (
                        "conflicting_duplicates",
                        "invalid_year",
                        "non_numeric_values",
                        "out_of_domain",
                        "isolated_spike",
                        "duplicate_rows",
                    )
                    and i.severity != "info"
                ):
                    out.append(f"{p.prov.title}: {i.message} - {i.action}.")
    return list(dict.fromkeys(out))


def _title(intent: dict[str, Any]) -> str:
    sectors = [sector_name(s) for s in intent.get("sectors", [])]
    subject = " vs ".join(sectors) if sectors else "Major industries"
    kind = {
        "trend": "employment & labour-market trends",
        "compare": "comparison",
        "correlate": "relationships",
        "forecast": "outlook",
        "overview": "overview",
    }[intent["kind"]]
    return f"{subject}: {kind}, {intent['start_year']}-{intent['end_year']}"


def assemble_report(
    *,
    query: str,
    intent: dict[str, Any],
    facts: list[dict[str, Any]],
    processed: dict[str, ProcessedDataset],
    dq: dict[str, DQReport],
    narrative: dict[str, Any],
    narrative_source: str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    citations = build_citations(processed, facts)
    findings = [{"id": f"K{i}", **item} for i, item in enumerate(narrative.get("findings", []), 1)]
    return {
        "title": _title(intent),
        "query": query,
        "window": [intent["start_year"], intent["end_year"]],
        "executive_summary": narrative.get("executive_summary") or {"text": "", "fact_ids": []},
        "findings": findings,
        "implications": narrative.get("implications", []),
        "recommendations": narrative.get("recommendations", []),
        "assumptions": intent.get("assumptions", []),
        "limitations": build_limitations(facts, dq, processed),
        "methodology": METHODOLOGY,
        "citations": citations,
        "narrative_source": narrative_source,
        "verification": verification,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
