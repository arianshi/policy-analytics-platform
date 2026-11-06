# Architecture

## 1. System overview

```
 Browser (Next.js)  ── REST ──►  FastAPI  ──► AnalysisService ──► asyncio task per analysis (semaphore-bounded)
        ▲                          │                                   │
        └── WebSocket (events) ◄───┘  EventBus ◄── events (also persisted) ◄── RunContext.emit
                                                                           │
                          ┌──────────────────── Coordinator agent (ReAct) ─┴───────────────────────────┐
                          │ interpret_query → plan_workflow → delegate_extraction → [replan] →         │
                          │ delegate_analytics → delegate_reporting                                    │
                          └───────┬──────────────────────┬──────────────────────┬──────────────────────┘
                                  ▼                      ▼                      ▼
                         Extraction agent        Analytics agent         Reporter agent
                         search_catalog          check_scope             draft_findings
                         fetch_datasets          analyze_trends          verify_draft
                         use_snapshot            compare_sectors         revise_draft
                         skip_datasets           analyze_relationships   compose_report
                         validate_and_clean      cross_source
                         store_datasets          outlook
                              │                        │                       │
                     DataRegistry                AnalysisEngine           LLMRouter + verify.py
              live → cache → snapshot         (pandas/scipy → Facts)   (providers, fallback, checks)
                              │                        │
                       SQLite: datasets, dataset_rows, analyses, events, llm_calls
```

**Principle: code computes, the LLM narrates, a verifier checks.** All numbers originate in `AnalysisEngine` as typed
`Fact` objects. LLMs interpret questions, choose the next tool, and write prose about facts, but they never supply a
number, and a verifier rejects prose containing any number it cannot trace to a cited fact.

## 2. Request lifecycle

1. `POST /api/analyses` validates the question, rate-limits per IP, inserts a `queued` row, and starts an asyncio task. Returns `202 {id}` immediately.
2. The task waits on a semaphore (`MAX_CONCURRENT_ANALYSES`), runs the Coordinator under a global timeout, and persists the result document.
3. Every agent step is an `Event` (`thought | action | observation | delegation | warning | llm | result | status | done`) that is stored in SQLite and fanned out to WebSocket subscribers. The socket subscribes *before* replaying history, so no event is lost or duplicated between replay and live; the UI falls back to polling `/events?after=<seq>` if the socket drops.
4. On startup, analyses left `queued/running` by a dead process are marked `failed` ("interrupted by a server restart").

## 3. Agent design

### ReAct loop (`agents/base.py`)
Each iteration: build a prompt (goal, tool list, compact *state*, last 4 steps) → ask the LLM for JSON
`{"thought", "action":{tool,args}}` or `{"thought","final"}` → validate → execute tool → record the observation.

Robustness built into the loop (each has a test in `tests/unit/test_react_agent.py`):

| Failure | Handling |
|---|---|
| LLM returns non-JSON / unknown tool | validator rejects → router tries the next provider; if all fail, the agent's **rule-based policy** decides the step (one warning, then policy for the rest of that task) |
| Tool raises | becomes an *observation* (`ok:false`) the agent can reason about; workflow continues |
| Agent repeats the same action 3× | loop detected → policy overrides the step |
| LLM says `final` too early | `can_finish()` rejects with a reason; after 2 rejections the policy takes over |
| Agent never finishes | hard step budget → `incomplete` result, never a hang |
| Tool hangs | 120 s per-tool timeout; 240 s per-analysis timeout |

The policies (`agents/policies.py`) are pure `state → decision` functions. They serve as the offline model's "brain" **and** as the guardrail for live LLMs, so a workflow always makes progress. A live LLM receives the same state and may choose differently.

### The four agents
| Agent | Autonomous decisions | Failure behaviour |
|---|---|---|
| **Coordinator** | interpret → ask for clarification *or* plan; after extraction decide to continue, **re-plan** (drop analyses whose optional datasets are missing) or abort; decide final status (`completed` / `degraded` / `failed` / `needs_clarification`) | clarification instead of guessing; fails closed with a reason when required data is unobtainable |
| **Extraction** | which datasets to fetch; **retry once** on transient errors (429/timeout); else **fall back to the bundled snapshot** (flagged degraded); else **skip** if optional; reject datasets with schema drift | per-dataset error isolation; required dataset with no fallback → coordinator fails the run |
| **Analytics** | executes the plan (`trend`, `compare`, `relationships`, `cross_source`, `outlook`); tools report `insufficient_data` instead of fabricating; a sector missing a measure becomes a *limitation fact* | trend-break detector withholds projections; empty analysis → `failed` |
| **Reporter** | draft → verify → (revise with the failures as feedback) → (rule-based draft) → compose | an unverifiable narrative can never be published |

Intent interpretation: the LLM proposes a `QueryIntent` JSON; pydantic validates it against the ontology (a hallucinated
sector id, a bad year range or a prompt-injected instruction cannot widen scope), years are clamped to data availability
with a disclosed assumption, and the deterministic heuristic parser is the fallback. User text is delimited as data in the prompt.

## 4. Data layer

`DataRegistry.acquire(key)` → **live API** (retry/backoff, honours `Retry-After`) → **disk cache** (TTL) → raises `DataSourceError(code, retryable)`.
The extraction agent, not the data layer, chooses to use `acquire_snapshot` - so degraded results are an explicit,
visible decision. Provenance (`mode`, `retrieved_at`, `degraded`, licence, URL) travels with every dataset into the
report's citations. See DATA_SOURCES.md.

`normalise.py` maps each source onto one canonical long format `(year, sector, value, unit)` through the sector
ontology (`ontology.py`), so MOM's "financial and insurance services" and SingStat's "Finance & Insurance" join cleanly.
A source that changes its columns raises `SchemaError`, which the agent handles.

`data_quality.py` coerces and validates (suppression tokens, thousands separators, bad years, domain ranges, exact vs
conflicting duplicates, isolated spikes, gaps, staleness), produces an auditable `DQReport` and a 0-100 score.
Deliberate policies: conflicting duplicates are *both excluded* (never guessed); isolated spikes in **official** data are
flagged but kept (they are often real: COVID), in **internal** data they are quarantined.

## 5. Analytics → facts

`AnalysisEngine` produces `Fact(id, kind, sector, metric, period, statement, values{name: (v, kind)}, sources, caveats, importance, mock)`.
The `statement` and `values` are generated together; a test asserts that every number in every fact sentence is accounted for by its own `values`.
Statistical choices that matter: correlations are computed on **year-on-year changes**; anomalies use a median/MAD robust z-score; projections are OLS with a prediction interval and are **withheld** when the latest observation falls outside the interval implied by the preceding trend (`breaks_trend`).

## 6. LLM layer

`LLMRouter.complete()` iterates providers in `LLM_PROVIDER_ORDER`:
* skip unconfigured providers; skip paid providers once the per-analysis **budget** is exhausted;
* **circuit breaker** per provider (open after N consecutive *availability* failures, half-open after a cooldown). Invalid output falls back for that request but does not open the circuit - a model with a bad format is not an outage;
* one retry on transient errors (timeout, 429, 5xx);
* a `validator` on every request - schema/ontology/format violations count as a provider failure and fall through;
* every attempt (success or failure) is recorded to `llm_calls` with tokens, estimated cost and latency.

Providers: OpenAI, Azure OpenAI (deployment URL + `api-key`), Gemini (`generateContent`), Bedrock (Converse API), and `offline` (always last, never paid).

## 7. Hallucination defence (`reporting/verify.py`)

Each narrative item cites fact ids and is checked for: unknown/missing citations; **every number** (typed: a `%` must match a percentage fact; "S$42.8 billion" is scale-converted to S$ million; "139.2k" to persons; rounding tolerance follows the number of decimals written); years outside coverage; agencies never used (IRAS, IMF…); MOCK data narrated without saying so; and (as warnings) causal language. Derived values (differences, % changes between two cited values) are allowed and tagged. Errors → revise → rule-based draft. Details and measured detection rates: TESTING.md.

## 8. Database schema (SQLite, WAL)

```
analyses(id PK, query, status, options, title, error, result JSON, facts, duration_ms, created_at, updated_at, completed_at)
events(id PK, analysis_id, seq, ts, agent, type, message, data JSON)          UNIQUE(analysis_id, seq)
datasets(key PK, source, title, mode, degraded, is_mock, retrieved_at, rows, dq_score, dq JSON, provenance JSON, updated_at)
dataset_rows(dataset_key, year, sector, sector_label, metric, value, unit)     cleaned canonical rows
llm_calls(id PK, analysis_id, ts, agent, task, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, ok, error)
```

## 9. Design decisions, trade-offs and production notes

| Decision | Trade-off / what changes in production |
|---|---|
| Custom ReAct over LangGraph | fewer dependencies and explicit control, but no built-in checkpointing/visualisation. A LangGraph port is mechanical (tools and policies are already plain functions). |
| SQLite + in-process tasks | zero setup; single node. Production: Postgres (swap `Database`), a queue (SQS/Celery) with stateless workers, Redis pub/sub for the event bus so WebSockets scale horizontally. |
| Offline policy as fallback model | determinism and key-free demos; it plans less flexibly than a strong LLM. Real LLMs get the same state and tools. |
| Annual data only | MOM publishes quarterly series too; the catalog/normaliser design extends to them (period column instead of year). |
| Resident-only employment | MOM's industry series cover citizens + PRs; the foreign-workforce datasets would complete the picture. Disclosed as a caveat on every employment fact. |
| LLM sees facts, not rows | minimises data leaving the boundary; good for sensitive deployments (INNOVATION.md). |

**Security:** secrets are runtime env vars only (never baked into images, never logged); containers run as non-root; the API validates and length-limits input, rate-limits per IP, and fault injection is switchable off (`ALLOW_FAULT_INJECTION=false`). There is **no authentication** in this prototype: put it behind SSO/an API gateway before exposing it. The query is treated as data in prompts, and LLM output can only select ontology ids.

**Observability:** structured logs with request ids, per-analysis event log, per-provider stats at `/api/providers`, per-call cost/latency in `llm_calls`.
