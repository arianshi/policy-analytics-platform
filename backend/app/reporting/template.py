"""Rule-based narrative from facts.

Used (a) by the offline provider as its answer to ``generate_insights`` / ``write_report`` and (b) as the
verified fallback when an LLM narrative fails the hallucination check. Every number it prints is copied from
a fact, so it passes :mod:`app.reporting.verify` by construction.
"""

from __future__ import annotations

from typing import Any

from ..analytics.facts import fmt_count, fmt_pct
from ..ontology import METRIC_BY_ID, sector_name

# (theme, metrics in priority order): the first metric is the theme's headline measure
THEMES: list[tuple[str, tuple[str, ...]]] = [
    ("Employment", ("employed_residents",)),
    ("Hiring demand", ("job_vacancies", "job_vacancy_rate", "recruitment_rate")),
    ("Churn and layoffs", ("retrenchments", "resignation_rate")),
    ("Wages", ("total_wage_change", "basic_wage_change")),
    ("Output and productivity", ("gdp_per_worker", "gdp")),
    ("Talent pipeline (MOCK internal data)", ("vacancies_per_completion", "programme_completions")),
    ("Outlook and context", ("unemployment_rate",)),
]
_KIND_ORDER = {"growth": 0, "latest": 1, "peak": 2, "comparison": 3, "share": 4, "productivity": 5, "trend": 6, "correlation": 7, "anomaly": 8,
               "pipeline": 9, "context": 10, "forecast": 11}
# which facts a theme leads with when it has more than three: the story first, statistical detail last
_PICK_ORDER = {"growth": 0, "peak": 1, "comparison": 2, "share": 3, "productivity": 4, "correlation": 5, "anomaly": 6, "latest": 7, "trend": 8,
               "pipeline": 9, "context": 10, "forecast": 11}


def _theme_of(f: dict[str, Any]) -> str | None:
    if f["kind"] == "forecast" or (f["kind"] == "limitation" and "projection" in f["statement"]):
        return "Outlook and context"
    for name, metrics in THEMES:
        if f.get("metric") in metrics:
            return name
    return None


def _metric_rank(f: dict[str, Any], theme: str) -> int:
    metrics = dict(THEMES)[theme]
    return metrics.index(f["metric"]) if f.get("metric") in metrics else len(metrics)


def build_findings(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        t = _theme_of(f)
        if t and (f["kind"] != "limitation" or t == "Outlook and context"):
            by_theme.setdefault(t, []).append(f)
    out = []
    for name, _ in THEMES:
        group = by_theme.get(name)
        if not group:
            continue
        # high-importance facts first, then the theme's headline measure, then story before statistics
        group = sorted(group, key=lambda f: (0 if f["importance"] >= 0.8 else 1, _metric_rank(f, name), _PICK_ORDER.get(f["kind"], 99), int(f["id"][1:])))[:3]
        group = sorted(group, key=lambda f: (_KIND_ORDER.get(f["kind"], 99), int(f["id"][1:])))
        out.append({"title": name, "text": " ".join(f["statement"] for f in group), "fact_ids": [f["id"] for f in group]})
    return out


def _find(facts: list[dict[str, Any]], kind: str, metric: str | None = None, sector: str | None = None) -> dict[str, Any] | None:
    for f in facts:
        if f["kind"] == kind and (metric is None or f.get("metric") == metric) and (sector is None or f.get("sector") == sector):
            return f
    return None


def _v(f: dict[str, Any], key: str) -> float:
    return float(f["values"][key]["v"])


def build_implications(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    peak = _find(facts, "peak", "job_vacancies")
    retr = _find(facts, "growth", "retrenchments")
    if peak and "move_to_end" in peak["values"] and _v(peak, "move_to_end") < -25:
        sname = sector_name(peak.get("sector"))
        text = (f"Hiring demand in {sname} has cooled sharply: job vacancies are {abs(_v(peak, 'move_to_end')):.1f}% below their {int(_v(peak, 'year'))} high "
                f"of {fmt_count(_v(peak, 'value'))}.")
        ids = [peak["id"]]
        if retr and "pct_change" in retr["values"]:
            text += f" Over the same window retrenchments changed by {fmt_pct(_v(retr, 'pct_change'), True)}, so support may need to shift from filling vacancies towards redeployment and reskilling."
            ids.append(retr["id"])
        out.append({"text": text, "fact_ids": ids})
    for f in facts:
        if f["kind"] == "latest" and "change" in f["values"] and f.get("period") and abs(_v(f, "change")) >= 3 and f["statement"].startswith("Beyond"):
            out.append({"text": f"The most recent data point ({int(_v(f, 'year'))}) shows a {fmt_pct(_v(f, 'change'), True)} move in {METRIC_BY_ID[f['metric']].name.lower()} "
                                "that falls outside the requested window; it should be monitored as a possible turning point.", "fact_ids": [f["id"]]})
            break
    prod = _find(facts, "productivity")
    if prod and _v(prod, "gdp_pct") > _v(prod, "emp_pct"):
        out.append({"text": f"Output growth ({fmt_pct(_v(prod, 'gdp_pct'), True)}) outpaced growth in resident employment ({fmt_pct(_v(prod, 'emp_pct'), True)}) over "
                            f"{int(_v(prod, 'start_year'))}-{int(_v(prod, 'end_year'))}, pointing to productivity-led rather than headcount-led expansion; "
                            "non-resident workers are not counted in employment, so the level of output per worker should be read with caution.", "fact_ids": [prod["id"]]})
    cmp_ = _find(facts, "comparison", "employed_residents")
    if cmp_ and "rank" in cmp_["values"] and _v(cmp_, "rank") <= 3:
        out.append({"text": f"On employment growth the sector ranked #{int(_v(cmp_, 'rank'))} of {int(_v(cmp_, 'of'))} major industries, "
                            "so its experience should not be read as representative of the wider labour market.", "fact_ids": [cmp_["id"]]})
    corr = _find(facts, "correlation", "job_vacancies")
    if corr and _v(corr, "p") < 0.05 and _v(corr, "r") < 0:
        out.append({"text": f"Year-on-year changes in vacancies and retrenchments move in opposite directions (r={_v(corr, 'r'):.2f}, n={int(_v(corr, 'n'))}), "
                            "consistent with a counter-cyclical labour market; this is an association, not evidence of cause.", "fact_ids": [corr["id"]]})
    return out[:5]


def build_recommendations(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    lead = next((f for f in facts if f["kind"] == "growth"), None)
    if lead:
        out.append({"text": "Track hiring demand and retrenchments on MOM's quarterly series rather than annual snapshots, so turning points are detected earlier.",
                    "fact_ids": [lead["id"]]})
    pipe = _find(facts, "pipeline")
    if pipe:
        out.append({"text": "Validate the talent-pipeline indicator with real programme data before using it for planning; the programme figures here are synthetic MOCK data.",
                    "fact_ids": [pipe["id"]]})
    ref = _find(facts, "productivity") or _find(facts, "growth", "total_wage_change") or lead
    if ref:
        out.append({"text": "Extend the analysis with the foreign-workforce split and inflation-adjusted wages before drawing policy conclusions.", "fact_ids": [ref["id"]]})
    return out


def build_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Lead with the headline fact, then anything that qualifies it (data beyond the window), then one fact from a different theme."""
    real = [f for f in facts if f["kind"] not in ("limitation", "trend")]
    if not real:
        return {"text": "No analysable data was available for this request.", "fact_ids": []}
    lead = max(real, key=lambda f: (f["importance"], -int(f["id"][1:])))
    chosen = [lead]
    beyond = next((f for f in real if f["kind"] == "latest" and f["statement"].startswith("Beyond") and f is not lead), None)
    if beyond:
        chosen.append(beyond)
    seen = {_theme_of(f) for f in chosen}
    for kind in ("productivity", "peak", "comparison", "correlation", "anomaly", "share"):
        extra = next((f for f in real if f["kind"] == kind and f not in chosen and _theme_of(f) not in seen), None)
        if extra:
            chosen.append(extra)
            break
    chosen.sort(key=lambda f: int(f["id"][1:]))
    return {"text": " ".join(f["statement"] for f in chosen), "fact_ids": [f["id"] for f in chosen]}


def build_narrative(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"findings": build_findings(facts), "implications": build_implications(facts), "recommendations": build_recommendations(facts),
            "executive_summary": build_summary(facts)}
