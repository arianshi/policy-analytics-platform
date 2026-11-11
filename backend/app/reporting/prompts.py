"""Prompts for the narrative LLM tasks. The model only ever sees computed facts and must cite them."""

from __future__ import annotations

import json
from typing import Any

INSIGHTS_SYSTEM = (
    "You are a policy analyst writing for government researchers. You are given FACTS computed by a deterministic engine. "
    "Write findings and implications using ONLY these facts.\n"
    "Rules: (1) every sentence must be supported by one or more cited fact ids; (2) copy numbers exactly as written in the facts - never "
    "compute, round differently, or introduce new numbers; (3) do not name data sources other than those in the facts; (4) do not claim causes - "
    "the evidence is descriptive/correlational; (5) facts marked mock=true are synthetic and must be described as MOCK/synthetic; "
    "(6) if a fact is a limitation, say so plainly.\n"
    'Return ONLY JSON: {"findings":[{"title":str,"text":str,"fact_ids":[str]}],"implications":[{"text":str,"fact_ids":[str]}]}. '
    "At most 6 findings and 4 implications."
)

REPORT_SYSTEM = (
    "You are a policy analyst. Using ONLY the supplied FACTS and FINDINGS, write an executive summary (2-4 sentences) and up to 3 recommendations. "
    "Same rules as before: cite fact ids for every sentence, copy numbers exactly, no new numbers, no causal claims, label MOCK data as synthetic.\n"
    'Return ONLY JSON: {"executive_summary":{"text":str,"fact_ids":[str]},"recommendations":[{"text":str,"fact_ids":[str]}]}.'
)


def facts_payload(facts: list[dict[str, Any]]) -> str:
    slim = [
        {"id": f["id"], "kind": f["kind"], "statement": f["statement"], "mock": f["mock"], "caveats": f["caveats"]} for f in facts
    ]
    return json.dumps(slim, ensure_ascii=False)


def insights_prompt(query: str, facts: list[dict[str, Any]], feedback: str = "") -> str:
    p = f"Question: {query[:600]}\nFACTS:\n{facts_payload(facts)}"
    if feedback:
        p += f"\n\nYour previous draft FAILED verification: {feedback}\nRewrite it fixing every problem; use only numbers present in the facts."
    return p


def report_prompt(query: str, facts: list[dict[str, Any]], findings: list[dict[str, Any]], feedback: str = "") -> str:
    p = f"Question: {query[:600]}\nFACTS:\n{facts_payload(facts)}\nFINDINGS:\n{json.dumps(findings, ensure_ascii=False)}"
    if feedback:
        p += f"\n\nYour previous draft FAILED verification: {feedback}\nRewrite it fixing every problem."
    return p
