import asyncio
from pathlib import Path

import pytest

from app.agents.workflow import run_analysis
from app.config import Settings
from tests.conftest import HEADLINE, make_ctx


@pytest.fixture(scope="module")
def module_settings(tmp_path_factory) -> Settings:
    return Settings(live_data=False, runtime_dir=Path(tmp_path_factory.mktemp("rt")), llm_provider_order="offline", http_backoff_s=0.0)


@pytest.fixture(scope="module")
def headline(module_settings):
    """One full offline run of the headline query, shared by the module's tests."""
    ctx = make_ctx(module_settings, query=HEADLINE)
    result = asyncio.run(run_analysis(ctx))
    return ctx, result
