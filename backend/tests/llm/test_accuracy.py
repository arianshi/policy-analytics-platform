"""Accuracy validation: every number the engine reports is recomputed here, independently, straight from the raw source files."""

import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from app.reporting.verify import Item, VerifyContext, verify_items

ICT = "information and communications"
MAJOR_LABELS = ["information and communications", "manufacturing", "construction", "wholesale and retail trade", "financial and insurance services",
                "health and social services", "accommodation and food services", "transportation and storage", "professional services",
                "real estate services", "administrative and support services", "arts, entertainment and recreation", "public administration and education"]


@pytest.fixture(scope="module")
def raw(module_settings):
    d = module_settings.snapshot_dir
    er = pd.read_csv(d / "mom_employed_residents.csv")
    jv = pd.read_csv(d / "mom_job_vacancies.csv")
    rt = pd.read_csv(d / "mom_retrenchments.csv")
    ss = json.loads((d / "singstat_M015731.json").read_text())["Data"]["row"]
    gdp = next(r for r in ss if r["rowText"] == "Information & Communications")
    return {
        "emp": er[er.industry2 == ICT].groupby("year").employed.sum(),
        "emp_all": er[er.industry2.isin(MAJOR_LABELS)].groupby("year").employed.sum(),
        "jv": jv[jv.industry == ICT].set_index("year").job_vacancy.astype(float),
        "rt": pd.to_numeric(rt[rt.industry == ICT].set_index("year").retrench, errors="coerce"),
        "gdp": pd.Series({int(c["key"]): float(c["value"]) for c in gdp["columns"]}),
    }


def fact(ctx, kind, metric=None, sector="ict", contains=None):
    for f in ctx.facts.all():
        if f.kind == kind and (metric is None or f.metric == metric) and f.sector == sector and (contains is None or contains in f.statement):
            return f
    raise AssertionError(f"no {kind}/{metric} fact")


def test_employment_growth_matches_source_arithmetic(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "growth", "employed_residents")
    e = raw["emp"]
    assert f.values["start"].v == e[2020] == 111600 and f.values["end"].v == e[2024] == 139200
    assert f.values["pct_change"].v == pytest.approx((e[2024] / e[2020] - 1) * 100)
    assert f.values["cagr"].v == pytest.approx(((e[2024] / e[2020]) ** (1 / 4) - 1) * 100)
    assert "111,600" in f.statement and "139,200" in f.statement and "+24.7%" in f.statement


def test_vacancy_peak_and_decline(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "peak", "job_vacancies")
    jv = raw["jv"]
    assert int(f.values["year"].v) == int(jv.loc[2020:2024].idxmax()) == 2022
    assert f.values["value"].v == jv[2022] == 10300
    assert f.values["move_to_end"].v == pytest.approx((jv[2024] / jv[2022] - 1) * 100)


def test_linear_trend_matches_scipy(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "trend", "employed_residents")
    e = raw["emp"].loc[2020:2024]
    res = sps.linregress(e.index.to_numpy(float), e.to_numpy(float))
    assert f.values["slope"].v == pytest.approx(abs(res.slope)) and f.values["r2"].v == pytest.approx(res.rvalue**2) and f.values["n"].v == 5


def test_employment_share_matches(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "share", "employed_residents")
    assert f.values["share_end"].v == pytest.approx(raw["emp"][2024] / raw["emp_all"][2024] * 100)
    assert f.values["share_start"].v == pytest.approx(raw["emp"][2020] / raw["emp_all"][2020] * 100)


def test_productivity_joins_two_sources_correctly(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "productivity", "gdp_per_worker")
    v = lambda y: raw["gdp"][y] * 1000 / raw["emp"][y]  # noqa: E731 (S$ million -> S$ thousand per worker)
    assert f.values["start"].v == pytest.approx(v(2020)) and f.values["end"].v == pytest.approx(v(2024))
    assert f.values["gdp_pct"].v == pytest.approx((raw["gdp"][2024] / raw["gdp"][2020] - 1) * 100)
    assert set(f.sources) == {"singstat.gdp_by_industry", "mom.employed_residents"}


def test_correlation_uses_year_on_year_changes_and_matches_scipy(headline, raw):
    ctx, _ = headline
    f = fact(ctx, "correlation", "job_vacancies")
    a = raw["jv"].loc[:2024].pct_change() * 100
    b = raw["rt"].loc[:2024].pct_change() * 100
    both = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    r, p = sps.pearsonr(both.iloc[:, 0], both.iloc[:, 1])
    assert f.values["r"].v == pytest.approx(r) and f.values["p"].v == pytest.approx(p) and f.values["n"].v == len(both)
    assert r < 0  # vacancies up <=> retrenchments down: the sign flips versus (spurious) level correlation
    levels_r = sps.pearsonr(raw["jv"].loc[:2024].align(raw["rt"].loc[:2024].dropna(), join="inner")[0], raw["rt"].loc[:2024].dropna())[0]
    assert levels_r > 0, "levels correlate positively only because both trend upward - the reason we difference"


def test_trend_break_withholds_projection(headline, raw):
    ctx, _ = headline
    assert raw["emp"][2025] < raw["emp"][2024]
    stmt = fact(ctx, "latest", "employed_residents", contains="Beyond the requested window").statement
    assert "129,700" in stmt and "-6.8%" in stmt


def test_every_fact_is_self_consistent(headline):
    """Each fact's sentence must only contain numbers that its own `values` can account for."""
    ctx, _ = headline
    vctx = VerifyContext((2020, 2024), (1998, 2027))
    bad = []
    for f in ctx.facts.all():
        res = verify_items([Item(f.id, f.statement, [f.id])], ctx.facts, vctx)
        bad += [(f.id, i.text) for i in res.errors]
    assert not bad, bad


def test_report_numbers_trace_to_the_facts(headline):
    ctx, result = headline
    assert result["verification"]["passed"] and result["verification"]["numbers_checked"] > 40 and result["verification"]["error_count"] == 0
    cited = set(result["report"]["facts_cited"])
    assert cited and cited <= ctx.facts.ids()


@pytest.mark.parametrize("query", [
    "Forecast employment in the manufacturing sector for the next two years",
    "Compare wage growth in finance and the technology sector since 2019",
    "Is there a link between job vacancies and retrenchments in the health sector?",
    "IT services versus telecommunications job vacancies 2019-2024",
    "job vacancy rate by sector 2021 to 2025",
    "arts sector wage growth 2021-2024",
])
def test_every_fact_kind_is_self_consistent_across_query_types(module_settings, query):
    """Run varied queries so *every* fact kind (forecast, comparison, limitation...) is checked against its own numbers."""
    import asyncio

    from app.agents.workflow import run_analysis
    from tests.conftest import make_ctx

    ctx = make_ctx(module_settings, query=query)
    asyncio.run(run_analysis(ctx))
    vctx = VerifyContext((1998, 2025), (1998, 2030))
    bad = [(f.id, f.kind, i.text) for f in ctx.facts.all() for i in verify_items([Item(f.id, f.statement, [f.id])], ctx.facts, vctx).errors]
    assert ctx.facts.all() and not bad, bad
