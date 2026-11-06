"""State shared by the agents during one analysis run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..analytics.engine import AnalysisEngine
from ..analytics.facts import FactStore
from ..config import Settings
from ..data_quality import DQReport, ProcessedDataset
from ..datasources.base import RawDataset
from ..datasources.faults import FaultPlan
from ..datasources.registry import DataRegistry
from ..events import Emit
from ..llm.router import CostTracker, LLMRouter
from ..llm.types import LLMRequest, LLMResponse
from .intent import Plan, QueryIntent


@dataclass
class RunContext:
    analysis_id: str
    query: str
    settings: Settings
    registry: DataRegistry
    router: LLMRouter
    faults: FaultPlan
    emit: Emit
    now_year: int
    tracker: CostTracker
    persist_dataset: Callable[[ProcessedDataset], int] | None = None
    intent: QueryIntent | None = None
    plan: Plan | None = None
    raw: dict[str, RawDataset] = field(default_factory=dict)
    processed: dict[str, ProcessedDataset] = field(default_factory=dict)
    dq: dict[str, DQReport] = field(default_factory=dict)
    failed: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    stored: set[str] = field(default_factory=set)
    facts: FactStore = field(default_factory=FactStore)
    charts: list[dict[str, Any]] = field(default_factory=list)
    engine: AnalysisEngine | None = None
    narrative: dict[str, Any] | None = None
    narrative_source: str = ""
    verification: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    stage_status: dict[str, str] = field(default_factory=dict)

    async def llm(self, agent: str, req: LLMRequest) -> LLMResponse:
        return await self.router.complete(req, agent=agent, emit=self.emit, tracker=self.tracker, faults=self.faults, analysis_id=self.analysis_id)

    def warn(self, agent: str, msg: str) -> None:
        self.warnings.append(msg)
        self.emit(agent, "warning", msg, None)
