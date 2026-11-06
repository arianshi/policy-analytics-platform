from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AnalysisRequest(BaseModel):
    query: str = Field(min_length=3, max_length=600, description="Natural-language research question")
    simulate_failures: list[str] = Field(default_factory=list, max_length=6,
                                         description="Demo/test only: e.g. 'source:mom:rate_limited', 'source:singstat:schema', 'source:internal', 'llm:openai'")

    @field_validator("query")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("query is too short")
        return v

    @field_validator("simulate_failures")
    @classmethod
    def _faults(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.lower().startswith(("source:", "llm:")):
                raise ValueError(f"invalid fault '{item}': use 'source:<id>[:<mode>]' or 'llm:<provider>'")
        return v


class AnalysisCreated(BaseModel):
    id: str
    status: str
