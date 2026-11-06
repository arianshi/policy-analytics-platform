"""Map each source's native layout onto the canonical long format.

Canonical columns: ``year, sector, sector_label, value, unit`` (+ ``metric``, ``dataset_key`` added later).
Values are still *raw* here (strings such as "-" survive); :mod:`app.data_quality` coerces and validates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from ..ontology import norm_label, resolve_sectors, sector_for_label
from .base import DatasetSpec, SchemaError

CANONICAL = ["year", "sector", "sector_label", "value", "unit"]

# Source labels that denote the whole economy rather than an industry
ECONOMY_LABELS = {"gdp at current market prices", "overall"}


@dataclass
class Normalised:
    df: pd.DataFrame
    dims: list[str] = field(default_factory=list)  # extra dimensions kept for aggregation (age, sex...)
    aggregate: str | None = None  # "sum" when dims/duplicate labels must be collapsed after cleaning
    notes: list[str] = field(default_factory=list)


def _require(df: pd.DataFrame, cols: tuple[str, ...], spec: DatasetSpec) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SchemaError(
            f"{spec.key}: expected columns {list(cols)} but source returned {list(df.columns)[:8]} "
            f"(missing {missing}) - the source schema may have changed",
            source=spec.source,
        )


def _map_sectors(labels: pd.Series) -> pd.Series:
    def one(lbl: str) -> str | None:
        return None if norm_label(lbl) in ECONOMY_LABELS else sector_for_label(lbl)

    return labels.map(one)


def _finish(df: pd.DataFrame, unit: str, notes: list[str], dims: list[str], aggregate: str | None) -> Normalised:
    df = df.copy()
    df["unit"] = unit
    unmapped_mask = df["sector"].isna() & ~df["sector_label"].map(lambda x: norm_label(x) in ECONOMY_LABELS)
    if unmapped_mask.any():
        labels = sorted(df.loc[unmapped_mask, "sector_label"].unique())
        notes.append(
            f"Dropped {int(unmapped_mask.sum())} rows whose industry label is outside the sector ontology "
            f"({len(labels)} labels, e.g. {', '.join(map(str, labels[:4]))})"
        )
        df = df[~unmapped_mask]
    return Normalised(df[[*CANONICAL, *dims]].reset_index(drop=True), dims, aggregate, notes)


def normalise_mom(spec: DatasetSpec, raw: pd.DataFrame) -> Normalised:
    _require(raw, spec.required_columns, spec)
    notes: list[str] = []
    unit = "%" if spec.metric.endswith(("rate", "change")) else "persons"
    if spec.metric == "unemployment_rate":
        df = pd.DataFrame({"year": raw["year"], "sector_label": "Overall", "value": raw["unemployment_rate"]})
        df["sector"] = None
        return _finish(df, "%", notes, [], None)
    if spec.metric == "employed_residents":
        df = pd.DataFrame({
            "year": raw["year"], "sector_label": raw["industry2"], "value": raw["employed"],
            "age": raw["age"], "sex": raw["sex"],
        })
        df["sector"] = _map_sectors(df["sector_label"])
        return _finish(df, "persons", notes, ["age", "sex"], "sum")
    label_col = "ind2" if "ind2" in raw.columns else "industry"
    value_col = next(c for c in spec.required_columns if c not in ("year", "industry", "ind2"))
    df = pd.DataFrame({"year": raw["year"], "sector_label": raw[label_col], "value": raw[value_col]})
    df["sector"] = _map_sectors(df["sector_label"])
    return _finish(df, unit, notes, [], None)


def normalise_singstat(spec: DatasetSpec, raw: pd.DataFrame) -> Normalised:
    _require(raw, spec.required_columns, spec)
    rows: list[dict] = []
    for _, r in raw.iterrows():
        for col in r["columns"]:
            m = re.match(r"\s*(\d{4})", str(col.get("key", "")))
            if not m:
                continue  # quarterly/monthly periods are out of scope for the annual GDP table
            rows.append({"year": m.group(1), "sector_label": r["rowText"], "value": col.get("value"),
                         "unit": r.get("uoM") or "S$ million"})
    if not rows:
        raise SchemaError(f"{spec.key}: no annual periods found in SingStat payload", source=spec.source)
    df = pd.DataFrame(rows)
    df["sector"] = _map_sectors(df["sector_label"])
    unit = "S$ million"
    out = _finish(df.drop(columns=["unit"]), unit, [], [], "sum")
    return out


def normalise_internal(spec: DatasetSpec, raw: pd.DataFrame) -> Normalised:
    _require(raw, spec.required_columns, spec)
    df = pd.DataFrame({
        "year": raw["year"], "sector_label": raw["sector"], "value": raw["completions"], "programme": raw["programme"],
    })
    # Internal systems use free-text sector names; the ontology's alias table resolves them.
    def to_sector(lbl: str) -> str | None:
        hit = sector_for_label(lbl)
        if hit:
            return hit
        found = resolve_sectors(str(lbl))
        return found[0] if found else None

    df["sector"] = df["sector_label"].map(to_sector)
    return _finish(df, "persons", [], ["programme"], "sum")


NORMALISERS = {"mom": normalise_mom, "singstat": normalise_singstat, "internal": normalise_internal}


def normalise(spec: DatasetSpec, raw: pd.DataFrame) -> Normalised:
    return NORMALISERS[spec.source](spec, raw)
