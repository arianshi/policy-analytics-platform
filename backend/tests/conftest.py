"""Shared fixtures. Tests run offline: bundled snapshots as data, the rule-based provider (or scripted fakes) as the LLM."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.agents.context import RunContext
from app.config import Settings
from app.datasources.faults import FaultPlan
from app.datasources.registry import DataRegistry
from app.llm.factory import build_providers
from app.llm.providers.base import LLMProvider
from app.llm.router import CostTracker, LLMRouter
from app.llm.types import Completion, LLMError, LLMRequest

HEADLINE = "Analyse employment trends in the technology sector from 2020-2024"
NOW_YEAR = 2026


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        live_data=False,
        runtime_dir=tmp_path / "var",
        llm_provider_order="offline",
        http_backoff_s=0.0,
        rate_limit_per_min=0,
        openai_api_key="",
        gemini_api_key="",
        azure_openai_api_key="",
        bedrock_enabled=False,
        fault_injection="",
    )


class ScriptedProvider(LLMProvider):
    """A fake cloud LLM whose answers are produced by a callable - used to simulate hostile / flaky models."""

    paid = True

    def __init__(self, name: str, fn: Callable[[LLMRequest], str], model: str = "scripted-1"):
        self.name, self.model, self.fn = name, model, fn
        self.calls: list[LLMRequest] = []

    def configured(self) -> bool:
        return True

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        self.calls.append(req)
        out = self.fn(req)
        if isinstance(out, Exception):
            raise out
        return Completion(out, 120, 80)


@pytest.fixture
def scripted():
    return ScriptedProvider


def make_ctx(
    settings: Settings,
    *,
    query: str = HEADLINE,
    providers: list[LLMProvider] | None = None,
    faults: str = "",
    registry: DataRegistry | None = None,
    events: list | None = None,
) -> RunContext:
    router = LLMRouter(providers if providers is not None else build_providers(settings), settings)
    log = events if events is not None else []

    def emit(agent, type_, message, data=None):
        log.append((agent, type_, message, data or {}))

    ctx = RunContext(
        "test",
        query,
        settings,
        registry or DataRegistry(settings),
        router,
        FaultPlan.parse(faults),
        emit,
        NOW_YEAR,
        CostTracker(1.0),
    )
    ctx.events = log  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def ctx_factory(settings):
    return lambda **kw: make_ctx(settings, **kw)


@pytest.fixture
def run_analysis_offline(settings):
    from app.agents.workflow import run_analysis

    async def _run(query: str = HEADLINE, faults: str = "", providers=None):
        ctx = make_ctx(settings, query=query, faults=faults, providers=providers)
        result = await run_analysis(ctx)
        return ctx, result

    return _run


def pytest_collection_modifyitems(config, items):
    import os

    skip_live = pytest.mark.skip(reason="set RUN_LIVE=1 to hit real government APIs")
    skip_llm = pytest.mark.skip(reason="no LLM provider credentials configured")
    has_llm = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
    for item in items:
        if "live" in item.keywords and not os.environ.get("RUN_LIVE"):
            item.add_marker(skip_live)
        if "live_llm" in item.keywords and not has_llm:
            item.add_marker(skip_llm)


__all__ = ["HEADLINE", "NOW_YEAR", "LLMError", "ScriptedProvider", "make_ctx"]
