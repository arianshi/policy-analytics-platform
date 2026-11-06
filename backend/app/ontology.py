"""Sector and metric ontology.

Government sources label the same industry differently (MOM: "financial and insurance services",
SingStat: "Finance & Insurance"). Every dataset is normalised onto the canonical sector ids below so
that sources can be joined, compared and cited consistently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def norm_label(label: str) -> str:
    """Lower-case, '&' -> 'and', collapse punctuation/whitespace: the key used for label matching."""
    s = str(label).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    labels: tuple[str, ...]  # source labels that map onto this sector
    aliases: tuple[str, ...] = ()  # words users type in queries
    parent: str | None = None
    major: bool = True  # included in "all sectors" comparisons


SECTORS: tuple[Sector, ...] = (
    Sector(
        "ict",
        "Information & Communications",
        ("information and communications",),
        ("tech", "technology", "ict", "infocomm", "info comm", "information technology", "it sector", "digital", "software", "tech sector"),
    ),
    Sector(
        "ict_it_services",
        "IT & Other Information Services",
        ("it and other information services",),
        ("it services", "software services", "it and information services"),
        parent="ict",
        major=False,
    ),
    Sector(
        "ict_telecom",
        "Telecommunications, Broadcasting & Publishing",
        ("telecommunications broadcasting and publishing", "telecommunications"),
        ("telecom", "telecoms", "telecommunications", "media"),
        parent="ict",
        major=False,
    ),
    Sector("manufacturing", "Manufacturing", ("manufacturing",), ("manufacturing", "factory", "industrial")),
    Sector("construction", "Construction", ("construction",), ("construction", "building")),
    Sector(
        "wholesale_retail",
        "Wholesale & Retail Trade",
        ("wholesale and retail trade",),
        ("retail", "wholesale", "trade", "retail trade"),
    ),
    Sector(
        "finance",
        "Financial & Insurance Services",
        ("financial and insurance services", "finance and insurance"),
        ("finance", "financial", "banking", "bank", "insurance", "fintech"),
    ),
    Sector(
        "health",
        "Health & Social Services",
        ("health and social services",),
        ("health", "healthcare", "health care", "social services", "medical"),
    ),
    Sector(
        "accommodation_food",
        "Accommodation & Food Services",
        ("accommodation and food services",),
        ("hospitality", "food", "f and b", "fnb", "restaurant", "hotel", "tourism", "accommodation"),
    ),
    Sector(
        "transport",
        "Transportation & Storage",
        ("transportation and storage", "transport and storage"),
        ("transport", "logistics", "shipping", "aviation", "transportation"),
    ),
    Sector(
        "professional_services",
        "Professional Services",
        ("professional services",),
        ("professional", "consulting", "legal", "accounting"),
    ),
    Sector(
        "real_estate",
        "Real Estate Services",
        ("real estate services", "real estate"),
        ("real estate", "property"),
    ),
    Sector(
        "admin_support",
        "Administrative & Support Services",
        ("administrative and support services",),
        ("administrative", "admin", "support services"),
    ),
    Sector(
        "arts_recreation",
        "Arts, Entertainment & Recreation",
        ("arts entertainment and recreation",),
        ("arts", "entertainment", "recreation"),
    ),
    Sector(
        "public_admin_edu",
        "Public Administration & Education",
        ("public administration and education", "public administration and defence", "education"),
        ("public sector", "government", "education", "public administration"),
    ),
    Sector(
        "other_services",
        "Other Community, Social & Personal Services",
        ("other community social and personal services", "community social and personal services"),
        ("community services", "personal services"),
        major=False,
    ),
)

SECTOR_BY_ID: dict[str, Sector] = {s.id: s for s in SECTORS}
_LABEL_TO_SECTOR: dict[str, str] = {norm_label(lbl): s.id for s in SECTORS for lbl in s.labels}


def sector_for_label(label: str) -> str | None:
    """Map a source label to a canonical sector id (None when the label is not part of the ontology)."""
    return _LABEL_TO_SECTOR.get(norm_label(label))


def major_sector_ids() -> list[str]:
    return [s.id for s in SECTORS if s.major]


def sector_name(sector_id: str | None) -> str:
    if sector_id is None:
        return "All industries"
    s = SECTOR_BY_ID.get(sector_id)
    return s.name if s else sector_id


def resolve_sectors(text: str) -> list[str]:
    """Find sectors mentioned in free text (alias match on word boundaries, longest alias first)."""
    t = " " + norm_label(text) + " "
    found: list[tuple[int, str]] = []
    pairs = sorted(
        ((norm_label(a), s.id) for s in SECTORS for a in (*s.aliases, s.name, *s.labels)),
        key=lambda p: -len(p[0]),
    )
    consumed = t
    for alias, sid in pairs:
        if not alias:
            continue
        needle = f" {alias} "
        pos = consumed.find(needle)
        if pos >= 0:
            found.append((pos, sid))
            consumed = consumed.replace(needle, " " * len(needle), 1)
    out: list[str] = []
    for _, sid in sorted(found):
        if sid not in out:
            out.append(sid)
    return out


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    unit: str
    kind: str  # "level" | "rate" | "change"
    description: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    higher_is: str = "neutral"  # "good" | "bad" | "neutral" - used for wording only


METRICS: tuple[Metric, ...] = (
    Metric("employed_residents", "Employed residents", "persons", "level",
           "Resident (citizen + PR) employment in the industry, summed over sex and age bands.",
           ("employment", "employed", "workforce", "jobs", "headcount", "workers", "labour force", "labor force")),
    Metric("job_vacancies", "Job vacancies", "vacancies", "level",
           "Number of job vacancies, all ranks, as at June each year.",
           ("vacancy", "vacancies", "openings", "demand", "hiring demand")),
    Metric("job_vacancy_rate", "Job vacancy rate", "%", "rate",
           "Vacancies as a percentage of total employment plus vacancies.",
           ("vacancy rate", "tightness", "labour market tightness")),
    Metric("recruitment_rate", "Recruitment rate", "%", "rate",
           "Average monthly recruitment rate.", ("recruitment", "hiring", "hires", "hiring rate")),
    Metric("resignation_rate", "Resignation rate", "%", "rate",
           "Average monthly resignation rate.", ("resignation", "attrition", "turnover", "quit")),
    Metric("retrenchments", "Retrenched employees", "persons", "level",
           "Number of employees retrenched during the year.",
           ("retrenchment", "retrenchments", "layoff", "layoffs", "redundancy", "redundancies"), higher_is="bad"),
    Metric("total_wage_change", "Total wage change (nominal)", "%", "change",
           "Year-on-year nominal change in total wages.", ("wage", "wages", "pay", "salary", "salaries", "earnings", "income")),
    Metric("basic_wage_change", "Basic wage change (nominal)", "%", "change",
           "Year-on-year nominal change in basic wages.", ("basic wage",)),
    Metric("gdp", "Gross domestic product (current prices)", "S$ million", "level",
           "Value added at current prices attributed to the industry (SingStat national accounts).",
           ("gdp", "output", "value added", "economic output", "contribution", "growth")),
    Metric("unemployment_rate", "Overall unemployment rate", "%", "rate",
           "Annual overall unemployment rate (economy-wide, not by industry).", ("unemployment", "unemployed", "jobless"),
           higher_is="bad"),
    Metric("programme_completions", "Tech-skills programme completions", "persons", "level",
           "Completions in internal tech-skills upskilling programmes (MOCK internal data).",
           ("training", "upskilling", "reskilling", "programme", "programmes", "skills", "talent pipeline", "pipeline")),
)

METRIC_BY_ID: dict[str, Metric] = {m.id: m for m in METRICS}

# Derived (computed, not fetched) metrics
DERIVED_METRICS = {
    "gdp_per_worker": Metric(
        "gdp_per_worker", "Value added per employed resident", "S$ thousand", "level",
        "GDP at current prices divided by employed residents (indicative: GDP also reflects non-resident workers).",
        ("productivity", "output per worker"),
    ),
    "vacancies_per_completion": Metric(
        "vacancies_per_completion", "Job vacancies per programme completion", "ratio", "level",
        "Open vacancies divided by tech-skills programme completions (indicative talent-pipeline gap).",
        ("talent gap", "skills gap", "pipeline gap"),
    ),
}


def resolve_metrics(text: str) -> list[str]:
    t = " " + norm_label(text) + " "
    out: list[str] = []
    for m in METRICS:
        for kw in (*m.keywords, m.name):
            if f" {norm_label(kw)} " in t and m.id not in out:
                out.append(m.id)
                break
    return out
