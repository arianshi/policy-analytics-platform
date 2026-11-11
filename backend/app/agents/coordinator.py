"""Data Coordinator Agent: interprets the question, plans the workflow, delegates, re-plans and decides when to stop."""

from __future__ import annotations

from typing import Any

from ..llm.types import LLMError, LLMRequest
from .analytics_agent import AnalyticsAgent
from .base import AgentResult, ReActAgent, ToolError
from .context import RunContext
from .extraction import ExtractionAgent
from .intent import build_plan, heuristic_intent, intent_prompt, intent_system_prompt, validate_intent_text
from .policies import decide_coordinator
from .reporter import ReporterAgent


class CoordinatorAgent(ReActAgent):
    name = "coordinator"
    role = (
        "You orchestrate the Data Extraction, Analytics and Reporter agents. Interpret the question, plan, delegate in order, re-plan when inputs are missing, "
        "ask for clarification instead of guessing when the question is ambiguous, and stop when the report is ready or cannot be produced."
    )

    def __init__(self, ctx: RunContext):
        super().__init__(ctx, max_steps=ctx.settings.agent_max_steps)
        self.clarified = False
        self.replanned = False
        self.extraction: dict[str, Any] | None = None
        self.analytics: dict[str, Any] | None = None
        self.reporting: dict[str, Any] | None = None
        self.add_tool(
            "interpret_query",
            "Turn the question into a structured intent (LLM + schema validation, heuristic fallback)",
            "",
            self._interpret,
        )
        self.add_tool(
            "request_clarification", "Ask the user to clarify an ambiguous question and stop", "question: str", self._clarify
        )
        self.add_tool("plan_workflow", "Derive required/optional datasets and analysis steps from the intent", "", self._plan)
        self.add_tool("delegate_extraction", "Run the Data Extraction agent", "", self._delegate_extraction)
        self.add_tool("replan", "Rebuild the plan without datasets that could not be obtained", "reason: str", self._replan)
        self.add_tool("delegate_analytics", "Run the Analytics agent", "", self._delegate_analytics)
        self.add_tool("delegate_reporting", "Run the Reporter agent", "", self._delegate_reporting)

    def goal(self) -> str:
        return f"Answer the policy question: {self.ctx.query[:200]!r}"

    def state(self) -> dict[str, Any]:
        i, p = self.ctx.intent, self.ctx.plan
        return {
            "intent": None
            if i is None
            else {"kind": i.kind, "sectors": i.sectors, "metrics": i.metrics, "window": [i.start_year, i.end_year]},
            "needs_clarification": bool(i and i.needs_clarification),
            "clarification": i.clarification if i else "",
            "clarified": self.clarified,
            "plan": None if p is None else {"required": len(p.required), "optional": len(p.optional), "steps": len(p.steps)},
            "extraction": self.extraction,
            "analytics": self.analytics,
            "reporting": self.reporting,
            "replanned": self.replanned,
        }

    def policy(self, state: dict[str, Any]) -> dict[str, Any]:
        return decide_coordinator(state)

    # ------------------------------------------------------------------ tools
    async def _interpret(self, args: dict[str, Any]) -> dict[str, Any]:
        c = self.ctx
        req = LLMRequest(
            task="interpret_query",
            system=intent_system_prompt(c.now_year),
            prompt=intent_prompt(c.query),
            max_tokens=600,
            context={"query": c.query, "now_year": c.now_year},
            validator=lambda t: validate_intent_text(t, now_year=c.now_year),
        )
        try:
            c.intent = (await c.llm(self.name, req)).parsed
        except LLMError as exc:
            c.warn(self.name, f"LLM interpretation unavailable ({exc}); using the rule-based parser")
            c.intent = heuristic_intent(c.query, now_year=c.now_year)
        i = c.intent
        label = ", ".join(i.sectors) or "all major sectors"
        return {
            "summary": f"Intent: {i.kind} | sectors: {label} | measures: {', '.join(i.metrics) or 'standard bundle'} | {i.start_year}-{i.end_year}"
            + (" | NEEDS CLARIFICATION" if i.needs_clarification else ""),
            "assumptions": i.assumptions,
        }

    async def _clarify(self, args: dict[str, Any]) -> dict[str, Any]:
        i = self.ctx.intent
        q = str(args.get("question") or (i.clarification if i else "Could you clarify your question?"))
        self.ctx.clarification = {"question": q, "suggestions": (i.suggestions if i else [])}
        self.clarified = True
        return {"summary": f"Asked the user: {q}"}

    async def _plan(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.ctx.intent is None:
            raise ToolError("interpret the query first")
        self.ctx.plan = build_plan(self.ctx.intent)
        p = self.ctx.plan
        self.ctx.emit(self.name, "status", "Workflow plan", {"plan": p.to_dict()})
        return {
            "summary": f"Plan: {len(p.required)} required + {len(p.optional)} optional datasets; {len(p.steps)} analysis steps ({', '.join(s['tool'] for s in p.steps)})"
        }

    async def _delegate_extraction(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.ctx.plan is None:
            raise ToolError("plan the workflow first")
        self.emit(
            "delegation", "Delegating to the Data Extraction agent", {"to": "extraction", "datasets": self.ctx.plan.datasets}
        )
        res = await ExtractionAgent(self.ctx).run()
        c = self.ctx
        missing = [k for k in c.plan.required if k not in c.processed and k not in c.skipped]  # type: ignore[union-attr]
        status = (
            "failed"
            if missing or res.status == "failed"
            else "partial"
            if c.skipped
            else "degraded"
            if any(p.prov.degraded for p in c.processed.values())
            else "ok"
        )
        self.extraction = {
            "status": status,
            "degraded": [k for k, p in c.processed.items() if p.prov.degraded],
            "skipped": sorted(c.skipped),
            "reason": res.final.get("reason") or (f"required datasets unavailable: {missing}" if missing else ""),
        }
        c.stage_status["extraction"] = status
        return {
            "summary": f"Extraction {status}: {len(c.processed)} datasets ready, {len(self.extraction['degraded'])} degraded, {len(c.skipped)} skipped",
            **self.extraction,
        }

    async def _replan(self, args: dict[str, Any]) -> dict[str, Any]:
        c = self.ctx
        before = len(c.plan.steps)  # type: ignore[union-attr]
        c.plan = build_plan(c.intent, skipped=set(c.skipped))  # type: ignore[arg-type]
        self.replanned = True
        c.emit(self.name, "status", "Re-planned workflow", {"plan": c.plan.to_dict(), "reason": str(args.get("reason", ""))})
        return {
            "summary": f"Re-planned without {', '.join(sorted(c.skipped))}: {before} -> {len(c.plan.steps)} steps. {' '.join(c.plan.notes)}"
        }

    async def _delegate_analytics(self, args: dict[str, Any]) -> dict[str, Any]:
        self.emit("delegation", "Delegating to the Analytics agent", {"to": "analytics", "steps": len(self.ctx.plan.steps)})  # type: ignore[union-attr]
        res = await AnalyticsAgent(self.ctx).run()
        real = [f for f in self.ctx.facts.all() if f.kind != "limitation"]
        status = "failed" if not real else "partial" if res.status in ("incomplete", "partial") else "ok"
        self.analytics = {"status": status, "facts": len(self.ctx.facts)}
        self.ctx.stage_status["analytics"] = status
        return {
            "summary": f"Analytics {status}: {len(real)} findings, {len(self.ctx.facts) - len(real)} limitation note(s), {len(self.ctx.charts)} chart(s)",
            **self.analytics,
        }

    async def _delegate_reporting(self, args: dict[str, Any]) -> dict[str, Any]:
        self.emit("delegation", "Delegating to the Reporter agent", {"to": "reporter", "facts": len(self.ctx.facts)})
        res = await ReporterAgent(self.ctx).run()
        v = self.ctx.verification or {}
        self.reporting = {"status": "ok" if self.ctx.report else "failed", "template_fallback": bool(v.get("template_fallback"))}
        self.ctx.stage_status["reporting"] = self.reporting["status"]
        if not self.ctx.report:
            raise ToolError(f"reporter ended without a report ({res.status})")
        return {
            "summary": f"Report ready (narrative: {self.ctx.narrative_source}; verification {'passed' if v.get('passed') else 'FAILED'})",
            **self.reporting,
        }

    async def run(self) -> AgentResult:
        res = await super().run()
        if res.status == "incomplete" and not self.ctx.report and not self.ctx.clarification:
            res.status = "failed"
        # Whether a finished run is 'completed' or 'degraded' is a fact about the stages, not the model's wording: a live
        # LLM answered {"status": "complete"} on a clean run, which would otherwise be mislabelled 'degraded'.
        if self.ctx.report and res.final.get("status") not in ("failed", "needs_clarification"):
            evidence = self.policy(self.state())
            if "final" in evidence:
                res.final["status"] = res.status = evidence["final"]["status"]
        return res
