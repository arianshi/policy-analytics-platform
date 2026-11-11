"""Analysis lifecycle: queueing, async execution, event persistence and fan-out."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from .agents.context import RunContext
from .agents.workflow import run_analysis
from .config import Settings
from .datasources.faults import FaultPlan
from .datasources.registry import DataRegistry
from .db import Database
from .events import Event, EventBus
from .llm.router import CostTracker, LLMRouter

log = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, settings: Settings, db: Database, registry: DataRegistry, router: LLMRouter, bus: EventBus):
        self.s, self.db, self.registry, self.router, self.bus = settings, db, registry, router, bus
        self._sem = asyncio.Semaphore(settings.max_concurrent_analyses)
        self.tasks: dict[str, asyncio.Task] = {}

    async def submit(self, query: str, simulate_failures: list[str] | None = None) -> dict[str, Any]:
        query = " ".join(query.split())
        faults = FaultPlan.parse(self.s.fault_injection)
        if simulate_failures and self.s.allow_fault_injection:
            faults = faults.merge(FaultPlan.parse(simulate_failures))
        analysis_id = uuid.uuid4().hex[:12]
        self.db.create_analysis(analysis_id, query, {"faults": faults.describe()})
        self.tasks[analysis_id] = asyncio.create_task(self._run(analysis_id, query, faults), name=f"analysis-{analysis_id}")
        self.tasks[analysis_id].add_done_callback(lambda _t, aid=analysis_id: self.tasks.pop(aid, None))
        return {"id": analysis_id, "status": "queued"}

    def emitter(self, analysis_id: str):
        def emit(agent: str, type_: str, message: str, data: dict[str, Any] | None = None) -> None:
            rec = self.db.add_event(analysis_id, agent, type_, message, data)
            self.bus.publish(Event(analysis_id, agent, type_, message, rec["data"], rec["seq"], rec["ts"]))

        return emit

    async def _run(self, analysis_id: str, query: str, faults: FaultPlan) -> None:
        emit = self.emitter(analysis_id)
        try:
            emit("system", "status", "Queued - waiting for a free worker" if self._sem.locked() else "Starting", {})
            async with self._sem:
                self.db.set_status(analysis_id, "running")
                ctx = RunContext(
                    analysis_id,
                    query,
                    self.s,
                    self.registry,
                    self.router,
                    faults,
                    emit,
                    datetime.now(UTC).year,
                    CostTracker(self.s.llm_budget_usd),
                    persist_dataset=self.db.store_dataset,
                )
                result = await asyncio.wait_for(run_analysis(ctx), timeout=self.s.analysis_timeout_s)
            self.db.save_result(analysis_id, result["status"], result, result.get("error", ""))
            emit("system", "done", f"Analysis {result['status']}", {"status": result["status"]})
        except asyncio.CancelledError:
            self.db.save_result(analysis_id, "cancelled", None, "Cancelled by the user")
            emit("system", "done", "Analysis cancelled", {"status": "cancelled"})
            raise
        except TimeoutError:
            msg = f"Analysis exceeded the {self.s.analysis_timeout_s:.0f}s time limit"
            self.db.save_result(analysis_id, "failed", None, msg)
            emit("system", "error", msg, {})
            emit("system", "done", "Analysis failed", {"status": "failed"})
        except Exception as exc:  # last line of defence: never leave an analysis stuck in 'running'
            log.exception("analysis %s crashed", analysis_id)
            msg = f"Internal error: {exc.__class__.__name__}"
            self.db.save_result(analysis_id, "failed", None, msg)
            emit("system", "error", msg, {})
            emit("system", "done", "Analysis failed", {"status": "failed"})

    async def cancel(self, analysis_id: str) -> bool:
        task = self.tasks.get(analysis_id)
        if not task or task.done():
            return False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def shutdown(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
