"""Fault injection for demos and tests.

Grammar (comma separated): ``source:<id>[:<mode>]`` and ``llm:<provider|*>``.
Modes for sources: ``unavailable`` (default), ``rate_limited``, ``timeout``, ``schema`` (simulated API drift).
Example: ``source:mom:rate_limited,llm:openai``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_MODES = {"unavailable", "rate_limited", "timeout", "schema"}


@dataclass(frozen=True)
class FaultPlan:
    sources: dict[str, str] = field(default_factory=dict)  # source id -> mode
    llm: frozenset[str] = frozenset()  # provider names (or "*")

    @classmethod
    def parse(cls, spec: str | list[str] | None) -> FaultPlan:
        if not spec:
            return cls()
        items = spec.split(",") if isinstance(spec, str) else spec
        sources: dict[str, str] = {}
        llm: set[str] = set()
        for raw in items:
            parts = [p.strip().lower() for p in str(raw).split(":") if p.strip()]
            if len(parts) >= 2 and parts[0] == "source":
                mode = parts[2] if len(parts) > 2 and parts[2] in SOURCE_MODES else "unavailable"
                sources[parts[1]] = mode
            elif len(parts) >= 2 and parts[0] == "llm":
                llm.add(parts[1])
        return cls(sources, frozenset(llm))

    def merge(self, other: FaultPlan) -> FaultPlan:
        return FaultPlan({**self.sources, **other.sources}, self.llm | other.llm)

    def source_fault(self, source: str) -> str | None:
        return self.sources.get(source)

    def llm_fault(self, provider: str) -> bool:
        return "*" in self.llm or provider in self.llm

    def __bool__(self) -> bool:
        return bool(self.sources or self.llm)

    def describe(self) -> list[str]:
        return [f"source:{s}:{m}" for s, m in self.sources.items()] + [f"llm:{p}" for p in sorted(self.llm)]
