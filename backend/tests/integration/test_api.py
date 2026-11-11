import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from tests.conftest import HEADLINE

TERMINAL = {"completed", "degraded", "failed", "needs_clarification", "cancelled"}


def wait_done(client, aid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        rec = client.get(f"/api/analyses/{aid}").json()
        if rec["status"] in TERMINAL:
            return rec
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def post(client, query=HEADLINE, **kw):
    r = client.post("/api/analyses", json={"query": query, **kw})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_health_samples_and_providers(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"] == "ok" and h["llm_providers_configured"] == ["offline"]
    assert len(client.get("/api/samples").json()["queries"]) >= 3
    p = client.get("/api/providers").json()
    assert [x["name"] for x in p["providers"]] == ["offline"] or "offline" in [x["name"] for x in p["providers"]]
    assert p["budget_usd_per_analysis"] > 0


def test_full_analysis_lifecycle(client):
    aid = post(client)
    rec = wait_done(client, aid)
    assert rec["status"] == "completed" and rec["duration_ms"] > 0
    r = rec["result"]
    assert len(r["facts"]) >= 30 and len(r["charts"]) >= 10 and len(r["datasets"]) == 10 and r["verification"]["passed"]
    assert r["report"]["findings"] and r["report"]["citations"] and r["intent"]["sectors"] == ["ict"]
    listing = client.get("/api/analyses").json()["items"]
    assert (
        listing[0]["id"] == aid and listing[0]["title"].startswith("Information & Communications") and "result" not in listing[0]
    )


def test_events_are_ordered_and_cover_all_four_agents(client):
    aid = post(client)
    wait_done(client, aid)
    events = client.get(f"/api/analyses/{aid}/events").json()["events"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert {"coordinator", "extraction", "analytics", "reporter"} <= {e["agent"] for e in events}
    assert {"thought", "action", "observation", "delegation"} <= {e["type"] for e in events} and events[-1]["type"] == "done"
    later = client.get(f"/api/analyses/{aid}/events", params={"after": seqs[10]}).json()["events"]
    assert later[0]["seq"] == seqs[11]


def test_websocket_streams_live_events_to_completion(client):
    aid = post(client)
    seen = []
    with client.websocket_connect(f"/ws/analyses/{aid}") as ws:
        while True:
            ev = ws.receive_json()
            if ev["type"] == "ping":
                continue
            seen.append(ev)
            if ev["type"] == "done":
                break
    assert seen[-1]["data"]["status"] == "completed"
    seqs = [e["seq"] for e in seen]
    assert seqs == sorted(seqs) and {"extraction", "analytics", "reporter"} <= {e["agent"] for e in seen}


def test_websocket_replays_history_for_finished_analysis_and_rejects_unknown(client):
    aid = post(client)
    wait_done(client, aid)
    with client.websocket_connect(f"/ws/analyses/{aid}") as ws:
        first = ws.receive_json()
        assert first["seq"] == 1
        types = [first["type"]]
        while types[-1] != "done":
            types.append(ws.receive_json()["type"])
    with client.websocket_connect("/ws/analyses/nope") as ws:
        assert ws.receive_json()["type"] == "error"


@pytest.mark.parametrize(
    "fmt,ctype,needle",
    [
        ("md", "text/markdown", "## Key findings"),
        ("json", "application/json", '"facts"'),
        ("csv", "text/csv", "fact_id,kind"),
        ("html", "text/html", "<h1>"),
    ],
)
def test_export_formats(client, fmt, ctype, needle):
    aid = post(client)
    wait_done(client, aid)
    r = client.get(f"/api/analyses/{aid}/export", params={"format": fmt})
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith(ctype)
        and needle in r.text
        and "attachment" in r.headers["content-disposition"]
    )


def test_markdown_export_is_cited_and_discloses_mock_data(client):
    aid = post(client)
    wait_done(client, aid)
    md = client.get(f"/api/analyses/{aid}/export?format=md").text
    assert (
        "## Sources" in md
        and "[MOCK]" in md
        and "data.gov.sg" in md.lower()
        and "Hallucination check: passed" in md
        and "[S1" in md
    )


def test_invalid_requests_get_structured_errors(client):
    r = client.post("/api/analyses", json={"query": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.post("/api/analyses", json={"query": "valid question here", "simulate_failures": ["rm -rf"]}).status_code == 422
    assert client.post("/api/analyses", json={}).status_code == 422
    assert client.post("/api/analyses", json={"query": "q" * 700}).status_code == 422
    for path in ("/api/analyses/nope", "/api/analyses/nope/events", "/api/analyses/nope/export"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/datasets/nope/rows").status_code == 404
    assert client.get("/api/analyses", params={"limit": 0}).status_code == 422


def test_export_requires_a_finished_report(client):
    aid = post(client, "tell me something interesting")
    wait_done(client, aid)
    r = client.get(f"/api/analyses/{aid}/export")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_report"


def test_ambiguous_question_returns_clarification_not_a_guess(client):
    rec = wait_done(client, post(client, "tell me something interesting"))
    assert rec["status"] == "needs_clarification"
    assert (
        rec["result"]["clarification"]["question"]
        and rec["result"]["clarification"]["suggestions"]
        and rec["result"]["datasets"] == []
    )


def test_simulated_failure_via_api_degrades_gracefully(client):
    rec = wait_done(client, post(client, simulate_failures=["source:internal"]))
    assert (
        rec["status"] == "degraded"
        and rec["result"]["skipped_datasets"] == ["internal.tech_programmes"]
        and rec["result"]["verification"]["passed"]
    )
    assert any("internal" in w for w in rec["result"]["warnings"])


def test_fault_injection_can_be_disabled(settings):
    with TestClient(create_app(settings.model_copy(update={"allow_fault_injection": False}))) as c:
        rec = wait_done(c, post(c, simulate_failures=["source:internal"]))
    assert rec["status"] == "completed"


def test_datasets_are_persisted_to_the_database(client):
    wait_done(client, post(client))
    stored = {d["key"]: d["stored"] for d in client.get("/api/datasets").json()["datasets"]}
    assert stored["mom.employed_residents"]["rows"] == 224 and stored["internal.tech_programmes"]["rows"] > 0
    rows = client.get("/api/datasets/singstat.gdp_by_industry/rows", params={"limit": 5}).json()["rows"]
    assert len(rows) == 5 and {"year", "sector", "value", "unit"} <= set(rows[0])
    totals = client.get("/api/providers").json()["totals"]
    assert totals and totals[0]["ok_calls"] > 10


def test_delete_removes_analysis_and_events(client):
    aid = post(client)
    wait_done(client, aid)
    assert client.delete(f"/api/analyses/{aid}").status_code == 204
    assert client.get(f"/api/analyses/{aid}").status_code == 404 and client.delete(f"/api/analyses/{aid}").status_code == 404


def test_cancelling_a_finished_analysis_is_a_conflict(client):
    aid = post(client)
    wait_done(client, aid)
    assert client.post(f"/api/analyses/{aid}/cancel").status_code == 409


def test_rate_limit(settings):
    with TestClient(create_app(settings.model_copy(update={"rate_limit_per_min": 2}))) as c:
        codes = [c.post("/api/analyses", json={"query": "tell me something interesting"}).status_code for _ in range(4)]
    assert codes == [202, 202, 429, 429]


def test_interrupted_analyses_are_failed_on_restart(settings):
    db = Database(settings.db_path)
    db.create_analysis("zombie", "q", {})
    db.set_status("zombie", "running")
    db.close()
    with TestClient(create_app(settings)) as c:
        rec = c.get("/api/analyses/zombie").json()
    assert rec["status"] == "failed" and "restart" in rec["error"]


def test_request_id_and_cors_headers(client):
    r = client.get("/api/health", headers={"x-request-id": "abc123", "origin": "http://localhost:3000"})
    assert r.headers["x-request-id"] == "abc123" and r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert client.get("/api/health").headers["x-request-id"]


def test_result_is_valid_json_roundtrip(client):
    aid = post(client)
    rec = wait_done(client, aid)
    assert json.loads(json.dumps(rec))["result"]["report"]["title"]
    assert Settings
