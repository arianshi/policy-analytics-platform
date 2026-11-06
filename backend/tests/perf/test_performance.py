"""Performance and load tests (offline, in-process). The standalone HTTP load test is scripts/load_test.py."""

import asyncio
import statistics
import time

import numpy as np
import pytest

from app.agents.workflow import run_analysis
from app.analytics import stats
from tests.conftest import HEADLINE, make_ctx
from tests.integration.test_service import make_service, wait

pytestmark = pytest.mark.perf


async def test_single_analysis_latency_budget(settings):
    t0 = time.perf_counter()
    r = await run_analysis(make_ctx(settings))
    elapsed = time.perf_counter() - t0
    assert r["status"] == "completed" and elapsed < 3.0, f"{elapsed:.2f}s"


async def test_load_20_concurrent_analyses(settings):
    s = settings.model_copy(update={"max_concurrent_analyses": 4})
    svc, db = make_service(s)
    t0 = time.perf_counter()
    ids = [(await svc.submit(HEADLINE))["id"] for _ in range(20)]
    recs = await asyncio.gather(*(wait(db, i, 120) for i in ids))
    wall = time.perf_counter() - t0
    durations = sorted(r["duration_ms"] for r in recs)
    assert all(r["status"] == "completed" for r in recs)
    p95 = durations[int(len(durations) * 0.95) - 1]
    print(f"\n20 analyses in {wall:.1f}s ({20 / wall:.1f}/s); duration p50={statistics.median(durations)}ms p95={p95}ms max={durations[-1]}ms")
    assert wall < 60 and p95 < 15000


def test_stats_scale_to_large_series():
    rng = np.random.default_rng(0)
    x = np.arange(200_000, dtype=float)
    y = 0.5 * x + rng.normal(size=x.size)
    t0 = time.perf_counter()
    stats.linear_trend(x, y)
    stats.correlation(x, y)
    stats.robust_anomalies(x, y, relative=False)
    stats.forecast_linear(x, y, 3)
    assert time.perf_counter() - t0 < 1.5


def test_verifier_throughput(settings):
    from app.reporting import template
    from app.reporting.verify import Item, VerifyContext, verify_items

    ctx = asyncio.run(_ctx_with_facts(settings))
    fd = [f.to_dict() for f in ctx.facts.all()]
    items = [Item(f["title"], f["text"], f["fact_ids"]) for f in template.build_findings(fd)] * 50
    t0 = time.perf_counter()
    res = verify_items(items, ctx.facts, VerifyContext((2020, 2024), (1998, 2027)))
    assert res.passed and time.perf_counter() - t0 < 2.0


async def _ctx_with_facts(settings):
    ctx = make_ctx(settings)
    await run_analysis(ctx)
    return ctx
