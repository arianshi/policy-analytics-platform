import asyncio

import pytest

from app.datasources.registry import DataRegistry
from app.db import Database
from app.events import EventBus
from app.llm.factory import build_providers
from app.llm.router import LLMRouter
from app.service import AnalysisService
from tests.conftest import HEADLINE, ScriptedProvider


def make_service(settings, providers=None):
    db = Database(":memory:")
    router = LLMRouter(providers or build_providers(settings), settings, recorder=db.add_llm_call)
    return AnalysisService(settings, db, DataRegistry(settings), router, EventBus()), db


async def wait(db, aid, timeout=20):
    for _ in range(int(timeout / 0.05)):
        if db.get_analysis(aid)["status"] in ("completed", "degraded", "failed", "cancelled", "needs_clarification"):
            return db.get_analysis(aid)
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


async def test_running_analysis_can_be_cancelled(settings):
    gate = asyncio.Event()

    class Blocking(ScriptedProvider):
        async def complete(self, req, *, timeout_s):
            await gate.wait()
            return await super().complete(req, timeout_s=timeout_s)

    svc, db = make_service(settings, [Blocking("block", lambda r: "{}")])
    aid = (await svc.submit(HEADLINE))["id"]
    await asyncio.sleep(0.2)
    assert db.get_analysis(aid)["status"] == "running"
    assert await svc.cancel(aid) is True
    rec = db.get_analysis(aid)
    assert rec["status"] == "cancelled" and db.get_events(aid)[-1]["type"] == "done"
    assert await svc.cancel(aid) is False


async def test_analysis_timeout_marks_failed_and_frees_the_worker(settings):
    gate = asyncio.Event()

    class Blocking(ScriptedProvider):
        async def complete(self, req, *, timeout_s):
            await gate.wait()

    s = settings.model_copy(update={"analysis_timeout_s": 0.3})
    svc, db = make_service(s, [Blocking("block", lambda r: "{}")])
    rec = await wait(db, (await svc.submit(HEADLINE))["id"])
    assert rec["status"] == "failed" and "time limit" in rec["error"]


async def test_unexpected_crash_never_leaves_an_analysis_running(settings, monkeypatch):
    svc, db = make_service(settings)

    async def boom(ctx):
        raise RuntimeError("bug")

    monkeypatch.setattr("app.service.run_analysis", boom)
    rec = await wait(db, (await svc.submit(HEADLINE))["id"])
    assert rec["status"] == "failed" and "RuntimeError" in rec["error"]


async def test_concurrency_is_bounded_by_the_semaphore(settings):
    s = settings.model_copy(update={"max_concurrent_analyses": 2})
    svc, db = make_service(s)
    peak = {"now": 0, "max": 0}
    original = svc.db.set_status

    def tracking(aid, status):
        if status == "running":
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        original(aid, status)

    svc.db.set_status = tracking  # type: ignore[method-assign]
    orig_save = svc.db.save_result

    def save(aid, status, result, error=""):
        peak["now"] -= 1
        orig_save(aid, status, result, error)

    svc.db.save_result = save  # type: ignore[method-assign]
    ids = [(await svc.submit(HEADLINE))["id"] for _ in range(5)]
    recs = [await wait(db, i, 60) for i in ids]
    assert all(r["status"] == "completed" for r in recs) and peak["max"] <= 2


async def test_event_bus_delivers_live_events_in_order(settings):
    svc, db = make_service(settings)
    aid = (await svc.submit(HEADLINE))["id"]
    q = svc.bus.subscribe(aid)
    got = []
    while True:
        ev = await asyncio.wait_for(q.get(), 20)
        got.append(ev)
        if ev.type == "done":
            break
    assert [e.seq for e in got] == sorted(e.seq for e in got) and {"extraction", "reporter"} <= {e.agent for e in got}
    assert db.get_events(aid)[-1]["seq"] >= got[-1].seq


def test_pytest_asyncio_is_configured():
    assert pytest
