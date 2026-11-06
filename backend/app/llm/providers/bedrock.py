from __future__ import annotations

import asyncio
from typing import Any

from ..types import Completion, LLMError, LLMRequest
from .base import LLMProvider


class BedrockProvider(LLMProvider):
    """AWS Bedrock via the model-agnostic Converse API. Credentials come from the standard AWS chain."""

    name = "bedrock"

    def __init__(self, enabled: bool, model_id: str, region: str, client: Any | None = None):
        self.enabled, self.model, self.region, self._client = enabled, model_id, region, client

    def configured(self) -> bool:
        return self.enabled

    def _get_client(self):
        if self._client is None:
            import boto3  # imported lazily: only needed when Bedrock is enabled

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        def call():
            return self._get_client().converse(
                modelId=self.model, system=[{"text": req.system}],
                messages=[{"role": "user", "content": [{"text": req.prompt}]}],
                inferenceConfig={"maxTokens": req.max_tokens, "temperature": req.temperature},
            )

        try:
            data = await asyncio.wait_for(asyncio.to_thread(call), timeout=timeout_s)
        except TimeoutError as exc:
            raise LLMError(f"{self.name}: timeout", transient=True, provider=self.name) from exc
        except Exception as exc:  # botocore ClientError etc.
            msg = str(exc)
            transient = any(t in msg for t in ("Throttling", "ServiceUnavailable", "ModelTimeout", "InternalServer"))
            raise LLMError(f"{self.name}: {exc.__class__.__name__}: {msg[:200]}", transient=transient, provider=self.name) from exc
        try:
            text = "".join(b.get("text", "") for b in data["output"]["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape", provider=self.name) from exc
        usage = data.get("usage") or {}
        return Completion(text, int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0)))
