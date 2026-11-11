from __future__ import annotations

import httpx

from ..types import Completion, LLMError, LLMRequest
from .base import LLMProvider, raise_for_http


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.base_url, self._transport = api_key, model, base_url.rstrip("/"), transport

    def configured(self) -> bool:
        return bool(self.api_key)

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        gen = {"temperature": req.temperature, "maxOutputTokens": req.max_tokens}
        if req.json_mode:
            gen["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.prompt}]}],
            "generationConfig": gen,
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout_s) as client:
                resp = await client.post(url, headers={"x-goog-api-key": self.api_key}, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{self.name}: timeout", transient=True, provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: network error {exc.__class__.__name__}", transient=True, provider=self.name) from exc
        raise_for_http(self.name, resp)
        data = resp.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "unexpected response shape"
            raise LLMError(f"{self.name}: {reason}", provider=self.name) from exc
        usage = data.get("usageMetadata") or {}
        return Completion(text, int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0)))
