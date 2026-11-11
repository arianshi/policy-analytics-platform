"""Deterministic rule-based 'model' - the last link of the fallback chain.

It needs no network or keys and returns the same JSON contracts as a real LLM, built from the same inputs:
the agents' policies decide ReAct steps, the heuristic parser interprets questions, and the template engine
narrates facts. This keeps the platform fully functional (and its tests deterministic) when every cloud LLM is down.
"""

from __future__ import annotations

import json

from ...agents.intent import heuristic_intent
from ...agents.policies import POLICIES
from ...reporting import template
from ..types import Completion, LLMError, LLMRequest
from .base import LLMProvider


class OfflineProvider(LLMProvider):
    name = "offline"
    model = "offline"
    paid = False

    def configured(self) -> bool:
        return True

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        c = req.context
        try:
            if req.task == "interpret_query":
                out = heuristic_intent(c["query"], now_year=c["now_year"]).model_dump()
            elif req.task == "react_step":
                out = POLICIES[c["agent"]](c["state"])
            elif req.task == "generate_insights":
                out = {"findings": template.build_findings(c["facts"]), "implications": template.build_implications(c["facts"])}
            elif req.task == "write_report":
                out = {
                    "executive_summary": template.build_summary(c["facts"]),
                    "recommendations": template.build_recommendations(c["facts"]),
                }
            else:
                raise LLMError(f"offline provider cannot handle task '{req.task}'", provider=self.name)
        except KeyError as exc:
            raise LLMError(f"offline provider missing context {exc}", provider=self.name) from exc
        text = json.dumps(out, default=str)
        return Completion(text, len(req.prompt) // 4, len(text) // 4)
