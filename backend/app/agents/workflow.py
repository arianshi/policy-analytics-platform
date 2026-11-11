"""Run one analysis end to end and shape the result document."""

from __future__ import annotations

import time
from typing import Any

from .context import RunContext
from .coordinator import CoordinatorAgent


async def run_analysis(ctx: RunContext) -> dict[str, Any]:
    t0 = time.perf_counter()
    ctx.emit("coordinator", "status", "Analysis started", {"query": ctx.query, "faults": ctx.faults.describe()})
    res = await CoordinatorAgent(ctx).run()
    status = res.final.get("status", res.status)
    if ctx.clarification:
        status = "needs_clarification"
    elif not ctx.report:
        status = "failed"
    elif status not in ("completed", "degraded"):
        status = "degraded" if ctx.report else "failed"
    # a clean run with fallbacks anywhere is still 'degraded' - disclosed, never hidden
    if status == "completed" and (ctx.warnings and any(p.prov.degraded for p in ctx.processed.values())):
        status = "degraded"
    cite_of = {c["dataset_key"]: c["id"] for c in (ctx.report or {}).get("citations", [])}
    facts = [{**f.to_dict(), "citations": [cite_of[k] for k in f.sources if k in cite_of]} for f in ctx.facts.all()]
    error = (
        ""
        if status in ("completed", "degraded", "needs_clarification")
        else (res.final.get("reason") or res.error or "analysis failed")
    )
    return {
        "status": status,
        "error": error,
        "query": ctx.query,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "intent": ctx.intent.model_dump() if ctx.intent else None,
        "plan": ctx.plan.to_dict() if ctx.plan else None,
        "clarification": ctx.clarification,
        "datasets": [
            {
                "key": k,
                "title": p.prov.title,
                "source": p.prov.source_name,
                "mode": p.prov.mode,
                "degraded": p.prov.degraded,
                "is_mock": p.prov.is_mock,
                "rows": p.dq.rows_out,
                "score": p.dq.score,
                "retrieved_at": p.prov.retrieved_at,
                "url": p.prov.url,
            }
            for k, p in ctx.processed.items()
        ],
        "skipped_datasets": sorted(ctx.skipped),
        "data_quality": [r.to_dict() for r in ctx.dq.values()],
        "facts": facts,
        "charts": ctx.charts,
        "report": ctx.report,
        "verification": ctx.verification,
        "llm": ctx.tracker.summary(),
        "warnings": ctx.warnings,
        "stages": ctx.stage_status,
    }
