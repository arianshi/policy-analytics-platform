"""Consistency checks: same question -> same answer; different phrasings of one question -> same interpretation and numbers."""

import asyncio

import pytest

from app.agents.intent import build_plan, heuristic_intent
from app.agents.workflow import run_analysis
from tests.conftest import HEADLINE, make_ctx

PARAPHRASES = [
    "Analyse employment trends in the technology sector from 2020-2024",
    "analyze employment trends in the tech sector between 2020 and 2024",
    "How has employment in the technology sector evolved, 2020 to 2024?",
    "Show me workforce trends for ICT from 2020 through 2024",
    "Employment trend for infocomm sector 2020–2024 please",
]


def test_repeated_runs_are_identical(module_settings):
    runs = [asyncio.run(run_analysis(make_ctx(module_settings, query=HEADLINE))) for _ in range(3)]
    statements = [[f["statement"] for f in r["facts"]] for r in runs]
    assert statements[0] == statements[1] == statements[2]
    assert len({r["report"]["executive_summary"]["text"] for r in runs}) == 1
    assert len({tuple(c["id"] for c in r["charts"]) for r in runs}) == 1
    assert len({r["verification"]["numbers_checked"] for r in runs}) == 1


@pytest.mark.parametrize("q", PARAPHRASES)
def test_paraphrases_share_one_interpretation(q):
    i = heuristic_intent(q, now_year=2026)
    assert i.sectors == ["ict"] and (i.start_year, i.end_year) == (2020, 2024) and not i.needs_clarification
    assert build_plan(i).required == build_plan(heuristic_intent(PARAPHRASES[0], now_year=2026)).required


def test_different_questions_give_the_same_underlying_numbers(module_settings):
    a = asyncio.run(run_analysis(make_ctx(module_settings, query=HEADLINE)))
    b = asyncio.run(run_analysis(make_ctx(module_settings, query="How did ICT headcount change between 2020 and 2024?")))

    def growth(r):
        return next(
            f for f in r["facts"] if f["kind"] == "growth" and f["metric"] == "employed_residents" and f["sector"] == "ict"
        )["values"]

    assert growth(a) == growth(b)


def test_number_of_findings_is_stable_across_sectors(module_settings):
    counts = {
        s: len(
            asyncio.run(
                run_analysis(make_ctx(module_settings, query=f"Analyse employment trends in the {s} sector from 2020-2024"))
            )["facts"]
        )
        for s in ("technology", "finance", "manufacturing")
    }
    assert all(n >= 25 for n in counts.values()), counts
