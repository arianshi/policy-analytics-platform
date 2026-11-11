import json

import httpx
import pandas as pd
import pytest

from app.data_quality import process_dataset
from app.datasources.base import DataSourceError, SchemaError
from app.datasources.catalog import SPEC_BY_KEY, datasets_for_metrics, search_catalog
from app.datasources.faults import FaultPlan
from app.datasources.http import get_with_retry
from app.datasources.registry import DataRegistry


def sleeper():
    calls: list[float] = []

    async def sleep(s):
        calls.append(s)

    return sleep, calls


def datagov_transport(settings, hits: dict | None = None, *, not_ready: int = 0, status_code: int = 200):
    """Fake Data.gov.sg: poll-download -> presigned URL -> CSV (served from the bundled snapshot)."""
    hits = hits if hits is not None else {}

    def handler(req: httpx.Request) -> httpx.Response:
        hits[req.url.path] = hits.get(req.url.path, 0) + 1
        if req.url.path.endswith("/poll-download"):
            if status_code != 200:
                return httpx.Response(status_code)
            if hits[req.url.path] <= not_ready:
                return httpx.Response(200, json={"code": 0, "data": {"status": "IN_PROGRESS"}})
            return httpx.Response(
                200, json={"code": 0, "data": {"status": "DOWNLOAD_SUCCESS", "url": "https://s3.example.test/f.csv"}}
            )
        return httpx.Response(200, content=(settings.snapshot_dir / "mom_job_vacancies.csv").read_bytes())

    return httpx.MockTransport(handler)


# ----------------------------------------------------------------------------- HTTP retry/backoff
async def test_retries_429_honouring_retry_after_then_succeeds(settings):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, headers={"retry-after": "2"}) if n["c"] < 3 else httpx.Response(200, text="ok")

    sleep, calls = sleeper()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        resp = await get_with_retry(c, "http://x.test", settings.model_copy(update={"http_retries": 4}), source="t", sleep=sleep)
    assert resp.text == "ok" and n["c"] == 3 and calls == [2.0, 2.0]


async def test_exhausted_retries_raise_typed_error(settings):
    sleep, _ = sleeper()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as c:
        with pytest.raises(DataSourceError) as e:
            await get_with_retry(c, "http://x.test", settings.model_copy(update={"http_retries": 2}), source="t", sleep=sleep)
    assert e.value.code == "unavailable" and e.value.retryable


async def test_404_is_not_retried(settings):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(DataSourceError) as e:
            await get_with_retry(c, "http://x.test", settings.model_copy(update={"http_retries": 5}), source="t")
    assert e.value.code == "not_found" and n["c"] == 1


async def test_timeout_maps_to_timeout_code(settings):
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    sleep, _ = sleeper()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(DataSourceError) as e:
            await get_with_retry(c, "http://x.test", settings.model_copy(update={"http_retries": 2}), source="t", sleep=sleep)
    assert e.value.code == "timeout"


# ------------------------------------------------------------------------------- live adapters (mocked)
async def test_datagov_live_fetch_then_cache(settings):
    s = settings.model_copy(update={"live_data": True})
    hits: dict = {}
    sleep, _ = sleeper()
    reg = DataRegistry(s, transport=datagov_transport(s, hits), sleep=sleep)
    first = await reg.acquire("mom.job_vacancies")
    assert first.prov.mode == "live" and not first.prov.degraded and len(first.df) > 1000
    second = await reg.acquire("mom.job_vacancies")
    assert second.prov.mode == "cache" and hits["/v1/public/api/datasets/d_01966fdb498fa7fa2863635e25539693/poll-download"] == 1


async def test_datagov_polls_until_download_ready(settings):
    s = settings.model_copy(update={"live_data": True})
    sleep, calls = sleeper()
    reg = DataRegistry(s, transport=datagov_transport(s, not_ready=2), sleep=sleep)
    raw = await reg.acquire("mom.job_vacancies")
    assert raw.prov.mode == "live" and len(calls) >= 2


async def test_live_failure_raises_but_snapshot_is_available_as_degraded_fallback(settings):
    s = settings.model_copy(update={"live_data": True, "http_retries": 2})
    sleep, _ = sleeper()
    reg = DataRegistry(s, transport=datagov_transport(s, status_code=503), sleep=sleep)
    with pytest.raises(DataSourceError) as e:
        await reg.acquire("mom.job_vacancies")
    assert e.value.retryable
    assert reg.has_snapshot("mom.job_vacancies")
    snap = reg.acquire_snapshot("mom.job_vacancies")
    assert snap.prov.mode == "snapshot" and snap.prov.degraded and "unavailable" in " ".join(snap.prov.notes)


async def test_singstat_json_adapter(settings):
    s = settings.model_copy(update={"live_data": True})
    body = (settings.snapshot_dir / "singstat_M015731.json").read_bytes()
    seen = {}

    def handler(req):
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, content=body)

    reg = DataRegistry(s, transport=httpx.MockTransport(handler))
    raw = await reg.acquire("singstat.gdp_by_industry")
    assert "timeFilter" in seen["params"] and raw.prov.mode == "live"
    p = process_dataset(raw, now_year=2026)
    ict = p.df[p.df.sector == "ict"].set_index("year").value
    assert ict[2024] == pytest.approx(42839.0)


async def test_unparseable_json_payload_is_a_schema_error(settings):
    s = settings.model_copy(update={"live_data": True})
    reg = DataRegistry(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>Service Unavailable</html>")))
    with pytest.raises(SchemaError):
        await reg.acquire("singstat.gdp_by_industry")


# --------------------------------------------------------------------------- fault injection + modes
async def test_fault_plan_parsing():
    f = FaultPlan.parse("source:mom:rate_limited, source:singstat, llm:openai")
    assert f.source_fault("mom") == "rate_limited" and f.source_fault("singstat") == "unavailable" and f.llm_fault("openai")
    assert not f.llm_fault("gemini") and FaultPlan.parse("llm:*").llm_fault("anything") and not FaultPlan.parse("")


@pytest.mark.parametrize(
    "mode,code,retryable",
    [("rate_limited", "rate_limited", True), ("timeout", "timeout", True), ("unavailable", "unavailable", False)],
)
async def test_injected_source_faults(settings, mode, code, retryable):
    reg = DataRegistry(settings)
    with pytest.raises(DataSourceError) as e:
        await reg.acquire("mom.job_vacancies", faults=FaultPlan.parse(f"source:mom:{mode}"))
    assert e.value.code == code and e.value.retryable is retryable


async def test_injected_schema_drift_is_caught_by_validation(settings):
    reg = DataRegistry(settings)
    raw = await reg.acquire("singstat.gdp_by_industry", faults=FaultPlan.parse("source:singstat:schema"))
    with pytest.raises(SchemaError) as e:
        process_dataset(raw)
    assert "schema may have changed" in str(e.value)


async def test_air_gapped_mode_serves_snapshot_without_degradation(settings):
    raw = await DataRegistry(settings).acquire("mom.employed_residents")
    assert raw.prov.mode == "snapshot" and not raw.prov.degraded


async def test_internal_source_and_its_outage(settings):
    reg = DataRegistry(settings)
    raw = await reg.acquire("internal.tech_programmes")
    assert (
        raw.prov.mode == "internal" and raw.prov.is_mock and {"year", "programme", "sector", "completions"} <= set(raw.df.columns)
    )
    with pytest.raises(DataSourceError):
        await reg.acquire("internal.tech_programmes", faults=FaultPlan.parse("source:internal"))
    assert not reg.has_snapshot("internal.tech_programmes")
    with pytest.raises(DataSourceError) as e:
        reg.acquire_snapshot("internal.tech_programmes")
    assert e.value.code == "not_found"


async def test_unknown_dataset_is_not_found(settings):
    with pytest.raises(DataSourceError) as e:
        await DataRegistry(settings).acquire("mom.nope")
    assert e.value.code == "not_found"


# --------------------------------------------------------------------------------------- catalog
def test_every_catalog_entry_has_a_bundled_snapshot_or_internal_file(settings):
    for spec in SPEC_BY_KEY.values():
        base = settings.internal_dir if spec.source == "internal" else settings.snapshot_dir
        assert (base / spec.snapshot_file).exists(), spec.key


def test_snapshot_manifest_records_provenance(settings):
    manifest = json.loads((settings.snapshot_dir / "manifest.json").read_text())
    for key, spec in SPEC_BY_KEY.items():
        if spec.source != "internal":
            assert {"retrieved_at", "sha256", "source_url"} <= set(manifest[key]), key


def test_catalog_search_and_metric_mapping():
    top = search_catalog("job vacancies by industry")[0]
    assert top["key"] == "mom.job_vacancies"
    assert search_catalog("gdp output value added")[0]["key"] == "singstat.gdp_by_industry"
    assert datasets_for_metrics(["gdp", "programme_completions"], include_mock=False) == ["singstat.gdp_by_industry"]


def test_each_dataset_parses_and_has_expected_columns(settings):
    import asyncio

    reg = DataRegistry(settings)
    for key, spec in SPEC_BY_KEY.items():
        raw = asyncio.run(reg.acquire(key))
        assert isinstance(raw.df, pd.DataFrame) and len(raw.df) > 10, key
        assert set(spec.required_columns) <= set(raw.df.columns), key
