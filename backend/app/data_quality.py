"""Data validation and cleaning.

Every dataset passes through :func:`process_dataset`, which normalises it to the canonical format,
coerces/validates values, resolves duplicates, quarantines implausible values, aggregates, and returns a
:class:`DQReport` describing *exactly* what was changed - so the cleaning is auditable, not silent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from .datasources.base import DatasetSpec, Provenance, RawDataset
from .datasources.normalise import Normalised, normalise
from .ontology import METRIC_BY_ID

MISSING_TOKENS = {"", "-", "--", "na", "n.a.", "n/a", "nil", "x", "..", "...", "nan", "none", "null", "s", "(s)"}
ISOLATED_SPIKE_RATIO = 5.0


@dataclass
class DQIssue:
    code: str
    severity: str  # info | warning | error
    message: str
    rows: int = 0
    action: str = ""
    examples: list[str] = field(default_factory=list)


@dataclass
class DQReport:
    dataset_key: str
    rows_in: int
    rows_out: int
    completeness: float
    score: int
    year_min: int | None
    year_max: int | None
    sectors: int
    issues: list[DQIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has(self, code: str) -> bool:
        return any(i.code == code for i in self.issues)


@dataclass
class ProcessedDataset:
    spec: DatasetSpec
    df: pd.DataFrame  # canonical: year, sector, sector_label, value, unit (+ metric, dataset_key)
    dq: DQReport
    prov: Provenance


def process_dataset(raw: RawDataset, *, now_year: int | None = None) -> ProcessedDataset:
    """Normalise, validate and clean a raw dataset. Raises ``SchemaError`` if the layout is unrecognisable."""
    norm = normalise(raw.spec, raw.df)
    df, report = clean(raw.spec, norm, now_year=now_year or datetime.now(UTC).year)
    if raw.prov.degraded:
        report.issues.append(DQIssue("degraded_source", "warning",
                                     "Live source was unavailable; a bundled snapshot was used", action="flagged in report"))
        report.score = max(0, report.score - 5)
    if raw.prov.is_mock:
        report.issues.append(DQIssue("mock_data", "info", "Synthetic MOCK dataset - illustrative only, not official statistics"))
    df = df.assign(metric=raw.spec.metric, dataset_key=raw.spec.key)
    return ProcessedDataset(raw.spec, df, report, raw.prov)


def clean(spec: DatasetSpec, norm: Normalised, *, now_year: int) -> tuple[pd.DataFrame, DQReport]:
    df = norm.df.copy()
    rows_in = len(df)
    issues: list[DQIssue] = [DQIssue("normalised", "info", n, action="dropped") for n in norm.notes]
    metric = METRIC_BY_ID[spec.metric]
    df["sector_label"] = df["sector_label"].astype(str).str.strip()

    # --- year ------------------------------------------------------------------------------
    year_num = pd.to_numeric(df["year"].astype(str).str.strip(), errors="coerce")
    bad_year = year_num.isna() | (year_num < 1900) | (year_num > now_year + 1)
    if bad_year.any():
        issues.append(DQIssue("invalid_year", "warning", f"{int(bad_year.sum())} rows have an unparseable or out-of-range year",
                              int(bad_year.sum()), "rows dropped", [str(v) for v in df.loc[bad_year, "year"].unique()[:3]]))
    df = df[~bad_year].copy()
    df["year"] = year_num[~bad_year].astype(int)

    # --- value coercion ----------------------------------------------------------------------
    tokens = df["value"].astype(str).str.strip().str.lower()
    is_missing_token = tokens.isin(MISSING_TOKENS)
    numeric = pd.to_numeric(tokens.str.replace(",", "", regex=False), errors="coerce")
    unparseable = numeric.isna() & ~is_missing_token
    if is_missing_token.any():
        counts = tokens[is_missing_token & (tokens != "")].value_counts().to_dict()
        n_blank = int((tokens == "").sum())
        detail = ", ".join(f"'{k}' x{v}" for k, v in counts.items()) or ""
        if n_blank:
            detail = (detail + ", " if detail else "") + f"blank x{n_blank}"
        issues.append(DQIssue("missing_values", "info", f"{int(is_missing_token.sum())} suppressed/missing values ({detail})",
                              int(is_missing_token.sum()), "treated as missing, excluded from calculations"))
    if unparseable.any():
        issues.append(DQIssue("non_numeric_values", "warning", f"{int(unparseable.sum())} values are not numeric",
                              int(unparseable.sum()), "set to missing", [str(v) for v in tokens[unparseable].unique()[:3]]))
    df["value"] = numeric.astype(float)

    # --- domain validation --------------------------------------------------------------------
    if metric.kind == "rate":
        impossible = df["value"].notna() & ((df["value"] < 0) | (df["value"] > 100))
    elif metric.kind == "change":
        impossible = df["value"].notna() & ((df["value"] < -100) | (df["value"] > 1000))
    else:
        impossible = df["value"].notna() & (df["value"] < 0)
    if impossible.any():
        issues.append(DQIssue("out_of_domain", "error", f"{int(impossible.sum())} values violate the {metric.kind} domain for '{metric.name}'",
                              int(impossible.sum()), "set to missing", [str(v) for v in df.loc[impossible, "value"].head(3)]))
        df.loc[impossible, "value"] = np.nan

    # --- duplicates ----------------------------------------------------------------------------
    key = ["year", "sector_label", *norm.dims]
    exact = df.duplicated(keep="first")
    if exact.any():
        issues.append(DQIssue("duplicate_rows", "warning", f"{int(exact.sum())} exact duplicate rows", int(exact.sum()), "removed"))
        df = df[~exact]
    conflict = df.duplicated(subset=key, keep=False)
    if conflict.any():
        ex = [f"{r.sector_label} {r.year}: {r.value:g}" for r in df[conflict].head(4).itertuples() if pd.notna(r.value)]
        issues.append(DQIssue("conflicting_duplicates", "warning",
                              f"{int(conflict.sum())} rows share a key but disagree, so neither can be trusted", int(conflict.sum()),
                              "both values excluded - verify with the source agency", ex))
        df.loc[conflict, "value"] = np.nan

    # --- isolated spikes (implausible, non-persisting values) ----------------------------------
    df = df.sort_values(["sector_label", *norm.dims, "year"]).reset_index(drop=True)
    spikes = _isolated_spikes(df, ["sector_label", *norm.dims]) if metric.kind != "change" else pd.Series(False, index=df.index)
    if spikes.any():
        ex = [f"{r.sector_label} {r.year}: {r.value:g}" for r in df[spikes].head(3).itertuples()]
        if spec.source == "internal":  # unverified internal systems: entry errors are likely
            issues.append(DQIssue("isolated_spike", "warning", f"{int(spikes.sum())} values are >{ISOLATED_SPIKE_RATIO:g}x their neighbours "
                                  "and revert immediately (likely entry errors)", int(spikes.sum()), "quarantined (set to missing)", ex))
            df.loc[spikes, "value"] = np.nan
        else:  # official statistics: spikes are often real events (e.g. COVID-19) - flag, never delete
            issues.append(DQIssue("unusual_jump", "info", f"{int(spikes.sum())} values are >{ISOLATED_SPIKE_RATIO:g}x their neighbours and revert; "
                                  "retained because this is official data (may be a genuine event)", int(spikes.sum()), "retained, flagged", ex))

    # --- aggregate dims / combined labels ------------------------------------------------------
    completeness = 1.0 - float(df["value"].isna().mean()) if len(df) else 0.0
    df["sector"] = df["sector"].where(df["sector"].notna(), None)
    if norm.aggregate:
        grp = df.assign(_sector=df["sector"].fillna("__economy__")).groupby(["year", "_sector"], sort=True)
        agg = grp.agg(value=("value", lambda s: s.sum(min_count=1)), sector_label=("sector_label", "first"), unit=("unit", "first")).reset_index()
        agg["sector"] = agg["_sector"].where(agg["_sector"] != "__economy__", None)
        df = agg[["year", "sector", "sector_label", "value", "unit"]]
        if norm.dims:
            issues.append(DQIssue("aggregated", "info", f"Summed over {', '.join(norm.dims)} to the (year, sector) level", 0, "aggregated"))

    # --- drop missing values -----------------------------------------------------
    df = df[df["value"].notna()].sort_values(["sector", "year"], na_position="first").reset_index(drop=True)

    # --- gaps & freshness --------------------------------------------------------------------------
    gaps = _year_gaps(df)
    if gaps:
        issues.append(DQIssue("year_gaps", "info", f"{len(gaps)} series have missing years inside their range",
                              len(gaps), "left as gaps (no interpolation)", gaps[:3]))
    ymax = int(df["year"].max()) if len(df) else None
    ymin = int(df["year"].min()) if len(df) else None
    if ymax is not None and now_year - ymax > 2:
        issues.append(DQIssue("stale", "warning", f"Latest observation is {ymax}, {now_year - ymax} years old"))
    if not len(df):
        issues.append(DQIssue("empty", "error", "No usable rows remain after cleaning", action="dataset unusable"))

    report = DQReport(spec.key, rows_in, len(df), round(completeness, 4), 0, ymin, ymax,
                      int(df["sector"].nunique()) if len(df) else 0, issues)
    report.score = _score(report)
    return df[["year", "sector", "sector_label", "value", "unit"]], report


def _isolated_spikes(df: pd.DataFrame, group_cols: list[str]) -> pd.Series:
    """Flag values that jump by >5x relative to *both* neighbours and then revert (data-entry style errors)."""
    flagged = pd.Series(False, index=df.index)
    for _, g in df.groupby(group_cols, sort=False, dropna=False):
        g = g.sort_values("year")
        v = g["value"].to_numpy(dtype=float)
        if len(v) < 3:
            continue
        for i in range(1, len(v) - 1):
            a, b, c = v[i - 1], v[i], v[i + 1]
            if np.isnan([a, b, c]).any() or a <= 0 or c <= 0:
                continue
            spike_up = b >= ISOLATED_SPIKE_RATIO * max(a, c)
            dip = 0 < b <= min(a, c) / ISOLATED_SPIKE_RATIO
            if spike_up or dip:
                flagged.loc[g.index[i]] = True
    return flagged


def _year_gaps(df: pd.DataFrame) -> list[str]:
    out: list[str] = []
    for sector, g in df.groupby(df["sector"].fillna("economy")):
        years = sorted(set(g["year"]))
        if len(years) < 2:
            continue
        missing = sorted(set(range(years[0], years[-1] + 1)) - set(years))
        if missing:
            out.append(f"{sector}: {missing[:4]}")
    return out


def _score(r: DQReport) -> int:
    penalty = 0.0
    for i in r.issues:
        penalty += {"error": 20, "warning": 4, "info": 0}.get(i.severity, 0)
    penalty += (1.0 - r.completeness) * 40
    return int(max(0, min(100, round(100 - penalty))))
