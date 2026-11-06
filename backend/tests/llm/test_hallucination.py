"""Hallucination detection: the verifier must catch fabricated numbers, years, sources and citations - and must not reject faithful paraphrases."""

import json
import re

import pytest

from app.reporting import template
from app.reporting.verify import Item, VerifyContext, verify_items
from tests.conftest import ScriptedProvider, make_ctx

VCTX = VerifyContext((2020, 2024), (1998, 2027))


@pytest.fixture(scope="module")
def facts(headline):
    ctx, _ = headline
    return ctx.facts, [f.to_dict() for f in ctx.facts.all()]


def check(store, text, ids):
    return verify_items([Item("t", text, ids)], store, VCTX)


def codes(res):
    return {i.code for i in res.issues if i.severity == "error"}


# -------------------------------------------------------------------------------- positives
def test_template_narrative_is_fully_grounded(facts):
    store, fd = facts
    items = [Item(f["title"], f["text"], f["fact_ids"]) for f in template.build_findings(fd)] + [Item("imp", i["text"], i["fact_ids"]) for i in template.build_implications(fd)]
    res = verify_items(items, store, VCTX)
    assert res.passed and res.numbers_checked > 40 and res.grounding_score == 1.0


@pytest.mark.parametrize("text", [
    "Employed residents in the sector reached 139,200 in 2024.",
    "Employment reached about 139.2k in 2024, up from 111.6k in 2020.",  # k-suffix
    "Growth of 24.7% over the period.", "Growth of roughly 25% over the period.",  # rounding to integer
    "Employment grew 5.7 per cent a year.", "Employment grew 5.7 percent a year compounded.",
    "Growth was 24.7 percent in total.",
    "That is a rise of 27,600 workers.",  # derived: 139,200 - 111,600
])
def test_faithful_paraphrases_are_accepted(facts, text):
    store, _ = facts
    res = check(store, text, ["F1"])
    assert res.passed, [i.__dict__ for i in res.errors]


def test_money_scales_are_understood(facts):
    store, _ = facts
    gdp = next(f for f in store.all() if f.kind == "productivity")
    assert check(store, f"Value added per worker was S$307.8 thousand in 2024 [{gdp.id}].", [gdp.id]).passed


# ------------------------------------------------------------------------------- negatives
@pytest.mark.parametrize("text,code", [
    ("Employed residents rose to 150,000 in 2024.", "unsupported_number"),
    ("Employment grew by 31.4% over the period.", "unsupported_number"),
    ("The sector grew 5.7% a year and 8.8% overall.", "unsupported_number"),
    ("Employment reached 139,200 in 2031.", "unsupported_year"),
    ("Employment reached 139,200 according to IRAS.", "unsupported_source"),
    ("The IMF reports employment of 139,200.", "unsupported_source"),
    ("Employment reached 139,200 in 2024.", "unknown_fact"),  # cites F999 below
])
def test_fabrications_are_detected(facts, text, code):
    store, _ = facts
    ids = ["F999"] if code == "unknown_fact" else ["F1"]
    assert code in codes(check(store, text, ids))


def test_uncited_claims_are_rejected(facts):
    store, _ = facts
    assert "uncited_claim" in codes(check(store, "Employment grew strongly.", []))


def test_numbers_must_come_from_the_cited_facts_not_just_any_fact(facts):
    store, _ = facts
    other = next(f for f in store.all() if f.kind == "context")  # unemployment 3.0% -> 2.0%
    assert "unsupported_number" in codes(check(store, "Unemployment fell to 2.0% in 2024.", ["F1"]))
    assert check(store, "Unemployment fell to 2.0% in 2024.", [other.id]).passed


def test_mock_data_must_be_labelled_synthetic(facts):
    store, _ = facts
    pipe = next(f for f in store.all() if f.mock)
    assert "mock_unlabelled" in codes(check(store, pipe.statement.replace("[MOCK internal data] ", ""), [pipe.id]))
    assert check(store, pipe.statement, [pipe.id]).passed


def test_causal_language_is_flagged_as_warning(facts):
    store, _ = facts
    res = check(store, "Employment reached 139,200 in 2024 due to the new tech policy.", ["F1"])
    assert res.passed and any(i.code == "causal_claim" for i in res.issues if i.severity == "warning")


def test_mutation_testing_detects_every_altered_number(facts):
    """Take the faithful narrative, alter each checkable number in turn, and require 100% detection."""
    store, fd = facts
    findings = template.build_findings(fd)
    tokens = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+\.\d+)(%?)")
    attempts = detected = 0
    for f in findings:
        for m in tokens.finditer(f["text"]):
            raw = m.group(1)
            val = float(raw.replace(",", ""))
            new = val * 1.37 + 3.1
            fmt = f"{new:,.0f}" if "," in raw else f"{new:.{len(raw.split('.')[1])}f}"
            mutated = f["text"][: m.start(1)] + fmt + f["text"][m.end(1):]
            attempts += 1
            if codes(check(store, mutated, f["fact_ids"])):
                detected += 1
    assert attempts >= 25 and detected == attempts, f"{detected}/{attempts} mutations detected"


# --------------------------------------------------------- hostile / honest / self-correcting LLMs
def run_with(settings, providers):
    import asyncio

    from app.agents.workflow import run_analysis
    from app.llm.providers.offline import OfflineProvider

    ctx = make_ctx(settings, providers=[*providers, OfflineProvider()])
    return ctx, asyncio.run(run_analysis(ctx))


HALLUCINATION = {"findings": [{"title": "Boom", "text": "Tech employment surged 85.3% to 250,000 workers, caused by the IMDA Tech Pass, according to IRAS.", "fact_ids": ["F1"]}],
                 "implications": [{"text": "Wages doubled in 2031.", "fact_ids": ["F1"]}], "executive_summary": {"text": "Everything is 91.7% better.", "fact_ids": ["F1"]},
                 "recommendations": [{"text": "Hire 40,000 more.", "fact_ids": []}]}


def test_hostile_llm_never_reaches_the_report(module_settings):
    def hostile(req):
        return json.dumps(HALLUCINATION) if req.task in ("generate_insights", "write_report") else "garbled"

    p = ScriptedProvider("hostile", hostile)
    ctx, result = run_with(module_settings, [p])
    text = json.dumps(result["report"])
    for forbidden in ("85.3", "250,000", "IRAS", "IMDA", "91.7", "2031", "40,000"):
        assert forbidden not in text, forbidden
    assert result["verification"]["passed"] and result["verification"]["template_fallback"] and result["report"]["narrative_source"] == "template-fallback"
    assert sum(1 for r in p.calls if r.task == "generate_insights") == 2  # first draft + one revision, then the safe fallback
    warnings = [e[2] for e in ctx.events if e[0] == "reporter" and e[1] == "warning"]
    assert any("unsupported_number" in w for w in warnings) and any("unsupported_source" in w for w in warnings)


def test_honest_llm_output_is_used_as_is(module_settings):
    def honest(req):
        if req.task == "generate_insights":
            fd = req.context["facts"]
            return json.dumps({"findings": template.build_findings(fd), "implications": template.build_implications(fd)})
        if req.task == "write_report":
            fd = req.context["facts"]
            return json.dumps({"executive_summary": template.build_summary(fd), "recommendations": template.build_recommendations(fd)})
        return "garbled"

    _ctx, result = run_with(module_settings, [ScriptedProvider("honest", honest)])
    assert result["report"]["narrative_source"] == "llm:honest" and result["verification"]["passed"] and not result["verification"]["template_fallback"]
    assert result["verification"]["revisions"] == 0


def test_llm_that_corrects_itself_after_feedback(module_settings):
    state = {"n": 0}

    def learner(req):
        if req.task == "generate_insights":
            state["n"] += 1
            if state["n"] == 1:
                return json.dumps(HALLUCINATION)
            assert "FAILED verification" in req.prompt and "unsupported_number" in req.prompt  # the specific failures were fed back
            fd = req.context["facts"]
            return json.dumps({"findings": template.build_findings(fd), "implications": template.build_implications(fd)})
        if req.task == "write_report":
            fd = req.context["facts"]
            return json.dumps({"executive_summary": template.build_summary(fd), "recommendations": template.build_recommendations(fd)})
        return "garbled"

    _, result = run_with(module_settings, [ScriptedProvider("learner", learner)])
    assert result["verification"]["revisions"] == 1 and not result["verification"]["template_fallback"] and result["report"]["narrative_source"] == "llm:learner"


def test_hallucinated_summary_alone_is_replaced(module_settings):
    def mixed(req):
        fd = req.context.get("facts")
        if req.task == "generate_insights":
            return json.dumps({"findings": template.build_findings(fd), "implications": template.build_implications(fd)})
        if req.task == "write_report":
            return json.dumps({"executive_summary": {"text": "Employment tripled to 400,000.", "fact_ids": ["F1"]}, "recommendations": []})
        return "garbled"

    _, result = run_with(module_settings, [ScriptedProvider("mixed", mixed)])
    assert "400,000" not in json.dumps(result["report"]) and result["verification"]["passed"]
    assert result["report"]["narrative_source"].endswith("+template-summary")
