"""Deterministic analytics engine.

Each method computes statistics from cleaned datasets and records :class:`Fact`s and chart specs.
Methods return a *compact* dict (the agent's observation); the full detail lives in the stores.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data_quality import ProcessedDataset
from ..ontology import DERIVED_METRICS, METRIC_BY_ID, major_sector_ids, sector_name
from . import stats
from .facts import COUNT, PCT, RATIO, YEAR, Fact, FactStore, FactValue, fmt_by_unit, fmt_count, fmt_pct, kind_for_unit

METRIC_CAVEATS = {
    "employed_residents": "Counts employed residents (citizens and permanent residents) only; foreign workers are excluded.",
    "job_vacancies": "Vacancies are measured at a June reference point each year.",
    "gdp": "GDP is at current prices (not adjusted for inflation).",
    "total_wage_change": "Nominal wage change - not adjusted for inflation.",
    "basic_wage_change": "Nominal wage change - not adjusted for inflation.",
}


def metric_label(metric: str) -> str:
    m = METRIC_BY_ID.get(metric) or DERIVED_METRICS.get(metric)
    return m.name.lower() if m else metric


def metric_unit(metric: str) -> str:
    m = METRIC_BY_ID.get(metric) or DERIVED_METRICS.get(metric)
    return m.unit if m else ""


class AnalysisEngine:
    def __init__(self, datasets: dict[str, ProcessedDataset], facts: FactStore, charts: list[dict]):
        self.datasets = datasets
        self.facts = facts
        self.charts = charts
        self._series: dict[tuple[str, str | None], pd.Series] = {}
        self._sources: dict[str, str] = {}
        for key, ds in datasets.items():
            df = ds.df
            for (metric, sector), g in df.groupby(["metric", df["sector"].fillna("__economy__")]):
                sec = None if sector == "__economy__" else sector
                s = pd.Series(g["value"].astype(float).to_numpy(), index=g["year"].astype(int).to_numpy()).sort_index()
                self._series[(metric, sec)] = s[~s.index.duplicated(keep="last")]
                self._sources[metric] = key

    # ------------------------------------------------------------------ access
    def series(self, metric: str, sector: str | None, y0: int | None = None, y1: int | None = None) -> pd.Series:
        s = self._series.get((metric, sector), pd.Series(dtype=float))
        if y0 is not None:
            s = s[s.index >= y0]
        if y1 is not None:
            s = s[s.index <= y1]
        return s

    def has(self, metric: str, sector: str | None) -> bool:
        return not self.series(metric, sector).empty

    def sources(self, *metrics: str) -> list[str]:
        out: list[str] = []
        for m in metrics:
            k = self._sources.get(m)
            if k and k not in out:
                out.append(k)
        return out

    def coverage(self) -> dict:
        """What (metric, sector) combinations exist, for the agent's scope check."""
        by_metric: dict[str, dict] = {}
        for (metric, _sector), s in self._series.items():
            if s.empty:
                continue
            e = by_metric.setdefault(metric, {"sectors": 0, "year_min": 9999, "year_max": 0})
            e["sectors"] += 1
            e["year_min"] = min(e["year_min"], int(s.index.min()))
            e["year_max"] = max(e["year_max"], int(s.index.max()))
        return by_metric

    def latest_year(self, metric: str, sector: str | None) -> int | None:
        s = self.series(metric, sector)
        return int(s.index.max()) if not s.empty else None

    # ------------------------------------------------------------------ analyses
    def trend(self, sector: str | None, metric: str, y0: int, y1: int, *, primary: bool = False) -> dict:
        sname, unit, label = sector_name(sector), metric_unit(metric), metric_label(metric)
        src = self.sources(metric)
        s = self.series(metric, sector, y0, y1)
        if len(s) < 3:
            self._limit(
                f"{sname}: only {len(s)} {label} observations in {y0}-{y1}; trend analysis needs at least 3.", sector, metric, src
            )
            return {"status": "insufficient_data", "observations": len(s)}
        ya, yb = int(s.index[0]), int(s.index[-1])
        first, last = float(s.iloc[0]), float(s.iloc[-1])
        vals = {
            "start_year": FactValue(ya, YEAR),
            "end_year": FactValue(yb, YEAR),
            "start": FactValue(first, kind_for_unit(unit)),
            "end": FactValue(last, kind_for_unit(unit)),
        }
        caveats = [METRIC_CAVEATS[metric]] if metric in METRIC_CAVEATS else []
        if (ya, yb) != (y0, y1):
            caveats.append(f"Data available for {ya}-{yb} only (requested {y0}-{y1}).")
        kind_level = METRIC_BY_ID[metric].kind == "level"
        imp = 0.95 if primary else 0.7
        if kind_level:
            pct = stats.pct_change(first, last)
            g = stats.cagr(first, last, yb - ya)
            word = "rose" if (pct or 0) > 0.5 else "fell" if (pct or 0) < -0.5 else "was broadly flat"
            if pct is not None:
                vals["pct_change"] = FactValue(pct, PCT)
            tail = f" ({fmt_pct(pct, True)} overall" if pct is not None else ""
            if g is not None:
                vals["cagr"] = FactValue(g, PCT)
                tail += f"; {fmt_pct(g)} a year compounded"
            tail += ")" if tail else ""
            text = (
                f"{sname}: {label} {word} from {fmt_by_unit(first, unit)} in {ya} to {fmt_by_unit(last, unit)} in {yb}{tail}."
                if word != "was broadly flat"
                else f"{sname}: {label} was broadly flat between {ya} ({fmt_by_unit(first, unit)}) and {yb} ({fmt_by_unit(last, unit)})."
            )
        else:
            delta = last - first
            vals["pp_change"] = FactValue(delta, PCT)
            vals["mean"] = FactValue(float(s.mean()), PCT)
            word = "rose" if delta > 0.05 else "fell" if delta < -0.05 else "was unchanged"
            text = (
                f"{sname}: the {label} {word} from {fmt_pct(first)} in {ya} to {fmt_pct(last)} in {yb}"
                f"{f' ({delta:+.1f} percentage points)' if word != 'was unchanged' else ''}; it averaged {fmt_pct(float(s.mean()))} over the period."
            )
        main = self.facts.add(
            "growth",
            text,
            sector=sector,
            metric=metric,
            period=(ya, yb),
            values=vals,
            sources=src,
            caveats=caveats,
            importance=imp,
        )
        fact_ids = [main.id]

        # peak / trough inside the window (only if it tells a story beyond the endpoints)
        if len(s) >= 4:
            ip, it = int(s.idxmax()), int(s.idxmin())
            for tag, yr, v in (("peak", ip, float(s.max())), ("trough", it, float(s.min()))):
                interior = ya < yr < yb
                span = float(s.max() - s.min())
                if interior and span > 0 and abs(v - (first + last) / 2) >= 0.15 * max(abs(first), abs(last), 1e-9):
                    end_move = stats.pct_change(v, last) if kind_level else last - v
                    move = (
                        f"{fmt_pct(end_move, True)}"
                        if kind_level and end_move is not None
                        else f"{(end_move or 0):+.1f} percentage points"
                    )
                    fv = {"year": FactValue(yr, YEAR), "value": FactValue(v, kind_for_unit(unit))}
                    if end_move is not None:
                        fv["move_to_end"] = FactValue(end_move, PCT)
                    f = self.facts.add(
                        "peak",
                        f"{sname}: {label} {'peaked' if tag == 'peak' else 'bottomed out'} at {fmt_by_unit(v, unit)} in {yr}, "
                        f"then moved {move} by {yb}.",
                        sector=sector,
                        metric=metric,
                        period=(ya, yb),
                        values=fv,
                        sources=src,
                        importance=imp - 0.15,
                    )
                    fact_ids.append(f.id)
                    break

        tr = stats.linear_trend(s.index.to_numpy(), s.to_numpy())
        if tr and len(s) >= 5:
            slope_txt = fmt_by_unit(abs(tr.slope), unit) if unit != "%" else f"{abs(tr.slope):.2f} percentage points"
            sig = "statistically significant" if tr.significant else "not statistically significant"
            f = self.facts.add(
                "trend",
                f"{sname}: the linear trend in {label} is {'upward' if tr.slope > 0 else 'downward'} at about {slope_txt} per year "
                f"(R²={tr.r2:.2f}, n={tr.n}; {sig}).",
                sector=sector,
                metric=metric,
                period=(ya, yb),
                values={
                    "r2": FactValue(tr.r2, RATIO),
                    "n": FactValue(tr.n, COUNT),
                    "p": FactValue(tr.p_value, RATIO),
                    "slope": FactValue(abs(tr.slope), PCT if unit == "%" else kind_for_unit(unit)),
                },
                sources=src,
                caveats=["Short annual series: trend statistics are indicative."] if tr.n < 8 else [],
                importance=imp - 0.3,
            )
            fact_ids.append(f.id)

        if len(s) >= 2:
            prev, cur = float(s.iloc[-2]), last
            ychg = stats.pct_change(prev, cur) if kind_level else cur - prev
            if ychg is not None:
                txt = fmt_pct(ychg, True) if kind_level else f"{ychg:+.1f} percentage points"
                if abs(ychg) < 0.05:
                    txt = "essentially nil"
                f = self.facts.add(
                    "latest",
                    f"{sname}: the latest year-on-year change in {label} ({int(s.index[-2])} to {yb}) was {txt}.",
                    sector=sector,
                    metric=metric,
                    period=(int(s.index[-2]), yb),
                    values={"yoy": FactValue(ychg, PCT)},
                    sources=src,
                    importance=imp - 0.35,
                )
                fact_ids.append(f.id)

        full = self.series(metric, sector)
        if primary and len(full) >= 2 and int(full.index.max()) > yb:
            ly = int(full.index.max())
            cur, prev = float(full.iloc[-1]), float(full.iloc[-2])
            chg = stats.pct_change(prev, cur) if kind_level else cur - prev
            if chg is not None:
                txt = fmt_pct(chg, True) if kind_level else f"{chg:+.1f} percentage points"
                f = self.facts.add(
                    "latest",
                    f"Beyond the requested window, the latest available data ({ly}) put {sname} {label} at {fmt_by_unit(cur, unit)}, "
                    f"a change of {txt} on {int(full.index[-2])}.",
                    sector=sector,
                    metric=metric,
                    period=(int(full.index[-2]), ly),
                    values={
                        "year": FactValue(ly, YEAR),
                        "level": FactValue(cur, kind_for_unit(unit)),
                        "change": FactValue(chg, PCT),
                    },
                    sources=src,
                    importance=0.85,
                    caveats=[
                        f"Falls outside the requested {y0}-{y1} period; shown because it may change the reading of the trend."
                    ],
                )
                fact_ids.append(f.id)

        hist = self.series(metric, sector, y0 - 2)
        self.charts.append(self._line_chart(f"{sname}: {label}", metric, sector, hist, (ya, yb), src))
        return {"status": "ok", "fact_ids": fact_ids, "headline": main.statement}

    def compare(self, focus: str | None, metric: str, y0: int, y1: int) -> dict:
        unit, label, kind = metric_unit(metric), metric_label(metric), METRIC_BY_ID[metric].kind
        src = self.sources(metric)
        measure: dict[str, float] = {}
        for sec in major_sector_ids():
            s = self.series(metric, sec, y0, y1)
            if len(s) < 3:
                continue
            if kind == "level":
                g = stats.cagr(float(s.iloc[0]), float(s.iloc[-1]), int(s.index[-1] - s.index[0]))
                if g is not None:
                    measure[sec] = g
            else:
                measure[sec] = float(s.mean())
        if len(measure) < 3:
            self._limit(
                f"Cross-sector comparison of {label} skipped: fewer than 3 sectors have data for {y0}-{y1}.", None, metric, src
            )
            return {"status": "insufficient_data", "sectors_with_data": len(measure)}
        ranked = sorted(measure.items(), key=lambda kv: -kv[1])
        name_of_measure = "compound annual growth rate" if kind == "level" else "average"
        med = float(np.median(list(measure.values())))
        top_id, top_v = ranked[0]
        bar = [{"x": sector_name(sid), "y": round(v, 2), "highlight": sid == focus} for sid, v in ranked]
        self.charts.append(
            {
                "id": f"chart-compare-{metric}",
                "title": f"{label.capitalize()}: {name_of_measure} by sector, {y0}-{y1}",
                "type": "bar",
                "x": {"label": "Sector", "type": "category"},
                "y": {
                    "label": f"{name_of_measure} (%)" if kind == "level" or unit == "%" else unit,
                    "unit": "%" if kind == "level" or unit == "%" else unit,
                },
                "series": [{"name": name_of_measure, "kind": "bar", "data": bar}],
                "range": [y0, y1],
                "sources": src,
                "note": "Highlighted bar is the focus sector.",
            }
        )
        fmt = (lambda v: fmt_pct(v)) if (kind == "level" or unit == "%") else (lambda v: fmt_by_unit(v, unit))
        if focus in measure:
            rank = [sid for sid, _ in ranked].index(focus) + 1
            if rank == 1:
                text = (
                    f"{sector_name(focus)} ranked #1 of {len(ranked)} sectors on {label} {name_of_measure} ({fmt(measure[focus])}) over {y0}-{y1}, "
                    f"well placed against the sector median of {fmt(med)}."
                )
            else:
                text = (
                    f"{sector_name(focus)} ranked #{rank} of {len(ranked)} sectors on {label} {name_of_measure} ({fmt(measure[focus])}) over {y0}-{y1}; "
                    f"the top sector was {sector_name(top_id)} ({fmt(top_v)}) and the sector median was {fmt(med)}."
                )
            vals = {
                "rank": FactValue(rank, COUNT),
                "of": FactValue(len(ranked), COUNT),
                "focus": FactValue(measure[focus], PCT if unit == "%" or kind == "level" else kind_for_unit(unit)),
                "top": FactValue(top_v, PCT if unit == "%" or kind == "level" else kind_for_unit(unit)),
                "median": FactValue(med, PCT if unit == "%" or kind == "level" else kind_for_unit(unit)),
            }
            f = self.facts.add(
                "comparison",
                text,
                sector=focus,
                metric=metric,
                period=(y0, y1),
                values=vals,
                sources=src,
                importance=0.8,
                caveats=["Compares the " + str(len(ranked)) + " major industries with data in every source."],
            )
        else:
            text = (
                f"Across {len(ranked)} sectors, {label} {name_of_measure} over {y0}-{y1} was highest in {sector_name(top_id)} ({fmt(top_v)}) "
                f"and lowest in {sector_name(ranked[-1][0])} ({fmt(ranked[-1][1])}); the median was {fmt(med)}."
            )
            vals = {
                "top": FactValue(top_v, PCT),
                "bottom": FactValue(ranked[-1][1], PCT),
                "median": FactValue(med, PCT),
                "sectors": FactValue(len(ranked), COUNT),
            }
            f = self.facts.add("comparison", text, metric=metric, period=(y0, y1), values=vals, sources=src, importance=0.65)
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def share(self, focus: str, y0: int, y1: int) -> dict:
        """Focus sector's share of employed residents across the mapped industries."""
        src = self.sources("employed_residents")
        sectors = major_sector_ids()
        frame = pd.DataFrame({sid: self.series("employed_residents", sid) for sid in sectors}).dropna(how="all")
        if focus not in frame.columns or y0 not in frame.index or y1 not in frame.index:
            self._limit("Employment share could not be computed for the requested years.", focus, "employed_residents", src)
            return {"status": "insufficient_data"}
        tot = frame.sum(axis=1, min_count=len(sectors) - 2)
        sh0, sh1 = float(frame.loc[y0, focus] / tot[y0] * 100), float(frame.loc[y1, focus] / tot[y1] * 100)
        word = "grew" if sh1 > sh0 + 0.05 else "shrank" if sh1 < sh0 - 0.05 else "was stable"
        f = self.facts.add(
            "share",
            f"{sector_name(focus)} accounted for {fmt_pct(sh1)} of employed residents across the major industries in {y1}, "
            f"versus {fmt_pct(sh0)} in {y0} - its share {word} ({sh1 - sh0:+.1f} percentage points).",
            sector=focus,
            metric="employed_residents",
            period=(y0, y1),
            values={"share_end": FactValue(sh1, PCT), "share_start": FactValue(sh0, PCT), "pp_change": FactValue(sh1 - sh0, PCT)},
            sources=src,
            caveats=["Share of residents employed in the industries mapped by the platform (excludes the 'others' category)."],
            importance=0.75,
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def correlate(self, sector: str, metric_a: str, metric_b: str, y1: int) -> dict:
        """Correlate *year-on-year changes* (levels that trend over time would produce spurious correlations)."""
        la, lb, sname = metric_label(metric_a), metric_label(metric_b), sector_name(sector)
        src = self.sources(metric_a, metric_b)

        def change(metric: str) -> pd.Series:
            s = self.series(metric, sector, None, y1)
            return s.pct_change() * 100.0 if METRIC_BY_ID[metric].kind == "level" else s.diff()

        both = (
            pd.concat([change(metric_a).rename("a"), change(metric_b).rename("b")], axis=1)
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        c = stats.correlation(both["a"].to_numpy(), both["b"].to_numpy()) if len(both) else None
        if c is None:
            self._limit(
                f"{sname}: not enough overlapping years of {la} and {lb} to estimate a correlation.", sector, metric_a, src
            )
            return {"status": "insufficient_data", "overlap_years": len(both)}
        ya, yb = int(both.index.min()), int(both.index.max())
        if c.p_value < 0.05:
            direction = "positively" if c.r > 0 else "negatively"
            text = (
                f"{sname}: over {c.n} years ({ya}-{yb}), year-on-year changes in {la} and {lb} are {c.strength}ly {direction} correlated "
                f"(Pearson r={c.r:.2f}, p={c.p_value:.3f}; Spearman ρ={c.spearman:.2f})."
            )
        else:
            text = (
                f"{sname}: over {c.n} years ({ya}-{yb}), no statistically significant relationship between year-on-year changes in {la} and {lb} was found "
                f"(Pearson r={c.r:.2f}, p={c.p_value:.3f})."
            )
        f = self.facts.add(
            "correlation",
            text,
            sector=sector,
            metric=metric_a,
            period=(ya, yb),
            values={
                "r": FactValue(c.r, RATIO),
                "p": FactValue(c.p_value, RATIO),
                "rho": FactValue(c.spearman, RATIO),
                "n": FactValue(c.n, COUNT),
                "start_year": FactValue(ya, YEAR),
                "end_year": FactValue(yb, YEAR),
            },
            sources=src,
            importance=0.6,
            caveats=[
                "Correlation does not imply causation.",
                "Computed on year-on-year changes to avoid spurious correlation between trending series.",
            ],
        )
        pts = [{"x": round(float(r.a), 2), "y": round(float(r.b), 2), "label": str(int(idx))} for idx, r in both.iterrows()]
        self.charts.append(
            {
                "id": f"chart-corr-{metric_a}-{metric_b}",
                "title": f"{sname}: year-on-year change in {la} vs {lb}",
                "type": "scatter",
                "x": {"label": f"Change in {la}", "type": "number"},
                "y": {"label": f"Change in {lb}", "unit": ""},
                "series": [{"name": f"{ya}-{yb}", "kind": "scatter", "data": pts}],
                "range": None,
                "sources": src,
                "note": f"Pearson r={c.r:.2f}, n={c.n}",
            }
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def anomalies(self, sector: str, metric: str, y0: int, y1: int) -> dict:
        sname, label, src = sector_name(sector), metric_label(metric), self.sources(metric)
        s = self.series(metric, sector, None, None)
        relative = METRIC_BY_ID[metric].kind == "level"
        found = [a for a in stats.robust_anomalies(s.index.to_numpy(), s.to_numpy(), relative=relative) if y0 <= a.year <= y1 + 1]
        ids = []
        for a in sorted(found, key=lambda a: -abs(a.z))[:2]:
            chg = fmt_pct(a.change, True) if relative else f"{a.change:+.1f} percentage points"
            f = self.facts.add(
                "anomaly",
                f"{sname}: {label} moved {chg} year-on-year in {a.year}, an unusually large change compared with its history (robust z-score {a.z:.1f}).",
                sector=sector,
                metric=metric,
                period=(a.year - 1, a.year),
                values={"year": FactValue(a.year, YEAR), "change": FactValue(a.change, PCT), "z": FactValue(a.z, RATIO)},
                sources=src,
                importance=0.7,
                caveats=["Flags statistical outliers only; the cause is not identified by this analysis."],
            )
            ids.append(f.id)
        for c in self.charts:  # mark flagged years on the matching trend chart
            if c["id"] == f"chart-trend-{metric}-{sector}":
                for p in c["series"][0]["data"]:
                    if any(int(p["x"]) == a.year for a in found):
                        p["flag"] = True
        return {
            "status": "ok",
            "anomalies": [{"year": a.year, "change": round(a.change, 1), "z": round(a.z, 1)} for a in found],
            "fact_ids": ids,
        }

    def productivity(self, sector: str, y0: int, y1: int) -> dict:
        sname = sector_name(sector)
        src = self.sources("gdp", "employed_residents")
        gdp, emp = self.series("gdp", sector, y0, y1), self.series("employed_residents", sector, y0, y1)
        df = pd.concat([gdp.rename("gdp"), emp.rename("emp")], axis=1).dropna()
        if len(df) < 3:
            self._limit(
                f"{sname}: value added per worker needs GDP and employment for at least 3 common years.", sector, "gdp", src
            )
            return {"status": "insufficient_data"}
        df["vpw"] = df["gdp"] * 1000.0 / df["emp"]  # S$ million -> S$ thousand per worker
        ya, yb = int(df.index[0]), int(df.index[-1])
        v0, v1 = float(df["vpw"].iloc[0]), float(df["vpw"].iloc[-1])
        p_v, p_g, p_e = (
            stats.pct_change(v0, v1),
            stats.pct_change(float(df["gdp"].iloc[0]), float(df["gdp"].iloc[-1])),
            stats.pct_change(float(df["emp"].iloc[0]), float(df["emp"].iloc[-1])),
        )
        f = self.facts.add(
            "productivity",
            f"{sname}: value added per employed resident {'rose' if (p_v or 0) > 0 else 'fell'} from S${v0:,.1f} thousand in {ya} to S${v1:,.1f} thousand in {yb} "
            f"({fmt_pct(p_v or 0, True)}), as GDP changed {fmt_pct(p_g or 0, True)} while resident employment changed {fmt_pct(p_e or 0, True)}.",
            sector=sector,
            metric="gdp_per_worker",
            period=(ya, yb),
            values={
                "start": FactValue(v0, COUNT),
                "end": FactValue(v1, COUNT),
                "pct_change": FactValue(p_v or 0, PCT),
                "gdp_pct": FactValue(p_g or 0, PCT),
                "emp_pct": FactValue(p_e or 0, PCT),
                "start_year": FactValue(ya, YEAR),
                "end_year": FactValue(yb, YEAR),
            },
            sources=src,
            importance=0.8,
            caveats=[
                "Indicative ratio: GDP reflects all workers, including non-residents, while employment counts residents only, so the level overstates output per worker; the trend is more reliable than the level.",
                "GDP is at current prices (not inflation-adjusted).",
            ],
        )
        idx = lambda col: [{"x": int(y), "y": round(float(v / df[col].iloc[0] * 100), 1)} for y, v in df[col].items()]  # noqa: E731
        self.charts.append(
            {
                "id": "chart-productivity",
                "title": f"{sname}: output, employment and value added per worker (index, {ya}=100)",
                "type": "line",
                "x": {"label": "Year", "type": "year"},
                "y": {"label": f"Index ({ya}=100)", "unit": "index"},
                "series": [
                    {"name": "GDP (current prices)", "kind": "line", "data": idx("gdp")},
                    {"name": "Employed residents", "kind": "line", "data": idx("emp")},
                    {"name": "Value added per worker", "kind": "line", "data": idx("vpw")},
                ],
                "range": [ya, yb],
                "sources": src,
                "note": "Cross-source join: SingStat GDP / MOM employed residents.",
            }
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def talent_pipeline(self, sector: str, y0: int, y1: int) -> dict:
        sname = sector_name(sector)
        src = self.sources("programme_completions", "job_vacancies")
        comp, vac = self.series("programme_completions", sector, y0, y1), self.series("job_vacancies", sector, y0, y1)
        df = pd.concat([comp.rename("c"), vac.rename("v")], axis=1).dropna()
        if len(df) < 3:
            self._limit(
                f"{sname}: talent-pipeline comparison needs programme completions and vacancies for at least 3 common years.",
                sector,
                "programme_completions",
                src,
            )
            return {"status": "insufficient_data"}
        df["ratio"] = df["v"] / df["c"]
        ya, yb = int(df.index[0]), int(df.index[-1])
        r0, r1 = float(df["ratio"].iloc[0]), float(df["ratio"].iloc[-1])
        f = self.facts.add(
            "pipeline",
            f"[MOCK internal data] {sname}: open vacancies per tech-skills programme completion moved from {r0:.2f} in {ya} to {r1:.2f} in {yb} "
            f"({fmt_count(float(df['c'].iloc[-1]))} completions vs {fmt_count(float(df['v'].iloc[-1]))} vacancies in {yb}).",
            sector=sector,
            metric="vacancies_per_completion",
            period=(ya, yb),
            values={
                "start": FactValue(r0, RATIO),
                "end": FactValue(r1, RATIO),
                "completions": FactValue(float(df["c"].iloc[-1]), COUNT),
                "vacancies": FactValue(float(df["v"].iloc[-1]), COUNT),
                "start_year": FactValue(ya, YEAR),
                "end_year": FactValue(yb, YEAR),
            },
            sources=src,
            mock=True,
            importance=0.55,
            caveats=[
                "Programme completions come from a synthetic MOCK internal dataset used to demonstrate multi-source integration; do not cite as real."
            ],
        )
        self.charts.append(
            {
                "id": "chart-pipeline",
                "title": f"{sname}: vacancies per programme completion [MOCK internal data]",
                "type": "bar",
                "x": {"label": "Year", "type": "year"},
                "y": {"label": "Vacancies per completion", "unit": "ratio"},
                "series": [
                    {
                        "name": "Vacancies per completion",
                        "kind": "bar",
                        "data": [{"x": int(y), "y": round(float(r), 2)} for y, r in df["ratio"].items()],
                    }
                ],
                "range": [ya, yb],
                "sources": src,
                "note": "Internal programme data is synthetic (MOCK).",
            }
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def forecast(self, sector: str, metric: str, horizon: int) -> dict:
        sname, label, unit, src = sector_name(sector), metric_label(metric), metric_unit(metric), self.sources(metric)
        s = self.series(metric, sector)
        if stats.breaks_trend(s.index.to_numpy(), s.to_numpy()):
            ly = int(s.index[-1])
            f = self._limit(
                f"{sname}: no projection is given for {label} because the latest observation ({ly}: {fmt_by_unit(float(s.iloc[-1]), unit)}) "
                f"falls outside the range implied by the preceding trend, so a linear extrapolation would be misleading.",
                sector,
                metric,
                src,
                values={"year": FactValue(ly, YEAR), "level": FactValue(float(s.iloc[-1]), kind_for_unit(unit))},
            )
            return {"status": "withheld_trend_break", "fact_ids": [f.id]}
        fc = stats.forecast_linear(s.index.to_numpy(), s.to_numpy(), horizon)
        if not fc:
            self._limit(f"{sname}: not enough history to project {label}.", sector, metric, src)
            return {"status": "insufficient_data"}
        n = min(8, len(s))
        last_hist = int(s.index[-1])
        yr, pt, lo, hi = fc[-1]
        f = self.facts.add(
            "forecast",
            f"Indicative outlook: extrapolating the last {n} years, {sname} {label} would be about {fmt_by_unit(pt, unit)} in {yr} "
            f"(80% prediction interval {fmt_by_unit(lo, unit)} to {fmt_by_unit(hi, unit)}). This is a statistical scenario, not a forecast of events.",
            sector=sector,
            metric=metric,
            period=(last_hist, yr),
            values={
                "year": FactValue(yr, YEAR),
                "point": FactValue(pt, kind_for_unit(unit)),
                "lower": FactValue(lo, kind_for_unit(unit)),
                "upper": FactValue(hi, kind_for_unit(unit)),
                "n": FactValue(n, COUNT),
                "interval_level": FactValue(80, PCT),
            },
            sources=src,
            importance=0.5,
            caveats=[
                "Simple linear extrapolation of a short annual series; cannot anticipate policy changes, shocks or turning points."
            ],
        )
        hist = [{"x": int(y), "y": float(v)} for y, v in s.iloc[-n:].items()]
        self.charts.append(
            {
                "id": f"chart-forecast-{metric}-{sector}",
                "title": f"{sname}: {label} with indicative projection",
                "type": "line",
                "x": {"label": "Year", "type": "year"},
                "y": {"label": label, "unit": unit},
                "series": [
                    {"name": "Observed", "kind": "line", "data": hist},
                    {
                        "name": "Projection",
                        "kind": "line",
                        "dashed": True,
                        "data": [{"x": last_hist, "y": float(s.iloc[-1])}] + [{"x": a, "y": round(p, 2)} for a, p, _, _ in fc],
                    },
                    {
                        "name": "80% interval (upper)",
                        "kind": "line",
                        "dashed": True,
                        "data": [{"x": a, "y": round(u, 2)} for a, _, _, u in fc],
                    },
                    {
                        "name": "80% interval (lower)",
                        "kind": "line",
                        "dashed": True,
                        "data": [{"x": a, "y": round(lw, 2)} for a, _, lw, _ in fc],
                    },
                ],
                "range": None,
                "sources": src,
                "note": "Indicative linear projection - scenario, not prediction.",
            }
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    def economy_context(self, y0: int, y1: int) -> dict:
        s = self.series("unemployment_rate", None, y0, y1)
        src = self.sources("unemployment_rate")
        if len(s) < 2:
            return {"status": "insufficient_data"}
        a, b = float(s.iloc[0]), float(s.iloc[-1])
        f = self.facts.add(
            "context",
            f"Economy-wide, the overall unemployment rate moved from {fmt_pct(a)} in {int(s.index[0])} to {fmt_pct(b)} in {int(s.index[-1])}.",
            metric="unemployment_rate",
            period=(int(s.index[0]), int(s.index[-1])),
            values={
                "start": FactValue(a, PCT),
                "end": FactValue(b, PCT),
                "start_year": FactValue(int(s.index[0]), YEAR),
                "end_year": FactValue(int(s.index[-1]), YEAR),
            },
            sources=src,
            importance=0.45,
        )
        return {"status": "ok", "fact_ids": [f.id], "headline": f.statement}

    # ------------------------------------------------------------------ helpers
    def _limit(
        self, text: str, sector: str | None, metric: str | None, src: list[str], values: dict[str, FactValue] | None = None
    ) -> Fact:
        return self.facts.add("limitation", text, sector=sector, metric=metric, sources=src, importance=0.3, values=values)

    def _line_chart(
        self, title: str, metric: str, sector: str | None, s: pd.Series, window: tuple[int, int], src: list[str]
    ) -> dict:
        unit = metric_unit(metric)
        return {
            "id": f"chart-trend-{metric}-{sector}",
            "title": title,
            "type": "line",
            "x": {"label": "Year", "type": "year"},
            "y": {"label": f"{metric_label(metric)} ({unit})", "unit": unit},
            "series": [
                {
                    "name": metric_label(metric),
                    "kind": "line",
                    "data": [{"x": int(y), "y": float(v), "flag": False} for y, v in s.items()],
                }
            ],
            "range": list(window),
            "sources": src,
            "note": "",
        }
