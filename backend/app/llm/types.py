"""Provider-neutral request/response types for the LLM layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """A provider call failed. ``transient`` errors (timeouts, 429, 5xx) are worth retrying."""

    def __init__(self, message: str, *, transient: bool = False, provider: str = ""):
        super().__init__(message)
        self.transient = transient
        self.provider = provider


class InvalidOutput(LLMError):
    """The provider answered, but the answer failed validation (not JSON, wrong schema, hallucinated ids...)."""


@dataclass
class LLMRequest:
    task: str  # interpret_query | react_step | generate_insights | write_report
    system: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)  # structured payload; only the offline provider reads it
    json_mode: bool = True
    max_tokens: int = 1800
    temperature: float = 0.0
    validator: Callable[[str], Any] | None = None  # parses/validates the text; raise ValueError to reject


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    parsed: Any
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    fallbacks: list[dict[str, str]] = field(default_factory=list)  # providers tried before this one
