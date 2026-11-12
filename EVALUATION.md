# Evaluation

How the platform is measured: what each metric means, how it is computed, the target, and the latest result.
[TESTING.md](TESTING.md) describes the test suites that enforce these metrics; this document is about the numbers.

**Contents**
1. [What matters](#1-what-matters)
2. [Metrics](#2-metrics)
3. [Results: offline benchmark](#3-results-offline-benchmark)
4. [Results: live runs with Claude](#4-results-live-runs-with-claude)
5. [What the metrics do not tell you](#5-what-the-metrics-do-not-tell-you)
6. [Next metrics to add](#6-next-metrics-to-add)

---

## 1. What matters

A policy report is only useful if a reader can trust it, so the metrics are ordered by the harm a failure would do:

1. **Grounding** - no number, year or source in the report that the data does not support.
2. **Accuracy** - the computed statistics are correct.
3. **Coverage** - the report answers the question that was asked, with the evidence available.
4. **Data quality** - the inputs are clean, and any problems are disclosed.
5. **Resilience** - a failing source or model degrades the result openly instead of breaking it.
6. **Consistency** - the same question gives the same answer.
7. **Latency and cost** - fast and cheap enough to use.

Targets below are this project's own goals, not external benchmarks.

---

## 2. Metrics

### 2.1 Grounding (hallucination) - `reporting/verify.py`
Every narrative item (executive summary, findings, implications, recommendations) is checked against the facts it cites.

| Metric | Definition | Target |
|---|---|---|
| **Verification pass** | No *error*-level issue in the final report (`verification.passed`) | 100 % of reports |
| **Grounding score** | `1 − errors / max(1, numbers_checked + items_checked)`, in `verification.grounding_score` | 1.0 |
| **Numbers checked** | Numbers in the narrative matched against the cited facts (years and unitless counts ≤ 12 excluded) | Reported, not targeted |
| **Errors by type** | `unknown_fact`, `uncited_claim`, `unsupported_number`, `unsupported_year`, `unsupported_source`, `mock_unlabelled` | 0 |
| **Warnings** | `causal_claim`, a year not in the cited facts | Reported; do not fail a report |
| **Revisions** | LLM redrafts needed after a failed check (`verification.revisions`) | 0 is best; at most 1 by design |
| **Template fallback** | The narrative was replaced by the rule-based draft (`verification.template_fallback`) | Only when every LLM is down or the LLM keeps failing the check |
| **Mutation detection rate** | Share of deliberately altered numbers in a faithful narrative that the checker catches (`tests/llm/test_hallucination.py`) | 100 % |
| **False-positive rate** | Faithful paraphrases ("139.2k", "per cent", rounding, S$ billion) wrongly rejected | 0 % |

### 2.2 Accuracy - `tests/llm/test_accuracy.py`
| Metric | Definition | Target |
|---|---|---|
| **Statistic agreement** | Platform output vs an independent recomputation (pandas/scipy straight on the raw source files) for levels, % change, CAGR, peak, OLS slope/R², share, value added per worker, Pearson r/p/n | Exact match (to stored precision) |
| **Intent accuracy** | Sector, kind, year window and measures parsed from a labelled set of questions | 100 % on the labelled set |
| **Invalid-intent rejection** | Model intents with made-up sector/measure ids, reversed years or unknown kinds that are rejected | 100 % |

### 2.3 Coverage
| Metric | Definition | Target |
|---|---|---|
| **Facts per report** | Facts the analytics agent produced (`len(result.facts)`) | Comparable between the offline and live models for the same question |
| **Charts per report** | `len(result.charts)` | As above |
| **Datasets used** | `len(result.datasets)`, plus `skipped_datasets` | All planned required datasets |
| **Clarification rate** | Share of questions answered with `needs_clarification` | Only for questions with no sector or measure |

### 2.4 Data quality - `data_quality.py`
| Metric | Definition | Target |
|---|---|---|
| **Quality score (per dataset)** | `100 − 20·errors − 4·warnings − 40·(1 − completeness)`, minus 5 if a snapshot replaced the live source, clipped to 0-100 | ≥ 90 for official data |
| **Completeness** | Share of non-missing values after cleaning | Reported per dataset |
| **Defect detection** | Each injected defect class (suppression markers, bad years, duplicates, conflicting duplicates, spikes, gaps, schema drift, …) detected and handled (`tests/dq`) | 100 % |

The synthetic internal dataset (labelled MOCK) scores 67 on purpose: it carries seven injected defects.

### 2.5 Resilience
| Metric | Definition | Target |
|---|---|---|
| **Run success rate** | Runs ending `completed` or `degraded` out of runs that should produce a report | 100 % |
| **Degraded rate** | Share of runs that used a snapshot, skipped a dataset, had partial analytics or used the rule-based narrative | Low in normal operation; always disclosed |
| **Fault recovery** | For each injected fault, the run still produces a verified report and is labelled `degraded` | 100 % |
| **LLM fallback events** | Failed provider attempts per run (`llm.failed_attempts`, `llm.fallback_events`) | 0 in normal operation |

### 2.6 Consistency - `tests/llm/test_consistency.py`
| Metric | Definition | Target |
|---|---|---|
| **Repeat stability** | Distinct outputs (fact statements) from running the same question 3 times | 1 with the offline model |
| **Paraphrase stability** | Rewordings of one question resolving to the same sector, window and dataset plan | 100 % |

### 2.7 Latency and cost
| Metric | Definition | Target |
|---|---|---|
| **End-to-end duration** | `result.duration_ms` | Offline < 3 s; live < 120 s (240 s hard limit) |
| **LLM calls per run** | `llm.calls` | Reported |
| **Tokens per run** | `llm.input_tokens`, `llm.output_tokens` | Reported |
| **Cost per run** | `llm.cost_usd`; all-time totals by provider at `GET /api/providers` | ≤ `LLM_BUDGET_USD` (0.30 in production) |
| **Throughput / tail latency** | `scripts/load_test.py` against a running API | No failed runs under load |

---

## 3. Results: offline benchmark

Measured on 2026-11-12, in-process on a laptop, with bundled real-data snapshots and the rule-based offline model.
Durations exclude HTTP and are a lower bound.

| Question | Status | Facts | Charts | Numbers checked | Grounding | Datasets | Min / mean DQ | ms |
|---|---|---:|---:|---:|---:|---:|---|---:|
| Analyse employment trends in the technology sector from 2020-2024 | completed | 37 | 13 | 63 | 1.0 | 10 | 67 / 96.3 | 325 |
| Compare wage growth in finance and the technology sector since 2019 | completed | 18 | 6 | 39 | 1.0 | 4 | 96 / 99.0 | 108 |
| Is there a link between job vacancies and retrenchments in the tech sector? | completed | 16 | 7 | 47 | 1.0 | 6 | 67 / 94.5 | 151 |
| Forecast employment in the manufacturing sector for the next two years | completed | 8 | 4 | 33 | 1.0 | 3 | 100 / 100 | 100 |
| IT services versus telecommunications job vacancies 2019-2024 | degraded¹ | 14 | 3 | 21 | 1.0 | 5 | 67 / 93.4 | 132 |
| job vacancy rate by sector 2021 to 2025 | completed | 3 | 2 | 14 | 1.0 | 3 | 100 / 100 | 37 |
| How have citizen births and the fertility rate changed since 2015? | completed | 10 | 3 | 17 | 1.0 | 3 | 100 / 100 | 19 |
| tell me something interesting | needs_clarification | 0 | 0 | - | - | 0 | - | 0 |

¹ A sub-sector is missing some measures, so the analysis records a limitation and is labelled degraded, as designed.

**Injected faults** (headline question):

| Fault | Status | Facts | Charts | Grounding | Narrative |
|---|---|---:|---:|---:|---|
| MOM rate-limited (429) | degraded | 37 | 13 | 1.0 | model |
| SingStat schema drift | degraded | 36 | 12 | 1.0 | model |
| Internal database down | degraded | 36 | 12 | 1.0 | model |
| Every LLM down | degraded | 37 | 13 | 1.0 | rule-based template |

**Summary**

| Metric | Result |
|---|---|
| Verification pass | 11 / 11 reports |
| Grounding score | 1.0 on every report |
| Run success rate | 11 / 11 questions that should produce a report (the 12th correctly asked for clarification) |
| Fault recovery | 4 / 4 faults: verified report, labelled degraded |
| Repeat stability | 1 distinct output over 3 runs |
| Mutation detection | 41 / 41 (from `tests/llm/test_hallucination.py`) |
| Statistic agreement | All checks pass (`tests/llm/test_accuracy.py`) |
| Load | 60 / 60 runs completed at concurrency 12, p95 2.54 s (from TESTING.md §7, offline data) |

---

## 4. Results: live runs with Claude

Production backend (OpenShift), live Data.gov.sg and SingStat data, `claude-opus-5-5` at low effort, 2026-11-12.

| Question | Status | Facts | Charts | Numbers checked | Grounding | Revisions | Narrative | LLM calls | Failed attempts | Cost | Duration |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| Analyse employment trends in the technology sector from 2020-2024 | completed | 8 | 3 | 48 | 1.0 | 0 | Claude | 25 | 0 | $0.245 | 76.3 s |
| How have citizen births and the fertility rate changed since 2015? | completed | 10 | 3 | 55 | 1.0 | 0 | Claude | 23 | 0 | $0.215 | 66.6 s |

All three datasets in each run came from the live source (no snapshot), each with a quality score of 100.

**Compared with the offline model**

| | Offline | Claude |
|---|---|---|
| Headline question: facts / charts | 37 / 13 | 8 / 3 |
| Births question: facts / charts | 10 / 3 | 10 / 3 |
| Duration | 0.3 s | 66-76 s |
| Cost | $0 | $0.21-0.25 (70-82 % of the per-run budget) |

On the headline question Claude reads "employment trends" as the employment count alone, so vacancies, retrenchments,
wages and productivity are not planned; the offline parser plans the full set of measures. This is a **coverage**
gap, not a grounding one: everything Claude did report passed verification.

Two live runs is a smoke test, not a benchmark; see §6.

---

## 5. What the metrics do not tell you

* **Grounding ignores direction.** The checker compares absolute values, so a sentence that says a measure *rose*
  6.8 % passes against a fact that it *fell* 6.8 %, and r = 0.64 passes against r = −0.64. A grounding score of 1.0
  means every number is traceable, not that every sign and direction word is right.
* **Grounding is not usefulness.** A faithful but unhelpful sentence passes. Nothing yet scores how well the report
  answers the question.
* **Small counts are not checked.** Unitless whole numbers up to 12 ("three sectors") are skipped.
* **Offline numbers flatter latency and coverage.** The offline model plans the full set of measures and answers
  instantly; live runs are slower and, today, narrower.
* **Live data changes.** Government sources revise figures, so live results can differ from the bundled snapshots
  the accuracy tests use.

---

## 6. Next metrics to add

| Metric | Why | How |
|---|---|---|
| **Direction agreement** | Closes the sign gap in §5 | Store a sign with each fact value; check "rose / fell / increased / declined" and signed numbers against it |
| **Coverage ratio** | Catches narrow plans like the 8-fact live run | Facts (or measures) in a live run ÷ the offline run for the same question |
| **Live intent accuracy** | The labelled intent set is only run against the rule-based parser | Run it against each configured LLM |
| **Answer quality** | Usefulness is unmeasured | A small rubric (answers the question, cites, discloses limits) scored by a person or an LLM judge on the sample questions |
| **Live latency and cost distribution** | Two runs is not enough | Report p50 / p95 duration and cost per completed report from the `llm_calls` table over a fixed question set |
| **Production degraded rate** | Shows how often live sources fail | Share of production runs labelled `degraded`, by reason |
