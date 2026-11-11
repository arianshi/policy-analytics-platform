"""Reporter Agent: LLM-written narrative over verified facts, with hallucination checks and a safe fallback."""

from __future__ import annotations

from typing import Any

from ..llm.router import parse_json_object
from ..llm.types import LLMError, LLMRequest
from ..reporting import prompts, template
from ..reporting.report import assemble_report
from ..reporting.verify import Item, VerificationResult, VerifyContext, verify_items
from .base import ReActAgent, ToolError
from .context import RunContext
from .policies import decide_reporter


def _parse_items(text: str, keys: tuple[str, ...]) -> dict[str, Any]:
    obj = parse_json_object(text)
    out: dict[str, Any] = {}
    for key in keys:
        raw = obj.get(key)
        if key == "executive_summary":
            if not isinstance(raw, dict):
                raise ValueError("executive_summary must be an object")
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError(f"'{key}' must be a list")
        items = []
        for it in raw[:8]:
            if not isinstance(it, dict) or not isinstance(it.get("text"), str) or not it["text"].strip():
                raise ValueError(f"each '{key}' item needs non-empty 'text'")
            ids = it.get("fact_ids", [])
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("'fact_ids' must be a list of strings")
            entry = {"text": it["text"].strip()[:1200], "fact_ids": ids}
            if key == "findings":
                entry["title"] = str(it.get("title", "Finding"))[:80]
            items.append(entry)
        out[key] = items[0] if key == "executive_summary" else items
    return out


class ReporterAgent(ReActAgent):
    name = "reporter"
    role = (
        "You write the final report. You may only narrate the supplied facts. Every draft is verified against the facts (numbers, citations, years, sources); "
        "failed drafts are revised once and then replaced by a rule-based draft, so unsupported claims never reach the reader."
    )

    def __init__(self, ctx: RunContext):
        super().__init__(ctx, max_steps=10)
        self.draft: dict[str, Any] | None = None
        self.verified: VerificationResult | None = None
        self.revisions = 0
        self.template_used = False
        self.composed = False
        self.source = ""
        self.add_tool("draft_findings", "Ask the LLM to draft findings and implications from the facts", "", self._draft)
        self.add_tool("verify_draft", "Verify the draft against the facts (hallucination check)", "", self._verify)
        self.add_tool(
            "revise_draft",
            "Revise the draft: mode 'llm' re-prompts with failures, 'template' uses the rule-based draft",
            "mode: 'llm'|'template'",
            self._revise,
        )
        self.add_tool(
            "compose_report", "Write executive summary + recommendations, verify, assemble the cited report", "", self._compose
        )

    def goal(self) -> str:
        return f"Produce a grounded, cited report with {len(self.ctx.facts)} facts available."

    def state(self) -> dict[str, Any]:
        v = self.verified
        return {
            "draft": None if self.draft is None else {"findings": len(self.draft["findings"])},
            "verified": None if v is None else {"passed": v.passed, "errors": len(v.errors)},
            "revisions": self.revisions,
            "template_used": self.template_used,
            "composed": self.composed,
        }

    def policy(self, state: dict[str, Any]) -> dict[str, Any]:
        return decide_reporter(state)

    def can_finish(self, state: dict[str, Any], final: dict[str, Any]) -> str | None:
        return None if self.composed else "the report has not been composed yet"

    # ------------------------------------------------------------------ helpers
    def _facts(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self.ctx.facts.all()]

    def _vctx(self) -> VerifyContext:
        i = self.ctx.intent
        years = [int(f.period[0]) for f in self.ctx.facts.all() if f.period] + [
            int(f.period[1]) for f in self.ctx.facts.all() if f.period
        ]
        lo = min([1998, *years]) if years else 1998
        hi = max([self.ctx.now_year - 1, i.end_year, *years])  # type: ignore[union-attr]
        return VerifyContext((i.start_year, i.end_year), (lo, hi))  # type: ignore[union-attr]

    def _items(self, draft: dict[str, Any]) -> list[Item]:
        items = [Item(f"finding:{f.get('title', i)}", f["text"], f["fact_ids"]) for i, f in enumerate(draft.get("findings", []))]
        items += [Item(f"implication:{i}", f["text"], f["fact_ids"]) for i, f in enumerate(draft.get("implications", []))]
        return items

    async def _ask(
        self, task: str, system: str, prompt: str, keys: tuple[str, ...], facts: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str]:
        req = LLMRequest(
            task=task,
            system=system,
            prompt=prompt,
            max_tokens=1800,
            context={"facts": facts},
            validator=lambda t: _parse_items(t, keys),
        )
        resp = await self.ctx.llm(self.name, req)
        return resp.parsed, ("offline-rule-based" if resp.provider == "offline" else f"llm:{resp.provider}")

    # ------------------------------------------------------------------ tools
    async def _draft(self, args: dict[str, Any]) -> dict[str, Any]:
        facts = self._facts()
        if not facts:
            raise ToolError("no facts to narrate")
        try:
            self.draft, self.source = await self._ask(
                "generate_insights",
                prompts.INSIGHTS_SYSTEM,
                prompts.insights_prompt(self.ctx.query, facts),
                ("findings", "implications"),
                facts,
            )
        except LLMError as exc:
            self.ctx.warn(self.name, f"No LLM could draft the narrative ({exc}); using the rule-based draft")
            self.draft = {"findings": template.build_findings(facts), "implications": template.build_implications(facts)}
            self.source, self.template_used = "template-fallback", True
        self.verified = None
        return {
            "summary": f"Drafted {len(self.draft['findings'])} findings and {len(self.draft['implications'])} implications ({self.source})"
        }

    async def _verify(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.draft is None:
            raise ToolError("nothing to verify - draft first")
        self.verified = verify_items(self._items(self.draft), self.ctx.facts, self._vctx())
        v = self.verified
        for issue in v.errors[:5]:
            self.emit(
                "warning",
                f"Hallucination check: {issue.code} in {issue.item}: {issue.text!r} {issue.detail}",
                {"issue": issue.__dict__},
            )
        return {
            "summary": f"Verification {'passed' if v.passed else 'FAILED'}: {v.numbers_checked} numbers checked, {len(v.errors)} error(s), "
            f"grounding {v.grounding_score}",
            "passed": v.passed,
            "errors": len(v.errors),
        }

    async def _revise(self, args: dict[str, Any]) -> dict[str, Any]:
        facts = self._facts()
        mode = args.get("mode", "llm")
        if mode == "template" or self.revisions >= 1:
            self.draft = {"findings": template.build_findings(facts), "implications": template.build_implications(facts)}
            self.template_used, self.source = True, "template-fallback"
        else:
            feedback = self.verified.feedback() if self.verified else ""
            try:
                self.draft, self.source = await self._ask(
                    "generate_insights",
                    prompts.INSIGHTS_SYSTEM,
                    prompts.insights_prompt(self.ctx.query, facts, feedback),
                    ("findings", "implications"),
                    facts,
                )
            except LLMError:
                self.draft = {"findings": template.build_findings(facts), "implications": template.build_implications(facts)}
                self.template_used, self.source = True, "template-fallback"
        self.revisions += 1
        self.verified = None
        return {"summary": f"Revised draft ({'rule-based template' if self.template_used else self.source})"}

    async def _compose(self, args: dict[str, Any]) -> dict[str, Any]:
        facts = self._facts()
        assert self.draft is not None
        tail: dict[str, Any] | None = None
        if not self.template_used:
            try:
                tail, _ = await self._ask(
                    "write_report",
                    prompts.REPORT_SYSTEM,
                    prompts.report_prompt(self.ctx.query, facts, self.draft["findings"]),
                    ("executive_summary", "recommendations"),
                    facts,
                )
                check = verify_items(
                    [
                        Item("executive_summary", tail["executive_summary"]["text"], tail["executive_summary"]["fact_ids"]),
                        *[Item(f"recommendation:{i}", r["text"], r["fact_ids"]) for i, r in enumerate(tail["recommendations"])],
                    ],
                    self.ctx.facts,
                    self._vctx(),
                )
                if not check.passed:
                    self.emit(
                        "warning",
                        f"Summary/recommendations failed verification ({len(check.errors)} errors); using the rule-based versions",
                        {},
                    )
                    tail = None
            except LLMError:
                tail = None
        if tail is None:
            tail = {"executive_summary": template.build_summary(facts), "recommendations": template.build_recommendations(facts)}
            if self.source.startswith("llm:"):
                self.source += "+template-summary"
        narrative = {**self.draft, **tail}
        final = verify_items(
            [
                Item("executive_summary", narrative["executive_summary"]["text"], narrative["executive_summary"]["fact_ids"]),
                *self._items(narrative),
                *[Item(f"recommendation:{i}", r["text"], r["fact_ids"]) for i, r in enumerate(narrative["recommendations"])],
            ],
            self.ctx.facts,
            self._vctx(),
        )
        self.ctx.verification = {**final.to_dict(), "revisions": self.revisions, "template_fallback": self.template_used}
        self.ctx.narrative, self.ctx.narrative_source = narrative, self.source
        self.ctx.report = assemble_report(
            query=self.ctx.query,
            intent=self.ctx.intent.model_dump(),
            facts=facts,
            processed=self.ctx.processed,
            dq=self.ctx.dq,  # type: ignore[union-attr]
            narrative=narrative,
            narrative_source=self.source,
            verification=self.ctx.verification,
        )
        self.ctx.report["facts_cited"] = sorted(
            {
                i
                for sec in (narrative["findings"], narrative["implications"], narrative["recommendations"])
                for it in sec
                for i in it["fact_ids"]
            }
        )
        self.composed = True
        return {
            "summary": f"Report composed: {len(narrative['findings'])} findings, {len(self.ctx.report['citations'])} sources cited; "
            f"verification {'passed' if final.passed else 'FAILED'} ({final.numbers_checked} numbers)"
        }
