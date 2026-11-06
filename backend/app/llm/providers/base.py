from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ..types import Completion, LLMError, LLMRequest


class LLMProvider(ABC):
    name: str = ""
    model: str = ""
    paid: bool = True

    @abstractmethod
    def configured(self) -> bool:
        """True when credentials/config are present (unconfigured providers are skipped silently)."""

    @abstractmethod
    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion: ...


def raise_for_http(provider: str, resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    transient = resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500
    raise LLMError(f"{provider}: HTTP {resp.status_code} {resp.text[:200]}", transient=transient, provider=provider)
