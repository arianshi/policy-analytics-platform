"""Multi-provider LLM router: ordered fallback, retries, circuit breakers, validation, cost tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..datasources.faults import FaultPlan
from ..events import Emit
from .pricing import estimate_cost, estimate_tokens
from .providers.base import LLMProvider
from .types import InvalidOutput, LLMError, LLMRequest, LLMResponse

log = logging.getLogger(__name__)


class Breaker:
    """Opens after ``threshold`` consecutive failures; lets one trial call through after ``cooldown_s``."""

    def __init__(self, threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown_s, self._clock = threshold, cooldown_s, clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self._clock() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold or self.opened_at is not None:
            self.opened_at = self._clock()


@dataclass
class ProviderStats:
    calls: int = 0
    failures: int = 0
    skipped: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms_total: int = 0
    last_error: str = ""


@dataclass
class CostTracker:
    """Per-analysis LLM usage; also enforces the soft budget."""

    budget_usd: float = 0.5
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, rec: dict[str, Any]) -> None:
        self.calls.append(rec)

    @property
    def cost_usd(self) -> float:
        return round(sum(c.get("cost_usd", 0.0) for c in self.calls if c.get("ok")), 6)

    @property
    def over_budget(self) -> bool:
        return self.cost_usd >= self.budget_usd

    def summary(self) -> dict[str, Any]:
        ok = [c for c in self.calls if c.get("ok")]
        by_provider: dict[str, int] = {}
        for c in ok:
            by_provider[c["provider"]] = by_provider.get(c["provider"], 0) + 1
        fallbacks = [c for c in self.calls if not c.get("ok")]
        return {
            "calls": len(ok),
            "failed_attempts": len(fallbacks),
            "input_tokens": sum(c.get("input_tokens", 0) for c in ok),
            "output_tokens": sum(c.get("output_tokens", 0) for c in ok),
            "cost_usd": self.cost_usd,
            "by_provider": by_provider,
            "fallback_events": [{"task": c["task"], "provider": c["provider"], "error": c.get("error", "")} for c in fallbacks][
                :20
            ],
        }


class LLMRouter:
    def __init__(
        self,
        providers: list[LLMProvider],
        settings: Settings,
        *,
        recorder: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep=asyncio.sleep,
    ):
        self.providers = providers
        self.s = settings
        self.recorder = recorder
        self._sleep = sleep
        self.breakers = {
            p.name: Breaker(settings.llm_breaker_threshold, settings.llm_breaker_cooldown_s, clock) for p in providers
        }
        self.stats = {p.name: ProviderStats() for p in providers}

    # ---------------------------------------------------------------- public
    def status(self) -> list[dict[str, Any]]:
        out = []
        for p in self.providers:
            st, br = self.stats[p.name], self.breakers[p.name]
            out.append(
                {
                    "name": p.name,
                    "model": p.model,
                    "configured": p.configured(),
                    "circuit": br.state,
                    "calls": st.calls,
                    "failures": st.failures,
                    "skipped": st.skipped,
                    "cost_usd": round(st.cost_usd, 6),
                    "input_tokens": st.input_tokens,
                    "output_tokens": st.output_tokens,
                    "avg_latency_ms": int(st.latency_ms_total / st.calls) if st.calls else 0,
                    "last_error": st.last_error,
                }
            )
        return out

    async def complete(
        self,
        req: LLMRequest,
        *,
        agent: str = "llm",
        emit: Emit | None = None,
        tracker: CostTracker | None = None,
        faults: FaultPlan | None = None,
        analysis_id: str | None = None,
    ) -> LLMResponse:
        faults = faults or FaultPlan()
        tracker = tracker or CostTracker(self.s.llm_budget_usd)
        tried: list[dict[str, str]] = []

        def say(kind: str, msg: str, data: dict | None = None) -> None:
            if emit:
                emit(agent, kind, msg, data)

        for p in self.providers:
            if not p.configured():
                continue
            br, st = self.breakers[p.name], self.stats[p.name]
            if p.paid and tracker.over_budget:
                st.skipped += 1
                tried.append({"provider": p.name, "error": "budget exceeded"})
                say("warning", f"LLM budget ${tracker.budget_usd:.2f} reached - skipping {p.name}", {"provider": p.name})
                continue
            if not br.allow():
                st.skipped += 1
                tried.append({"provider": p.name, "error": "circuit open"})
                say("warning", f"{p.name} circuit is open - skipping", {"provider": p.name})
                continue
            try:
                resp = await self._call(p, req, faults, tracker, analysis_id, agent)
            except LLMError as exc:
                if not isinstance(
                    exc, InvalidOutput
                ):  # bad *format* is not an outage: fall back for this request, keep the circuit closed
                    br.failure()
                st.failures += 1
                st.last_error = str(exc)[:200]
                tried.append({"provider": p.name, "error": str(exc)[:160]})
                say(
                    "warning",
                    f"{p.name} failed ({exc}); falling back to the next provider",
                    {"provider": p.name, "task": req.task},
                )
                continue
            br.success()
            resp.fallbacks = tried
            say(
                "llm",
                f"{req.task} answered by {p.name}/{p.model}",
                {
                    "provider": p.name,
                    "model": p.model,
                    "tokens_in": resp.input_tokens,
                    "tokens_out": resp.output_tokens,
                    "cost_usd": resp.cost_usd,
                    "latency_ms": resp.latency_ms,
                    "fallbacks": len(tried),
                },
            )
            return resp
        raise LLMError("all LLM providers failed: " + "; ".join(f"{t['provider']}: {t['error']}" for t in tried))

    # --------------------------------------------------------------- internals
    async def _call(
        self, p: LLMProvider, req: LLMRequest, faults: FaultPlan, tracker: CostTracker, analysis_id: str | None, agent: str
    ) -> LLMResponse:
        st = self.stats[p.name]
        attempts = 1 + max(0, self.s.llm_max_retries)
        last: LLMError | None = None
        for attempt in range(attempts):
            t0 = time.perf_counter()
            try:
                if faults.llm_fault(p.name):
                    raise LLMError(f"{p.name}: simulated outage (fault injection)", provider=p.name)
                comp = await p.complete(req, timeout_s=self.s.llm_timeout_s)
                parsed: Any = comp.text
                if req.validator:
                    try:
                        parsed = req.validator(comp.text)
                    except (ValueError, KeyError, TypeError) as exc:
                        raise InvalidOutput(f"{p.name}: invalid output ({exc})", provider=p.name) from exc
                latency = int((time.perf_counter() - t0) * 1000)
                tin = comp.input_tokens or estimate_tokens(req.system + req.prompt)
                tout = comp.output_tokens or estimate_tokens(comp.text)
                cost = 0.0 if not p.paid else estimate_cost(p.model, tin, tout)
                st.calls += 1
                st.input_tokens += tin
                st.output_tokens += tout
                st.cost_usd += cost
                st.latency_ms_total += latency
                self._record(tracker, analysis_id, agent, req, p, tin, tout, cost, latency, True, "")
                return LLMResponse(comp.text, parsed, p.name, p.model, tin, tout, cost, latency)
            except LLMError as exc:
                last = exc
                self._record(
                    tracker, analysis_id, agent, req, p, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False, str(exc)[:200]
                )
                if exc.transient and attempt < attempts - 1:
                    await self._sleep(min(0.5 * (2**attempt), 4.0))
                    continue
                raise
        assert last is not None
        raise last

    def _record(self, tracker: CostTracker, analysis_id, agent, req, p, tin, tout, cost, latency, ok, error) -> None:
        rec = {
            "analysis_id": analysis_id,
            "agent": agent,
            "task": req.task,
            "provider": p.name,
            "model": p.model,
            "input_tokens": tin,
            "output_tokens": tout,
            "cost_usd": cost,
            "latency_ms": latency,
            "ok": ok,
            "error": error,
        }
        tracker.add(rec)
        if self.recorder:
            try:
                self.recorder(rec)
            except Exception:  # never let bookkeeping break an analysis
                log.exception("llm call recorder failed")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text, tolerating markdown fences and leading/trailing prose."""
    t = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in output") from None
        try:
            obj = json.loads(t[start : end + 1])
        except ValueError as exc:
            raise ValueError(f"malformed JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj
