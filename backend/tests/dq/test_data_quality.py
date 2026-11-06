"""Data-quality validation: synthetic defects are detected and handled by policy; real snapshots surface their real defects."""

import numpy as np
import pandas as pd
import pytest

from app.data_quality import clean, process_dataset
from app.datasources.base import SchemaError
from app.datasources.catalog import CATALOG, SPEC_BY_KEY
from app.datasources.normalise import Normalised, normalise
from app.datasources.registry import DataRegistry

MOM_RATE = SPEC_BY_KEY["mom.job_vacancy_rate"]
MOM_COUNT = SPEC_BY_KEY["mom.job_vacancies"]
INTERNAL = SPEC_BY_KEY["internal.tech_programmes"]


def frame(rows, dims=None):
    df = pd.DataFrame(rows, columns=["year", "sector_label", "value"])
    df["sector"] = "ict"
    df["unit"] = "persons"
    return Normalised(df[["year", "sector", "sector_label", "value", "unit"]], dims or [], None)


def codes(report):
    return {i.code for i in report.issues}


def series(years, values):
    return [(y, "ict", v) for y, v in zip(years, values, strict=True)]


def test_suppression_tokens_become_missing_not_zero():
    df, rep = clean(MOM_COUNT, frame(series(range(2015, 2022), ["100", "110", "-", "n/a", "", "130", "140"])), now_year=2026)
    assert len(df) == 4 and 0 not in df.value.tolist()
    assert "missing_values" in codes(rep) and rep.completeness == pytest.approx(4 / 7, abs=0.01)


def test_non_numeric_garbage_is_flagged_as_warning():
    _, rep = clean(MOM_COUNT, frame(series(range(2015, 2020), ["1", "2", "abc", "4", "5"])), now_year=2026)
    assert "non_numeric_values" in codes(rep)


def test_thousands_separators_are_parsed():
    df, _ = clean(MOM_COUNT, frame(series(range(2015, 2018), ["1,200", "1,300", "1,400"])), now_year=2026)
    assert df.value.tolist() == [1200.0, 1300.0, 1400.0]


def test_rate_outside_0_100_is_rejected_as_error():
    df, rep = clean(MOM_RATE, frame(series(range(2015, 2020), ["4.1", "4.5", "145", "4.7", "-3"])), now_year=2026)
    assert len(df) == 3 and "out_of_domain" in codes(rep)
    assert any(i.severity == "error" for i in rep.issues) and rep.score < 85


def test_negative_count_rejected():
    df, rep = clean(MOM_COUNT, frame(series(range(2015, 2019), ["10", "-5", "12", "13"])), now_year=2026)
    assert len(df) == 3 and "out_of_domain" in codes(rep)


def test_bad_years_dropped_with_examples():
    df, rep = clean(MOM_COUNT, frame([("2O22", "ict", "5"), ("2021", "ict", "4"), ("1850", "ict", "3"), ("2030", "ict", "9")]), now_year=2026)
    assert df.year.tolist() == [2021]
    issue = next(i for i in rep.issues if i.code == "invalid_year")
    assert issue.rows == 3 and "2O22" in issue.examples


def test_exact_duplicates_removed_and_conflicting_duplicates_excluded_not_guessed():
    rows = [*series(range(2015, 2020), ["10", "11", "12", "13", "14"]), (2017, "ict", "12"), (2018, "ict", "99")]
    df, rep = clean(MOM_COUNT, frame(rows), now_year=2026)
    assert "duplicate_rows" in codes(rep) and "conflicting_duplicates" in codes(rep)
    assert 2018 not in df.year.tolist()  # 13 vs 99: neither is trusted
    assert df[df.year == 2017].value.tolist() == [12.0]
    bad = next(i for i in rep.issues if i.code == "conflicting_duplicates")
    assert any("13" in e for e in bad.examples) and any("99" in e for e in bad.examples)


def test_isolated_spike_in_official_data_is_retained_but_flagged():
    vals = ["100", "105", "110", "900", "112", "115"]  # genuine-looking shock that reverts
    spec = MOM_COUNT
    df, rep = clean(spec, frame(series(range(2015, 2021), vals)), now_year=2026)
    assert 900.0 in df.value.tolist() and "unusual_jump" in codes(rep) and "isolated_spike" not in codes(rep)


def test_isolated_spike_in_internal_data_is_quarantined():
    vals = ["100", "105", "110", "900", "112", "115"]
    df, rep = clean(INTERNAL, frame(series(range(2015, 2021), vals)), now_year=2026)
    assert 900.0 not in df.value.tolist() and "isolated_spike" in codes(rep)


def test_persistent_level_shift_is_not_a_spike():
    df, rep = clean(INTERNAL, frame(series(range(2015, 2021), ["100", "105", "500", "520", "540", "560"])), now_year=2026)
    assert len(df) == 6 and "isolated_spike" not in codes(rep)


def test_change_metrics_are_exempt_from_spike_detection():
    spec = SPEC_BY_KEY["mom.total_wage_change"]
    _, rep = clean(spec, frame(series(range(2015, 2021), ["0.2", "0.3", "5.0", "0.2", "0.3", "0.2"])), now_year=2026)
    assert "isolated_spike" not in codes(rep) and "unusual_jump" not in codes(rep)


def test_aggregation_sums_dimensions():
    df = pd.DataFrame({"year": ["2020"] * 4, "sector": ["ict"] * 4, "sector_label": ["x"] * 4, "value": ["1", "2", "3", "4"], "unit": ["persons"] * 4,
                       "age": ["a", "a", "b", "b"], "sex": ["m", "f", "m", "f"]})
    out, rep = clean(SPEC_BY_KEY["mom.employed_residents"], Normalised(df, ["age", "sex"], "sum"), now_year=2026)
    assert out.value.tolist() == [10.0] and "aggregated" in codes(rep)


def test_stale_data_warning():
    _, rep = clean(MOM_COUNT, frame(series(range(2010, 2016), ["1", "2", "3", "4", "5", "6"])), now_year=2026)
    assert "stale" in codes(rep)


def test_empty_after_cleaning_is_an_error_not_a_crash():
    df, rep = clean(MOM_COUNT, frame(series(range(2015, 2018), ["-", "-", "-"])), now_year=2026)
    assert df.empty and "empty" in codes(rep) and rep.score <= 80


def test_gaps_are_reported_not_interpolated():
    df, rep = clean(MOM_COUNT, frame(series([2015, 2016, 2018, 2019], ["1", "2", "4", "5"])), now_year=2026)
    assert "year_gaps" in codes(rep) and df.year.tolist() == [2015, 2016, 2018, 2019]


def test_missing_required_column_is_a_schema_error():
    with pytest.raises(SchemaError):
        normalise(MOM_COUNT, pd.DataFrame({"year": ["2020"], "wrong": ["x"]}))


def test_unmapped_industries_are_dropped_and_disclosed():
    raw = pd.DataFrame({"year": ["2020", "2020"], "industry": ["information and communications", "air transport and supporting services"], "job_vacancy": ["5", "6"]})
    n = normalise(MOM_COUNT, raw)
    assert len(n.df) == 1 and any("outside the sector ontology" in note for note in n.notes)


# ---------------------------------------------------------------------- real snapshot defects
@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    import asyncio

    from app.config import Settings

    reg = DataRegistry(Settings(live_data=False, runtime_dir=tmp_path_factory.mktemp("rt")))
    return {s.key: process_dataset(asyncio.run(reg.acquire(s.key)), now_year=2026) for s in CATALOG}


def test_all_snapshots_clean_to_usable_datasets(processed):
    for key, p in processed.items():
        assert p.dq.rows_out > 10 and p.dq.completeness > 0.95 and p.dq.year_max >= 2024, key
        assert set(p.df.columns) >= {"year", "sector", "value", "metric", "dataset_key"} and p.df.value.notna().all(), key
        assert not np.isinf(p.df.value).any()


def test_real_upstream_defects_are_surfaced(processed):
    retr = processed["mom.retrenchments"].dq
    assert retr.has("missing_values") and any("'-'" in i.message for i in retr.issues if i.code == "missing_values")
    wages = processed["mom.total_wage_change"].dq
    assert wages.has("conflicting_duplicates")
    assert any("Construction 2023" in e for i in wages.issues if i.code == "conflicting_duplicates" for e in i.examples)
    construction = processed["mom.total_wage_change"].df
    assert 2023 not in construction[construction.sector == "construction"].year.tolist()


def test_official_data_is_never_quarantined(processed):
    for key, p in processed.items():
        if not p.spec.is_mock:
            assert not p.dq.has("isolated_spike"), key


def test_internal_mock_defects_are_all_detected(processed):
    rep = processed["internal.tech_programmes"].dq
    assert {"invalid_year", "missing_values", "out_of_domain", "duplicate_rows", "isolated_spike", "mock_data"} <= codes(rep)
    assert rep.score < 80 and processed["internal.tech_programmes"].prov.is_mock
    df = processed["internal.tech_programmes"].df
    assert set(df.sector) == {"ict", "finance", "manufacturing", "health", "wholesale_retail"}  # 'ICT', 'Info-comm' variants resolved
    assert (df.value > 0).all()


def test_degraded_snapshot_lowers_the_score_and_is_flagged():
    import asyncio

    from app.config import Settings

    reg = DataRegistry(Settings(live_data=False))
    normal = process_dataset(reg.acquire_snapshot("mom.job_vacancies", degraded=False), now_year=2026)
    degraded = process_dataset(reg.acquire_snapshot("mom.job_vacancies", degraded=True), now_year=2026)
    assert degraded.dq.has("degraded_source") and degraded.dq.score < normal.dq.score
    assert asyncio  # keep import used
