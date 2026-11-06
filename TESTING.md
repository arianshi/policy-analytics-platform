# Testing

## 1. Strategy

The system's risk is not "does the endpoint return 200" but **"can it say something false, silently?"** - so tests are
organised around the ways it could: wrong numbers, invented numbers, inconsistent answers, dirty data, dependency
failure and overload. Everything runs **offline and deterministically** (bundled snapshots + the rule-based model +
scripted fake LLMs + mocked HTTP transports), so CI needs no secrets and results are reproducible. Live tests exist but are opt-in.

| Suite | Path | Tests | What it proves |
|---|---|---:|---|
| Unit | `backend/tests/unit` | 106 | stats functions, ontology, data-source adapters (retry/backoff/429/timeout/cache/faults), LLM router (fallback, breaker, budget, validation), each provider's request shape, ReAct loop failure handling, intent parsing |
| Data quality | `backend/tests/dq` | 22 | every defect class is detected and handled by policy; real snapshots surface their real defects |
| Integration | `backend/tests/integration` | 45 | REST + WebSocket API, multi-agent workflows end to end, every failure scenario, service lifecycle (cancel, timeout, crash, concurrency bound) |
| LLM-specific | `backend/tests/llm` | 49 | accuracy vs independent recomputation, hallucination detection, consistency |
| Performance | `backend/tests/perf` | 4 | latency budget, 20-way concurrency, large-series statistics, verifier throughput |
| Frontend | `frontend/src/__tests__` | 31 | API client, WebSocket hook (live + polling fallback + id switching), components, page flows |
| **Total** | | **257** | |

**Coverage** (`pytest --cov=app`): **95 %** of 3,306 backend statements (every module ≥ 83 %; the least-covered are `datasources/cache.py` 83 %, `llm/providers/bedrock.py` 85 %, `api/ws.py` 85 %). Frontend: **92.8 %** lines (ChartCard is 53 % because Recharts cannot measure a container in jsdom - the chart rendering was checked in a real browser instead). CI enforces `--cov-fail-under=90` on the backend.

## 2. Hallucination detection methodology

Defence in depth, from construction to verification to measurement.

**Layer 1 - prevent.** LLMs never produce statistics. `AnalysisEngine` computes them and emits typed `Fact`s (`values: {name: (number, kind)}`). Prompts give the model only fact sentences and forbid new numbers, causal claims and unlisted sources.

**Layer 2 - verify** (`reporting/verify.py`). Every narrative item must cite fact ids, then:

| Check | Example it catches |
|---|---|
| `unknown_fact` / `uncited_claim` | cites `F999`; a sentence with no citation |
| `unsupported_number` | "grew 31.4 %" when the cited facts say 24.7 %; "150,000" vs 139,200 |
| `unsupported_year` | "in 2031" (outside coverage); warning if a year isn't in the cited facts |
| `unsupported_source` | "according to IRAS", "the IMF reports" (agencies never used) |
| `mock_unlabelled` | narrating the synthetic programme data without saying it is synthetic |
| `causal_claim` (warning) | "due to", "caused by", "proves" |

Number matching is **typed and scale-aware**: a `%` matches only percentage facts; "S$42.8 billion" ↔ 42,839 S$ million; "139.2k" ↔ 139,200; a number written with *d* decimals matches within ±0.5·10⁻ᵈ (so "about 25 %" accepts 24.7 %, "5.7 per cent" accepts 5.67). Differences/percentage changes between two values of the *same cited fact* are accepted and tagged `derived_number`; arbitrary new numbers are not. Numbers must come from the facts **cited by that item**, not any fact (test: "Unemployment fell to 2.0 %" passes only when it cites the unemployment fact).

**Layer 3 - recover.** Failed draft → model revises with the exact failures in the prompt (1×) → rule-based draft built only from fact values. The final report always carries a `verification` block (numbers checked, errors, warnings, grounding score, revisions, `template_fallback`) shown in the UI's *Trust & cost* tab.

**Layer 4 - measure** (`tests/llm/test_hallucination.py`, `test_accuracy.py`):
* **Mutation testing**: take the faithful narrative, alter each checkable number in turn (×1.37 + 3.1), and require detection → **41 / 41 mutations detected**.
* **False-positive guard**: 9 faithful paraphrases (k-suffix, "per cent", rounding, S$ billion, derived difference) must pass.
* **Hostile model** (scripted): fabricates "+85.3 % to 250,000 workers, caused by the IMDA Tech Pass, according to IRAS", a 2031 figure and uncited advice → none of it reaches the report; verification passes via template fallback; the revision attempt happened exactly once.
* **Honest model**: faithful output is used as-is (0 revisions). **Self-correcting model**: fails first, receives the feedback (asserted to contain `FAILED verification` and the failure codes), succeeds on revision → `llm:` narrative kept.
* **Self-consistency of facts**: for every fact kind (forecast, comparison, limitation, correlation…, across 6 query types) every number in the fact's own sentence must be accounted for by its `values`. *This test found two real bugs during development*: the forecast sentence said "80 % interval" without storing 80, and the cross-sector comparison said "13 sectors" without storing 13.

## 3. Accuracy validation

`test_accuracy.py` recomputes results **independently from the raw source files** (pandas/scipy straight on the downloaded CSV/JSON, not through the platform's code) and asserts equality: employment 2020/2024 (111,600 / 139,200), % change, CAGR, vacancy peak (10,300 in 2022) and decline, OLS slope / R² / n vs `scipy.linregress`, employment share vs a hand-built denominator, value-added-per-worker from SingStat GDP ÷ MOM employment, and the correlation r, p, n vs `scipy.pearsonr` on differenced series. The same test asserts *why* we difference: the level correlation is +0.49 (spurious, shared trend) while the change correlation is −0.64. Query-interpretation accuracy is checked on a labelled table (sector, kind, window, metrics) plus validation that invalid LLM intents (hallucinated sector/metric ids, inverted years, unknown kinds) are rejected.

## 4. Consistency checks (`test_consistency.py`)
* The same question run 3× yields byte-identical fact sentences, summary, chart ids and verification counts.
* Five paraphrases of the headline question ("analyze … tech sector between 2020 and 2024", "workforce trends for ICT…") resolve to the same sector, window and dataset plan.
* A differently-worded question about the same quantity ("How did ICT headcount change…") yields identical underlying fact values.
* Fact volume is stable across sectors (≥ 25 facts for technology, finance, manufacturing).

## 5. Data-quality tests (`tests/dq`)
Synthetic defects, each asserted to be detected *and* handled by policy: suppression tokens (`-`, `n/a`, blank → missing, never 0), non-numeric garbage, thousands separators, rates outside 0-100, negative counts, bad years (`2O22`), exact duplicates (removed), **conflicting duplicates (both excluded, both values reported)**, isolated spikes (**kept + flagged for official data, quarantined for internal data**), persistent level shifts (not a spike), change metrics exempt from spike rules, dimension aggregation, staleness, gaps (reported, not interpolated), empty-after-cleaning (error, not crash), schema drift, unmapped industries (dropped and disclosed).
On the **real snapshots** it asserts the genuine upstream defects are surfaced: the `-` suppression marker in retrenchments, the **Construction 2023 conflicting wage-change duplicate (4.2 vs 5.5)**, and that no official dataset is ever quarantined. The MOCK Excel's seven injected defects (bad year, blank, `n/a`, negative, duplicate rows, 9× outlier, label variants `ICT` / `Info-comm`) are all detected (quality score 67/100).

## 6. Failure and workflow tests (`integration/test_workflows.py`)
Each asserts the *agent behaviour*, not just the final status: MOM rate-limited → `fetch → retry once → use_snapshot`, result `degraded`, citations flagged; internal database down → `skip_datasets` then coordinator `replan`; SingStat schema drift → dataset rejected, analysis continues; **live-mode schema drift recovers through the snapshot** (mocked HTTP); every LLM down → rule-based policy completes with a verified report; a model that returns garbage for everything; ambiguous question → clarification, no data touched; sector missing a measure → limitation fact, no crash; sub-sector query; multi-sector comparison; forecast withheld on trend break and produced for a stable series; chart structure; every report passes its own hallucination check.

## 7. Performance and load

| Test | Result |
|---|---|
| Single analysis, in-process (`perf/`) | 0.3-1.3 s (budget asserted < 3 s) |
| 20 concurrent analyses, 4 workers (in-process) | 5.0 s total, p50 0.96 s, p95 1.13 s |
| **HTTP load test** (`scripts/load_test.py`, API with `LIVE_DATA=false`) | **60 analyses @ concurrency 12: 60/60 completed, 6.3 analyses/s, end-to-end p50 1.85 s / p95 2.54 s / max 2.85 s; `POST /analyses` accept p50 175 ms, p95 427 ms** |
| HTTP load test, concurrency 4 | 40/40 completed, 5.3/s, p50 0.72 s, p95 1.23 s |
| Rate limiter (30/min/IP) | 40-request burst: 29 accepted and completed, 11 rejected `429` as designed |
| 200,000-point series (trend + correlation + anomalies + forecast) | < 1.5 s asserted |
| Verifier on 350 narrative items | < 2 s asserted |

These are offline-data numbers on a laptop with the rule-based model; with real LLMs end-to-end latency is dominated by ~25 sequential model calls (≈ 24 are tiny ReAct decisions - the obvious optimisation is a small/cheap model for those and a larger one only for narrative). A live run against Data.gov.sg took 18.6 s because the real API rate-limited (see DATA_SOURCES.md).

## 8. End-to-end UI check
Driven in headless Chromium against the real backend: WebSocket opened and 69 events streamed live; run finished "Completed"; no console errors or failed requests; tabs, fact-citation popovers and exports present; simulated MOM rate-limit run shows "Completed with fallbacks" and the warnings; ambiguous question shows the clarification card; phone-width (390 px) has no horizontal overflow. Screenshot review found and fixed three issues (overlapping bar-chart labels, a repetitive executive summary, a thin "Hiring demand" finding).

## 9. How to run
```bash
cd backend && .venv/bin/pytest -o addopts="" --cov=app --cov-report=term:skip-covered     # all 226, offline
.venv/bin/pytest -o addopts="" tests/llm -v                                                # hallucination / accuracy / consistency
.venv/bin/pytest -o addopts="" tests/perf -s                                               # load numbers
RUN_LIVE=1 .venv/bin/pytest -o addopts="" -m live                                          # real government APIs (opt-in)
cd frontend && npm test && npm run test:coverage
```
CI (`.github/workflows/ci.yml`): backend lint + tests + coverage gate, frontend lint/typecheck/test/build, Docker build + compose smoke test (submits the headline query through the API), then image publish and an approval-gated deploy.

## 10. Known gaps
* **No live-LLM tests have been run** (no keys). `@pytest.mark.live_llm` is the hook for them; adapters are verified against mocked transports and the fallback/verification logic against scripted models.
* The verifier checks *grounding* (is this number/claim traceable?), not *interpretation quality* - a faithful but unhelpful sentence passes. A human-rated rubric or LLM-judge evaluation set would be the next layer.
* Small integers (≤ 12, unitless) are not treated as claims ("three sectors"), so a wrong small count could pass.
* No browser-level automated UI test is committed (the headless-Chromium check above was run ad hoc); Playwright specs would be the next step.
* CI workflow and Docker images are unexecuted (see README *Verification status*).
