"""Analytics Agent: runs the planned statistical analyses on the cleaned data and records cited facts + charts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..analytics.engine import AnalysisEngine
from ..ontology import METRIC_BY_ID, SECTOR_BY_ID, sector_name
from .base import ReActAgent, ToolError
from .context import RunContext
from .intent import primary_metric
from .policies import decide_analytics


class AnalyticsAgent(ReActAgent):
    name = "analytics"
    role = (
        "You turn cleaned datasets into statistically sound findings: trends, cross-sector comparison, correlations on year-on-year changes, anomaly detection, "
        "cross-source joins and cautious projections. You never state a number that the tools did not compute."
    )

    def __init__(self, ctx: RunContext):
        plan = ctx.plan
        assert plan is not None
        super().__init__(ctx, max_steps=len(plan.steps) + 6)
        self.engine: AnalysisEngine = AnalysisEngine(ctx.processed, ctx.facts, ctx.charts)
        ctx.engine = self.engine
        self.scope_checked = False
        self.done: Counter[str] = Counter()
        self.add_tool("check_scope", "Report what each loaded dataset covers (sectors, years)", "", self._scope)
        self.add_tool(
            "analyze_trends",
            "Growth, peak/trough, linear trend and latest change for a sector",
            "sector: str, metrics: list[str]",
            self._trends,
        )
        self.add_tool(
            "compare_sectors",
            "Rank sectors on a measure and compute the focus sector's employment share",
            "metrics: list[str], focus: str|null",
            self._compare,
        )
        self.add_tool(
            "analyze_relationships",
            "Correlations of year-on-year changes and anomaly detection",
            "sector: str, pairs: list[[str,str]], anomaly_metrics: list[str]",
            self._relationships,
        )
        self.add_tool(
            "cross_source",
            "Join sources: value added per worker (SingStat x MOM) and talent pipeline (internal x MOM)",
            "sector: str",
            self._cross,
        )
        self.add_tool(
            "outlook",
            "Economy-wide context and an optional indicative projection",
            "sector: str|null, metric: str|null, horizon: int",
            self._outlook,
        )

    # ------------------------------------------------------------------ state
    def goal(self) -> str:
        i = self.ctx.intent
        return f"Run the planned analyses for '{self.ctx.query[:120]}' over {i.start_year}-{i.end_year}."  # type: ignore[union-attr]

    def _todo(self) -> list[dict[str, Any]]:
        seen: Counter[str] = Counter()
        todo = []
        for step in self.ctx.plan.steps:  # type: ignore[union-attr]
            seen[step["tool"]] += 1
            if seen[step["tool"]] > self.done[step["tool"]]:
                todo.append(step)
        return todo

    def state(self) -> dict[str, Any]:
        n_facts = len([f for f in self.ctx.facts.all() if f.kind != "limitation"])
        status = (
            "failed"
            if n_facts == 0 and self.scope_checked and not self._todo()
            else "partial"
            if any(f.kind == "limitation" for f in self.ctx.facts.all())
            else "ok"
        )
        return {"scope_checked": self.scope_checked, "todo": self._todo(), "facts": len(self.ctx.facts), "final_status": status}

    def policy(self, state: dict[str, Any]) -> dict[str, Any]:
        return decide_analytics(state)

    def can_finish(self, state: dict[str, Any], final: dict[str, Any]) -> str | None:
        if state["todo"]:
            return f"{len(state['todo'])} planned analysis step(s) are still pending"
        return None

    # ------------------------------------------------------------------ helpers
    @property
    def _win(self) -> tuple[int, int]:
        return self.ctx.intent.start_year, self.ctx.intent.end_year  # type: ignore[union-attr]

    def _sector(self, args: dict[str, Any], *, required: bool = True) -> str | None:
        s = args.get("sector")
        if s is None and not required:
            return None
        if s not in SECTOR_BY_ID:
            raise ToolError(f"unknown sector {s!r}; valid ids: {sorted(SECTOR_BY_ID)}")
        return s

    @staticmethod
    def _metrics(args: dict[str, Any], key: str = "metrics") -> list[str]:
        ms = args.get(key) or []
        bad = [m for m in ms if m not in METRIC_BY_ID]
        if bad:
            raise ToolError(f"unknown metric(s) {bad}; valid ids: {sorted(METRIC_BY_ID)}")
        return list(ms)

    def _done(self, tool: str, results: list[dict[str, Any]], what: str) -> dict[str, Any]:
        self.done[tool] += 1
        new = [fid for r in results for fid in r.get("fact_ids", [])]
        heads = [r["headline"] for r in results if r.get("headline")][:2]
        return {
            "summary": f"{what}: {len(new)} new fact(s)" + (f". e.g. {heads[0]}" if heads else ""),
            "fact_ids": new,
            "statuses": [r["status"] for r in results],
        }

    # ------------------------------------------------------------------ tools
    async def _scope(self, args: dict[str, Any]) -> dict[str, Any]:
        cov = self.engine.coverage()
        y1 = self._win[1]
        notes = []
        for metric, c in cov.items():
            if c["year_max"] < y1:
                notes.append(f"{metric}: data ends {c['year_max']} (< requested {y1})")
        for n in notes:
            self.ctx.warn(self.name, n)
        self.scope_checked = True
        return {
            "summary": f"{len(cov)} measures available" + (f"; {len(notes)} end before {y1}" if notes else ""),
            "coverage": cov,
        }

    async def _trends(self, args: dict[str, Any]) -> dict[str, Any]:
        sector, metrics = self._sector(args), self._metrics(args)
        y0, y1 = self._win
        primary = primary_metric(self.ctx.intent)  # type: ignore[arg-type]
        results = []
        for m in metrics:
            if not self.engine.has(m, sector):
                self.engine._limit(
                    f"{sector_name(sector)}: no {METRIC_BY_ID[m].name.lower()} data is available for this sector in the loaded sources.",
                    sector,
                    m,
                    self.engine.sources(m),
                )
                results.append({"status": "no_data"})
                continue
            results.append(self.engine.trend(sector, m, y0, y1, primary=(m == primary and sector == self.ctx.plan.focus)))  # type: ignore[union-attr]
        return self._done("analyze_trends", results, f"Trend analysis of {len(metrics)} measure(s) for {sector_name(sector)}")

    async def _compare(self, args: dict[str, Any]) -> dict[str, Any]:
        metrics = self._metrics(args)
        focus = args.get("focus")
        if focus is not None and focus not in SECTOR_BY_ID:
            raise ToolError(f"unknown sector {focus!r}")
        y0, y1 = self._win
        results = [self.engine.compare(focus, m, y0, y1) for m in metrics]
        if focus and "employed_residents" in self.engine.coverage():
            results.append(self.engine.share(focus, y0, y1))
        return self._done("compare_sectors", results, f"Cross-sector comparison on {', '.join(metrics)}")

    async def _relationships(self, args: dict[str, Any]) -> dict[str, Any]:
        sector = self._sector(args)
        y0, y1 = self._win
        results = []
        for pair in args.get("pairs") or []:
            if not (isinstance(pair, list) and len(pair) == 2):
                raise ToolError("each pair must be [metric_a, metric_b]")
            a, b = self._metrics({"m": pair}, "m")
            results.append(self.engine.correlate(sector, a, b, y1))
        for m in self._metrics(args, "anomaly_metrics"):
            if self.engine.has(m, sector):
                results.append(self.engine.anomalies(sector, m, y0, y1))
        return self._done("analyze_relationships", results, f"Relationship and anomaly analysis for {sector_name(sector)}")

    async def _cross(self, args: dict[str, Any]) -> dict[str, Any]:
        sector = self._sector(args)
        y0, y1 = self._win
        results = []
        if "gdp" in self.engine.coverage() and "employed_residents" in self.engine.coverage():
            results.append(self.engine.productivity(sector, y0, y1))
        if "programme_completions" in self.engine.coverage():
            results.append(self.engine.talent_pipeline(sector, y0, y1))
        return self._done("cross_source", results, f"Cross-source analysis for {sector_name(sector)}")

    async def _outlook(self, args: dict[str, Any]) -> dict[str, Any]:
        y0, y1 = self._win
        results = [self.engine.economy_context(y0, y1)]
        horizon = int(args.get("horizon") or 0)
        sector, metric = args.get("sector"), args.get("metric")
        if horizon > 0 and sector in SECTOR_BY_ID and metric in METRIC_BY_ID and self.engine.has(metric, sector):
            results.append(self.engine.forecast(sector, metric, min(horizon, 5)))
        return self._done("outlook", results, "Context and outlook")
