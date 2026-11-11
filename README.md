# Agentic Policy Data Analytics Platform

A multi-agent system that answers natural-language policy questions about Singapore's labour market and economy.
Four cooperating agents plan the work, extract data from government sources, analyse it statistically and write a
cited report. **Every number in the report is computed by code and then verified against the underlying facts**, so
an LLM can narrate the results but cannot invent them.

> **Try it with no API keys.** Without credentials the platform runs on a deterministic rule-based "offline model",
> so the whole system, the demo and the test-suite work out of the box. Add Anthropic (Claude) / OpenAI / Gemini / Azure
> OpenAI / AWS Bedrock keys and those models take over, with automatic fallback between them.

| | |
|---|---|
| **Agents** | Coordinator → Data Extraction → Analytics → Reporter (custom ReAct framework) |
| **Data** | MOM via Data.gov.sg (CSV), DOS SingStat Table Builder (JSON API), internal Excel extract (MOCK) |
| **LLMs** | Anthropic Claude · OpenAI · Azure OpenAI · Google Gemini · AWS Bedrock · offline fallback, behind one router |
| **Stack** | FastAPI + SQLite + pandas/scipy · Next.js 16 / React 19 / TypeScript / Recharts |
| **Quality** | 226 backend tests (95 % coverage) · 31 frontend tests · hallucination, accuracy, consistency, data-quality and load tests |

Documents: [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE.md](DATABASE.md) · [TESTING.md](TESTING.md) · [DATA_SOURCES.md](DATA_SOURCES.md) · [INNOVATION.md](INNOVATION.md) (Section 2 answer)

## What it does

Ask: *"Analyse employment trends in the technology sector from 2020-2024"*. In about a second (offline) the system:

1. **Coordinator** interprets the question (LLM output is schema-validated against a sector/measure ontology), plans datasets and analyses.
2. **Extraction agent** fetches 10 datasets from 3 sources concurrently, retries on rate limits, falls back to bundled snapshots when a live source is down, then validates and cleans every dataset and stores it in SQLite.
3. **Analytics agent** computes growth/CAGR, trends, cross-sector rankings, employment share, correlations on year-on-year changes, anomaly detection, and a cross-source productivity join (SingStat GDP ÷ MOM employment) - 37 cited facts and 13 charts.
4. **Reporter agent** has the LLM draft findings from the facts, **verifies every number, year, citation and source name**, asks the model to revise if anything is unsupported, and falls back to a rule-based draft rather than ever publishing an unverified claim.

The web UI streams every thought → action → observation over a WebSocket, then shows the report, charts, data-quality
audit, sources, facts, hallucination-check result and LLM cost. Reports export as Markdown, HTML, JSON and CSV.

### What the headline query finds (real data, 2020-2024)
* ICT employed residents rose from 111,600 to 139,200 (+24.7 %, 5.7 % a year) - #1 of 13 industries.
* Job vacancies spiked +87 % in 2021 (flagged as an anomaly), peaked at 10,300 in 2022, then fell 38.8 %; retrenchments peaked at 2,740 in 2023.
* Year-on-year vacancy and retrenchment changes are *negatively* correlated (r = −0.64). The correlation of raw levels is a misleading +0.49 caused by shared trends, which is why the engine differences the series.
* Output grew +62.6 % vs +24.7 % employment: value added per resident worker +30.4 %.
* **2025 data (outside the requested window) shows a −6.8 % drop**, so the platform surfaces it and *withholds* a linear projection because the latest point breaks the trend.

## Quick start

Prerequisites: Python ≥ 3.12, Node ≥ 20. (Docker is optional.)

```bash
# 1. backend  (http://localhost:8000, docs at /docs)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
LIVE_DATA=false .venv/bin/uvicorn app.main:app --port 8000     # offline: bundled real snapshots
#   or: .venv/bin/uvicorn app.main:app --port 8000               # live Data.gov.sg / SingStat

# 2. frontend (http://localhost:3000)
cd ../frontend
npm ci && npm run dev
```

Or `make setup`, `make backend`, `make frontend`. With Docker: `cp .env.example .env && docker compose up --build`
(see *Verification status* below: the container files are written but could not be built on the authoring machine).

### Enabling real LLMs
Copy `.env.example` to `backend/.env` (or keep it as `.env` at the repo root and start the backend with
`.venv/bin/uvicorn app.main:app --port 8000 --env-file ../.env`) and set any of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `AZURE_OPENAI_*`, or `BEDROCK_ENABLED=true` (+ AWS credentials). Claude defaults to `claude-opus-5-5`
at `ANTHROPIC_EFFORT=low`; requests Claude's safety classifiers decline are re-run server-side on Anthropic's
recommended fallback model. Providers are tried in `LLM_PROVIDER_ORDER`; each
has a circuit breaker; invalid or hallucinated output also triggers fallback. `GET /api/providers` shows status and cost.

## Sample queries

| Query | Demonstrates |
|---|---|
| Analyse employment trends in the technology sector from 2020-2024 | full pipeline, 3 sources, cross-source join, anomaly, trend-break |
| Compare wage growth in finance and the technology sector since 2019 | multi-sector comparison and ranking |
| Is there a link between job vacancies and retrenchments in the tech sector? | correlation on differenced series |
| Forecast employment in the manufacturing sector for the next two years | projection with 80 % interval (tech/finance are withheld: trend break) |
| IT services versus telecommunications job vacancies 2019-2024 | sub-sector ontology, missing-measure limitations |
| job vacancy rate by sector 2021 to 2025 | no sector named → cross-sector overview with disclosed assumption |
| tell me something interesting | ambiguous → clarification with suggestions (no data touched) |

**Error-handling demo:** open *Demo: simulate a failure* in the UI (or POST `simulate_failures`): MOM rate-limited → retry → snapshot fallback (report flagged *Completed with fallbacks*); SingStat schema drift → dataset rejected, analysis continues without productivity; internal DB down → optional dataset skipped and the plan is rebuilt; every LLM down → rule-based policy finishes the run.

## Running the tests

```bash
make test                  # or:
cd backend  && .venv/bin/pytest -o addopts="" --cov=app      # 226 tests, fully offline, ~25 s
cd frontend && npm test                                       # 31 tests
make load-test             # HTTP load test against a running API (start it with LIVE_DATA=false RATE_LIMIT_PER_MIN=0)
RUN_LIVE=1 pytest -m live  # optional: hit the real government APIs
```

See [TESTING.md](TESTING.md) for strategy, hallucination-detection methodology and results.

## Technology choices

| Choice | Why |
|---|---|
| **Custom ReAct framework** (no LangGraph/CrewAI) | The workflow is a small, well-defined graph; owning the loop made the failure handling (loop detection, premature-final rejection, step budgets, policy fallback) explicit, testable and dependency-free. See [ARCHITECTURE.md §4](ARCHITECTURE.md#4-agent-design). |
| **Facts-first design** (code computes, LLM narrates) | Makes hallucination detection tractable: every claim has a typed numeric source to be checked against. |
| **FastAPI + asyncio tasks + SQLite** | Async API, WebSockets and background work in one process; SQLite keeps setup to zero. Repository layer is small so Postgres is a drop-in (see [ARCHITECTURE.md §10](ARCHITECTURE.md#10-design-decisions-and-trade-offs) and [DATABASE.md](DATABASE.md)). |
| **pandas / scipy** | Standard, auditable statistics; results are cross-checked against independent recomputations in tests. |
| **Next.js + Recharts** | Typed UI with server-side build output for a small Docker image; charts are rendered from a JSON spec the backend emits, so exports and UI share one source of truth. |
| **httpx REST adapters for LLMs** | One thin adapter per provider (≈40 lines) instead of four heavy SDKs; trivially mockable for tests. Bedrock uses boto3's Converse API. |
| **Offline provider** | Removes API keys as a barrier for reviewers and makes the test-suite deterministic. |

## Repository layout

```
backend/app/
  agents/       coordinator, extraction, analytics_agent, reporter, base (ReAct loop), intent, policies, workflow
  analytics/    stats.py (pure statistics), facts.py (Fact model), engine.py (analyses → facts + charts)
  datasources/  registry (live → cache → snapshot), catalog, normalise, http (retry/backoff), faults
  llm/          router (fallback, circuit breaker, budget), providers/{openai,azure,gemini,bedrock,offline}
  reporting/    verify.py (hallucination check), template.py, report.py, export.py
  data_quality.py  db.py  service.py  events.py  api/{routes,ws,schemas}.py  main.py
backend/data/   snapshots/ (real downloaded data + manifest.json)   internal/ (MOCK Excel)
backend/tests/  unit · dq · integration · llm · perf
frontend/src/   app/ components/ hooks/ lib/ __tests__/
```

## Verification status (what was and wasn't exercised)

Verified on the authoring machine:
* Backend and frontend test-suites, lint, type-check and production build.
* The full system against **live** Data.gov.sg and SingStat APIs (including a genuine HTTP 429 rate-limit recovery, see DATA_SOURCES.md).
* The web UI end to end in headless Chromium: live WebSocket stream, tabs, charts, simulated-failure run, clarification flow, phone-width layout.
* HTTP load test: 60 concurrent analyses, 100 % success, 6.3/s, p95 2.5 s (offline data).

**Not** verified - please treat as unproven:
* **Real LLM providers.** No API keys were available. Each adapter is tested against a mocked transport for the exact request shape, auth, parsing and error mapping, and the fallback/verification logic is tested with scripted models, but no call has been made to OpenAI, Gemini, Azure or Bedrock.
* **Docker images and `docker-compose.yml`.** Written and syntax-checked, but the Docker daemon is not running on the authoring machine (no `docker compose` plugin), so images were not built.
* **The GitHub Actions workflow.** YAML parses, but it has never run.
* **The internal programmes dataset is synthetic.** It exists to demonstrate Excel ingestion and data-quality handling and is labelled MOCK everywhere it appears.
