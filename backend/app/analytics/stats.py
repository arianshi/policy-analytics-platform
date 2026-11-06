"""Pure statistical helpers (no I/O). All functions tolerate short or degenerate series."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats as sps


@dataclass(frozen=True)
class Trend:
    slope: float
    intercept: float
    r2: float
    p_value: float
    n: int

    @property
    def significant(self) -> bool:
        return self.n >= 5 and self.p_value < 0.05


@dataclass(frozen=True)
class Correlation:
    r: float
    p_value: float
    spearman: float
    n: int

    @property
    def strength(self) -> str:
        a = abs(self.r)
        return "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else "weak"


@dataclass(frozen=True)
class Anomaly:
    year: int
    change: float  # pct change for levels, absolute change for rates
    z: float


def pct_change(first: float, last: float) -> float | None:
    if first is None or last is None or first == 0 or np.isnan(first) or np.isnan(last):
        return None
    return (last - first) / abs(first) * 100.0


def cagr(first: float, last: float, years: int) -> float | None:
    """Compound annual growth rate in percent; undefined for non-positive endpoints or zero span."""
    if years <= 0 or first is None or last is None or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def linear_trend(x: np.ndarray, y: np.ndarray) -> Trend | None:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 3 or np.ptp(x) == 0 or np.allclose(y, y[0]):
        return None if len(x) < 3 else Trend(0.0, float(y[0]), 0.0, 1.0, len(x))
    res = sps.linregress(x, y)
    return Trend(float(res.slope), float(res.intercept), float(res.rvalue**2), float(res.pvalue), len(x))


def correlation(x: np.ndarray, y: np.ndarray) -> Correlation | None:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    if len(x) < 5 or np.allclose(x, x[0]) or np.allclose(y, y[0]):
        return None
    r, p = sps.pearsonr(x, y)
    rho, _ = sps.spearmanr(x, y)
    return Correlation(float(r), float(p), float(rho), len(x))


def robust_anomalies(years: np.ndarray, values: np.ndarray, *, relative: bool, threshold: float = 3.0, min_points: int = 8) -> list[Anomaly]:
    """Flag unusually large year-on-year moves using a median/MAD z-score (robust to the anomalies themselves)."""
    v = np.asarray(values, dtype=float)
    yrs = np.asarray(years)
    if len(v) < min_points:
        return []
    with np.errstate(divide="ignore", invalid="ignore"):
        change = (v[1:] - v[:-1]) / np.abs(v[:-1]) * 100.0 if relative else v[1:] - v[:-1]
    ok = np.isfinite(change)
    if ok.sum() < min_points - 1:
        return []
    med = float(np.median(change[ok]))
    mad = float(np.median(np.abs(change[ok] - med)))
    if mad == 0:
        return []
    z = 0.6745 * (change - med) / mad
    return [Anomaly(int(yrs[i + 1]), float(change[i]), float(z[i])) for i in range(len(change)) if ok[i] and abs(z[i]) >= threshold]


def forecast_linear(years: np.ndarray, values: np.ndarray, horizon: int, *, window: int = 8, level: float = 0.8
                    ) -> list[tuple[int, float, float, float]]:
    """Indicative forecast: OLS on the last ``window`` points with a t-based prediction interval."""
    x = np.asarray(years, dtype=float)[-window:]
    y = np.asarray(values, dtype=float)[-window:]
    n = len(x)
    if n < 5 or horizon < 1:
        return []
    xm = x.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx == 0:
        return []
    slope = float(((x - xm) * (y - y.mean())).sum() / sxx)
    intercept = float(y.mean() - slope * xm)
    resid = y - (intercept + slope * x)
    s = float(np.sqrt((resid**2).sum() / (n - 2)))
    t = float(sps.t.ppf(0.5 + level / 2, n - 2))
    out = []
    for h in range(1, horizon + 1):
        xf = x[-1] + h
        point = intercept + slope * xf
        se = s * np.sqrt(1 + 1 / n + (xf - xm) ** 2 / sxx)
        out.append((int(xf), float(point), float(point - t * se), float(point + t * se)))
    return out


def breaks_trend(years: np.ndarray, values: np.ndarray, *, window: int = 8, level: float = 0.95) -> bool:
    """True when the latest point falls outside the prediction interval implied by the points before it."""
    if len(values) < 6:
        return False
    prior = forecast_linear(np.asarray(years)[:-1], np.asarray(values)[:-1], 1, window=window, level=level)
    if not prior:
        return False
    _, _, lo, hi = prior[0]
    actual = float(values[-1])
    return not (lo <= actual <= hi)
