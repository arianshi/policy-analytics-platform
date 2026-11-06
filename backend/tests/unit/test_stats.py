import numpy as np
import pytest

from app.analytics import stats


def test_pct_change_and_cagr():
    assert stats.pct_change(100, 150) == pytest.approx(50.0)
    assert stats.pct_change(0, 5) is None
    assert stats.cagr(100, 121, 2) == pytest.approx(10.0)
    assert stats.cagr(100, 50, 0) is None
    assert stats.cagr(-5, 10, 3) is None


def test_linear_trend_recovers_slope():
    x = np.arange(2015, 2025)
    t = stats.linear_trend(x, 3.0 * (x - 2015) + 10)
    assert t.slope == pytest.approx(3.0) and t.r2 == pytest.approx(1.0) and t.n == 10 and t.significant
    assert stats.linear_trend(np.array([1, 2]), np.array([1, 2])) is None


def test_linear_trend_flat_series_is_not_significant():
    t = stats.linear_trend(np.arange(6), np.full(6, 4.0))
    assert t.slope == 0.0 and not t.significant


def test_correlation_detects_relationship_and_rejects_constants():
    rng = np.random.default_rng(1)
    x = rng.normal(size=40)
    c = stats.correlation(x, -2 * x + rng.normal(scale=0.1, size=40))
    assert c.r < -0.95 and c.p_value < 0.001 and c.strength == "very strong"
    assert stats.correlation(np.arange(10.0), np.full(10, 3.0)) is None
    assert stats.correlation(np.arange(3.0), np.arange(3.0)) is None  # too few points


def test_robust_anomalies_flags_the_jump_only():
    years = np.arange(2000, 2020)
    vals = np.linspace(100, 119, 20)
    vals[12] = 400
    found = stats.robust_anomalies(years, vals, relative=True)
    assert [a.year for a in found] and 2012 in [a.year for a in found]
    assert stats.robust_anomalies(years[:5], vals[:5], relative=True) == []


def test_forecast_linear_interval_widens_and_short_series_returns_nothing():
    yrs = np.arange(2016, 2026)
    vals = 100 + 5 * (yrs - 2016) + np.array([0, 1, -1, 0.5, -0.5, 1, 0, -1, 0.5, 0])
    fc = stats.forecast_linear(yrs, vals, 3)
    assert [f[0] for f in fc] == [2026, 2027, 2028]
    assert fc[0][1] == pytest.approx(150, abs=3)
    widths = [hi - lo for _, _, lo, hi in fc]
    assert widths == sorted(widths)
    assert stats.forecast_linear(yrs[:4], vals[:4], 2) == []


def test_breaks_trend():
    yrs = np.arange(2016, 2026)
    smooth = 100 + 5 * (yrs - 2016).astype(float)
    assert not stats.breaks_trend(yrs, smooth + np.array([0, 1, -1, 0.5, -0.5, 1, 0, -1, 0.5, 0.2]))
    broken = smooth.copy()
    broken[-1] = 60
    assert stats.breaks_trend(yrs, broken)
