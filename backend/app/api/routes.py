from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .. import __version__
from ..agents.intent import SUGGESTIONS
from ..datasources.catalog import CATALOG
from ..reporting.export import facts_to_csv, to_html, to_markdown
from .schemas import AnalysisCreated, AnalysisRequest

router = APIRouter(prefix="/api")
_hits: dict[str, deque[float]] = defaultdict(deque)


def _svc(request: Request):
    return request.app.state.service


def _rate_limit(request: Request) -> None:
    limit = request.app.state.settings.rate_limit_per_min
    if limit <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, detail={"code": "rate_limited", "message": f"Too many analyses: limit is {limit} per minute"})
    q.append(now)


@router.get("/health")
async def health(request: Request):
    svc = _svc(request)
    s = request.app.state.settings
    ok = svc.db.ping()
    return {"status": "ok" if ok else "degraded", "version": __version__, "database": "ok" if ok else "error",
            "live_data": s.live_data, "running_analyses": len(svc.tasks), "llm_providers_configured": [p["name"] for p in svc.router.status() if p["configured"]]}


@router.get("/samples")
async def samples():
    return {"queries": SUGGESTIONS}


@router.post("/analyses", status_code=202, response_model=AnalysisCreated)
async def create_analysis(body: AnalysisRequest, request: Request):
    _rate_limit(request)
    return await _svc(request).submit(body.query, body.simulate_failures)


@router.get("/analyses")
async def list_analyses(request: Request, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return {"items": _svc(request).db.list_analyses(limit, offset)}


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str, request: Request):
    rec = _svc(request).db.get_analysis(analysis_id)
    if not rec:
        raise HTTPException(404, detail={"code": "not_found", "message": f"analysis '{analysis_id}' not found"})
    return rec


@router.get("/analyses/{analysis_id}/events")
async def get_events(analysis_id: str, request: Request, after: int = Query(0, ge=0)):
    svc = _svc(request)
    if not svc.db.get_analysis(analysis_id):
        raise HTTPException(404, detail={"code": "not_found", "message": f"analysis '{analysis_id}' not found"})
    return {"events": svc.db.get_events(analysis_id, after)}


@router.post("/analyses/{analysis_id}/cancel")
async def cancel_analysis(analysis_id: str, request: Request):
    if not await _svc(request).cancel(analysis_id):
        raise HTTPException(409, detail={"code": "not_running", "message": "analysis is not running"})
    return {"id": analysis_id, "status": "cancelled"}


@router.delete("/analyses/{analysis_id}", status_code=204)
async def delete_analysis(analysis_id: str, request: Request):
    svc = _svc(request)
    await svc.cancel(analysis_id)
    if not svc.db.delete_analysis(analysis_id):
        raise HTTPException(404, detail={"code": "not_found", "message": f"analysis '{analysis_id}' not found"})
    return Response(status_code=204)


@router.get("/analyses/{analysis_id}/export")
async def export_analysis(analysis_id: str, request: Request, format: str = Query("md", pattern="^(md|json|csv|html)$")):
    rec = _svc(request).db.get_analysis(analysis_id)
    if not rec:
        raise HTTPException(404, detail={"code": "not_found", "message": f"analysis '{analysis_id}' not found"})
    result = rec.get("result")
    if not result or not result.get("report"):
        raise HTTPException(409, detail={"code": "no_report", "message": f"analysis is '{rec['status']}' and has no report to export"})
    name = f"analysis-{analysis_id}"
    if format == "json":
        return Response(json.dumps(result, indent=2, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if format == "csv":
        return Response(facts_to_csv(result), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}-facts.csv"'})
    if format == "html":
        return Response(to_html(result), media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{name}.html"'})
    return Response(to_markdown(result), media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@router.get("/providers")
async def providers(request: Request):
    svc = _svc(request)
    return {"providers": svc.router.status(), "totals": svc.db.llm_totals(), "budget_usd_per_analysis": request.app.state.settings.llm_budget_usd}


@router.get("/datasets")
async def datasets(request: Request):
    stored = {d["key"]: d for d in _svc(request).db.list_datasets()}
    return {"datasets": [{"key": s.key, "title": s.title, "source": s.source_name, "format": s.fmt, "metric": s.metric, "url": s.page_url, "mock": s.is_mock,
                          "stored": stored.get(s.key)} for s in CATALOG]}


@router.get("/datasets/{key}/rows")
async def dataset_rows(key: str, request: Request, limit: int = Query(200, ge=1, le=2000)):
    if key not in {s.key for s in CATALOG}:
        raise HTTPException(404, detail={"code": "not_found", "message": f"unknown dataset '{key}'"})
    return {"key": key, "rows": _svc(request).db.dataset_rows(key, limit)}
