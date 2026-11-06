"""Generate the MOCK internal programmes extract (``data/internal/tech_programmes.xlsx``).

Deterministic (fixed seed). The data is synthetic and intentionally dirty - duplicates, a negative
count, an outlier, blanks, free-text sector labels - so the data-quality stage has real work to do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "internal" / "tech_programmes.xlsx"

PROGRAMMES = ["Digital Foundations Bootcamp", "Cloud & DevOps Conversion", "Data & AI Career Switch",
              "Cybersecurity Practitioner Track", "Software Engineering Immersion"]
SECTORS = {  # canonical-ish label -> share of completions
    "Information & Communications": 0.46, "Financial & Insurance Services": 0.22, "Manufacturing": 0.12,
    "Health & Social Services": 0.10, "Wholesale & Retail Trade": 0.10,
}
YEARS = range(2018, 2026)


def build() -> pd.DataFrame:
    rng = np.random.default_rng(20261030)
    rows = []
    for year in YEARS:
        growth = 1.0 + 0.16 * (year - 2018) - (0.10 if year == 2020 else 0.0)  # dip in 2020
        for prog_i, prog in enumerate(PROGRAMMES):
            base = 420 + 90 * prog_i
            for sector, share in SECTORS.items():
                enrol = int(base * growth * share * 2 * rng.uniform(0.9, 1.1))
                done = int(enrol * rng.uniform(0.72, 0.88))
                rows.append({"year": year, "programme": prog, "sector": sector, "enrolments": enrol,
                             "completions": done, "placement_rate": round(rng.uniform(0.55, 0.85), 2)})
    df = pd.DataFrame(rows)

    # --- inject realistic data-quality problems (documented in DATA_SOURCES.md) ---------------
    df.loc[(df.year == 2021) & (df.programme == PROGRAMMES[1]), "sector"] = df.loc[
        (df.year == 2021) & (df.programme == PROGRAMMES[1]), "sector"].replace({"Information & Communications": "ICT"})
    df.loc[(df.year == 2022) & (df.programme == PROGRAMMES[3]), "sector"] = df.loc[
        (df.year == 2022) & (df.programme == PROGRAMMES[3]), "sector"].replace({"Information & Communications": "Info-comm"})
    df = df.astype({"completions": "object", "year": "object"})
    df.loc[5, "completions"] = None                                   # blank
    df.loc[17, "completions"] = "n/a"                                  # text token
    df.loc[31, "completions"] = -12                                    # impossible negative
    df.loc[44, "completions"] = int(pd.to_numeric(df.loc[44, "completions"]) * 9)  # outlier
    df.loc[58, "year"] = "2O22"                                        # letter O instead of zero
    df = pd.concat([df, df.iloc[[70, 71]]], ignore_index=True)         # exact duplicates
    df = df[~((df.year == 2020) & (df.programme == PROGRAMMES[4]) & (df.sector == "Manufacturing"))]  # a missing cell
    return df.reset_index(drop=True)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame = build()
    with pd.ExcelWriter(OUT, engine="openpyxl") as xl:
        frame.to_excel(xl, sheet_name="completions", index=False)
    print(f"wrote {OUT} ({len(frame)} rows)", file=sys.stderr)
