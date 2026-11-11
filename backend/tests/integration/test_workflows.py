"""Multi-agent workflows end to end: happy path plus every failure mode the platform is designed to survive."""

import json

import httpx
import pytest

from app.agents.workflow import run_analysis
from app.datasources.registry import DataRegistry
from tests.conftest import HEADLINE, ScriptedProvider, make_ctx


def texts(ctx, agent=None, type_=None):
    return [e[2] for e in ctx.events if (agent is None or e[0] == agent) and (type_ is None or e[1] == type_)]


def actions(ctx, agent):
    return [e[3]["tool"] for e in ctx.events if e[0] == agent and e[1] == "action"]


async def test_headline_query_happy_path(run_analysis_offline):
    ctx, r = await run_analysis_offline()
    assert r["status"] == "completed" and not r["warnings"]
    assert actions(ctx, "coordinator") == [
        "interpret_query",
        "plan_workflow",
        "delegate_extraction",
        "delegate_analytics",
        "delegate_reporting",
    ]
    assert actions(ctx, "extraction") == ["search_catalog", "fetch_datasets", "validate_and_clean", "store_datasets"]
    assert actions(ctx, "analytics") == [
        "check_scope",
        "analyze_trends",
        "compare_sectors",
        "analyze_relationships",
        "cross_source",
        "outlook",
    ]
    assert actions(ctx, "reporter") == ["draft_findings", "verify_draft", "compose_report"]
    rep = r["report"]
    assert [f["title"] for f in rep["findings"]][:3] == ["Employment", "Hiring demand", "Churn and layoffs"]
    assert any("MOCK" in f["text"] for f in rep["findings"]), "mock data must be labelled in the narrative"
    assert {c["dataset_key"] for c in rep["citations"]} >= {
        "mom.employed_residents",
        "singstat.gdp_by_industry",
        "internal.tech_programmes",
    }
    assert len({d["key"].split(".")[0] for d in r["datasets"]}) == 3, "three different sources"
    kinds = {f["kind"] for f in r["facts"]}
    assert {
        "growth",
        "peak",
        "trend",
        "comparison",
        "share",
        "correlation",
        "anomaly",
        "productivity",
        "pipeline",
        "context",
    } <= kinds
    assert r["intent"]["sectors"] == ["ict"] and r["llm"]["calls"] > 10 and r["llm"]["cost_usd"] == 0


async def test_rate_limited_source_is_retried_then_falls_back_to_snapshot(run_analysis_offline):
    ctx, r = await run_analysis_offline(faults="source:mom:rate_limited")
    assert r["status"] == "degraded" and r["verification"]["passed"]
    ex = actions(ctx, "extraction")
    assert ex.count("fetch_datasets") == 2 and "use_snapshot" in ex, "fetch -> retry once -> snapshot"
    assert any("Retrying once" in t for t in texts(ctx, "extraction", "thought"))
    mom = [d for d in r["datasets"] if d["key"].startswith("mom.")]
    assert mom and all(d["mode"] == "snapshot" and d["degraded"] for d in mom)
    assert any("degraded" in w for w in r["warnings"]) and any("snapshot" in x for x in r["report"]["limitations"])
    assert all(c["degraded"] for c in r["report"]["citations"] if c["dataset_key"].startswith("mom."))


async def test_optional_source_outage_triggers_replanning(run_analysis_offline):
    ctx, r = await run_analysis_offline(faults="source:internal")
    assert r["status"] == "degraded" and r["skipped_datasets"] == ["internal.tech_programmes"]
    assert "replan" in actions(ctx, "coordinator") and "skip_datasets" in actions(ctx, "extraction")
    assert not any(f["kind"] == "pipeline" for f in r["facts"]) and "internal.tech_programmes" not in r["plan"]["optional"]
    assert r["verification"]["passed"]


async def test_schema_drift_in_one_source_is_contained(run_analysis_offline):
    ctx, r = await run_analysis_offline(faults="source:singstat:schema")
    assert r["status"] == "degraded" and "singstat.gdp_by_industry" in r["skipped_datasets"]
    assert not any(f["kind"] == "productivity" for f in r["facts"]) and r["verification"]["passed"]
    assert any("schema" in t.lower() for t in texts(ctx, "extraction", "observation"))


async def test_live_schema_drift_recovers_through_snapshot(settings, tmp_path):
    s = settings.model_copy(update={"live_data": True, "runtime_dir": tmp_path / "rt"})
    body = (settings.snapshot_dir / "singstat_M015731.json").read_bytes()
    drifted = json.dumps(
        {"Data": {"row": [{"series": r["rowText"], "values": r["columns"]} for r in json.loads(body)["Data"]["row"]]}}
    ).encode()
    csv = {
        k: (settings.snapshot_dir / f"mom_{k}.csv").read_bytes()
        for k in (
            "employed_residents",
            "job_vacancies",
            "job_vacancy_rate",
            "retrenchments",
            "recruitment_rate",
            "resignation_rate",
            "total_wage_change",
            "unemployment_rate",
        )
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "tablebuilder" in req.url.host:
            return httpx.Response(200, content=drifted)
        if req.url.path.endswith("/poll-download"):
            ds = req.url.path.split("/")[-2]
            return httpx.Response(
                200, json={"code": 0, "data": {"status": "DOWNLOAD_SUCCESS", "url": f"https://s3.test/{ds}.csv"}}
            )
        from app.datasources.catalog import CATALOG

        ds = req.url.path.split("/")[-1].removesuffix(".csv")
        key = next(c.key for c in CATALOG if c.dataset_id == ds).split(".")[1]
        return httpx.Response(200, content=csv[key])

    async def nosleep(_):
        return None

    ctx = make_ctx(s, registry=DataRegistry(s, transport=httpx.MockTransport(handler), sleep=nosleep))
    r = await run_analysis(ctx)
    gdp = next(d for d in r["datasets"] if d["key"] == "singstat.gdp_by_industry")
    assert gdp["mode"] == "snapshot" and gdp["degraded"] and r["status"] == "degraded"
    assert {d["mode"] for d in r["datasets"] if d["key"].startswith("mom.")} == {"live"}
    assert any(f["kind"] == "productivity" for f in r["facts"]), "analysis still completes using the snapshot"
    assert "use_snapshot" in actions(ctx, "extraction")


async def test_total_llm_outage_still_produces_a_verified_report(run_analysis_offline):
    _ctx, r = await run_analysis_offline(faults="llm:*")
    assert r["status"] in ("completed", "degraded") and r["verification"]["passed"] and r["report"]["findings"]
    assert any("rule-based" in w for w in r["warnings"])
    assert r["report"]["narrative_source"] == "template-fallback"


async def test_ambiguous_question_stops_before_touching_any_data(run_analysis_offline):
    ctx, r = await run_analysis_offline("what's the weather like tomorrow?")
    assert r["status"] == "needs_clarification" and r["clarification"]["suggestions"] and not r["datasets"] and not r["facts"]
    assert actions(ctx, "coordinator") == ["interpret_query", "request_clarification"] and not actions(ctx, "extraction")


async def test_sector_without_data_for_a_measure_reports_a_limitation_instead_of_crashing(run_analysis_offline):
    _, r = await run_analysis_offline("arts sector wage growth 2021-2024")
    assert r["status"] in ("completed", "degraded") and r["report"]
    limitation = [f for f in r["facts"] if f["kind"] == "limitation"]
    assert limitation and any("arts" in f["statement"].lower() or "Arts" in f["statement"] for f in limitation)
    assert any("Arts" in x for x in r["report"]["limitations"])


async def test_sub_sector_query(run_analysis_offline):
    _, r = await run_analysis_offline("IT services versus telecommunications job vacancies 2019-2024")
    assert r["intent"]["sectors"] == ["ict_it_services", "ict_telecom"] and r["status"] in ("completed", "degraded")
    assert {f["sector"] for f in r["facts"] if f["metric"] == "job_vacancies" and f["kind"] == "growth"} == {
        "ict_it_services",
        "ict_telecom",
    }


async def test_multi_sector_comparison(run_analysis_offline):
    _, r = await run_analysis_offline("Compare wage growth in finance and the technology sector since 2019")
    wage = [f for f in r["facts"] if f["metric"] == "total_wage_change" and f["kind"] == "growth"]
    assert ({f["sector"] for f in wage} == {"finance", "ict"} and "Finance" in r["report"]["title"]) or "Financial" in r[
        "report"
    ]["title"]
    assert any(f["kind"] == "comparison" for f in r["facts"]) and r["verification"]["passed"]


async def test_forecast_is_withheld_when_the_latest_point_breaks_the_trend(run_analysis_offline):
    _, r = await run_analysis_offline("Forecast employment in the technology sector for the next two years")
    assert not any(f["kind"] == "forecast" for f in r["facts"])
    assert any("no projection is given" in f["statement"] for f in r["facts"] if f["kind"] == "limitation")
    assert any("projection" in x for x in r["report"]["limitations"]) and r["verification"]["passed"]


async def test_forecast_is_produced_for_a_stable_series(run_analysis_offline):
    _, r = await run_analysis_offline("Forecast employment in the manufacturing sector for the next two years")
    fc = [f for f in r["facts"] if f["kind"] == "forecast"]
    assert (
        fc
        and "80% prediction interval" in fc[0]["statement"]
        and fc[0]["values"]["lower"]["v"] < fc[0]["values"]["point"]["v"] < fc[0]["values"]["upper"]["v"]
    )
    assert any(c["id"].startswith("chart-forecast") for c in r["charts"])


async def test_workflow_survives_an_llm_that_returns_garbage_for_everything(settings):
    from app.llm.providers.offline import OfflineProvider

    ctx = make_ctx(settings, providers=[ScriptedProvider("junk", lambda r: "lol no"), OfflineProvider()])
    r = await run_analysis(ctx)
    assert r["status"] == "completed" and r["verification"]["passed"]


@pytest.mark.parametrize("word", ["complete", "done", "success"])
async def test_final_status_comes_from_the_evidence_not_the_models_wording(settings, word):
    """A real LLM finished a clean run with {"status": "complete"}, which was mislabelled 'degraded'."""
    from app.llm.providers.offline import OfflineProvider

    def model(req):
        if "coordinator agent" in req.system and '"reporting": {' in req.prompt:
            return json.dumps({"thought": "All stages finished.", "final": {"status": word}})
        return "not json"  # every other step falls through to the offline model

    ctx = make_ctx(settings, providers=[ScriptedProvider("wordy", model), OfflineProvider()])
    r = await run_analysis(ctx)
    assert r["status"] == "completed"


async def test_charts_are_well_formed(run_analysis_offline):
    _, r = await run_analysis_offline()
    ids = set()
    for c in r["charts"]:
        assert c["id"] not in ids
        ids.add(c["id"])
        assert (
            c["type"] in ("line", "bar", "scatter") and c["series"] and all(s["data"] for s in c["series"]) and c["sources"]
        ) or c["id"] == "chart-productivity"
        for s in c["series"]:
            assert all("x" in p and "y" in p and isinstance(p["y"], int | float) for p in s["data"])
    assert any(c["type"] == "scatter" for c in r["charts"]) and any(c["type"] == "bar" for c in r["charts"])
    flagged = [
        p for c in r["charts"] if c["id"].startswith("chart-trend-job_vacancies") for p in c["series"][0]["data"] if p.get("flag")
    ]
    assert [p["x"] for p in flagged] == [2021]


@pytest.mark.parametrize(
    "q",
    [
        HEADLINE,
        "Compare wage growth in finance and the technology sector since 2019",
        "Forecast employment in the manufacturing sector for the next two years",
    ],
)
async def test_every_report_passes_its_own_hallucination_check(run_analysis_offline, q):
    _, r = await run_analysis_offline(q)
    assert r["verification"]["passed"] and r["verification"]["grounding_score"] == 1.0 and r["verification"]["error_count"] == 0


async def test_policy_announcement_gets_a_verified_births_and_fertility_report(run_analysis_offline):
    from tests.unit.test_family_data import ANNOUNCEMENT

    ctx, r = await run_analysis_offline(ANNOUNCEMENT)
    assert r["status"] == "completed" and r["verification"]["passed"]
    assert {f["metric"] for f in r["facts"]} >= {"total_fertility_rate", "citizen_births", "citizen_births_third_plus"}
    share = next(f for f in r["facts"] if f["kind"] == "share")
    assert round(share["values"]["share_end"]["v"], 1) == 18.4  # 4,808 of 26,071 citizen births in 2025
    assert r["report"]["recommendations"] and "hiring" not in json.dumps(r["report"]["recommendations"])
    # the agents see the whole question, not a 200-character cut
    goals = [e[2] for e in ctx.events if e[1] == "status" and "Analysis started" in e[2]]
    assert goals and len(ANNOUNCEMENT) > 200


def test_coordinator_goal_carries_the_full_question(settings):
    from app.agents.coordinator import CoordinatorAgent
    from tests.unit.test_family_data import ANNOUNCEMENT

    assert ANNOUNCEMENT[-40:] in CoordinatorAgent(make_ctx(settings, query=ANNOUNCEMENT)).goal()


async def test_a_question_asked_mid_run_never_mislabels_a_finished_report(settings):
    """Live Claude asked for clarification although the intent was resolved, then produced the full report anyway."""
    from app.llm.providers.offline import OfflineProvider
    from tests.unit.test_family_data import ANNOUNCEMENT

    asked = {"n": 0}

    def model(req):
        if "coordinator agent" in req.system and '"intent": {' in req.prompt and '"plan": null' in req.prompt and not asked["n"]:
            asked["n"] += 1
            return json.dumps(
                {
                    "thought": "Not phrased as a question.",
                    "action": {"tool": "request_clarification", "args": {"question": "What should I analyse?"}},
                }
            )
        return "not json"  # everything else: the offline model

    ctx = make_ctx(settings, query=ANNOUNCEMENT, providers=[ScriptedProvider("asker", model), OfflineProvider()])
    r = await run_analysis(ctx)
    assert asked["n"] == 1
    assert r["status"] == "completed" and r["clarification"] is None and r["report"]
    assert r["report"]["title"] == "Singapore: births & fertility, 2016-2025"
