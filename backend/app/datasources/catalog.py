"""Catalog of datasets the platform can fetch, plus keyword search over it.

Real dataset ids were discovered through the public Data.gov.sg and SingStat Table Builder APIs
(see DATA_SOURCES.md). The ``internal`` source is clearly-labelled MOCK data.
"""

from __future__ import annotations

import re

from .base import DatasetSpec

_DG = "https://data.gov.sg/datasets/{}/view"
_MOM = "Ministry of Manpower (via Data.gov.sg)"
_SINGSTAT = "Singapore Department of Statistics (SingStat Table Builder)"
_MOM_IND_COLS = ("year", "industry")


def _mom(
    key: str,
    ds_id: str,
    title: str,
    desc: str,
    metric: str,
    value_col: str,
    kw: tuple[str, ...],
    cols: tuple[str, ...] | None = None,
) -> DatasetSpec:
    return DatasetSpec(
        key=f"mom.{key}",
        source="mom",
        source_name=_MOM,
        title=title,
        description=desc,
        metric=metric,
        fmt="csv",
        dataset_id=ds_id,
        page_url=_DG.format(ds_id),
        keywords=kw,
        required_columns=cols or (*_MOM_IND_COLS, value_col),
        snapshot_file=f"mom_{key}.csv",
    )


CATALOG: tuple[DatasetSpec, ...] = (
    _mom(
        "employed_residents",
        "d_a7ece9330c024a90b258776e69c2ae00",
        "Employed Residents Aged 15 Years & Over by Industry, Age And Sex",
        "Resident employment by industry, age band and sex, annual (MOM Labour Force Survey).",
        "employed_residents",
        "employed",
        ("employment", "employed", "workforce", "jobs", "labour force", "residents", "industry", "age", "sex"),
        ("year", "sex", "industry2", "age", "employed"),
    ),
    _mom(
        "job_vacancies",
        "d_01966fdb498fa7fa2863635e25539693",
        "Number of Job Vacancy by Industry - Annual",
        "Job vacancies by industry, annual (June reference), MOM Job Vacancies survey.",
        "job_vacancies",
        "job_vacancy",
        ("vacancy", "vacancies", "openings", "demand", "industry"),
    ),
    _mom(
        "job_vacancy_rate",
        "d_c53ea3b4afe234c08853b7fca12e804e",
        "Job Vacancy Rate by Industry - Annual",
        "Job vacancy rate by industry, annual.",
        "job_vacancy_rate",
        "job_vacancy_rate",
        ("vacancy rate", "tightness", "vacancy", "industry"),
    ),
    _mom(
        "recruitment_rate",
        "d_df2976561f50447ff200a3230b2b0396",
        "Average Monthly Recruitment Rate by Industry - Annual",
        "Average monthly recruitment (hiring) rate by industry, annual.",
        "recruitment_rate",
        "recruitment_rate",
        ("recruitment", "hiring", "hires", "industry"),
    ),
    _mom(
        "resignation_rate",
        "d_83f04fc0e00cb029000aa3bf8c5d0478",
        "Average Monthly Resignation Rate by Industry - Annual",
        "Average monthly resignation rate by industry, annual.",
        "resignation_rate",
        "resignation_rate",
        ("resignation", "attrition", "turnover", "quit", "industry"),
    ),
    _mom(
        "retrenchments",
        "d_661bf51b24d91dff23c053fc461c0923",
        "Number of Retrenched Employees by Industry - Annual",
        "Employees retrenched by industry, annual.",
        "retrenchments",
        "retrench",
        ("retrenchment", "layoff", "layoffs", "redundancy", "industry"),
    ),
    _mom(
        "total_wage_change",
        "d_e5848828d6a14d35a8b7eea7e15a149e",
        "Total Wage (Nominal) Changes by Industry",
        "Year-on-year nominal change in total wages by industry (MOM).",
        "total_wage_change",
        "twc",
        ("wage", "wages", "pay", "salary", "earnings", "industry"),
        ("year", "ind2", "twc"),
    ),
    _mom(
        "basic_wage_change",
        "d_bd83b4534db1032ca7415f82bbaae003",
        "Basic Wage (Nominal) Changes by Industry",
        "Year-on-year nominal change in basic wages by industry (MOM).",
        "basic_wage_change",
        "bwc",
        ("basic wage", "wage", "pay", "industry"),
        ("year", "ind2", "bwc"),
    ),
    _mom(
        "unemployment_rate",
        "d_80effd26c9ca5755a7ff0598fe2e0be6",
        "Overall Unemployment Rate, Annual",
        "Economy-wide overall unemployment rate, annual.",
        "unemployment_rate",
        "unemployment_rate",
        ("unemployment", "unemployed", "jobless", "economy"),
        ("year", "residential_status", "unemployment_rate"),
    ),
    DatasetSpec(
        key="singstat.gdp_by_industry",
        source="singstat",
        source_name="Singapore Department of Statistics (SingStat Table Builder)",
        title="Gross Domestic Product At Current Prices, By Industry (SSIC 2020), Annual",
        description="National accounts: value added by industry at current prices, S$ million, annual.",
        metric="gdp",
        fmt="json",
        dataset_id="M015731",
        page_url="https://tablebuilder.singstat.gov.sg/table/TS/M015731",
        keywords=("gdp", "output", "value added", "economy", "growth", "national accounts", "industry", "productivity"),
        required_columns=("rowText", "columns"),
        snapshot_file="singstat_M015731.json",
        licence="SingStat Open Data Licence",
    ),
    DatasetSpec(
        key="singstat.total_fertility_rate",
        source="singstat",
        source_name=_SINGSTAT,
        title="Births And Fertility Rates, Annual: Total Fertility Rate (TFR)",
        description="Resident total fertility rate (births per female), annual, Singapore-wide.",
        metric="total_fertility_rate",
        fmt="json",
        dataset_id="M810091",
        page_url="https://tablebuilder.singstat.gov.sg/table/TS/M810091",
        keywords=("fertility", "tfr", "birth rate", "births", "babies", "population", "family", "families", "baby bonus"),
        required_columns=("seriesNo", "rowText", "columns"),
        snapshot_file="singstat_M810091.json",
        licence="SingStat Open Data Licence",
        series_nos=("1",),
    ),
    DatasetSpec(
        key="singstat.citizen_births",
        source="singstat",
        source_name=_SINGSTAT,
        title="Births And Fertility Rates, Annual: Citizen Live-Births",
        description="Number of live-births to Singapore citizens, annual, Singapore-wide.",
        metric="citizen_births",
        fmt="json",
        dataset_id="M810091",
        page_url="https://tablebuilder.singstat.gov.sg/table/TS/M810091",
        keywords=("births", "live-births", "babies", "newborns", "citizen children", "population", "family", "baby bonus"),
        required_columns=("seriesNo", "rowText", "columns"),
        snapshot_file="singstat_M810091.json",
        licence="SingStat Open Data Licence",
        series_nos=("7",),
    ),
    DatasetSpec(
        key="singstat.citizen_births_third_plus",
        source="singstat",
        source_name=_SINGSTAT,
        title="Resident And Citizen Live-Births By Birth Order, Annual: citizen 3rd and higher order",
        description="Citizen live-births of a third or later child (3rd + 4th + 5th-and-higher order), annual.",
        metric="citizen_births_third_plus",
        fmt="json",
        dataset_id="M810761",
        page_url="https://tablebuilder.singstat.gov.sg/table/TS/M810761",
        keywords=("birth order", "third child", "large families", "larger families", "births", "family", "baby bonus"),
        required_columns=("seriesNo", "rowText", "columns"),
        snapshot_file="singstat_M810761.json",
        licence="SingStat Open Data Licence",
        series_nos=("2.3", "2.4", "2.5"),
    ),
    DatasetSpec(
        key="internal.tech_programmes",
        source="internal",
        source_name="MOCK internal programme database (illustrative)",
        title="Tech-skills upskilling programmes: enrolments & completions (MOCK)",
        description="Synthetic internal-system extract of tech-skills programme completions by sector. NOT real data.",
        metric="programme_completions",
        fmt="xlsx",
        dataset_id="tech_programmes.xlsx",
        page_url="internal://tech_programmes.xlsx",
        keywords=("training", "upskilling", "programme", "skills", "talent pipeline", "internal"),
        required_columns=("year", "programme", "sector", "completions"),
        snapshot_file="tech_programmes.xlsx",
        is_mock=True,
        licence="Synthetic - for demonstration only",
    ),
)

SPEC_BY_KEY: dict[str, DatasetSpec] = {s.key: s for s in CATALOG}
DATASET_FOR_METRIC: dict[str, str] = {s.metric: s.key for s in CATALOG}

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def search_catalog(query: str, limit: int = 6) -> list[dict]:
    """Rank catalog entries against a free-text need (keyword overlap, weighted by field)."""
    q = _tokens(query)
    scored: list[tuple[float, DatasetSpec]] = []
    for spec in CATALOG:
        kw = _tokens(" ".join(spec.keywords))
        title = _tokens(spec.title)
        desc = _tokens(spec.description)
        score = 3.0 * len(q & kw) + 2.0 * len(q & title) + 1.0 * len(q & desc)
        if spec.metric in q or spec.metric.replace("_", " ") in query.lower():
            score += 4.0
        if score > 0:
            scored.append((score, spec))
    scored.sort(key=lambda p: (-p[0], p[1].key))
    return [
        {"key": s.key, "title": s.title, "source": s.source_name, "metric": s.metric, "score": round(sc, 1), "mock": s.is_mock}
        for sc, s in scored[:limit]
    ]


def datasets_for_metrics(metrics: list[str], *, include_mock: bool = True) -> list[str]:
    keys: list[str] = []
    for m in metrics:
        k = DATASET_FOR_METRIC.get(m)
        if k and k not in keys and (include_mock or not SPEC_BY_KEY[k].is_mock):
            keys.append(k)
    return keys
