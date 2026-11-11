"""Singapore-wide births and fertility (SingStat M810091 / M810761): data layer, interpretation and planning."""

import pytest

from app.agents.intent import build_plan, heuristic_intent
from app.data_quality import process_dataset
from app.datasources.registry import DataRegistry
from app.ontology import resolve_sectors

NOW = 2026
ANNOUNCEMENT = (
    "As announced at the 2026 National Day Rally, the SG Child Support Package will replace the Baby Bonus Scheme and Large "
    "Families Scheme. The Package provides families with stronger and more sustained financial support to help raise their "
    "children from birth through the growing years. Under this Package, every Singapore citizen child will receive the same "
    "level of support, regardless of birth order. The Package payouts will start from April 2027. Until then, eligible "
    "children can continue to enrol in the Baby Bonus Scheme to receive existing benefits, including the Baby Bonus Cash Gift (BBCG)."
)


async def _series(settings, key):
    ds = process_dataset(await DataRegistry(settings).acquire(key), now_year=NOW)
    assert ds.df["sector"].isna().all()  # national series: no sector
    return dict(zip(ds.df["year"].astype(int), ds.df["value"].astype(float), strict=True))


async def test_national_series_match_the_published_singstat_figures(settings):
    tfr = await _series(settings, "singstat.total_fertility_rate")
    births = await _series(settings, "singstat.citizen_births")
    third_plus = await _series(settings, "singstat.citizen_births_third_plus")
    assert (tfr[2016], tfr[2024], tfr[2025]) == (1.20, 0.97, 0.87)
    assert births[2025] == 26071
    assert third_plus[2025] == 3176 + 1031 + 601  # 3rd + 4th + 5th-and-higher order, summed after cleaning


@pytest.mark.parametrize(
    "text,expected",
    [
        ("stronger and more sustained financial support for families", []),
        ("financial assistance for parents in the technology sector", ["ict"]),
        ("jobs in financial services", ["finance"]),
    ],
)
def test_financial_support_is_not_the_finance_sector(text, expected):
    assert resolve_sectors(text) == expected


def test_policy_announcement_is_read_as_a_births_question():
    i = heuristic_intent(ANNOUNCEMENT, now_year=NOW)
    assert not i.needs_clarification and i.sectors == []
    assert set(i.metrics) == {"total_fertility_rate", "citizen_births", "citizen_births_third_plus"}
    assert (i.start_year, i.end_year) == (2016, 2025)  # "2026" in the announcement is not the analysis period
    assert any("10 years" in a for a in i.assumptions)


def test_explicit_period_is_kept_for_births():
    i = heuristic_intent("How have citizen births and the fertility rate changed since 2015?", now_year=NOW)
    assert (i.sectors, i.start_year, i.end_year) == ([], 2015, 2025)


def test_national_plan_skips_sector_comparisons_and_labour_context():
    plan = build_plan(heuristic_intent(ANNOUNCEMENT, now_year=NOW))
    assert [s["tool"] for s in plan.steps] == ["analyze_trends", "cross_source", "outlook"]
    assert all(s["args"]["sector"] is None for s in plan.steps)
    assert all(k.startswith("singstat.") for k in plan.datasets) and "mom.unemployment_rate" not in plan.datasets


def test_births_question_loads_the_other_family_series_as_context():
    plan = build_plan(heuristic_intent("How have citizen births changed since 2015?", now_year=NOW))
    assert plan.required == ["singstat.citizen_births"]
    assert set(plan.optional) == {"singstat.total_fertility_rate", "singstat.citizen_births_third_plus"}
