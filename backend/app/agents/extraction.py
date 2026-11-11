"""Data Extraction Agent: fetch -> (retry | snapshot fallback | skip) -> validate/clean -> store."""

from __future__ import annotations

import asyncio
from typing import Any

from ..data_quality import process_dataset
from ..datasources.base import DataSourceError, SchemaError
from ..datasources.catalog import SPEC_BY_KEY, search_catalog
from .base import ReActAgent, ToolError
from .context import RunContext
from .policies import decide_extraction


class ExtractionAgent(ReActAgent):
    name = "extraction"
    role = (
        "You acquire Singapore government datasets reliably. Fetch concurrently; on transient failures retry once; if a live source stays down fall back "
        "to the bundled snapshot (and say so); skip optional datasets with no fallback; always validate and clean before storing."
    )

    def __init__(self, ctx: RunContext):
        super().__init__(ctx, max_steps=ctx.settings.agent_max_steps + 6)
        self.searched = False
        self.add_tool("search_catalog", "Search the dataset catalog by free text", "query: str", self._search)
        self.add_tool("fetch_datasets", "Fetch datasets from their live sources (concurrent)", "keys: list[str]", self._fetch)
        self.add_tool(
            "use_snapshot",
            "Load the bundled snapshot for datasets whose live source failed (marks them degraded)",
            "keys: list[str]",
            self._snapshot,
        )
        self.add_tool(
            "skip_datasets", "Skip optional datasets that cannot be obtained", "keys: list[str], reason: str", self._skip
        )
        self.add_tool("validate_and_clean", "Normalise, validate and clean fetched datasets", "keys: list[str]", self._validate)
        self.add_tool("store_datasets", "Persist cleaned datasets to the database", "keys: list[str]", self._store)

    # ------------------------------------------------------------------ state
    def goal(self) -> str:
        return f"Obtain, validate and store datasets: required={self.ctx.plan.required}, optional={self.ctx.plan.optional}."  # type: ignore[union-attr]

    def state(self) -> dict[str, Any]:
        c, plan = self.ctx, self.ctx.plan
        assert plan is not None
        wanted = [*plan.required, *plan.optional]
        pending = [k for k in wanted if k not in c.raw and k not in c.processed and k not in c.failed and k not in c.skipped]
        to_validate = [k for k in c.raw if k not in c.processed]
        to_store = [k for k in c.processed if k not in c.stored]
        degraded = [k for k, p in c.processed.items() if p.prov.degraded]
        skipped = sorted(c.skipped)
        status = "partial" if skipped else "degraded" if degraded else "ok"
        return {
            "need_text": "labour market employment wages vacancies GDP " + " ".join(plan.datasets),
            "required": plan.required,
            "optional": plan.optional,
            "searched": self.searched,
            "pending_fetch": pending,
            "to_validate": to_validate,
            "to_store": to_store,
            "fetched": {k: r.prov.mode for k, r in c.raw.items()},
            "failed": c.failed,
            "stored": sorted(c.stored),
            "skipped": skipped,
            "degraded": degraded,
            "final_status": status,
        }

    def policy(self, state: dict[str, Any]) -> dict[str, Any]:
        return decide_extraction(state)

    def can_finish(self, state: dict[str, Any], final: dict[str, Any]) -> str | None:
        if final.get("status") == "failed" and state["failed"]:
            return None
        if state["pending_fetch"] or state["failed"] or state["to_validate"] or state["to_store"]:
            return "datasets are still pending, failed, unvalidated or unstored"
        return None

    # ------------------------------------------------------------------ tools
    async def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        hits = search_catalog(str(args.get("query", "")), limit=8)
        self.searched = True
        return {
            "summary": f"Catalog search returned {len(hits)} candidate dataset(s); planned datasets are {self.ctx.plan.datasets}.",
            "hits": hits,
        }  # type: ignore[union-attr]

    async def _fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = self._keys(args)
        if any(k in self.ctx.failed for k in keys):  # a retry: pause briefly before hitting the source again
            await asyncio.sleep(self.ctx.settings.http_backoff_s)
        sem = asyncio.Semaphore(2)  # Data.gov.sg rate-limits aggressively
        results: dict[str, Any] = {}

        async def one(key: str) -> None:
            spec = self.ctx.registry.spec(key)
            async with sem:
                try:
                    raw = await self.ctx.registry.acquire(key, faults=self.ctx.faults)
                except DataSourceError as exc:
                    prev = self.ctx.failed.get(key, {"attempts": 0})
                    self.ctx.failed[key] = {
                        "attempts": prev["attempts"] + 1,
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "has_snapshot": self.ctx.registry.has_snapshot(key),
                        "required": key in (self.ctx.plan.required if self.ctx.plan else []),
                    }
                    results[key] = {"status": "error", "code": exc.code, "message": str(exc)}
                    return
                self.ctx.raw[key] = raw
                self.ctx.failed.pop(key, None)
                results[key] = {"status": "ok", "mode": raw.prov.mode, "rows": len(raw.df), "source": spec.source_name}

        await asyncio.gather(*(one(k) for k in keys))
        ok = [k for k, r in results.items() if r["status"] == "ok"]
        bad = [k for k, r in results.items() if r["status"] != "ok"]
        return {
            "summary": f"Fetched {len(ok)}/{len(keys)} dataset(s)"
            + (f"; failed: {', '.join(f'{k} ({results[k]["code"]})' for k in bad)}" if bad else ""),
            "results": results,
        }

    async def _snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        keys, results = self._keys(args), {}
        for k in keys:
            try:
                raw = self.ctx.registry.acquire_snapshot(k, degraded=True)
            except DataSourceError as exc:
                results[k] = {"status": "error", "message": str(exc)}
                if k in self.ctx.failed:
                    self.ctx.failed[k]["has_snapshot"] = False
                continue
            self.ctx.raw[k] = raw
            self.ctx.failed.pop(k, None)
            self.ctx.warn(
                self.name,
                f"{k}: using bundled snapshot retrieved {raw.prov.retrieved_at[:10]} (live source unavailable) - result is flagged as degraded",
            )
            results[k] = {"status": "ok", "mode": "snapshot", "retrieved_at": raw.prov.retrieved_at}
        return {
            "summary": f"Loaded {sum(r['status'] == 'ok' for r in results.values())}/{len(keys)} snapshot(s) as fallback",
            "results": results,
        }

    async def _skip(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = self._keys(args)
        for k in keys:
            self.ctx.skipped.add(k)
            self.ctx.failed.pop(k, None)
        reason = str(args.get("reason", "unavailable"))
        self.ctx.warn(self.name, f"Skipping optional dataset(s) {', '.join(keys)}: {reason}")
        return {"summary": f"Skipped {', '.join(keys)} ({reason})"}

    async def _validate(self, args: dict[str, Any]) -> dict[str, Any]:
        keys, results = self._keys(args), {}
        for k in keys:
            raw = self.ctx.raw.get(k)
            if raw is None:
                results[k] = {"status": "error", "message": "not fetched"}
                continue
            try:
                p = await asyncio.to_thread(process_dataset, raw, now_year=self.ctx.now_year)
            except SchemaError as exc:
                self.ctx.raw.pop(k, None)
                prev = self.ctx.failed.get(k, {"attempts": 0})
                self.ctx.failed[k] = {
                    "attempts": prev["attempts"] + 1,
                    "code": "schema",
                    "message": str(exc),
                    "retryable": False,
                    "has_snapshot": raw.prov.mode != "snapshot" and self.ctx.registry.has_snapshot(k),
                    "required": k in (self.ctx.plan.required if self.ctx.plan else []),
                }
                results[k] = {"status": "error", "code": "schema", "message": str(exc)}
                continue
            self.ctx.processed[k] = p
            self.ctx.dq[k] = p.dq
            notable = [i.code for i in p.dq.issues if i.severity != "info"]
            results[k] = {
                "status": "ok",
                "rows_in": p.dq.rows_in,
                "rows_out": p.dq.rows_out,
                "score": p.dq.score,
                "completeness": p.dq.completeness,
                "issues": notable,
            }
        ok = [r for r in results.values() if r["status"] == "ok"]
        bad = [k for k, r in results.items() if r["status"] != "ok"]
        flagged = sum(1 for r in ok if r["issues"])
        return {
            "summary": f"Validated {len(ok)}/{len(keys)} dataset(s); {flagged} with data-quality warnings"
            + (f"; schema problems in {', '.join(bad)}" if bad else ""),
            "results": results,
        }

    async def _store(self, args: dict[str, Any]) -> dict[str, Any]:
        keys, total = self._keys(args), 0
        for k in keys:
            p = self.ctx.processed.get(k)
            if p is None:
                continue
            if self.ctx.persist_dataset:
                total += await asyncio.to_thread(self.ctx.persist_dataset, p)
            self.ctx.stored.add(k)
        return {"summary": f"Stored {len(keys)} cleaned dataset(s) ({total} rows)"}

    @staticmethod
    def _keys(args: dict[str, Any]) -> list[str]:
        keys = args.get("keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise ToolError("'keys' must be a non-empty list of dataset keys")
        unknown = [k for k in keys if k not in SPEC_BY_KEY]
        if unknown:
            raise ToolError(f"unknown dataset key(s) {unknown}; valid keys: {sorted(SPEC_BY_KEY)}")
        return list(dict.fromkeys(keys))
