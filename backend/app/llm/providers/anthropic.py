from __future__ import annotations

from typing import Any

from ..types import Completion, LLMError, LLMRequest
from .base import LLMProvider

# Claude thinks before answering, and thinking tokens count against max_tokens: keep enough room for the answer.
MIN_MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    """Claude via the Anthropic Messages API (official ``anthropic`` SDK).

    Retries are left to the router (``max_retries=0``) so its circuit breaker sees every failure. A request declined by
    Claude's safety classifiers is re-run server-side on Anthropic's recommended fallback model (``fallbacks="default"``).
    """

    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str, client: Any | None = None):
        self.api_key, self.model, self.effort, self._client = api_key, model, effort, client

    def configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic  # imported lazily: only needed when Claude is configured

            self._client = AsyncAnthropic(api_key=self.api_key, max_retries=0)
        return self._client

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        import anthropic

        try:
            msg = await self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=max(req.max_tokens, MIN_MAX_TOKENS),
                system=req.system,
                messages=[{"role": "user", "content": req.prompt}],
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                timeout=timeout_s,
            )
        except anthropic.APITimeoutError as exc:
            raise LLMError(f"{self.name}: timeout", transient=True, provider=self.name) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"{self.name}: network error {exc.__class__.__name__}", transient=True, provider=self.name) from exc
        except anthropic.APIStatusError as exc:
            transient = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            raise LLMError(
                f"{self.name}: HTTP {exc.status_code} {str(exc.message)[:200]}", transient=transient, provider=self.name
            ) from exc

        if msg.stop_reason == "refusal":
            category = getattr(msg.stop_details, "category", None) if msg.stop_details else None
            raise LLMError(f"{self.name}: request declined (refusal, category={category})", provider=self.name)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if not text and msg.stop_reason == "max_tokens":
            raise LLMError(f"{self.name}: hit max_tokens before answering", provider=self.name)
        return Completion(text, int(msg.usage.input_tokens or 0), int(msg.usage.output_tokens or 0))
