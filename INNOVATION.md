# Section 2: Innovation Assessment

**How would I evolve this platform to serve policy researchers in secure government environments?**

The prototype already encodes one idea I would keep at the centre of every evolution: **code computes, the LLM narrates, a verifier checks.** Everything below extends that principle - it is what lets a regulated user trust AI output - while adding the security, scale and collaboration a government setting demands. Each proposal starts from something I built or observed.

## 1. User trust and transparency: from "verified" to "auditable"

*What exists:* every number in a report is traced to a typed fact; a verifier checks numbers, years, citations and source names (41 of 41 deliberately altered numbers were caught); a hostile model's fabricated "+85.3 % … according to IRAS" never reaches a report.

*Next:* (a) **Claim-level provenance ledger**: store, per sentence, the fact ids, dataset versions (SHA-256 of the source payload, already recorded in the snapshot manifest) and model/prompt versions, in an append-only signed log, so a minister's office can ask *"exactly where did 38.8 % come from, and has the source been revised since?"* and get a reproducible answer. (b) **Re-run on revision**: MOM and SingStat revise published series; hash-based dataset versioning lets the platform say "this 2024 report's headline changed by 0.4 points after the March release". (c) **Calibrated uncertainty in the UI**: today a trend over five points is flagged "not statistically significant" in text; surface that as a visual confidence band and refuse to rank sectors whose intervals overlap. (d) **Human-in-the-loop sign-off**: reviewers approve or amend findings; approved claims become reusable, versioned knowledge.

## 2. Security and compliance in government environments

*What exists:* the LLM sees computed facts, never raw rows; secrets are runtime-only; containers run non-root; input is validated and rate-limited; every LLM call is logged with tokens, cost and latency.

*Next:* (a) **Data-classification-aware routing.** The router already orders providers and skips them on budget/outage; add a *classification policy* so "restricted" analyses may only use in-region, private-endpoint models (Bedrock in `ap-southeast-1` via VPC endpoint, Azure OpenAI in a government region) or an on-prem open-weight model, with the offline model as the guaranteed floor. (b) **Zero-trust access:** SSO/OIDC, role- and agency-scoped access to datasets and analyses, per-tenant encryption keys, immutable audit trail of who asked what. (c) **PII minimisation by design:** microdata would be aggregated and small-cell-suppressed inside the secure zone *before* facts are produced, so a model can never receive an identifiable record. (d) **Prompt-injection hardening:** already schema-validated (an LLM can only select ontology ids); extend to tool-call allow-lists per agent and output filters for any text sourced from documents. (e) Supply-chain controls: pinned images, SBOM, signed releases, and the CI deploy gate already modelled as an approval-protected environment.

## 3. Agent intelligence and adaptability; changing APIs and incomplete data

*What I observed:* the live Data.gov.sg API **rate-limited a real run** (5 of 10 datasets, HTTP 429); one MOM table contains a **conflicting duplicate** for Construction 2023; a 2025 figure fell outside the 2020-24 window and **broke the trend**, which is why the platform withholds a forecast rather than extrapolating (a naïve model would have projected ~154,000 ICT workers against an actual 129,700).

*Next:* (a) **Failure memory:** agents record which sources throttle and when, and schedule or cache accordingly instead of rediscovering it each run. (b) **API contract monitoring:** the schema-drift handling (a dataset missing a required column is rejected and the run degrades gracefully) becomes a scheduled canary that diffs live payloads against the snapshot and opens an alert before users notice. (c) **A Source-Discovery agent:** I paged all 4,663 Data.gov.sg datasets by hand; an agent can crawl the catalog, propose new adapters from metadata (units, coverage, agency) and draft the ontology mapping for human approval. (d) **Principled imputation:** where data is missing, offer model-based estimates *labelled and carried through* as uncertain facts - never silently interpolated (the platform currently reports gaps and refuses to guess). (e) **Cost-aware planning:** of ≈25 model calls per analysis, ≈24 are tiny ReAct decisions; route those to a small, cheap model and reserve a larger one for narrative, with a per-team budget (the per-analysis cap exists today).

## 4. Scalability and performance on large datasets

SQLite with in-process tasks handled 60 concurrent analyses at 6.3/s (p95 2.5 s) offline, which is right for a prototype and wrong for a ministry. Evolution: **Postgres + a columnar layer (DuckDB/Parquet)** for microdata-scale tables; a **durable queue with stateless workers** (SQS/Celery) and Redis pub/sub so WebSocket streams scale horizontally; **fact caching keyed by (dataset version hash, analysis spec)** so repeating or slightly varying a question is near-instant; and **incremental recomputation** when only one source updates. Because analyses are expressed as discrete, idempotent tools over cleaned long-format data, they parallelise naturally.

## 5. Collaboration and knowledge sharing across research teams

*Next:* shared workspaces where saved analyses, assumptions and approved findings are versioned and commentable; **"diff reports"** between runs; reusable **question templates** per policy domain; and a **RAG layer** (vector database) over methodology notes, past reports, SSIC definitions and agency footnotes, so the Coordinator can cite *why* a series changed and avoid re-deriving institutional knowledge. Each team's corrections to the sector ontology (e.g. mapping an agency-specific label) feed back as reviewed, shared configuration - the way the internal "ICT" / "Info-comm" label variants were resolved in the prototype.

## 6. Multi-cloud resilience and cost optimisation

*What exists:* four provider adapters behind one router, per-provider circuit breakers, fallback on outage *and on invalid output*, a per-analysis budget, and a rule-based floor so analysis never stops. *Next:* **latency- and cost-aware routing** from the `llm_calls` telemetry (route by observed p95 and price per task type); **health probes** that open circuits proactively; **shadow evaluation** that replays a sample of production questions against a candidate model and compares hallucination-check pass rates before promoting it; and **regional failover** between clouds that satisfy the same data-residency class.

## 7. Evaluation as a product feature

A nightly **golden-question suite** (accuracy against independently recomputed values, hallucination detection rate, consistency across paraphrases, cost and latency) gates any prompt, model or ontology change. It is the same discipline as the prototype's test-suite, run continuously and published to users as a transparency dashboard - the surest way to earn trust from analysts who have been burned by confident, wrong AI.

**Priority order I would propose:** (1) provenance ledger + dataset versioning, (2) classification-aware routing and private-endpoint models, (3) Postgres/queue/fact-cache, (4) cost-tiered model routing, (5) shared workspaces and RAG - because trust and security gate adoption, and scale and collaboration only matter once adoption exists.
