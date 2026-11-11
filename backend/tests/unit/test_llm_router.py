import pytest

from app.config import Settings
from app.datasources.faults import FaultPlan
from app.llm.router import Breaker, CostTracker, LLMRouter, parse_json_object
from app.llm.types import LLMError, LLMRequest
from tests.conftest import ScriptedProvider


def req(validator=None, task="react_step"):
    return LLMRequest(task=task, system="s", prompt="p", validator=validator)


def make_router(providers, **overrides):
    s = Settings(llm_breaker_threshold=2, llm_breaker_cooldown_s=30, llm_max_retries=1, llm_budget_usd=0.5, **overrides)
    clock = {"t": 0.0}
    sleeps: list[float] = []

    async def sleep(x):
        sleeps.append(x)

    r = LLMRouter(providers, s, clock=lambda: clock["t"], sleep=sleep)
    r.test_clock, r.test_sleeps = clock, sleeps  # type: ignore[attr-defined]
    return r


async def test_uses_first_healthy_provider_and_tracks_cost():
    a = ScriptedProvider("a", lambda r: '{"x":1}', model="gpt-4o-mini")
    router = make_router([a])
    tracker = CostTracker(0.5)
    resp = await router.complete(req(), tracker=tracker)
    assert resp.provider == "a" and resp.parsed == '{"x":1}' and resp.cost_usd > 0
    assert tracker.cost_usd == resp.cost_usd and tracker.summary()["by_provider"] == {"a": 1}
    st = router.status()[0]
    assert st["calls"] == 1 and st["cost_usd"] > 0 and st["circuit"] == "closed"


async def test_falls_back_when_provider_errors():
    a = ScriptedProvider("a", lambda r: LLMError("boom", provider="a"))
    b = ScriptedProvider("b", lambda r: "ok")
    resp = await make_router([a, b]).complete(req())
    assert resp.provider == "b" and resp.fallbacks == [{"provider": "a", "error": "boom"}]


async def test_invalid_output_triggers_fallback_not_acceptance():
    def validator(text):
        if "good" not in text:
            raise ValueError("not good")
        return {"ok": True}

    a = ScriptedProvider("a", lambda r: "garbage")
    b = ScriptedProvider("b", lambda r: "good answer")
    resp = await make_router([a, b]).complete(req(validator))
    assert resp.provider == "b" and resp.parsed == {"ok": True} and "invalid output" in resp.fallbacks[0]["error"]


async def test_transient_errors_are_retried_once_then_fall_back():
    n = {"c": 0}

    def flaky(r):
        n["c"] += 1
        return LLMError("429", transient=True, provider="a")

    router = make_router([ScriptedProvider("a", flaky), ScriptedProvider("b", lambda r: "ok")])
    resp = await router.complete(req())
    assert resp.provider == "b" and n["c"] == 2 and router.test_sleeps  # type: ignore[attr-defined]


async def test_permanent_errors_are_not_retried():
    n = {"c": 0}

    def bad(r):
        n["c"] += 1
        return LLMError("400 bad request", transient=False, provider="a")

    await make_router([ScriptedProvider("a", bad), ScriptedProvider("b", lambda r: "ok")]).complete(req())
    assert n["c"] == 1


async def test_circuit_opens_skips_provider_then_recovers_after_cooldown():
    healthy = {"v": False}
    a = ScriptedProvider("a", lambda r: "ok" if healthy["v"] else LLMError("down", provider="a"))
    b = ScriptedProvider("b", lambda r: "fallback")
    router = make_router([a, b])
    for _ in range(2):  # threshold = 2 -> opens
        assert (await router.complete(req())).provider == "b"
    assert router.status()[0]["circuit"] == "open"
    calls_before = len(a.calls)
    assert (await router.complete(req())).provider == "b" and len(a.calls) == calls_before  # skipped, not called
    router.test_clock["t"] = 31.0  # type: ignore[attr-defined]
    healthy["v"] = True
    assert router.status()[0]["circuit"] == "half-open"
    assert (await router.complete(req())).provider == "a" and router.status()[0]["circuit"] == "closed"


def test_breaker_half_open_failure_reopens():
    t = {"now": 0.0}
    b = Breaker(2, 10, clock=lambda: t["now"])
    b.failure()
    b.failure()
    assert b.state == "open"
    t["now"] = 11
    assert b.state == "half-open" and b.allow()
    b.failure()
    assert b.state == "open"


async def test_all_providers_failing_raises_with_every_reason():
    router = make_router(
        [
            ScriptedProvider("a", lambda r: LLMError("x", provider="a")),
            ScriptedProvider("b", lambda r: LLMError("y", provider="b")),
        ]
    )
    with pytest.raises(LLMError) as e:
        await router.complete(req())
    assert "a: x" in str(e.value) and "b: y" in str(e.value)


async def test_budget_cap_skips_paid_providers_but_not_free_ones():
    paid = ScriptedProvider("paid", lambda r: "paid")
    free = ScriptedProvider("free", lambda r: "free")
    free.paid = False
    tracker = CostTracker(budget_usd=0.0)
    resp = await make_router([paid, free]).complete(req(), tracker=tracker)
    assert resp.provider == "free" and not paid.calls and resp.cost_usd == 0.0


async def test_fault_injection_fails_named_provider_only():
    a, b = ScriptedProvider("a", lambda r: "a"), ScriptedProvider("b", lambda r: "b")
    resp = await make_router([a, b]).complete(req(), faults=FaultPlan.parse("llm:a"))
    assert resp.provider == "b" and not a.calls


async def test_recorder_receives_successes_and_failures():
    recs = []
    s = Settings(llm_max_retries=0)
    router = LLMRouter(
        [ScriptedProvider("a", lambda r: LLMError("no", provider="a")), ScriptedProvider("b", lambda r: "ok")],
        s,
        recorder=recs.append,
    )
    await router.complete(req(), analysis_id="A1")
    assert [(r["provider"], r["ok"]) for r in recs] == [("a", False), ("b", True)] and recs[0]["analysis_id"] == "A1"


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! Here you go: {"a": {"b": 2}} Hope that helps.') == {"a": {"b": 2}}
    for bad in ("no json here", "[1,2]", '{"a": }'):
        with pytest.raises(ValueError):
            parse_json_object(bad)


async def test_invalid_output_falls_back_but_does_not_open_the_circuit():
    def validator(text):
        raise ValueError("never valid")

    a = ScriptedProvider("a", lambda r: "garbage")
    b = ScriptedProvider("b", lambda r: "fine")
    router = make_router([a, b])
    for _ in range(5):  # well past the breaker threshold of 2
        assert (
            await router.complete(req(validator=lambda t: t if t == "fine" else (_ for _ in ()).throw(ValueError("bad"))))
        ).provider == "b"
    assert router.status()[0]["circuit"] == "closed" and len(a.calls) == 5
